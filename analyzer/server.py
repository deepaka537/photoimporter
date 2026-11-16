#!/usr/bin/env python3
"""
Photo Library Web GUI — Flask server.

Usage:
    python analyzer/server.py --dest "E:\\Camera Footage"

Opens http://localhost:5000 in the browser automatically.
"""

import sys
import os
import io
import re
import hashlib
import threading
import argparse
import webbrowser
from pathlib import Path
from typing import Optional, Tuple, Dict, List
from urllib.parse import quote

# Flask
from flask import Flask, jsonify, send_file, request, abort, render_template

# Local modules (same directory as server.py)
sys.path.insert(0, str(Path(__file__).parent))
import db
import quality

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PHOTO_EXTENSIONS = {
    '.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.tif',
    '.raw', '.cr2', '.cr3', '.nef', '.arw', '.dng', '.orf', '.rw2',
    '.heic', '.heif', '.webp',
}

RAW_EXTENSIONS = {'.cr2', '.cr3', '.nef', '.arw', '.dng', '.orf', '.rw2', '.raw'}

THUMB_SIZES: Dict[str, Tuple[int, int]] = {
    'sm': (320, 240),
    'lg': (800, 600),
}

# ---------------------------------------------------------------------------
# Globals (populated in main())
# ---------------------------------------------------------------------------

app = Flask(__name__)

DEST_ROOT: Optional[Path] = None
thumb_cache: Optional['ThumbnailCache'] = None

_burst_score_cache: Dict[str, list] = {}
_burst_score_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Thumbnail Cache
# ---------------------------------------------------------------------------

