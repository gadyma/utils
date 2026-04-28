import os
import sys
from pathlib import Path
from datetime import datetime

def find_latest_folder(foldername):
    if not os.path.exists(foldername):
        print(f"Directory does not exist: {foldername}")
        return None
    
    if not os.path.isdir(foldername):
        print(f"Path is not a directory: {foldername}")
        return None
    folders = []   
    try:
        # Get all items in the directory
        for item in os.listdir(foldername):
            item_path = os.path.join(foldername, item)
            # Only consider directories
            if os.path.isdir(item_path):
                # Get modification time
                mod_time = os.path.getmtime(item_path)
                folders.append((item_path, mod_time, item))        
        if not folders:
            print(f"No folders found in: {foldername}")
            return None
        
        # Sort by modification time (newest first)
        folders.sort(key=lambda x: x[1], reverse=True)
        latest_folder = folders[0]
        
        return latest_folder[0]
        
    except PermissionError:
        print(f"Permission denied accessing: {foldername}")
        return None
    except Exception as e:
        print(f"Error accessing directory {foldername}: {e}")
        return None

def main():
    """
    Main function to demonstrate usage of find_latest_folder function
    """
    print("=== Finding Latest Folder ===")
    
    # Use the specific folder path
    folder_path = "/Users/gadymargalit/Library/CloudStorage/GoogleDrive-gady@esh.com/Shared drives/גיבויים Smartsuite/SmartSuite backup/"
    latest_folder = find_latest_folder(folder_path)
    if latest_folder:
        print(f"\n✅ Latest folder found: {latest_folder}")
    else:
        print("\n❌ No latest folder found")

if __name__ == "__main__":
    main()
