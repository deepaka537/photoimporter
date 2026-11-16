# Continuation Prompt — Photo Library Web GUI

Paste everything below this line into a new chat to continue the project.

---

## Project: Photo Library Web GUI

I have a Python photo importer and analyzer project at `c:\Users\4akas\Documents\Python Scripts\photoimporter\`. Read all relevant files before starting work.

### What exists

**`import_media.py`** — imports photos from SD cards, organizes into `M-D-YYYY` date folders, tracks duplicates via `import_log.json`. Run via `python run_importer.py`.

**`analyzer/` subfolder:**

| File | Purpose |
|---|---|
| `db.py` | SQLite (`photo_analysis.db`) — tables: `photos`, `faces` (512-d embeddings), `people` (clusters) |
| `face_engine.py` | MTCNN + InceptionResnetV1 (facenet-pytorch), GPU via CUDA, handles JPEG + RAW (rawpy) |
| `burst_detector.py` | Groups burst shots by timestamp gap (≤3 s) or sequential filenames |
| `quality.py` | Laplacian variance sharpness scoring; thumbnail generation via Pillow + rawpy |
| `analyze.py` | CLI: `--faces`, `--bursts`, `--cluster`, `--label`, `--find`, `--find-similar`, `--export-bursts`, `--burst-html`, `--stats` |
| `requirements.txt` | facenet-pytorch, torch, torchvision, rawpy, Pillow, scikit-learn, imagehash, numpy |

**Library folder structure on disk:**
```
E:\Camera Footage\
├── import_log.json
├── photo_analysis.db
├── .thumbnails\          ← to be created by the server
├── 11-14-2025\
│   ├── IMG_1234.JPG
│   └── IMG_1234.CR2
└── 2-3-2024\
    └── DSC_9876.ARW
```

---

### What to build next

A local Flask web app — **`analyzer/server.py`** + **`analyzer/templates/index.html`** + **`analyzer/static/app.js`** + **`analyzer/static/style.css`**.

Entry point: `python analyzer/server.py --dest "E:\Camera Footage"` — opens `http://localhost:5000` in the browser automatically.

Add `flask` to `analyzer/requirements.txt`.

---

### Design requirements

**SSD-friendly lazy loading:**
- On startup: only scan folder names (fast `os.scandir`, no file reads)
- Clicking a date folder loads only that folder's photo list
- Thumbnails load individually via `<img src="/thumb?p=...">` with `loading="lazy"` — browser fetches them one by one as they scroll into view

**GPU-accelerated thumbnail generation:**
- `ThumbnailCache` class in `server.py`
- Cache dir: `<dest_root>/.thumbnails/`, key: `MD5(abs_path + size).jpg`
- JPEG path: `torchvision.io.decode_jpeg` → tensor to CUDA → `F.interpolate` → CPU → `torchvision.io.encode_jpeg` (~3–5× faster than Pillow)
- RAW path: `rawpy` decode (CPU, unavoidable) → PIL → resize → encode
- Falls back to Pillow if CUDA unavailable
- Two sizes: `sm` = 320×240 (grid thumbnails), `lg` = 800×600 (viewer sidebar)
- **Background pre-generation thread** starts on server launch, crawls library and pre-generates all thumbnails using GPU so clicking folders feels instant

---

### API routes

| Route | Returns |
|---|---|
| `GET /` | `index.html` SPA shell |
| `GET /api/folders` | `[{name, count, year}]` — dir scan only, no file reads |
| `GET /api/folder/<name>` | `[{path, thumb_url, date, burst_group}]` |
| `GET /api/people` | `[{id, name, count, sample_thumbs}]` |
| `GET /api/person/<id>` | `[{path, thumb_url, date}]` |
| `POST /api/person/<id>/rename` | `{ok}` — calls `db.label_person()` |
| `GET /api/bursts` | `[{id, date, count, shots:[{path,thumb_url,sharpness_pct,is_best}]}]` |
| `GET /api/photo/info?p=<path>` | `{date, exif, faces, burst_group}` |
| `GET /api/precache_progress` | `{done, total}` — for subtle UI progress indicator |
| `GET /thumb?p=<abs_path>&s=<sm\|lg>` | JPEG thumbnail (generate + cache on demand) |
| `GET /photo?p=<abs_path>` | Original file (full-size) |
| `GET /api/stats` | Stats dict from `db.get_stats()` |

**Security:** Every `/thumb` and `/photo` request must validate the path starts with `dest_root` before serving.

---

### Four frontend views (vanilla JS, no framework, hash-based routing)

**`#timeline` — main view**
- Sidebar: date folders grouped by year with photo count chips
- Click folder → responsive CSS grid (4–6 col) of thumbnails with `loading="lazy"`
- Click photo → full-screen viewer modal

**`#people`**
- Grid of person cards: 3 sample thumbnails + name + photo count
- Unnamed people shown as "Person #N"
- Click person → their photo grid slides in
- Double-click name → inline rename → POST to `/api/person/<id>/rename`
- Shows "Run `--faces` then `--cluster` first" banner if DB has no people

**`#bursts`**
- Accordion list of burst groups sorted by date
- Expanded: horizontal scroll of thumbnails; BEST badge + green border on sharpest; sharpness bar under each shot
- Click shot → full-screen viewer

**Full-screen viewer (native `<dialog>` element)**
- Full-size photo + filename, date, camera model from EXIF
- Prev / Next navigation within current context (folder / person / burst)
- Shows burst group indicator if applicable
- Shows face count detected

---

### Modules to reuse (do not duplicate their logic)

From `db.py`: `connect()`, `get_people()`, `get_photos_for_person()`, `get_burst_groups()`, `label_person()`, `get_stats()`

From `quality.py`: `make_thumbnail()` as Pillow fallback in `ThumbnailCache`; `score_group()` for burst sharpness ranking

From `analyze.py`: `_get_capture_ts()` for EXIF info; `_scan_library()` for folder scanning

---

### Hardware

- Windows 11, Nvidia RTX 3050 4GB VRAM (laptop variant)
- Python 3.x, all `analyzer/requirements.txt` packages already installed
- Library paths use Windows-style (e.g. `E:\Camera Footage`)
