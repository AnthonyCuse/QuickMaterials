import os
import shutil
from datetime import datetime

# Dynamically determine the source folder based on the script's location
source_folder = os.path.dirname(os.path.abspath(__file__))  # Directory where this script is located

# Extract project name from source folder for reuse
project_name = os.path.basename(source_folder)

# Dynamically define the destination folder within the Maya scripts folder
destination_folder = os.path.join(r'C:\Users\adsyr\Documents\maya\scripts', project_name)

# Backup folder path now based on source directory
backup_folder = os.path.join(source_folder, 'backup')  # Backup folder path in the source directory


def create_backup_folder():
    print(f"Preparing to create backup folder in: {backup_folder}")

    # Create the main backup folder if it doesn't exist
    if not os.path.exists(backup_folder):
        print(f"Backup folder does not exist. Creating: {backup_folder}")
        os.makedirs(backup_folder)
    else:
        print(f"Backup folder already exists: {backup_folder}")

    # Create a subfolder with the current date (Year_Month_Day)
    date_folder = datetime.now().strftime('%Y_%m_%d')
    dated_backup_folder = os.path.join(backup_folder, date_folder)
    if not os.path.exists(dated_backup_folder):
        print(f"Dated backup folder does not exist. Creating: {dated_backup_folder}")
        os.makedirs(dated_backup_folder)
    else:
        print(f"Dated backup folder already exists: {dated_backup_folder}")

    # Determine the version number for today's backup
    version_number = 1
    while True:
        versioned_backup_folder = os.path.join(dated_backup_folder, f'version_{version_number:02}')
        if not os.path.exists(versioned_backup_folder):
            print(f"Creating versioned backup folder: {versioned_backup_folder}")
            os.makedirs(versioned_backup_folder)
            break
        print(f"Version folder already exists: {versioned_backup_folder}")
        version_number += 1

    print(f"Final versioned backup folder created: {versioned_backup_folder}")
    return versioned_backup_folder


def backup_files(files_to_backup, versioned_backup_folder):
    print(f"Backing up files to: {versioned_backup_folder}")
    # Copy each file to the versioned backup folder
    for file in files_to_backup:
        source_file = file['source']
        relative_path = os.path.relpath(source_file, source_folder)
        backup_path = os.path.join(versioned_backup_folder, relative_path)

        # Ensure the backup path directory exists
        os.makedirs(os.path.dirname(backup_path), exist_ok=True)

        shutil.copy2(source_file, backup_path)
        print(f'Backed up {source_file} to {backup_path}')


def copy_icons_filtered(source_icons_folder, destination_icons_folder):
    """
    Copy only common image icon files (jpg, jpeg, png) from the icons folder
    and its subdirectories to the destination folder, preserving the folder structure.
    """
    print(f"Copying icons from {source_icons_folder} to {destination_icons_folder}")

    for root, dirs, files in os.walk(source_icons_folder):
        relative_path = os.path.relpath(root, source_icons_folder)
        dest_dir = os.path.join(destination_icons_folder, relative_path)
        os.makedirs(dest_dir, exist_ok=True)

        for file in files:
            # Only copy image files with common icon formats
            if file.lower().endswith(('.png', '.jpg', '.jpeg')):
                source_file = os.path.join(root, file)
                dest_file = os.path.join(dest_dir, file)
                shutil.copy2(source_file, dest_file)
                print(f'Copied {source_file} to {dest_file}')
            else:
                print(f"Skipping {file} (not a valid icon format)")


def sync_files(source, destination):
    print(f"Syncing files from {source} to {destination}")

    # Get the name of this script file to exclude it
    this_script = os.path.basename(__file__)
    files_to_backup = []

    # Iterate over all files in the source folder (excluding 'venv' and 'backup')
    for root, _, files in os.walk(source):
        # Skip 'venv' and 'backup' folders
        if 'venv' in root or 'backup' in root:
            continue

        for file in files:
            if file == this_script:
                print(f"Skipping {file} (this script itself).")
                continue

            # Only consider Python (.py) files and UI (.ui) files (for example, from QtDesigner)
            if file.endswith('.py') or (file.endswith('.ui') and 'QtDesigner' in root):
                source_file = os.path.join(root, file)
                destination_subdir = os.path.relpath(root, source_folder)
                destination_file = os.path.join(destination, destination_subdir, file)

                files_to_backup.append({'source': source_file, 'destination': destination_file})

    # Backup all files first
    if files_to_backup:
        # Create a versioned backup folder
        versioned_backup_folder = create_backup_folder()
        backup_files(files_to_backup, versioned_backup_folder)

        # Copy files to the destination folder, maintaining folder structure
        for file_info in files_to_backup:
            source_file = file_info['source']
            destination_file = file_info['destination']

            # Ensure destination directory exists
            os.makedirs(os.path.dirname(destination_file), exist_ok=True)

            shutil.copy2(source_file, destination_file)
            print(f'Copied {source_file} to {destination_file}')
    else:
        print("No files to backup or sync.")

    # Handle icons folder: search the current directory for an 'icons' folder
    icons_source_folder = os.path.join(source_folder, 'icons')
    icons_destination_folder = os.path.join(destination_folder, 'icons')

    if os.path.exists(icons_source_folder) and os.path.isdir(icons_source_folder):
        copy_icons_filtered(icons_source_folder, icons_destination_folder)
    else:
        print(f"No icons folder found in {source_folder}.")


if __name__ == "__main__":
    sync_files(source_folder, destination_folder)
