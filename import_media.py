#!/usr/bin/env python3
"""
Lightweight Photo and Video Importer
=====================================
A cross-platform media importer that organizes photos and videos by capture date.
Similar in spirit to Sony PlayMemories, but simpler and faster.

Features:
- Imports photos and videos from SD cards or camera folders
- Organizes by capture date (YYYY-MM-DD format)
- Avoids duplicates using a log file
- Checks disk space before importing
- Copy-only: never modifies source files
- Minimal UI: Tkinter dialogs for folder selection, console for progress
- Real-time transfer speed monitoring and time estimates
"""

import os
import sys
import argparse
import shutil
import json
import hashlib
import time
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, Tuple, List, Dict
import tkinter as tk
from tkinter import filedialog

# Try to import PIL for EXIF data extraction
try:
    from PIL import Image
    from PIL.ExifTags import TAGS
    HAS_PIL = True
except ImportError:
    HAS_PIL = False
    print("Warning: PIL/Pillow not installed. Will use file modification dates instead of EXIF.")
    print("Install with: pip install Pillow")

# ============================================================================
# CONFIGURATION
# ============================================================================

# Supported media file extensions (case-insensitive)
PHOTO_EXTENSIONS = {
    '.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.tif',
    '.raw', '.cr2', '.cr3', '.nef', '.arw', '.dng', '.orf', '.rw2',
    '.heic', '.heif', '.webp'
}

VIDEO_EXTENSIONS = {
    '.mp4', '.mov', '.avi', '.mkv', '.wmv', '.flv', '.m4v',
    '.mpg', '.mpeg', '.3gp', '.mts', '.m2ts', '.webm'
}

ALL_MEDIA_EXTENSIONS = PHOTO_EXTENSIONS | VIDEO_EXTENSIONS

# Log file name (stored in destination root)
LOG_FILE_NAME = 'import_log.json'

# Date format for folder names (M-D-YYYY without leading zeros)
# Example: 1-12-2025, 11-14-2025
DATE_FOLDER_FORMAT = 'custom'  # Will use custom formatting function

# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

def format_bytes(bytes_size: int) -> str:
    """Convert bytes to human-readable format."""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if bytes_size < 1024.0:
            return f"{bytes_size:.2f} {unit}"
        bytes_size /= 1024.0
    return f"{bytes_size:.2f} PB"


def format_date_folder(date: datetime) -> str:
    """
    Format a date for folder naming.
    Uses M-D-YYYY format (no leading zeros).
    Examples: 1-12-2025, 11-14-2025, 2-3-2024
    """
    return f"{date.month}-{date.day}-{date.year}"


