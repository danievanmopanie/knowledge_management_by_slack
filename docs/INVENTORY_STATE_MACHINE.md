# Asset lifecycle state machine

Authoritative rules enforced by the Compliance Agent.

## States

- `Ordered`
- `Received`
- `In Storeroom`
- `Issued-Dedicated`
- `Issued-Loan`
- `Returned`
- `Quarantine`
- `Retired`

## Transition table

| From | To | Actor | Required data |
|------|-----|-------|---------------|
| Ordered | Received | Intake | delivery_note_id, barcode/serial |
| Received | In Storeroom | Storage | location |
| Received | Quarantine | Intake/Compliance | reason, optional photo |
| In Storeroom | Issued-Dedicated | Issue | assignee, reason |
| In Storeroom | Issued-Loan | Issue | assignee, return_due_at |
| In Storeroom | Quarantine | any authorised | reason |
| In Storeroom | Retired | Compliance | write-off reason + confirm |
| Issued-Dedicated | Returned | Returns | optional condition photo |
| Issued-Loan | Returned | Returns | optional condition photo |
| Issued-Loan | Quarantine | Returns/Compliance | overdue or damage |
| Returned | In Storeroom | Storage | location |
| Returned | Quarantine | Returns | damage reason |
| Returned | Retired | Compliance | write-off reason |
| Quarantine | In Storeroom | Compliance | clearance note |
| Quarantine | Issued-Dedicated | Compliance | rare; clearance |
| Quarantine | Retired | Compliance | write-off reason |

## Hard blocks

- Cannot issue from `Ordered` or `Received`
- Cannot retire while `Issued-*` (must return first)
- Cannot put away without location
- Loan without `return_due_at` rejected
- Duplicate barcode/serial rejected

## Side effects

On success:

1. Append movement row
2. Update asset.status
3. Slack confirmation in `#inventory`
4. If quarantine or variance → open Exception + notify leads

On illegal attempt:

1. Refuse write
2. Reply with exact missing step
3. Optional Exception after repeated attempts
