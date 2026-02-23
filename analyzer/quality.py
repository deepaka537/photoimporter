"""
Photo quality scoring for burst shot comparison.

Uses Laplacian variance to measure sharpness:
  - High variance  = sharp, in-focus image
  - Low variance   = blurry or out-of-focus image

Only requires Pillow + numpy (no OpenCV dependency).
Images are downscaled to 800px before scoring for speed.
"""

import numpy as np
from pathlib import Path
from typing import List, Tuple, Optional
from PIL import Image

try:
    import rawpy
    HAS_RAWPY = True
except ImportError:
    HAS_RAWPY = False

RAW_EXTENSIONS = {'.cr2', '.cr3', '.nef', '.arw', '.dng', '.orf', '.rw2', '.raw'}
SCORE_SIZE = 800   # Resize longest side to this before scoring


def _load_for_scoring(filepath: Path) -> Optional[np.ndarray]:
    """
    Load image as a grayscale numpy array, resized to SCORE_SIZE.
    Returns None on error.
    """
    ext = filepath.suffix.lower()
    try:
        if ext in RAW_EXTENSIONS:
            if not HAS_RAWPY:
                return None
            with rawpy.imread(str(filepath)) as raw:
                rgb = raw.postprocess(
                    use_camera_wb=True,
                    half_size=True,
                    output_bps=8,
                )
            img = Image.fromarray(rgb).convert('L')
        else:
            img = Image.open(filepath).convert('L')
    except Exception:
        return None

    w, h = img.size
    longest = max(w, h)
    if longest > SCORE_SIZE:
        scale = SCORE_SIZE / longest
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)

    return np.array(img, dtype=np.float32)


def sharpness(filepath: Path) -> float:
    """
    Return a sharpness score for a photo (higher = sharper).
    Returns 0.0 on load failure.
    """
    arr = _load_for_scoring(filepath)
    if arr is None:
        return 0.0

    # Laplacian via finite differences (no scipy/cv2 needed)
    lap = (
        -arr[:-2, 1:-1]
        - arr[2:,  1:-1]
        - arr[1:-1, :-2]
        - arr[1:-1, 2:]
        + 4.0 * arr[1:-1, 1:-1]
    )
    return float(np.var(lap))


def score_group(paths: List[str]) -> List[Tuple[str, float]]:
    """
    Score every photo in a burst group.
    Returns [(file_path, score), ...] sorted best-first.
    """
    scored = [(p, sharpness(Path(p))) for p in paths]
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored


def make_thumbnail(filepath: Path, size: Tuple[int, int] = (240, 180)) -> Optional[bytes]:
    """
    Return a JPEG thumbnail as bytes (for embedding in HTML).
    Returns None on error.
    """
    ext = filepath.suffix.lower()
    try:
        if ext in RAW_EXTENSIONS:
            if not HAS_RAWPY:
                return None
            with rawpy.imread(str(filepath)) as raw:
                rgb = raw.postprocess(
                    use_camera_wb=True,
                    half_size=True,
                    output_bps=8,
                )
            img = Image.fromarray(rgb)
        else:
            img = Image.open(filepath)

        img.thumbnail(size, Image.LANCZOS)
        img = img.convert('RGB')

        import io
        buf = io.BytesIO()
        img.save(buf, format='JPEG', quality=70, optimize=True)
        return buf.getvalue()

    except Exception:
        return None
