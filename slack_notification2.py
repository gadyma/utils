import subprocess
import logging
import json
import sys
import os
import re
import requests
import socket
from datetime import datetime
from dataclasses import dataclass, field
from typing import List, Tuple, Optional

# ================= CONFIG IMPORT =================
script_dir = os.path.dirname("$HOME/Google Drive/Shared drives/Vaults/utils/")
home_dir = os.path.expanduser('~')
for path in [script_dir, home_dir]:
    if path not in sys.path:
        sys.path.insert(0, path)

try:
    from config import (
        WEBHOOK_URL
    )
except ImportError as e:
    print(f"Error: Could not import 'config_incoming_BOI.py': {e}")
    print("Please ensure the config file exists in the script directory or home directory.")
    sys.exit(1)

def send_slack_notification(message: str, title: str = "Rclone Move", status: str = "info") -> bool:
    """Send notification to Slack if enabled."""
    if "hooks.slack.com" not in WEBHOOK_URL:
        logging.debug("Slack notifications disabled or invalid webhook URL")
        return False

    color_map = {
        "success": "#36a64f",
        "error": "#ff0000",
        "warning": "#ffcc00",
        "info": "#439fe0"
    }

    # Use plain ASCII for Slack - emojis handled by Slack's emoji syntax
    payload = {
        "attachments": [{
            "title": title,
            "text": message,
            "color": color_map.get(status, "#439fe0"),
            "footer": "Rclone Move Script",
            "ts": datetime.now().timestamp()
        }]
    }

    try:
        response = requests.post(WEBHOOK_URL, json=payload, timeout=10)
        logging.debug(f"Slack notification sent successfully: {response.status_code}")
        return True
    except Exception as e:
        logging.error(f"Failed to send Slack notification: {e}")
        return False


def run_rclone(command: str, args: List[str], is_dry_run: bool = False) -> Tuple[bool, str, str]:
    """
    Execute rclone command.
    
    Returns:
        Tuple of (success, stdout, stderr)
    """
    cmd = [RCLONE_BIN, command] + args

    cmd_str = ' '.join(f'"{c}"' if ' ' in c else c for c in cmd)
    if is_dry_run:
        logging.info(f"\n[DRY-RUN]: {cmd_str}\n")
    else:
        logging.debug(f"Executing: {cmd_str}")

    logging.info(f"Executing rclone {command}...")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=3600  # 1 hour timeout
        )

        if result.returncode != 0:
            logging.error(f"Rclone Error (exit code {result.returncode}): {result.stderr}")
            return False, result.stdout, result.stderr

        return True, result.stdout, result.stderr

    except subprocess.TimeoutExpired:
        logging.error("Rclone command timed out after 1 hour")
        return False, "", "Command timed out"
    except Exception as e:
        logging.error(f"Failed to execute rclone: {e}")
        return False, "", str(e)


def format_size(bytes_val: int) -> str:
    """Format bytes to human readable format."""
    if bytes_val == 0:
        return "0 B"
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if bytes_val < 1024.0:
            return f"{bytes_val:.2f} {unit}"
        bytes_val /= 1024.0
    return f"{bytes_val:.2f} PB"


def parse_rclone_stats_output(output: str) -> TransferStats:
    """Parse rclone stats from stdout/stderr (fallback method)."""
    stats = TransferStats()

    # Look for transfer summary in output
    # Example: "Transferred:   5 / 5, 100%"
    transferred_match = re.search(r'Transferred:\s*(\d+)\s*/\s*(\d+)', output)
    if transferred_match:
        stats.files_moved = int(transferred_match.group(1))

    # Look for bytes transferred
    # Example: "Transferred:      1.234 GiB"
    bytes_match = re.search(r'Transferred:\s*([\d.]+)\s*([KMGTP]?i?B)', output)
    if bytes_match:
        size = float(bytes_match.group(1))
        unit = bytes_match.group(2).upper()
        multipliers = {'B': 1, 'KB': 1024, 'KIB': 1024, 'MB': 1024**2, 'MIB': 1024**2,
                       'GB': 1024**3, 'GIB': 1024**3, 'TB': 1024**4, 'TIB': 1024**4}
        stats.bytes_moved = int(size * multipliers.get(unit, 1))

    # Look for errors
    errors_match = re.search(r'Errors:\s*(\d+)', output)
    if errors_match:
        stats.errors = int(errors_match.group(1))

    return stats


def parse_moved_files(output: str) -> list:
    """Extract list of moved files from verbose rclone output."""
    files = []
    
    # Pattern for verbose output: "INFO  : name: Copied (new)"
    # or "INFO  : name: Moved"
    patterns = [
        r'INFO\s*:\s+([^\s:][^:]*?):\s+(?:Copied|Moved)',
        r'NOTICE\s*:\s+([^\s:][^:]*?):\s+(?:Copied|Moved)',
    ]
    
    for line in output.split('\n'):
        for pattern in patterns:
            match = re.search(pattern, line)
            if match:
                filename = match.group(1).strip()
                if filename and len(filename) > 0:
                    files.append(filename)
                    if len(files) >= 20:  # Limit to 20 files for display
                        return files
    
    return files


