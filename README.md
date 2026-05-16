# Telegram Sticker & Emoji WebM Maker

Converts GIFs, PNGs and WEBPs to the `.webm` format required by Telegram for **animated stickers** and **animated emojis**, with full transparency support (VP9 alpha channel).

---

## Features

- Converts GIF, PNG, WEBP and JPG to WebM with transparency
- Multi-file queue — add as many files as needed and convert in one click
- **3D Spin tab** — generate thick-coin 3D spin animations from any static image
- Live preview with checkerboard background to visualize transparency
- Automatic transparent border cropping with adjustable margin (0–25 px)
- Adaptive CRF compression — tries CRF 30 → 63 until the file fits the limit
- Configurable output FPS (Auto, 5, 10, 15, 30)
- Sticker (512 px) and emoji (100 px) modes in the same interface
- Background threading — UI stays responsive during conversion
- Scrollable color-coded log (green = success, yellow = warning, red = error)
- Correct GIF frame composition — no transparency artefacts
- Auto-install of Python dependencies on first run
- Fatal error window with traceback + `ERROR_LOG.txt` for any crash

---

## Requirements

- Python 3.10 or higher
- [FFmpeg](https://ffmpeg.org/download.html) installed and added to the system PATH
- The following Python libraries (**installed automatically** on first run):
  - `Pillow`
  - `numpy`

### Installing FFmpeg on Windows

1. Download from https://ffmpeg.org/download.html
2. Extract the folder and copy the path of the `bin` subfolder
3. Add it to the system environment variables → `PATH`
4. Verify with: `ffmpeg -version` in a terminal

---

## Installation

```bash
git clone https://github.com/your-username/telegram-emoji-maker.git
cd telegram-emoji-maker

# Python dependencies install automatically on first run
# Or install manually:
pip install -r requirements.txt
```

---

## Usage

```bash
python webm_animated_sticker_emoji_maker_telegram.py
```

### Tab 1 — Convert GIF / WebM

1. Click **+ Add Files** to add one or more `.gif`, `.png`, `.webp` or `.jpg` files
2. Click a file row to preview it in the right panel
3. Set the **Output Folder**
4. Configure options: crop margin, output FPS, and sticker vs emoji mode
5. Press **▶ Convert**

Output files are saved as `filename_512px.webm` or `filename_100px.webm`.

### Tab 2 — 3D Spin

1. Click **Browse…** and select any image with a transparent background
2. Adjust thickness, edge opacity, tilt, FPS and edge color
3. Press **▶ Generate** and watch the live preview
4. Choose output format (Sticker 512px / Emoji 100px / GIF) and press **💾 Save…**

---

## Telegram Limits

| Type | Max size | Resolution | Max duration |
|------|----------|------------|--------------|
| Animated sticker | 256 KB | 512 × 512 px | 3 s |
| Animated emoji | 64 KB | 100 × 100 px | 3 s |

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| FFmpeg not found | Install it and add to PATH. Verify with `ffmpeg -version` |
| Output file too large | Script adjusts automatically up to CRF 63. Try a simpler GIF |
| Transparent artefacts | v2.0.0 fixes frame composition — update if on an older version |
| App crashes on startup | A debug window will appear with the full error. Check `ERROR_LOG.txt` |
| Conversion thread hangs | Press **■ Cancel** to stop the current batch |

---

## Changelog

### v2.1.0 — 2026-05-16

#### 3D Spin — new tab

- Shape-aware extrusion engine: rim follows exact alpha silhouette of the image, not a bounding box
- Moon-phase model: rim appears on one side only at a time, never bleeds to the opposite side
- Thickness slider (2–200 px) — absolute pixels, integer only, no decimal drift
- Edge opacity slider (0–100%) — 0% hides rim entirely for pure spinning face, 100% solid coin
- Tilt angle (0–40°) for perspective depth
- Edge color presets: Black, Dark gray, White, Gold, Silver, Red, Blue
- Flip back face checkbox — toggle between mirrored and same-orientation back side
- Live animated preview while controls are adjusted
- Export as WebM sticker (512px / 256 KB), WebM emoji (100px / 64 KB), or GIF (no FFmpeg required)
- Adaptive CRF encoding loop: tries CRF 30→63 until file fits Telegram limits

#### Tab 1 — Convert GIF / WebM

- Fixed frame stacking bug in `compose_gif_frames`: each frame now starts from a clean transparent canvas instead of accumulating previous frames

#### Architecture

- Tabbed layout with `ttk.Notebook` — Convert GIF/WebM and 3D Spin in one window
- 3D engine is pure Python + NumPy + Pillow — no additional dependencies
- All spin state initialized before UI build to prevent `AttributeError` on startup
- Threaded generation with queue-based progress updates — UI never freezes

---

### v2.0.0

- Full rewrite with a cleaner architecture: converter engine separated from UI
- Multi-file queue with per-file status indicators (pending / processing / done / error)
- Live preview with checkerboard transparency display and file info (resolution, frames, FPS, size)
- Real progress bar tracking overall batch progress
- Conversion runs on a background thread — UI never freezes
- Cancel button to stop batch mid-way
- Correct GIF frame composition respecting disposal method (fixes transparency artefacts)
- Adaptive CRF compression: auto-adjusts from CRF 30 to CRF 63 to meet Telegram size limits
- Scrollable log panel with color-coded messages (info / success / warning / error)
- Telegram-style paper plane window icon
- Fatal error window with full traceback, copy-to-clipboard, and `ERROR_LOG.txt` fallback
- Output files named with resolution suffix (`_512px` / `_100px`) to prevent overwrites
- Auto-installation of Python dependencies on first run (Pillow, NumPy)

---

### v1.0.2

- Fixed critical bug: pixel format `gbrap` → `yuva420p` (caused total VP9 encoder failure)
- Fixed bug: missing `-framerate` flag in FFmpeg commands
- Fixed bug: invalid `loop=-1` video filter removed
- Added auto-installation of Python dependencies on startup
- Added debug window with full traceback on any error
- Added `ERROR_LOG.txt` on critical startup crash
- Output files now include resolution suffix (`_512px` / `_100px`)

---

### v1.0.1

- Added transparent border cropping before conversion

---

### v1.0.0

- Sticker creation support at 512×512 with dynamic file size to comply with Telegram limits
- Static image conversion support

---

## Dependencies

```
Pillow>=10.0.0
numpy>=1.24.0
```

---

## License

MIT — free to use, modify and distribute.

---

## Credits

Built with Python, Pillow and FFmpeg.
Designed for creating compact transparent WebM animations for Telegram.
