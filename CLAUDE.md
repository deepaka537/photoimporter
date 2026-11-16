# Photo Importer

Copies photos/videos from an SD card or camera into a library folder, organized into
`M-D-YYYY` date folders (no leading zeros), skipping anything already imported.

## Primary tool: the importer
- `import_media.py` — all import logic. Standard library only (tkinter for folder pickers); no pip installs needed.
- `run_importer.py` / `run_importer.bat` — double-click launcher; opens source + destination folder dialogs.
- `index_library.bat` — runs `--index-destination` once on an existing library so it isn't re-imported.

CLI:
```
python import_media.py --source "F:\DCIM" --dest "E:\Camera Footage" [--dry-run]
python import_media.py --index-destination --dest "E:\Camera Footage"
```

### How duplicate detection works
- `import_log.json` lives in the library root (not the repo), keyed by a hash.
- Default hash is **quick**: MD5 of `filename + size` (`get_file_hash`, quick=True), not file contents.
- Stored `dest_path` values are absolute, but `--index-destination` re-resolves them under the
  current `--dest` if the drive letter changed, so the library can move between drives/machines.

## Secondary: analyzer (`analyzer/`, rarely used)
Flask web GUI + face detection / burst detection (`server.py`, `analyze.py`, `db.py`, `face_engine.py`).
Needs `pip install -r analyzer/requirements.txt` (torch etc.). Its SQLite DB and thumbnail cache store
absolute paths, so they won't survive a drive-letter change. Not a priority.

## Setup on a new machine
1. Install Python 3.10+ from python.org (includes tkinter) and check "Add to PATH".
2. `git clone` this repo.
3. Run `run_importer.bat`. If the library already exists but has no `import_log.json`, run `index_library.bat` first.

## Preferences
- Windows 11; concise responses.
- Keep the importer dependency-free.
