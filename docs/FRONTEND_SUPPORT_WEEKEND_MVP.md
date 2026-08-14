# Frontend Support Weekend MVP

## Outcome

The `#frontend-support` experience is designed to feel like a natural team support chat rather than a command-driven bot. The same Frontend Support assistant now supports five connected behaviours:

1. Ambient public troubleshooting in `#frontend-support`.
2. Historical incident + governed knowledge + graph-backed support memory.
3. Private one-on-one Slack DM coaching.
4. Local transcription of Slack voice notes.
5. Morning/afternoon operational intelligence and automatic formal-knowledge gap tasks.

## Public support behaviour

The bot observes ordinary messages in `#frontend-support` without requiring an `@mention`.

It stores the thread as attributed case evidence, including who said what and which troubleshooting actions were reported. Social conversation stays quiet. Technical messages can invoke the support agent when it can add useful historical evidence or a next step.

A technician can say phrases such as:

- `We've got this`
- `We'll take it from here`
- `Assistant, stay quiet`

The assistant acknowledges once, then continues capturing the thread without proactively replying. It can be called back with wording such as `Assistant, help again`.

The ServiceNow incident number is no longer treated as an entry gate. A technical conversation can start naturally. When useful, the assistant reminds the requester to add the `INC...` number so the learning remains referenceable.

## Evidence versus knowledge

All relevant human conversation is stored as evidence. It is **not automatically treated as formal knowledge**.

When a likely resolution is detected, the original requester or identified resolver confirms the capture. A confirmed Slack resolution becomes trusted operational evidence and is linked into the support graph.

Historical incident notes are also evidence, not truth. The technician-facing agent is instructed to prefer repeated confirmed outcomes and governed knowledge over a single historical resolution note.

## Formal knowledge gap loop

After a human confirms a field resolution, the bot checks the governed knowledge store using the problem plus confirmed resolution.

If there is no strong governed match, it creates a deduplicated `KG-xxxxx` task and posts it to:

1. `CHANNEL_CREATE_KNOWLEDGE`, when configured; otherwise
2. `CHANNEL_KNOWLEDGE_UPLOADS` as a temporary fallback.

The task contains:

- source incident number
- problem/symptom
- confirmed field resolution
- contributors
- reason the knowledge gap was created
- source Slack channel/thread identity

This deliberately separates fast operational learning from formal curation.

## Private technician coaching

A direct Slack message to the bot is routed to the Frontend Support agent as a private coaching session.

Private conversations are stored separately from public thread events. The model is explicitly instructed never to reveal, quote, attribute or imply private content in a public channel. If something useful should move into the public support conversation, the assistant should offer a sanitized technical summary first.

This allows a technician to ask basic questions, request explanations or explore troubleshooting privately without exposing the private conversation to the team.

### Slack app settings required for private coaching

In the Slack app configuration:

1. Add the bot token scope `im:history` if it is not already present.
2. Keep `chat:write` enabled so the assistant can respond.
3. Under **Event Subscriptions → Subscribe to bot events**, add `message.im`.
4. Reinstall the Slack app to the workspace after adding OAuth scopes.

The existing public-channel message subscription remains required for ambient `#frontend-support` listening.

## Voice notes

Voice-note support is local. Slack audio is downloaded to the controlled staging area, transcribed on the GX10 with `faster-whisper`, then deleted after transcription. The transcript is processed like a typed technician message.

The Slack bot requires `files:read` so it can download a voice note shared in a conversation it can access. Reinstall the app after adding that scope if necessary.

Install the optional voice dependency:

```bash
cd /opt/knowledge_management_by_slack
source .venv/bin/activate
pip install -e '.[voice]'
```

Recommended `.env` start:

```text
VOICE_NOTES_ENABLED=true
VOICE_TRANSCRIPTION_MODEL=small
VOICE_TRANSCRIPTION_DEVICE=cuda
VOICE_TRANSCRIPTION_COMPUTE_TYPE=float16
```

