# Barcode scanning (local)

## Dependencies

System (Ubuntu/Debian on GX10):

```bash
sudo apt-get update
sudo apt-get install -y libzbar0t64
# fallback on older releases:
# sudo apt-get install -y libzbar0
```

Python (project venv):

```bash
pip install pyzbar Pillow
```

## API

`src/inventory/barcode.py`:

- `decode_image(path_or_bytes) -> list[DecodedBarcode]`
- `decode_first(path_or_bytes) -> str | None`

Uses pyzbar; returns empty list if no codes found (caller may fall back to OCR).

## Slack behaviour

| Input | Behaviour |
|-------|-----------|
| Image with one barcode | Use value; continue current flow |
| Image with multiple | List codes; ask which to use |
| Short alphanumeric message (8–20 chars) mid-flow | Treat as scanner wedge input |
| Unreadable | Ask for re-photo or typed serial |

## Asset match order

1. `assets.barcode`
2. `assets.serial_number`
3. Fuzzy serial (optional)
