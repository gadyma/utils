import re
from datetime import timedelta

def shift_srt_time(input_file, output_file, shift_seconds):
    # Regex to find the SRT time format: 00:00:55,087
    time_pattern = re.compile(r'(\d{2}:\d{2}:\d{2},\d{3})')

    def adjust_match(match):
        time_str = match.group(1)
        # Parse time string into components
        h, m, s_ms = time_str.split(':')
        s, ms = s_ms.split(',')
        
        # Create timedelta and add the shift
        t = timedelta(hours=int(h), minutes=int(m), seconds=int(s), milliseconds=int(ms))
        new_time = t + timedelta(seconds=shift_seconds)
        
        # Format back to SRT standard
        total_seconds = int(new_time.total_seconds())
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        seconds = total_seconds % 60
        milliseconds = int(new_time.microseconds / 1000)
        
        return f"{hours:02}:{minutes:02}:{seconds:02},{milliseconds:03}"

    with open(input_file, 'r', encoding='utf-8') as f:
        content = f.read()

    # Apply the shift to every timestamp found
    new_content = time_pattern.sub(adjust_match, content)

    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(new_content)
    
    print(f"Done! Subtitles shifted by {shift_seconds}s and saved to {output_file}")

# Usage
shift_srt_time('Donald In Mathmagic Land.heb.srt', 'Donald_Synced.srt', 1)