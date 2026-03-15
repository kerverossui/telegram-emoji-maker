import subprocess
import sys
import os
import traceback

# --- AUTO-INSTALACIÓN DE DEPENDENCIAS ---
def bootstrap():
    """Instala Pillow y numpy automáticamente si no están presentes."""
    dependencies = ["Pillow", "numpy"]
    needs_restart = False
    for lib in dependencies:
        try:
            if lib == "Pillow": import PIL
            else: import numpy
        except ImportError:
            print(f"Instalando {lib}...")
            try:
                subprocess.check_call([sys.executable, "-m", "pip", "install", lib])
                needs_restart = True
            except Exception as e:
                print(f"Error instalando {lib}: {e}")
    if needs_restart:
        print("Dependencias instaladas. Reiniciando...")
        os.execv(sys.executable, ['python'] + sys.argv)

bootstrap()

import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from PIL import Image, ImageSequence
import tempfile
import shutil
import math
import numpy as np

# Configuration
MAX_SIZE_KB_STICKER = 256
MAX_SIZE_KB_EMOJI = 64
MAX_DURATION_ANIMATED = 2.95  # seconds for animated GIFs, strictly enforced
MAX_DURATION_STATIC = 2.0  # seconds for static images
STICKER_SIZE = 512  # One side must be 512 pixels
EMOJI_SIZE = (100, 100)  # Exactly 100x100 pixels
DEFAULT_FPS = 30  # Default frame rate for static images

class WebMStickerEmojiApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Telegram Sticker & Emoji WebM Converter")
        self.root.state('zoomed')  # Maximize the window by default
        self.root.configure(bg="#2c2f33")  # Dark theme background

        # Style configuration
        style = ttk.Style()
        style.configure("TButton", font=("Arial", 10), padding=10)
        style.map("TButton", background=[("active", "#45a049")])
        style.configure("TLabel", font=("Arial", 10), background="#2c2f33", foreground="#ffffff")
        style.configure("TEntry", fieldbackground="#2c2f33", foreground="#ffffff", insertbackground="#ffffff")
        style.map("TEntry", fieldbackground=[("disabled", "#2c2f33"), ("!disabled", "#2c2f33")])
        style.configure("TRadiobutton", background="#2c2f33", foreground="#ffffff")
        style.configure("TCombobox", fieldbackground="#2c2f33", background="#2c2f33", foreground="#ffffff")

        # Main frame
        main_frame = tk.Frame(root, bg="#2c2f33")
        main_frame.pack(padx=20, pady=20, fill="both", expand=True)

        # Title
        tk.Label(main_frame, text="Telegram Sticker & Emoji Converter", font=("Arial", 16, "bold"), bg="#2c2f33", fg="#ffffff").pack(pady=10)
        tk.Label(main_frame, text="Create WebM stickers (512px side, ≤256KB) or emoji (100x100px, ≤64KB)", font=("Arial", 10), bg="#2c2f33", fg="#bbbbbb").pack(pady=5)

        # Input files
        tk.Label(main_frame, text="Select Input Images (GIF, PNG, JPEG, WEBP):", bg="#2c2f33", fg="#ffffff").pack(pady=5)
        self.input_entry = ttk.Entry(main_frame, width=50, style="TEntry")
        self.input_entry.configure(background="#2c2f33")
        self.input_entry.pack(pady=5)
        ttk.Button(main_frame, text="Browse", command=self.browse_input).pack(pady=5)

        # Output folder
        tk.Label(main_frame, text="Select Output Folder:", bg="#2c2f33", fg="#ffffff").pack(pady=5)
        self.output_entry = ttk.Entry(main_frame, width=50, style="TEntry")
        self.output_entry.configure(background="#2c2f33")
        self.output_entry.pack(pady=5)
        ttk.Button(main_frame, text="Browse", command=self.browse_output_folder).pack(pady=5)

        # Crop mode selection
        tk.Label(main_frame, text="Crop Mode (Optional):", bg="#2c2f33", fg="#ffffff").pack(pady=5)
        self.crop_mode = tk.StringVar(value="No Crop")
        crop_options = ["No Crop", "Full Crop", "1px Border", "2px Border", "3px Border"]
        self.crop_combo = ttk.Combobox(main_frame, textvariable=self.crop_mode, values=crop_options, state="readonly", style="TCombobox")
        self.crop_combo.pack(pady=5)

        # Size reduction method
        tk.Label(main_frame, text="Size Reduction Method:", bg="#2c2f33", fg="#ffffff").pack(pady=5)
        self.size_reduction_var = tk.StringVar(value="crf")
        ttk.Radiobutton(main_frame, text="Reduce Image Quality (CRF)", value="crf", variable=self.size_reduction_var, style="TRadiobutton").pack(pady=2)
        ttk.Radiobutton(main_frame, text="Reduce FPS by 50% (e.g., 30 to 15 FPS)", value="fps_50", variable=self.size_reduction_var, style="TRadiobutton").pack(pady=2)
        ttk.Radiobutton(main_frame, text="Reduce FPS by 25% (e.g., 30 to 22.5 FPS)", value="fps_25", variable=self.size_reduction_var, style="TRadiobutton").pack(pady=2)

        # Convert buttons
        ttk.Button(main_frame, text="Make Stickers (512px side)", command=lambda: self.safe_convert(is_sticker=True), style="TButton").pack(pady=10)
        ttk.Button(main_frame, text="Make Emojis (100x100px)", command=lambda: self.safe_convert(is_sticker=False), style="TButton").pack(pady=10)

        # Status label
        self.status_label = tk.Label(main_frame, text="", font=("Arial", 10), bg="#2c2f33", fg="#bbbbbb")
        self.status_label.pack(pady=5)

    def show_debug(self, error_trace):
        """Muestra una ventana con el traceback completo del error."""
        debug_win = tk.Toplevel(self.root)
        debug_win.title("DEBUG LOG - Error Detectado")
        debug_win.configure(bg="black")
        tk.Label(debug_win, text="ERROR DETALLADO", fg="#FF4444", bg="black",
                 font=("Consolas", 11, "bold")).pack(pady=(8, 2))
        text = tk.Text(debug_win, bg="black", fg="#00FF00",
                       font=("Consolas", 10), width=90, height=30)
        text.insert("1.0", error_trace)
        text.config(state="disabled")
        text.pack(padx=10, pady=10, expand=True, fill="both")
        ttk.Button(debug_win, text="Cerrar",
                   command=debug_win.destroy).pack(pady=(0, 10))

    def safe_convert(self, is_sticker):
        """Llama a convert() con captura total de errores y ventana de debug."""
        try:
            self.convert(is_sticker=is_sticker)
        except Exception:
            self.show_debug(traceback.format_exc())

    def browse_input(self):
        files = filedialog.askopenfilenames(filetypes=[("Image files", "*.gif;*.png;*.jpg;*.jpeg;*.webp")])
        if files:
            self.input_entry.delete(0, tk.END)
            self.input_entry.insert(0, ";".join(files))
            self.status_label.config(text=f"{len(files)} input files selected")

    def browse_output_folder(self):
        folder = filedialog.askdirectory()
        if folder:
            self.output_entry.delete(0, tk.END)
            self.output_entry.insert(0, folder)
            self.status_label.config(text="Output folder selected")

    def get_content_bounds(self, frame, border=0):
        """Get the bounding box of non-transparent content in a frame with optional border."""
        if frame.mode != 'RGBA':
            frame = frame.convert('RGBA')
        
        img_array = np.array(frame)
        alpha = img_array[:, :, 3]
        
        non_transparent = np.where(alpha > 0)
        if len(non_transparent[0]) == 0:
            return None
        
        y_min, y_max = np.min(non_transparent[0]), np.max(non_transparent[0])
        x_min, x_max = np.min(non_transparent[1]), np.max(non_transparent[1])
        
        y_min = max(0, y_min - border)
        y_max = min(frame.size[1], y_max + border + 1)
        x_min = max(0, x_min - border)
        x_max = min(frame.size[0], x_max + border + 1)
        
        return (x_min, y_min, x_max, y_max)

    def resize_to_fit(self, image, target_width, target_height):
        """Resize image to fit within target dimensions while maintaining aspect ratio."""
        orig_width, orig_height = image.size
        target_ratio = target_width / target_height
        img_ratio = orig_width / orig_height

        if img_ratio > target_ratio:
            new_width = target_width
            new_height = int(target_width / img_ratio)
        else:
            new_height = target_height
            new_width = int(target_height * img_ratio)
        
        return image.resize((new_width, new_height), Image.Resampling.LANCZOS)

    def process_animated_image_crop(self, input_path, temp_dir, target_width, target_height, border):
        """Process an animated image with cropping."""
        try:
            img = Image.open(input_path)
            if not hasattr(img, 'is_animated') or not img.is_animated:
                return self.process_static_image_crop(input_path, temp_dir, target_width, target_height, border)

            frames = []
            durations = []
            bounds = None

            for frame in ImageSequence.Iterator(img):
                frame = frame.convert('RGBA')
                frame_bounds = self.get_content_bounds(frame, border)
                
                if frame_bounds:
                    if bounds is None:
                        bounds = frame_bounds
                    else:
                        bounds = (
                            min(bounds[0], frame_bounds[0]),
                            min(bounds[1], frame_bounds[1]),
                            max(bounds[2], frame_bounds[2]),
                            max(bounds[3], frame_bounds[3])
                        )
                
                frames.append(frame)
                durations.append(frame.info.get('duration', 100) / 1000)

            if bounds is None:
                raise Exception("No non-transparent content found")

            cropped_frames = [frame.crop(bounds) for frame in frames]
            resized_frames = [self.resize_to_fit(frame, target_width, target_height) for frame in cropped_frames]
            
            final_frames = []
            temp_files = []
            for i, frame in enumerate(resized_frames):
                canvas = Image.new('RGBA', (target_width, target_height), (0, 0, 0, 0))
                offset_x = (target_width - frame.size[0]) // 2
                offset_y = (target_height - frame.size[1]) // 2
                canvas.paste(frame, (offset_x, offset_y))
                temp_file = os.path.join(temp_dir, f"frame_{i:04d}.png")
                canvas.save(temp_file, format="PNG")
                temp_files.append(temp_file)
                final_frames.append(canvas)
            
            return final_frames, durations, temp_files

        except Exception as e:
            raise Exception(f"Error processing animated image: {str(e)}")

    def process_static_image_crop(self, input_path, temp_dir, target_width, target_height, border):
        """Process a static image with cropping."""
        try:
            img = Image.open(input_path).convert('RGBA')
            bounds = self.get_content_bounds(img, border)
            
            if bounds is None:
                raise Exception("No non-transparent content found")

            cropped = img.crop(bounds)
            resized = self.resize_to_fit(cropped, target_width, target_height)
            
            canvas = Image.new('RGBA', (target_width, target_height), (0, 0, 0, 0))
            offset_x = (target_width - resized.size[0]) // 2
            offset_y = (target_height - resized.size[1]) // 2
            canvas.paste(resized, (offset_x, offset_y))
            
            temp_file = os.path.join(temp_dir, "frame_0000.png")
            canvas.save(temp_file, format="PNG")
            
            frame_count = int(MAX_DURATION_STATIC * DEFAULT_FPS)
            return [canvas] * frame_count, [MAX_DURATION_STATIC / frame_count] * frame_count, [temp_file] * frame_count

        except Exception as e:
            raise Exception(f"Error processing static image: {str(e)}")

    def is_animated_image(self, image_path):
        """Check if the input is an animated image (GIF or WEBP)."""
        try:
            with Image.open(image_path) as im:
                return im.is_animated if hasattr(im, "is_animated") else False
        except Exception:
            return False

    def get_duration(self, image_path, is_animated):
        """Calculate duration and frame durations for input image, strictly capping at 2.95s for animated."""
        if is_animated:
            try:
                with Image.open(image_path) as im:
                    durations = [frame.info.get('duration', 100) / 1000 for frame in ImageSequence.Iterator(im)]
                    total_duration = sum(durations)
                    if total_duration > MAX_DURATION_ANIMATED:
                        speed_factor = total_duration / MAX_DURATION_ANIMATED
                        durations = [d / speed_factor for d in durations]
                        total_duration = MAX_DURATION_ANIMATED
                    else:
                        total_duration = min(total_duration, MAX_DURATION_ANIMATED)
                    return total_duration, durations
            except Exception as e:
                messagebox.showerror("Error", f"Failed to read animated image: {e}")
                return 0, []
        else:
            frame_count = int(MAX_DURATION_STATIC * DEFAULT_FPS)
            frame_duration = MAX_DURATION_STATIC / frame_count
            return MAX_DURATION_STATIC, [frame_duration] * frame_count

    def resize_frame(self, frame, is_sticker=True):
        """Resize a single frame to Telegram sticker or emoji dimensions."""
        if frame.mode != "RGBA":
            frame = frame.convert("RGBA")
        
        if is_sticker:
            width, height = frame.size
            aspect_ratio = width / height
            if width >= height:
                target_width = STICKER_SIZE
                target_height = min(int(STICKER_SIZE / aspect_ratio), STICKER_SIZE)
            else:
                target_height = STICKER_SIZE
                target_width = min(int(STICKER_SIZE * aspect_ratio), STICKER_SIZE)
            target_size = (target_width, target_height)
        else:
            target_size = EMOJI_SIZE

        frame.thumbnail(target_size, Image.Resampling.LANCZOS)
        new_frame = Image.new("RGBA", target_size, (0, 0, 0, 0))
        offset = ((target_size[0] - frame.size[0]) // 2, (target_size[1] - frame.size[1]) // 2)
        new_frame.paste(frame, offset)
        return new_frame

    def create_webm(self, input_pattern, output_path, duration, frame_count, is_animated, is_sticker):
        """Convert image sequence to VP9 WebM with FFmpeg, strictly enforcing duration."""
        try:
            max_duration = MAX_DURATION_ANIMATED if is_animated else MAX_DURATION_STATIC
            max_size_kb  = MAX_SIZE_KB_STICKER if is_sticker else MAX_SIZE_KB_EMOJI
            scale        = "512:512" if is_sticker else "100:100"
            total_duration = min(duration, max_duration)
            fps          = max(1.0, frame_count / max(total_duration, 0.001))
            speed_factor = duration / max_duration if duration > max_duration else 1.0
            setpts       = f"setpts={1/speed_factor}*PTS"

            # FIX: vf base sin loop (loop=-1 no es filtro vf válido)
            vf_base = (
                f"{setpts},"
                f"scale={scale}:force_original_aspect_ratio=decrease,"
                f"pad={scale}:(ow-iw)/2:(oh-ih)/2:color=black@0,"
                f"format=yuva420p"           # ← convierte gbrap/rgba → yuva420p
            )

            def run_ffmpeg(vf, out_fps, crf):
                cmd = [
                    "ffmpeg",
                    "-framerate", str(out_fps),   # ← fps de entrada del image sequence
                    "-i", input_pattern,
                    "-c:v", "libvpx-vp9",
                    "-pix_fmt", "yuva420p",        # ← pixel format con alpha para VP9
                    "-b:v", "0",
                    "-crf", str(crf),
                    "-vf", vf,
                    "-r", str(out_fps),            # ← fps de salida
                    "-an",
                    "-t", str(max_duration),
                    "-y",
                    output_path
                ]
                subprocess.run(cmd, check=True, capture_output=True)

            size_reduction_method = self.size_reduction_var.get()

            if size_reduction_method == "crf":
                crf = 30
                max_attempts = 5
                for attempt in range(max_attempts):
                    self.status_label.config(text=f"Converting with CRF={crf}...")
                    self.root.update()
                    run_ffmpeg(vf_base, fps, crf)
                    if os.path.getsize(output_path) / 1024 <= max_size_kb:
                        break
                    crf += 5
                    if attempt == max_attempts - 1:
                        messagebox.showwarning("Warning", f"WebM exceeds {max_size_kb} KB. Using highest compression.")
            else:
                fps_reduction_factor = 0.5 if size_reduction_method == "fps_50" else 0.75
                current_fps = max(1.0, fps * fps_reduction_factor)
                max_attempts = 3
                for attempt in range(max_attempts):
                    self.status_label.config(text=f"Converting with FPS={current_fps:.1f}...")
                    self.root.update()
                    run_ffmpeg(vf_base, current_fps, 30)
                    if os.path.getsize(output_path) / 1024 <= max_size_kb:
                        break
                    current_fps = max(1.0, current_fps * 0.75)
                    if attempt == max_attempts - 1:
                        messagebox.showwarning("Warning", f"WebM exceeds {max_size_kb} KB after FPS reduction.")
            self.status_label.config(text="Conversion complete!")
        except subprocess.CalledProcessError as e:
            self.status_label.config(text="Conversion failed")
            messagebox.showerror("Error", f"FFmpeg failed: {e.stderr.decode()}")
        except Exception as e:
            self.status_label.config(text="Conversion failed")
            messagebox.showerror("Error", f"Failed to create WebM: {e}")

    def convert(self, is_sticker=True):
        """Handle batch conversion process with optional cropping."""
        input_paths = self.input_entry.get().split(";")
        output_folder = self.output_entry.get()

        if not input_paths or not output_folder:
            self.status_label.config(text="Missing file paths or output folder")
            messagebox.showerror("Error", "Please specify both input files and output folder.")
            return

        valid_inputs = [path for path in input_paths if os.path.exists(path)]
        if not valid_inputs:
            self.status_label.config(text="No valid input files")
            messagebox.showerror("Error", "No valid input files found.")
            return

        if not os.path.exists(output_folder):
            try:
                os.makedirs(output_folder)
            except Exception as e:
                self.status_label.config(text="Failed to create output folder")
                messagebox.showerror("Error", f"Failed to create output folder: {e}")
                return

        # Determine crop settings
        crop_mode = self.crop_mode.get()
        border = 0
        if crop_mode == "1px Border":
            border = 1
        elif crop_mode == "2px Border":
            border = 2
        elif crop_mode == "3px Border":
            border = 3
        do_crop = crop_mode != "No Crop"

        # Process each input file
        for input_path in valid_inputs:
            self.status_label.config(text=f"Processing {os.path.basename(input_path)}...")
            self.root.update()

            temp_dir = tempfile.mkdtemp()
            try:
                is_animated = self.is_animated_image(input_path)
                target_width = STICKER_SIZE if is_sticker else EMOJI_SIZE[0]
                target_height = STICKER_SIZE if is_sticker else EMOJI_SIZE[1]

                if do_crop:
                    self.status_label.config(text=f"Cropping {os.path.basename(input_path)}...")
                    self.root.update()
                    if is_animated:
                        frames, frame_durations, temp_files = self.process_animated_image_crop(input_path, temp_dir, target_width, target_height, border)
                    else:
                        frames, frame_durations, temp_files = self.process_static_image_crop(input_path, temp_dir, target_width, target_height, border)
                else:
                    self.status_label.config(text=f"Processing frames for {os.path.basename(input_path)}...")
                    self.root.update()
                    with Image.open(input_path) as im:
                        if is_animated:
                            frames = [frame.copy() for frame in ImageSequence.Iterator(im)]
                            if self.size_reduction_var.get() == "fps_50":
                                frames = frames[::2]
                            elif self.size_reduction_var.get() == "fps_25":
                                target_count = math.ceil(len(frames) * 0.75)
                                step = len(frames) / target_count if target_count > 0 else 1
                                frames = [frames[i] for i in range(len(frames)) if i % step < 1 or i >= target_count]
                        else:
                            frame_count = int(MAX_DURATION_STATIC * DEFAULT_FPS)
                            if self.size_reduction_var.get() == "fps_50":
                                frame_count = max(1, frame_count // 2)
                            elif self.size_reduction_var.get() == "fps_25":
                                frame_count = max(1, math.ceil(frame_count * 0.75))
                            frames = [im.copy() for _ in range(frame_count)]

                    processed_frames = []
                    temp_files = []
                    for i, frame in enumerate(frames):
                        resized = self.resize_frame(frame, is_sticker)
                        processed_frames.append(resized)
                        temp_file = os.path.join(temp_dir, f"frame_{i:04d}.png")
                        resized.save(temp_file, format="PNG")
                        temp_files.append(temp_file)
                    frame_durations = self.get_duration(input_path, is_animated)[1]

                total_duration = sum(frame_durations)
                frame_count = len(frames)

                base_name = os.path.splitext(os.path.basename(input_path))[0]
                suffix = "512px" if is_sticker else "100px"
                output_path = os.path.join(output_folder, f"{base_name}_{suffix}.webm")
                input_pattern = os.path.join(temp_dir, "frame_%04d.png")
                self.create_webm(input_pattern, output_path, total_duration, frame_count, is_animated, is_sticker)

            except Exception as e:
                self.status_label.config(text=f"Processing failed for {os.path.basename(input_path)}")
                messagebox.showerror("Error", f"Processing failed for {os.path.basename(input_path)}: {e}")
            finally:
                shutil.rmtree(temp_dir, ignore_errors=True)

        self.status_label.config(text="Batch conversion complete!")

if __name__ == "__main__":
    try:
        root = tk.Tk()
        app = WebMStickerEmojiApp(root)
        root.mainloop()
    except Exception:
        with open("ERROR_LOG.txt", "w") as f:
            f.write(traceback.format_exc())
        print("Error crítico al iniciar. Revisa ERROR_LOG.txt")
