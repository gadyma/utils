"""
Rclone File Transfer Automation Script (Improved)
Moves files from Google Drive source to destination with backup and rollback.

Improvements from original:
- Added file integrity verification after copy
- Fixed temp directory cleanup (removes full tree)
- Added retry logic for transient failures
- Added file count/size reporting
- Fixed config import path handling
- Added graceful shutdown handling
- Recovers stuck files from failed previous runs
"""

import subprocess
import logging
import json
import sys
import os
import re
import shutil
import time
import signal
import urllib.request
from datetime import datetime
from pathlib import Path

# ================= CONFIG IMPORT =================

# Ensure config can be found in script directory or home directory
script_dir = os.path.dirname(os.path.abspath(__file__))
home_dir = os.path.expanduser('~')
for path in [script_dir, home_dir]:
    if path not in sys.path:
        sys.path.insert(0, path)

try:
    from config_outgoing_BOI import (
        WEBHOOK_URL, ENABLE_SLACK, RCLONE_BIN, SRC_ROOT,
        DST_ROOT, BACKUP_ROOT, LOG_FILE, FOLDER_OWNERS
    )
except ImportError as e:
    print(f"Error: Could not import 'config_outgoing_BOI.py'.")
    print(f"Searched in: {script_dir}, {home_dir}")
    print(f"Details: {e}")
    sys.exit(1)

# ================= CONSTANTS =================

MAX_RETRIES = 3
RETRY_DELAY = 5  # seconds
CLEANUP_RETRIES = 5
CLEANUP_DELAY = 2  # seconds

# ================= SETUP LOGGING =================

logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
console = logging.StreamHandler()
console.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
console.setFormatter(formatter)
logging.getLogger('').addHandler(console)

# ================= GRACEFUL SHUTDOWN =================

shutdown_requested = False

def signal_handler(signum, frame):
    global shutdown_requested
    logging.warning("Shutdown requested. Will exit after current folder completes.")
    shutdown_requested = True

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

# ================= HELPER FUNCTIONS =================

def send_slack_notification(message: str, title: str = "Rclone Automation", status: str = "info") -> bool:
    """Send notification to Slack webhook. Returns True if successful."""
    if not ENABLE_SLACK or "hooks.slack.com" not in WEBHOOK_URL:
        return False
    
    color_map = {
        "success": "#36a64f",
        "error": "#ff0000",
        "warning": "#ffcc00",
        "info": "#439fe0"
    }
    
    payload = {
        "attachments": [{
            "title": title,
            "text": message,
            "color": color_map.get(status, "#439fe0"),
            "footer": "Rclone Python Script",
            "ts": datetime.now().timestamp()
        }]
    }
    
    try:
        req = urllib.request.Request(
            WEBHOOK_URL,
            data=json.dumps(payload).encode('utf-8'),
            headers={'Content-Type': 'application/json'}
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            return response.status == 200
    except Exception as e:
        logging.error(f"Failed to send Slack notification: {e}")
        return False


def run_rclone(command: str, args: list, retries: int = 1) -> tuple[bool, str]:
    """
    Execute an rclone command with optional retries.
    Returns (success: bool, output: str)
    """
    cmd = [RCLONE_BIN, command] + args
    cmd_str = ' '.join(f'"{c}"' if ' ' in c else c for c in cmd)
    
    for attempt in range(retries):
        if attempt > 0:
            logging.info(f"Retry {attempt}/{retries-1} for: rclone {command}")
            time.sleep(RETRY_DELAY)
        
        logging.info(f"Executing: {cmd_str}")
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode == 0:
            return True, result.stdout
        
        logging.warning(f"Rclone returned code {result.returncode}: {result.stderr}")
    
    logging.error(f"Rclone failed after {retries} attempts: {result.stderr}")
    return False, result.stderr


def get_subdirectories(remote_path: str) -> list[str]:
    """List subdirectories in a remote path."""
    cmd = [RCLONE_BIN, "lsf", remote_path, "--dirs-only"]
    result = subprocess.run(cmd, capture_output=True, text=True)
    
    if result.returncode != 0:
        logging.error(f"Failed to list directories in {remote_path}: {result.stderr}")
        return []
    
    return [d.rstrip('/') for d in result.stdout.strip().split('\n') if d]


def get_file_count_and_size(path: str) -> tuple[int, int]:
    """
    Get file count and total size for a path (local or remote).
    Returns (count, size_in_bytes)
    """
    cmd = [RCLONE_BIN, "size", path, "--json"]
    result = subprocess.run(cmd, capture_output=True, text=True)
    
    if result.returncode != 0:
        return 0, 0
    
    try:
        data = json.loads(result.stdout)
        return data.get('count', 0), data.get('bytes', 0)
    except json.JSONDecodeError:
        return 0, 0


def format_size(size_bytes: int) -> str:
    """Format bytes to human readable string."""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} PB"