class ThumbnailCache:
    """
    Generates and caches JPEG thumbnails on disk.
    Uses GPU (CUDA NVJPEG + torchvision) for JPEG decoding when available,
    falls back to Pillow for everything else.
    """

    def __init__(self, dest_root: Path):
        self.dest_root = dest_root
        self.cache_dir = dest_root / '.thumbnails'
        self.cache_dir.mkdir(exist_ok=True)

        self._done = 0
        self._total = 0
        self._lock = threading.Lock()

        self._use_gpu = False
        try:
            import torch
            if torch.cuda.is_available():
                self._use_gpu = True
                print(f"[ThumbnailCache] GPU: {torch.cuda.get_device_name(0)}")
            else:
                print("[ThumbnailCache] CUDA not available — Pillow fallback")
        except ImportError:
            print("[ThumbnailCache] torch not installed — Pillow fallback")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self, abs_path: str, size_name: str = 'sm') -> Optional[bytes]:
        """Return cached thumbnail bytes, generating on demand."""
        cached = self._cache_key(abs_path, size_name)
        if cached.exists():
            return cached.read_bytes()
        data = self._generate(Path(abs_path), size_name)
        if data:
            try:
                cached.write_bytes(data)
            except OSError:
                pass
        return data

    def progress(self) -> Dict:
        with self._lock:
            return {'done': self._done, 'total': self._total}

    def start_precache(self):
        t = threading.Thread(target=self._precache_worker, daemon=True, name='precache')
        t.start()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _cache_key(self, abs_path: str, size_name: str) -> Path:
        h = hashlib.md5(f"{abs_path}{size_name}".encode('utf-8')).hexdigest()
        return self.cache_dir / f"{h}.jpg"

    def _jpeg_companion(self, raw_path: Path) -> Optional[Path]:
        """Return a companion JPEG next to a RAW file, if one exists."""
        for ext in ('.jpg', '.jpeg', '.JPG', '.JPEG'):
            companion = raw_path.with_suffix(ext)
            if companion.exists():
                return companion
        return None

    def _generate(self, src: Path, size_name: str) -> Optional[bytes]:
        size = THUMB_SIZES.get(size_name, THUMB_SIZES['sm'])
        if src.suffix.lower() in RAW_EXTENSIONS:
            result = quality.make_thumbnail(src, size)
            if result:
                return result
            # rawpy failed — use the companion JPEG if present
            companion = self._jpeg_companion(src)
            if companion:
                return quality.make_thumbnail(companion, size)
            return None
        if self._use_gpu:
            result = self._generate_gpu(src, size)
            if result:
                return result
        return quality.make_thumbnail(src, size)

    def _generate_gpu(self, src: Path, size: Tuple[int, int]) -> Optional[bytes]:
        try:
            import torch
            import torch.nn.functional as F
            import torchvision.io as tvio

            raw_bytes = tvio.read_file(str(src))
            img = tvio.decode_jpeg(raw_bytes, device='cuda')   # [C, H, W] uint8

            _, H, W = img.shape
            tw, th = size
            scale = min(tw / W, th / H)
            nw = max(1, int(round(W * scale)))
            nh = max(1, int(round(H * scale)))

            img_f = img.float().unsqueeze(0)   # [1, C, H, W]
            resized = F.interpolate(img_f, size=(nh, nw), mode='bilinear', align_corners=False)
            resized_u8 = resized.squeeze(0).clamp(0, 255).to(torch.uint8).cpu()

            jpeg_tensor = tvio.encode_jpeg(resized_u8, quality=82)
            return bytes(jpeg_tensor.numpy())
        except Exception:
            return None

    def _precache_worker(self):
        photos: List[str] = []
        try:
            for entry in os.scandir(self.dest_root):
                if not entry.is_dir() or entry.name.startswith('.'):
                    continue
                try:
                    for sub in os.scandir(entry.path):
                        if sub.is_file() and Path(sub.name).suffix.lower() in PHOTO_EXTENSIONS:
                            photos.append(sub.path)
                except PermissionError:
                    pass
        except Exception as exc:
            print(f"[precache] Scan error: {exc}")
            return

        n_sizes = len(THUMB_SIZES)
        with self._lock:
            self._total = len(photos) * n_sizes

        print(f"[precache] Pre-generating thumbnails for {len(photos)} photos × {n_sizes} sizes …")

        for photo_path in photos:
            for size_name in THUMB_SIZES:
                cached = self._cache_key(photo_path, size_name)
                if not cached.exists():
                    self._generate(Path(photo_path), size_name)
                with self._lock:
                    self._done += 1

        print("[precache] Done.")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _validate_path(raw: str) -> Path:
    """Resolve raw to an absolute Path and abort(403) if outside DEST_ROOT."""
    try:
        p = Path(raw).resolve()
        p.relative_to(DEST_ROOT.resolve())
    except (ValueError, OSError):
        abort(403)
    return p


def _parse_folder_date(name: str) -> Tuple[tuple, Optional[int]]:
    """
    Parse folder name into ((year, month, day), year).
    Supports M-D-YYYY (import_media format) and YYYY-MM-DD.
    """
    m = re.match(r'^(\d{1,2})-(\d{1,2})-(\d{4})$', name)
    if m:
        mo, d, yr = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return (yr, mo, d), yr
    m = re.match(r'^(\d{4})-(\d{2})-(\d{2})$', name)
    if m:
        yr, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return (yr, mo, d), yr
    return (0, 0, 0), None


def _thumb_url(abs_path: str, size: str = 'sm') -> str:
    return f'/thumb?p={quote(abs_path)}&s={size}'


def _photo_url(abs_path: str) -> str:
    return f'/photo?p={quote(abs_path)}'


