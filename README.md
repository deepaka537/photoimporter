# Photo & Video Importer

A lightweight, cross-platform media importer that automatically organizes photos and videos by capture date. Similar to Sony PlayMemories, but simpler and faster.

## Features

- ✅ **Automatic Organization** - Sorts media by date into `M-D-YYYY` folders (e.g., `11-14-2025`)
- ✅ **EXIF Support** - Uses actual capture dates from photo metadata (requires Pillow)
- ✅ **Duplicate Prevention** - Tracks imported files to avoid re-importing
- ✅ **Progress Tracking** - Real-time transfer speed (MB/s, Mbps) and ETA
- ✅ **Disk Space Checking** - Verifies sufficient space before importing
- ✅ **Safe & Copy-Only** - Never deletes or modifies source files
- ✅ **Multiple File Formats** - JPG, RAW (CR2, CR3, NEF, ARW, RW2, DNG), MOV, MP4, and more
- ✅ **Library Indexing** - Index existing files to prevent duplicates

## Installation

### Requirements
- Python 3.6 or higher
- `tkinter` (included with most Python installations)
- `Pillow` (optional, but recommended for EXIF support)

### Install Pillow (Recommended)
```bash
pip install Pillow
```

Without Pillow, the script uses file modification dates instead of actual capture dates.

## Quick Start

### Method 1: Quick Launch (Easiest)
Double-click `run_importer.bat` or run:
```bash
python run_importer.py
```
This opens folder picker dialogs - no command line needed!

### Method 2: Command Line

**First Time Setup - Index Your Existing Library:**
```bash
python import_media.py --index-destination --dest "E:\Camera Footage"
```

**Import from SD Card/Camera:**
```bash
python import_media.py
```

## Command Reference

### Indexing (First Time Only)

Before your first import, index your existing camera footage library to prevent duplicates:

```bash
# Index with folder picker dialog
python import_media.py --index-destination

# Index with specific path
python import_media.py --index-destination --dest "E:\Camera Footage"
```

**What indexing does:**
- Scans all media files in your existing library
- Adds them to the import log
- Prevents re-importing files you already have
- **Does NOT copy, move, or modify any files**

### Normal Import

```bash
# Interactive mode (dialogs for both source and destination)
python import_media.py

# Specify destination, choose source via dialog
python import_media.py --dest "E:\Camera Footage"

# Full CLI mode (no dialogs)
python import_media.py --source "D:\DCIM" --dest "E:\Camera Footage"
```

### Dry Run (Preview)

See what would be imported without actually copying:

```bash
# Dry run with dialogs
python import_media.py --dry-run

# Dry run with specific paths
python import_media.py --source "D:\DCIM" --dest "E:\Camera Footage" --dry-run
```

**What dry run shows:**
- Which files would be imported
- Where they would be organized (by date)
- Total size and disk space requirements
- **Does NOT copy any files or modify the log**

## Folder Structure

Media files are organized by capture date:

```
E:\Camera Footage\
├── import_log.json          # Tracks all imported files
├── 1-12-2025\               # January 12, 2025
│   ├── IMG_1234.JPG
│   ├── IMG_1234.CR2
│   └── VID_5678.MOV
├── 11-14-2025\              # November 14, 2025
│   ├── DSC_9876.JPG
│   └── DSC_9876.ARW
└── 2-3-2024\                # February 3, 2024
    └── P1303000.RW2
```

## Supported File Formats

### Photos
- **Common**: JPG, JPEG, PNG, GIF, BMP, TIFF, WEBP
- **RAW Formats**: CR2, CR3 (Canon), NEF (Nikon), ARW (Sony), RW2 (Panasonic), DNG, ORF (Olympus)
- **Modern**: HEIC, HEIF

### Videos
- **Common**: MP4, MOV, AVI, MKV, WMV
- **Camera Formats**: M2TS, MTS, 3GP, FLV
- **Modern**: WEBM

## How It Works

