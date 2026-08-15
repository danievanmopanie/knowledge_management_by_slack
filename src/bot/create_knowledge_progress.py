"""Block Kit renderer for Create Knowledge incident-build progress."""

from __future__ import annotations

from typing import Any


def _n(value: Any) -> str:
    try:
        return f"{int(value or 0):,}"
    except (TypeError, ValueError):
        return "0"


def _seconds(value: Any) -> str:
    try:
        seconds = float(value or 0)
    except (TypeError, ValueError):
        seconds = 0.0
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, remaining = divmod(int(round(seconds)), 60)
    if minutes < 60:
        return f"{minutes}m {remaining:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"


def _bar(done: int, total: int, width: int = 12) -> str:
    if total <= 0:
        return "░" * width
    ratio = min(1.0, max(0.0, done / total))
    filled = int(round(ratio * width))
    return "█" * filled + "░" * (width - filled)


def _embedding_line(progress: dict) -> str:
    stage = progress.get("stage") or "queued"
    total = int(progress.get("documents_total") or 0)
    done = int(progress.get("documents_done") or 0)
    metadata_only = int(progress.get("metadata_only_incidents") or 0)

    if stage in {"embedding_completed", "completed"} or "vector_documents" in progress:
        vectors = int(progress.get("vector_documents") or done)
        avoided = int(progress.get("vector_metadata_only_incidents") or metadata_only)
        suffix = f" — {_n(vectors)} vector documents"
        if avoided:
            suffix += f"; {_n(avoided)} lifecycle-only incidents avoided embedding"
        return f"✅ *Searchable embeddings*{suffix}"

    if stage in {"embedding_plan", "metadata_updated", "embedding_progress", "graph_completed"}:
        if total == 0:
            return "✅ *Searchable embeddings* — no semantic re-embedding required"
        pct = int(round((done / total) * 100)) if total else 0
        return (
            f"🔄 *Searchable embeddings* `{_bar(done, total)}` {pct}% "
            f"({_n(done)} / {_n(total)} documents)"
        )

    return "⬜ *Searchable embeddings* — waiting"


def _process_lines(progress: dict) -> list[str]:
    stage = progress.get("stage") or "queued"
    rows = progress.get("rows")
    unique = progress.get("unique_incidents")
    has_temporal = "new" in progress
    has_graph = "graph_edges_added" in progress
    completed = stage == "completed"
    failed = stage == "failed"

    lines = ["✅ *Upload profiled and confirmed*"]

    if rows is not None:
        lines.append(f"✅ *Parse ServiceNow snapshot* — {_n(rows)} rows / {_n(unique)} incidents")
    elif stage == "parse_started":
        lines.append("🔄 *Parse ServiceNow snapshot* — reading CSV")
    else:
        lines.append("⬜ *Parse ServiceNow snapshot*")

    if has_temporal:
        lines.append(
            "✅ *Compare with existing knowledge* — "
            f"{_n(progress.get('new'))} new · {_n(progress.get('changed'))} changed · "
            f"{_n(progress.get('unchanged'))} unchanged"
        )
        lines.append(
            "✅ *Build temporal history* — "
            f"{_n(progress.get('versions_written'))} versions · "
            f"{_n(progress.get('transitions_written'))} transitions · "
            f"{_n(progress.get('dwell_rows_written'))} dwell records"
        )
    elif stage == "comparison_started":
        lines.extend(
            [
                "🔄 *Compare with existing knowledge* — detecting new and changed incidents",
                "⬜ *Build temporal history*",
            ]
        )
    else:
        lines.extend(["⬜ *Compare with existing knowledge*", "⬜ *Build temporal history*"])

    if has_graph:
        lines.append(
            "✅ *Build knowledge graph* — "
            f"+{_n(progress.get('graph_nodes_added'))} nodes · "
            f"+{_n(progress.get('graph_edges_added'))} relationships"
        )
    elif stage in {
        "build_started",
        "embedding_plan",
        "metadata_updated",
        "embedding_progress",
    }:
        lines.append("🔄 *Build knowledge graph* — structured relationships")
    else:
        lines.append("⬜ *Build knowledge graph*")

    lines.append(_embedding_line(progress))

    if completed:
        lines.append("✅ *Validate and publish* — knowledge is searchable")
    elif failed:
        lines.append("❌ *Validate and publish* — build failed")
    else:
        lines.append("⬜ *Validate and publish*")
    return lines


