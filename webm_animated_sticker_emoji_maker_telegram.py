import subprocess
import sys
import os
import traceback
import threading
import queue

# ── AUTO-INSTALL ──────────────────────────────────────────────────────────────
def bootstrap():
    needed = False
    for lib, imp in [("Pillow", "PIL"), ("numpy", "numpy")]:
        try:
            __import__(imp)
        except ImportError:
            print(f"Installing {lib}...")
            subprocess.check_call([sys.executable, "-m", "pip", "install", lib],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            needed = True
    if needed:
        os.execv(sys.executable, ['python'] + sys.argv)

bootstrap()

# ── IMPORTS ───────────────────────────────────────────────────────────────────
import tkinter as tk
from tkinter import filedialog, ttk
from PIL import Image, ImageSequence, ImageTk, ImageDraw
import numpy as np
import tempfile
import shutil
import math

# ── TELEGRAM CONSTANTS ────────────────────────────────────────────────────────
STICKER_PX   = 512
EMOJI_PX     = 100
STICKER_KB   = 256
EMOJI_KB     = 64
# FIX: 2.99 gives a full extra frame vs 2.9, maximises animation time
MAX_DURATION = 2.99

# ══════════════════════════════════════════════════════════════════════════════
# CONVERTER ENGINE  (no UI dependencies)
# ══════════════════════════════════════════════════════════════════════════════

def get_gif_info(path):
    """
    Returns (frames, src_fps, is_animated).
    FIX: reads duration from the SAME open() used for frame iteration so per-
    frame duration is taken from frame-0 info before seek invalidates it.
    Caps total frames to MAX_DURATION.
    """
    img = Image.open(path)

    # Read duration before any seek
    try:
        duration_ms = img.info.get("duration", 100)
        fps         = round(1000 / max(duration_ms, 1))
        fps         = max(1, min(fps, 60))
    except Exception:
        fps = 15

    frames = []
    try:
        while True:
            frames.append(img.copy().convert("RGBA"))
            img.seek(img.tell() + 1)
    except EOFError:
        pass
    except Exception:
        pass

    if len(frames) <= 1:
        return [Image.open(path).convert("RGBA")], None, False

    # Cap to MAX_DURATION
    max_frames = int(fps * MAX_DURATION)
    frames = frames[:max_frames]
    return frames, fps, True


def compose_gif_frames(frames):
    """
    Normalize already-coalesced GIF frames onto a clean, uniform-size canvas.

    IMPORTANT (frame-stacking fix): Pillow ALREADY coalesces animated GIF
    frames on seek — each frame returned by get_gif_info() is the fully
    rendered image at that instant, with disposal already applied. So this
    function must NOT accumulate: it composes every frame on its OWN fresh
    transparent canvas, independent of the others.

    The previous version carried a single `canvas` forward across iterations
    (`canvas = new_canvas`), which pasted each frame on top of ALL earlier
    frames. With any moving/changing content the transparent regions of a
    later frame revealed the older frames underneath → the frames "stacked"
    (visible trails / ghosting), growing the opaque area every frame.
    """
    if not frames:
        return frames
    size     = frames[0].size
    composed = []
    for frame in frames:
        if frame.mode != "RGBA":
            frame = frame.convert("RGBA")
        canvas = Image.new("RGBA", size, (0, 0, 0, 0))
        # paste onto a FRESH canvas every iteration — no carry-over
        canvas.paste(frame, (0, 0), frame)
        composed.append(canvas)
    return composed


def make_checker_numpy(w, h, sq=8):
    """FIX: Build checkerboard with numpy — O(1) vs O(n²) putpixel loop."""
    arr = np.zeros((h, w, 4), dtype=np.uint8)
    # Light squares
    arr[:, :] = [200, 200, 200, 255]
    # Dark squares
    xs = np.arange(w)
    ys = np.arange(h)
    xg, yg = np.meshgrid(xs, ys)
    dark = ((xg // sq) + (yg // sq)) % 2 == 0
    arr[dark] = [160, 160, 160, 255]
    return Image.fromarray(arr, "RGBA")


def autocrop_unified(frames, margin=0):
    """
    FIX: Compute a single bounding box across ALL frames so the crop area is
    stable — prevents visual 'jumping' when content moves between frames.
    """
    if not frames:
        return frames
    h0, w0 = np.array(frames[0]).shape[:2]
    min_l, min_t = w0, h0
    max_r, max_b = 0, 0

    for f in frames:
        if f.mode != "RGBA":
            f = f.convert("RGBA")
        alpha = np.array(f)[:, :, 3]
        rows  = np.any(alpha > 0, axis=1)
        cols  = np.any(alpha > 0, axis=0)
        if not rows.any():
            continue
        h, w = alpha.shape
        min_l = min(min_l, int(np.argmax(cols)))
        min_t = min(min_t, int(np.argmax(rows)))
        max_r = max(max_r, int(w - np.argmax(cols[::-1])))
        max_b = max(max_b, int(h - np.argmax(rows[::-1])))

    if max_r <= min_l or max_b <= min_t:
        return frames  # nothing visible

    ml = max(0,  min_l - margin)
    mt = max(0,  min_t - margin)
    mr = min(w0, max_r + margin)
    mb = min(h0, max_b + margin)
    box = (ml, mt, mr, mb)
    return [f.crop(box) for f in frames]


def autocrop_single(img, margin=0):
    """Single-frame autocrop (used for non-animated inputs)."""
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    alpha = np.array(img)[:, :, 3]
    rows  = np.any(alpha > 0, axis=1)
    cols  = np.any(alpha > 0, axis=0)
    if not rows.any():
        return img
    h, w = alpha.shape
    t = max(0,  int(np.argmax(rows))      - margin)
    b = min(h,  int(h - np.argmax(rows[::-1])) + margin)
    l = max(0,  int(np.argmax(cols))      - margin)
    r = min(w,  int(w - np.argmax(cols[::-1])) + margin)
    return img.crop((l, t, r, b))


def normalize_frames(frames, target_fps, src_fps):
    """
    FIX: explicit validation — returns original frames if result would be empty.
    """
    if target_fps >= src_fps or not frames:
        return frames
    ratio   = src_fps / target_fps
    n_out   = max(1, int(len(frames) / ratio))
    result  = [frames[min(int(i * ratio), len(frames)-1)] for i in range(n_out)]
    return result if result else frames


def save_frames(frames, tmp_dir):
    """Save frames as numbered PNGs with unified canvas size."""
    max_w = max(f.width  for f in frames)
    max_h = max(f.height for f in frames)
    for i, f in enumerate(frames):
        canvas = Image.new("RGBA", (max_w, max_h), (0, 0, 0, 0))
        canvas.paste(f, ((max_w - f.width) // 2, (max_h - f.height) // 2))
        canvas.save(os.path.join(tmp_dir, f"frame_{i:04d}.png"), "PNG")
    return os.path.join(tmp_dir, "frame_%04d.png")


def run_ffmpeg(pattern, out_path, fps_in, fps_out, size_px, crf, max_kb):
    """Run FFmpeg VP9 encode, return (fits_limit, actual_kb)."""
    size = f"{size_px}:{size_px}"
    vf   = (
        f"scale={size}:force_original_aspect_ratio=decrease,"
        f"pad={size}:(ow-iw)/2:(oh-ih)/2:color=black@0,"
        f"format=yuva420p"
    )
    cmd = [
        "ffmpeg",
        "-framerate", str(fps_in),
        "-i", pattern,
        "-c:v", "libvpx-vp9",
        "-pix_fmt", "yuva420p",
        "-b:v", "0",
        "-crf", str(crf),
        "-vf", vf,
        "-r", str(fps_out),
        "-an",
        "-t", str(MAX_DURATION),
        "-y", out_path
    ]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode(errors="replace"))
    kb = os.path.getsize(out_path) / 1024
    return kb <= max_kb, kb


def convert_file(path, out_dir, is_sticker, crop_margin, target_fps, progress_cb):
    """
    Full conversion pipeline for one file.
    FIX: makedirs moved to caller so errors surface before thread starts.
    Returns (out_path, final_kb, warning_msg or None).
    """
    size_px = STICKER_PX if is_sticker else EMOJI_PX
    max_kb  = STICKER_KB if is_sticker else EMOJI_KB
    suffix  = "512px"    if is_sticker else "100px"
    base    = os.path.splitext(os.path.basename(path))[0]
    out     = os.path.join(out_dir, f"{base}_{suffix}.webm")
    tmp     = tempfile.mkdtemp()

    try:
        progress_cb("Reading frames…", 5)
        frames, src_fps, is_anim = get_gif_info(path)

        if is_anim:
            progress_cb("Composing frames…", 15)
            frames = compose_gif_frames(frames)

        if crop_margin >= 0:
            progress_cb("Cropping transparent borders…", 25)
            # FIX: unified crop across all frames
            if is_anim and len(frames) > 1:
                frames = autocrop_unified(frames, margin=crop_margin)
            else:
                frames = [autocrop_single(f, margin=crop_margin) for f in frames]

        fps_in  = src_fps or 1
        fps_out = fps_in
        if target_fps and target_fps < fps_in:
            progress_cb(f"Reducing FPS {fps_in} → {target_fps}…", 35)
            frames  = normalize_frames(frames, target_fps, fps_in)
            fps_out = target_fps

        progress_cb("Saving frame sequence…", 45)
        pattern = save_frames(frames, tmp)

        # Adaptive CRF loop
        warning   = None
        crf_steps = [30, 36, 42, 48, 54, 60, 63]
        for i, crf in enumerate(crf_steps):
            pct = 55 + int((i / len(crf_steps)) * 40)
            progress_cb(f"Encoding CRF {crf}  ({len(frames)} frames @ {fps_out} fps)…", pct)
            ok, kb = run_ffmpeg(pattern, out, fps_in, fps_out, size_px, crf, max_kb)
            if ok:
                break
            if i == len(crf_steps) - 1:
                warning = f"⚠ File is {kb:.0f} KB — exceeds {max_kb} KB (max compression applied)"

        progress_cb("Done!", 100)
        final_kb = os.path.getsize(out) / 1024
        return out, final_kb, warning

    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ══════════════════════════════════════════════════════════════════════════════
# 3D SPIN ENGINE
# ══════════════════════════════════════════════════════════════════════════════

def autocrop_rgba(img, margin=4):
    """Single-frame RGBA autocrop for spin engine."""
    arr   = np.array(img.convert("RGBA"))
    alpha = arr[:, :, 3]
    rows  = np.any(alpha > 0, axis=1)
    cols  = np.any(alpha > 0, axis=0)
    if not rows.any():
        return img
    h, w   = arr.shape[:2]
    top    = max(0, int(np.argmax(rows))              - margin)
    bottom = min(h, int(h - np.argmax(rows[::-1]))    + margin)
    left   = max(0, int(np.argmax(cols))              - margin)
    right  = min(w, int(w - np.argmax(cols[::-1]))    + margin)
    return img.crop((left, top, right, bottom))


def apply_glint(face_arr, theta, intensity=0.7, angle_deg=40, speed=1.0):
    """
    Screen-blend a diagonal white glint band over a face RGBA array.
    Only visible when face is facing camera (cos²(theta) falloff).
    """
    H, W = face_arr.shape[:2]
    if H < 2 or W < 2:
        return face_arr
    cos_t      = math.cos(theta)
    visibility = (cos_t ** 2) * intensity
    if visibility < 0.005:
        return face_arr
    half_pos = (theta * speed % math.pi) / math.pi
    band_cx  = W * (half_pos * 1.4 - 0.2)
    band_w   = W * 0.30
    cos_a    = math.cos(math.radians(angle_deg))
    sin_a    = math.sin(math.radians(angle_deg))
    xs       = np.arange(W, dtype=np.float32)
    ys       = np.arange(H, dtype=np.float32)
    xg, yg   = np.meshgrid(xs, ys)
    proj     = (xg - band_cx) * cos_a + (yg - H * 0.5) * (-sin_a)
    glint_a  = np.exp(-0.5 * (proj / (band_w * 0.4)) ** 2) * visibility
    glint_a *= face_arr[:, :, 3] / 255.0
    base     = face_arr[:, :, :3].astype(np.float32) / 255.0
    screen   = 1.0 - (1.0 - base) * (1.0 - glint_a[:, :, np.newaxis])
    face_arr[:, :, :3] = (screen * 255).clip(0, 255).astype(np.uint8)
    return face_arr


def apply_glint_cel(face_arr, theta, intensity=0.85, speed=1.0):
    """Cel glint: hard binary mask instead of gaussian — solid band."""
    H, W = face_arr.shape[:2]
    if H < 2 or W < 2:
        return face_arr
    cos_t      = math.cos(theta)
    visibility = (cos_t ** 2) * intensity
    if visibility < 0.005:
        return face_arr
    half_pos = (theta * speed % math.pi) / math.pi
    band_cx  = W * (half_pos * 1.4 - 0.2)
    band_w   = W * 0.30
    cos_a    = math.cos(math.radians(40))
    sin_a    = math.sin(math.radians(40))
    xs       = np.arange(W, dtype=np.float32)
    ys       = np.arange(H, dtype=np.float32)
    xg, yg   = np.meshgrid(xs, ys)
    proj     = (xg - band_cx) * cos_a + (yg - H * 0.5) * (-sin_a)
    mask     = (np.abs(proj) < band_w * 0.5).astype(np.float32)
    glint_a  = mask * visibility * (face_arr[:, :, 3] / 255.0)
    base     = face_arr[:, :, :3].astype(np.float32) / 255.0
    screen   = 1.0 - (1.0 - base) * (1.0 - glint_a[:, :, np.newaxis])
    face_arr[:, :, :3] = (screen * 255).clip(0, 255).astype(np.uint8)
    return face_arr


def _dwell_theta(i, n_frames, dwell, face=False):
    """
    Map frame index i → theta (0..2π) with non-uniform angular velocity.

    dwell=0.0  →  uniform spin (original behaviour)
    dwell=1.0  →  maximum slowdown at the dwell positions

    face=False → dwell at the two EDGE-ON positions (θ=π/2, 3π/2): the rim/thickness
                 lingers in view.
    face=True  → dwell at the two FACE-ON positions (θ=0, π): the coin pauses showing
                 its face (front and back), zipping through the edge-on moments.

    Strategy: in normalised phase φ ∈ [0,1) over a full rotation,

      φ_eased = φ  ±  dwell * A * sin(4π·φ)

    sin(4π·φ) has zeros at φ=0,0.25,0.5,0.75,1. With the '+' sign the mapping slows
    near φ=0.25/0.75 (edge-on); flipping to '-' slows near φ=0/0.5 (face-on) instead.
    Amplitude A = 0.95/(4π) keeps the derivative ≥0 (monotone, never reverses) even
    at dwell=1, for either sign.
    """
    if dwell < 0.001:
        return 2 * math.pi * i / n_frames
    phi   = i / n_frames                          # 0..1 uniform
    A     = 0.95 / (4 * math.pi)
    sign  = -1.0 if face else 1.0
    phi_e = phi + sign * dwell * A * math.sin(4 * math.pi * phi)
    return phi_e * 2 * math.pi


def make_3d_spin_frames(img_path,
                         n_frames         = 36,
                         thick_pct        = 8.0,   # % of image short-side, NOT px
                         edge_color       = (30, 30, 30),
                         edge_opacity     = 1.0,
                         tilt_deg         = 15,
                         flip_back        = False,
                         crop_margin      = 4,
                         glint            = False,
                         glint_intensity  = 0.7,
                         glint2           = False,
                         glint2_intensity = 0.85,
                         glint2_speed     = 1.0,
                         edge_dwell       = 0.0,   # 0=uniform, 1=max slowdown at dwell pos
                         dwell_face       = False, # False=dwell at edge-on, True=at face-on
                         edge_shade       = 0.0,   # 0=solid rim color, >0=subtle cylinder shading
                         edge_glint       = False, # metallic moving sheen on the rim
                         edge_glint_intensity = 0.8,
                         edge_glint_speed = 1.0,
                         bg_color         = (0, 0, 0, 0),
                         progress_cb      = None):
    """
    Render n_frames of a 3D coin-flip for the given image.
    thick_pct:  rim thickness as % of the image short side.
    edge_dwell: 0.0–1.0. Higher values slow the spin at the two edge-on moments
                (when the coin's rim/thickness faces the camera) so that moment
                gets more screen time and feels more dramatic.
    """
    img = Image.open(img_path).convert("RGBA")
    if crop_margin >= 0:
        img = autocrop_rgba(img, margin=crop_margin)
    W, H = img.size

    # convert pct → px, relative to the short side
    short_side = min(W, H)
    thick_px   = max(1, int(round(short_side * thick_pct / 100.0)))

    tilt    = math.radians(tilt_deg)
    y_scale = math.cos(tilt)

    CW = W + thick_px * 2 + 16
    CH = H + thick_px * 2 + 16
    cx = CW // 2
    cy = CH // 2

    src_alpha = np.array(img)[:, :, 3]
    THRESH    = 32

    row_left  = np.full(H, W,  dtype=np.int32)
    row_right = np.full(H, -1, dtype=np.int32)
    for y in range(H):
        opaque = np.where(src_alpha[y] >= THRESH)[0]
        if len(opaque):
            row_left[y]  = int(opaque[0])
            row_right[y] = int(opaque[-1])

    ec = np.array(edge_color, dtype=np.float32)

    frames = []
    for i in range(n_frames):
        if progress_cb:
            progress_cb(i, n_frames)

        theta        = _dwell_theta(i, n_frames, edge_dwell, face=dwell_face)
        cos_t        = math.cos(theta)
        sin_t        = math.sin(theta)
        x_scale      = abs(cos_t)
        facing_front = cos_t >= 0

        face_h  = max(1, int(H * y_scale))
        face_w  = max(1, int(W * x_scale)) if x_scale > 0.005 else 0
        scale_y = face_h / H

        rim_w     = max(1, int(thick_px * abs(sin_t)))

        # Thickness parallax: the visible face slides laterally by (t/2)·sinθ·sign(cosθ),
        # exactly as a real coin's front/back surface (offset ±t/2 in depth) projects
        # when rotated about the vertical axis. The rim (coin's side) fills the
        # OPPOSITE side. This is what makes the spin read as a 3D disc turning in
        # depth instead of a flat card being squashed.  (Verified vs a 3D render.)
        face_shift  = (thick_px * 0.5) * sin_t * (1.0 if facing_front else -1.0)
        shift_i     = int(round(face_shift))
        rim_on_right = face_shift < 0

        frame_arr = np.zeros((CH, CW, 4), dtype=np.uint8)

        # Face
        if face_w >= 1:
            face = img.resize((face_w, face_h), Image.LANCZOS)
            if not facing_front and not flip_back:
                face = face.transpose(Image.FLIP_LEFT_RIGHT)
            fa = np.array(face, dtype=np.uint8).copy()
            if glint:
                fa = apply_glint(fa, theta, intensity=glint_intensity, speed=glint2_speed)
            if glint2:
                fa = apply_glint_cel(fa, theta, intensity=glint2_intensity, speed=glint2_speed)
            px_ = max(0, min(CW - face_w, cx - face_w // 2 + shift_i))
            py_ = max(0, min(CH - face_h, cy - face_h // 2))
            ey2 = min(py_ + face_h, CH); ex2 = min(px_ + face_w, CW)
            fy2 = ey2 - py_;             fx2 = ex2 - px_
            if fy2 > 0 and fx2 > 0:
                src = fa[:fy2, :fx2].astype(np.float32)
                a_  = src[:, :, 3:4] / 255.0
                dst = frame_arr[py_:ey2, px_:ex2].astype(np.float32)
                frame_arr[py_:ey2, px_:ex2] = (src * a_ + dst * (1 - a_)).astype(np.uint8)

        # Rim
        if abs(sin_t) > 0.02 and rim_w >= 1 and edge_opacity > 0:
            fy_arr    = np.arange(face_h)
            src_y_arr = np.clip((fy_arr / scale_y).astype(np.int32), 0, H - 1)
            sl_arr    = row_left[src_y_arr]
            sr_arr    = row_right[src_y_arr]
            valid     = sr_arr >= sl_arr
            cy_arr    = cy - face_h // 2 + fy_arr

            eff_face_w  = max(1, face_w)
            scale_x     = eff_face_w / W
            face_anchor = cx - eff_face_w // 2 + shift_i

            if rim_on_right:
                sr_canvas     = face_anchor + (sr_arr * scale_x).astype(np.int32)
                rim_start_arr = sr_canvas + 1
                rim_end_arr   = sr_canvas + 1 + rim_w
                t_shade = np.linspace(0, 1, rim_w)
            else:
                sl_canvas     = face_anchor + (sl_arr * scale_x).astype(np.int32)
                rim_end_arr   = sl_canvas
                rim_start_arr = sl_canvas - rim_w
                t_shade = 1.0 - np.linspace(0, 1, rim_w)

            # SOLID rim: fill with the exact edge_color, no brightness gradient and
            # no angle-dependent dimming. edge_shade>0 re-enables a subtle cylinder
            # shade for those who want it; default 0.0 keeps the border a flat solid.
            if edge_shade > 0.0:
                bright_v = 1.0 - edge_shade * (
                    1.0 - np.maximum(0.0, np.cos((1.0 - t_shade) * math.pi / 2)))
                rim_rgb = np.clip(ec[np.newaxis, :] * bright_v[:, np.newaxis],
                                  0, 255).astype(np.uint8)
            else:
                rim_rgb = np.tile(np.clip(ec, 0, 255).astype(np.uint8), (rim_w, 1))
            rim_a    = int(255 * edge_opacity)

            # Metallic edge glint: a bright specular highlight that sweeps along the
            # rim (vertically) in sync with the spin, plus a soft cross-width sheen.
            # Simulates light sliding across a metal coin's milled edge.
            edge_glint_on = edge_glint and edge_glint_intensity > 0.0
            if edge_glint_on:
                sweep   = ((theta * max(0.1, edge_glint_speed)) % (2 * math.pi)) / (2 * math.pi)
                c1      = sweep * face_h
                c2      = ((sweep + 0.5) % 1.0) * face_h
                sig_y   = max(2.0, face_h * 0.14)
                gv_all  = (np.exp(-0.5 * ((fy_arr - c1) / sig_y) ** 2)
                           + 0.4 * np.exp(-0.5 * ((fy_arr - c2) / sig_y) ** 2))
                gv_all  = np.clip(gv_all, 0.0, 1.0)
                off_idx = np.arange(rim_w, dtype=np.float32)
                sheen_w = np.exp(-0.5 * ((off_idx - rim_w * 0.5)
                                         / max(1.0, rim_w * 0.55)) ** 2)
                sheen_w = (0.45 + 0.55 * sheen_w).astype(np.float32)   # base + peak
                white   = np.array([255.0, 255.0, 255.0], dtype=np.float32)

            for fi in range(face_h):
                if not valid[fi]:
                    continue
                py2 = int(cy_arr[fi])
                if py2 < 0 or py2 >= CH:
                    continue
                rx0 = int(rim_start_arr[fi]); rx1 = int(rim_end_arr[fi])
                cx0 = max(0, rx0);            cx1 = min(CW, rx1)
                if cx1 <= cx0:
                    continue
                off0 = max(0, cx0 - rx0)
                off1 = min(rim_w, off0 + (cx1 - cx0))
                if off1 <= off0:
                    continue
                n    = off1 - off0
                mask = frame_arr[py2, cx0:cx0 + n, 3] < 128
                if not mask.any():
                    continue
                if edge_glint_on:
                    ha      = np.clip(edge_glint_intensity * gv_all[fi] * sheen_w[off0:off1],
                                      0.0, 1.0)[:, np.newaxis]
                    base    = rim_rgb[off0:off1].astype(np.float32)
                    row_rgb = (base * (1.0 - ha) + white * ha).astype(np.uint8)
                else:
                    row_rgb = rim_rgb[off0:off1]
                frame_arr[py2, cx0:cx0 + n, :3] = np.where(
                    mask[:, np.newaxis], row_rgb,
                    frame_arr[py2, cx0:cx0 + n, :3])
                frame_arr[py2, cx0:cx0 + n, 3] = np.where(
                    mask, rim_a, frame_arr[py2, cx0:cx0 + n, 3])

        frames.append(Image.fromarray(frame_arr, "RGBA"))

    return frames


def spin_frames_to_webm(frames, out_path, fps=24, size_px=512, max_kb=256):
    """Save 3D spin RGBA frames as VP9 WebM for Telegram."""
    tmp = tempfile.mkdtemp()
    try:
        # Unified bounding box across ALL frames
        min_l = frames[0].width;  min_t = frames[0].height
        max_r = 0;                max_b = 0
        for f in frames:
            arr   = np.array(f)
            alpha = arr[:, :, 3]
            cols  = np.any(alpha > 0, axis=0)
            rows  = np.any(alpha > 0, axis=1)
            if cols.any():
                l = int(np.argmax(cols))
                r = int(len(cols) - np.argmax(cols[::-1]))
                t = int(np.argmax(rows))
                b = int(len(rows) - np.argmax(rows[::-1]))
                min_l = min(min_l, l); min_t = min(min_t, t)
                max_r = max(max_r, r); max_b = max(max_b, b)
        min_l = max(0, min_l - 2);  min_t = max(0, min_t - 2)
        max_r = min(frames[0].width,  max_r + 2)
        max_b = min(frames[0].height, max_b + 2)
        crop_box = (min_l, min_t, max_r, max_b)

        cw = max_r - min_l
        ch = max_b - min_t
        for i, f in enumerate(frames):
            cropped = f.crop(crop_box)
            canvas  = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
            canvas.paste(cropped, (0, 0), cropped)
            canvas.save(os.path.join(tmp, f"frame_{i:04d}.png"), "PNG")

        pattern = os.path.join(tmp, "frame_%04d.png")
        size    = f"{size_px}:{size_px}"
        vf      = (f"scale={size}:force_original_aspect_ratio=decrease,"
                   f"pad={size}:(ow-iw)/2:(oh-ih)/2:color=black@0,"
                   f"format=yuva420p")
        for crf in [30, 36, 42, 48, 54, 60, 63]:
            cmd = ["ffmpeg", "-framerate", str(fps), "-i", pattern,
                   "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p",
                   "-b:v", "0", "-crf", str(crf), "-vf", vf,
                   "-an", "-t", str(MAX_DURATION), "-y", out_path]
            subprocess.run(cmd, capture_output=True)
            kb = os.path.getsize(out_path) / 1024
            if kb <= max_kb:
                break
        return os.path.getsize(out_path) / 1024
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ══════════════════════════════════════════════════════════════════════════════
# UI
# ══════════════════════════════════════════════════════════════════════════════

# ── Paleta monitor clínico ──────────────────────────────────────────────────
#   Estética de monitor de paciente: negro absoluto, color saturado solo en
#   datos y estados, codificación semántica fija. El color NUNCA rellena un
#   control — vive en texto y bordes. Sin gradientes, sombras ni blur.
BG0  = "#000000"   # negro absoluto — fondo principal
BG1  = "#111111"   # gris carbón — paneles elevados, preview
BG2  = "#1C1C1C"   # gris oscuro — campos, celdas, botones, listas
BORDER = "#2A2A2A" # gris muy oscuro — separadores, bordes finos
ACC  = "#00FFFF"   # cian eléctrico — acento primario / foco
ACC2 = "#00FF00"   # verde néon — éxito / estado OK
WARN = "#FFFF00"   # amarillo puro — advertencia / en proceso
ERR  = "#FF4040"   # rojo alarma — error / cancelar (AA sobre fondos oscuros)
FG   = "#FFFFFF"   # blanco — texto principal, valores
FG2  = "#C8C8C8"   # gris claro legible — texto secundario, etiquetas
FG3  = "#909090"   # gris medio — subtexto fino, placeholders
FONT      = ("Segoe UI", 10)
FONT_BOLD = ("Segoe UI", 10, "bold")
FONT_BIG  = ("Segoe UI", 14, "bold")   # título compacto (no dominante)
FONT_MONO = ("Consolas", 9)


class FileRow(tk.Frame):
    STATUS_COLORS = {
        "pending":    FG2,
        "processing": WARN,
        "done":       ACC2,
        "error":      ERR,
    }

    def __init__(self, parent, path, remove_cb, **kw):
        super().__init__(parent, bg=BG2, **kw)
        self.path      = path
        self.remove_cb = remove_cb
        self._build()

    def _build(self):
        self.columnconfigure(1, weight=1)
        self.dot = tk.Label(self, text="●", fg=FG2, bg=BG2, font=("Segoe UI", 8))
        self.dot.grid(row=0, column=0, padx=(8, 4), pady=6)
        name = os.path.basename(self.path)
        self.name_lbl = tk.Label(self, text=name, fg=FG, bg=BG2, font=FONT, anchor="w")
        self.name_lbl.grid(row=0, column=1, sticky="ew", pady=6)
        self.info_lbl = tk.Label(self, text="pending", fg=FG2, bg=BG2,
                                 font=FONT_MONO, width=22, anchor="e")
        self.info_lbl.grid(row=0, column=2, padx=6)
        tk.Button(self, text="✕", fg=FG2, bg=BG2, relief="flat",
                  activebackground=ERR, activeforeground=FG,
                  font=("Segoe UI", 9), cursor="hand2",
                  command=lambda: self.remove_cb(self)).grid(row=0, column=3, padx=(0, 6))
        tk.Frame(self, bg=BG1, height=1).grid(row=1, column=0, columnspan=4, sticky="ew")

    def set_status(self, status, info=""):
        color = self.STATUS_COLORS.get(status, FG2)
        self.dot.config(fg=color)
        self.info_lbl.config(fg=color, text=info or status)

    def set_progress(self, msg):
        self.info_lbl.config(fg=WARN, text=msg[:24])


class TelegramMaker(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Telegram WebM Maker")
        self.configure(bg=BG0)
        self.minsize(700, 640)
        self.resizable(True, True)

        self._file_rows   = []
        self._running     = False
        self._cancel_flag = threading.Event()
        self._q           = queue.Queue()

        self._spin_anim_id  = None
        self._spin_frames   = []
        self._spin_photos   = []
        self._spin_anim_idx = 0
        self._spin_q        = queue.Queue()
        self._spin_img_path = None

        self._check_ffmpeg()
        self._build_ui()
        self._build_spin_tab()
        self._set_icon()
        self._poll_queue()

    # ── FFmpeg check ──────────────────────────────────────────────────
    def _check_ffmpeg(self):
        try:
            subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
            self._ffmpeg_ok = True
        except Exception:
            self._ffmpeg_ok = False

    # ── Icon ──────────────────────────────────────────────────────────
    def _set_icon(self):
        try:
            size = 32
            img  = Image.new("RGBA", (size, size), (0, 0, 0, 0))
            d    = ImageDraw.Draw(img)
            d.ellipse([0, 0, size-1, size-1], fill="#2CA5E0")
            d.polygon([(7, 16), (26, 8), (19, 24)], fill="white")
            d.polygon([(7, 16), (15, 18), (19, 24)], fill="#d0eaf8")
            photo = ImageTk.PhotoImage(img)
            self.iconphoto(True, photo)
            self._icon_ref = photo
        except Exception:
            pass

    # ── UI BUILD ──────────────────────────────────────────────────────
    def _build_ui(self):
        hdr = tk.Frame(self, bg=BG0)
        hdr.pack(fill="x", padx=24, pady=(20, 6))
        tk.Label(hdr, text="Telegram WebM Maker",
                 font=("Segoe UI", 14, "bold"), fg=FG, bg=BG0).pack(side="left")
        tk.Label(hdr, text="v2.1", font=("Segoe UI", 9),
                 fg=ACC, bg=BG0).pack(side="left", padx=(8, 0), pady=(6, 0))

        # Línea de acento cian bajo el header (separador del sistema)
        tk.Frame(self, bg=ACC, height=2).pack(fill="x")

        if not self._ffmpeg_ok:
            tk.Label(self, text="⚠  FFmpeg not found — install it and add to PATH",
                     fg=ERR, bg=BG0, font=FONT).pack(pady=(0, 8))

        style = ttk.Style()
        style.theme_use("default")
        style.configure("TNotebook", background=BG0, borderwidth=0)
        style.configure("TNotebook.Tab", background=BG2, foreground=FG2,
                        padding=[14, 6], font=FONT)
        style.map("TNotebook.Tab",
                  background=[("selected", BG1)],
                  foreground=[("selected", ACC)])

        # ── Estilo ttk monitor clínico: troughs/sliders/combos oscuros ──
        # El tema "default" de ttk usa grises claros brillantes que rompen
        # la estética oscura. Se fuerza fondo oscuro y acento cian.
        style.configure("Horizontal.TScale",
                        background=BG0, troughcolor=BG2,
                        borderwidth=0, lightcolor=BG2, darkcolor=BG2)
        style.map("Horizontal.TScale",
                  background=[("active", BG0)])
        style.configure("TCombobox",
                        fieldbackground=BG2, background=BG2,
                        foreground=FG, arrowcolor=FG2,
                        bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER,
                        selectbackground=BG2, selectforeground=ACC)
        style.map("TCombobox",
                  fieldbackground=[("readonly", BG2)],
                  foreground=[("readonly", FG)],
                  arrowcolor=[("active", ACC)])
        style.configure("TProgressbar",
                        background=ACC, troughcolor=BG2,
                        borderwidth=0, lightcolor=ACC, darkcolor=ACC)
        style.configure("Vertical.TScrollbar",
                        background=BG2, troughcolor=BG0, bordercolor=BG0,
                        arrowcolor=FG2, lightcolor=BG2, darkcolor=BG2)
        style.configure("Horizontal.TScrollbar",
                        background=BG2, troughcolor=BG0, bordercolor=BG0,
                        arrowcolor=FG2, lightcolor=BG2, darkcolor=BG2)
        style.map("Vertical.TScrollbar",   background=[("active", BORDER)])
        style.map("Horizontal.TScrollbar", background=[("active", BORDER)])

        self._notebook = ttk.Notebook(self)
        self._notebook.pack(fill="both", expand=True, padx=24, pady=(0, 8))

        # Tab 1: GIF/WebM converter
        tab1 = tk.Frame(self._notebook, bg=BG0)
        self._notebook.add(tab1, text="  Convert GIF / WebM  ")

        body = tk.Frame(tab1, bg=BG0)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=3)
        body.columnconfigure(1, weight=2)

        # LEFT — file queue
        left = tk.Frame(body, bg=BG0)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        left.rowconfigure(1, weight=1)

        tk.Label(left, text="INPUT FILES", font=("Segoe UI", 8, "bold"),
                 fg=FG2, bg=BG0, anchor="w").grid(row=0, column=0, sticky="ew", pady=(0, 4))

        list_outer = tk.Frame(left, bg=BG2, bd=0)
        list_outer.grid(row=1, column=0, sticky="nsew")
        list_outer.rowconfigure(0, weight=1)
        list_outer.columnconfigure(0, weight=1)

        self._list_canvas = tk.Canvas(list_outer, bg=BG2, highlightthickness=0, bd=0)
        scroll = ttk.Scrollbar(list_outer, orient="vertical",
                               command=self._list_canvas.yview)
        self._list_canvas.configure(yscrollcommand=scroll.set)
        self._list_canvas.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")

        self._list_frame = tk.Frame(self._list_canvas, bg=BG2)
        self._list_window = self._list_canvas.create_window(
            (0, 0), window=self._list_frame, anchor="nw")
        self._list_frame.bind("<Configure>", self._on_list_resize)
        self._list_canvas.bind("<Configure>", self._on_canvas_resize)

        self._empty_lbl = tk.Label(self._list_frame,
            text="Click  +  to add files\n\n.gif  .png  .webp  .jpg",
            fg=FG2, bg=BG2, font=("Segoe UI", 10), justify="center")
        self._empty_lbl.pack(expand=True, pady=40)

        btn_row = tk.Frame(left, bg=BG0)
        btn_row.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        self._btn(btn_row, "+  Add Files", self._add_files, ACC).pack(side="left")
        self._btn(btn_row, "Clear All",    self._clear_files, BG2).pack(side="left", padx=(6, 0))

        # RIGHT — options + preview
        right = tk.Frame(body, bg=BG0)
        right.grid(row=0, column=1, sticky="nsew")

        tk.Label(right, text="PREVIEW", font=("Segoe UI", 8, "bold"),
                 fg=FG2, bg=BG0, anchor="w").pack(fill="x", pady=(0, 4))
        self._preview_canvas = tk.Canvas(right, bg=BG1, width=200, height=160,
                                         highlightthickness=0, bd=0)
        self._preview_canvas.pack(pady=(0, 2))
        self._preview_info = tk.Label(right, text="", fg=FG2, bg=BG0, font=FONT_MONO)
        self._preview_info.pack(anchor="w", pady=(3, 12))

        tk.Label(right, text="OUTPUT FOLDER", font=("Segoe UI", 8, "bold"),
                 fg=FG2, bg=BG0, anchor="w").pack(fill="x", pady=(0, 4))
        out_row = tk.Frame(right, bg=BG0)
        out_row.pack(fill="x", pady=(0, 12))
        out_row.columnconfigure(0, weight=1)
        self._out_var = tk.StringVar()
        tk.Entry(out_row, textvariable=self._out_var,
                 bg=BG2, fg=FG, insertbackground=FG,
                 relief="flat", font=FONT, bd=6).grid(row=0, column=0, sticky="ew")
        self._btn(out_row, "…", self._browse_out, BG2).grid(row=0, column=1, padx=(4, 0))

        opt = tk.Frame(right, bg=BG1, padx=12, pady=12)
        opt.pack(fill="x", pady=(0, 12))
        opt.columnconfigure(0, weight=1)

        tk.Label(opt, text="OPTIONS", font=("Segoe UI", 8, "bold"),
                 fg=FG2, bg=BG1, anchor="w").grid(
                 row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))

        self._crop_var = tk.StringVar(value="1 px")
        tk.Label(opt, text="Transparent crop margin", fg=FG, bg=BG1,
                 font=FONT, anchor="w").grid(row=1, column=0, sticky="w", pady=3)
        ttk.Combobox(opt, textvariable=self._crop_var, state="readonly", width=14,
            values=["No crop"] + [f"{i} px" for i in range(0, 26)]
            ).grid(row=1, column=1, sticky="e", pady=3)

        self._fps_var = tk.StringVar(value="Auto")
        tk.Label(opt, text="Output FPS", fg=FG, bg=BG1,
                 font=FONT, anchor="w").grid(row=2, column=0, sticky="w", pady=3)
        ttk.Combobox(opt, textvariable=self._fps_var, state="readonly", width=14,
            values=["Auto", "5", "10", "15", "30"]
            ).grid(row=2, column=1, sticky="e", pady=3)

        tk.Label(opt, text="Mode", fg=FG, bg=BG1,
                 font=FONT, anchor="w").grid(row=3, column=0, sticky="w", pady=(10, 2))
        self._mode_var = tk.StringVar(value="sticker")
        mode_row = tk.Frame(opt, bg=BG1)
        mode_row.grid(row=4, column=0, columnspan=2, sticky="w", pady=(0, 4))
        for val, lbl in [("sticker", "Sticker  512 px"), ("emoji", "Emoji  100 px")]:
            tk.Radiobutton(mode_row, text=lbl, variable=self._mode_var, value=val,
                           bg=BG1, fg=FG, selectcolor=BG0, activebackground=BG1,
                           font=FONT).pack(side="left", padx=(0, 12))

        self._conv_btn = tk.Button(right, text="▶  Convert",
            bg=BG2, fg=ACC, font=("Segoe UI", 11, "bold"),
            relief="flat", cursor="hand2", pady=10, bd=0,
            highlightthickness=1, highlightbackground=ACC, highlightcolor=ACC,
            activebackground=BORDER, activeforeground=ACC,
            command=self._start_conversion)
        self._conv_btn.pack(fill="x", pady=(0, 8))

        self._cancel_btn = tk.Button(right, text="■  Cancel",
            bg=BG2, fg=ERR, font=FONT,
            relief="flat", cursor="hand2", pady=6,
            state="disabled", command=self._cancel)
        self._cancel_btn.pack(fill="x")

        log_frame = tk.Frame(tab1, bg=BG1)
        log_frame.pack(fill="x", padx=0, pady=(10, 4))

        self._progress = ttk.Progressbar(log_frame, mode="determinate", maximum=100)
        self._progress.pack(fill="x", pady=(8, 4))

        self._log = tk.Text(log_frame, height=5, bg=BG1, fg=FG2,
                            font=FONT_MONO, relief="flat", state="disabled",
                            wrap="word", bd=8)
        self._log.pack(fill="x")
        self._log.tag_config("ok",   foreground=ACC2)
        self._log.tag_config("warn", foreground=WARN)
        self._log.tag_config("err",  foreground=ERR)
        self._log.tag_config("info", foreground=FG2)

    # ── 3D Spin tab ───────────────────────────────────────────────────
    def _build_spin_tab(self):
        tab2 = tk.Frame(self._notebook, bg=BG0)
        self._notebook.add(tab2, text="  3D Coin Flip  ")

        tab2.columnconfigure(0, weight=1)
        tab2.columnconfigure(1, weight=1)
        tab2.rowconfigure(0, weight=1)

        # LEFT: controls (scrollable)
        left_outer = tk.Frame(tab2, bg=BG0)
        left_outer.grid(row=0, column=0, sticky="nsew", padx=(0, 6), pady=8)
        left_outer.rowconfigure(0, weight=1)
        left_outer.columnconfigure(0, weight=1)

        lc = tk.Canvas(left_outer, bg=BG0, highlightthickness=0)
        lsb = ttk.Scrollbar(left_outer, orient="vertical", command=lc.yview)
        lc.configure(yscrollcommand=lsb.set)
        lc.grid(row=0, column=0, sticky="nsew")
        lsb.grid(row=0, column=1, sticky="ns")

        left = tk.Frame(lc, bg=BG1, padx=16, pady=14)
        lwin = lc.create_window((0, 0), window=left, anchor="nw")
        left.bind("<Configure>", lambda e: lc.configure(
            scrollregion=lc.bbox("all")))
        lc.bind("<Configure>", lambda e: lc.itemconfig(lwin, width=e.width))

        def lbl(text, row):
            tk.Label(left, text=text, bg=BG1, fg=FG2,
                     font=FONT, anchor="w").grid(
                row=row, column=0, sticky="w", pady=(8, 0))

        def val_lbl(var, row, suffix=""):
            f = tk.Frame(left, bg=BG1)
            f.grid(row=row, column=1, sticky="e", pady=(8, 0))
            tk.Label(f, textvariable=var, bg=BG1, fg=FG, font=FONT, width=5).pack(side="left")
            if suffix:
                tk.Label(f, text=suffix, bg=BG1, fg=FG2, font=FONT).pack(side="left")

        def slider(var, from_, to, row, cmd=None, res=1):
            kw = dict(from_=from_, to=to, variable=var, orient="horizontal", length=220)
            if cmd:
                kw["command"] = cmd
            ttk.Scale(left, **kw).grid(
                row=row+1, column=0, columnspan=2, sticky="ew", pady=(2, 4))

        left.columnconfigure(0, weight=1)

        # File
        tk.Label(left, text="Input image:", bg=BG1, fg=FG2, font=FONT).grid(
            row=0, column=0, sticky="w")
        self._spin_file_lbl = tk.Label(left, text="No file selected",
                                       bg=BG2, fg=FG2, font=FONT,
                                       width=24, anchor="w", padx=6)
        self._spin_file_lbl.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(2, 6))
        self._btn(left, "Browse…", self._spin_browse, ACC).grid(
            row=2, column=0, columnspan=2, sticky="ew", pady=(0, 6))

        # Crop margin
        tk.Label(left, text="Crop margin:", bg=BG1, fg=FG2, font=FONT).grid(
            row=3, column=0, sticky="w", pady=(4, 0))
        self._spin_crop_var = tk.StringVar(value="1 px")
        ttk.Combobox(left, textvariable=self._spin_crop_var, state="readonly",
                     values=["No crop"] + [f"{i} px" for i in range(0, 26)],
                     width=8).grid(row=3, column=1, sticky="e", pady=(4, 0))

        # FPS — shown first so n_frames label can reference it
        self._spin_fps_var = tk.IntVar(value=24)  # default
        lbl("FPS:", 4); val_lbl(self._spin_fps_var, 4)
        def _fps_changed(v):
            self._spin_fps_var.set(int(float(v)))
            self._update_spin_frames_label()
        slider(self._spin_fps_var, 8, 50, 4, cmd=_fps_changed)

        # Rotations count
        self._spin_rotations_var = tk.DoubleVar(value=1.0)
        lbl("Rotations in 2.99s:", 6); val_lbl(self._spin_rotations_var, 6, "x")
        def _rot_changed(v):
            rv = round(float(v) * 4) / 4   # snap to 0.25 steps
            self._spin_rotations_var.set(rv)
            self._update_spin_frames_label()
        slider(self._spin_rotations_var, 0.25, 4.0, 6, cmd=_rot_changed)

        # Frames info label (derived, read-only)
        self._spin_frames_info = tk.StringVar(value="")
        self._update_spin_frames_label()
        tk.Label(left, textvariable=self._spin_frames_info,
                 bg=BG1, fg=ACC2, font=FONT_MONO).grid(
            row=9, column=0, columnspan=2, sticky="w", pady=(0, 4))

        # Thickness % (FIX: relative to image short side)
        self._spin_thick_var = tk.DoubleVar(value=25.0)
        lbl("Thickness (% of image):", 10); val_lbl(self._spin_thick_var, 10, "%")
        def _thick_changed(v):
            rv = round(float(v) * 2) / 2    # 0.5% steps
            self._spin_thick_var.set(rv)
        slider(self._spin_thick_var, 1.0, 40.0, 10, cmd=_thick_changed)

        # Dwell: slow down at edge-on (edge mode) or face-on (face mode).
        # Mutuamente excluyente: Off / Edge / Face.
        self._spin_dwell_var = tk.IntVar(value=100)
        lbl("Dwell (drama):", 12); val_lbl(self._spin_dwell_var, 12, "%")
        self._spin_dwell_mode = tk.StringVar(value="edge")
        dwf = tk.Frame(left, bg=BG1)
        dwf.grid(row=13, column=0, columnspan=2, sticky="w", pady=(2, 2))
        for val, txt in [("none", "Off"), ("edge", "Edge (rim)"), ("face", "Face")]:
            tk.Radiobutton(dwf, text=txt, variable=self._spin_dwell_mode, value=val,
                           bg=BG1, fg=FG, selectcolor=BG0, activebackground=BG1,
                           font=FONT, command=self._on_dwell_mode).pack(
                side="left", padx=(0, 10))
        self._spin_dwell_scale = ttk.Scale(
            left, from_=0, to=100, variable=self._spin_dwell_var,
            orient="horizontal", length=220,
            command=lambda v: self._spin_dwell_var.set(int(float(v))))
        self._spin_dwell_scale.grid(row=14, column=0, columnspan=2,
                                    sticky="ew", pady=(2, 2))
        self._spin_dwell_help = tk.Label(
            left, text="↑ rim stays longer in view",
            bg=BG1, fg=FG2, font=("Segoe UI", 8), anchor="w")
        self._spin_dwell_help.grid(row=15, column=0, columnspan=2,
                                   sticky="w", pady=(0, 4))

        # Edge opacity
        self._spin_opacity_var = tk.IntVar(value=100)
        lbl("Edge opacity %:", 16); val_lbl(self._spin_opacity_var, 16)
        slider(self._spin_opacity_var, 0, 100, 16,
               cmd=lambda v: self._spin_opacity_var.set(int(float(v))))

        # Tilt
        self._spin_tilt_var = tk.IntVar(value=15)
        lbl("Tilt °:", 18); val_lbl(self._spin_tilt_var, 18)
        slider(self._spin_tilt_var, 0, 40, 18,
               cmd=lambda v: self._spin_tilt_var.set(int(float(v))))

        # Edge color  (preset dropdown + HEX field — no picker)
        tk.Label(left, text="Edge color:", bg=BG1, fg=FG2, font=FONT).grid(
            row=20, column=0, sticky="w", pady=(8, 0))
        ecf = tk.Frame(left, bg=BG1)
        ecf.grid(row=21, column=0, columnspan=2, sticky="ew", pady=(2, 6))
        ecf.columnconfigure(0, weight=1)

        self._spin_edge_var = tk.StringVar(value="Black")
        edge_cb = ttk.Combobox(ecf, textvariable=self._spin_edge_var, state="readonly",
                               values=list(self.SPIN_EDGE_COLORS.keys()) + ["Custom…"],
                               width=14)
        edge_cb.grid(row=0, column=0, columnspan=3, sticky="ew")
        edge_cb.bind("<<ComboboxSelected>>", self._on_edge_preset)

        row2 = tk.Frame(ecf, bg=BG1)
        row2.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(4, 0))
        tk.Label(row2, text="HEX:", bg=BG1, fg=FG2, font=FONT).pack(side="left")
        self._spin_edge_hex_var = tk.StringVar(value="#050505")
        hexe = tk.Entry(row2, textvariable=self._spin_edge_hex_var, width=10,
                        bg=BG2, fg=FG, insertbackground=FG, relief="flat",
                        font=FONT_MONO)
        hexe.pack(side="left", padx=(4, 6))
        hexe.bind("<FocusOut>", lambda e: self._update_edge_swatch())
        hexe.bind("<Return>",   lambda e: self._update_edge_swatch())
        self._spin_edge_swatch = tk.Label(row2, text="   ", bg="#050505",
                                          relief="solid", bd=1)
        self._spin_edge_swatch.pack(side="left", ipadx=8)

        # Flip back face
        self._spin_flip_var = tk.BooleanVar(value=False)
        tk.Checkbutton(left, text="Flip back face horizontally",
                       variable=self._spin_flip_var,
                       bg=BG1, fg=FG, selectcolor=BG0,
                       activebackground=BG1, font=FONT).grid(
            row=22, column=0, columnspan=2, sticky="w", pady=(0, 4))

        # Glint
        tk.Label(left, text="Glint:", bg=BG1, fg=FG2, font=FONT).grid(
            row=23, column=0, sticky="w", pady=(4, 0))
        self._spin_glint_mode = tk.StringVar(value="soft")
        gf = tk.Frame(left, bg=BG1)
        gf.grid(row=24, column=0, columnspan=2, sticky="w", pady=(2, 4))
        for val, txt in [("none", "Off"), ("soft", "Soft"), ("cel", "Cel")]:
            tk.Radiobutton(gf, text=txt, variable=self._spin_glint_mode, value=val,
                           bg=BG1, fg=FG, selectcolor=BG0,
                           activebackground=BG1, font=FONT).pack(side="left", padx=(0, 8))

        self._spin_glint_int_var = tk.IntVar(value=80)
        tk.Label(left, text="Glint intensity:", bg=BG1, fg=FG2, font=FONT).grid(
            row=25, column=0, sticky="w")
        tk.Label(left, textvariable=self._spin_glint_int_var, bg=BG1, fg=FG,
                 font=FONT, width=4).grid(row=25, column=1, sticky="e")
        ttk.Scale(left, from_=10, to=100, variable=self._spin_glint_int_var,
                  orient="horizontal", length=220,
                  command=lambda v: self._spin_glint_int_var.set(int(float(v)))).grid(
            row=26, column=0, columnspan=2, sticky="ew", pady=(2, 4))

        self._spin_glint_speed_var = tk.IntVar(value=1)
        tk.Label(left, text="Glint speed:", bg=BG1, fg=FG2, font=FONT).grid(
            row=27, column=0, sticky="w")
        tk.Label(left, textvariable=self._spin_glint_speed_var, bg=BG1, fg=FG,
                 font=FONT, width=4).grid(row=27, column=1, sticky="e")
        ttk.Scale(left, from_=1, to=6, variable=self._spin_glint_speed_var,
                  orient="horizontal", length=220,
                  command=lambda v: self._spin_glint_speed_var.set(int(float(v)))).grid(
            row=28, column=0, columnspan=2, sticky="ew", pady=(2, 8))

        # Edge metallic glint
        self._spin_edge_glint_var = tk.BooleanVar(value=False)
        tk.Checkbutton(left, text="Edge metal glint",
                       variable=self._spin_edge_glint_var,
                       bg=BG1, fg=FG, selectcolor=BG0,
                       activebackground=BG1, font=FONT).grid(
            row=29, column=0, columnspan=2, sticky="w", pady=(2, 0))
        self._spin_edge_glint_int_var = tk.IntVar(value=20)
        tk.Label(left, text="Edge glint intensity:", bg=BG1, fg=FG2, font=FONT).grid(
            row=30, column=0, sticky="w")
        tk.Label(left, textvariable=self._spin_edge_glint_int_var, bg=BG1, fg=FG,
                 font=FONT, width=4).grid(row=30, column=1, sticky="e")
        ttk.Scale(left, from_=10, to=100, variable=self._spin_edge_glint_int_var,
                  orient="horizontal", length=220,
                  command=lambda v: self._spin_edge_glint_int_var.set(int(float(v)))).grid(
            row=31, column=0, columnspan=2, sticky="ew", pady=(2, 8))

        # Output mode
        tk.Label(left, text="Output:", bg=BG1, fg=FG2, font=FONT).grid(
            row=32, column=0, sticky="w")
        self._spin_out_var = tk.StringVar(value="gif")
        out_frame = tk.Frame(left, bg=BG1)
        out_frame.grid(row=33, column=0, columnspan=2, sticky="w", pady=(2, 10))
        for val, txt in [("sticker", "Sticker 512px"), ("emoji", "Emoji 100px"), ("gif", "GIF")]:
            tk.Radiobutton(out_frame, text=txt, variable=self._spin_out_var, value=val,
                           bg=BG1, fg=FG, selectcolor=BG0,
                           activebackground=BG1, font=FONT).pack(side="left", padx=(0, 10))

        # Generate button
        self._spin_gen_btn = tk.Button(left, text="▶  Generate",
            bg=BG2, fg=ACC2, font=FONT_BOLD, relief="flat", bd=0,
            highlightthickness=1, highlightbackground=ACC2, highlightcolor=ACC2,
            activebackground=BORDER, activeforeground=ACC2,
            padx=10, pady=7, cursor="hand2", command=self._spin_generate)
        self._spin_gen_btn.grid(row=34, column=0, columnspan=2, sticky="ew", pady=(4, 4))

        self._spin_save_btn = tk.Button(left, text="💾  Save…",
            bg=BG2, fg=FG, font=FONT, relief="flat",
            padx=10, pady=5, cursor="hand2", state="disabled",
            command=self._spin_save)
        self._spin_save_btn.grid(row=35, column=0, columnspan=2, sticky="ew", pady=(0, 4))

        self._spin_prog = ttk.Progressbar(left, length=220, mode="determinate")
        self._spin_prog.grid(row=36, column=0, columnspan=2, sticky="ew", pady=(8, 2))
        self._spin_status = tk.Label(left, text="Ready", bg=BG1, fg=FG2, font=FONT)
        self._spin_status.grid(row=37, column=0, columnspan=2, sticky="w")

        # RIGHT: preview
        right = tk.Frame(tab2, bg=BG1, padx=10, pady=10)
        right.grid(row=0, column=1, sticky="nsew", padx=(6, 0), pady=8)
        tk.Label(right, text="Preview", font=FONT_BOLD, bg=BG1, fg=FG2).pack(anchor="w")
        self._spin_canvas = tk.Canvas(right, width=320, height=320,
                                      bg=BG2, highlightthickness=0)
        self._spin_canvas.pack(pady=(4, 0))
        self._spin_canvas.create_text(160, 160, text="Generate to preview",
                                      fill=FG2, font=FONT, tags="placeholder")

    def _update_spin_frames_label(self):
        """Recompute and display how many frames will be generated."""
        fps  = self._spin_fps_var.get()
        rots = self._spin_rotations_var.get()
        # FIX: frames = fps * MAX_DURATION, rounded to full rotations
        n = int(fps * MAX_DURATION)
        frames_per_rot = max(1, int(fps * MAX_DURATION / rots))
        n_adjusted = frames_per_rot * round(rots * frames_per_rot / frames_per_rot) if False else n
        self._spin_frames_info.set(
            f"→ {n} frames  |  {rots:.2f} rot × {fps} fps  |  {MAX_DURATION}s")

    # ── 3D Spin helpers ───────────────────────────────────────────────

    SPIN_EDGE_COLORS = {
        # Neutros
        "Black":      (5,   5,   5),
        "Charcoal":   (30,  30,  30),
        "Dark gray":  (60,  60,  60),
        "Gray":       (110, 110, 110),
        "Light gray": (180, 180, 180),
        "White":      (235, 235, 235),
        # Metales
        "Gold":       (180, 140, 30),
        "Brass":      (190, 160, 80),
        "Bronze":     (140, 95,  40),
        "Copper":     (184, 115, 51),
        "Rose gold":  (200, 150, 130),
        "Silver":     (160, 165, 170),
        "Steel":      (110, 120, 130),
        "Platinum":   (200, 205, 210),
        # Cálidos
        "Maroon":     (110, 20,  30),
        "Crimson":    (180, 30,  50),
        "Red":        (200, 30,  30),
        "Ruby":       (155, 17,  55),
        "Orange":     (220, 110, 20),
        "Amber":      (220, 160, 40),
        "Yellow":     (225, 200, 40),
        # Verdes
        "Olive":      (110, 110, 40),
        "Green":      (40,  150, 60),
        "Emerald":    (30,  160, 110),
        "Lime":       (130, 200, 50),
        "Mint":       (130, 210, 180),
        "Teal":       (20,  140, 140),
        # Fríos
        "Cyan":       (40,  185, 205),
        "Sky":        (90,  170, 230),
        "Blue":       (30,  70,  200),
        "Sapphire":   (30,  60,  150),
        "Navy":       (20,  30,  80),
        "Indigo":     (70,  40,  140),
        # Púrpuras / rosas
        "Purple":     (120, 45,  175),
        "Violet":     (150, 90,  210),
        "Magenta":    (200, 40,  160),
        "Pink":       (225, 100, 160),
    }

    @staticmethod
    def _parse_hex(s):
        """Parse '#RRGGBB' / 'RRGGBB' / '#RGB' → (r,g,b) ints, or None if invalid."""
        if not s:
            return None
        s = s.strip().lstrip("#")
        if len(s) == 3 and all(c in "0123456789abcdefABCDEF" for c in s):
            s = "".join(c * 2 for c in s)
        if len(s) != 6 or any(c not in "0123456789abcdefABCDEF" for c in s):
            return None
        return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))

    def _on_dwell_mode(self):
        """Update help text / slider state when the dwell mode changes."""
        mode = self._spin_dwell_mode.get()
        if mode == "face":
            self._spin_dwell_help.config(
                text="↑ coin pauses on the face (front and back)")
        elif mode == "edge":
            self._spin_dwell_help.config(
                text="↑ rim stays longer in view")
        else:  # none
            self._spin_dwell_help.config(text="Uniform spin speed")
        try:
            self._spin_dwell_scale.state(
                ["disabled"] if mode == "none" else ["!disabled"])
        except Exception:
            pass

    def _on_edge_preset(self, event=None):
        """Preset chosen → fill HEX field. 'Custom…' just lets the user type a HEX."""
        name = self._spin_edge_var.get()
        if name == "Custom…":
            return
        rgb = self.SPIN_EDGE_COLORS.get(name)
        if rgb:
            self._spin_edge_hex_var.set("#%02X%02X%02X" % rgb)
            self._update_edge_swatch()

    def _update_edge_swatch(self):
        """Reflect the HEX field in the swatch; normalise valid input to #RRGGBB and
        keep the preset dropdown honest (show the matching preset name, else 'Custom…')."""
        rgb = self._parse_hex(self._spin_edge_hex_var.get())
        if rgb is None:
            self._spin_edge_swatch.config(bg=ERR)   # invalid → red flag
            return
        hx = "#%02X%02X%02X" % rgb
        if self._spin_edge_hex_var.get() != hx:
            self._spin_edge_hex_var.set(hx)
        self._spin_edge_swatch.config(bg=hx)
        # sync the dropdown label to the colour without re-triggering anything
        match = next((k for k, v in self.SPIN_EDGE_COLORS.items() if v == rgb), "Custom…")
        if self._spin_edge_var.get() != match:
            self._spin_edge_var.set(match)

    def _resolve_edge_color(self):
        """Final RGB for rendering: HEX field wins; fall back to preset, then black."""
        rgb = self._parse_hex(self._spin_edge_hex_var.get())
        if rgb is not None:
            return rgb
        return self.SPIN_EDGE_COLORS.get(self._spin_edge_var.get(), (5, 5, 5))

    def _spin_browse(self):
        path = filedialog.askopenfilename(
            filetypes=[("Images", "*.png *.jpg *.jpeg *.gif *.webp *.bmp"), ("All", "*.*")])
        if not path:
            return
        self._spin_img_path = path
        name = os.path.basename(path)
        self._spin_file_lbl.config(
            text=name[:26] + ("…" if len(name) > 26 else ""), fg=FG)
        self._spin_show_static(path)

    def _spin_show_static(self, path):
        try:
            img = Image.open(path).convert("RGBA")
            img.thumbnail((320, 320), Image.LANCZOS)
            checker = make_checker_numpy(320, 320)
            checker.paste(img, ((320 - img.width) // 2, (320 - img.height) // 2), img)
            photo = ImageTk.PhotoImage(checker)
            self._spin_canvas.delete("all")
            self._spin_canvas.create_image(160, 160, anchor="center", image=photo)
            self._spin_canvas._img = photo
        except Exception:
            pass

    def _spin_generate(self):
        if not self._spin_img_path:
            self._spin_status.config(text="Select an image first.", fg=ERR)
            return
        self._spin_stop_anim()
        self._spin_gen_btn.config(state="disabled")
        self._spin_save_btn.config(state="disabled")
        self._spin_prog["value"] = 0
        self._spin_frames = []

        fps       = self._spin_fps_var.get()
        rots      = self._spin_rotations_var.get()
        thick_pct = self._spin_thick_var.get()
        opacity   = self._spin_opacity_var.get() / 100.0
        tilt      = self._spin_tilt_var.get()
        dwell_mode = self._spin_dwell_mode.get()
        edge_dwell = 0.0 if dwell_mode == "none" else self._spin_dwell_var.get() / 100.0
        dwell_face = (dwell_mode == "face")
        edge_col  = self._resolve_edge_color()
        flip      = self._spin_flip_var.get()
        glint_mode    = self._spin_glint_mode.get()
        glint_int     = self._spin_glint_int_var.get() / 100.0
        glint2_speed  = self._spin_glint_speed_var.get()
        edge_glint     = self._spin_edge_glint_var.get()
        edge_glint_int = self._spin_edge_glint_int_var.get() / 100.0
        crop_raw      = self._spin_crop_var.get()
        crop_margin   = -1 if crop_raw == "No crop" else int(crop_raw.split()[0])
        img_path      = self._spin_img_path

        # FIX: n_frames = fps * MAX_DURATION so the animation fills 2.99s exactly.
        # Then we compute frames_per_rotation to match requested rotations.
        total_frames       = max(1, int(fps * MAX_DURATION))
        frames_per_rotation = max(1, int(round(total_frames / rots)))
        # Snap total_frames to a whole number of rotations
        n_frames = frames_per_rotation * max(1, round(total_frames / frames_per_rotation))

        self._spin_status.config(
            text=f"Generating {n_frames} frames @ {fps} fps ({rots:.2f} rotations)…",
            fg=FG2)

        def progress_cb(i, total):
            pct = int(100 * i / total)
            self._spin_q.put(("progress", pct, f"Frame {i+1}/{total}…"))

        def worker():
            try:
                frames = make_3d_spin_frames(
                    img_path,
                    n_frames         = n_frames,
                    thick_pct        = thick_pct,
                    edge_color       = edge_col,
                    edge_opacity     = opacity,
                    tilt_deg         = tilt,
                    flip_back        = flip,
                    crop_margin      = crop_margin,
                    glint            = (glint_mode == "soft"),
                    glint_intensity  = glint_int,
                    glint2           = (glint_mode == "cel"),
                    glint2_intensity = glint_int,
                    glint2_speed     = glint2_speed,
                    edge_dwell       = edge_dwell,
                    dwell_face       = dwell_face,
                    edge_glint           = edge_glint,
                    edge_glint_intensity = edge_glint_int,
                    progress_cb      = progress_cb,
                )
                self._spin_q.put(("done", frames, fps))
            except Exception as e:
                self._spin_q.put(("error", str(e)))

        threading.Thread(target=worker, daemon=True).start()

    def _spin_save(self):
        if not self._spin_frames:
            return
        mode = self._spin_out_var.get()
        fps  = self._spin_fps_var.get()

        if mode == "gif":
            path = filedialog.asksaveasfilename(
                defaultextension=".gif",
                filetypes=[("GIF", "*.gif")],
                initialfile="spin3d.gif")
            if not path:
                return
            self._spin_status.config(text="Saving GIF…", fg=FG2)
            self.update()

            def worker_gif():
                try:
                    ml = self._spin_frames[0].width
                    mt = self._spin_frames[0].height
                    mr = 0; mb = 0
                    for fr in self._spin_frames:
                        aa = np.array(fr)[:, :, 3]
                        rc = np.any(aa > 0, axis=0)
                        rr = np.any(aa > 0, axis=1)
                        if rc.any():
                            ml = min(ml, int(np.argmax(rc)))
                            mt = min(mt, int(np.argmax(rr)))
                            mr = max(mr, int(len(rc) - np.argmax(rc[::-1])))
                            mb = max(mb, int(len(rr) - np.argmax(rr[::-1])))
                    box = (max(0, ml-2), max(0, mt-2),
                           min(self._spin_frames[0].width, mr+2),
                           min(self._spin_frames[0].height, mb+2))
                    cropped = [fr.crop(box) for fr in self._spin_frames]
                    dur = max(20, int(1000 / fps))
                    cropped[0].save(
                        path, save_all=True,
                        append_images=cropped[1:],
                        loop=0, duration=dur, disposal=2, optimize=False)
                    kb = os.path.getsize(path) / 1024
                    self._spin_q.put(("status", f"Saved {kb:.0f} KB", ACC2))
                except Exception as e:
                    self._spin_q.put(("status", f"Error: {e}", ERR))
            threading.Thread(target=worker_gif, daemon=True).start()
            return

        if not self._ffmpeg_ok:
            self._spin_status.config(text="FFmpeg not found.", fg=ERR)
            return
        is_sticker = mode == "sticker"
        size_px    = STICKER_PX if is_sticker else EMOJI_PX
        max_kb     = STICKER_KB if is_sticker else EMOJI_KB

        path = filedialog.asksaveasfilename(
            defaultextension=".webm",
            filetypes=[("WebM", "*.webm")],
            initialfile=f"spin3d_{'512px' if is_sticker else '100px'}.webm")
        if not path:
            return

        self._spin_status.config(text="Encoding WebM…", fg=FG2)
        self.update()

        def worker():
            try:
                kb = spin_frames_to_webm(
                    self._spin_frames, path,
                    fps=fps, size_px=size_px, max_kb=max_kb)
                msg   = f"Saved {kb:.0f} KB"
                color = ACC2 if kb <= max_kb else WARN
                if kb > max_kb:
                    msg += f" ⚠ exceeds {max_kb} KB"
                self._spin_q.put(("status", msg, color))
            except Exception as e:
                self._spin_q.put(("status", f"Error: {e}", ERR))
        threading.Thread(target=worker, daemon=True).start()

    def _spin_stop_anim(self):
        if self._spin_anim_id:
            self.after_cancel(self._spin_anim_id)
            self._spin_anim_id = None

    def _spin_tick(self):
        if not self._spin_photos:
            return
        photo = self._spin_photos[self._spin_anim_idx]
        self._spin_canvas.delete("all")
        self._spin_canvas.create_image(160, 160, anchor="center", image=photo)
        self._spin_anim_idx = (self._spin_anim_idx + 1) % len(self._spin_photos)
        fps   = self._spin_fps_var.get()
        delay = max(16, int(1000 / fps))
        self._spin_anim_id = self.after(delay, self._spin_tick)

    def _spin_build_previews(self, frames):
        """FIX: use numpy checkerboard — fast."""
        photos = []
        for f in frames:
            thumb = f.copy()
            thumb.thumbnail((320, 320), Image.LANCZOS)
            W2, H2  = thumb.size
            checker = make_checker_numpy(320, 320)
            checker.paste(thumb, ((320 - W2) // 2, (320 - H2) // 2), thumb)
            photos.append(ImageTk.PhotoImage(checker))
        return photos

    def _spin_poll(self):
        try:
            while True:
                msg  = self._spin_q.get_nowait()
                kind = msg[0]
                if kind == "progress":
                    self._spin_prog["value"] = msg[1]
                    self._spin_status.config(text=msg[2], fg=FG2)
                elif kind == "done":
                    frames = msg[1]
                    self._spin_frames   = frames
                    self._spin_prog["value"] = 100
                    self._spin_status.config(
                        text=f"Done — {len(frames)} frames", fg=ACC2)
                    self._spin_gen_btn.config(state="normal")
                    self._spin_save_btn.config(state="normal")
                    self._spin_photos   = self._spin_build_previews(frames)
                    self._spin_anim_idx = 0
                    self._spin_tick()
                elif kind == "error":
                    self._spin_status.config(text=f"Error: {msg[1]}", fg=ERR)
                    self._spin_gen_btn.config(state="normal")
                elif kind == "status":
                    self._spin_status.config(text=msg[1], fg=msg[2])
        except queue.Empty:
            pass

    # ── Helpers ───────────────────────────────────────────────────────
    def _btn(self, parent, text, cmd, bg):
        """Monitor-style button. The color NEVER fills the control background.

        The 4th argument is interpreted as a ROLE, not a fill color:
          - bg == BG2  -> normal button: dark background, light gray text.
          - bg == ACC/ACC2/...  -> PRIMARY button: dark background, text and
            border in that accent color. The color lives in text + border only.
        Keeps the original signature to avoid breaking existing call sites.
        """
        if bg == BG2:
            return tk.Button(parent, text=text, command=cmd,
                             bg=BG2, fg=FG2, relief="flat", bd=0,
                             font=FONT, padx=10, pady=5, cursor="hand2",
                             activebackground=BORDER, activeforeground=FG)
        # Primary: color goes to text + thin border, dark background
        return tk.Button(parent, text=text, command=cmd,
                         bg=BG2, fg=bg, relief="flat", bd=0,
                         font=FONT, padx=10, pady=5, cursor="hand2",
                         highlightthickness=1, highlightbackground=bg,
                         highlightcolor=bg,
                         activebackground=BORDER, activeforeground=bg)

    def _on_list_resize(self, e):
        self._list_canvas.configure(scrollregion=self._list_canvas.bbox("all"))

    def _on_canvas_resize(self, e):
        self._list_canvas.itemconfig(self._list_window, width=e.width)

    def _log_write(self, msg, tag="info"):
        self._log.config(state="normal")
        self._log.insert("end", msg + "\n", tag)
        self._log.see("end")
        self._log.config(state="disabled")

    # ── File management ───────────────────────────────────────────────
    def _add_files(self):
        paths = filedialog.askopenfilenames(
            filetypes=[("Images", "*.gif *.png *.webp *.jpg *.jpeg")])
        existing = {r.path for r in self._file_rows}
        for p in paths:
            if p not in existing:
                self._add_row(p)

    def _add_row(self, path):
        if self._empty_lbl.winfo_ismapped():
            self._empty_lbl.pack_forget()
        row = FileRow(self._list_frame, path, self._remove_row)
        row.pack(fill="x", padx=2, pady=1)
        self._file_rows.append(row)
        row.bind("<Button-1>", lambda e, p=path: self._show_preview(p))
        row.name_lbl.bind("<Button-1>", lambda e, p=path: self._show_preview(p))
        if len(self._file_rows) == 1:
            self._show_preview(path)

    def _remove_row(self, row):
        row.destroy()
        self._file_rows.remove(row)
        if not self._file_rows:
            self._empty_lbl.pack(expand=True, pady=40)
            self._preview_canvas.delete("all")
            self._preview_canvas.create_text(100, 80, text="No file selected",
                                             fill=FG2, font=FONT)
            self._preview_info.config(text="")

    def _clear_files(self):
        for r in list(self._file_rows):
            r.destroy()
        self._file_rows.clear()
        self._empty_lbl.pack(expand=True, pady=40)
        self._preview_canvas.delete("all")
        self._preview_canvas.create_text(100, 80, text="No file selected",
                                         fill=FG2, font=FONT)
        self._preview_info.config(text="")

    def _browse_out(self):
        folder = filedialog.askdirectory()
        if folder:
            self._out_var.set(folder)

    # ── Preview ───────────────────────────────────────────────────────
    def _show_preview(self, path):
        try:
            CW, CH = 200, 160
            img = Image.open(path).convert("RGBA")
            img.thumbnail((CW, CH), Image.Resampling.LANCZOS)

            # FIX: numpy checkerboard — no putpixel loop
            checker = make_checker_numpy(img.width, img.height, sq=8)
            checker.paste(img, (0, 0), img)

            photo = ImageTk.PhotoImage(checker)
            self._preview_canvas.delete("all")
            self._preview_canvas.config(bg=BG1)
            self._preview_canvas.create_image(CW // 2, CH // 2,
                                              anchor="center", image=photo)
            self._preview_canvas._img = photo

            try:
                im2    = Image.open(path)
                frames = getattr(im2, "n_frames", 1)
                w, h   = im2.size
                kb     = os.path.getsize(path) / 1024
                fps    = round(1000 / max(im2.info.get("duration", 100), 1)) if frames > 1 else "-"
                self._preview_info.config(text=f"{w}×{h}  {frames}fr  {fps}fps  {kb:.0f}KB")
            except Exception:
                pass
        except Exception:
            self._preview_canvas.delete("all")
            self._preview_canvas.create_text(100, 80, text="Preview unavailable",
                                             fill=FG2, font=FONT)

    # ── Conversion ────────────────────────────────────────────────────
    def _parse_crop(self):
        v = self._crop_var.get()
        if v == "No crop":
            return -1
        try:
            return int(v.split()[0])
        except ValueError:
            return 0

    def _parse_fps(self):
        v = self._fps_var.get()
        if v == "Auto":
            return 0
        return int(v)

    def _start_conversion(self):
        if not self._ffmpeg_ok:
            self._log_write("FFmpeg not found. Install it and add to PATH.", "err")
            return
        if not self._file_rows:
            self._log_write("No input files added.", "warn")
            return
        out_dir = self._out_var.get().strip()
        if not out_dir:
            self._log_write("Select an output folder first.", "warn")
            return

        # FIX: validate and create output folder here, before spawning thread
        try:
            os.makedirs(out_dir, exist_ok=True)
        except Exception as e:
            self._log_write(f"Cannot create output folder: {e}", "err")
            return

        self._running = True
        self._cancel_flag.clear()
        self._conv_btn.config(state="disabled")
        self._cancel_btn.config(state="normal")
        self._progress["value"] = 0

        is_sticker  = self._mode_var.get() == "sticker"
        crop_margin = self._parse_crop()
        target_fps  = self._parse_fps()
        paths       = [r.path for r in self._file_rows]
        rows_map    = {r.path: r for r in self._file_rows}

        def worker():
            total = len(paths)
            for idx, path in enumerate(paths):
                if self._cancel_flag.is_set():
                    self._q.put(("log", "Cancelled.", "warn"))
                    break
                row = rows_map[path]
                self._q.put(("row_status", row, "processing", "starting…"))
                self._q.put(("log", f"[{idx+1}/{total}] {os.path.basename(path)}", "info"))

                def prog(msg, pct, r=row, idx=idx, total=total):
                    overall = int(((idx + pct/100) / total) * 100)
                    self._q.put(("progress", overall))
                    self._q.put(("row_progress", r, msg))

                try:
                    out, kb, warn = convert_file(
                        path, out_dir, is_sticker, crop_margin, target_fps, prog)
                    if warn:
                        self._q.put(("row_status", row, "done", f"{kb:.0f} KB ⚠"))
                        self._q.put(("log", warn, "warn"))
                    else:
                        self._q.put(("row_status", row, "done", f"{kb:.0f} KB ✔"))
                        self._q.put(("log", f"  → {os.path.basename(out)}  {kb:.0f} KB", "ok"))
                except Exception as e:
                    self._q.put(("row_status", row, "error", "failed"))
                    self._q.put(("log", f"  ERROR: {e}", "err"))

            self._q.put(("done",))

        threading.Thread(target=worker, daemon=True).start()

    def _cancel(self):
        self._cancel_flag.set()

    def _poll_queue(self):
        try:
            while True:
                msg  = self._q.get_nowait()
                kind = msg[0]
                if kind == "progress":
                    self._progress["value"] = msg[1]
                elif kind == "log":
                    self._log_write(msg[1], msg[2])
                elif kind == "row_status":
                    msg[1].set_status(msg[2], msg[3])
                elif kind == "row_progress":
                    msg[1].set_progress(msg[2])
                elif kind == "done":
                    self._running = False
                    self._conv_btn.config(state="normal")
                    self._cancel_btn.config(state="disabled")
                    self._progress["value"] = 100
                    self._log_write("All done.", "ok")
        except queue.Empty:
            pass
        self._spin_poll()
        self.after(50, self._poll_queue)


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def show_fatal_error(tb: str):
    log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ERROR_LOG.txt")
    try:
        with open(log_path, "w", encoding="utf-8") as f:
            f.write(tb)
    except Exception:
        pass

    print("=" * 60)
    print("CRITICAL ERROR — see ERROR_LOG.txt")
    print("=" * 60)
    print(tb)

    try:
        root = tk.Tk()
        root.title("CRITICAL ERROR — Telegram WebM Maker")
        root.configure(bg="#000000")
        root.geometry("780x460")
        root.resizable(True, True)

        tk.Label(root, text="The application crashed before starting.",
                 fg="#FF4040", bg="#000000",
                 font=("Consolas", 11, "bold")).pack(pady=(16, 4))
        tk.Label(root, text=f"Error log saved to:  {log_path}",
                 fg="#909090", bg="#000000",
                 font=("Consolas", 9)).pack(pady=(0, 10))

        frame = tk.Frame(root, bg="#000000")
        frame.pack(fill="both", expand=True, padx=16, pady=(0, 8))

        text = tk.Text(frame, bg="#000000", fg="#00FF00",
                       font=("Consolas", 9), relief="flat", wrap="none", bd=0)
        sb_y = ttk.Scrollbar(frame, orient="vertical",   command=text.yview)
        sb_x = ttk.Scrollbar(frame, orient="horizontal", command=text.xview)
        text.configure(yscrollcommand=sb_y.set, xscrollcommand=sb_x.set)
        sb_y.pack(side="right",  fill="y")
        sb_x.pack(side="bottom", fill="x")
        text.pack(fill="both", expand=True)
        text.insert("1.0", tb)
        text.config(state="disabled")

        btn_row = tk.Frame(root, bg="#000000")
        btn_row.pack(fill="x", padx=16, pady=(0, 14))
        tk.Button(btn_row, text="Copy to clipboard",
                  bg="#1C1C1C", fg="#FFFFFF", relief="flat",
                  padx=10, pady=6, cursor="hand2",
                  command=lambda: (root.clipboard_clear(),
                                   root.clipboard_append(tb))).pack(side="left")
        tk.Button(btn_row, text="Close",
                  bg="#1C1C1C", fg="#FF4040", relief="flat", bd=0,
                  highlightthickness=1, highlightbackground="#FF4040",
                  padx=10, pady=6, cursor="hand2",
                  command=root.destroy).pack(side="right")
        root.mainloop()
    except Exception:
        try:
            import tkinter.messagebox as mb
            r2 = tk.Tk(); r2.withdraw()
            mb.showerror("Critical Error",
                         "App crashed. See ERROR_LOG.txt\n\n" + tb[:600])
            r2.destroy()
        except Exception:
            pass


if __name__ == "__main__":
    try:
        app = TelegramMaker()
        app.mainloop()
    except Exception:
        show_fatal_error(traceback.format_exc())