1. **Scan** - Finds all media files in the source folder
2. **Analyze** - Reads capture dates from EXIF data (or file dates)
3. **Check** - Verifies files aren't already imported (via log)
4. **Verify** - Checks sufficient disk space
5. **Copy** - Copies files to date-organized folders
6. **Log** - Updates import log with new files

## Import Log

The script maintains `import_log.json` in your destination folder:

```json
{
  "version": "1.0",
  "created": "2025-11-19T12:34:56",
  "total_files_imported": 1523,
  "total_bytes_imported": 85432198765,
  "files": {
    "abc123...": {
      "filename": "IMG_1234.JPG",
      "original_path": "D:\\DCIM\\IMG_1234.JPG",
      "dest_path": "E:\\Camera Footage\\11-14-2025\\IMG_1234.JPG",
      "size": 4523456,
      "import_date": "2025-11-19T12:35:01"
    }
  }
}
```

## Common Workflows

### First Time Setup
```bash
# 1. Index your existing library
python import_media.py --index-destination --dest "E:\Camera Footage"

# 2. Import from camera/SD card
python import_media.py --source "D:" --dest "E:\Camera Footage"
```

### Regular Imports (After Initial Setup)
```bash
# Quick mode - just double-click run_importer.bat
# Or use command line:
python import_media.py --dest "E:\Camera Footage"
```

### Preview Before Importing
```bash
# See what would be imported
python import_media.py --source "D:" --dest "E:\Camera Footage" --dry-run
```

## Troubleshooting

### "PIL/Pillow not installed" Warning
**Solution**: Install Pillow for better date detection
```bash
pip install Pillow
```
Without Pillow, the script uses file modification dates (still works fine!).

### Files Already Imported
If the script says files were skipped because they're already imported, this is normal - it prevents duplicates.

To re-import anyway:
1. Delete or rename `import_log.json` in your destination folder
2. Run import again

### Duplicate Files with Different Names
The script uses filename + size for duplicate detection. If you have the same photo with different filenames, both will be imported.

### Transfer Speed Issues
Slow transfers are usually due to:
- USB 2.0 instead of USB 3.0
- SD card reader speed
- Network drives
- Disk fragmentation

### Filename Conflicts
If a file already exists in the destination with the same name, the script appends a number:
- `IMG_1234.JPG` → `IMG_1234_1.JPG`

## Safety Features

- ✅ **Copy-only operation** - Source files are never modified or deleted
- ✅ **Pre-flight checks** - Verifies disk space before copying
- ✅ **Error handling** - Continues on errors, reports at end
- ✅ **Dry run mode** - Preview without making changes
- ✅ **Duplicate protection** - Tracks imported files in log

## Performance

- **Fast scanning** - Efficient file system traversal
- **Quick duplicate checks** - Hash-based lookups (filename + size)
- **Real-time progress** - Speed and ETA updates every 2 seconds
- **Batch processing** - Handles thousands of files efficiently

Typical performance:
- **Small files** (JPG): 500-1000 files/min
- **RAW files** (20-40MB): 50-100 files/min
- **Videos** (100MB+): 10-50 files/min

*Performance depends on disk speed, USB connection, and file sizes.*

## Command Line Options

```
usage: import_media.py [-h] [--source SOURCE] [--dest DEST]
                       [--dry-run] [--index-destination]

options:
  --source SOURCE          Source folder (SD card/camera)
  --dest DEST             Destination folder (main library)
  --dry-run               Preview without copying
  --index-destination     Index existing library (first time only)
  -h, --help              Show help message
```

## Examples

### Example 1: First Time User
```bash
# Step 1: Index existing library
python import_media.py --index-destination --dest "E:\Camera Footage"

# Step 2: Import from SD card
python import_media.py
# (Select D:\ as source, E:\Camera Footage as destination in dialogs)
```

### Example 2: Regular User
```bash
# Just run the quick launcher
python run_importer.py
```

### Example 3: Power User (CLI)
```bash
# Import with specific paths, no dialogs
python import_media.py --source "F:\DCIM" --dest "E:\Camera Footage"
```