def _read_exif(fp: Path) -> Dict:
    """Read JPEG EXIF metadata. Returns {} on failure or for RAW files."""
    if fp.suffix.lower() in RAW_EXTENSIONS:
        return {}
    result: Dict = {}
    try:
        from PIL import Image
        from PIL.ExifTags import TAGS
        with Image.open(fp) as img:
            exif = img._getexif()
        if not exif:
            return result
        for tag_id, value in exif.items():
            tag = TAGS.get(tag_id, str(tag_id))
            try:
                if tag == 'DateTimeOriginal':
                    result['date'] = str(value)
                elif tag == 'Make':
                    result['make'] = str(value).strip('\x00').strip()
                elif tag == 'Model':
                    result['model'] = str(value).strip('\x00').strip()
                elif tag == 'ISOSpeedRatings':
                    result['iso'] = int(value)
                elif tag == 'FNumber':
                    result['aperture'] = round(float(value), 1)
                elif tag == 'ExposureTime':
                    f = float(value)
                    result['shutter'] = f'1/{round(1/f)}' if f < 1 else str(round(f, 2))
                elif tag in ('ExifImageWidth', 'ImageWidth'):
                    result.setdefault('width', int(value))
                elif tag in ('ExifImageHeight', 'ImageLength'):
                    result.setdefault('height', int(value))
            except Exception:
                pass
    except Exception:
        pass
    return result


def _get_scored_bursts(folder_abs: Optional[str] = None) -> List[Dict]:
    """Return all burst groups with sharpness scoring, cached in memory.

    If folder_abs is given (absolute path string), only return groups
    whose photos live inside that folder.
    """
    groups = db.get_burst_groups(DEST_ROOT)
    if folder_abs:
        groups = {
            gid: paths for gid, paths in groups.items()
            if any(str(Path(p).parent.resolve()) == folder_abs for p in paths)
        }
    result = []
    with _burst_score_lock:
        for gid, paths in groups.items():
            if gid not in _burst_score_cache:
                _burst_score_cache[gid] = quality.score_group(paths)
            scored = _burst_score_cache[gid]
            if not scored:
                continue
            max_score = scored[0][1] if scored[0][1] > 0 else 1.0
            best_path = scored[0][0]
            date = Path(paths[0]).parent.name
            shots = []
            for path, score in scored:
                pct = min(100, int(score / max_score * 100)) if max_score > 0 else 0
                shots.append({
                    'path': path,
                    'name': Path(path).name,
                    'thumb_url': _thumb_url(path),
                    'sharpness_pct': pct,
                    'is_best': path == best_path,
                })
            result.append({
                'id': gid,
                'date': date,
                'count': len(paths),
                'shots': shots,
            })
    result.sort(key=lambda x: x['date'], reverse=True)
    return result


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/folders')
def api_folders():
    folders = []
    dest = DEST_ROOT.resolve()
    try:
        for root, dirs, files in os.walk(dest):
            # Skip hidden directories
            dirs[:] = [d for d in sorted(dirs) if not d.startswith('.')]
            root_path = Path(root)
            if root_path == dest:
                continue
            count = sum(1 for f in files if Path(f).suffix.lower() in PHOTO_EXTENSIONS)
            if count == 0:
                continue
            rel = root_path.relative_to(dest)
            rel_str = str(rel).replace('\\', '/')
            leaf_name = root_path.name
            sort_key, year = _parse_folder_date(leaf_name)
            folders.append({
                'name': leaf_name,
                'path': rel_str,
                'count': count,
                'year': year,
                '_s': sort_key,
            })
    except OSError as exc:
        return jsonify({'error': str(exc)}), 500
    folders.sort(key=lambda x: x['_s'], reverse=True)
    for f in folders:
        del f['_s']
    return jsonify(folders)


