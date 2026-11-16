#!/usr/bin/env python3
"""
Photo Analyzer
==============
Post-import analysis for the Photo & Video Importer library.

Adds two capabilities to an already-imported library:

  Face recognition  — detects faces in every photo, clusters them into
                      people, and lets you name those people so you can
                      find all photos of a specific person.

  Burst detection   — groups photos taken within a few seconds of each
                      other (or with sequential filenames) so you can
                      browse burst sets and pick the best shot.

All data is stored in photo_analysis.db alongside import_log.json.
No source or destination files are ever modified.

Usage
-----
  # Step 1 – process (run whenever new photos have been imported)
  python analyze.py --dest "E:\\Camera Footage" --faces --bursts

  # Step 2 – cluster detected faces into people (run after processing)
  python analyze.py --dest "E:\\Camera Footage" --cluster

  # Step 3 – label the clusters with real names
  python analyze.py --dest "E:\\Camera Footage" --label

  # Find all photos of a named person
  python analyze.py --dest "E:\\Camera Footage" --find "Alice"

  # Find photos similar to a reference face
  python analyze.py --dest "E:\\Camera Footage" --find-similar "ref.jpg"

  # List all known people
  python analyze.py --dest "E:\\Camera Footage" --people

  # List burst groups
  python analyze.py --dest "E:\\Camera Footage" --bursts-list

  # Export burst groups to review folders (hardlinks, no extra disk space)
  python analyze.py --dest "E:\\Camera Footage" --export-bursts "E:\\burst_review"

  # Generate a visual HTML gallery of all burst groups
  python analyze.py --dest "E:\\Camera Footage" --burst-html "E:\\burst_review.html"

  # Show database summary
  python analyze.py --dest "E:\\Camera Footage" --stats
"""

import os
import sys
import time
import argparse
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Tuple

from PIL import Image

# Local modules
import db
import burst_detector

# ---------------------------------------------------------------------------
# Supported extensions (mirrors import_media.py)
# ---------------------------------------------------------------------------

PHOTO_EXTENSIONS = {
    '.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.tif',
    '.raw', '.cr2', '.cr3', '.nef', '.arw', '.dng', '.orf', '.rw2',
    '.heic', '.heif', '.webp',
}

