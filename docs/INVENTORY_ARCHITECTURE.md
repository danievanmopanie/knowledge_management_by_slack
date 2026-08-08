# Inventory Channel Architecture (`#inventory`)

Slack-only, fully local inventory management for IT Frontend Support.
No external SaaS integrations. Data lives on the GX10.

## Goals

1. Bulk-create an order from an approved quote
2. Accept delivery (delivery note) and record assets against the order; surface discrepancies (count, type, etc.)
3. Facilitate escalations / errors via Exception records
4. Enforce full lifecycle compliance per asset (hard state machine)
5. Support barcode scanning (photo or hardware wedge) offline via pyzbar

Recons / full stocktakes are deferred.

## Agent map (LangGraph)

| Agent | Responsibility |
|-------|----------------|
| **Coordinator** | Intent routing in `#inventory` |
| **Order Agent** | Quote → bulk Purchase Order |
| **Intake Agent** | Delivery note + asset receipt + variance |
| **Storage Agent** | Put-away / location |
| **Issue Agent** | Dedicated issue & loans |
| **Returns Agent** | Check-in + condition |
| **Compliance Agent** | State-machine guard + Exception creation |
| **Vision / Barcode helper** | pyzbar decode + optional OCR fallback |

## Data store

Local SQLite (preferred) or JSON under `data/inventory/`:

- `orders`
- `order_lines`
- `assets`
- `movements`
- `delivery_notes`
- `exceptions`

See `src/inventory/models.py` for field definitions.

## Core flows

### 1. Bulk order from approved quote

1. Tech posts quote (PDF/image/text) in `#inventory`
2. Order Agent extracts line items (type, model, qty)
3. Creates `PO-YYYY-NNNN` with status `Pending Delivery`
4. Posts confirmation block for correction before lock

### 2. Accept delivery + record assets + discrepancies

1. “Accept delivery for PO-…” + attach delivery note
2. Intake Agent stores DN (image + extracted supplier DN #, date)
3. Tech records each physical asset (barcode photo / typed serial)
4. Assets linked to Order ID, status `Received`
5. On “delivery complete”, variance report:

   | Expected | Received | Flag |
   |----------|----------|------|
   | qty / type | actual | short / over / type mismatch |

6. Discrepancies → Exception + `@inventory-leads`

### 3. Escalations

Exception triggers: quantity/type variance, damage, unreadable barcode skipped twice, illegal state transition attempts.

Fields: order_id / asset_id, type, raised_by, status (`Open`/`Resolved`), Slack thread link.

Manual: `@inventory escalate PO-… – reason`

### 4. Lifecycle enforcement

See **State machine** below. Compliance Agent blocks illegal transitions and tells the tech the exact missing step.

## State machine

### States

| State | Meaning |
|-------|---------|
| `Ordered` | On a PO; not yet physical |
| `Received` | Accepted against DN; not put away |
| `In Storeroom` | Location assigned; available |
| `Issued-Dedicated` | Permanent assignment |
| `Issued-Loan` | Temporary; return date required |
| `Returned` | Back from user; awaiting put-away |
| `Quarantine` | Damage / investigation |
| `Retired` | Written off / disposed |

### Allowed transitions

```
Ordered → Received
Received → In Storeroom | Quarantine
In Storeroom → Issued-Dedicated | Issued-Loan | Quarantine | Retired
Issued-Dedicated → Returned
Issued-Loan → Returned | Quarantine
Returned → In Storeroom | Quarantine | Retired
Quarantine → In Storeroom | Issued-Dedicated | Retired
```

### Guards (examples)

| Transition | Required |
|------------|----------|
| Ordered → Received | Open PO line + unique serial/barcode |
| Received → In Storeroom | Valid location |
| In Storeroom → Issued-Loan | Target user + expected return date |
| * → Retired | Not currently Issued (return first) |

Illegal attempt → refuse + explain next legal step; repeated force → Exception.

Every successful transition writes a **movement** log (who, when, from→to, optional photo).

## Barcode scanning (local)

### Inputs

1. **Photo** in Slack → `pyzbar` decode (Code 128, Code 39, QR, EAN, …)
2. **Hardware scanner** (keyboard wedge) → short alphanumeric token in message

### Pipeline

```
Slack image/message → Coordinator → barcode_decoder (pyzbar)
  → asset lookup (barcode then serial)
  → route to Intake / Storage / Issue / Returns
```

### Linux install (GX10)

```bash
sudo apt-get update
sudo apt-get install -y libzbar0t64   # or libzbar0 on older Ubuntu
pip install pyzbar Pillow
```

If import fails with “Unable to find zbar shared library”, the apt package is missing.

### Lookup order

1. Exact barcode
2. Exact serial
3. Fuzzy serial (OCR noise)

Unknown barcode → offer create asset or link to open PO.

## Slack interaction patterns

- `/order from quote` (or natural language + attachment)
- `/deliver PO-xxxx` + DN photo
- Photo of barcode + `receive against PO-xxxx`
- `/store A-1042 shelf-B3`
- `/issue A-1042 to @user dedicated`
- `/issue A-1042 to @user loan until 2026-08-20`
- `/return A-1042`
- `/status A-1042` or `/status PO-xxxx`
- `@inventory escalate …`

## Out of scope (for now)

- Full store recons / cycle counts
- ServiceNow / ERP sync
- Cloud vision APIs
