"""Two-step purchase-order intake for inventory procurement."""

from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from src.inventory.domain import InventoryDomainError, TrackingMode
from src.inventory.image_ocr import DeliveryImageOcr, IMAGE_EXTENSIONS
from src.inventory.items import ItemCatalogService
from src.inventory.procurement import PurchaseOrder, PurchaseOrderLine
from src.inventory.repository import InventoryRepository
from src.inventory.suppliers import SupplierService
from src.knowledge.file_loader import extract_text


@dataclass(frozen=True)
class StagedPurchaseOrder:
    staging_id: str
    purchase_order_id: str
    supplier: str
    actor: str
    source_path: str
    created_at: str
    status: str = "pending_confirmation"


class PurchaseOrderDocumentParser:
    REQUIRED = {"sku", "quantity"}

    def __init__(self, image_ocr: DeliveryImageOcr | None = None) -> None:
        self.image_ocr = image_ocr or DeliveryImageOcr()

    def parse(self, path: Path) -> list[PurchaseOrderLine]:
        if path.suffix.lower() == ".csv":
            with path.open(newline="", encoding="utf-8", errors="ignore") as handle:
                rows = list(csv.DictReader(handle))
            return self._rows(rows)
        if path.suffix.lower() in IMAGE_EXTENSIONS:
            text = self.image_ocr.extract_text(path)
        else:
            text = extract_text(path)
        return self._parse_text(text)

    def _parse_text(self, text: str) -> list[PurchaseOrderLine]:
        table = [line.strip() for line in text.splitlines() if "|" in line]
        if len(table) < 2:
            raise InventoryDomainError(
                "Could not identify a structured PO table. Use columns such as sku | quantity | tracking_mode."
            )
        headers = [self._normalise_header(v) for v in table[0].split("|")]
        rows = []
        for raw in table[1:]:
            values = [v.strip() for v in raw.split("|")]
            if len(values) == len(headers):
                rows.append(dict(zip(headers, values, strict=False)))
        return self._rows(rows)

    def _rows(self, rows: list[dict[str, str]]) -> list[PurchaseOrderLine]:
        result: list[PurchaseOrderLine] = []
        for index, raw in enumerate(rows, 1):
            row = {self._normalise_header(k): (v or "").strip() for k, v in raw.items()}
            if not self.REQUIRED.issubset(row):
                raise InventoryDomainError("PO rows require at least 'sku' and 'quantity' columns.")
            try:
                quantity = int(row["quantity"])
                unit_price = float(row.get("unit_price") or row.get("price") or 0)
            except ValueError as exc:
                raise InventoryDomainError("PO quantity and price values are invalid.") from exc
            if quantity <= 0:
                raise InventoryDomainError("PO quantity must be positive.")
            mode_text = (row.get("tracking_mode") or row.get("tracking") or "quantity").lower()
            try:
                mode = TrackingMode(mode_text)
            except ValueError as exc:
                raise InventoryDomainError("tracking_mode must be 'serialized' or 'quantity'.") from exc
            result.append(
                PurchaseOrderLine(
                    line_id=row.get("line_id") or row.get("po_line_id") or f"LINE-{index:03d}",
                    sku=row["sku"].upper(),
                    description=row.get("description") or row.get("name") or row["sku"],
                    quantity_ordered=quantity,
                    tracking_mode=mode,
                    unit_price=unit_price,
                    model=row.get("model", ""),
                )
            )
        if not result:
            raise InventoryDomainError("No PO lines were found in the attachment.")
        return result

    @staticmethod
    def _normalise_header(value: str) -> str:
        text = value.strip().lower().replace(" ", "_").replace("-", "_")
        aliases = {
            "qty": "quantity",
            "quantity_ordered": "quantity",
            "price_each": "unit_price",
            "serialised": "tracking_mode",
        }
        return aliases.get(text, text)