RAW_EXTENSIONS = {
    '.raw', '.cr2', '.cr3', '.nef', '.arw', '.dng', '.orf', '.rw2',
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_dest(args) -> Path:
    if args.dest:
        return Path(args.dest)
    # Fall back to folder picker
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes('-topmost', True)
        folder = filedialog.askdirectory(title="Select your photo library destination folder")
        root.destroy()
        if folder:
            return Path(folder)
    except Exception:
        pass
    print("Error: No destination folder specified. Use --dest.")
    sys.exit(1)


def _get_capture_ts(filepath: Path) -> Optional[float]:
    """
    Read capture timestamp from EXIF (JPEG) or file mtime (fallback / RAW).
    Returns Unix timestamp float or None.
    """
    ext = filepath.suffix.lower()
    if ext not in RAW_EXTENSIONS:
        try:
            from PIL.ExifTags import TAGS
            img = Image.open(filepath)
            exif = img._getexif()
            if exif:
                for tag_id, value in exif.items():
                    if TAGS.get(tag_id) in ('DateTimeOriginal', 'DateTime'):
                        try:
                            dt = datetime.strptime(value, '%Y:%m:%d %H:%M:%S')
                            return dt.timestamp()
                        except ValueError:
                            continue
        except Exception:
            pass
    # Fallback: mtime
    try:
        return filepath.stat().st_mtime
    except Exception:
        return None


def _jpeg_companion(filepath: Path) -> Optional[Path]:
    """
    If filepath is a RAW file and a JPEG with the same stem exists
    in the same folder, return the JPEG path (used for face processing).
    """
    if filepath.suffix.lower() not in RAW_EXTENSIONS:
        return None
    for ext in ('.jpg', '.jpeg'):
        companion = filepath.with_suffix(ext)
        if companion.exists():
            return companion
        companion = filepath.with_suffix(ext.upper())
        if companion.exists():
            return companion
    return None


def _scan_library(dest_root: Path) -> List[Path]:
    """
    Recursively find all JPEG/RAW photos under dest_root.
    Skips the root itself (which only holds log/db files).
    """
    found = []
    for root, dirs, files in os.walk(dest_root):
        root_path = Path(root)
        if root_path == dest_root:
            continue
        for filename in files:
            fp = root_path / filename
            if fp.suffix.lower() in PHOTO_EXTENSIONS:
                found.append(fp)
    return found


def _cosine_similarity(a, b):
    import numpy as np
    a = a / (np.linalg.norm(a) + 1e-8)
    b = b / (np.linalg.norm(b) + 1e-8)
    return float(np.dot(a, b))


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_process(dest_root: Path, do_faces: bool, do_bursts: bool):
    """
    Scan library for new photos, register them in the DB,
    optionally run face detection and/or burst grouping.
    """
    print("\n=== SCANNING LIBRARY ===")
    all_files = _scan_library(dest_root)
    print(f"Found {len(all_files)} photo files")

    # Only register photos not already in the DB
    with db.connect(dest_root) as conn:
        existing = set(r['file_path'] for r in conn.execute("SELECT file_path FROM photos").fetchall())

    new_files = [fp for fp in all_files if str(fp) not in existing]

    if new_files:
        print(f"Registering {len(new_files)} new photos in database...")
        for fp in new_files:
            ts = _get_capture_ts(fp)
            db.upsert_photo(dest_root, str(fp), ts)
        print(f"Registered {len(new_files)} new photos.")
    else:
        print(f"All {len(all_files)} photos already in database.")

    # ---- Burst detection ----
    if do_bursts:
        print("\n=== BURST DETECTION ===")
        photos_ts = db.get_all_photos_with_ts(dest_root)
        print(f"Analysing {len(photos_ts)} photos for bursts...")
        burst_map = burst_detector.detect_bursts(photos_ts)
        db.set_burst_groups(dest_root, burst_map)
        summary = burst_detector.summarise_bursts(burst_map)
        total_burst_photos = len(burst_map)
        print(f"Found {len(summary)} burst groups covering {total_burst_photos} photos")

    # ---- Face detection ----
    if do_faces:
        print("\n=== FACE DETECTION ===")
        from face_engine import FaceEngine
        engine = FaceEngine()

        unprocessed = db.get_unprocessed_photos(dest_root)

        # If a RAW and its companion JPEG are both unprocessed, skip the JPEG —
        # the RAW will be processed using the JPEG as source, covering both.
        # Case-insensitive lookup because Windows FS is case-insensitive but DB
        # stores paths with original casing (e.g. .JPG vs .jpg).
        unprocessed_lower = {p.lower(): p for p in unprocessed}
        companion_jpegs = set()
        for file_path in unprocessed:
            fp = Path(file_path)
            if fp.suffix.lower() in RAW_EXTENSIONS:
                companion = _jpeg_companion(fp)
                if companion:
                    actual = unprocessed_lower.get(str(companion).lower())
                    if actual:
                        companion_jpegs.add(actual)
        if companion_jpegs:
            print(f"  Skipping {len(companion_jpegs)} companion JPEGs (covered by paired RAW)")
            with db.connect(dest_root) as conn:
                for path in companion_jpegs:
                    conn.execute("UPDATE photos SET faces_done = 1 WHERE file_path = ?", (path,))
            unprocessed = [fp for fp in unprocessed if fp not in companion_jpegs]

        print(f"{len(unprocessed)} photos need face processing")

        if not unprocessed:
            print("Nothing to do.")
        else:
            from concurrent.futures import ThreadPoolExecutor

            # Tune these for your hardware:
            #   BATCH_SIZE  images per GPU forward pass (higher = better GPU util, more VRAM)
            #   N_LOADERS   CPU threads for parallel disk/decode (hides I/O latency)
            #   PREFETCH    images to pre-load per chunk (bounds peak RAM; 32 × ~30 MB ≈ 1 GB)
            BATCH_SIZE = 16   # bumped: GPU has plenty of headroom (0.1/6 GB seen)
            N_LOADERS  = 8    # bumped: ARW rawpy decode is CPU-intensive
            PREFETCH   = 64   # bumped: keep pipeline full

            done        = 0
            total_faces = 0
            total       = len(unprocessed)
            start_time  = time.time()

            def _eta_str(seconds):
                if seconds >= 7200:
                    return f"{int(seconds // 3600)}h {int((seconds % 3600) // 60)}m"
                if seconds >= 120:
                    return f"{int(seconds // 60)}m {int(seconds % 60)}s"
                return f"{int(seconds)}s"

            def _gpu_mem():
                try:
                    import torch
                    if torch.cuda.is_available():
                        used = torch.cuda.memory_allocated(0) / 1024 ** 3
                        total_vram = torch.cuda.get_device_properties(0).total_memory / 1024 ** 3
                        return f"  VRAM {used:.1f}/{total_vram:.0f}GB"
                except Exception:
                    pass
                return ""

            def _load_one(file_path):
                """Load + resize one image (thread-safe; uses JPEG companion for RAW)."""
                fp = Path(file_path)
                src = _jpeg_companion(fp) or fp
                img = engine.load_image(src)
                if img is not None:
                    img = engine._resize(img)
                return file_path, fp, img

            batch_paths: List[str]       = []
            batch_fps:   List[Path]      = []
            batch_imgs:  List           = []

            def _flush_batch():
                nonlocal done, total_faces
                if not batch_imgs:
                    return
                results = engine.process_batch_loaded(batch_fps, batch_imgs)
                for bp, (embs, bboxes, confs) in zip(batch_paths, results):
                    db.save_faces(dest_root, bp, embs, bboxes, confs)
                    total_faces += len(embs)
                    done += 1

            pos = 0  # images iterated (for progress %; done lags behind in batches)
            last_name = ''

            with ThreadPoolExecutor(max_workers=N_LOADERS) as pool:
                # Process in PREFETCH-sized chunks so at most PREFETCH decoded images
                # are held in RAM simultaneously (instead of submitting all 34k at once).
                for chunk_start in range(0, total, PREFETCH):
                    chunk = unprocessed[chunk_start:chunk_start + PREFETCH]
                    for file_path, fp, img in pool.map(_load_one, chunk):
                        pos += 1
                        last_name = fp.name

                        if img is not None:
                            batch_paths.append(file_path)
                            batch_fps.append(fp)
                            batch_imgs.append(img)
                        else:
                            # Load failed — mark processed with no faces
                            db.save_faces(dest_root, file_path, [], [], [])
                            done += 1

                        if len(batch_imgs) >= BATCH_SIZE:
                            _flush_batch()
                            batch_paths.clear()
                            batch_fps.clear()
                            batch_imgs.clear()

                        # Progress line
                        elapsed = time.time() - start_time
                        speed   = pos / elapsed if elapsed > 0 else 0
                        eta_sec = (total - pos) / speed if speed > 0 else 0
                        pct     = pos / total * 100
                        line = (
                            f"  [{pos}/{total}] {pct:.1f}%"
                            f"  {speed:.1f} img/s"
                            f"  ETA {_eta_str(eta_sec)}"
                            f"  {total_faces} faces"
                            f"{_gpu_mem()}"
                            f"  {last_name}"
                        )
                        if len(line) > 119:
                            line = line[:116] + "..."
                        print(f"\r{line:<119}", end='', flush=True)

                        if pos % 500 == 0:
                            print()

                # Flush any remaining images that didn't fill a full batch
                _flush_batch()

            elapsed_total = time.time() - start_time
            avg_speed = done / elapsed_total if elapsed_total > 0 else 0
            print(f"\n\nDone. {done} photos in {_eta_str(elapsed_total)}"
                  f"  ({avg_speed:.1f} img/s avg)  —  {total_faces} faces found.")
            print("Run --cluster next to group faces into people.")


def cmd_cluster(dest_root: Path, eps: float = 0.3, min_samples: int = 2, use_hdbscan: bool = False):
    """
    Load all face embeddings and cluster them into people.
    Safe to re-run — existing cluster assignments are overwritten.

    eps        — cosine-distance threshold (DBSCAN only). Lower = stricter.
                 0.25 is tight (fewer false merges, more noise).
                 0.30 is a good default.  0.40 is too loose for most libraries.
    min_samples — minimum faces to form a cluster (noise otherwise).
    use_hdbscan — use HDBSCAN instead of DBSCAN (more robust, no chaining).
    """
    try:
        import numpy as np
    except ImportError:
        print("ERROR: numpy not installed.")
        sys.exit(1)

    if use_hdbscan:
        try:
            import hdbscan as hdbscan_mod
            _hdbscan = hdbscan_mod.HDBSCAN
            _backend = 'hdbscan'
        except ImportError:
            try:
                from sklearn.cluster import HDBSCAN as _hdbscan
                _backend = 'sklearn-hdbscan'
            except ImportError:
                print("ERROR: hdbscan not installed.  Run: pip install hdbscan")
                sys.exit(1)
    else:
        try:
            from sklearn.cluster import DBSCAN as _dbscan
        except ImportError:
            print("ERROR: scikit-learn not installed.  Run: pip install scikit-learn")
            sys.exit(1)

    print("\n=== CLUSTERING FACES ===")
    embeddings, face_ids = db.load_all_embeddings(dest_root)

    if len(embeddings) == 0:
        print("No face embeddings found. Run --faces first.")
        return

    print(f"Clustering {len(embeddings)} face embeddings...")

    # Normalise embeddings to unit length for cosine distance
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True) + 1e-8
    normed = embeddings / norms

    if use_hdbscan:
        # HDBSCAN: no chaining problem, handles variable-density clusters better.
        # min_cluster_size ≈ min_samples; metric='euclidean' on L2-normed vectors
        # is equivalent to cosine distance (euclidean² = 2 - 2·cosine_similarity).
        print(f"  Algorithm : HDBSCAN  (min_cluster_size={min_samples}, {_backend})")
        clustering = _hdbscan(
            min_cluster_size=min_samples,
            min_samples=1,
            metric='euclidean',
            cluster_selection_method='eom',
        ).fit(normed)
        labels = clustering.labels_.tolist()
    else:
        print(f"  Algorithm : DBSCAN  (eps={eps}, min_samples={min_samples}, metric=cosine)")
        clustering = _dbscan(
            eps=eps,
            min_samples=min_samples,
            metric='cosine',
            n_jobs=-1,
        ).fit(normed)
        labels = clustering.labels_.tolist()

    n_clusters = len(set(l for l in labels if l >= 0))
    n_noise    = labels.count(-1)

    print(f"Found {n_clusters} people  ({n_noise} unmatched faces discarded as noise)")

    db.save_clusters(dest_root, face_ids, labels)

    print("\nRun --label to assign names, or --people to see the list.")
    if n_clusters < 10 and not use_hdbscan:
        print(f"\nTip: got very few clusters — try a lower eps (e.g. --eps {eps - 0.05:.2f})")
    elif n_noise > len(embeddings) * 0.5 and not use_hdbscan:
        print(f"\nTip: >50% of faces discarded as noise — try a higher eps (e.g. --eps {eps + 0.05:.2f})")


