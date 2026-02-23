"""
Burst shot detection.

A burst group is a run of photos where every consecutive pair satisfies
at least one of:
  - Timestamp gap <= BURST_GAP_SECONDS  (rapid capture / burst mode)
  - Filename sequence numbers are consecutive (e.g. IMG_1234 → IMG_1235)

Groups smaller than MIN_BURST_SIZE photos are discarded.
"""

import re
import hashlib
from pathlib import Path
from typing import Dict, List, Optional, Tuple

BURST_GAP_SECONDS = 3.0   # photos within this gap are "in the same burst"
MIN_BURST_SIZE    = 2      # lone photos are not a burst


def _seq_number(filename: str) -> Optional[int]:
    """
    Extract trailing integer from a filename stem.
    IMG_1234.JPG → 1234,  DSC09876.ARW → 9876,  random.jpg → None
    """
    stem = Path(filename).stem
    m = re.search(r'(\d+)$', stem)
    return int(m.group(1)) if m else None


def _group_id(first_path: str) -> str:
    """Stable 12-char hex ID for a burst group, derived from the first photo."""
    return hashlib.md5(first_path.encode()).hexdigest()[:12]


def detect_bursts(
    photos: List[Tuple[str, Optional[float]]]
) -> Dict[str, str]:
    """
    Identify burst groups from a list of (file_path, capture_ts) pairs.

    capture_ts is a Unix timestamp (float) or None.
    Photos without timestamps are sorted to the end and skipped for
    time-based grouping, but can still join a sequence-number group.

    Returns:
        {file_path: burst_group_id}  — only photos that belong to a burst.
        Solo photos are excluded from the returned dict.
    """
    if not photos:
        return {}

    # Sort: photos with timestamps first (by time), then by filename
    def sort_key(item: Tuple[str, Optional[float]]):
        path, ts = item
        return (ts is None, ts or 0.0, Path(path).name)

    sorted_photos = sorted(photos, key=sort_key)

    # Walk through sorted list and assign burst groups
    # Each "current_group" is a list of file paths
    groups: List[List[str]] = []
    current_group: List[str] = []

    prev_ts:  Optional[float] = None
    prev_num: Optional[int]   = None

    for file_path, ts in sorted_photos:
        seq_num = _seq_number(Path(file_path).name)

        if not current_group:
            current_group.append(file_path)
        else:
            time_ok = (
                ts is not None
                and prev_ts is not None
                and (ts - prev_ts) <= BURST_GAP_SECONDS
            )
            seq_ok = (
                seq_num is not None
                and prev_num is not None
                and seq_num == prev_num + 1
            )

            if time_ok or seq_ok:
                current_group.append(file_path)
            else:
                if len(current_group) >= MIN_BURST_SIZE:
                    groups.append(current_group)
                current_group = [file_path]

        prev_ts  = ts
        prev_num = seq_num

    # Flush the last group
    if len(current_group) >= MIN_BURST_SIZE:
        groups.append(current_group)

    # Build result mapping
    result: Dict[str, str] = {}
    for group in groups:
        gid = _group_id(group[0])
        for path in group:
            result[path] = gid

    return result


def summarise_bursts(groups: Dict[str, str]) -> Dict[str, List[str]]:
    """
    Invert the burst map into {group_id: [file_path, ...]}.
    Useful for display / inspection.
    """
    inverted: Dict[str, List[str]] = {}
    for path, gid in groups.items():
        inverted.setdefault(gid, []).append(path)
    return inverted
