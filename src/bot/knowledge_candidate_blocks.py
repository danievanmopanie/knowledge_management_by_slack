"""Block Kit views/cards for the incident-to-knowledge candidate workflow."""

from __future__ import annotations

import json

from src.bot.blockkit import ids
from src.knowledge.knowledge_candidates import KnowledgeCandidate

_STATUS_LABELS = {
    "needs_enrichment": "Needs enrichment",
    "in_progress": "In progress",
    "needs_input": "Needs input",
    "ready_for_review": "Ready for review",
    "changes_requested": "Changes requested",
    "published": "Published",
}


def _meta(**values: str) -> str:
    return json.dumps(values)


def _input(block_id: str, label: str, value: str = "", *, multiline: bool = True, optional: bool = False) -> dict:
    element: dict = {
        "type": "plain_text_input",
        "action_id": "value",
        "multiline": multiline,
    }
    if value:
        element["initial_value"] = value[:3000]
    return {
        "type": "input",
        "block_id": block_id,
        "optional": optional,
        "label": {"type": "plain_text", "text": label[:75]},
        "element": element,
    }


def frontend_candidate_actions(*, incident_number: str, channel_id: str, thread_ts: str, message_ts: str = "") -> list[dict]:
    value = _meta(
        incident_number=incident_number,
        channel_id=channel_id,
        thread_ts=thread_ts,
        message_ts=message_ts,
    )
    return [
        {"type": "divider"},
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    "*Should this become proper organisational knowledge?*  "
                    "Create a reviewable candidate or flag the gap for the `#create-knowledge` backlog."
                ),
            },
        },
        {
            "type": "actions",
            "block_id": f"frontend_knowledge_candidate_{incident_number}",
            "elements": [
                {
                    "type": "button",
                    "action_id": ids.FRONTEND_CREATE_KNOWLEDGE_CANDIDATE,
                    "text": {"type": "plain_text", "text": "Create knowledge candidate"},
                    "style": "primary",
                    "value": value,
                },
                {
                    "type": "button",
                    "action_id": ids.FRONTEND_FLAG_KNOWLEDGE_GAP,
                    "text": {"type": "plain_text", "text": "Flag knowledge gap"},
                    "value": value,
                },
            ],
        },
    ]