def cmd_label(dest_root: Path):
    """Interactive session to assign names to detected people clusters."""
    people = db.get_people(dest_root)

    if not people:
        print("No people found. Run --cluster first.")
        return

    print(f"\n=== LABEL PEOPLE ({len(people)} clusters) ===")
    print("Press Enter to skip a cluster, type a name to label it.")
    print("Type 'quit' or Ctrl-C to stop.\n")

    for person in people:
        pid      = person['id']
        name     = person['name'] or '(unnamed)'
        count    = person['photo_count']
        cid      = person['cluster_id']

        print(f"  Person #{pid}  |  {count} photos  |  cluster {cid}  |  current name: {name}")

        # Show a sample of file paths
        sample_paths = db.get_photos_for_person(dest_root, pid)[:3]
        for p in sample_paths:
            print(f"    {p}")

        try:
            raw = input("  Name (Enter to skip): ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nStopped.")
            break

        if raw.lower() == 'quit':
            break
        if raw:
            db.label_person(dest_root, pid, raw)
            print(f"  Labelled as '{raw}'\n")
        else:
            print()


def cmd_people(dest_root: Path):
    """List all known people with photo counts."""
    people = db.get_people(dest_root)

    if not people:
        print("No people found. Run --cluster (and optionally --label) first.")
        return

    print(f"\n{'ID':>4}  {'Name':<24}  {'Photos':>7}  {'Cluster':>8}")
    print("-" * 50)
    for p in people:
        name = p['name'] or '(unnamed)'
        print(f"{p['id']:>4}  {name:<24}  {p['photo_count']:>7}  {p['cluster_id']:>8}")


