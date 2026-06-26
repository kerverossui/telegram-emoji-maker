# Telegram Sticker & Emoji WebM Maker

Converts GIFs, PNGs and WEBPs to the `.webm` format required by Telegram for **animated stickers** and **animated emojis**, with full transparency support (VP9 alpha channel). Includes a **3D Coin Flip** generator to create spinning coin animations from any static image.

---

## Features

- Converts GIF, PNG, WEBP and JPG to WebM with transparency
- Multi-file queue — add as many files as needed and convert in one click
- **3D Coin Flip tab** — generate thick-coin 3D spin animations from any static image
- Live preview with checkerboard background to visualize transparency
- Automatic transparent border cropping with adjustable margin (0–25 px), default 1 px
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

| Control | Default | Range | Description |
|---|---|---|---|
| FPS | — | 8–50 | Output frame rate |
| Rotations in 2.99s | — | 0.25–4.0 | Full rotations per animation cycle |
| Thickness (% of image) | 25% | 1–40% | Rim width relative to the image short side |
| Dwell | Face 100% | Off / Edge / Face, 0–100% | Slows the spin at edge-on or face-on positions for dramatic effect; mutually exclusive modes |
| Edge opacity | 100% | 0–100% | 0% = pure face spin, 100% = solid coin rim |
| Tilt ° | 15° | 0–40 | Perspective tilt angle |
| Edge color | Auto-suggested | 37 presets + HEX field | Rim color; auto-suggested from image dominant hue on load |
| Edge metal glint | On 25% | checkbox + 10–100% | Metallic specular highlight sweeping along the rim |
| Glint | Soft | Off / Soft / Cel | Light reflection on coin face |
| Glint intensity | 80% | 10–100% | Strength of the face glint effect |
| Glint speed | 1 | 1–6 | How fast the face glint sweeps |
| Crop margin | 1 px | No crop / 0–25 px | Transparent border trimming before render |
| Flip back face | unchecked | checkbox | Mirror or repeat the same face on the back |

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

### v2.3.0

#### 3D Coin Flip — engine

- **Correct 3D parallax** — the visible face now shifts laterally by `(thickness/2)·sinθ·sign(cosθ)`, exactly as a real coin's front/back surface projects when rotating about the vertical axis. Previously the face stayed centered and the rim appeared as a flat slab; it now reads as a genuine disc turning in depth.
- **Rim placement fixed** — the rim always appears on the side opposite the face shift (physically correct). Previously it was placed on a fixed side regardless of angle.
- **Solid rim color** — replaced the angle-dependent brightness gradient (`cos(…) × (0.45 + 0.55·|sinθ|)`) with a flat, uniform fill. The rim color is now exactly the chosen `edge_color` with no darkening or gradients. An optional `edge_shade` parameter re-enables subtle cylinder shading when desired.
- **Edge metal glint** — new per-rim specular highlight: a Gaussian-shaped bright spot sweeps vertically along the rim in sync with the spin angle, plus a soft cross-width sheen. Simulates light sliding across a milled metal coin edge. Controlled by `edge_glint`, `edge_glint_intensity`, and `edge_glint_speed` engine parameters.
- **Dwell mode split into Edge and Face** — `_dwell_theta` now accepts a `face` boolean. Edge mode (previous behaviour) slows at θ = 90°/270° (edge-on); Face mode slows at θ = 0°/180° (face-on), causing the coin to linger showing its face and zip through the edge-on moments. Both use `φ ± dwell·A·sin(4πφ)` with amplitude `A = 0.95/(4π)`, guaranteed monotone (no reverse spin) at any dwell value.

#### 3D Coin Flip — UI

- **Dwell control reworked** — single slider replaced by a 3-way mutually exclusive radio selector: **Off** (uniform), **Edge** (rim lingers), **Face** (face lingers). Intensity slider shared. Help text updates dynamically per mode. Default: Face 100%.
- **Edge color expanded to 37 presets** — grouped by family (neutrals, metals, warms, greens, cools, purples/pinks): Black, Charcoal, Dark gray, Gray, Light gray, White, Gold, Brass, Bronze, Copper, Rose gold, Silver, Steel, Platinum, Maroon, Crimson, Red, Ruby, Orange, Amber, Yellow, Olive, Green, Emerald, Lime, Mint, Teal, Cyan, Sky, Blue, Sapphire, Navy, Indigo, Purple, Violet, Magenta, Pink.
- **HEX input field** — free-form color entry alongside the preset dropdown. Accepts `#RRGGBB`, `RRGGBB`, and shorthand `#RGB`. Normalises to `#RRGGBB` on commit; invalid input flags the swatch red. The preset dropdown syncs back to the matching name when the HEX matches a preset, otherwise shows "Custom…". HEX is the source of truth at render time. No native color picker dependency — works identically on Windows, macOS, and Linux.
- **Auto-suggested rim color on image load** — when an image is loaded, the dominant hue is extracted (HSV bucketing on a 50×50 thumbnail, median within the dominant bin, vivid-pixels-only filter), then shifted toward blue/cyan (`cool_shift = 0.22`), desaturated to 95% of original, and darkened to 45% brightness. The result is placed in the HEX field as a starting suggestion; overridable at any time.
- **Edge metal glint controls** — checkbox (default: on) + intensity slider 10–100% (default: 25%).
- **Default crop margin** — changed from 4 px to **1 px** in both the Converter and the 3D Coin Flip tab.
- **Default thickness** — changed from 21% to **25%**.
- **UI language** — all user-visible strings and code docstrings are now fully in English.

#### Convert GIF / WebM

- Default crop margin changed from 0 px to **1 px**.

---

### v2.2.0

#### 3D Coin Flip

- **Thickness now in % of image short side** — no longer in absolute px, so it looks consistent across any image resolution
- **Edge dwell slider (0–100%)** — easing function that slows the spin at the two edge-on moments (θ = 90°, 270°), giving the coin rim more screen time for dramatic effect
- **Rim shading reworked** — replaced cylindrical gradient with flat-face shading: uniform base + narrow Gaussian center highlight + soft edge AO
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
