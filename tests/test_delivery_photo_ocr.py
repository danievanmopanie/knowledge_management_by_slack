from pathlib import Path

from src.inventory.assisted_receiving import DeliveryDocumentParser


class FakeOcr:
    def extract_text(self, path: Path) -> str:
        assert path.name == "delivery.jpg"
        return "sku    quantity    po_line_id\nMOUSE-01    4    L1\nKB-01    2    L2"


def test_image_ocr_text_is_normalised_into_delivery_lines(tmp_path):
    image = tmp_path / "delivery.jpg"
    image.write_bytes(b"not-a-real-image")
    parser = DeliveryDocumentParser(image_ocr=FakeOcr())

    lines = parser.parse(image)

    assert [(line.sku, line.quantity_delivered, line.po_line_id) for line in lines] == [
        ("MOUSE-01", 4, "L1"),
        ("KB-01", 2, "L2"),
    ]