def get_file_hash(filepath: Path, quick: bool = True) -> str:
    """
    Generate a hash of the file for duplicate detection.
    If quick=True, only hash filename and size for speed.
    """
    if quick:
        # Quick hash: just filename + size
        stat = filepath.stat()
        hash_str = f"{filepath.name}_{stat.st_size}"
        return hashlib.md5(hash_str.encode()).hexdigest()
    else:
        # Full file hash (slower, more accurate)
        hash_md5 = hashlib.md5()
        with open(filepath, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_md5.update(chunk)
        return hash_md5.hexdigest()


def get_exif_date(filepath: Path) -> Optional[datetime]:
    """
    Extract the capture date from photo EXIF data.
    Returns None if EXIF data is not available.
    """
    if not HAS_PIL:
        return None

    try:
        image = Image.open(filepath)
        exif_data = image._getexif()

        if exif_data is None:
            return None

        # Look for DateTimeOriginal (tag 36867) or DateTime (tag 306)
        for tag_id, value in exif_data.items():
            tag_name = TAGS.get(tag_id, tag_id)

            if tag_name in ('DateTimeOriginal', 'DateTime'):
                # Parse EXIF date format: "YYYY:MM:DD HH:MM:SS"
                try:
                    return datetime.strptime(value, '%Y:%m:%d %H:%M:%S')
                except ValueError:
                    continue

        return None

    except Exception:
        # If any error occurs, return None
        return None


def get_video_date(filepath: Path) -> Optional[datetime]:
    """
    Extract capture date from video metadata.
    For simplicity, we'll use file modification time as fallback.
    A full implementation could use ffmpeg or similar tools.
    """
    # For now, return None and let the caller use file mtime
    # In a more advanced version, you could parse video metadata here
    return None


def get_media_date(filepath: Path) -> datetime:
    """
    Get the capture date for a media file.
    Priority:
    1. EXIF data (for photos)
    2. Video metadata (for videos)
    3. File modification time (fallback)
    """
    ext = filepath.suffix.lower()

    if ext in PHOTO_EXTENSIONS:
        exif_date = get_exif_date(filepath)
        if exif_date:
            return exif_date

    elif ext in VIDEO_EXTENSIONS:
        video_date = get_video_date(filepath)
        if video_date:
            return video_date

    # Fallback: use file modification time
    mtime = filepath.stat().st_mtime
    return datetime.fromtimestamp(mtime)


def is_media_file(filepath: Path) -> bool:
    """Check if a file is a supported media file."""
    return filepath.suffix.lower() in ALL_MEDIA_EXTENSIONS


def select_folder(title: str) -> Optional[str]:
    """
    Show a folder selection dialog using Tkinter.
    Returns the selected folder path or None if cancelled.
    """
    root = tk.Tk()
    root.withdraw()  # Hide the main window
    root.attributes('-topmost', True)  # Bring dialog to front

    folder = filedialog.askdirectory(
        title=title,
        mustexist=True
    )

    root.destroy()

    return folder if folder else None


def get_disk_usage(path: Path) -> Tuple[int, int, int]:
    """
    Get disk usage for the given path.
    Returns (total, used, free) in bytes.
    """
    usage = shutil.disk_usage(path)
    return usage.total, usage.used, usage.free


# ============================================================================
# LOG MANAGEMENT
# ============================================================================

class ImportLog:
    """Manages the import log file that tracks imported media."""

    def __init__(self, dest_root: Path):
        self.dest_root = dest_root
        self.log_path = dest_root / LOG_FILE_NAME
        self.data = self._load()

    def _load(self) -> Dict:
        """Load the log file from disk."""
        if self.log_path.exists():
            try:
                with open(self.log_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError) as e:
                print(f"Warning: Could not load log file: {e}")
                print("Starting with empty log.")
                return self._create_empty_log()
        else:
            return self._create_empty_log()

    def _create_empty_log(self) -> Dict:
        """Create an empty log structure."""
        return {
            'version': '1.0',
            'created': datetime.now().isoformat(),
            'total_files_imported': 0,
            'total_bytes_imported': 0,
            'files': {}  # hash -> file info
        }

    def save(self):
        """Save the log file to disk."""
        try:
            # Ensure destination directory exists
            self.dest_root.mkdir(parents=True, exist_ok=True)

            with open(self.log_path, 'w', encoding='utf-8') as f:
                json.dump(self.data, f, indent=2, ensure_ascii=False)
        except IOError as e:
            print(f"Error: Could not save log file: {e}")

    def is_imported(self, filepath: Path) -> bool:
        """Check if a file has already been imported."""
        file_hash = get_file_hash(filepath)
        return file_hash in self.data['files']

    def add_file(self, filepath: Path, dest_path: Path, file_size: int):
        """Add a file to the import log."""
        file_hash = get_file_hash(filepath)

        self.data['files'][file_hash] = {
            'filename': filepath.name,
            'original_path': str(filepath),
            'dest_path': str(dest_path),
            'size': file_size,
            'import_date': datetime.now().isoformat()
        }

        self.data['total_files_imported'] += 1
        self.data['total_bytes_imported'] += file_size

    def remove_file(self, file_hash: str) -> bool:
        """Remove a file from the import log by its hash."""
        if file_hash in self.data['files']:
            file_info = self.data['files'][file_hash]
            del self.data['files'][file_hash]
            self.data['total_files_imported'] -= 1
            self.data['total_bytes_imported'] -= file_info['size']
            return True
        return False

    def get_stats(self) -> Dict:
        """Get import statistics."""
        return {
            'total_files': self.data['total_files_imported'],
            'total_size': self.data['total_bytes_imported'],
            'total_size_formatted': format_bytes(self.data['total_bytes_imported'])
        }


# ============================================================================
# MEDIA SCANNER
# ============================================================================

class MediaScanner:
    """Scans a source folder for media files."""

    def __init__(self, source_root: Path):
        self.source_root = source_root
        self.media_files: List[Path] = []

    def scan(self) -> List[Path]:
        """
        Recursively scan the source folder for media files.
        Returns a list of Path objects.
        """
        print(f"\nScanning source folder: {self.source_root}")
        print("-" * 60)

        self.media_files = []

        if not self.source_root.exists():
            print(f"Error: Source folder does not exist: {self.source_root}")
            return []

        if not self.source_root.is_dir():
            print(f"Error: Source path is not a directory: {self.source_root}")
            return []

        # Walk through all subdirectories
        for root, _, files in os.walk(self.source_root):
            root_path = Path(root)

            for filename in files:
                filepath = root_path / filename

                if is_media_file(filepath):
                    self.media_files.append(filepath)

        print(f"Found {len(self.media_files)} media files")
        return self.media_files


# ============================================================================
# MEDIA IMPORTER
# ============================================================================

class MediaImporter:
    """Imports media files from source to destination."""

    def __init__(self, source_root: Path, dest_root: Path, dry_run: bool = False):
        self.source_root = source_root
        self.dest_root = dest_root
        self.dry_run = dry_run
        self.log = ImportLog(dest_root)

        # Statistics
        self.files_to_import = 0
        self.files_skipped = 0
        self.files_imported = 0
        self.bytes_to_import = 0
        self.bytes_imported = 0

    def import_media(self):
        """Main import process."""
        print("\n" + "=" * 70)
        print("MEDIA IMPORT")
        print("=" * 70)
        print(f"Source:      {self.source_root}")
        print(f"Destination: {self.dest_root}")
        print(f"Dry run:     {self.dry_run}")
        print("=" * 70)

        # Step 1: Scan source
        scanner = MediaScanner(self.source_root)
        media_files = scanner.scan()

        if not media_files:
            print("\nNo media files found. Nothing to import.")
            return

        # Step 2: Analyze files
        print(f"\nAnalyzing {len(media_files)} files...")
        print("-" * 70)

        files_to_copy: List[Tuple[Path, Path, int]] = []  # (source, dest, size)

        for filepath in media_files:
            # Check if already imported
            if self.log.is_imported(filepath):
                self.files_skipped += 1
                continue

            # Get capture date
            capture_date = get_media_date(filepath)

            # Determine destination folder (by date)
            date_folder_name = format_date_folder(capture_date)
            dest_folder = self.dest_root / date_folder_name
            dest_path = dest_folder / filepath.name

            # Handle filename conflicts
            counter = 1
            while dest_path.exists():
                stem = filepath.stem
                ext = filepath.suffix
                dest_path = dest_folder / f"{stem}_{counter}{ext}"
                counter += 1

            file_size = filepath.stat().st_size
            files_to_copy.append((filepath, dest_path, file_size))

            self.files_to_import += 1
            self.bytes_to_import += file_size

        # Step 3: Check disk space
        print(f"\nFiles to import: {self.files_to_import}")
        print(f"Files skipped (already imported): {self.files_skipped}")
        print(f"Total size: {format_bytes(self.bytes_to_import)}")

        if self.files_to_import == 0:
            print("\nAll files have already been imported. Nothing to do.")
            return

        # Check free space
        total, used, free = get_disk_usage(self.dest_root)
        print(f"\nDestination disk space:")
        print(f"  Total: {format_bytes(total)}")
        print(f"  Used:  {format_bytes(used)}")
        print(f"  Free:  {format_bytes(free)}")

        if free < self.bytes_to_import:
            print(f"\nError: Not enough disk space!")
            print(f"  Required: {format_bytes(self.bytes_to_import)}")
            print(f"  Available: {format_bytes(free)}")
            print(f"  Shortage: {format_bytes(self.bytes_to_import - free)}")
            return

        # Step 4: Copy files (or dry run)
        if self.dry_run:
            self._dry_run_preview(files_to_copy)
        else:
            self._copy_files(files_to_copy)

        # Step 5: Summary
        print("\n" + "=" * 70)
        print("IMPORT COMPLETE")
        print("=" * 70)

        if self.dry_run:
            print(f"Files that would be imported: {self.files_to_import}")
            print(f"Total size: {format_bytes(self.bytes_to_import)}")
        else:
            print(f"Files imported: {self.files_imported}")
            print(f"Files skipped: {self.files_skipped}")
            print(f"Total size imported: {format_bytes(self.bytes_imported)}")

            # Show overall stats
            stats = self.log.get_stats()
            print(f"\nOverall library statistics:")
            print(f"  Total files: {stats['total_files']}")
            print(f"  Total size: {stats['total_size_formatted']}")
            print(f"  Log file: {self.log.log_path}")

    def _dry_run_preview(self, files_to_copy: List[Tuple[Path, Path, int]]):
        """
        Preview what would be imported in dry run mode.

        DRY RUN EXPLANATION:
        When --dry-run is enabled, this script will:
        - Scan the source folder and find all media files
        - Check which files are new (not already imported)
        - Show you exactly what would be copied and where
        - Display the total size and disk space requirements
        - NOT copy any files
        - NOT modify the import log
        - NOT create any new folders

        This lets you safely preview the import operation before committing to it.
        """
        print("\n" + "=" * 70)
        print("DRY RUN - No files will be copied")
        print("=" * 70)
        print("\nWhat this dry run shows you:")
        print("  - Which files would be imported")
        print("  - Where they would be organized (by date)")
        print("  - Total size and disk space requirements")
        print("\nWhat will NOT happen:")
        print("  - No files will be copied")
        print("  - No folders will be created")
        print("  - No import log will be modified")
        print("\n" + "-" * 70)
        print("\nFiles that would be imported:")

        # Group files by date folder
        by_date = {}
        for source, dest, size in files_to_copy:
            date_folder = dest.parent.name
            if date_folder not in by_date:
                by_date[date_folder] = []
            by_date[date_folder].append((source.name, size))

        # Show grouped by date
        for date_folder in sorted(by_date.keys()):
            files = by_date[date_folder]
            folder_size = sum(size for _, size in files)
            print(f"\n  {date_folder}/ ({len(files)} files, {format_bytes(folder_size)})")
            for filename, size in files[:5]:  # Show first 5 files per folder
                print(f"    - {filename} ({format_bytes(size)})")
            if len(files) > 5:
                print(f"    ... and {len(files) - 5} more files")

    def _copy_files(self, files_to_copy: List[Tuple[Path, Path, int]]):
        """Copy files with progress tracking and speed monitoring."""
        print("\n" + "=" * 70)
        print("IMPORTING FILES")
        print("=" * 70)

        start_time = time.time()
        last_update = start_time

        for i, (source, dest, size) in enumerate(files_to_copy, 1):
            # Create destination folder if needed
            dest.parent.mkdir(parents=True, exist_ok=True)

            try:
                # Copy file
                shutil.copy2(source, dest)

                # Add to log
                self.log.add_file(source, dest, size)
                self.files_imported += 1
                self.bytes_imported += size

                # Calculate transfer statistics
                elapsed = time.time() - start_time
                current_time = time.time()

                # Update progress every file or every 2 seconds
                if current_time - last_update >= 2 or i == len(files_to_copy):
                    if elapsed > 0:
                        # Calculate speed
                        speed_mbps = (self.bytes_imported / elapsed) / (1024 * 1024)  # MB/s
                        speed_mbits = speed_mbps * 8  # Mbps

                        # Estimate time remaining
                        bytes_remaining = self.bytes_to_import - self.bytes_imported
                        if bytes_remaining > 0 and self.bytes_imported > 0:
                            seconds_remaining = bytes_remaining / (self.bytes_imported / elapsed)
                            eta = str(timedelta(seconds=int(seconds_remaining)))
                        else:
                            eta = "0:00:00"

                        # Progress percentage
                        progress_pct = (self.bytes_imported / self.bytes_to_import) * 100

                        print(f"[{i}/{self.files_to_import}] {source.name}")
                        print(f"  -> {dest.parent.name}/{dest.name}")
                        print(f"  Progress: {progress_pct:.1f}% | Speed: {speed_mbps:.2f} MB/s ({speed_mbits:.2f} Mbps) | ETA: {eta}")

                        last_update = current_time
                    else:
                        print(f"[{i}/{self.files_to_import}] {source.name} -> {dest.parent.name}/")
                else:
                    # Quick update without stats
                    print(f"[{i}/{self.files_to_import}] {source.name}")

            except Exception as e:
                print(f"[{i}/{self.files_to_import}] {source.name} - ERROR: {e}")

        # Final statistics
        total_time = time.time() - start_time
        avg_speed_mbps = (self.bytes_imported / total_time) / (1024 * 1024) if total_time > 0 else 0
        avg_speed_mbits = avg_speed_mbps * 8

        print("\n" + "-" * 70)
        print(f"Transfer completed in {str(timedelta(seconds=int(total_time)))}")
        print(f"Average speed: {avg_speed_mbps:.2f} MB/s ({avg_speed_mbits:.2f} Mbps)")
        print("-" * 70)

        # Save log
        self.log.save()

    def index_destination(self):
        """
        Index all existing media files in the destination folder.
        This should be run ONCE before your first import to prevent duplicates.

        This will:
        - Scan all date folders in the destination
        - Find all media files
        - Add them to the import log without copying
        - Prevent re-importing files you already have
        """
        print("\n" + "=" * 70)
        print("INDEX DESTINATION FOLDER")
        print("=" * 70)
        print(f"Destination: {self.dest_root}")
        print()
        print("This will scan your existing camera footage library and add all")
        print("media files to the import log, preventing duplicate imports.")
        print("=" * 70)

        if not self.dest_root.exists():
            print(f"\nError: Destination folder does not exist: {self.dest_root}")
            return

        # Count existing files in log before indexing
        existing_count = len(self.log.data['files'])

        print(f"\nCurrent log status:")
        print(f"  Files already logged: {existing_count}")
        print(f"  Total size logged: {format_bytes(self.log.data['total_bytes_imported'])}")

        # Check for files in log that no longer exist on disk
        print(f"\nChecking for deleted files...")
        print("-" * 70)

        deleted_hashes = []
        deleted_size = 0

        for file_hash, file_info in self.log.data['files'].items():
            # Check if dest_path exists (handle drive letter changes)
            try:
                stored_path = Path(file_info['dest_path'])
                file_exists = False

                # First, check if the file exists at the stored path
                if stored_path.exists():
                    file_exists = True
                else:
                    # Drive letter may have changed, try to find file under current dest_root
                    # Extract the relative path by finding the parts after the root folder name
                    try:
                        # Get the root folder name (e.g., "Camera Footage" from "D:\Camera Footage")
                        root_name = self.dest_root.name
                        stored_parts = stored_path.parts

                        # Find where the root folder name appears in the stored path
                        if root_name in stored_parts:
                            root_index = stored_parts.index(root_name)
                            # Get everything after the root folder
                            relative_parts = stored_parts[root_index + 1:]
                            if relative_parts:
                                # Construct new path under current dest_root
                                new_path = self.dest_root
                                for part in relative_parts:
                                    new_path = new_path / part
                                if new_path.exists():
                                    file_exists = True
                    except (ValueError, IndexError):
                        pass

                if not file_exists:
                    deleted_hashes.append((file_hash, file_info))
                    deleted_size += file_info['size']

            except (KeyError, OSError) as e:
                print(f"  Warning: Could not check {file_info.get('filename', 'unknown')}: {e}")

        print(f"Found {len(deleted_hashes)} deleted files ({format_bytes(deleted_size)})")

        # Remove obsolete entries with confirmation
        files_removed = 0
        if len(deleted_hashes) > 0:
            print("\n" + "=" * 70)
            print(f"Ready to remove {len(deleted_hashes)} deleted files from the log.")
            print("These files no longer exist on disk.")
            print("=" * 70)

            response = input("\nProceed with cleanup? (yes/no): ").strip().lower()

            if response in ['yes', 'y']:
                print("\nRemoving deleted entries...")
                for file_hash, file_info in deleted_hashes:
                    self.log.remove_file(file_hash)
                    files_removed += 1

                self.log.save()
                print(f"Removed {files_removed} files from log")
                print(f"Freed {format_bytes(deleted_size)} from log tracking")
            else:
                print("Cleanup cancelled. Deleted files will remain in log.")

        # Scan destination folder
        print(f"\nScanning destination folder: {self.dest_root}")
        print("-" * 70)

        indexed_files = []
        total_size = 0
        folders_scanned = 0

        # Walk through all subdirectories in destination
        for root, dirs, files in os.walk(self.dest_root):
            root_path = Path(root)

            # Skip the root folder itself
            if root_path == self.dest_root:
                continue

            folders_scanned += 1
            folder_files = 0

            for filename in files:
                filepath = root_path / filename

                # Skip the log file itself
                if filepath.name == LOG_FILE_NAME:
                    continue

                if is_media_file(filepath):
                    # Check if already in log
                    if not self.log.is_imported(filepath):
                        try:
                            file_size = filepath.stat().st_size
                            indexed_files.append((filepath, file_size))
                            total_size += file_size
                            folder_files += 1
                        except Exception as e:
                            print(f"  Warning: Could not access {filepath.name}: {e}")

            if folder_files > 0:
                print(f"  {root_path.name}/: {folder_files} new files")

        # Summary of what was found
        print("\n" + "-" * 70)
        print(f"Scan complete!")
        print(f"  Folders scanned: {folders_scanned}")
        print(f"  New files found: {len(indexed_files)}")
        print(f"  Total size: {format_bytes(total_size)}")
        print(f"  Already logged: {existing_count} files (skipped)")

        if len(indexed_files) == 0:
            print("\nNo new files to index. Your log is up to date!")
            return

        # Ask for confirmation
        print("\n" + "=" * 70)
        print(f"Ready to add {len(indexed_files)} files to the import log.")
        print("This will NOT copy or move any files.")
        print("=" * 70)

        response = input("\nProceed with indexing? (yes/no): ").strip().lower()

        if response not in ['yes', 'y']:
            print("Indexing cancelled.")
            return

        # Add files to log
        print("\nIndexing files...")
        print("-" * 70)

        start_time = time.time()

        for i, (filepath, file_size) in enumerate(indexed_files, 1):
            # Add to log (using the file's current location as both source and dest)
            self.log.add_file(filepath, filepath, file_size)

            # Show progress every 100 files or at the end
            if i % 100 == 0 or i == len(indexed_files):
                elapsed = time.time() - start_time
                files_per_sec = i / elapsed if elapsed > 0 else 0
                print(f"  Indexed {i}/{len(indexed_files)} files ({files_per_sec:.0f} files/sec)")

        # Save the updated log
        self.log.save()

        # Final summary
        elapsed_total = time.time() - start_time
        print("\n" + "=" * 70)
        print("INDEXING COMPLETE")
        print("=" * 70)
        if files_removed > 0:
            print(f"Files removed from log: {files_removed} (deleted from disk)")
        print(f"Files added to log: {len(indexed_files)} (new on disk)")
        if len(indexed_files) > 0:
            print(f"Total size added: {format_bytes(total_size)}")
        print(f"Time elapsed: {str(timedelta(seconds=int(elapsed_total)))}")

        # Show updated stats
        stats = self.log.get_stats()
        print(f"\nUpdated log statistics:")
        print(f"  Total files in log: {stats['total_files']}")
        print(f"  Total size in log: {stats['total_size_formatted']}")
        print(f"  Log file: {self.log.log_path}")
        print("\nYou can now run imports without --index-destination and existing")
        print("files will be automatically skipped!")


# ============================================================================
# MAIN
# ============================================================================

def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description='Lightweight Photo and Video Importer',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # FIRST TIME SETUP: Index your existing camera footage library
  # (Run this ONCE before your first import to prevent duplicates)
  python import_media.py --index-destination --dest "E:\\Camera Footage"

  # Interactive mode (dialogs for both folders)
  python import_media.py

  # Specify destination, choose source via dialog
  python import_media.py --dest "E:\\"

  # Full CLI mode
  python import_media.py --source "F:\\DCIM" --dest "E:\\Camera Footage"

  # Dry run (preview without copying)
  python import_media.py --source "F:\\DCIM" --dest "E:\\" --dry-run
        """
    )

    parser.add_argument(
        '--source',
        type=str,
        help='Source folder (e.g., SD card mount point or camera folder)'
    )

    parser.add_argument(
        '--dest',
        type=str,
        help='Destination folder (main import folder containing date subfolders)'
    )

    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Show what would be imported without actually copying files'
    )

    parser.add_argument(
        '--index-destination',
        action='store_true',
        help='Index all existing media files in destination folder and add to log (run once before first import)'
    )

    args = parser.parse_args()

    # Handle indexing mode (only needs destination)
    if args.index_destination:
        # Get destination folder
        dest_path = None
        if args.dest:
            dest_path = Path(args.dest)
        else:
            print("Select DESTINATION folder to index (your existing camera footage library)...")
            dest_str = select_folder("Select DESTINATION folder to index")
            if dest_str:
                dest_path = Path(dest_str)

        if not dest_path:
            print("Error: No destination folder selected. Exiting.")
            return 1

        # Create importer with dummy source (not used for indexing)
        importer = MediaImporter(dest_path, dest_path, dry_run=False)
        importer.index_destination()
        return 0

    # Normal import mode - needs both source and destination
    # Get source folder
    source_path = None
    if args.source:
        source_path = Path(args.source)
    else:
        print("Select SOURCE folder (camera/SD card)...")
        source_str = select_folder("Select SOURCE folder (camera/SD card)")
        if source_str:
            source_path = Path(source_str)

    if not source_path:
        print("Error: No source folder selected. Exiting.")
        return 1

    # Get destination folder
    dest_path = None
    if args.dest:
        dest_path = Path(args.dest)
    else:
        print("Select DESTINATION folder (main import folder)...")
        dest_str = select_folder("Select DESTINATION folder (main import folder)")
        if dest_str:
            dest_path = Path(dest_str)

    if not dest_path:
        print("Error: No destination folder selected. Exiting.")
        return 1

    # Run importer
    importer = MediaImporter(source_path, dest_path, dry_run=args.dry_run)
    importer.import_media()

    return 0


if __name__ == '__main__':
    sys.exit(main())
