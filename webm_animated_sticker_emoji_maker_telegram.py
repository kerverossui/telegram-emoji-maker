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
    canvas   = Image.new("RGBA", frames[0].size, (0, 0, 0, 0))
    for frame in frames:
        canvas = canvas.copy()
        canvas.paste(frame, (0, 0), frame)
        composed.append(canvas.copy())
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

        self._check_ffmpeg()
        self._build_ui()
        self._set_icon()
        self._poll_queue()

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

        # ── Two-column layout ─────────────────────────────────────────
        body = tk.Frame(self, bg=BG0)
        body.pack(fill="both", expand=True, padx=24, pady=0)
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
        log_frame = tk.Frame(self, bg=BG1)
        log_frame.pack(fill="x", padx=24, pady=(10, 16))

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