def cmd_find(dest_root: Path, name: str):
    """Print all file paths containing a named person."""
    person = db.get_person_by_name(dest_root, name)

    if person is None:
        print(f"No person named '{name}' found.")
        print("Run --people to see all labelled people.")
        return

    paths = db.get_photos_for_person(dest_root, person['id'])
    print(f"\nFound {len(paths)} photos of '{name}':\n")
    for p in paths:
        print(f"  {p}")


def cmd_find_similar(dest_root: Path, reference_path: str):
    """
    Encode the face in a reference photo and find the most similar
    people in the library, then list their photos.

    Similarity threshold: cosine similarity >= 0.6 (same person).
    """
    import numpy as np

    print(f"\nEncoding reference photo: {reference_path}")

    try:
        from face_engine import FaceEngine
        engine = FaceEngine()
        ref_img = Image.open(reference_path).convert('RGB')
        ref_emb = engine.encode_single(ref_img)
    except Exception as e:
        print(f"Error: {e}")
        return

    if ref_emb is None:
        print("No face detected in the reference photo.")
        return

    print("Searching library...")

    # Load all embeddings and find nearest people
    all_embs, face_ids = db.load_all_embeddings(dest_root)
    if len(all_embs) == 0:
        print("No face embeddings in library. Run --faces first.")
        return

    # Cosine similarity to every face in library
    norms    = np.linalg.norm(all_embs, axis=1, keepdims=True) + 1e-8
    normed   = all_embs / norms
    ref_norm = ref_emb / (np.linalg.norm(ref_emb) + 1e-8)
    sims     = normed @ ref_norm  # shape [N]

    THRESHOLD = 0.6
    matches = [(face_id, sim) for face_id, sim in zip(face_ids, sims) if sim >= THRESHOLD]

    if not matches:
        print(f"No faces found with similarity >= {THRESHOLD}.")
        return

    # Group matching face_ids by person
    with db.connect(dest_root) as conn:
        person_sims = {}
        for face_id, sim in matches:
            row = conn.execute(
                """SELECT f.person_id, p.name, ph.file_path
                   FROM faces f
                   JOIN photos ph ON ph.id = f.photo_id
                   LEFT JOIN people p ON p.id = f.person_id
                   WHERE f.id = ?""",
                (face_id,)
            ).fetchone()
            if row:
                pid  = row['person_id']
                name = row['name'] or f'(unnamed #{pid})'
                key  = (pid, name)
                if key not in person_sims or sim > person_sims[key][0]:
                    person_sims[key] = (sim, row['file_path'])

    print(f"\nMatching people (similarity >= {THRESHOLD}):\n")
    for (pid, name), (best_sim, example) in sorted(
        person_sims.items(), key=lambda x: -x[1][0]
    ):
        paths = db.get_photos_for_person(dest_root, pid) if pid else []
        print(f"  {name}  (best match: {best_sim:.2f},  {len(paths)} photos)")
        for p in paths[:5]:
            print(f"    {p}")
        if len(paths) > 5:
            print(f"    ... and {len(paths) - 5} more")
        print()


