"""Central registry of Block Kit action_id / callback_id strings.

Keeping these as named constants (instead of scattering literal strings across
`interactivity.py`, `home.py`, `modals.py`, and `actions.py`) avoids typo
mismatches between the button/modal that produces an id and the handler that
listens for it.
"""

from __future__ import annotations

# App Home buttons -> open a modal
HOME_OPEN_ISSUE_ASSET = "inventory_open_issue_asset"
HOME_OPEN_RETURN_ASSET = "inventory_open_return_asset"
HOME_OPEN_CREATE_CUSTOMER = "inventory_open_create_customer"
HOME_OPEN_CREATE_LOCATION = "inventory_open_create_location"
HOME_OPEN_INVENTORY_SUMMARY = "inventory_open_summary"

# Modal callback_ids (view_submission)
MODAL_ISSUE_ASSET = "inventory_issue_asset_submit"
MODAL_RETURN_ASSET = "inventory_return_asset_submit"
MODAL_CREATE_CUSTOMER = "inventory_create_customer_submit"
MODAL_CREATE_LOCATION = "inventory_create_location_submit"

# Issue Asset modal: allocation static_select triggers a views_update to
# show/hide the loan-until datepicker.
ISSUE_ASSET_ALLOCATION_SELECT = "inventory_issue_asset_allocation"

# Confirm/Cancel buttons attached to staged PO / receipt / count replies.
CONFIRM_PO = "inventory_confirm_po"
CANCEL_PO = "inventory_cancel_po"
CONFIRM_RECEIPT = "inventory_confirm_receipt"
CANCEL_RECEIPT = "inventory_cancel_receipt"
CONFIRM_COUNT = "inventory_confirm_count"
CANCEL_COUNT = "inventory_cancel_count"

# Builder Agent persistent status card buttons.
BUILDER_CANCEL_TURN = "builder_cancel_turn"
BUILDER_MERGE_DEPLOY = "builder_merge_deploy"