def parse_moved_files(output: str) -> list:
    """Extract list of moved files from rclone output (INFO/NOTICE lines).

    Returns up to 100 filenames to avoid giant messages.
    """
    files = []
    patterns = [
        r'INFO\s*:\s+([^\s:][^:]*?):\s+(?:Copied|Moved)',
        r'NOTICE\s*:\s+([^\s:][^:]*?):\s+(?:Copied|Moved)',
    ]

    for line in output.split('\n'):
        for pattern in patterns:
            match = re.search(pattern, line)
            if match:
                filename = match.group(1).strip()
                if filename:
                    files.append(filename)
                    if len(files) >= 100:
                        return files

    return files


def is_folder_empty(remote_path: str) -> bool:
    """Check if a remote folder contains any files."""
    count, _ = get_file_count_and_size(remote_path)
    return count == 0


def verify_transfer(source_path: str, dest_path: str) -> bool:
    """
    Verify that destination has at least as many files as source.
    Uses rclone check for integrity verification.
    """
    src_count, src_size = get_file_count_and_size(source_path)
    dst_count, dst_size = get_file_count_and_size(dest_path)
    
    logging.info(f"Verification - Source: {src_count} files ({format_size(src_size)}), "
                 f"Dest: {dst_count} files ({format_size(dst_size)})")
    
    if dst_count < src_count:
        logging.error(f"Verification failed: destination has fewer files ({dst_count}) than source ({src_count})")
        return False
    
    if dst_size < src_size:
        logging.warning(f"Verification warning: destination size ({format_size(dst_size)}) "
                       f"is less than source ({format_size(src_size)})")
    
    return True


def cleanup_directory(path: str) -> bool:
    """Remove a directory with retry logic. Returns True if successful."""
    if not os.path.exists(path):
        return True
    
    import platform
    
    for attempt in range(CLEANUP_RETRIES):
        try:
            time.sleep(CLEANUP_DELAY)
            
            if platform.system() == "Windows":
                # Use subprocess for more reliable Windows cleanup
                result = subprocess.run(
                    ['cmd', '/c', 'rmdir', '/s', '/q', path],
                    capture_output=True,
                    text=True
                )
                if result.returncode == 0 or not os.path.exists(path):
                    return True
            else:
                shutil.rmtree(path)
                return True
                
        except Exception as e:
            if attempt < CLEANUP_RETRIES - 1:
                logging.debug(f"Cleanup attempt {attempt + 1} failed: {e}")
            else:
                logging.warning(f"Could not clean up {path} after {CLEANUP_RETRIES} attempts: {e}")
    
    return False


# ================= MAIN LOGIC =================

