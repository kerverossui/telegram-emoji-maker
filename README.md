# Telegram Sticker & Emoji WebM Maker

Converts GIFs, PNGs and WEBPs to the `.webm` format required by Telegram for **animated stickers** and **animated emojis**, with full transparency support (VP9 alpha channel).

---

## Changelog

### V 1.0.2
- Fixed critical bug: pixel format `gbrap` → `yuva420p` (caused total VP9 encoder failure)
- Fixed bug: missing `-framerate` flag in FFmpeg commands
- Fixed bug: invalid `loop=-1` video filter removed
- Auto-installation of Python dependencies on startup (Pillow, NumPy)
- Debug window with full traceback on any error
- Critical startup errors captured and saved to `ERROR_LOG.txt`
- Output files now include resolution suffix (`_512px` / `_100px`) to prevent overwrites

### V 1.0.1
- Added transparent border cropping before conversion

### V 1.0.0
- Sticker creation support at 512×512 with dynamic file size to comply with Telegram limits
- Static image conversion support

---

## About

A Python GUI application (Tkinter) that converts animated and static images to VP9 WebM format with alpha channel, fully compliant with Telegram's technical requirements. Supports stickers (512×512 px, ≤256 KB) and animated emojis (100×100 px, ≤64 KB), with dynamic CRF compression and FPS reduction to automatically meet size limits.

---

## Features

- Converts GIF, PNG, WEBP and JPG to WebM with transparency
- Automatic transparent border cropping with adjustable margin (0–3 px)
- Aspect-ratio-preserving resize with transparent padding
- Dynamic compression: automatically adjusts CRF to meet the size limit
- Configurable FPS reduction (50% or 25%) as an alternative compression method
- Sticker (512 px) and emoji (100×100 px) modes in the same interface
- Automatic resolution suffix in output filename (`_512px` / `_100px`)
- Auto-installation of Python dependencies on first run
- Debug window with full error log on any failure

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
# Clone the repository
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

1. **Input files** — select one or more `.gif`, `.png`, `.webp`, `.jpg` files
2. **Output folder** — choose where to save the `.webm` files
3. **Crop Mode** — choose whether to crop transparent borders and how many px of margin to keep
4. **Size Reduction Method** — choose between CRF-based or FPS-based compression
5. Press **Make Stickers (512px)** or **Make Emojis (100x100px)**

Output files are saved as `filename_512px.webm` or `filename_100px.webm` depending on the mode.

---

## Telegram Limits

| Type | Max size | Resolution | Max duration |
|------|----------|------------|--------------|
| Animated sticker | 256 KB | 512 × 512 px | 3 s |
| Animated emoji   | 64 KB  | 100 × 100 px | 3 s |

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| FFmpeg not found | Install it and add it to PATH. Verify with `ffmpeg -version` |
| Output file too large | The script adjusts automatically. Try a simpler GIF with fewer frames |
| Invalid GIF | Make sure the file is complete and contains animation frames |
| Pixel format error | Update to V1.0.2 which fixes the `gbrap` → `yuva420p` bug |
| Crash on startup | Check `ERROR_LOG.txt` generated in the same folder as the script |

---

## Dependencies (requirements.txt)

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
