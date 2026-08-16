# Builder Agent: Atlas + AEON Qwen3.8 canary

This change introduces Atlas only for the Slack `#builder` path. The rest of the
platform keeps its existing LLM and embedding endpoints. That makes the
migration reversible and limits a serving failure to Builder tasks rather than
the technician/support experience.

## Target architecture

```text
Slack #builder
    -> BuilderAgent task queue
    -> builder-worker.service
    -> isolated git worktree
    -> Aider
         -> OpenAI-compatible http://127.0.0.1:8888/v1
         -> Atlas (GB10)
         -> AEON-7/Qwen3.8-27B-AEON-ULTIMATE-UNCENSORED-BF16
    -> local Ruff + pytest gate on the GX10
         -> if red: send failure output back to AEON and repair
         -> repeat up to BUILDER_MAX_REPAIR_ATTEMPTS
    -> only when green: git push
    -> create GitHub PR
    -> report PR back into the Slack thread
```

## Why this is a canary

Atlas has a GB10 `qwen3.8-27b` kernel target and an OpenAI-compatible API. The
AEON checkpoint is intentionally configured behind the Builder-only endpoint,
so it can be proven on this workload before any other agent is migrated.

The exact AEON Qwen3.8 BF16 checkpoint + Atlas combination should still be
smoke-tested on the target GX10 after deployment. If that model has a checkpoint
layout that Atlas rejects, the rest of the Slack platform is unaffected and the
Builder can be rolled back by changing its model setting.

## Configuration

Copy these values into the deployment `.env` (use the real Slack/GitHub values
already present in that file):

```dotenv
BUILDER_AIDER_MODEL=openai/aeon-builder
BUILDER_LLM_BASE_URL=http://127.0.0.1:8888/v1
BUILDER_LLM_API_KEY=atlas-local
BUILDER_MODEL_CHECKPOINT=AEON-7/Qwen3.8-27B-AEON-ULTIMATE-UNCENSORED-BF16

BUILDER_TEST_COMMAND={python} -m ruff check src tests scripts && {python} -m pytest -q
BUILDER_TEST_TIMEOUT_SECONDS=1200
BUILDER_MAX_REPAIR_ATTEMPTS=2
BUILDER_REQUIRE_TESTS_PASS=true
BUILDER_VALIDATION_OUTPUT_CHARS=8000
```

`openai/aeon-builder` is the Aider/LiteLLM provider name. Atlas is started with
`--model-name aeon-builder`, so the model name in Aider requests matches the
served alias while the actual Hugging Face checkpoint remains configurable.

The validation command mirrors the repository's current high-signal GitHub CI
checks. Install the worker environment with development dependencies so both
Ruff and pytest are available:

```bash
/opt/knowledge_management_by_slack/.venv/bin/pip install -e '.[dev]'
```

## Start Atlas on the GX10

The launcher uses Atlas's GB10 Docker image, host networking, and the existing
Hugging Face cache:

```bash
cd /opt/knowledge_management_by_slack
sudo docker pull avarok/atlas-gb10:latest
sudo -u danie /bin/bash scripts/run_atlas_builder.sh
```

For persistent operation, install the optional systemd unit:

```bash
sudo cp deploy/systemd/atlas-builder.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now atlas-builder.service
sudo systemctl status atlas-builder.service
```

The launcher pins `--kernel-target qwen3.8-27b` and `--no-auto-swap`. A request
from Aider therefore cannot cause Atlas to replace the loaded model with a
second checkpoint.

## Smoke test Atlas before enabling Builder work

Check that the OpenAI-compatible endpoint is alive:

```bash
curl -s http://127.0.0.1:8888/v1/models
```

Then send a tiny completion:

```bash
curl -s http://127.0.0.1:8888/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer atlas-local' \
  -d '{
    "model": "aeon-builder",
    "messages": [{"role": "user", "content": "Reply with exactly: builder-ready"}],
    "max_tokens": 32,
    "temperature": 0
  }'
```

The API key is a local client value; Atlas is bound through host networking and
should not be exposed through Cloudflare or a public firewall rule.

## Builder execution loop

For each Slack build request the worker now:

1. fetches `main` and creates an isolated git worktree/branch;
2. asks AEON through Atlas to implement the requested change;
3. runs `BUILDER_TEST_COMMAND` locally in that worktree;
4. if validation fails, sends the bounded validation output back to AEON with an
   instruction not to skip/weaken/xfail tests;
5. reruns validation after each repair;
6. refuses to push or create a PR if validation remains red;
7. pushes and creates the PR only after the local gate passes; and
8. reports progress and the final PR link in the original Slack thread.

This means the human no longer needs to pull every Builder PR merely to discover
whether the repository's lint and test gates pass.

## Deployment order

Use this order so a model-serving issue cannot interrupt the current Builder
until the endpoint is proven:

```bash
# 1. Deploy/pull the application change.
# 2. Update .env with the Builder-only values above.
# 3. Ensure the worker venv has .[dev] dependencies.
# 4. Start atlas-builder.service and complete the two smoke tests.
# 5. Restart builder-worker.service.
sudo systemctl restart builder-worker.service
sudo journalctl -u builder-worker.service -f
```

Do not migrate `LLM_BASE_URL`, `SUPPORT_EXTRACTION_MODEL`, embeddings, or the
frontend-support agent in the same change. Atlas should earn that wider role
with Builder telemetry first.

## Rollback

Atlas can be removed from the Builder without touching any other agent. Restore
the previous Builder model setting and restart the worker:

```dotenv
BUILDER_AIDER_MODEL=ollama_chat/qwen3-coder:30b
```

Then:

```bash
sudo systemctl restart builder-worker.service
sudo systemctl disable --now atlas-builder.service
```

The Builder worktree, task queue, Slack routing, and GitHub PR flow remain the
same; only its inference provider changes.

## Security boundary

The Builder intentionally runs repository code to prove changes. Keep the
existing Slack allowlist narrow, keep the Builder endpoint local-only, and run
the worker under a dedicated non-root account with access only to the Builder
checkout/worktree paths and the Git credentials required to push branches.

An uncensored model is useful here because it is less likely to refuse legitimate
engineering operations, but it must not be treated as a security control. The
allowlist, isolated worktree, deterministic validation gate, GitHub review, and
host permissions remain the controls.
