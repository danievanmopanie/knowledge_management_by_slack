# Inventory Domain Core

The inventory capability separates **serialized assets** from **quantity stock** while sharing a common catalog, storage hierarchy and immutable transaction history.

## Core concepts

- **CatalogItem** — SKU/product definition and tracking mode (`serialized` or `quantity`).
- **StorageLocation** — hierarchical physical location: site → storeroom → cage/shelf/bin.
- **SerializedAsset** — individually tracked equipment such as laptops, monitors and other assets requiring serial/asset identity.
- **StockBalance** — quantity balance for consumables/non-serialized equipment at one storage location.
- **StockTransaction** — auditable receive, issue, return, transfer and disposal movement.
- **ReconciliationCount** — physical count compared with the ledger, exposing an explicit variance.

## Business boundaries

The domain core is deliberately deterministic. Agents may interpret user intent and documents, but they do not decide inventory truth. All inventory writes must eventually pass through domain services/business rules.

The first slice enforces:

1. stock cannot become negative;
2. movements require the appropriate source/destination location;
3. transfers decrement one location and increment another;
4. reconciliation validates the expected ledger quantity before applying a physical count;
5. reorder checks use *available* rather than gross on-hand quantity;
6. serialized assets carry lifecycle, assignee/customer, PO and location identity independently from quantity stock.

## Next slices

1. Purchase orders and PO lines.
2. Delivery receipt + PO reconciliation (short/over/wrong/damaged).
3. Persistent SQLite repository and immutable movement/event tables.
4. Serialized asset receive/store/issue/return/repair/retire/dispose service.
5. Quantity-stock issue/return/transfer/reconciliation service.
6. Slack inventory command/intention layer on top of the domain services.