def frontend_candidate_modal(
    *,
    incident_number: str,
    title: str,
    issue_pattern: str,
    symptom: str,
    recorded_resolution: str,
    resolution_pattern: str,
    recorded_root_cause: str,
    channel_id: str,
    thread_ts: str,
    message_ts: str,
) -> dict:
    evidence = [f"*Incident:* `{incident_number}`"]
    if issue_pattern:
        evidence.append(f"*Pattern:* {issue_pattern}")
    if symptom:
        evidence.append(f"*Symptom:* {symptom}")
    if recorded_resolution:
        evidence.append(f"*Recorded outcome:* {recorded_resolution}")
    if recorded_root_cause:
        evidence.append(f"*Recorded root cause:* {recorded_root_cause}")
    gap = (
        "Turn this incident into reusable knowledge. Confirm when the procedure applies, "
        "the exact steps, post-fix validation, and any prerequisites or risks."
    )
    return {
        "type": "modal",
        "callback_id": ids.MODAL_FRONTEND_KNOWLEDGE_CANDIDATE,
        "private_metadata": _meta(
            incident_number=incident_number,
            channel_id=channel_id,
            thread_ts=thread_ts,
            message_ts=message_ts,
            issue_pattern=issue_pattern,
            symptom=symptom,
            recorded_resolution=recorded_resolution,
            resolution_pattern=resolution_pattern,
            recorded_root_cause=recorded_root_cause,
        ),
        "title": {"type": "plain_text", "text": "Knowledge candidate"},
        "submit": {"type": "plain_text", "text": "Send to backlog"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": [
            {"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(evidence)[:2900]}},
            _input("title", "Knowledge title", title, multiline=False),
            _input("knowledge_gap", "What still needs to be turned into proper knowledge?", gap),
            _input("notes", "Technician notes / context", "", optional=True),
        ],
    }


def _candidate_summary(candidate: KnowledgeCandidate) -> str:
    owner = f"<@{candidate.owner_id}>" if candidate.owner_id else "Unassigned"
    lines = [
        f"*{candidate.candidate_id} — {candidate.title or 'Untitled knowledge candidate'}*",
        f"*Status:* {_STATUS_LABELS.get(candidate.status, candidate.status)}  •  *Owner:* {owner}",
    ]
    if candidate.incident_number:
        lines.append(f"*Evidence incident:* `{candidate.incident_number}`")
    if candidate.issue_pattern:
        lines.append(f"*Issue pattern:* {candidate.issue_pattern}")
    if candidate.symptom:
        lines.append(f"*Symptom:* {candidate.symptom}")
    if candidate.resolution_pattern:
        lines.append(f"*Captured resolution pattern:* {candidate.resolution_pattern}")
    elif candidate.recorded_resolution:
        lines.append(f"*Recorded resolution:* {candidate.recorded_resolution}")
    if candidate.knowledge_gap:
        lines.append(f"*Knowledge gap / review note:* {candidate.knowledge_gap}")
    missing = candidate.missing_review_fields
    if candidate.status != "published":
        if missing:
            labels = {"applies_when": "when this applies", "procedure": "procedure", "validation": "validation"}
            lines.append("*Still required before review:* " + ", ".join(labels[item] for item in missing))
        else:
            lines.append("*Publication gate:* required knowledge fields are complete.")
    if candidate.status == "needs_input" and candidate.input_request:
        target = f" from <@{candidate.input_requested_from}>" if candidate.input_requested_from else ""
        lines.append(f"*Input requested{target}:* {candidate.input_request}")
    if candidate.status == "published":
        lines.append(f"*Governed document:* `{candidate.published_document_id or 'published'}`")
    return "\n".join(lines)[:3000]


def candidate_card(candidate: KnowledgeCandidate) -> list[dict]:
    blocks: list[dict] = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": "✅ Published knowledge" if candidate.status == "published" else "📚 Knowledge Candidate",
                "emoji": True,
            },
        },
        {"type": "section", "text": {"type": "mrkdwn", "text": _candidate_summary(candidate)}},
    ]
    if candidate.status == "published":
        blocks.append(
            {
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": "Approved content is now searchable as governed knowledge."}],
            }
        )
        return blocks

    actions = []
    if not candidate.owner_id:
        actions.append(
            {
                "type": "button",
                "action_id": ids.KNOWLEDGE_CANDIDATE_CLAIM,
                "text": {"type": "plain_text", "text": "Assign to me"},
                "value": candidate.candidate_id,
            }
        )
    actions.append(
        {
            "type": "button",
            "action_id": ids.KNOWLEDGE_CANDIDATE_EDIT,
            "text": {"type": "plain_text", "text": "Edit / enrich"},
            "style": "primary" if candidate.status in {"needs_enrichment", "changes_requested", "needs_input"} else None,
            "value": candidate.candidate_id,
        }
    )
    # Slack rejects style=null.
    actions[-1] = {key: value for key, value in actions[-1].items() if value is not None}
    actions.append(
        {
            "type": "button",
            "action_id": ids.KNOWLEDGE_CANDIDATE_REQUEST_INPUT,
            "text": {"type": "plain_text", "text": "Request input"},
            "value": candidate.candidate_id,
        }
    )
    if candidate.status == "ready_for_review":
        actions.extend(
            [
                {
                    "type": "button",
                    "action_id": ids.KNOWLEDGE_CANDIDATE_APPROVE,
                    "text": {"type": "plain_text", "text": "Approve & publish"},
                    "style": "primary",
                    "value": candidate.candidate_id,
                    "confirm": {
                        "title": {"type": "plain_text", "text": "Publish knowledge?"},
                        "text": {"type": "mrkdwn", "text": "This will make the reviewed content searchable as governed knowledge."},
                        "confirm": {"type": "plain_text", "text": "Publish"},
                        "deny": {"type": "plain_text", "text": "Cancel"},
                    },
                },
                {
                    "type": "button",
                    "action_id": ids.KNOWLEDGE_CANDIDATE_REQUEST_CHANGES,
                    "text": {"type": "plain_text", "text": "Request changes"},
                    "value": candidate.candidate_id,
                },
            ]
        )
    else:
        actions.append(
            {
                "type": "button",
                "action_id": ids.KNOWLEDGE_CANDIDATE_READY,
                "text": {"type": "plain_text", "text": "Mark ready for review"},
                "value": candidate.candidate_id,
            }
        )
    blocks.append({"type": "actions", "block_id": f"knowledge_candidate_actions_{candidate.id}", "elements": actions[:5]})
    if len(actions) > 5:
        blocks.append({"type": "actions", "block_id": f"knowledge_candidate_review_{candidate.id}", "elements": actions[5:]})
    if candidate.source_channel_id and candidate.source_thread_ts:
        blocks.append(
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": (
                            f"Origin: Frontend Support thread `{candidate.source_thread_ts}` • "
                            "Incident evidence remains provenance; this candidate is not knowledge until approved."
                        ),
                    }
                ],
            }
        )
    return blocks


