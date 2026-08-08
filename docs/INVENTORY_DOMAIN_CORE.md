# Inventory Domain Core

The inventory capability separates **serialized assets** from **quantity stock** while sharing a common catalog, storage hierarchy and immutable transaction history.

## Core concepts

- **CatalogItem** — SKU/product definition and tracking mode (`serialized` or `quantity`).
- **StorageLocation** — hierarchical physical location: site → storeroom → cage/shelf/bin.
- **SerializedAsset** — individually tracked equipment such as laptops, monitors and other assets requiring serial/asset identity.
- **StockBalance** — quantity balance for consumables/non-serialized equipment at one storage location.
- **StockTransaction** — auditable receive, issue, return, transfer and disposal movement.
- **ReconciliationCount** — physical count compared with the ledger, exposing an explicit variance.
- **PurchaseOrder / PurchaseOrderLine** — expected supplier commitment, including SKU, quantity, tracking mode and received-to-date balance.
- **Delivery / DeliveryLine** — one physical supplier delivery note and what was actually delivered.
- **DeliveryReconciliation** — deterministic comparison of the delivery against the PO before stock is changed.

## Business boundaries

The domain core is deliberately deterministic. Agents may interpret user intent and documents, but they do not decide inventory truth. All inventory writes must eventually pass through domain services/business rules.

The domain currently enforces:

1. stock cannot become negative;
2. movements require the appropriate source/destination location;
3. transfers decrement one location and increment another;
4. reconciliation validates the expected ledger quantity before applying a physical count;
5. reorder checks use *available* rather than gross on-hand quantity;
6. serialized assets carry lifecycle, assignee/customer, PO and location identity independently from quantity stock;
7. deliveries cannot be received against a closed/cancelled or unrelated PO;
8. delivered lines are reconciled against PO lines before quantities are accepted;
9. short, over, wrong-SKU, damaged and unexpected delivery lines are classified explicitly;
10. serialized accepted units require one serial number per accepted unit;
11. accepted delivery quantities cannot over-receive a PO line;
12. PO status is derived from accepted quantities (`open`, `partially_received`, `received`).

## Receiving flow

`Purchase Order → Delivery Note / Physical Delivery → Reconcile → Exceptions or Accepted Quantities → Update PO → later stock/asset receipt`

Reconciliation intentionally does **not** post stock. This prevents an OCR/agent interpretation or an unresolved discrepancy from silently changing stock balances. A later receiving service will translate accepted quantities into `StockTransaction` records or new `SerializedAsset` records inside one durable transaction.

## Next slices

1. Persistent SQLite inventory repository and immutable movement/event tables.
2. Receiving service that atomically posts accepted delivery quantities to quantity stock and serialized assets.
3. Serialized asset receive/store/issue/return/repair/retire/dispose service.
4. Quantity-stock issue/return/transfer/reconciliation service.
5. Slack inventory command/intention layer on top of the domain services.