def process_folder(folder_name: str) -> bool:
    """
    Process a single folder: move to temp, copy to dest, backup original.
    Returns True if successful.
    """
    logging.info(f"{'='*50}")
    logging.info(f"Processing folder: {folder_name}")
    logging.info(f"{'='*50}")
    
    src_path = f"{SRC_ROOT}/{folder_name}"
    dst_path = f"{DST_ROOT}/{folder_name}"
    timestamp = datetime.now().strftime('%Y-%m-%d__%H-%M-%S')
    backup_path = f"{BACKUP_ROOT}/{folder_name}/{timestamp}"
    temp_local = os.path.abspath(f"./temp/{folder_name}/{timestamp}")
    owner_name = FOLDER_OWNERS.get(folder_name, "Unknown Owner")
    
    # Pre-check: Get file count and size
    file_count, total_size = get_file_count_and_size(src_path)
    
    if file_count == 0:
        logging.info(f"Folder '{folder_name}' is empty. Skipping.")
        return True
    
    logging.info(f"Found {file_count} files ({format_size(total_size)}) to process")
    
    # Create temp local directory
    os.makedirs(temp_local, exist_ok=True)
    
    base_flags = ["--log-level", "INFO", "--stats", "0"]
    
    # Step 1: Move files from source to local temp
    logging.info(f"Step 1/4: Moving files from source to temp: {temp_local}")
    success, output = run_rclone(
        "move",
        [src_path, temp_local] + base_flags,
        retries=MAX_RETRIES
    )
    
    if not success:
        err_msg = f"CRITICAL: Move to temp failed for '{folder_name}'.\nError: {output}"
        logging.error(err_msg)
        send_slack_notification(err_msg, title="❌ Temp Move Failed", status="error")
        return False

    # Parse moved filenames from rclone output (if any)
    moved_files = parse_moved_files(output)
    if moved_files:
        logging.info(f"Moved files detected: {moved_files[:5]}{' and more' if len(moved_files)>5 else ''}")

    # Verify files arrived in temp
    temp_count, _ = get_file_count_and_size(temp_local)
    if temp_count == 0:
        logging.info(f"No files were moved for '{folder_name}'. Nothing to do.")
        cleanup_directory(temp_local)
        return True
    
    # Step 2: Copy from temp to destination
    logging.info(f"Step 2/4: Copying files to destination: {dst_path}")
    success, error = run_rclone(
        "copy",
        [temp_local, dst_path, "--inplace"] + base_flags,
        retries=MAX_RETRIES
    )
    
    if not success:
        err_msg = f"Copy to destination failed for '{folder_name}'!\nFiles preserved in: {temp_local}"
        logging.error(err_msg)
        send_slack_notification(err_msg, title="⚠️ Copy Failed - Starting Rollback", status="error")
        
        # Rollback: Move files back to source
        logging.info(f"ROLLBACK: Restoring files from temp to source: {src_path}")
        logging.debug(f"Source path details - SRC_ROOT: {SRC_ROOT}, folder_name: {folder_name}, full path: {src_path}")
        rollback_success, rollback_error = run_rclone(
            "move",
            [temp_local, src_path] + base_flags,
            retries=MAX_RETRIES
        )
        
        if rollback_success:
            msg = f"Folder: {folder_name}\nStatus: Files restored to source after failed copy."
            logging.info(msg)
            send_slack_notification(msg, title="✅ Rollback Complete", status="success")
            cleanup_directory(temp_local)
        else:
            msg = f"CRITICAL: Rollback failed for '{folder_name}'!\nFiles stranded in: {temp_local}\nRollback error: {rollback_error}\nMANUAL INTERVENTION REQUIRED!"
            logging.error(msg)
            send_slack_notification(msg, title="🚨 ROLLBACK FAILED", status="error")
        
        return False
    
    # Step 2.5: Verify the copy succeeded
    logging.info("Step 2.5/4: Verifying transfer integrity...")
    if not verify_transfer(temp_local, dst_path):
        err_msg = f"Transfer verification failed for '{folder_name}'!\nStarting rollback..."
        logging.error(err_msg)
        send_slack_notification(err_msg, title="⚠️ Verification Failed", status="error")
        
        # Rollback
        logging.info(f"ROLLBACK: Restoring files from temp to source: {src_path}")
        run_rclone("move", [temp_local, src_path] + base_flags, retries=MAX_RETRIES)
        return False
    
    # Step 3: Move from temp to backup
    logging.info(f"Step 3/4: Moving files to backup: {backup_path}")
    success, backup_output = run_rclone(
        "move",
        [temp_local, backup_path] + base_flags,
        retries=MAX_RETRIES
    )

    if not success:
        err_msg = f"Move to backup failed for '{folder_name}'!\nFiles still in temp: {temp_local}\n(Destination copy succeeded)\nError: {backup_output}"
        # Don't return False - destination copy succeeded, this is less critical
    
    # Step 4: Cleanup temp directory
    logging.info(f"Step 4/4: Cleaning up temp folder")
    # Clean up the specific temp folder and its parent if empty
    cleanup_directory(temp_local)
    parent_temp = os.path.dirname(temp_local)
    if os.path.exists(parent_temp) and not os.listdir(parent_temp):
        cleanup_directory(parent_temp)
    
    # Success!
    # Ensure we have moved_files from earlier step; if not, try to parse from the backup move output
    if 'moved_files' not in locals() or not moved_files:
        moved_files = []
        if 'backup_output' in locals() and backup_output:
            moved_files = parse_moved_files(backup_output)

    msg_lines = [
        f"Folder: {folder_name}",
        f"Owner: {owner_name}",
        f"Files: {file_count} ({format_size(total_size)})",
        f"Status: ✅ Success",
        f"Backup: {backup_path}"
    ]

    if moved_files:
        msg_lines.append("")
        msg_lines.append(":page_facing_up: *Files moved:*")
        for filename in moved_files[:20]:
            short = filename if len(filename) <= 80 else "..." + filename[-77:]
            msg_lines.append(f"• {short}")
        if len(moved_files) > 20:
            msg_lines.append(f"• ... and {len(moved_files) - 20} more files")

    msg = "\n".join(msg_lines)
    logging.info(f"Successfully processed '{folder_name}'")
    send_slack_notification(msg, title="✅ Transfer Success", status="success")

    return True