def cmd_bursts_list(dest_root: Path):
    """Print all burst groups and their members."""
    groups = db.get_burst_groups(dest_root)

    if not groups:
        print("No burst groups found. Run --bursts first.")
        return

    print(f"\n{len(groups)} burst groups:\n")
    for gid, paths in groups.items():
        print(f"  [{gid}]  {len(paths)} shots")
        for p in paths:
            print(f"    {p}")
        print()


def cmd_export_bursts(dest_root: Path, export_root: Path):
    """
    Create a review folder containing one subfolder per burst group.
    Each subfolder is populated with hardlinks to the originals (zero extra
    disk space on NTFS).  Falls back to copies if hardlinks fail (e.g.
    cross-drive exports).

    The sharpest photo in each group is renamed with a  BEST__  prefix so it
    sorts to the top and is easy to spot in File Explorer.
    """
    import quality

    groups = db.get_burst_groups(dest_root)
    if not groups:
        print("No burst groups found. Run --bursts first.")
        return

    export_root.mkdir(parents=True, exist_ok=True)
    print(f"\n=== EXPORTING {len(groups)} BURST GROUPS ===")
    print(f"Destination: {export_root}\n")

    exported = 0
    for gid, paths in groups.items():
        # Score shots — best first
        scored = quality.score_group(paths)
        best_path = scored[0][0]

        # Build a readable folder name:  <date>_burst_<shortid>_<N>shots
        # Pull date from the parent folder of the first photo
        first_fp  = Path(paths[0])
        date_part = first_fp.parent.name          # e.g. "11-14-2025"
        folder_name = f"{date_part}_burst_{gid}_{len(paths)}shots"
        group_dir = export_root / folder_name
        group_dir.mkdir(exist_ok=True)

        for orig_path, score in scored:
            src  = Path(orig_path)
            stem = src.stem
            ext  = src.suffix
            prefix = "BEST__" if orig_path == best_path else ""
            dst  = group_dir / f"{prefix}{stem}{ext}"

            try:
                os.link(src, dst)           # hardlink — free on same drive
            except OSError:
                import shutil
                shutil.copy2(src, dst)      # fallback copy (cross-drive)

        print(f"  {folder_name}/  ({len(paths)} shots, best: {Path(best_path).name})")
        exported += 1

    print(f"\nDone. {exported} burst groups written to:\n  {export_root}")
    print("\nTip: browse in File Explorer — BEST__ photos sort to the top.")
    print("     Delete unwanted shots from the review folder; originals are untouched.")


