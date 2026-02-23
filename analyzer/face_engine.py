"""
GPU-accelerated face detection and encoding.

Pipeline per image:
  1. Load  — Pillow for JPEG/PNG/HEIC, rawpy for RAW (CR2/CR3/NEF/ARW/etc.)
  2. Resize — longest side capped at MAX_SIDE (1920px) before GPU work
  3. Detect — MTCNN finds face bounding boxes + landmarks
  4. Encode — InceptionResnetV1 (VGGFace2) produces 512-d embeddings

Falls back to CPU automatically if CUDA is unavailable.
"""

import sys
import numpy as np
from pathlib import Path
from typing import List, Tuple, Optional

from PIL import Image

try:
    import torch
    from facenet_pytorch import MTCNN, InceptionResnetV1
    HAS_FACENET = True
except ImportError:
    HAS_FACENET = False

try:
    import rawpy
    HAS_RAWPY = True
except ImportError:
    HAS_RAWPY = False

# File extensions handled by rawpy instead of Pillow
RAW_EXTENSIONS = {'.cr2', '.cr3', '.nef', '.arw', '.dng', '.orf', '.rw2', '.raw'}

# Resize images to this before sending to GPU — saves VRAM, no accuracy loss
MAX_SIDE = 1920


def check_dependencies():
    """Print helpful error and exit if required packages are missing."""
    missing = []
    if not HAS_FACENET:
        missing.append("facenet-pytorch  (pip install facenet-pytorch torch torchvision)")
    if not HAS_RAWPY:
        print("Warning: rawpy not installed — RAW files will be skipped.")
        print("         Install with: pip install rawpy")
    if missing:
        print("ERROR: Missing required packages:")
        for m in missing:
            print(f"  {m}")
        sys.exit(1)


class FaceEngine:
    """
    Wraps MTCNN + InceptionResnetV1 for face detection and encoding.
    Instantiate once and reuse across many images.
    """

    def __init__(self):
        check_dependencies()

        self.device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')

        if self.device.type == 'cuda':
            name = torch.cuda.get_device_name(0)
            vram = torch.cuda.get_device_properties(0).total_memory / 1024 ** 3
            print(f"  GPU : {name} ({vram:.1f} GB VRAM)")
        else:
            print("  Device: CPU  (install CUDA-enabled PyTorch for GPU acceleration)")

        # MTCNN — face detector
        # thresholds are P-Net / R-Net / O-Net confidence cutoffs
        self.mtcnn = MTCNN(
            keep_all=True,
            device=self.device,
            min_face_size=20,
            thresholds=[0.6, 0.7, 0.7],
            post_process=True,
            select_largest=False,
        )

        # InceptionResnetV1 — face encoder (pretrained on VGGFace2)
        print("  Loading FaceNet model (downloads ~90 MB on first run)...")
        self.resnet = InceptionResnetV1(pretrained='vggface2').eval().to(self.device)
        print("  Model ready.")

    # ------------------------------------------------------------------
    # Image loading
    # ------------------------------------------------------------------

    def load_image(self, filepath: Path) -> Optional[Image.Image]:
        """
        Load an image as an RGB PIL Image.
        Handles both standard formats (Pillow) and camera RAW (rawpy).
        Returns None on any error.
        """
        ext = filepath.suffix.lower()
        try:
            if ext in RAW_EXTENSIONS:
                if not HAS_RAWPY:
                    return None
                with rawpy.imread(str(filepath)) as raw:
                    # half_size=True gives 2x faster decode with no face-detection penalty
                    rgb = raw.postprocess(
                        use_camera_wb=True,
                        half_size=True,
                        no_auto_bright=False,
                        output_bps=8,
                    )
                return Image.fromarray(rgb)
            else:
                return Image.open(filepath).convert('RGB')
        except Exception:
            return None

    def _resize(self, img: Image.Image) -> Image.Image:
        w, h = img.size
        longest = max(w, h)
        if longest > MAX_SIDE:
            scale = MAX_SIDE / longest
            img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
        return img

    # ------------------------------------------------------------------
    # Face processing
    # ------------------------------------------------------------------

    def process(
        self, filepath: Path
    ) -> Tuple[
        List[np.ndarray],                        # embeddings  [N x 512]
        List[Tuple[float, float, float, float]],  # bboxes      [N x (x1,y1,x2,y2)]
        List[float],                             # confidences [N]
    ]:
        """
        Detect and encode all faces in one image file.
        Returns three parallel lists; all empty if no faces or on error.
        """
        img = self.load_image(filepath)
        if img is None:
            return [], [], []

        img = self._resize(img)

        # --- Detection pass: get boxes + confidence scores ---
        try:
            boxes, probs = self.mtcnn.detect(img)
        except Exception:
            return [], [], []

        if boxes is None or len(boxes) == 0:
            return [], [], []

        # --- Alignment pass: get face crops as tensors for the encoder ---
        try:
            face_tensors = self.mtcnn(img)
        except Exception:
            return [], [], []

        if face_tensors is None:
            return [], [], []

        # Normalise to batch dimension (single face → 3D, multiple → 4D)
        if face_tensors.dim() == 3:
            face_tensors = face_tensors.unsqueeze(0)

        face_tensors = face_tensors.to(self.device)

        # --- Encoding pass ---
        try:
            with torch.no_grad():
                embeddings = self.resnet(face_tensors).cpu().numpy()
        except Exception:
            return [], [], []

        # Align lengths (detect and __call__ are deterministic on the same image,
        # but guard against any edge-case mismatch)
        n = min(len(embeddings), len(boxes))

        return (
            [embeddings[i] for i in range(n)],
            [tuple(float(v) for v in boxes[i]) for i in range(n)],
            [float(probs[i]) if probs[i] is not None else 0.0 for i in range(n)],
        )

    def encode_single(self, img: Image.Image) -> Optional[np.ndarray]:
        """
        Encode the largest (or only) face in a PIL Image.
        Used for reference-photo similarity search.
        Returns a 512-d embedding, or None if no face is found.
        """
        img = self._resize(img)
        try:
            face_tensor = self.mtcnn(img)  # returns single crop for largest face
        except Exception:
            return None

        if face_tensor is None:
            return None

        if face_tensor.dim() == 3:
            face_tensor = face_tensor.unsqueeze(0)

        face_tensor = face_tensor.to(self.device)
        with torch.no_grad():
            emb = self.resnet(face_tensor).cpu().numpy()[0]
        return emb
