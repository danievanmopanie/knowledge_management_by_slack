# Knowledge Base Onboarding and In-Thread Article Reference/Edit

This covers two related additions on top of the existing governed-knowledge
pipeline (`src/knowledge/governed_ingest.py`, `#knowledge-uploads`):

1. Bulk-onboarding an existing, formal knowledge base so it becomes retrieval
   evidence for `#frontend-support` from day one, instead of growing only from
   confirmed field fixes.
2. Referencing and editing an existing article directly from a
   `#frontend-support` conversation, when a technician recognises that an
   article is wrong or incomplete mid-troubleshooting.

Both reuse the existing governed-knowledge storage
(`KnowledgeCatalog` + `VectorStore` + `commit_knowledge()`), so anything
onboarded or edited here is immediately part of the same evidence
`HybridRetriever` and `FrontendSupportAgent` already query — no separate index.

## 1. Bulk-onboarding an existing knowledge base

### Option A — CLI, for a batch already on disk

Drop existing KB exports (Markdown, text, PDF, DOCX, CSV) into the configured
raw-docs directory (`RAW_DOCS_PATH`, default `./data/raw`) on the GX10, then:

```bash
# Preview what would be imported
python scripts/bulk_import_knowledge.py --dry-run

# Import everything under RAW_DOCS_PATH
python scripts/bulk_import_knowledge.py

# Import from a specific export directory instead
python scripts/bulk_import_knowledge.py --source ./data/raw/servicenow-kb-export
```

Each file becomes its own governed document, keyed by its path
(`bulk-import:<relative-path>`), and versioned the same way as every other
governed document — `commit_knowledge()` is content-hash based, so re-running
the script after a partial export or a corrected file only touches what
actually changed. Unsupported file types (images, anything outside
`src/knowledge/file_loader.py::SUPPORTED_EXTENSIONS`) are skipped with a
one-line reason, not a hard failure.

### Option B — Slack, for a handful of files someone is uploading now

`#knowledge-uploads` (or `#create-knowledge`) already stages a file per
message and requires an explicit `confirm <stage-id>` before it becomes
searchable. That is by design — nothing should become governed knowledge
without a human confirming it — but confirming one-by-one doesn't scale to
onboarding an existing KB a few dozen files at a time. Uploading several files
in one Slack message now stages all of them, and:

```text
confirm all
```

commits every file *you* staged *in that channel* in one shot (per-file
confirm/cancel still work individually). Nothing is confirmed on another
person's behalf — `confirm all` only ever touches uploads staged by the user
who sends it.

## 2. Reference/edit an existing article from #frontend-support

### The problem this solves

A technician mid-conversation may recognise that the article the assistant
just cited is wrong or missing a step. Previously there was no way to act on
that without leaving `#frontend-support` and re-finding the article manually
in `#knowledge-uploads`.

### How "that article" gets resolved

`FrontendSupportAgent` already cites governed evidence inline (`[E1]`, `[E2]`,
...). It now also remembers, per thread, which document it most recently
cited (`src/knowledge/citation_memory.py::CitationMemory`, written from
`FrontendSupportAgent.handle()`). So when a technician says something like:

- "That KB article is outdated"
- "Update the knowledge article to also mention the profile rebuild step"
- "Flag that article for review"

`FrontendCollaborationService.classify()` recognises the intent
(`MessageKind.KNOWLEDGE_EDIT_REQUEST`) and the bot resolves "that article"
from citation memory — no need to repeat the title. If nothing was recently
cited in the thread, the bot asks for the article by name instead of
guessing.

### Flow

1. **In-thread confirmation.** "Want me to flag *\<Article\>* for review with
   this note?" — `Flag for review` / `Not now`. This mirrors the existing
   resolution-capture confirmation pattern so nothing is queued without an
   explicit yes (`docs/SLACK_UX_RULES.md`).
2. **Drafting.** On confirmation, the bot reconstructs the article's current
   text from its indexed chunks (`src/knowledge/article_revision.py::reconstruct_document_text`)
   and drafts a targeted revision grounded in that text plus the technician's
   note (`revise_article`) — an edit, not a rewrite: everything the note
   doesn't touch is preserved, and a note too vague to apply confidently gets
   left alone with a `## Reviewer note` appended instead of the model
   guessing.
3. **Review card in `#create-knowledge`** (falls back to
   `#knowledge-uploads`) shows the technician's note, the drafted revision,
   and:
   - **Approve revision** — commits the draft via `commit_knowledge()`,
     reusing the *original* document's `source_id` so this becomes a new
     version of the same article, not a duplicate.
   - **Assign a teammate** — a Slack user picker; the assignee gets a DM with
     the article, the note and a link back to the review channel.
   - **Dismiss** — closes the request without publishing.

### Slack app settings required

Same as knowledge-task assignment: the `im:write` bot token scope is needed
for the assignment DM (`conversations.open`). Without it, assignment still
records who was assigned and `send_dm()` logs a failure and skips the DM.

## Useful validation commands

```bash
# Bulk import + confirm-all
pytest -q tests/test_bulk_import_knowledge.py

# Citation memory + article revision drafting
pytest -q tests/test_citation_memory.py tests/test_article_revision.py

# Edit-intent detection + the full review-card flow
pytest -q tests/test_frontend_collaboration.py tests/test_frontend_edit_flow.py

# Manual dry run of the bulk importer
python scripts/bulk_import_knowledge.py --dry-run
```