def cmd_burst_html(dest_root: Path, html_path: Path):
    """
    Generate a self-contained HTML gallery of all burst groups.
    Thumbnails are embedded as base64 JPEG so the file is fully portable.
    Each thumbnail links to the full-size original.
    The suggested best shot is highlighted with a green border.
    """
    import base64
    import quality

    groups = db.get_burst_groups(dest_root)
    if not groups:
        print("No burst groups found. Run --bursts first.")
        return

    print(f"\n=== GENERATING BURST HTML GALLERY ===")
    print(f"Output: {html_path}")
    print(f"Groups: {len(groups)}\n")

    # Sort groups by date (extracted from first photo's parent folder name)
    def group_date_key(item):
        gid, paths = item
        return Path(paths[0]).parent.name
    sorted_groups = sorted(groups.items(), key=group_date_key)

    rows_html = []
    for i, (gid, paths) in enumerate(sorted_groups, 1):
        scored  = quality.score_group(paths)
        best    = scored[0][0]
        max_sc  = scored[0][1] if scored[0][1] > 0 else 1.0
        date    = Path(paths[0]).parent.name

        print(f"  [{i}/{len(sorted_groups)}] {date}  {len(paths)} shots", end='\r')

        cards = []
        for orig_path, score in scored:
            fp     = Path(orig_path)
            is_best = orig_path == best
            pct    = min(100, int(score / max_sc * 100))

            thumb_bytes = quality.make_thumbnail(fp)
            if thumb_bytes:
                b64 = base64.b64encode(thumb_bytes).decode('ascii')
                img_src = f"data:image/jpeg;base64,{b64}"
            else:
                img_src = ""

            border   = ' style="border:3px solid #22c55e;"' if is_best else ''
            best_tag = '<span class="best-tag">BEST</span>' if is_best else ''
            bar_col  = '#22c55e' if is_best else '#60a5fa'

            # Use forward slashes so file:// links work in browsers
            file_url = fp.as_uri()

            card = f"""
            <div class="card">
              <a href="{file_url}" target="_blank">
                <img src="{img_src}" alt="{fp.name}"{border}>
              </a>
              {best_tag}
              <div class="fname">{fp.name}</div>
              <div class="bar-wrap">
                <div class="bar" style="width:{pct}%;background:{bar_col}"></div>
              </div>
              <div class="score">sharpness {pct}%</div>
            </div>"""
            cards.append(card)

        rows_html.append(f"""
        <div class="group">
          <h2>{date} &nbsp;<span class="sub">{len(paths)} shots &nbsp;·&nbsp; group {gid}</span></h2>
          <div class="row">{''.join(cards)}
          </div>
        </div>""")

    print(f"\nRendering HTML...")

    total_groups = len(groups)
    total_photos = sum(len(p) for p in groups.values())

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Burst Review Gallery</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: system-ui, sans-serif; background: #111; color: #e5e5e5; padding: 24px; }}
  h1   {{ font-size: 1.4rem; margin-bottom: 4px; }}
  .meta{{ font-size: .85rem; color: #888; margin-bottom: 32px; }}
  .group {{ margin-bottom: 40px; }}
  h2   {{ font-size: 1rem; font-weight: 600; margin-bottom: 12px; color: #f0f0f0; }}
  .sub {{ font-weight: 400; color: #888; font-size: .85rem; }}
  .row {{ display: flex; flex-wrap: wrap; gap: 12px; }}
  .card{{ background: #1e1e1e; border-radius: 8px; padding: 8px;
          width: 200px; flex-shrink: 0; position: relative; }}
  .card img {{ width: 184px; height: 138px; object-fit: cover;
               border-radius: 4px; display: block; border: 3px solid transparent; }}
  .card a {{ display: block; }}
  .best-tag {{ position: absolute; top: 12px; left: 12px;
               background: #22c55e; color: #000; font-size: .7rem;
               font-weight: 700; padding: 2px 6px; border-radius: 4px; }}
  .fname{{ font-size: .72rem; color: #aaa; margin-top: 6px;
           white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
  .bar-wrap {{ background: #333; border-radius: 4px; height: 4px; margin-top: 6px; }}
  .bar {{ height: 4px; border-radius: 4px; }}
  .score{{ font-size: .68rem; color: #666; margin-top: 3px; }}
</style>
</head>
<body>
<h1>Burst Review Gallery</h1>
<p class="meta">{total_groups} burst groups &nbsp;·&nbsp; {total_photos} photos
  &nbsp;·&nbsp; click any thumbnail to open full-size &nbsp;·&nbsp;
  green border = sharpest shot</p>
{''.join(rows_html)}
</body>
</html>"""

    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(html, encoding='utf-8')
    print(f"Saved: {html_path}")
    print(f"\nOpen in your browser — click thumbnails to see full-size originals.")


def cmd_stats(dest_root: Path):
    """Display a summary of the analysis database."""
    s = db.get_stats(dest_root)
    print(f"\n=== LIBRARY ANALYSIS STATS ===")
    print(f"  Photos in DB     : {s['total_photos']}")
    print(f"  Face-processed   : {s['processed']}")
    print(f"  Faces detected   : {s['total_faces']}")
    print(f"  People (clusters): {s['total_people']}")
    print(f"  Named people     : {s['named_people']}")
    print(f"  Burst photos     : {s['burst_photos']}")
    print(f"  Burst groups     : {s['burst_groups']}")
    print(f"  Database file    : {db._db_path(dest_root)}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description='Photo library analyzer — face recognition & burst grouping',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Process new imports (faces + bursts in one pass):
  python analyze.py --dest "E:\\Camera Footage" --faces --bursts

  # Cluster detected faces into people:
  python analyze.py --dest "E:\\Camera Footage" --cluster

  # Interactively name the people:
  python analyze.py --dest "E:\\Camera Footage" --label

  # Find photos of a named person:
  python analyze.py --dest "E:\\Camera Footage" --find "Alice"

  # Find photos of whoever is in a reference image:
  python analyze.py --dest "E:\\Camera Footage" --find-similar "C:\\ref.jpg"

  # List all known people:
  python analyze.py --dest "E:\\Camera Footage" --people

  # List burst groups:
  python analyze.py --dest "E:\\Camera Footage" --bursts-list

  # Export burst groups to review subfolders (hardlinks, no extra disk space):
  python analyze.py --dest "E:\\Camera Footage" --export-bursts "E:\\burst_review"

  # Generate visual HTML gallery of all burst groups:
  python analyze.py --dest "E:\\Camera Footage" --burst-html "E:\\burst_review.html"

  # Show stats:
  python analyze.py --dest "E:\\Camera Footage" --stats
        """
    )

    parser.add_argument('--dest',         help='Destination library folder')
    parser.add_argument('--faces',        action='store_true', help='Detect and encode faces')
    parser.add_argument('--bursts',       action='store_true', help='Detect burst shot groups')
    parser.add_argument('--cluster',      action='store_true', help='Cluster faces into people')
    parser.add_argument('--eps',          type=float, default=0.3,
                        help='DBSCAN cosine-distance threshold (default 0.3; lower = stricter)')
    parser.add_argument('--min-samples',  type=int,   default=2,
                        help='Min faces to form a cluster, noise otherwise (default 2)')
    parser.add_argument('--hdbscan',      action='store_true',
                        help='Use HDBSCAN instead of DBSCAN (more robust, no chaining)')
    parser.add_argument('--label',        action='store_true', help='Interactively label people')
    parser.add_argument('--people',       action='store_true', help='List all known people')
    parser.add_argument('--find',         metavar='NAME',      help='Find photos of a named person')
    parser.add_argument('--find-similar', metavar='PATH',      help='Find people matching a reference photo')
    parser.add_argument('--bursts-list',   action='store_true', help='List all burst groups')
    parser.add_argument('--export-bursts', metavar='FOLDER',   help='Export burst groups as review subfolders (hardlinks)')
    parser.add_argument('--burst-html',    metavar='FILE',     help='Generate HTML visual gallery of burst groups')
    parser.add_argument('--stats',         action='store_true', help='Show database statistics')

    args = parser.parse_args()

    dest_root = _get_dest(args)

    if not dest_root.exists():
        print(f"Error: Destination folder does not exist: {dest_root}")
        sys.exit(1)

    # Initialise DB (no-op if already exists)
    db.init_db(dest_root)

    # Dispatch
    ran_something = False

    if args.faces or args.bursts:
        cmd_process(dest_root, do_faces=args.faces, do_bursts=args.bursts)
        ran_something = True

    if args.cluster:
        cmd_cluster(dest_root, eps=args.eps, min_samples=args.min_samples, use_hdbscan=args.hdbscan)
        ran_something = True

    if args.label:
        cmd_label(dest_root)
        ran_something = True

    if args.people:
        cmd_people(dest_root)
        ran_something = True

    if args.find:
        cmd_find(dest_root, args.find)
        ran_something = True

    if args.find_similar:
        cmd_find_similar(dest_root, args.find_similar)
        ran_something = True

    if args.bursts_list:
        cmd_bursts_list(dest_root)
        ran_something = True

    if args.export_bursts:
        cmd_export_bursts(dest_root, Path(args.export_bursts))
        ran_something = True

    if args.burst_html:
        cmd_burst_html(dest_root, Path(args.burst_html))
        ran_something = True

    if args.stats:
        cmd_stats(dest_root)
        ran_something = True

    if not ran_something:
        parser.print_help()


if __name__ == '__main__':
    main()
