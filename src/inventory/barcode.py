"""Local barcode decoding via pyzbar (requires libzbar on the host)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import BinaryIO

logger = logging.getLogger(__name__)


@dataclass
class DecodedBarcode:
    data: str
    type: str
    quality: int | None = None


def _load_image(source: str | Path | bytes | BinaryIO):
    from PIL import Image

    if isinstance(source, (str, Path)):
        return Image.open(source)
    if isinstance(source, bytes):
        return Image.open(BytesIO(source))
    return Image.open(source)


def decode_image(source: str | Path | bytes | BinaryIO) -> list[DecodedBarcode]:
    """
    Decode all barcodes found in an image.

    Returns an empty list if none found or if pyzbar/libzbar is unavailable.
    """
    try:
        from pyzbar.pyzbar import decode as zbar_decode
    except ImportError as e:
        logger.error(
            "pyzbar not available (%s). Install libzbar0t64 + pip install pyzbar Pillow",
            e,
        )
        return []

    try:
        image = _load_image(source)
        results = zbar_decode(image)
    except Exception:
        logger.exception("Barcode decode failed")
        return []

    out: list[DecodedBarcode] = []
    for r in results:
        try:
            data = r.data.decode("utf-8", errors="replace").strip()
        except Exception:
            data = str(r.data)
        if not data:
            continue
        out.append(
            DecodedBarcode(
                data=data,
                type=getattr(r, "type", "") or "",
                quality=getattr(r, "quality", None),
            )
        )
    return out


def decode_first(source: str | Path | bytes | BinaryIO) -> str | None:
    """Return the first barcode string, or None."""
    codes = decode_image(source)
    return codes[0].data if codes else None