def edit_candidate_modal(candidate: KnowledgeCandidate, *, channel_id: str, message_ts: str) -> dict:
    return {
        "type": "modal",
        "callback_id": ids.MODAL_KNOWLEDGE_CANDIDATE_EDIT,
        "private_metadata": _meta(candidate_id=candidate.candidate_id, channel_id=channel_id, message_ts=message_ts),
        "title": {"type": "plain_text", "text": "Enrich knowledge"},
        "submit": {"type": "plain_text", "text": "Save draft"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*Evidence:* `{candidate.incident_number}`\n"
                        f"*Captured pattern:* {candidate.issue_pattern or 'Not captured'}\n"
                        f"*Captured outcome:* {candidate.recorded_resolution or 'Not captured'}"
                    )[:2900],
                },
            },
            _input("title", "Knowledge title", candidate.title, multiline=False),
            _input("applies_when", "When does this procedure apply?", candidate.applies_when),
            _input("procedure", "Exact resolution / procedure", candidate.procedure),
            _input("validation", "How do we validate the fix?", candidate.validation),
            _input("prerequisites", "Prerequisites / risks", candidate.prerequisites, optional=True),
            _input("root_cause", "Confirmed root cause (only if known)", candidate.recorded_root_cause, optional=True),
            _input("notes", "Additional notes", candidate.notes, optional=True),
        ],
    }


def request_input_modal(candidate: KnowledgeCandidate, *, channel_id: str, message_ts: str) -> dict:
    return {
        "type": "modal",
        "callback_id": ids.MODAL_KNOWLEDGE_CANDIDATE_INPUT,
        "private_metadata": _meta(candidate_id=candidate.candidate_id, channel_id=channel_id, message_ts=message_ts),
        "title": {"type": "plain_text", "text": "Request input"},
        "submit": {"type": "plain_text", "text": "Request"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": [
            {
                "type": "input",
                "block_id": "person",
                "optional": True,
                "label": {"type": "plain_text", "text": "Ask a specific person (optional)"},
                "element": {"type": "users_select", "action_id": "value", "placeholder": {"type": "plain_text", "text": "Select person"}},
            },
            _input("question", "What input is needed?", candidate.input_request),
        ],
    }


def request_changes_modal(candidate: KnowledgeCandidate, *, channel_id: str, message_ts: str) -> dict:
    return {
        "type": "modal",
        "callback_id": ids.MODAL_KNOWLEDGE_CANDIDATE_CHANGES,
        "private_metadata": _meta(candidate_id=candidate.candidate_id, channel_id=channel_id, message_ts=message_ts),
        "title": {"type": "plain_text", "text": "Request changes"},
        "submit": {"type": "plain_text", "text": "Send back"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": [_input("changes", "What must change before approval?", candidate.knowledge_gap)],
    }
