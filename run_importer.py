#!/usr/bin/env python3
"""
Quick Launcher for Photo & Video Importer
==========================================
Double-click this file to launch the importer with folder picker dialogs.
No command line needed!
"""

import sys
from pathlib import Path

# Import the main script
import import_media

if __name__ == '__main__':
    print("=" * 70)
    print("PHOTO & VIDEO IMPORTER - QUICK LAUNCH")
    print("=" * 70)
    print()
    print("This will open folder selection dialogs.")
    print("You'll be asked to select:")
    print("  1. SOURCE folder (your camera/SD card)")
    print("  2. DESTINATION folder (your camera footage library)")
    print()
    print("=" * 70)
    print()

    # Run the main import with no arguments (will use dialogs)
    sys.exit(import_media.main())