class PurchaseOrderIntakeWorkflow:
    def __init__(
        self,
        repository: InventoryRepository | None = None,
        parser: PurchaseOrderDocumentParser | None = None,
    ) -> None:
        self.repository = repository or InventoryRepository()
        self.parser = parser or PurchaseOrderDocumentParser()
        self.items = ItemCatalogService(self.repository)
        self.suppliers = SupplierService(self.repository)
        self._init_schema()

    def _init_schema(self) -> None:
        with self.repository._connect() as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS inventory_staged_purchase_orders (
                    staging_id TEXT PRIMARY KEY,
                    purchase_order_id TEXT NOT NULL,
                    supplier TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    source_path TEXT NOT NULL,
                    po_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    status TEXT NOT NULL
                )"""
            )

    def _validate_lines(self, lines: list[PurchaseOrderLine]) -> None:
        for line in lines:
            self.items.validate_po_item(
                sku=line.sku,
                tracking_mode=line.tracking_mode,
                model=line.model,
            )

    def stage(
        self,
        *,
        purchase_order_id: str,
        supplier: str,
        source_path: Path,
        actor: str,
        external_reference: str = "",
    ) -> tuple[StagedPurchaseOrder, str]:
        if not purchase_order_id or not supplier:
            raise InventoryDomainError("PO number and supplier are required.")
        governed_supplier = self.suppliers.require_active(supplier)
        with self.repository._connect() as conn:
            existing = conn.execute(
                "SELECT 1 FROM inventory_purchase_orders WHERE purchase_order_id=?",
                (purchase_order_id,),
            ).fetchone()
        if existing:
            raise InventoryDomainError(f"Purchase order already exists: {purchase_order_id}")

        lines = self.parser.parse(source_path)
        self._validate_lines(lines)
        po = PurchaseOrder(
            purchase_order_id=purchase_order_id,
            supplier=governed_supplier.supplier_id,
            lines=lines,
            created_by=actor,
            external_reference=external_reference,
        )
        staged = StagedPurchaseOrder(
            staging_id=f"PO-STAGE-{uuid4().hex[:10]}",
            purchase_order_id=purchase_order_id,
            supplier=governed_supplier.supplier_id,
            actor=actor,
            source_path=str(source_path),
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        payload = {
            "purchase_order_id": po.purchase_order_id,
            "supplier": po.supplier,
            "created_by": po.created_by,
            "created_at": po.created_at.isoformat(),
            "external_reference": po.external_reference,
            "lines": [
                {**asdict(line), "tracking_mode": line.tracking_mode.value}
                for line in po.lines
            ],
        }
        with self.repository.transaction() as conn:
            conn.execute(
                "INSERT INTO inventory_staged_purchase_orders VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    staged.staging_id,
                    staged.purchase_order_id,
                    staged.supplier,
                    staged.actor,
                    staged.source_path,
                    json.dumps(payload),
                    staged.created_at,
                    staged.status,
                ),
            )
        total = sum(line.quantity_ordered for line in po.lines)
        value = sum(line.quantity_ordered * line.unit_price for line in po.lines)
        preview = (
            f"*PO preview* `{staged.staging_id}` for `{purchase_order_id}`\n"
            f"Supplier: *{governed_supplier.name}* (`{governed_supplier.supplier_id}`) | "
            f"Lines: *{len(po.lines)}* | Units: *{total}* | Value: *{value:.2f}*\n"
            f"Confirm with `confirm po {staged.staging_id}` or cancel with `cancel po {staged.staging_id}`."
        )
        return staged, preview

    def confirm(self, staging_id: str, *, actor: str) -> str:
        with self.repository._connect() as conn:
            row = conn.execute(
                "SELECT * FROM inventory_staged_purchase_orders WHERE staging_id=?",
                (staging_id,),
            ).fetchone()
        if row is None or row["status"] != "pending_confirmation":
            raise InventoryDomainError("Unknown or inactive staged purchase order.")
        if row["actor"] != actor:
            raise InventoryDomainError("Only the user who staged this PO may confirm it.")
        payload = json.loads(row["po_json"])
        governed_supplier = self.suppliers.require_active(payload["supplier"])
        po = PurchaseOrder(
            purchase_order_id=payload["purchase_order_id"],
            supplier=governed_supplier.supplier_id,
            created_by=payload["created_by"],
            created_at=datetime.fromisoformat(payload["created_at"]),
            external_reference=payload.get("external_reference", ""),
            lines=[
                PurchaseOrderLine(
                    line_id=item["line_id"],
                    sku=item["sku"],
                    description=item["description"],
                    quantity_ordered=int(item["quantity_ordered"]),
                    quantity_received=int(item.get("quantity_received", 0)),
                    tracking_mode=TrackingMode(item["tracking_mode"]),
                    unit_price=float(item.get("unit_price", 0)),
                    model=item.get("model", ""),
                )
                for item in payload["lines"]
            ],
        )
        self._validate_lines(po.lines)
        with self.repository.transaction() as conn:
            existing = conn.execute(
                "SELECT 1 FROM inventory_purchase_orders WHERE purchase_order_id=?",
                (po.purchase_order_id,),
            ).fetchone()
            if existing:
                raise InventoryDomainError(f"Purchase order already exists: {po.purchase_order_id}")
            self.repository.save_purchase_order(po, conn)
            conn.execute(
                "UPDATE inventory_staged_purchase_orders SET status='confirmed' WHERE staging_id=?",
                (staging_id,),
            )
        return (
            f"Purchase order `{po.purchase_order_id}` confirmed with {len(po.lines)} line(s) "
            f"for supplier `{governed_supplier.supplier_id}`."
        )

    def cancel(self, staging_id: str, *, actor: str) -> str:
        with self.repository.transaction() as conn:
            row = conn.execute(
                "SELECT actor,status FROM inventory_staged_purchase_orders WHERE staging_id=?",
                (staging_id,),
            ).fetchone()
            if row is None or row["status"] != "pending_confirmation":
                raise InventoryDomainError("Unknown or inactive staged purchase order.")
            if row["actor"] != actor:
                raise InventoryDomainError("Only the user who staged this PO may cancel it.")
            conn.execute(
                "UPDATE inventory_staged_purchase_orders SET status='cancelled' WHERE staging_id=?",
                (staging_id,),
            )
        return f"Staged PO `{staging_id}` cancelled. No purchase order was created."