def move_all_files(dry_run: bool = False) -> bool:
    """Move all files from SRC to DST in one operation."""
    logging.info("=" * 60)
    logging.info(f"Starting bulk move from {SRC_ROOT} to {DST_ROOT}")
    logging.info(f"Mode: {'DRY-RUN' if dry_run else 'LIVE'}")
    logging.info("=" * 60)

    # Prepare flags
    # Note: Don't redirect all output to log file - we need errors on stderr
    flags = [
        "-vv",  # Extra verbose to see all files being moved
        "--stats", "1s",
        "--stats-one-line",
    ]
    if dry_run:
        flags.append("--dry-run")

    logging.info(f"Moving files from {SRC_ROOT} to {DST_ROOT}")

    success, stdout, stderr = run_rclone("move", [SRC_ROOT, DST_ROOT] + flags, is_dry_run=dry_run)

    # Combine output for logging
    combined_output = f"STDOUT:\n{stdout}\nSTDERR:\n{stderr}" if stdout or stderr else "(no output)"

    # DEBUG: Log raw output to help troubleshoot parsing issues
    logging.info(f"Raw rclone STDERR length: {len(stderr)}, STDOUT length: {len(stdout)}")

    # Log first 2000 chars of output for debugging
    if stderr:
        logging.info(f"STDERR sample (first 2000 chars):\n{stderr[:2000]}")
    if stdout:
        logging.info(f"STDOUT sample (first 2000 chars):\n{stdout[:2000]}")

    logging.debug(f"Rclone full output:\n{combined_output}")

    # Parse stats from stdout/stderr
    stats = parse_rclone_stats_output(stdout + "\n" + stderr)

    # Extract list of moved files from verbose output
    moved_files = parse_moved_files(stdout + "\n" + stderr)

    # DEBUG: Log what was parsed
    logging.info(f"Parsed stats: files_moved={stats.files_moved}, bytes={stats.bytes_moved}, errors={stats.errors}")
    logging.info(f"Parsed moved_files count: {len(moved_files)}")

    # If rclone verbose output contains moved file entries but the summary stats show 0 files,
    # prefer the verbose list as it more accurately reflects what was moved.
    if stats.files_moved == 0 and moved_files:
        logging.debug("Stats reported 0 files moved but verbose output contains moved file entries; adjusting count from verbose list")
        stats.files_moved = len(moved_files)

    logging.info(f"Transfer stats: {stats.files_moved} files, {format_size(stats.bytes_moved)}, {stats.errors} errors")
    if moved_files:
        logging.info(f"Moved files: {moved_files[:5]}")  # Log first 5 files

    # Build result message
    msg_lines = [
        f"*Source:* `{SRC_ROOT}`",
        f"*Destination:* `{DST_ROOT}`",
        ""
    ]

    if not success:
        # Combine all available error info
        error_info = ""
        if stderr:
            error_info = stderr
        elif stdout:
            error_info = stdout
        else:
            error_info = "No error details available - check rclone configuration"
        
        msg_lines.append(":x: *Move operation failed!*")
        # Truncate error message for Slack
        error_preview = error_info[:500] + "..." if len(error_info) > 500 else error_info
        msg_lines.append(f"```{error_preview}```")

        err_msg = "\n".join(msg_lines)
        logging.error(f"Move operation failed: {error_info}")
        send_slack_notification(err_msg, title=":x: Move Failed", status="error")

        return False

    # Success path
    if dry_run:
        msg_lines.append(":eyes: *DRY RUN - No files actually moved*")
        if stats.files_moved > 0:
            msg_lines.append(f"Would move: {stats.files_moved} files")
    elif stats.files_moved > 0:
        msg_lines.append(":chart_with_upwards_trend: *Transfer Results:*")
        msg_lines.append(f"• Files moved: {stats.files_moved}")
        if stats.bytes_moved > 0:
            msg_lines.append(f"• Total size: {format_size(stats.bytes_moved)}")
        if stats.errors > 0:
            msg_lines.append(f"• Errors: {stats.errors}")

        # Add moved files list
        if moved_files:
            msg_lines.append("")
            msg_lines.append(":page_facing_up: *Files moved:*")
            for filename in moved_files:
                # Truncate long paths
                if len(filename) > 80:
                    filename = "..." + filename[-77:]
                msg_lines.append(f"• {filename}")
            if len(moved_files) >= 20:
                # Use stats.files_moved (adjusted if needed) to report the remaining count
                msg_lines.append(f"• ... and {stats.files_moved - len(moved_files)} more files")

    else:
        msg_lines.append(":white_check_mark: No files to move (source empty or already synced)")

    msg_lines.append("")
    msg_lines.append(":white_check_mark: *Complete*")

    # If there are no files to move (and this is not a dry-run), skip sending Slack notification
    logging.info(f"Notification check: files_moved={stats.files_moved}, moved_files_count={len(moved_files)}, dry_run={dry_run}")
    if stats.files_moved == 0 and len(moved_files) == 0 and not dry_run:
        logging.info("No files to move; skipping Slack notification")
        return True

    final_msg = "\n".join(msg_lines)
    logging.info("Move completed successfully - sending Slack notification")
    send_slack_notification(final_msg, title=":white_check_mark: Move Complete", status="success")
    return True
# --- Example Usage ---
if __name__ == "__main__":
    try:
        # A successful notification
        send_slack_notification("Hello222 from Python! 🐍 Your script has finished running successfully.")

        # Simulating an error notification
        # In a real app, you would call this in a try...except block
        # raise ValueError("Something went wrong!")
    except Exception as e:
        send_slack_notification(f"🚨 An error occurred: {e}")