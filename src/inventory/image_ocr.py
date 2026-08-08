"""Optional OCR adapter for photographed/scanned delivery notes."""

from __future__ import annotations

from pathlib import Path

from src.inventory.domain import InventoryDomainError


IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}


class DeliveryImageOcr:
    """Extract delivery-note text from an image using local Tesseract OCR.

    The Python package is optional and the Tesseract executable must be installed
    on the host. Keeping OCR behind this adapter means the receiving workflow is
    unchanged if a different local vision/OCR engine is used later.
    """

    def extract_text(self, path: Path) -> str:
        if path.suffix.lower() not in IMAGE_EXTENSIONS:
            raise InventoryDomainError("Unsupported delivery image format.")
        try:
            import pytesseract
            from PIL import Image, ImageOps
        except ImportError as exc:
            raise InventoryDomainError(
                "Photo OCR is not installed. Install the project OCR extra and Tesseract on the host."
            ) from exc

        try:
            with Image.open(path) as image:
                prepared = ImageOps.grayscale(image)
                prepared = ImageOps.autocontrast(prepared)
                text = pytesseract.image_to_string(prepared, config="--psm 6")
        except Exception as exc:
            raise InventoryDomainError(f"Could not OCR delivery image: {exc}") from exc

        text = (text or "").strip()
        if not text:
            raise InventoryDomainError("No readable text was detected in the delivery image.")
        return text
