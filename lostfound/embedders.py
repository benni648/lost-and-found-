"""Embedding backends behind one tiny interface.

The demo runs on CPU with no model downloads, so the defaults are:
  * text  -> hashed character n-grams (strong on short, specific reports)
  * image -> HSV histogram + spatial colour/shape grid

Both are **classical feature extractors, not deep models**. That is a
deliberate constraint: nothing here loads a pretrained network, downloads
weights or takes a gradient, so a CPU-only checkout, CI and a laptop demo all
exercise the same code path.

The image vector separates instances by colour and silhouette, which is the
information the scorer actually consumes (image cosine plus a CIE76 colour
difference), so a learned embedding of the same photos adds no new evidence.

A different classical encoder plugs in by implementing the two-method
interface below and registering itself in ``_TEXT`` / ``_IMAGE``.
"""

from __future__ import annotations

import zlib
from typing import Protocol

import numpy as np
from PIL import Image, ImageFilter

from .textnorm import tokenize


class TextEmbedder(Protocol):
    name: str
    dim: int

    def encode(self, text: str) -> np.ndarray: ...


class ImageEmbedder(Protocol):
    name: str
    dim: int

    def encode(self, image: Image.Image) -> np.ndarray: ...


def _l2(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v))
    return v / n if n > 0 else v


# --------------------------------------------------------------------- text
class HashingTextEmbedder:
    """Character n-gram hashing: robust to typos and word order, no downloads."""

    name = "hash-char-ngram-v1"

    def __init__(self, dim: int = 512) -> None:
        self.dim = dim

    def encode(self, text: str) -> np.ndarray:
        vec = np.zeros(self.dim, dtype=np.float32)
        toks = tokenize(text)
        for tok in toks:
            self._add(vec, f"w:{tok}", 1.0)
            padded = f"<{tok}>"
            for n, w in ((3, 0.35), (4, 0.4), (5, 0.3)):
                for i in range(len(padded) - n + 1):
                    self._add(vec, f"g{n}:{padded[i : i + n]}", w)
        # signed hashing can push buckets negative, so sublinear-scale the
        # magnitude and keep the sign (log1p of a negative < -1 would be NaN)
        return _l2(np.sign(vec) * np.log1p(np.abs(vec)))

    def _add(self, vec: np.ndarray, key: str, weight: float) -> None:
        h = zlib.crc32(key.encode("utf-8"))
        idx = h % self.dim
        sign = 1.0 if (h >> 16) & 1 else -1.0
        vec[idx] += sign * weight


# -------------------------------------------------------------------- image
class HistGridImageEmbedder:
    """HSV histogram + 4x4 colour grid + 4x4 edge density."""

    name = "hist-grid-v1"
    dim = 12 * 4 * 4 + 16 * 3 + 16

    def __init__(self, work: int = 96) -> None:
        self.work = work

    def encode(self, image: Image.Image) -> np.ndarray:
        img = image.convert("RGB").resize((self.work, self.work), Image.Resampling.BILINEAR)
        hsv = np.asarray(img.convert("HSV"), dtype=np.uint16)
        h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
        idx = (h * 12 // 256) * 16 + (s * 4 // 256) * 4 + (v * 4 // 256)
        hist = np.bincount(idx.ravel(), minlength=12 * 4 * 4).astype(np.float32)
        hist /= max(1.0, hist.sum())

        rgb = np.asarray(img, dtype=np.float32) / 255.0
        cells = rgb.reshape(4, self.work // 4, 4, self.work // 4, 3).mean(axis=(1, 3)).ravel()

        edges = img.convert("L").filter(ImageFilter.FIND_EDGES)
        edge_arr = np.asarray(edges, dtype=np.float32) / 255.0
        edge_cells = edge_arr.reshape(4, self.work // 4, 4, self.work // 4).mean(axis=(1, 3)).ravel()

        return _l2(np.concatenate([hist, cells, edge_cells * 2.0]).astype(np.float32))


# ----------------------------------------------------------------- registry
_TEXT = {"hash-char-ngram-v1": HashingTextEmbedder}
_IMAGE = {"hist-grid-v1": HistGridImageEmbedder}


def text_embedder(name: str) -> TextEmbedder:
    if name not in _TEXT:
        raise KeyError(f"unknown text embedder {name!r}; have {sorted(_TEXT)}")
    return _TEXT[name]()


def image_embedder(name: str) -> ImageEmbedder:
    if name not in _IMAGE:
        raise KeyError(f"unknown image embedder {name!r}; have {sorted(_IMAGE)}")
    return _IMAGE[name]()
