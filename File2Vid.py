#https://gemini.google.com/app/b3fd8cd31577cf00
import cv2
import numpy as np
import reedsolo
import os
import math
import json
import argparse
import time
from pathlib import Path

# --- Configuration & Constants ---
class Config:
    # Resolution of the output video
    WIDTH = 1920
    HEIGHT = 1080
    FPS = 10
    
    # Visual Layout
    MARGIN_X = 80
    MARGIN_Y = 80
    PIXEL_SIZE = 16  # Size of each data "block" in pixels
    
    # Colors for Calibration (BGR Format for OpenCV)
    COLOR_TL = (0, 0, 255)      # Red
    COLOR_TR = (0, 255, 0)      # Green
    COLOR_BL = (255, 0, 0)      # Blue
    COLOR_BR = (0, 255, 255)    # Yellow
    COLOR_BORDER = (255, 255, 255)
    COLOR_BG = (0, 0, 0)

class VideoEncoder:
    def __init__(self, input_file, output_video, bit_depth=12, ecc_level=20, pixel_size=16, fps=10):
        self.input_file = input_file
        self.output_video = output_video
        self.bit_depth = bit_depth
        self.ecc_level = ecc_level
        self.pixel_size = pixel_size
        self.fps = fps
        self.rs = reedsolo.RSCodec(ecc_level)
        
        # Calculate grid dimensions
        self.cols = (Config.WIDTH - (2 * Config.MARGIN_X)) // self.pixel_size
        self.rows = (Config.HEIGHT - (2 * Config.MARGIN_Y)) // self.pixel_size
        self.blocks_per_frame = self.cols * self.rows
        
        print(f"[-] Grid Size: {self.cols}x{self.rows}")
        print(f"[-] Blocks per frame: {self.blocks_per_frame}")

    def file_to_bits(self):
        """Reads file, applies ECC, returns binary string."""
        print("[-] Reading file and applying Reed-Solomon ECC...")
        with open(self.input_file, 'rb') as f:
            data = f.read()
        
        # Apply Error Correction
        # We chunk data to respect ReedSolo limits (255 bytes max usually per block)
        # We use a larger chunk size minus ECC bytes
        chunk_size = 255 - self.ecc_level
        encoded_data = bytearray()
        
        total_chunks = math.ceil(len(data) / chunk_size)
        
        for i in range(0, len(data), chunk_size):
            chunk = data[i:i+chunk_size]
            encoded_chunk = self.rs.encode(chunk)
            encoded_data.extend(encoded_chunk)
            
        print(f"[-] Original size: {len(data)} bytes")
        print(f"[-] Encoded size: {len(encoded_data)} bytes (with ECC)")

        # Convert to bit string
        # Optimization: Use int conversion for speed
        # This converts the entire byte array to a massive integer, then to binary
        int_val = int.from_bytes(encoded_data, byteorder='big')
        bit_string = bin(int_val)[2:]
        
        # Pad to ensure full byte length alignment
        target_len = len(encoded_data) * 8
        bit_string = bit_string.zfill(target_len)
        
        return bit_string, len(data)

    def bits_to_colors(self, bit_string):
        """Converts bit string into color tuples based on bit depth."""
        print(f"[-] Converting bits to {self.bit_depth}-bit colors...")
        
        # Pad bit string to be divisible by bit_depth
        remainder = len(bit_string) % self.bit_depth
        if remainder != 0:
            bit_string += '0' * (self.bit_depth - remainder)
            
        chunks = [bit_string[i:i+self.bit_depth] for i in range(0, len(bit_string), self.bit_depth)]
        
        colors = []
        max_val = (2 ** self.bit_depth) - 1
        
        for chunk in chunks:
            val = int(chunk, 2)
            
            # Map value to RGB
            # Logic: We normalize the value to 0-1, then map to 0-255 for RGB channels
            # This is a generalized approach for any bit depth up to 24
            
            if self.bit_depth == 12:
                # 4 bits R, 4 bits G, 4 bits B
                r = ((val >> 8) & 0xF) * 17
                g = ((val >> 4) & 0xF) * 17
                b = (val & 0xF) * 17
            elif self.bit_depth == 8:
                # Grayscale / 8-bit color map
                r = g = b = val
            elif self.bit_depth == 4:
                # 1 bit R, 2 bit G, 1 bit B (approx) or just grayscale mapping
                factor = 255 // 15
                r = g = b = val * factor
            elif self.bit_depth == 16:
                 # 5 bits R, 6 bits G, 5 bits B (RGB565)
                r = ((val >> 11) & 0x1F) * 8
                g = ((val >> 5) & 0x3F) * 4
                b = (val & 0x1F) * 8
            else:
                # Fallback grayscale
                factor = 255 // max_val
                r = g = b = val * factor
                
            colors.append((b, g, r)) # OpenCV uses BGR
            
        return colors

    def create_frame(self, color_chunk, frame_idx, total_frames):
        """Generates a single video frame with data, border, and progress."""
        # Create black canvas
        frame = np.zeros((Config.HEIGHT, Config.WIDTH, 3), dtype=np.uint8)
        
        # Draw Border
        cv2.rectangle(frame, 
                      (Config.MARGIN_X - 5, Config.MARGIN_Y - 5), 
                      (Config.WIDTH - Config.MARGIN_X + 5, Config.HEIGHT - Config.MARGIN_Y + 5), 
                      Config.COLOR_BORDER, 2)

        # Create data grid using Numpy for speed (Vectorization)
        # Instead of drawing thousands of rectangles, we create a small grid and scale it up
        
        # Prepare grid array
        grid_data = np.zeros((self.rows, self.cols, 3), dtype=np.uint8)
        
        count = 0
        for r in range(self.rows):
            for c in range(self.cols):
                if count < len(color_chunk):
                    grid_data[r, c] = color_chunk[count]
                    count += 1
                else:
                    # Padding (Black)
                    grid_data[r, c] = (0, 0, 0)
        
        # Resize grid to pixel size using Nearest Neighbor (keeps edges sharp)
        data_area = cv2.resize(grid_data, 
                               (self.cols * self.pixel_size, self.rows * self.pixel_size), 
                               interpolation=cv2.INTER_NEAREST)
        
        # Paste data area into frame
        y_offset = Config.MARGIN_Y
        x_offset = Config.MARGIN_X
        frame[y_offset:y_offset+data_area.shape[0], x_offset:x_offset+data_area.shape[1]] = data_area

        # Draw Calibration Corners (Outside data area)
        # TL (Red), TR (Green), BL (Blue), BR (Yellow)
        m = 20 # Marker size
        cv2.rectangle(frame, (20, 20), (20+m, 20+m), Config.COLOR_TL, -1)
        cv2.rectangle(frame, (Config.WIDTH-40, 20), (Config.WIDTH-40+m, 20+m), Config.COLOR_TR, -1)
        cv2.rectangle(frame, (20, Config.HEIGHT-40), (20+m, Config.HEIGHT-40+m), Config.COLOR_BL, -1)
        cv2.rectangle(frame, (Config.WIDTH-40, Config.HEIGHT-40), (Config.WIDTH-40+m, Config.HEIGHT-40+m), Config.COLOR_BR, -1)

        # Progress Info
        info_text = f"Frame {frame_idx+1}/{total_frames} | {self.bit_depth}-bit | ECC {self.ecc_level}"
        cv2.putText(frame, info_text, (Config.MARGIN_X, Config.MARGIN_Y - 15), 
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)

        # Progress Bar
        bar_width = int((frame_idx + 1) / total_frames * (Config.WIDTH - 2 * Config.MARGIN_X))
        cv2.rectangle(frame, 
                      (Config.MARGIN_X, Config.HEIGHT - Config.MARGIN_Y + 10), 
                      (Config.MARGIN_X + bar_width, Config.HEIGHT - Config.MARGIN_Y + 20), 
                      (0, 255, 0), -1)

        return frame

    def create_header(self, filename, size, total_frames):
        frame = np.zeros((Config.HEIGHT, Config.WIDTH, 3), dtype=np.uint8)
        
        # Header Text
        lines = [
            "FILE TO VIDEO TRANSFER START",
            f"File: {filename}",
            f"Size: {size} bytes",
            f"Encoding: {self.bit_depth}-bit Color",
            f"ECC Level: {self.ecc_level}",
            f"Total Frames: {total_frames}",
            "",
            "PREPARE TO RECORD SCREEN",
            "Ensure corners are visible"
        ]
        
        y = Config.HEIGHT // 3
        for line in lines:
            text_size = cv2.getTextSize(line, cv2.FONT_HERSHEY_SIMPLEX, 1.5, 2)[0]
            x = (Config.WIDTH - text_size[0]) // 2
            cv2.putText(frame, line, (x, y), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (255, 255, 255), 2)
            y += 60
            
        return frame

    def run(self):
        bit_string, original_size = self.file_to_bits()
        colors = self.bits_to_colors(bit_string)
        
        total_frames = math.ceil(len(colors) / self.blocks_per_frame)
        print(f"[-] Generating {total_frames} data frames at {self.fps} FPS...")
        
        # Initialize Video Writer
        fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        out = cv2.VideoWriter(self.output_video, fourcc, self.fps, (Config.WIDTH, Config.HEIGHT))
        
        # 1. Write Header (3 seconds)
        header_frame = self.create_header(os.path.basename(self.input_file), original_size, total_frames)
        for _ in range(self.fps * 3):
            out.write(header_frame)
            
        # 2. Write Data Frames
        for i in range(total_frames):
            start = i * self.blocks_per_frame
            end = start + self.blocks_per_frame
            chunk = colors[start:end]
            
            frame = self.create_frame(chunk, i, total_frames)
            out.write(frame)
            
            if i % 10 == 0:
                print(f"    Encoded frame {i+1}/{total_frames}")

        # 3. Write Tail (1 second black)
        black_frame = np.zeros((Config.HEIGHT, Config.WIDTH, 3), dtype=np.uint8)
        for _ in range(self.fps):
            out.write(black_frame)
            
        out.release()
        
        # Save Metadata
        meta = {
            "filename": os.path.basename(self.input_file),
            "original_size": original_size,
            "bit_depth": self.bit_depth,
            "ecc_level": self.ecc_level,
            "pixel_size": self.pixel_size,
            "fps": self.fps,
            "total_frames": total_frames,
            "blocks_per_frame": self.blocks_per_frame,
            "rows": self.rows,
            "cols": self.cols
        }
        meta_path = self.output_video + ".json"
        with open(meta_path, 'w') as f:
            json.dump(meta, f, indent=4)
            
        print(f"[+] Done! Video saved to {self.output_video}")
        print(f"[+] Metadata saved to {meta_path}")


