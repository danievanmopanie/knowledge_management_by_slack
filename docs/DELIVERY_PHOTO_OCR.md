# Delivery Photo OCR

Delivery-note photos can use the same staged receiving flow as CSV/PDF/DOCX delivery documents.

## Runtime setup

Install the Python OCR extra:

```bash
pip install -e '.[ocr]'
```

Install the Tesseract executable on the host separately. The Python package does not bundle the OCR engine.

## Safety model

OCR never writes stock directly. A photo is processed as:

`photo → OCR text → structured delivery lines → PO reconciliation → preview → explicit confirmation → atomic receipt`

If OCR is missing, unreadable, or produces a table that cannot be interpreted, the receipt is rejected before staging/stock mutation.

The OCR implementation is isolated behind `DeliveryImageOcr`, so Tesseract can later be replaced with a different local vision/OCR engine without changing reconciliation or receiving logic.
