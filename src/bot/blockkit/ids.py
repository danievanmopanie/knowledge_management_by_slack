"""Central registry of Block Kit action_id / callback_id strings.

Keeping these as named constants (instead of scattering literal strings across
interactivity modules and block builders) avoids typo mismatches between the
control that produces an id and the handler that listens for it.
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

# Create Knowledge staged-upload decisions.
CREATE_KNOWLEDGE_CONFIRM_STAGE = "create_knowledge_confirm_stage"
CREATE_KNOWLEDGE_CANCEL_STAGE = "create_knowledge_cancel_stage"

# Frontend Support -> Knowledge Candidate handoff.
FRONTEND_CREATE_KNOWLEDGE_CANDIDATE = "frontend_create_knowledge_candidate"
FRONTEND_FLAG_KNOWLEDGE_GAP = "frontend_flag_knowledge_gap"
MODAL_FRONTEND_KNOWLEDGE_CANDIDATE = "frontend_knowledge_candidate_submit"

# Create Knowledge candidate backlog workflow.
KNOWLEDGE_CANDIDATE_CLAIM = "knowledge_candidate_claim"
KNOWLEDGE_CANDIDATE_EDIT = "knowledge_candidate_edit"
KNOWLEDGE_CANDIDATE_REQUEST_INPUT = "knowledge_candidate_request_input"
KNOWLEDGE_CANDIDATE_READY = "knowledge_candidate_ready_review"
KNOWLEDGE_CANDIDATE_APPROVE = "knowledge_candidate_approve_publish"
KNOWLEDGE_CANDIDATE_REQUEST_CHANGES = "knowledge_candidate_request_changes"
MODAL_KNOWLEDGE_CANDIDATE_EDIT = "knowledge_candidate_edit_submit"
MODAL_KNOWLEDGE_CANDIDATE_INPUT = "knowledge_candidate_input_submit"
MODAL_KNOWLEDGE_CANDIDATE_CHANGES = "knowledge_candidate_changes_submit"
