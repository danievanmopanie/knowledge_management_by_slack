"""Two-step delivery-note intake and confirmation workflow for inventory receiving."""

from __future__ import annotations

import csv
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from src.inventory.domain import InventoryDomainError, TrackingMode
from src.inventory.image_ocr import DeliveryImageOcr, IMAGE_EXTENSIONS
from src.inventory.procurement import (
    Delivery,
    DeliveryLine,
    DeliveryReconciler,
    PurchaseOrder,
    PurchaseOrderLine,
    PurchaseOrderStatus,
)
from src.inventory.receiving_service import AtomicReceivingService
from src.inventory.repository import InventoryRepository
from src.knowledge.file_loader import extract_text


@dataclass(frozen=True)
class StagedReceipt:
    receipt_id: str
    purchase_order_id: str
    delivery_id: str
    destination_location: str
    actor: str
    source_path: str
    created_at: str
    status: str = "pending_confirmation"


class DeliveryDocumentParser:
    """Parse structured documents or OCR text into delivery lines."""

    REQUIRED = {"sku", "quantity"}

    def __init__(self, image_ocr: DeliveryImageOcr | None = None):
        self.image_ocr = image_ocr or DeliveryImageOcr()

    def parse(self, path: Path) -> list[DeliveryLine]:
        suffix = path.suffix.lower()
        if suffix == ".csv":
            return self._parse_csv(path)
        if suffix in IMAGE_EXTENSIONS:
            return self._parse_text(self._normalise_ocr_table(self.image_ocr.extract_text(path)))
        return self._parse_text(extract_text(path))

    def _parse_csv(self, path: Path) -> list[DeliveryLine]:
        with path.open(newline="", encoding="utf-8", errors="ignore") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames:
                raise InventoryDomainError("Delivery file has no header row.")
            rows = list(reader)
        return self._rows_to_lines(rows)

    def _parse_text(self, text: str) -> list[DeliveryLine]:
        lines = [line.strip() for line in text.splitlines() if "|" in line]
        if len(lines) < 2:
            raise InventoryDomainError(
                "Could not identify a structured delivery table. Use columns such as sku | quantity | po_line_id."
            )
        headers = [self._normalise_header(value) for value in lines[0].split("|")]
        rows: list[dict[str, str]] = []
        for raw in lines[1:]:
            values = [value.strip() for value in raw.split("|")]
            if len(values) != len(headers):
                continue
            rows.append(dict(zip(headers, values, strict=False)))
        return self._rows_to_lines(rows)

    @staticmethod
    def _normalise_ocr_table(text: str) -> str:
        """Convert common OCR table spacing into the parser's pipe-delimited contract."""
        output: list[str] = []
        for line in text.splitlines():
            clean = line.strip()
            if not clean:
                continue
            if "|" in clean:
                output.append(clean)
                continue
            columns = [value.strip() for value in re.split(r"\s{2,}|\t+", clean) if value.strip()]
            if len(columns) >= 2:
                output.append(" | ".join(columns))
        return "\n".join(output)

    def _rows_to_lines(self, rows: list[dict[str, str]]) -> list[DeliveryLine]:
        result: list[DeliveryLine] = []
        for index, raw in enumerate(rows, 1):
            row = {self._normalise_header(key): (value or "").strip() for key, value in raw.items()}
            if not self.REQUIRED.issubset(row):
                raise InventoryDomainError("Delivery rows require at least 'sku' and 'quantity' columns.")
            try:
                quantity = int(row["quantity"])
                damaged = int(row.get("damaged_quantity") or row.get("damaged") or 0)
            except ValueError as exc:
                raise InventoryDomainError("Delivery quantities must be whole numbers.") from exc
            serials = tuple(
                value.strip()
                for value in (row.get("serial_numbers") or row.get("serials") or "").replace(";", ",").split(",")
                if value.strip()
            )
            result.append(
                DeliveryLine(
                    delivery_line_id=row.get("delivery_line_id") or f"DL-{index:03d}",
                    po_line_id=row.get("po_line_id", ""),
                    sku=row["sku"],
                    quantity_delivered=quantity,
                    damaged_quantity=damaged,
                    serial_numbers=serials,
                    note=row.get("note", ""),
                )
            )
        if not result:
            raise InventoryDomainError("No delivery lines were found in the attachment.")
        return result

    @staticmethod
    def _normalise_header(value: str) -> str:
        text = value.strip().lower().replace(" ", "_").replace("-", "_")
        aliases = {
            "qty": "quantity",
            "quantity_delivered": "quantity",
            "po_line": "po_line_id",
            "line_id": "po_line_id",
            "damaged_qty": "damaged_quantity",
            "serial_number": "serial_numbers",
        }
        return aliases.get(text, text)


