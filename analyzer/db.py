"""
SQLite database for photo analysis results.
Stores face embeddings, people clusters, and burst group IDs.
Database file lives alongside import_log.json in the destination root.
"""

import sqlite3
import numpy as np
from pathlib import Path
from typing import Optional, List, Tuple, Dict

DB_FILE = 'photo_analysis.db'


def _db_path(dest_root: Path) -> Path:
    return dest_root / DB_FILE


def connect(dest_root: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(_db_path(dest_root)))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init_db(dest_root: Path):
    """Create tables if they don't exist."""
    with connect(dest_root) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS photos (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                file_path   TEXT    UNIQUE NOT NULL,
                capture_ts  REAL,           -- Unix timestamp from EXIF/mtime
                burst_group TEXT,           -- Hex ID shared by burst siblings
                faces_done  INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS faces (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                photo_id    INTEGER NOT NULL REFERENCES photos(id),
                face_idx    INTEGER NOT NULL,
                embedding   BLOB    NOT NULL,  -- 512 x float32
                person_id   INTEGER REFERENCES people(id),
                confidence  REAL,
                bbox_x1     REAL,
                bbox_y1     REAL,
                bbox_x2     REAL,
                bbox_y2     REAL
            );

            CREATE TABLE IF NOT EXISTS people (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT,
                cluster_id  INTEGER UNIQUE
            );

            CREATE INDEX IF NOT EXISTS idx_photos_path   ON photos(file_path);
            CREATE INDEX IF NOT EXISTS idx_faces_photo   ON faces(photo_id);
            CREATE INDEX IF NOT EXISTS idx_faces_person  ON faces(person_id);
            CREATE INDEX IF NOT EXISTS idx_photos_burst  ON photos(burst_group);
        """)


# ---------------------------------------------------------------------------
# Photos
# ---------------------------------------------------------------------------

def upsert_photo(dest_root: Path, file_path: str, capture_ts: Optional[float]):
    """Add photo to DB if not already present. Never overwrites existing rows."""
    with connect(dest_root) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO photos (file_path, capture_ts) VALUES (?, ?)",
            (file_path, capture_ts)
        )


def get_unprocessed_photos(dest_root: Path) -> List[str]:
    """Return file paths of photos not yet processed for faces."""
    with connect(dest_root) as conn:
        rows = conn.execute(
            "SELECT file_path FROM photos WHERE faces_done = 0 ORDER BY capture_ts"
        ).fetchall()
    return [r['file_path'] for r in rows]


def get_all_photos_with_ts(dest_root: Path) -> List[Tuple[str, Optional[float]]]:
    """Return all (file_path, capture_ts) pairs for burst detection."""
    with connect(dest_root) as conn:
        rows = conn.execute(
            "SELECT file_path, capture_ts FROM photos ORDER BY capture_ts"
        ).fetchall()
    return [(r['file_path'], r['capture_ts']) for r in rows]


def set_burst_groups(dest_root: Path, groups: Dict[str, str]):
    """Apply burst group IDs. groups = {file_path: group_id}."""
    with connect(dest_root) as conn:
        for file_path, group_id in groups.items():
            conn.execute(
                "UPDATE photos SET burst_group = ? WHERE file_path = ?",
                (group_id, file_path)
            )


# ---------------------------------------------------------------------------
# Faces
# ---------------------------------------------------------------------------

def embedding_to_blob(arr: np.ndarray) -> bytes:
    return arr.astype(np.float32).tobytes()


def blob_to_embedding(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32).copy()


def save_faces(
    dest_root: Path,
    file_path: str,
    embeddings: List[np.ndarray],
    bboxes: List[Tuple[float, float, float, float]],
    confidences: List[float],
):
    """Persist detected faces for a photo and mark it as processed."""
    with connect(dest_root) as conn:
        row = conn.execute(
            "SELECT id FROM photos WHERE file_path = ?", (file_path,)
        ).fetchone()
        if row is None:
            return
        photo_id = row['id']

        conn.execute("DELETE FROM faces WHERE photo_id = ?", (photo_id,))

        for i, (emb, bbox, conf) in enumerate(zip(embeddings, bboxes, confidences)):
            x1, y1, x2, y2 = bbox
            conn.execute(
                """INSERT INTO faces
                   (photo_id, face_idx, embedding, confidence, bbox_x1, bbox_y1, bbox_x2, bbox_y2)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (photo_id, i, embedding_to_blob(emb), conf, x1, y1, x2, y2)
            )

        conn.execute("UPDATE photos SET faces_done = 1 WHERE id = ?", (photo_id,))


def load_all_embeddings(dest_root: Path) -> Tuple[np.ndarray, List[int]]:
    """
    Load every face embedding in the DB.
    Returns (matrix of shape [N, 512], list of face row IDs).
    """
    with connect(dest_root) as conn:
        rows = conn.execute("SELECT id, embedding FROM faces").fetchall()

    if not rows:
        return np.empty((0, 512), dtype=np.float32), []

    face_ids = [r['id'] for r in rows]
    embeddings = np.stack([blob_to_embedding(r['embedding']) for r in rows])
    return embeddings, face_ids


# ---------------------------------------------------------------------------
# Clustering & people
# ---------------------------------------------------------------------------