@app.route('/api/folder')
def api_folder():
    rel_path = request.args.get('path', '')
    if not rel_path:
        abort(400)
    # Reject path traversal
    if '..' in rel_path.split('/') or '..' in rel_path.split('\\'):
        abort(400)
    folder_path = (DEST_ROOT / rel_path).resolve()
    try:
        folder_path.relative_to(DEST_ROOT.resolve())
    except ValueError:
        abort(403)
    if not folder_path.is_dir():
        abort(404)

    # Fetch burst groups from DB for photos in this folder
    folder_prefix = str(folder_path) + os.sep
    prefix_len = len(folder_prefix)
    try:
        with db.connect(DEST_ROOT) as conn:
            rows = conn.execute(
                "SELECT file_path, burst_group, capture_ts FROM photos "
                "WHERE SUBSTR(file_path, 1, ?) = ?",
                (prefix_len, folder_prefix)
            ).fetchall()
        db_info = {r['file_path']: (r['burst_group'], r['capture_ts']) for r in rows}
    except Exception:
        db_info = {}

    photos = []
    try:
        entries = sorted(os.scandir(folder_path), key=lambda e: e.name)
    except OSError:
        entries = []

    for entry in entries:
        if not entry.is_file():
            continue
        if Path(entry.name).suffix.lower() not in PHOTO_EXTENSIONS:
            continue
        abs_path = str(Path(entry.path).resolve())
        burst_group, capture_ts = db_info.get(abs_path, (None, None))
        photos.append({
            'path': abs_path,
            'name': entry.name,
            'thumb_url': _thumb_url(abs_path, 'sm'),
            'photo_url': _photo_url(abs_path),
            'date': folder_path.name,
            'burst_group': burst_group,
            'capture_ts': capture_ts,
        })
    return jsonify(photos)


@app.route('/api/people')
def api_people():
    try:
        people = db.get_people(DEST_ROOT)
    except Exception:
        return jsonify([])
    result = []
    for p in people:
        try:
            sample_paths = db.get_photos_for_person(DEST_ROOT, p['id'])[:3]
        except Exception:
            sample_paths = []
        result.append({
            'id': p['id'],
            'name': p['name'],
            'count': p['photo_count'],
            'sample_thumbs': [_thumb_url(path) for path in sample_paths],
        })
    return jsonify(result)


@app.route('/api/person/<int:person_id>')
def api_person(person_id):
    try:
        paths = db.get_photos_for_person(DEST_ROOT, person_id)
    except Exception:
        return jsonify([])
    result = []
    for path in paths:
        result.append({
            'path': path,
            'name': Path(path).name,
            'thumb_url': _thumb_url(path, 'sm'),
            'photo_url': _photo_url(path),
            'date': Path(path).parent.name,
        })
    return jsonify(result)


@app.route('/api/person/<int:person_id>/rename', methods=['POST'])
def api_person_rename(person_id):
    data = request.get_json(silent=True) or {}
    name = str(data.get('name', '')).strip()
    if not name:
        return jsonify({'ok': False, 'error': 'Name cannot be empty'}), 400
    try:
        db.label_person(DEST_ROOT, person_id, name)
    except Exception as exc:
        return jsonify({'ok': False, 'error': str(exc)}), 500
    return jsonify({'ok': True})


@app.route('/api/person/merge', methods=['POST'])
def api_person_merge():
    data = request.get_json(silent=True) or {}
    keep_id  = data.get('keep_id')
    merge_ids = data.get('merge_ids', [])
    if not isinstance(keep_id, int) or not merge_ids:
        return jsonify({'ok': False, 'error': 'keep_id and merge_ids required'}), 400
    if not all(isinstance(i, int) for i in merge_ids):
        return jsonify({'ok': False, 'error': 'merge_ids must be integers'}), 400
    try:
        db.merge_people(DEST_ROOT, keep_id, merge_ids)
    except Exception as exc:
        return jsonify({'ok': False, 'error': str(exc)}), 500
    return jsonify({'ok': True})


@app.route('/api/bursts')
def api_bursts():
    folder_rel = request.args.get('folder', '')
    folder_abs = None
    if folder_rel:
        if '..' in folder_rel.split('/') or '..' in folder_rel.split('\\'):
            abort(400)
        fp = (DEST_ROOT / folder_rel).resolve()
        try:
            fp.relative_to(DEST_ROOT.resolve())
        except ValueError:
            abort(403)
        folder_abs = str(fp)
    try:
        result = _get_scored_bursts(folder_abs)
    except Exception as exc:
        return jsonify({'error': str(exc)}), 500
    return jsonify(result)