def queued_blocks(job: dict) -> list[dict]:
    return build_blocks(job, {"stage": "queued"})


def build_blocks(job: dict, progress: dict | None = None) -> list[dict]:
    progress = dict(progress or {})
    stage = progress.get("stage") or "queued"
    job_id = str(job.get("job_id") or "")
    file_name = str(job.get("file_name") or "ServiceNow incident CSV")

    if stage == "completed":
        metrics = dict(progress.get("metrics") or {})
        return completed_blocks(job, metrics)
    if stage == "failed":
        return failed_blocks(job, str(progress.get("error") or job.get("error_message") or "Unknown error"))

    title = "Queued" if stage == "queued" else "Building incident knowledge"
    blocks: list[dict] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"🧠 {title}", "emoji": True},
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"*{file_name}*  •  `{job_id}`  •  Fast temporal build — no LLM extraction",
                }
            ],
        },
        {"type": "divider"},
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "*Build process*\n" + "\n".join(_process_lines(progress)),
            },
        },
    ]

    elapsed = progress.get("elapsed_seconds")
    if elapsed is not None:
        blocks.append(
            {
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": f"Elapsed: *{_seconds(elapsed)}*"}],
            }
        )
    return blocks


def completed_blocks(job: dict, metrics: dict) -> list[dict]:
    job_id = str(job.get("job_id") or "")
    file_name = str(job.get("file_name") or "ServiceNow incident CSV")
    metadata_only = int(metrics.get("vector_metadata_only_incidents") or 0)
    vector_incidents = int(metrics.get("vector_incidents") or 0)
    changed_total = int(metrics.get("new") or 0) + int(metrics.get("changed") or 0)
    avoided_pct = (metadata_only / changed_total * 100) if changed_total else 0.0

    result_lines = [
        f"• *Incidents:* {_n(metrics.get('new'))} new · {_n(metrics.get('changed'))} changed · {_n(metrics.get('unchanged'))} unchanged",
        f"• *Temporal knowledge:* {_n(metrics.get('versions_written'))} versions · {_n(metrics.get('transitions_written'))} transitions · {_n(metrics.get('dwell_rows_written'))} assignment dwell records",
        f"• *Graph knowledge:* +{_n(metrics.get('graph_nodes_added'))} nodes · +{_n(metrics.get('graph_edges_added'))} relationships",
        f"• *Search knowledge:* {_n(metrics.get('vector_documents'))} vector documents processed · {_n(metrics.get('vector_collection_total'))} total in incident index",
    ]
    if metadata_only:
        result_lines.append(
            f"• *Embedding avoided:* {_n(metadata_only)} lifecycle-only incidents ({avoided_pct:.0f}% of new/changed workload)"
        )
    if vector_incidents:
        result_lines.append(
            f"• *Semantic changes embedded:* {_n(vector_incidents)} incidents at {float(metrics.get('embedding_documents_per_second') or 0):.1f} docs/sec"
        )

    return [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "✅ Knowledge build complete", "emoji": True},
        },
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": f"*{file_name}*  •  `{job_id}`"}],
        },
        {"type": "divider"},
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "*Build process*\n" + "\n".join(_process_lines({**metrics, "stage": "completed"})),
            },
        },
        {"type": "divider"},
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": "*Knowledge created*\n" + "\n".join(result_lines)},
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": (
                        f"Completed in *{_seconds(metrics.get('total_seconds'))}*  •  "
                        f"graph {_seconds(metrics.get('graph_seconds'))}  •  "
                        f"embedding {_seconds(metrics.get('embedding_seconds'))}"
                    ),
                }
            ],
        },
    ]


def failed_blocks(job: dict, error: str) -> list[dict]:
    job_id = str(job.get("job_id") or "")
    file_name = str(job.get("file_name") or "ServiceNow incident CSV")
    return [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "❌ Knowledge build failed", "emoji": True},
        },
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": f"*{file_name}*  •  `{job_id}`"}],
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"The incident knowledge build stopped before publishing.\n```{error[:1200]}```",
            },
        },
    ]
