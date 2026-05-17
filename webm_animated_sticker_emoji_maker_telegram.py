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
from PIL import Image, ImageSequence, ImageTk
import numpy as np
import tempfile
import shutil
import math

# ── TELEGRAM CONSTANTS ────────────────────────────────────────────────────────
STICKER_PX   = 512
EMOJI_PX     = 100
STICKER_KB   = 256
EMOJI_KB     = 64
MAX_DURATION = 2.9

# ══════════════════════════════════════════════════════════════════════════════
# CONVERTER ENGINE  (no UI dependencies)
# ══════════════════════════════════════════════════════════════════════════════

def get_gif_info(path):
    """Returns (frames, src_fps, is_animated). Safely caps at MAX_DURATION."""
    img    = Image.open(path)
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

    try:
        img2        = Image.open(path)
        duration_ms = img2.info.get("duration", 100)
        fps         = round(1000 / max(duration_ms, 1))
        fps         = max(1, min(fps, 60))
    except Exception:
        fps = 15

    # cap total frames to MAX_DURATION
    max_frames = int(fps * MAX_DURATION)
    frames     = frames[:max_frames]
    return frames, fps, True


def compose_gif_frames(frames):
    """
    Correctly compose GIF frames respecting disposal method,
    preventing artefacts from previous frames bleeding through.
    """
    composed = []
    size = frames[0].size
    for frame in frames:
        canvas = Image.new("RGBA", size, (0, 0, 0, 0))
        canvas.paste(frame, (0, 0), frame)
        composed.append(canvas)
    return composed


def autocrop(img, margin=0):
    """Crop transparent borders from an RGBA frame."""
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    arr   = np.array(img)
    alpha = arr[:, :, 3]
    rows  = np.any(alpha > 0, axis=1)
    cols  = np.any(alpha > 0, axis=0)
    if not rows.any():
        return img
    h, w   = arr.shape[:2]
    top    = max(0, int(np.argmax(rows))                    - margin)
    bottom = min(h, int(len(rows) - np.argmax(rows[::-1])) + margin)
    left   = max(0, int(np.argmax(cols))                    - margin)
    right  = min(w, int(len(cols) - np.argmax(cols[::-1])) + margin)
    return img.crop((left, top, right, bottom))


def normalize_frames(frames, target_fps, src_fps):
    """Drop frames evenly to reach target_fps from src_fps."""
    if target_fps >= src_fps or not frames:
        return frames
    ratio = src_fps / target_fps
    return [frames[int(i * ratio)] for i in range(int(len(frames) / ratio))] or frames