class AssistedReceivingWorkflow:
    """Stage a proposed receipt, show reconciliation, then confirm atomically."""

    def __init__(self, repository: InventoryRepository | None = None, parser: DeliveryDocumentParser | None = None) -> None:
        self.repository = repository or InventoryRepository()
        self.parser = parser or DeliveryDocumentParser()
        self.reconciler = DeliveryReconciler()
        self.receiver = AtomicReceivingService(repository=self.repository)
        self._init_schema()

    def _init_schema(self) -> None:
        with self.repository._connect() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS inventory_staged_receipts (
                receipt_id TEXT PRIMARY KEY, purchase_order_id TEXT NOT NULL,
                delivery_id TEXT NOT NULL, destination_location TEXT NOT NULL,
                actor TEXT NOT NULL, source_path TEXT NOT NULL, delivery_json TEXT NOT NULL,
                created_at TEXT NOT NULL, status TEXT NOT NULL)""")

    def stage(self, *, purchase_order_id: str, source_path: Path, destination_location: str, actor: str, supplier_delivery_note: str = "") -> tuple[StagedReceipt, str]:
        if not destination_location:
            raise InventoryDomainError("Destination location is required.")
        po = self._load_purchase_order(purchase_order_id)
        delivery = Delivery(
            delivery_id=f"DEL-{uuid4().hex[:12]}", purchase_order_id=purchase_order_id,
            supplier_delivery_note=supplier_delivery_note or source_path.stem,
            lines=self.parser.parse(source_path), received_by=actor,
        )
        result = self.reconciler.reconcile(po, delivery)
        receipt = StagedReceipt(
            receipt_id=f"RCV-{uuid4().hex[:10]}", purchase_order_id=purchase_order_id,
            delivery_id=delivery.delivery_id, destination_location=destination_location,
            actor=actor, source_path=str(source_path), created_at=datetime.now(timezone.utc).isoformat(),
        )
        with self.repository.transaction() as conn:
            conn.execute("INSERT INTO inventory_staged_receipts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (
                receipt.receipt_id, receipt.purchase_order_id, receipt.delivery_id,
                receipt.destination_location, receipt.actor, receipt.source_path,
                json.dumps(self._delivery_payload(delivery)), receipt.created_at, receipt.status,
            ))
        return receipt, self._format_preview(po, delivery, result, receipt.receipt_id)

    def confirm(self, receipt_id: str, *, actor: str) -> str:
        with self.repository._connect() as conn:
            row = conn.execute("SELECT * FROM inventory_staged_receipts WHERE receipt_id=?", (receipt_id,)).fetchone()
        if row is None:
            raise InventoryDomainError("Unknown staged receipt.")
        if row["status"] != "pending_confirmation":
            raise InventoryDomainError("Receipt is no longer awaiting confirmation.")
        if row["actor"] != actor:
            raise InventoryDomainError("Only the user who staged this receipt may confirm it.")
        po = self._load_purchase_order(row["purchase_order_id"])
        delivery = self._delivery_from_payload(json.loads(row["delivery_json"]))
        result = self.receiver.receive(purchase_order=po, delivery=delivery, destination_location=row["destination_location"], actor=actor)
        with self.repository.transaction() as conn:
            conn.execute("UPDATE inventory_staged_receipts SET status='confirmed' WHERE receipt_id=?", (receipt_id,))
        discrepancy_summary = ", ".join(sorted({item.discrepancy_type.value for item in result.discrepancies}))
        suffix = f" Exceptions: {discrepancy_summary}." if discrepancy_summary else ""
        return f"Receipt `{receipt_id}` confirmed for PO `{po.purchase_order_id}`.{suffix}"

    def cancel(self, receipt_id: str, *, actor: str) -> str:
        with self.repository.transaction() as conn:
            row = conn.execute("SELECT actor,status FROM inventory_staged_receipts WHERE receipt_id=?", (receipt_id,)).fetchone()
            if row is None or row["status"] != "pending_confirmation":
                raise InventoryDomainError("Unknown or inactive staged receipt.")
            if row["actor"] != actor:
                raise InventoryDomainError("Only the user who staged this receipt may cancel it.")
            conn.execute("UPDATE inventory_staged_receipts SET status='cancelled' WHERE receipt_id=?", (receipt_id,))
        return f"Receipt `{receipt_id}` cancelled. Inventory was not changed."

    def _load_purchase_order(self, purchase_order_id: str) -> PurchaseOrder:
        with self.repository._connect() as conn:
            header = conn.execute("SELECT * FROM inventory_purchase_orders WHERE purchase_order_id=?", (purchase_order_id,)).fetchone()
            if header is None:
                raise InventoryDomainError(f"Unknown purchase order: {purchase_order_id}")
            rows = conn.execute("SELECT * FROM inventory_po_lines WHERE purchase_order_id=? ORDER BY line_id", (purchase_order_id,)).fetchall()
        return PurchaseOrder(
            purchase_order_id=header["purchase_order_id"], supplier=header["supplier"],
            created_by=header["created_by"], status=PurchaseOrderStatus(header["status"]),
            created_at=datetime.fromisoformat(header["created_at"]), external_reference=header["external_reference"],
            lines=[PurchaseOrderLine(
                line_id=row["line_id"], sku=row["sku"], description=row["description"],
                quantity_ordered=int(row["quantity_ordered"]), quantity_received=int(row["quantity_received"]),
                tracking_mode=TrackingMode(row["tracking_mode"]), unit_price=float(row["unit_price"]), model=row["model"],
            ) for row in rows],
        )

    @staticmethod
    def _delivery_payload(delivery: Delivery) -> dict:
        return {"delivery_id": delivery.delivery_id, "purchase_order_id": delivery.purchase_order_id,
                "supplier_delivery_note": delivery.supplier_delivery_note, "received_by": delivery.received_by,
                "received_at": delivery.received_at.isoformat(), "lines": [asdict(line) for line in delivery.lines]}

    @staticmethod
    def _delivery_from_payload(payload: dict) -> Delivery:
        return Delivery(
            delivery_id=payload["delivery_id"], purchase_order_id=payload["purchase_order_id"],
            supplier_delivery_note=payload["supplier_delivery_note"], received_by=payload["received_by"],
            received_at=datetime.fromisoformat(payload["received_at"]),
            lines=[DeliveryLine(
                delivery_line_id=item["delivery_line_id"], sku=item["sku"],
                quantity_delivered=int(item["quantity_delivered"]), po_line_id=item.get("po_line_id", ""),
                damaged_quantity=int(item.get("damaged_quantity", 0)),
                serial_numbers=tuple(item.get("serial_numbers", ())), note=item.get("note", ""),
            ) for item in payload["lines"]],
        )

    @staticmethod
    def _format_preview(po, delivery, result, receipt_id: str) -> str:
        accepted = sum(result.accepted_quantities.values())
        lines = [f"*Receipt preview* `{receipt_id}` for PO `{po.purchase_order_id}`",
                 f"Delivery lines: *{len(delivery.lines)}* | Accepted units: *{accepted}*"]
        if result.discrepancies:
            lines.append("*Discrepancies:*")
            lines.extend(f"• {item.discrepancy_type.value}: {item.detail}" for item in result.discrepancies)
        else:
            lines.append("No discrepancies detected.")
        lines.append(f"Confirm with `confirm receipt {receipt_id}` or cancel with `cancel receipt {receipt_id}`.")
        return "\n".join(lines)
