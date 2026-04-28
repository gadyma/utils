import subprocess

def run_rclone_command(subcommand: str, source: str = None, destination: str = None, extra_args: list = None):
    """
    Executes an rclone command with the given parameters using subprocess.

    Args:
        subcommand (str): The rclone command to execute (e.g., 'copy', 'lsd', 'sync').
        source (str, optional): The source path. Defaults to None.
        destination (str, optional): The destination path. Defaults to None.
        extra_args (list, optional): A list of additional string arguments/flags. Defaults to None.

    Returns:
        str: The standard output of the command if successful, otherwise None.
    """
    # Start building the command list
    command = ["rclone", subcommand]

    # Add any extra arguments/flags (e.g., ['-v', '--progress'])
    if extra_args:
        command.extend(extra_args)

    # Add source and destination if they are provided
    if source:
        command.append(source)
    if destination:
        command.append(destination)
    
    print(f"▶️  Executing command: {' '.join(command)}")

    try:
        # Execute the command
        result = subprocess.run(
            command, 
            capture_output=True, 
            text=True, 
            check=True  # This will raise CalledProcessError on non-zero exit codes
        )
        print("✅ Command executed successfully!")
        return result.stdout

    except subprocess.CalledProcessError as e:
        # Handle errors from the rclone command itself
        print(f"❌ An error occurred while executing rclone.")
        print(f"Return code: {e.returncode}")
        print(f"Standard Output:\n{e.stdout}")
        print(f"Standard Error:\n{e.stderr}")
        return None

    except FileNotFoundError:
        # Handle the case where 'rclone' isn't installed or in the PATH
        print("❌ Error: 'rclone' command not found.")
        print("Please ensure rclone is installed and in your system's PATH.")
        return None

# --- EXAMPLES ---
if __name__ == "__main__":
    # Remember to replace 'your_remote:' with your actual remote name.
    
    print("\n--- Example 1: Listing directories in a remote ---")
    output = run_rclone_command(subcommand="lsd", source="GoogleDrive:")
    if output:
        print("Directories found:\n", output)

    print("\n--- Example 2: Copying a local file with flags ---")
    # This example assumes you have a