def save_frames(frames, tmp_dir):
    """Save frames as numbered PNGs. Returns pattern path."""
    max_w = max(f.width  for f in frames)
    max_h = max(f.height for f in frames)
    for i, f in enumerate(frames):
        canvas = Image.new("RGBA", (max_w, max_h), (0, 0, 0, 0))
        canvas.paste(f, ((max_w - f.width) // 2, (max_h - f.height) // 2))
        canvas.save(os.path.join(tmp_dir, f"frame_{i:04d}.png"), "PNG")
    return os.path.join(tmp_dir, "frame_%04d.png")


def run_ffmpeg(pattern, out_path, fps_in, fps_out, size_px, crf, max_kb):
    """Run FFmpeg and return (success, actual_kb)."""
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
    progress_cb(message, percent) called throughout.
    Returns (out_path, final_kb, warning_msg or None)
    """
    size_px = STICKER_PX if is_sticker else EMOJI_PX
    max_kb  = STICKER_KB if is_sticker else EMOJI_KB
    suffix  = "512px"    if is_sticker else "100px"
    base    = os.path.splitext(os.path.basename(path))[0]
    out     = os.path.join(out_dir, f"{base}_{suffix}.webm")
    tmp     = tempfile.mkdtemp()

    try:
        progress_cb("Reading frames...", 5)
        frames, src_fps, is_anim = get_gif_info(path)

        if is_anim:
            progress_cb("Composing frames...", 15)
            frames = compose_gif_frames(frames)

        if crop_margin >= 0:
            progress_cb("Cropping transparent borders...", 25)
            frames = [autocrop(f, crop_margin) for f in frames]

        fps_in  = src_fps or 1
        fps_out = fps_in
        if target_fps and target_fps < fps_in:
            progress_cb(f"Reducing FPS {fps_in} → {target_fps}...", 35)
            frames  = normalize_frames(frames, target_fps, fps_in)
            fps_out = target_fps

        progress_cb("Saving frame sequence...", 45)
        pattern = save_frames(frames, tmp)

        # Adaptive CRF loop
        warning = None
        crf_steps = [30, 36, 42, 48, 54, 60, 63]
        for i, crf in enumerate(crf_steps):
            pct = 55 + int((i / len(crf_steps)) * 40)
            progress_cb(f"Encoding  CRF {crf}  ({len(frames)} frames @ {fps_out} fps)...", pct)
            ok, kb = run_ffmpeg(pattern, out, fps_in, fps_out, size_px, crf, max_kb)
            if ok:
                break
            if i == len(crf_steps) - 1:
                warning = f"⚠ File is {kb:.0f} KB — exceeds {max_kb} KB limit (max compression applied)"

        progress_cb("Done!", 100)
        final_kb = os.path.getsize(out) / 1024
        return out, final_kb, warning

    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ══════════════════════════════════════════════════════════════════════════════
# 3D SPIN ENGINE
# ══════════════════════════════════════════════════════════════════════════════

def autocrop_rgba(img, margin=4):
    arr   = np.array(img.convert("RGBA"))
    alpha = arr[:, :, 3]
    rows  = np.any(alpha > 0, axis=1)
    cols  = np.any(alpha > 0, axis=0)
    if not rows.any():
        return img
    h, w   = arr.shape[:2]
    top    = max(0, int(np.argmax(rows)) - margin)
    bottom = min(h, int(len(rows) - np.argmax(rows[::-1])) + margin)
    left   = max(0, int(np.argmax(cols)) - margin)
    right  = min(w, int(len(cols) - np.argmax(cols[::-1])) + margin)
    return img.crop((left, top, right, bottom))


def apply_glint(face_arr, theta, intensity=0.7, angle_deg=40):
    """
    Screen-blend a diagonal white glint band over a face RGBA array.
    face_arr : (H, W, 4) uint8 numpy array — modified in-place.
    Only visible when face is facing camera (cos²(theta) falloff).
    """
    H, W = face_arr.shape[:2]
    if H < 2 or W < 2:
        return face_arr
    cos_t      = math.cos(theta)
    visibility = (cos_t ** 2) * intensity
    if visibility < 0.005:
        return face_arr
    # Band sweeps left->right within each half-rotation
    half_pos = (theta % math.pi) / math.pi      # 0..1 per half turn
    band_cx  = W * (half_pos * 1.4 - 0.2)       # enters left, exits right
    band_w   = W * 0.30
    cos_a    = math.cos(math.radians(angle_deg))
    sin_a    = math.sin(math.radians(angle_deg))
    xs       = np.arange(W, dtype=np.float32)
    ys       = np.arange(H, dtype=np.float32)
    xg, yg   = np.meshgrid(xs, ys)
    proj     = (xg - band_cx) * cos_a + (yg - H * 0.5) * (-sin_a)
    glint_a  = np.exp(-0.5 * (proj / (band_w * 0.4)) ** 2) * visibility
    glint_a *= face_arr[:, :, 3] / 255.0          # respect image alpha
    base     = face_arr[:, :, :3].astype(np.float32) / 255.0
    screen   = 1.0 - (1.0 - base) * (1.0 - glint_a[:, :, np.newaxis])
    face_arr[:, :, :3] = (screen * 255).clip(0, 255).astype(np.uint8)
    return face_arr



def apply_glint_cel(face_arr, theta, intensity=0.85):
    """
    Cel glint: thin + thick + thin vertical bars in face-space.
    Bars squish naturally with face foreshortening. Screen blend.
    """
    H, W = face_arr.shape[:2]
    if H < 2 or W < 2:
        return face_arr
    cos_t = math.cos(theta)
    if abs(cos_t) < 0.05:
        return face_arr
    half_pos = (theta % math.pi) / math.pi
    group_cx = -W * 0.3 + half_pos * (W * 1.6)
    thin_w   = W * 0.06
    thick_w  = W * 0.14
    gap      = W * 0.04
    bars = [
        (group_cx - thick_w / 2 - gap - thin_w / 2, thin_w),
        (group_cx,                                   thick_w),
        (group_cx + thick_w / 2 + gap + thin_w / 2, thin_w),
    ]
    xs   = np.arange(W, dtype=np.float32)
    mask = np.zeros(W, dtype=np.float32)
    for cx, bw in bars:
        mask += (np.abs(xs - cx) < bw * 0.5).astype(np.float32)
    mask    = np.clip(mask, 0, 1)
    glint_a = mask[np.newaxis, :] * intensity * (face_arr[:, :, 3] / 255.0)
    base    = face_arr[:, :, :3].astype(np.float32) / 255.0
    screen  = 1.0 - (1.0 - base) * (1.0 - glint_a[:, :, np.newaxis])
    face_arr[:, :, :3] = (screen * 255).clip(0, 255).astype(np.uint8)
    return face_arr


def make_3d_spin_frames(img_path,
                         n_frames      = 36,
                         thick_px      = 30,
                         edge_color    = (30, 30, 30),
                         edge_opacity  = 1.0,
                         tilt_deg      = 15,
                         flip_back     = False,
                         crop_margin   = 4,
                         glint         = False,
                         glint_intensity = 0.7,
                         glint2        = False,
                         glint2_intensity = 0.85,
                         bg_color      = (0, 0, 0, 0),
                         progress_cb   = None):
    img = Image.open(img_path).convert("RGBA")
    if crop_margin >= 0:
        img = autocrop_rgba(img, margin=crop_margin)
    W, H = img.size

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

        theta        = 2 * math.pi * i / n_frames
        cos_t        = math.cos(theta)
        sin_t        = math.sin(theta)
        x_scale      = abs(cos_t)
        facing_front = cos_t >= 0
        rim_on_right = sin_t > 0

        face_h  = max(1, int(H * y_scale))
        face_w  = max(1, int(W * x_scale)) if x_scale > 0.005 else 0
        scale_y = face_h / H

        rim_w     = max(1, int(thick_px * abs(sin_t)))
        frame_arr = np.zeros((CH, CW, 4), dtype=np.uint8)

        # Face
        if face_w >= 1:
            face = img.resize((face_w, face_h), Image.LANCZOS)
            if not facing_front and not flip_back:
                face = face.transpose(Image.FLIP_LEFT_RIGHT)
            fa  = np.array(face, dtype=np.uint8).copy()
            if glint:
                fa = apply_glint(fa, theta, intensity=glint_intensity)
            if glint2:
                fa = apply_glint_cel(fa, theta, intensity=glint2_intensity)
            px  = max(0, min(CW - face_w, cx - face_w // 2))
            py  = max(0, min(CH - face_h, cy - face_h // 2))
            ey2 = min(py + face_h, CH); ex2 = min(px + face_w, CW)
            fy2 = ey2 - py;             fx2 = ex2 - px
            if fy2 > 0 and fx2 > 0:
                src = fa[:fy2, :fx2].astype(np.float32)
                a_  = src[:, :, 3:4] / 255.0
                dst = frame_arr[py:ey2, px:ex2].astype(np.float32)
                frame_arr[py:ey2, px:ex2] = (src * a_ + dst * (1 - a_)).astype(np.uint8)

        # Rim
        if abs(sin_t) > 0.02 and rim_w >= 1 and edge_opacity > 0:
            fy_arr    = np.arange(face_h)
            src_y_arr = np.clip((fy_arr / scale_y).astype(np.int32), 0, H - 1)
            sl_arr    = row_left[src_y_arr]
            sr_arr    = row_right[src_y_arr]
            valid     = sr_arr >= sl_arr
            cy_arr    = cy - face_h // 2 + fy_arr

            eff_face_w = max(1, face_w)
            scale_x    = eff_face_w / W
            face_anchor = cx - eff_face_w // 2

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

            bright_v = np.maximum(0.10, np.cos((1.0 - t_shade) * math.pi / 2)) \
                       * (0.45 + 0.55 * abs(sin_t))
            rim_rgb  = np.clip(ec[np.newaxis, :] * bright_v[:, np.newaxis],
                               0, 255).astype(np.uint8)
            rim_a    = int(255 * edge_opacity)

            for fi in range(face_h):
                if not valid[fi]:
                    continue
                py_ = int(cy_arr[fi])
                if py_ < 0 or py_ >= CH:
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
                mask = frame_arr[py_, cx0:cx0 + n, 3] < 128
                if not mask.any():
                    continue
                frame_arr[py_, cx0:cx0 + n, :3] = np.where(
                    mask[:, np.newaxis], rim_rgb[off0:off1],
                    frame_arr[py_, cx0:cx0 + n, :3])
                frame_arr[py_, cx0:cx0 + n, 3] = np.where(
                    mask, rim_a, frame_arr[py_, cx0:cx0 + n, 3])

        frames.append(Image.fromarray(frame_arr, "RGBA"))

    return frames


def spin_frames_to_webm(frames, out_path, fps=24, size_px=512, max_kb=256):
    """Save 3D spin RGBA frames as VP9 WebM for Telegram."""
    tmp = tempfile.mkdtemp()
    try:
        # Compute tight bounding box across ALL frames so crop is consistent
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
        # Add 2px safety margin
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

BG0  = "#1a1c1f"   # deepest background
BG1  = "#22252a"   # panel background
BG2  = "#2a2e35"   # input / list background
ACC  = "#5b8dee"   # blue accent
ACC2 = "#3ecf8e"   # green accent (success)
WARN = "#f0a500"   # amber warning
ERR  = "#e05c5c"   # red error
FG   = "#e8eaf0"   # primary text
FG2  = "#8b90a0"   # secondary text
FONT      = ("Segoe UI", 10)
FONT_BOLD = ("Segoe UI", 10, "bold")
FONT_BIG  = ("Segoe UI", 13, "bold")
FONT_MONO = ("Consolas", 9)


class FileRow(tk.Frame):
    """One row in the file queue list."""
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

        # status dot
        self.dot = tk.Label(self, text="●", fg=FG2, bg=BG2, font=("Segoe UI", 8))
        self.dot.grid(row=0, column=0, padx=(8, 4), pady=6)

        # filename
        name = os.path.basename(self.path)
        self.name_lbl = tk.Label(self, text=name, fg=FG, bg=BG2,
                                 font=FONT, anchor="w")
        self.name_lbl.grid(row=0, column=1, sticky="ew", pady=6)

        # size / status text
        self.info_lbl = tk.Label(self, text="pending", fg=FG2, bg=BG2,
                                 font=FONT_MONO, width=22, anchor="e")
        self.info_lbl.grid(row=0, column=2, padx=6)

        # remove button
        tk.Button(self, text="✕", fg=FG2, bg=BG2, relief="flat",
                  activebackground=ERR, activeforeground=FG,
                  font=("Segoe UI", 9), cursor="hand2",
                  command=lambda: self.remove_cb(self)).grid(row=0, column=3, padx=(0, 6))

        # thin separator
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
        self.minsize(700, 620)
        self.resizable(True, True)

        self._file_rows   = []
        self._running     = False
        self._cancel_flag = threading.Event()
        self._q           = queue.Queue()

        # spin state — must exist before _poll_queue is called
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

    def _build_spin_tab(self):
        """Build the 3D Spin tab."""
        tab2 = tk.Frame(self._notebook, bg=BG0)
        self._notebook.add(tab2, text="  3D Spin  ")

        # ── Two-column layout ──────────────────────────────────────────
        tab2.columnconfigure(0, weight=1)
        tab2.columnconfigure(1, weight=1)
        tab2.rowconfigure(0, weight=1)

        # LEFT: controls
        left = tk.Frame(tab2, bg=BG1, padx=16, pady=14)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 6), pady=8)

        def lbl(text, row, bold=False):
            tk.Label(left, text=text, bg=BG1, fg=FG2,
                     font=FONT_BOLD if bold else FONT, anchor="w").grid(
                row=row, column=0, sticky="w", pady=(6, 0))

        def val_lbl(var, row):
            tk.Label(left, textvariable=var, bg=BG1, fg=FG,
                     font=FONT, width=5).grid(row=row, column=1, sticky="e", pady=(6, 0))

        def slider(var, from_, to, row, cmd=None):
            kw = dict(from_=from_, to=to, variable=var, orient="horizontal", length=220)
            if cmd:
                kw["command"] = cmd
            ttk.Scale(left, **kw).grid(
                row=row+1, column=0, columnspan=2, sticky="ew", pady=(2, 4))

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
            row=3, column=0, sticky="w", pady=(4,0))
        self._spin_crop_var = tk.StringVar(value="4 px")
        ttk.Combobox(left, textvariable=self._spin_crop_var, state="readonly",
                     values=["No crop"] + [f"{i} px" for i in range(0, 26)],
                     width=8).grid(row=3, column=1, sticky="e", pady=(4,0))

        # Frames
        self._spin_frames_var = tk.IntVar(value=36)
        lbl("Frames:", 5); val_lbl(self._spin_frames_var, 5)
        slider(self._spin_frames_var, 12, 72, 5,
               cmd=lambda v: self._spin_frames_var.set(int(float(v))))

        # Thickness
        self._spin_thick_var = tk.IntVar(value=20)
        lbl("Thickness px:", 7); val_lbl(self._spin_thick_var, 7)
        slider(self._spin_thick_var, 2, 200, 7,
               cmd=lambda v: self._spin_thick_var.set(int(float(v))))

        # Edge opacity
        self._spin_opacity_var = tk.IntVar(value=100)
        lbl("Edge opacity %:", 9); val_lbl(self._spin_opacity_var, 9)
        slider(self._spin_opacity_var, 0, 100, 9,
               cmd=lambda v: self._spin_opacity_var.set(int(float(v))))

        # Tilt
        self._spin_tilt_var = tk.IntVar(value=15)
        lbl("Tilt °:", 11); val_lbl(self._spin_tilt_var, 11)
        slider(self._spin_tilt_var, 0, 40, 11,
               cmd=lambda v: self._spin_tilt_var.set(int(float(v))))

        # FPS
        self._spin_fps_var = tk.IntVar(value=24)
        lbl("FPS:", 13); val_lbl(self._spin_fps_var, 13)
        slider(self._spin_fps_var, 8, 50, 13,
               cmd=lambda v: self._spin_fps_var.set(int(float(v))))

        # Edge color
        tk.Label(left, text="Edge color:", bg=BG1, fg=FG2, font=FONT).grid(
            row=15, column=0, sticky="w", pady=(8, 0))
        self._spin_edge_var = tk.StringVar(value="Black")
        edge_cb = ttk.Combobox(left, textvariable=self._spin_edge_var, state="readonly",
                               values=["Black", "Dark gray", "White",
                                       "Gold", "Silver", "Red", "Blue"], width=14)
        edge_cb.grid(row=16, column=0, columnspan=2, sticky="ew", pady=(2, 6))

        # Flip back face
        self._spin_flip_var = tk.BooleanVar(value=False)
        tk.Checkbutton(left, text="Flip back face horizontally",
                       variable=self._spin_flip_var,
                       bg=BG1, fg=FG, selectcolor=BG0,
                       activebackground=BG1, font=FONT).grid(
            row=17, column=0, columnspan=2, sticky="w", pady=(0, 4))

        # Glint effect (auto-exclusive radio buttons)
        tk.Label(left, text="Glint:", bg=BG1, fg=FG2, font=FONT).grid(
            row=18, column=0, sticky="w", pady=(4,0))
        self._spin_glint_mode = tk.StringVar(value="none")
        gf = tk.Frame(left, bg=BG1)
        gf.grid(row=19, column=0, columnspan=2, sticky="w", pady=(2,4))
        for val, txt in [("none","Off"),("soft","Soft"),("cel","Cel")]:
            tk.Radiobutton(gf, text=txt, variable=self._spin_glint_mode, value=val,
                           bg=BG1, fg=FG, selectcolor=BG0,
                           activebackground=BG1, font=FONT).pack(side="left", padx=(0,8))
        self._spin_glint_int_var = tk.IntVar(value=75)
        tk.Label(left, text="Intensity:", bg=BG1, fg=FG2, font=FONT).grid(
            row=20, column=0, sticky="w")
        tk.Label(left, textvariable=self._spin_glint_int_var, bg=BG1, fg=FG,
                 font=FONT, width=4).grid(row=20, column=1, sticky="e")
        ttk.Scale(left, from_=10, to=100, variable=self._spin_glint_int_var,
                  orient="horizontal", length=220,
                  command=lambda v: self._spin_glint_int_var.set(int(float(v)))).grid(
            row=21, column=0, columnspan=2, sticky="ew", pady=(2, 8))

        # Output mode
        tk.Label(left, text="Output:", bg=BG1, fg=FG2, font=FONT).grid(
            row=22, column=0, sticky="w")
        self._spin_out_var = tk.StringVar(value="sticker")
        out_frame = tk.Frame(left, bg=BG1)
        out_frame.grid(row=23, column=0, columnspan=2, sticky="w", pady=(2, 10))
        for val, txt in [("sticker", "Sticker 512px"), ("emoji", "Emoji 100px"), ("gif", "GIF")]:
            tk.Radiobutton(out_frame, text=txt, variable=self._spin_out_var, value=val,
                           bg=BG1, fg=FG, selectcolor=BG0,
                           activebackground=BG1, font=FONT).pack(side="left", padx=(0, 10))

        # Generate button
        self._spin_gen_btn = tk.Button(left, text="▶  Generate",
            bg=ACC2, fg="#0a1a12", font=FONT_BOLD, relief="flat",
            padx=10, pady=7, cursor="hand2", command=self._spin_generate)
        self._spin_gen_btn.grid(row=24, column=0, columnspan=2, sticky="ew", pady=(4, 4))

        # Save button
        self._spin_save_btn = tk.Button(left, text="💾  Save…",
            bg=BG2, fg=FG, font=FONT, relief="flat",
            padx=10, pady=5, cursor="hand2", state="disabled",
            command=self._spin_save)
        self._spin_save_btn.grid(row=25, column=0, columnspan=2, sticky="ew", pady=(0, 4))

        # Progress
        self._spin_prog = ttk.Progressbar(left, length=220, mode="determinate")
        self._spin_prog.grid(row=26, column=0, columnspan=2, sticky="ew", pady=(8, 2))
        self._spin_status = tk.Label(left, text="Ready", bg=BG1, fg=FG2, font=FONT)
        self._spin_status.grid(row=27, column=0, columnspan=2, sticky="w")

        # RIGHT: preview
        right = tk.Frame(tab2, bg=BG1, padx=10, pady=10)
        right.grid(row=0, column=1, sticky="nsew", padx=(6, 0), pady=8)
        tk.Label(right, text="Preview", font=FONT_BOLD, bg=BG1, fg=FG2).pack(anchor="w")
        self._spin_canvas = tk.Canvas(right, width=320, height=320,
                                      bg=BG2, highlightthickness=0)
        self._spin_canvas.pack(pady=(4, 0))
        self._spin_canvas.create_text(160, 160, text="Generate to preview",
                                      fill=FG2, font=FONT, tags="placeholder")

    # ── 3D Spin helpers ───────────────────────────────────────────────

    SPIN_EDGE_COLORS = {
        "Black":     (5,   5,   5),
        "Dark gray": (40,  40,  40),
        "White":     (220, 220, 220),
        "Gold":      (180, 140, 30),
        "Silver":    (160, 165, 170),
        "Red":       (140, 20,  20),
        "Blue":      (20,  40,  160),
    }

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
            checker = self._spin_make_checker(img.width, img.height)
            checker.paste(img, (0, 0), img)
            photo = ImageTk.PhotoImage(checker)
            self._spin_canvas.delete("all")
            self._spin_canvas.create_image(160, 160, anchor="center", image=photo)
            self._spin_canvas._img = photo
        except Exception:
            pass

    def _spin_make_checker(self, w, h, sq=10):
        checker = Image.new("RGBA", (w, h), (200, 200, 200, 255))
        for y in range(0, h, sq):
            for x in range(0, w, sq):
                if (x // sq + y // sq) % 2 == 0:
                    for py in range(y, min(y + sq, h)):
                        for px in range(x, min(x + sq, w)):
                            checker.putpixel((px, py), (160, 160, 160, 255))
        return checker

    def _spin_generate(self):
        if not hasattr(self, "_spin_img_path") or not self._spin_img_path:
            self._spin_status.config(text="Select an image first.", fg=ERR)
            return
        self._spin_stop_anim()
        self._spin_gen_btn.config(state="disabled")
        self._spin_save_btn.config(state="disabled")
        self._spin_prog["value"] = 0
        self._spin_frames = []

        n_frames  = self._spin_frames_var.get()
        thick     = self._spin_thick_var.get()
        opacity   = self._spin_opacity_var.get() / 100.0
        tilt      = self._spin_tilt_var.get()
        edge_col      = self.SPIN_EDGE_COLORS.get(self._spin_edge_var.get(), (5, 5, 5))
        flip          = self._spin_flip_var.get()
        glint_mode    = self._spin_glint_mode.get()
        glint_int     = self._spin_glint_int_var.get() / 100.0
        crop_raw      = self._spin_crop_var.get()
        crop_margin   = -1 if crop_raw == "No crop" else int(crop_raw.split()[0])
        img_path      = self._spin_img_path

        def progress_cb(i, total):
            pct = int(100 * i / total)
            self._spin_q.put(("progress", pct, f"Frame {i+1}/{total}…"))

        def worker():
            try:
                frames = make_3d_spin_frames(
                    img_path,
                    n_frames=n_frames,
                    thick_px=thick,
                    edge_color=edge_col,
                    edge_opacity=opacity,
                    tilt_deg=tilt,
                    flip_back=flip,
                    crop_margin=crop_margin,
                    glint=(glint_mode == "soft"),
                    glint_intensity=glint_int,
                    glint2=(glint_mode == "cel"),
                    glint2_intensity=glint_int,
                    progress_cb=progress_cb,
                )
                self._spin_q.put(("done", frames))
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
                    # Tight bounding box crop across all frames
                    ml=self._spin_frames[0].width; mt=self._spin_frames[0].height; mr=0; mb=0
                    for fr in self._spin_frames:
                        aa=np.array(fr)[:,:,3]
                        rc=np.any(aa>0,axis=0); rr=np.any(aa>0,axis=1)
                        if rc.any():
                            ml=min(ml,int(np.argmax(rc))); mt=min(mt,int(np.argmax(rr)))
                            mr=max(mr,int(len(rc)-np.argmax(rc[::-1]))); mb=max(mb,int(len(rr)-np.argmax(rr[::-1])))
                    box=(max(0,ml-2),max(0,mt-2),min(self._spin_frames[0].width,mr+2),min(self._spin_frames[0].height,mb+2))
                    cropped=[fr.crop(box) for fr in self._spin_frames]
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
                msg = f"Saved {kb:.0f} KB"
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
        delay = max(20, int(1000 / fps))
        self._spin_anim_id = self.after(delay, self._spin_tick)

    def _spin_build_previews(self, frames):
        photos = []
        for f in frames:
            thumb = f.copy()
            thumb.thumbnail((320, 320), Image.LANCZOS)
            W2, H2 = thumb.size
            checker = self._spin_make_checker(320, 320)
            checker.paste(thumb, ((320 - W2) // 2, (320 - H2) // 2), thumb)
            photos.append(ImageTk.PhotoImage(checker))
        return photos

    def _spin_poll(self):
        try:
            while True:
                msg = self._spin_q.get_nowait()
                kind = msg[0]
                if kind == "progress":
                    self._spin_prog["value"] = msg[1]
                    self._spin_status.config(text=msg[2], fg=FG2)
                elif kind == "done":
                    frames = msg[1]
                    self._spin_frames = frames
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


    def _set_icon(self):
        """Draw a Telegram-style paper plane as window icon."""
        try:
            from PIL import ImageDraw
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

    # ── FFmpeg check ──────────────────────────────────────────────────
    def _check_ffmpeg(self):
        try:
            subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
            self._ffmpeg_ok = True
        except Exception:
            self._ffmpeg_ok = False

    # ── UI BUILD ──────────────────────────────────────────────────────
    def _build_ui(self):
        # ── Header ───────────────────────────────────────────────────
        hdr = tk.Frame(self, bg=BG0)
        hdr.pack(fill="x", padx=24, pady=(20, 6))
        tk.Label(hdr, text="Telegram WebM Maker",
                 font=("Segoe UI", 18, "bold"), fg=FG, bg=BG0).pack(side="left")
        ver = tk.Label(hdr, text="v2.0", font=("Segoe UI", 9),
                       fg=ACC, bg=BG0)
        ver.pack(side="left", padx=(8, 0), pady=(6, 0))

        if not self._ffmpeg_ok:
            tk.Label(self, text="⚠  FFmpeg not found — install it and add to PATH",
                     fg=ERR, bg=BG0, font=FONT).pack(pady=(0, 8))

        # ── Notebook with tabs ────────────────────────────────────────
        style = ttk.Style()
        style.theme_use("default")
        style.configure("TNotebook", background=BG0, borderwidth=0)
        style.configure("TNotebook.Tab", background=BG2, foreground=FG2,
                        padding=[14, 6], font=FONT)
        style.map("TNotebook.Tab",
                  background=[("selected", BG1)],
                  foreground=[("selected", FG)])
        self._notebook = ttk.Notebook(self)
        self._notebook.pack(fill="both", expand=True, padx=24, pady=(0, 8))

        # Tab 1: GIF/WebM converter
        tab1 = tk.Frame(self._notebook, bg=BG0)
        self._notebook.add(tab1, text="  Convert GIF / WebM  ")

        # ── Two-column layout inside tab1 ────────────────────────────
        body = tk.Frame(tab1, bg=BG0)
        body.pack(fill="both", expand=True, padx=0, pady=0)
        body.columnconfigure(0, weight=3)
        body.columnconfigure(1, weight=2)

        # LEFT — file queue
        left = tk.Frame(body, bg=BG0)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        left.rowconfigure(1, weight=1)

        tk.Label(left, text="INPUT FILES", font=("Segoe UI", 8, "bold"),
                 fg=FG2, bg=BG0, anchor="w").grid(row=0, column=0, sticky="ew", pady=(0, 4))

        # file list container
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

        # drop zone label when empty
        self._empty_lbl = tk.Label(self._list_frame,
            text="Click  +  to add files\n\n.gif  .png  .webp  .jpg",
            fg=FG2, bg=BG2, font=("Segoe UI", 10), justify="center")
        self._empty_lbl.pack(expand=True, pady=40)

        # add / clear buttons
        btn_row = tk.Frame(left, bg=BG0)
        btn_row.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        self._btn(btn_row, "+  Add Files", self._add_files, ACC).pack(side="left")
        self._btn(btn_row, "Clear All",    self._clear_files, BG2).pack(side="left", padx=(6, 0))

        # ── RIGHT — options + preview ─────────────────────────────────
        right = tk.Frame(body, bg=BG0)
        right.grid(row=0, column=1, sticky="nsew")

        # preview
        tk.Label(right, text="PREVIEW", font=("Segoe UI", 8, "bold"),
                 fg=FG2, bg=BG0, anchor="w").pack(fill="x", pady=(0, 4))
        # Fixed-size preview canvas — no stretching or cropping
        self._preview_canvas = tk.Canvas(right, bg=BG1, width=200, height=160,
                                         highlightthickness=0, bd=0)
        self._preview_canvas.pack(pady=(0, 2))
        self._preview_box = self._preview_canvas  # alias for compat
        self._preview_info = tk.Label(right, text="", fg=FG2, bg=BG0, font=FONT_MONO)
        self._preview_info.pack(anchor="w", pady=(3, 12))

        # output folder
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

        # options panel — pure grid, no pack mixing
        opt = tk.Frame(right, bg=BG1, padx=12, pady=12)
        opt.pack(fill="x", pady=(0, 12))
        opt.columnconfigure(0, weight=1)

        # OPTIONS title
        tk.Label(opt, text="OPTIONS", font=("Segoe UI", 8, "bold"),
                 fg=FG2, bg=BG1, anchor="w").grid(
                 row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))

        # crop
        self._crop_var = tk.StringVar(value="0 px")
        tk.Label(opt, text="Transparent crop margin", fg=FG, bg=BG1,
                 font=FONT, anchor="w").grid(row=1, column=0, sticky="w", pady=3)
        ttk.Combobox(opt, textvariable=self._crop_var, state="readonly", width=14,
            values=["No crop"] + [f"{i} px" for i in range(0, 26)]
            ).grid(row=1, column=1, sticky="e", pady=3)

        # fps
        self._fps_var = tk.StringVar(value="Auto")
        tk.Label(opt, text="Output FPS", fg=FG, bg=BG1,
                 font=FONT, anchor="w").grid(row=2, column=0, sticky="w", pady=3)
        ttk.Combobox(opt, textvariable=self._fps_var, state="readonly", width=14,
            values=["Auto", "5", "10", "15", "30"]
            ).grid(row=2, column=1, sticky="e", pady=3)

        # mode radio
        tk.Label(opt, text="Mode", fg=FG, bg=BG1,
                 font=FONT, anchor="w").grid(row=3, column=0, sticky="w", pady=(10, 2))
        self._mode_var = tk.StringVar(value="sticker")
        mode_row = tk.Frame(opt, bg=BG1)
        mode_row.grid(row=4, column=0, columnspan=2, sticky="w", pady=(0, 4))
        for val, lbl in [("sticker", "Sticker  512 px"), ("emoji", "Emoji  100 px")]:
            tk.Radiobutton(mode_row, text=lbl, variable=self._mode_var, value=val,
                           bg=BG1, fg=FG, selectcolor=BG0, activebackground=BG1,
                           font=FONT).pack(side="left", padx=(0, 12))

        # convert button
        self._conv_btn = tk.Button(right, text="▶  Convert",
            bg=ACC, fg=FG, font=("Segoe UI", 11, "bold"),
            relief="flat", cursor="hand2", pady=10,
            activebackground="#4070cc", activeforeground=FG,
            command=self._start_conversion)
        self._conv_btn.pack(fill="x", pady=(0, 8))

        self._cancel_btn = tk.Button(right, text="■  Cancel",
            bg=BG2, fg=ERR, font=FONT,
            relief="flat", cursor="hand2", pady=6,
            state="disabled", command=self._cancel)
        self._cancel_btn.pack(fill="x")

        # ── Log / status bar ──────────────────────────────────────────
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

    # ── helpers ───────────────────────────────────────────────────────
    def _btn(self, parent, text, cmd, bg):
        return tk.Button(parent, text=text, command=cmd,
                         bg=bg, fg=FG, relief="flat",
                         font=FONT, padx=10, pady=5,
                         cursor="hand2",
                         activebackground=ACC,
                         activeforeground=FG)

    # _option_row removed — options built inline with pure grid

    def _on_list_resize(self, e):
        self._list_canvas.configure(scrollregion=self._list_canvas.bbox("all"))

    def _on_canvas_resize(self, e):
        self._list_canvas.itemconfig(self._list_window, width=e.width)

    def _log_write(self, msg, tag="info"):
        self._log.config(state="normal")
        self._log.insert("end", msg + "\n", tag)
        self._log.see("end")
        self._log.config(state="disabled")

    # ── file management ───────────────────────────────────────────────
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
            self._preview_canvas.create_text(100, 80, text="No file selected", fill=FG2, font=FONT)
            self._preview_info.config(text="")

    def _clear_files(self):
        for r in list(self._file_rows):
            r.destroy()
        self._file_rows.clear()
        self._empty_lbl.pack(expand=True, pady=40)
        self._preview_canvas.delete("all")
        self._preview_canvas.create_text(100, 80, text="No file selected", fill=FG2, font=FONT)
        self._preview_info.config(text="")

    def _browse_out(self):
        folder = filedialog.askdirectory()
        if folder:
            self._out_var.set(folder)

    # ── preview ───────────────────────────────────────────────────────
    def _show_preview(self, path):
        try:
            CW, CH = 200, 160   # canvas dimensions
            img = Image.open(path).convert("RGBA")
            img.thumbnail((CW, CH), Image.Resampling.LANCZOS)

            # checkerboard sized to image
            checker = Image.new("RGBA", img.size, (255, 255, 255, 255))
            sq = 8
            for y in range(0, img.height, sq):
                for x in range(0, img.width, sq):
                    if (x // sq + y // sq) % 2:
                        for py in range(y, min(y+sq, img.height)):
                            for px_i in range(x, min(x+sq, img.width)):
                                checker.putpixel((px_i, py), (180, 180, 180, 255))
            checker.paste(img, (0, 0), img)

            photo = ImageTk.PhotoImage(checker)
            # center image on canvas
            self._preview_canvas.delete("all")
            self._preview_canvas.config(bg=BG1)
            cx = CW // 2
            cy = CH // 2
            self._preview_canvas.create_image(cx, cy, anchor="center", image=photo)
            self._preview_canvas._img = photo  # keep reference

            # info line
            try:
                im2 = Image.open(path)
                frames = getattr(im2, "n_frames", 1)
                w, h   = im2.size
                kb     = os.path.getsize(path) / 1024
                fps    = round(1000 / max(im2.info.get("duration", 100), 1)) if frames > 1 else "-"
                self._preview_info.config(
                    text=f"{w}×{h}  {frames}fr  {fps}fps  {kb:.0f}KB")
            except Exception:
                pass
        except Exception:
            self._preview_canvas.delete("all")
            self._preview_canvas.create_text(100, 80, text="Preview unavailable",
                                             fill=FG2, font=FONT)

    # ── conversion ────────────────────────────────────────────────────
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
        os.makedirs(out_dir, exist_ok=True)

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
                self._q.put(("row_status", row, "processing", "starting..."))
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
                msg = self._q.get_nowait()
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
    """
    Last-resort error display. Works even if the main window never opened.
    1. Always writes ERROR_LOG.txt next to the script.
    2. Tries to show a standalone Tkinter error window.
    3. Falls back to a plain messagebox if the window fails too.
    """
    # 1. Write log file
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

    # 2. Try a dedicated debug window (works even without a running mainloop)
    try:
        root = tk.Tk()
        root.title("CRITICAL ERROR — Telegram WebM Maker")
        root.configure(bg="#0d0d0d")
        root.geometry("780x460")
        root.resizable(True, True)

        tk.Label(root,
                 text="The application crashed before starting.",
                 fg="#e05c5c", bg="#0d0d0d",
                 font=("Consolas", 11, "bold")).pack(pady=(16, 4))
        tk.Label(root,
                 text=f"Error log saved to:  {log_path}",
                 fg="#888888", bg="#0d0d0d",
                 font=("Consolas", 9)).pack(pady=(0, 10))

        frame = tk.Frame(root, bg="#0d0d0d")
        frame.pack(fill="both", expand=True, padx=16, pady=(0, 8))

        text = tk.Text(frame, bg="#0d0d0d", fg="#00ff88",
                       font=("Consolas", 9), relief="flat",
                       wrap="none", bd=0)
        sb_y = ttk.Scrollbar(frame, orient="vertical",   command=text.yview)
        sb_x = ttk.Scrollbar(frame, orient="horizontal", command=text.xview)
        text.configure(yscrollcommand=sb_y.set, xscrollcommand=sb_x.set)
        sb_y.pack(side="right",  fill="y")
        sb_x.pack(side="bottom", fill="x")
        text.pack(fill="both", expand=True)
        text.insert("1.0", tb)
        text.config(state="disabled")

        btn_row = tk.Frame(root, bg="#0d0d0d")
        btn_row.pack(fill="x", padx=16, pady=(0, 14))
        tk.Button(btn_row, text="Copy to clipboard",
                  bg="#2a2e35", fg="white", relief="flat",
                  padx=10, pady=6, cursor="hand2",
                  command=lambda: (root.clipboard_clear(),
                                   root.clipboard_append(tb))).pack(side="left")
        tk.Button(btn_row, text="Close",
                  bg="#e05c5c", fg="white", relief="flat",
                  padx=10, pady=6, cursor="hand2",
                  command=root.destroy).pack(side="right")

        root.mainloop()
    except Exception as e2:
        # 3. Absolute last resort — plain messagebox
        try:
            import tkinter.messagebox as mb
            r2 = tk.Tk(); r2.withdraw()
            mb.showerror("Critical Error",
                         "App crashed. See ERROR_LOG.txt\n\n" + tb[:600])


            r2.destroy()
        except Exception:
            pass  # nothing left to do


if __name__ == "__main__":
    try:
        app = TelegramMaker()
        app.mainloop()
    except Exception:
        show_fatal_error(traceback.format_exc())
