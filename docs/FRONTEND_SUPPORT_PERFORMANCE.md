# Frontend Support performance tuning

This slice keeps the technician-facing response model unchanged and reduces latency around it.

## Applied optimisations

- **Single incident retrieval per turn:** incident hits are reused for prompt rendering and graph enrichment instead of running the same vector search twice.
- **Parallel evidence retrieval:** governed knowledge and incident-vector retrieval execute concurrently because neither depends on the other.
- **Top 3 conversational evidence:** live Slack turns cap retrieval at three incident/governed matches rather than five.
- **Compact prompt context:** combined evidence context is capped at 4,500 characters; incident context is capped at 2,600 characters.
- **Short-lived context-scoped cache:** repeated normalized queries are cached for five minutes, with channel/user/role scope in the key to prevent permission-sensitive evidence leaking between contexts. The in-memory cache is capped at 128 entries.
- **Slack streaming:** once generation starts, accumulated LLM output updates the existing progress message in-place. Updates are throttled to avoid one Slack API call per token. The final sanitized/cited answer replaces the streaming text when complete.

## Validation

Run:

```bash
python -m pytest -q \
  tests/test_frontend_conversation.py \
  tests/test_frontend_collaboration.py \
  tests/test_frontend_abstention.py \
  tests/test_frontend_trigger_feedback.py \
  tests/test_frontend_clarification.py \
  tests/test_frontend_interactivity_clarification.py \
  tests/test_frontend_progress.py \
  tests/test_frontend_performance.py
```

Live logs should still show `stage=retrieval`, `stage=llm`, and total lane latency so before/after performance can be compared.