@app.route('/api/photo/info')
def api_photo_info():
    raw = request.args.get('p', '')
    if not raw:
        abort(400)
    fp = _validate_path(raw)
    if not fp.exists():
        abort(404)

    exif = _read_exif(fp)
    fp_str = str(fp)

    face_count = 0
    burst_group = None
    try:
        with db.connect(DEST_ROOT) as conn:
            row = conn.execute(
                "SELECT id, burst_group FROM photos WHERE file_path = ?", (fp_str,)
            ).fetchone()
            if row:
                burst_group = row['burst_group']
                face_count = conn.execute(
                    "SELECT COUNT(*) FROM faces WHERE photo_id = ?", (row['id'],)
                ).fetchone()[0]
    except Exception:
        pass

    return jsonify({
        'name': fp.name,
        'date': fp.parent.name,
        'exif': exif,
        'face_count': face_count,
        'burst_group': burst_group,
    })


@app.route('/api/precache_progress')
def api_precache_progress():
    return jsonify(thumb_cache.progress())


@app.route('/api/stats')
def api_stats():
    try:
        s = db.get_stats(DEST_ROOT)
    except Exception:
        s = {}
    return jsonify(s)


@app.route('/thumb')
def serve_thumb():
    raw = request.args.get('p', '')
    size = request.args.get('s', 'sm')
    if size not in THUMB_SIZES:
        size = 'sm'
    if not raw:
        abort(400)
    fp = _validate_path(raw)
    if not fp.exists():
        abort(404)
    data = thumb_cache.get(str(fp), size)
    if not data:
        abort(404)
    response = send_file(io.BytesIO(data), mimetype='image/jpeg')
    response.headers['Cache-Control'] = 'public, max-age=86400'
    return response


@app.route('/photo')
def serve_photo():
    raw = request.args.get('p', '')
    if not raw:
        abort(400)
    fp = _validate_path(raw)
    if not fp.exists():
        abort(404)
    return send_file(str(fp), conditional=True)


@app.route('/api/open_in_explorer', methods=['POST'])
def api_open_in_explorer():
    import subprocess
    data = request.get_json(silent=True) or {}
    raw = data.get('path', '')
    if not raw:
        return jsonify({'ok': False, 'error': 'No path provided'}), 400
    fp = _validate_path(raw)
    if not fp.exists():
        return jsonify({'ok': False, 'error': 'File not found'}), 404
    try:
        # /select highlights the file in Explorer; works on Windows only
        subprocess.Popen(f'explorer /select,"{fp}"', shell=True)
        return jsonify({'ok': True})
    except Exception as exc:
        return jsonify({'ok': False, 'error': str(exc)}), 500


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description='Photo Library Web GUI')
    parser.add_argument('--dest', required=True, help='Photo library destination folder')
    parser.add_argument('--port', type=int, default=5000, help='Port (default: 5000)')
    parser.add_argument('--no-browser', action='store_true', help='Skip auto-opening browser')
    parser.add_argument('--precache', action='store_true',
                        help='Pre-generate all thumbnails in background on startup '
                             '(can slow down browsing on large libraries)')
    args = parser.parse_args()

    global DEST_ROOT, thumb_cache
    DEST_ROOT = Path(args.dest).resolve()

    if not DEST_ROOT.exists():
        print(f"Error: folder does not exist: {DEST_ROOT}")
        sys.exit(1)

    db.init_db(DEST_ROOT)
    thumb_cache = ThumbnailCache(DEST_ROOT)
    if args.precache:
        thumb_cache.start_precache()
    else:
        # Mark precache as complete so the progress bar never shows
        with thumb_cache._lock:
            thumb_cache._done = 0
            thumb_cache._total = 0

    url = f'http://localhost:{args.port}'
    print(f'\nPhoto Library GUI  →  {url}')
    print(f'Library            :  {DEST_ROOT}')
    print('Press Ctrl+C to stop.\n')

    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    app.run(host='0.0.0.0', port=args.port, debug=False, threaded=True)


if __name__ == '__main__':
    main()
