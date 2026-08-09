from src.bot.blockkit import ids
from src.bot.blockkit.actions import attach_confirm_cancel, remove_confirm_cancel_blocks

# Sample reply text mirroring the literal format produced by
# PurchaseOrderIntakeWorkflow.stage() (src/inventory/po_intake.py),
# AssistedReceivingWorkflow.stage() (src/inventory/assisted_receiving.py),
# and the STAGE_COUNT_RE branch of InventoryAgent.handle() (src/agents/inventory/agent.py).
PO_PREVIEW = (
    "*PO preview* `PO-STAGE-abc1234567` for `PO-2026-0001`\n"
    "Supplier: *Dell* (`DELL`) | Lines: *2* | Units: *12* | Value: *450.00*\n"
    "Confirm with `confirm po PO-STAGE-abc1234567` or cancel with `cancel po PO-STAGE-abc1234567`."
)
RECEIPT_PREVIEW = (
    "*Receipt preview* `RCV-abc1234567` for PO `PO-2026-0001` into *Main Store*\n"
    "Delivery lines: *2* | Accepted units: *12*\n"
    "No discrepancies detected.\n"
    "Confirm with `confirm receipt RCV-abc1234567` or cancel with `cancel receipt RCV-abc1234567`."
)
COUNT_STAGED = (
    "*Stock count staged* `CNT-abc1234567`\n"
    "• SKU/location: `MOUSE-01` @ `STORE-A`\n"
    "• Ledger on hand: *95*\n"
    "• Reserved: *0*\n"
    "• Physical count: *97*\n"
    "• Variance: *+2*\n"
    "No stock has changed. "
    "Use `confirm count CNT-abc1234567` or `cancel count CNT-abc1234567`."
)


def test_attach_confirm_cancel_extracts_po_id():
    blocks = attach_confirm_cancel(PO_PREVIEW)
    assert blocks is not None
    assert blocks[0]["text"]["text"] == PO_PREVIEW
    elements = blocks[1]["elements"]
    assert elements[0]["action_id"] == ids.CONFIRM_PO
    assert elements[0]["value"] == "PO-STAGE-abc1234567"
    assert elements[1]["action_id"] == ids.CANCEL_PO
    assert elements[1]["value"] == "PO-STAGE-abc1234567"


def test_attach_confirm_cancel_extracts_receipt_id():
    blocks = attach_confirm_cancel(RECEIPT_PREVIEW)
    assert blocks is not None
    assert blocks[0]["text"]["text"] == RECEIPT_PREVIEW
    elements = blocks[1]["elements"]
    assert elements[0]["action_id"] == ids.CONFIRM_RECEIPT
    assert elements[0]["value"] == "RCV-abc1234567"


def test_attach_confirm_cancel_extracts_count_id():
    blocks = attach_confirm_cancel(COUNT_STAGED)
    assert blocks is not None
    assert blocks[0]["text"]["text"] == COUNT_STAGED
    elements = blocks[1]["elements"]
    assert elements[0]["action_id"] == ids.CONFIRM_COUNT
    assert elements[0]["value"] == "CNT-abc1234567"


def test_attach_confirm_cancel_returns_none_for_plain_text():
    assert attach_confirm_cancel("Created customer `EMP-42`.") is None
    assert attach_confirm_cancel("Sorry, I could not complete that request.") is None


def test_remove_confirm_cancel_blocks_preserves_preview_and_unrelated_actions():
    blocks = attach_confirm_cancel(PO_PREVIEW)
    assert blocks is not None
    unrelated = {"type": "actions", "block_id": "unrelated", "elements": []}

    remaining = remove_confirm_cancel_blocks([*blocks, unrelated])

    assert remaining == [blocks[0], unrelated]
