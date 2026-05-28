# Telegram Sticker & Emoji WebM Maker

Converts GIFs, PNGs and WEBPs to the `.webm` format required by Telegram for **animated stickers** and **animated emojis**, with full transparency support (VP9 alpha channel). Includes a **3D Coin Flip** generator to create spinning coin animations from any static image.

---

## Features

- Converts GIF, PNG, WEBP and JPG to WebM with transparency
- Multi-file queue — add as many files as needed and convert in one click
- **3D Coin Flip tab** — generate thick-coin 3D spin animations from any static image
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

1. Download from <https://ffmpeg.org/download.html>
2. Extract the folder and copy the path of the `bin` subfolder
3. Add it to the system environment variables → `PATH`
4. Verify with: `ffmpeg -version` in a terminal

---

## Installation

```
git clone https://github.com/kerverossui/telegram-emoji-sticker-maker.git
cd telegram-emoji-sticker-maker

# Python dependencies install automatically on first run
# Or install manually:
pip install -r requirements.txt
```

---

## Usage

```
python webm_animated_sticker_emoji_maker_telegram.py
```

### Tab 1 — Convert GIF / WebM

1. Click **+ Add Files** to add one or more `.gif`, `.png`, `.webp` or `.jpg` files
2. Click a file row to preview it in the right panel
3. Set the **Output Folder**
4. Configure options: crop margin, output FPS, and sticker vs emoji mode
5. Press **▶ Convert**

Output files are saved as `filename_512px.webm` or `filename_100px.webm`.

### Tab 2 — 3D Coin Flip

1. Click **Browse…** and select any image with a transparent background
2. Adjust controls (see options below) and press **▶ Generate**
3. Watch the live animated preview
4. Choose output format (Sticker 512px / Emoji 100px / GIF) and press **💾 Save…**

**3D Coin Flip controls:**

| Control | Range | Description |
|---|---|---|
| FPS | 8–50 | Output frame rate |
| Rotations in 2.99s | 0.25–4.0 | Full rotations per animation cycle |
| Thickness (% of image) | 1–40% | Rim width relative to the image short side — scales with any image size |
| Edge dwell | 0–100% | Slows the spin at the edge-on moment for dramatic effect |
| Edge opacity | 0–100% | 0% = pure face spin, 100% = solid coin rim |
| Tilt ° | 0–40 | Perspective tilt angle |
| Edge color | 16 presets + hex input + system color picker | Rim color |
| Glint | Off / Soft / Cel | Light reflection on coin face |
| Glint intensity | 10–100% | Strength of the glint effect |
| Glint speed | 1–6 | How fast the glint sweeps |
| Flip back face | checkbox | Mirror or repeat the same face on the back |

---

## Telegram Limits

| Type | Max size | Resolution | Max duration |
|---|---|---|---|
| Animated sticker | 256 KB | 512 × 512 px | 3 s |
| Animated emoji | 64 KB | 100 × 100 px | 3 s |

---

## Troubleshooting

| Issue | Solution |
|---|---|
| FFmpeg not found | Install it and add to PATH. Verify with `ffmpeg -version` |
| Output file too large | Script adjusts automatically up to CRF 63. Try a simpler GIF |
| Transparent artefacts | v2.1 fixes unified frame crop — update if on an older version |
| App crashes on startup | A debug window will appear with the full error. Check `ERROR_LOG.txt` |
| Conversion thread hangs | Press **■ Cancel** to stop the current batch |

---

## Changelog

### v2.2.0

#### 3D Coin Flip

- **Thickness now in % of image short side** — no longer in absolute px, so it looks consistent across any image resolution
- **Edge dwell slider (0–100%)** — easing function that slows the spin at the two edge-on moments (θ = 90°, 270°), giving the coin rim more screen time for dramatic effect
- **Rim shading reworked** — replaced cylindrical gradient with flat-face shading: uniform base + narrow Gaussian center highlight + soft edge AO; coin edge now looks like a coin, not a tube
- **Full color picker for edge color** — 16 color swatches, live hex input field with preview swatch, and system native color chooser dialog; replaces the 7-item dropdown
- **Rotations in 2.99s slider** — replaces fixed frame count; controls how many full rotations occur in the animation (0.25×–4×, snaps to 0.25 steps)
- **Frames computed from FPS × 2.99** — animation always fills exactly 2.99 s at the chosen FPS; frame count is derived, not set manually

#### Convert GIF / WebM

- Checkerboard background rendered with NumPy — no more O(n²) `putpixel` loop; preview loads instantly on large images
- Unified autocrop across all frames — prevents content jumping between frames when the animated subject moves
- `makedirs` validation happens before thread spawn — folder errors surface in the log immediately

---

### v2.1.0

#### 3D Spin — new tab

- Shape-aware extrusion engine: rim follows exact alpha silhouette of the image, not a bounding box
- Moon-phase model: rim appears on one side only at a time, never bleeds to the opposite side
- Thickness slider (2–200 px) — absolute pixels
- Edge opacity slider (0–100%)
- Tilt angle (0–40°) for perspective depth
- Edge color presets: Black, Dark gray, White, Gold, Silver, Red, Blue
- Flip back face checkbox
- Live animated preview
- Export as WebM sticker (512px / 256 KB), WebM emoji (100px / 64 KB), or GIF
- Adaptive CRF encoding loop: tries CRF 30→63 until file fits Telegram limits
- `MAX_DURATION` set to 2.99 s to maximise animation length within Telegram's 3 s cap

#### Convert GIF / WebM

- Fixed frame stacking bug in `compose_gif_frames`
- GIF `duration` read from frame-0 before any `seek()` to correctly detect FPS

#### Architecture

- Tabbed layout with `ttk.Notebook`
- 3D engine: pure Python + NumPy + Pillow, no extra dependencies
- Threaded generation with queue-based progress updates

---

### v2.0.0

- Full rewrite: converter engine separated from UI
- Multi-file queue with per-file status indicators
- Live preview with checkerboard transparency display
- Real progress bar tracking overall batch progress
- Conversion runs on a background thread — UI never freezes
- Cancel button to stop batch mid-way
- Adaptive CRF compression: auto-adjusts from CRF 30 to CRF 63
- Scrollable log panel with color-coded messages
- Telegram-style paper plane window icon
- Fatal error window with full traceback + `ERROR_LOG.txt` fallback
- Output files named with resolution suffix (`_512px` / `_100px`)
- Auto-installation of Python dependencies on first run

---

### v1.0.2

- Fixed critical bug: pixel format `gbrap` → `yuva420p`
- Fixed missing `-framerate` flag in FFmpeg commands
- Fixed invalid `loop=-1` video filter
- Added auto-install of Python dependencies
- Added debug window with full traceback on error

---

### v1.0.1

- Added transparent border cropping before conversion

---

### v1.0.0

- Sticker creation support at 512×512 px
- Static image conversion support

---

## Dependencies (requirements.txt)

```
Pillow>=10.0.0
numpy>=1.24.0
```

---

## License

GPL-3.0 — see [LICENSE](LICENSE) for full terms.

---

## Credits

Built with Python, Pillow and FFmpeg.
Designed for creating compact transparent WebM animations for Telegram.