def save_clusters(dest_root: Path, face_ids: List[int], labels: List[int]):
    """
    Write DBSCAN cluster labels back to the faces table.
    Creates a people row for each unique cluster (label >= 0).
    Faces with label == -1 (noise) get person_id = NULL.
    """
    with connect(dest_root) as conn:
        unique = set(l for l in labels if l >= 0)

        for cluster_id in unique:
            conn.execute(
                "INSERT OR IGNORE INTO people (cluster_id) VALUES (?)",
                (cluster_id,)
            )

        person_map: Dict[int, int] = {
            r['cluster_id']: r['id']
            for r in conn.execute("SELECT id, cluster_id FROM people").fetchall()
            if r['cluster_id'] is not None
        }

        for face_id, label in zip(face_ids, labels):
            person_id = person_map.get(label) if label >= 0 else None
            conn.execute(
                "UPDATE faces SET person_id = ? WHERE id = ?",
                (person_id, face_id)
            )


def get_people(dest_root: Path) -> List[Dict]:
    """Return all people with their photo counts, sorted by most photos."""
    with connect(dest_root) as conn:
        rows = conn.execute("""
            SELECT p.id, p.name, p.cluster_id,
                   COUNT(DISTINCT ph.id) AS photo_count
            FROM   people p
            JOIN   faces  f  ON f.person_id = p.id
            JOIN   photos ph ON ph.id = f.photo_id
            GROUP  BY p.id
            ORDER  BY photo_count DESC
        """).fetchall()
    return [dict(r) for r in rows]


def label_person(dest_root: Path, person_id: int, name: str):
    """Assign a name to a person cluster."""
    with connect(dest_root) as conn:
        conn.execute("UPDATE people SET name = ? WHERE id = ?", (name, person_id))


def get_person_by_name(dest_root: Path, name: str) -> Optional[Dict]:
    """Look up a person by name (case-insensitive)."""
    with connect(dest_root) as conn:
        row = conn.execute(
            "SELECT * FROM people WHERE LOWER(name) = LOWER(?)", (name,)
        ).fetchone()
    return dict(row) if row else None


def get_photos_for_person(dest_root: Path, person_id: int) -> List[str]:
    """Return all file paths containing a given person, sorted by timestamp."""
    with connect(dest_root) as conn:
        rows = conn.execute("""
            SELECT DISTINCT ph.file_path
            FROM   photos ph
            JOIN   faces  f  ON f.photo_id = ph.id
            WHERE  f.person_id = ?
            ORDER  BY ph.capture_ts
        """, (person_id,)).fetchall()
    return [r['file_path'] for r in rows]


def merge_people(dest_root: Path, keep_id: int, merge_ids: List[int]):
    """
    Merge several person clusters into one.
    All faces from merge_ids are reassigned to keep_id; merged rows are deleted.
    """
    with connect(dest_root) as conn:
        for mid in merge_ids:
            conn.execute(
                "UPDATE faces SET person_id = ? WHERE person_id = ?",
                (keep_id, mid)
            )
            conn.execute("DELETE FROM people WHERE id = ?", (mid,))


def get_faces_for_person(dest_root: Path, person_id: int) -> List[np.ndarray]:
    """Return all embeddings for a person (used for similarity search)."""
    with connect(dest_root) as conn:
        rows = conn.execute(
            "SELECT embedding FROM faces WHERE person_id = ?", (person_id,)
        ).fetchall()
    return [blob_to_embedding(r['embedding']) for r in rows]


def get_burst_groups(dest_root: Path) -> Dict[str, List[str]]:
    """Return {burst_group_id: [file_path, ...]} for all burst groups."""
    with connect(dest_root) as conn:
        rows = conn.execute("""
            SELECT burst_group, file_path
            FROM   photos
            WHERE  burst_group IS NOT NULL
            ORDER  BY burst_group, capture_ts
        """).fetchall()

    groups: Dict[str, List[str]] = {}
    for row in rows:
        groups.setdefault(row['burst_group'], []).append(row['file_path'])
    return groups


def get_stats(dest_root: Path) -> Dict:
    """Return summary statistics from the DB."""
    with connect(dest_root) as conn:
        total_photos = conn.execute("SELECT COUNT(*) FROM photos").fetchone()[0]
        processed    = conn.execute("SELECT COUNT(*) FROM photos WHERE faces_done = 1").fetchone()[0]
        total_faces  = conn.execute("SELECT COUNT(*) FROM faces").fetchone()[0]
        total_people = conn.execute("SELECT COUNT(*) FROM people").fetchone()[0]
        named_people = conn.execute("SELECT COUNT(*) FROM people WHERE name IS NOT NULL").fetchone()[0]
        burst_photos = conn.execute("SELECT COUNT(*) FROM photos WHERE burst_group IS NOT NULL").fetchone()[0]
        burst_groups = conn.execute("SELECT COUNT(DISTINCT burst_group) FROM photos WHERE burst_group IS NOT NULL").fetchone()[0]

    return {
        'total_photos':  total_photos,
        'processed':     processed,
        'total_faces':   total_faces,
        'total_people':  total_people,
        'named_people':  named_people,
        'burst_photos':  burst_photos,
        'burst_groups':  burst_groups,
    }