If CUDA is not available to the Python environment, use `VOICE_TRANSCRIPTION_DEVICE=cpu` and an appropriate compute type for the host.

The first model load may require the transcription model to be available in the local model cache or downloadable by the host.

## Morning and afternoon support pulse

The 07:00 morning report has been upgraded from a recent-incident count to an operational focus report. It includes deterministic facts for:

- current open workload
- aging open work
- stale/untouched open work
- recent activity
- assignment-group concentration
- location concentration
- recent categories/themes
- samples of aging, stale and recently changed incidents

The local LLM turns those facts into a short technician-focused priority briefing.

A new 16:00 afternoon report provides a handover-style summary: what changed, unresolved clusters, aging/stale work and what must carry forward.

Default thresholds:

```text
REPORT_DAILY_HOURS=24
REPORT_AFTERNOON_HOURS=10
REPORT_AGING_DAYS=3
REPORT_STALE_HOURS=24
```

## Required channel configuration

Add the new curated-knowledge channel ID:

```text
CHANNEL_FRONTEND_SUPPORT=C...
CHANNEL_CREATE_KNOWLEDGE=C...
```

`CHANNEL_KNOWLEDGE_UPLOADS` remains supported for existing document ingestion.

## Enable the afternoon timer

Copy/update the systemd units from `deploy/systemd`, then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now frontend-daily-report.timer
sudo systemctl enable --now frontend-afternoon-report.timer
systemctl list-timers | grep frontend
```

The timers assume the GX10 host timezone is configured correctly for local SAST execution.

## Restart the Slack bot

After pulling/merging and updating `.env`, restart the existing bot service using the same deployment method already used on the GX10.

## Weekend pilot scenarios

Run these in order:

1. **Normal team chat:** greetings/thanks should not trigger support answers.
2. **Typed incident:** describe a real problem without an incident number. The agent should help first and only lightly request the number.
3. **Human contribution:** a second technician suggests an action. The subsequent agent query must include that contribution and attribution.
4. **Known failed action:** report an unsuccessful step. The agent should not recommend the same step again.
5. **Mute:** say `We've got this`. The agent should stop proactive replies but continue recording subsequent troubleshooting messages.
6. **Resume:** say `Assistant, help again`. Proactive support should resume.
7. **Resolution:** confirm a real fix. The requester/resolver should be able to capture it as operational knowledge.
8. **Knowledge gap:** if governed knowledge coverage is weak, a `KG-xxxxx` task should appear in `#create-knowledge`.
9. **Private coaching:** DM the bot about the same technical issue. Private content must not appear in the public thread.
10. **Voice note:** post a short incident voice note and verify the transcript is used for troubleshooting.
11. **Morning pulse:** run `python scripts/run_daily_report.py` manually once before enabling the timer.
12. **Afternoon handover:** run `python scripts/run_afternoon_report.py` manually once before enabling the timer.

## Useful validation commands

```bash
# Tests
pytest -q tests/test_frontend_collaboration.py tests/test_frontend_voice.py tests/test_support_operations.py

# Existing support/RAG regression tests
pytest -q tests/test_frontend_abstention.py tests/test_incident_field_aware_rag.py tests/test_support_graph.py

# Manual report generation/publication
python scripts/run_daily_report.py
python scripts/run_afternoon_report.py

# Check scheduled jobs
systemctl list-timers | grep frontend
```

## Deliberate MVP boundaries

This weekend build does not claim that every Slack sentence is correctly transformed into a formal symptom/action/root-cause graph fact. The full source thread is retained as attributed evidence; trusted graph promotion remains human-confirmed.

Knowledge-gap creation currently uses a confirmed resolution plus weak/missing governed knowledge as the trigger. Recurrence volume, business impact and cross-location frequency can be added as stronger task-prioritisation signals after the pilot data is flowing.

The next major improvement after this MVP should be semantic thread-state extraction (symptoms, hypotheses, actions and outcomes) so intervention decisions rely less on keyword heuristics while preserving the same storage and governance boundaries.
