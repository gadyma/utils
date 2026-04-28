# pip3 install "ivrit[all]"
import ivrit
import os
import sys
import time
import subprocess
from datetime import datetime

def get_media_duration(file_path):
    """Uses ffprobe to get the media duration in seconds."""
    try:
        # ffprobe comes with ffmpeg, which is required for faster-whisper
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "format=duration", "-of",
             "default=noprint_wrappers=1:nokey=1", file_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True
        )
        return float(result.stdout.strip())
    except Exception as e:
        print(f"(Warning: Could not get media length. Is ffprobe installed? Error: {e})")
        return 0.0

def main():
    # 1. Check if the user provided a file path as an argument
    if len(sys.argv) < 2:
        print("Usage: python3 transcribe_tool.py <path_to_audio_file>")
        return

    # 2. Get the path from the command line argument
    raw_path = sys.argv[1]
    file_path = os.path.expanduser(raw_path)

    if not os.path.exists(file_path):
        print(f"Error: File not found at {file_path}")
        return

    # Calculate file size in Megabytes (MB)
    file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
    
    # Get media duration
    media_duration_sec = get_media_duration(file_path)
    media_duration_min = media_duration_sec / 60

    # 3. Create the destination path (same name as audio file but .txt)
    base_name, _ = os.path.splitext(file_path)
    dest_path = f"{base_name}.txt"

    print("-" * 30)
    print(f"File to process: {os.path.basename(file_path)}")
    print(f"File size: {file_size_mb:.2f} MB")
    if media_duration_sec > 0:
        print(f"Media length: {media_duration_min:.2f} minutes ({media_duration_sec:.2f} seconds)")
    print("-" * 30)
    
    # --- START TIMING ---
    start_time = time.time()
    start_dt = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"Started processing at: {start_dt}")
    print("Loading model and transcribing (this may take a while)...")

    # 4. Initialize the Ivrit AI model
    model = ivrit.load_model(
        engine="faster-whisper", 
        model="ivrit-ai/whisper-large-v3-ct2", 
        device="cpu",      
        compute_type="int8" 
    )

    # 5. Transcribe
    result = model.transcribe(path=file_path)

    # 6. Save the output
    with open(dest_path, "w", encoding="utf-8") as f:
        f.write(result["text"])

    # --- END TIMING ---
    end_time = time.time()
    end_dt = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # Calculate duration
    processing_duration_sec = end_time - start_time
    processing_duration_min = processing_duration_sec / 60

    print("-" * 30)
    print(f"Success! The transcription is ready in: {dest_path}")
    print(f"Finished at: {end_dt}")
    print(f"Total processing time: {processing_duration_min:.2f} minutes ({processing_duration_sec:.2f} seconds)")
    
    # Provide a processing speed ratio if we have the media length
    if media_duration_sec > 0:
        speed_ratio = media_duration_sec / processing_duration_sec
        print(f"Processing speed: {speed_ratio:.2f}x (processed {speed_ratio:.2f} seconds of audio per 1 second of compute)")
    print("-" * 30)

if __name__ == "__main__":
    main()