### Example 4: Testing/Preview
```bash
# Preview what would be imported
python import_media.py --source "D:" --dest "E:\Camera Footage" --dry-run
```

## Tips & Best Practices

1. **Always index first** - Run `--index-destination` on your existing library before the first import
2. **Use EXIF dates** - Install Pillow for accurate capture dates instead of file dates
3. **Keep the log file** - Don't delete `import_log.json` unless you want to re-import everything
4. **Regular backups** - Keep backups of your camera footage library
5. **Verify imports** - Check a few folders after importing to ensure files copied correctly
6. **Use dry run** - Test with `--dry-run` before importing large batches

## Technical Details

### Duplicate Detection
Files are identified by: `MD5(filename + filesize)`

This provides a good balance of:
- **Speed** - No need to read entire file contents
- **Accuracy** - Catches most duplicates
- **Efficiency** - Fast lookups in the log

### Date Detection Priority
1. **EXIF DateTimeOriginal** (most accurate - actual capture time)
2. **EXIF DateTime** (camera date/time)
3. **File modification time** (fallback if no EXIF)

### Log File Location
The log is stored in the destination root: `<destination>/import_log.json`

This ensures:
- Each library has its own log
- Portable across drives
- Survives source media changes

## License

This script is provided as-is for personal use. Feel free to modify and distribute.

## Support

For issues or questions:
1. Check the **Troubleshooting** section above
2. Verify Python and Pillow are installed correctly
3. Try with `--dry-run` to preview behavior
4. Check the import log for details on skipped files

---

## Analyzer & Web GUI

The `analyzer/` subfolder adds face recognition, burst detection, and a local web browser GUI on top of your imported library.

### Install Analyzer Requirements

```bash
pip install -r analyzer/requirements.txt
```

### Step-by-Step: Face Recognition

```bash
# Step 1 -- detect faces and burst groups in newly imported photos
python analyzer/analyze.py --dest "D:\Camera Footage" --faces --bursts

# Step 2 -- cluster detected faces into people
python analyzer/analyze.py --dest "D:\Camera Footage" --cluster

# Step 3 -- interactively name each person cluster (or rename in the web GUI)
python analyzer/analyze.py --dest "D:\Camera Footage" --label
```

Re-run Step 1 after every import. Steps 2-3 only need re-running when you want to update clusters.

### Burst Detection Only

```bash
python analyzer/analyze.py --dest "D:\Camera Footage" --bursts
```

### Browse Your Library (Web GUI)

```bash
python analyzer/server.py --dest "D:\Camera Footage"
```

Opens http://localhost:5000 automatically. Three views:

- **Timeline** -- photos by date folder; filter by type (JPEG / RAW) and sort by date taken
- **People** -- face clusters; double-click a name to rename inline
- **Bursts** -- burst groups with sharpness scores and BEST badge on the sharpest shot

Use `--port 5001` if port 5000 is already taken.

### Other Useful Analyzer Commands

```bash
# List all named people
python analyzer/analyze.py --dest "D:\Camera Footage" --people

# Find all photos of a named person
python analyzer/analyze.py --dest "D:\Camera Footage" --find "Alice"

# Find photos matching a reference face image
python analyzer/analyze.py --dest "D:\Camera Footage" --find-similar "C:\ref.jpg"

# Show database statistics
python analyzer/analyze.py --dest "D:\Camera Footage" --stats

# Export burst groups to review subfolders (hardlinks -- no extra disk space)
python analyzer/analyze.py --dest "D:\Camera Footage" --export-bursts "D:\burst_review"

# Generate a standalone HTML burst gallery
python analyzer/analyze.py --dest "D:\Camera Footage" --burst-html "D:\burst_review.html"
```

### Analyzer Data Files

All analyzer data lives in your library root alongside `import_log.json`. No photos are ever modified.

| File | Contents |
|---|---|
| `photo_analysis.db` | SQLite: face embeddings, people clusters, burst groups |
| `.thumbnails\` | JPEG thumbnail cache (safe to delete to free disk space) |