class VideoDecoder:
    def __init__(self, input_video, output_dir="restored_files", config_path=None):
        self.input_video = input_video
        self.output_dir = output_dir
        
        # Try load metadata
        if config_path:
            meta_path = config_path
        else:
            meta_path = input_video + ".json"

        if not os.path.exists(meta_path):
            raise FileNotFoundError(
                f"Metadata JSON file not found at '{meta_path}'.\n"
                "The decoder needs the .json file generated during encoding.\n"
                "If you have it in a different location, use the --config argument."
            )
            
        print(f"[-] Loading configuration from: {meta_path}")
        with open(meta_path, 'r') as f:
            self.meta = json.load(f)
            
        self.rs = reedsolo.RSCodec(self.meta['ecc_level'])
        
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)

    def color_to_val(self, color):
        """Reverses bit depth mapping. Input is BGR."""
        b, g, r = int(color[0]), int(color[1]), int(color[2])
        bit_depth = self.meta['bit_depth']
        
        if bit_depth == 12:
            # Reconstruct 12-bit int from RGB
            # Note: We divide by 17 to reverse the *17 scaling, then round
            ri = round(r / 17)
            gi = round(g / 17)
            bi = round(b / 17)
            val = (ri << 8) | (gi << 4) | bi
            return val
        elif bit_depth == 8:
            return r # Grayscale assumption or simple map
        # Add other depths as needed
        return 0
        
    def is_calibration_present(self, frame):
        """Checks for the presence of TL (Red) and TR (Green) markers."""
        if frame is None or frame.shape[0] < 50 or frame.shape[1] < 50:
            return False
            
        # Markers are drawn at (20, 20) with size 20.
        # Center is at (30, 30).
        # TL should be RED (0, 0, 255) in BGR
        tl_pixel = frame[30, 30]
        is_red = tl_pixel[2] > 200 and tl_pixel[1] < 50 and tl_pixel[0] < 50
        
        # TR should be GREEN (0, 255, 0)
        # TR X pos: Config.WIDTH - 40. Center: Config.WIDTH - 30.
        # Note: We assume 1920 width here roughly, but better to use shape
        width = frame.shape[1]
        tr_pixel = frame[30, width - 30]
        is_green = tr_pixel[1] > 200 and tr_pixel[2] < 50 and tr_pixel[0] < 50
        
        return is_red and is_green

    def extract_data_from_frame(self, frame):
        """Samples the pixel grid centers to recover data."""
        # Note: In a real screen-capture scenario, you would use 
        # cv2.findHomography here using the corner markers to un-warp the screen.
        # For this implementation, we assume the video is reasonably aligned or direct file feed.
        
        rows = self.meta['rows']
        cols = self.meta['cols']
        p_size = self.meta.get('pixel_size', Config.PIXEL_SIZE)
        
        # Offset to center of pixels
        half_p = p_size // 2
        
        extracted_vals = []
        
        for r in range(rows):
            y = Config.MARGIN_Y + (r * p_size) + half_p
            for c in range(cols):
                x = Config.MARGIN_X + (c * p_size) + half_p
                
                # Sample pixel
                if y < frame.shape[0] and x < frame.shape[1]:
                    pixel = frame[y, x]
                    val = self.color_to_val(pixel)
                    extracted_vals.append(val)
                else:
                    extracted_vals.append(0)
                    
        return extracted_vals

    def run(self):
        cap = cv2.VideoCapture(self.input_video)
        print(f"[-] Opening video: {self.input_video}")
        
        # Syncing Logic: Find the first frame with calibration markers
        fps = self.meta.get('fps', Config.FPS)
        
        # Start looking a bit before the expected start time to be safe
        expected_start = fps * 3
        # Ensure we don't go negative, and look at least 10 frames back or start at 0
        search_start = max(0, expected_start - 10)
        cap.set(cv2.CAP_PROP_POS_FRAMES, search_start)
        
        print(f"[-] Syncing: Scanning for calibration markers starting from frame {search_start}...")
        
        first_data_frame = None
        while True:
            ret, frame = cap.read()
            if not ret:
                print("[!] Error: Could not find start of data stream (calibration markers not found).")
                return
            
            if self.is_calibration_present(frame):
                first_data_frame = frame
                print(f"[-] Synced! Data stream found.")
                break
        
        all_vals = []
        # Process the first frame we already found
        vals = self.extract_data_from_frame(first_data_frame)
        all_vals.extend(vals)
        frames_processed = 1
        total_data_frames = self.meta['total_frames']
        
        print("[-] Decoding frames...")
        
        while frames_processed < total_data_frames:
            ret, frame = cap.read()
            if not ret:
                break
                
            vals = self.extract_data_from_frame(frame)
            all_vals.extend(vals)
            frames_processed += 1
            
            if frames_processed % 10 == 0:
                print(f"    Decoded frame {frames_processed}/{total_data_frames}")
                
        cap.release()
        
        # Reconstruct Bits
        print("[-] Reconstructing bit stream...")
        bit_depth = self.meta['bit_depth']
        bit_string = ""
        
        for val in all_vals:
            b_str = bin(val)[2:].zfill(bit_depth)
            bit_string += b_str
            
        # Convert Bits to Bytes
        # Trim padding based on expected byte length is hard without exact bit count
        # But we can process byte by byte
        
        byte_data = bytearray()
        for i in range(0, len(bit_string), 8):
            if i + 8 <= len(bit_string):
                byte_val = int(bit_string[i:i+8], 2)
                byte_data.append(byte_val)
                
        # Reed Solomon Decode
        print(f"[-] Applying Reed-Solomon Correction (Level {self.meta['ecc_level']})...")
        
        decoded_file_data = bytearray()
        chunk_size = 255 # Standard RS block size
        
        # We need to account for the fact that the last block might be padded
        # Try/Except block is crucial here as RS will fail on bad blocks
        
        errors_fixed = 0
        
        try:
            for i in range(0, len(byte_data), chunk_size):
                chunk = byte_data[i:i+chunk_size]
                if len(chunk) < chunk_size:
                    # If end of stream is not full chunk, it might be padding or garbage
                    continue
                    
                try:
                    decoded_chunk, decoded_part_obj, err_list = self.rs.decode(chunk)
                    decoded_file_data.extend(decoded_chunk)
                    if err_list:
                        errors_fixed += len(err_list)
                except reedsolo.ReedSolomonError:
                    print(f"    [!] Error: Frame chunk {i//chunk_size} corrupted beyond repair.")
        except Exception as e:
            print(f"    [!] processing error: {e}")

        # Truncate to original size
        original_size = self.meta['original_size']
        final_data = decoded_file_data[:original_size]
        
        output_path = os.path.join(self.output_dir, "restored_" + self.meta['filename'])
        
        with open(output_path, 'wb') as f:
            f.write(final_data)
            
        print(f"[+] Success! File restored to: {output_path}")
        print(f"    Errors fixed by ECC: {errors_fixed}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="File to Video Transfer System")
    subparsers = parser.add_subparsers(dest="mode", help="Mode: encode or decode")
    
    # Encode Arguments
    enc_parser = subparsers.add_parser("encode", help="Convert file to video")
    enc_parser.add_argument("input", help="Input file path")
    enc_parser.add_argument("output", help="Output video path (e.g. transfer.mp4)")
    enc_parser.add_argument("--ecc", type=int, default=20, help="ECC Level (0-50), Default 20")
    enc_parser.add_argument("--bits", type=int, default=12, choices=[2, 4, 8, 12, 16], help="Bit depth (color count), Default 12")
    enc_parser.add_argument("--pixel_size", type=int, default=16, help="Pixel size, Default 16")
    enc_parser.add_argument("--fps", type=int, default=10, help="Frames per second, Default 10")
    
    # Decode Arguments
    dec_parser = subparsers.add_parser("decode", help="Convert video back to file")
    dec_parser.add_argument("input", help="Input video path")
    dec_parser.add_argument("--outdir", default="output", help="Output directory")
    dec_parser.add_argument("--config", help="Path to the .json metadata file (required if not named <video>.json)")

    args = parser.parse_args()
    
    if args.mode == "encode":
        if not os.path.exists(args.input):
            print("Error: Input file not found.")
            exit()
            
        app = VideoEncoder(args.input, args.output, bit_depth=args.bits, ecc_level=args.ecc, pixel_size=args.pixel_size, fps=args.fps)
        app.run()
        
    elif args.mode == "decode":
        if not os.path.exists(args.input):
            print("Error: Input video not found.")
            exit()
        app = VideoDecoder(args.input, args.outdir, config_path=args.config)
        app.run()
        
    else:
        parser.print_help()