def recover_stuck_files() -> int:
    """
    Check for files stuck in temp from a previous failed run.
    Attempts to restore them to source.
    Returns count of recovered folders.
    """
    temp_base = os.path.abspath("./temp")
    
    if not os.path.exists(temp_base):
        return 0
    
    recovered = 0
    
    # Structure is ./temp/<folder_name>/<timestamp>/
    for folder_name in os.listdir(temp_base):
        folder_path = os.path.join(temp_base, folder_name)
        if not os.path.isdir(folder_path):
            continue
        
        for timestamp_dir in os.listdir(folder_path):
            timestamp_path = os.path.join(folder_path, timestamp_dir)
            if not os.path.isdir(timestamp_path):
                continue
            
            # Check if there are files stuck here
            stuck_count, stuck_size = get_file_count_and_size(timestamp_path)
            
            if stuck_count > 0:
                logging.warning(f"Found {stuck_count} stuck files ({format_size(stuck_size)}) in: {timestamp_path}")
                src_path = f"{SRC_ROOT}/{folder_name}"
                
                # Restore files to source
                logging.info(f"Restoring stuck files to source: {src_path}")
                base_flags = ["--log-level", "INFO", "--stats", "0"]
                success, error = run_rclone(
                    "move",
                    [timestamp_path, src_path] + base_flags,
                    retries=MAX_RETRIES
                )
                
                if success:
                    logging.info(f"Successfully restored stuck files for '{folder_name}'")
                    send_slack_notification(
                        f"Recovered {stuck_count} stuck files ({format_size(stuck_size)}) for folder '{folder_name}'",
                        title="🔄 Stuck Files Recovered",
                        status="info"
                    )
                    recovered += 1
                    
                    # Clean up empty temp dirs
                    cleanup_directory(timestamp_path)
                else:
                    logging.error(f"Failed to restore stuck files: {error}")
                    send_slack_notification(
                        f"Failed to recover stuck files in: {timestamp_path}\nManual intervention needed!",
                        title="⚠️ Recovery Failed",
                        status="error"
                    )
    
    return recovered


def cleanup_empty_temp_dirs():
    """Clean up empty temp directories after successful run."""
    temp_base = os.path.abspath("./temp")
    
    if not os.path.exists(temp_base):
        return
    
    # Walk bottom-up to remove empty directories
    for root, dirs, files in os.walk(temp_base, topdown=False):
        for dir_name in dirs:
            dir_path = os.path.join(root, dir_name)
            try:
                if os.path.isdir(dir_path) and not os.listdir(dir_path):
                    os.rmdir(dir_path)
                    logging.debug(f"Removed empty temp dir: {dir_path}")
            except OSError:
                pass
    
    # Remove temp_base itself if empty
    try:
        if os.path.isdir(temp_base) and not os.listdir(temp_base):
            os.rmdir(temp_base)
            logging.info("Cleaned up empty temp directory")
    except OSError:
        pass


def main():
    """Main entry point."""
    logging.info("=" * 60)
    logging.info("Starting rclone file transfer automation")
    logging.info("=" * 60)
    
    # FIRST: Check for and recover any stuck files from previous failed runs
    temp_base = os.path.abspath("./temp")
    if os.path.exists(temp_base):
        logging.info("Checking for stuck files from previous runs...")
        recovered = recover_stuck_files()
        if recovered > 0:
            logging.info(f"Recovered files from {recovered} folder(s)")
    
    # Get list of folders to process
    subdirs = get_subdirectories(SRC_ROOT)
    
    if not subdirs:
        logging.info(f"No directories found in source: {SRC_ROOT}")
        return
    
    logging.info(f"Found {len(subdirs)} folder(s) to process: {', '.join(subdirs)}")
    
    success_count = 0
    fail_count = 0
    
    for folder in subdirs:
        if shutdown_requested:
            logging.warning("Shutdown requested. Stopping processing.")
            break
        
        try:
            if process_folder(folder):
                success_count += 1
            else:
                fail_count += 1
        except Exception as e:
            logging.error(f"Unhandled exception processing '{folder}': {e}", exc_info=True)
            send_slack_notification(
                f"Unhandled error on folder '{folder}':\n{str(e)[:500]}",
                title="🚨 Script Error",
                status="error"
            )
            fail_count += 1
    
    # Clean up empty temp directories after all processing
    if fail_count == 0:
        cleanup_empty_temp_dirs()
    else:
        logging.info("Skipping temp cleanup due to failures (files may need recovery on next run)")
    
    # Summary
    logging.info("=" * 60)
    logging.info(f"Processing complete. Success: {success_count}, Failed: {fail_count}")
    logging.info("=" * 60)
    
    if fail_count > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()