# Builder Agent: natural Slack coding harness on Atlas + AEON Qwen3.8

This deployment introduces Atlas only for the Slack `#builder` path. The rest of
the platform keeps its existing LLM and embedding endpoints, so the migration is
reversible and a serving failure is isolated to Builder work.

## Target experience

`#builder` is a conversation, not a command interface. A user can write:

```text
Can you look at why the frontend tests are failing?
```

and then continue in the thread:

```text
That makes sense. Fix it and add a regression test.
```

No `build:` prefix or bot mention is required. `status <task-id>` and
`cancel <task-id>` remain optional deterministic escape hatches.

A Slack thread is also a coding session. If the thread already created an open
PR, later code-changing turns continue that same branch/PR. A new Slack thread
starts fresh. If the old PR has been merged or closed, the next turn also starts
fresh from `main` rather than mutating historical work.

## Target architecture

```text
Slack #builder
    -> natural root message / thread follow-up
    -> recent Slack thread context
    -> Builder task queue
    -> builder-worker.service on the device
    -> isolated git worktree
         -> new session: origin/main
         -> existing session: open PR branch
    -> Aider coding harness
         -> OpenAI-compatible http://127.0.0.1:8888/v1
         -> Atlas (GB10)
         -> AEON-7/Qwen3.8-27B-AEON-ULTIMATE-UNCENSORED-BF16

If no repository change:
    -> normal conversational answer in Slack
    -> no PR manufactured

If repository changed:
    -> local Ruff + pytest gate
         -> if red: send validation output back to AEON and repair
         -> repeat up to BUILDER_MAX_REPAIR_ATTEMPTS
    -> only when green: commit/push
    -> create a PR OR update the thread's existing open PR
    -> persistent Block Kit card becomes PR-ready
```

## Slack UX / Block Kit rule

Conversation stays conversational. Normal explanations, questions and answers
are normal Slack thread messages.

Block Kit is used where structure materially helps: durable progress, validation
state, warnings, choices and links to created artefacts. Builder creates one
persistent status card per turn and updates that card in place as the work moves
through working, repair, validation, failure or PR-ready state. It does not spam
the thread with a new progress message for every internal step.

The final card exposes an **Open pull request** action only when a PR exists.
Every Block Kit message also has useful plain-text fallback text. The repository-
wide rule is documented in `docs/SLACK_UX_RULES.md`.

## Why Atlas is a canary

Atlas has a GB10 `qwen3.8-27b` kernel target and an OpenAI-compatible API. The
AEON checkpoint is intentionally configured behind the Builder-only endpoint so
it can be proven on coding work before any other agent is migrated.

The exact AEON Qwen3.8 BF16 checkpoint + Atlas combination must still be smoke-
tested on the target GX10 after deployment. If Atlas rejects that checkpoint
layout, the rest of the Slack platform is unaffected and Builder can be rolled
back to its previous serving path.

## Configuration

Copy these values into the deployment `.env` while preserving the real Slack and
GitHub credentials already present there:

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
`--model-name aeon-builder`, so Aider's request name matches the served alias
while the Hugging Face checkpoint remains configurable.

The validation command mirrors the repository's current high-signal GitHub CI
checks. Install the worker environment with development dependencies:

```bash
/opt/knowledge_management_by_slack/.venv/bin/pip install -e '.[dev]'
```

## Slack permissions for natural thread memory

The Slack app must receive ordinary messages in `#builder`, not only mentions,
and must have the applicable channel-history permission needed for
`conversations.replies`. That is what lets a short follow-up such as “also cover
the error path” carry the preceding thread context into the coding harness.

## Start Atlas on the GX10

The launcher uses Atlas's GB10 Docker image, host networking and the existing
Hugging Face cache:

```bash
cd /opt/knowledge_management_by_slack
sudo docker pull avarok/atlas-gb10:latest
sudo -u danie /bin/bash scripts/run_atlas_builder.sh
```

For persistent operation:

```bash
sudo cp deploy/systemd/atlas-builder.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now atlas-builder.service
sudo systemctl status atlas-builder.service
```

The launcher pins `--kernel-target qwen3.8-27b` and `--no-auto-swap`. An Aider
request therefore cannot replace the loaded model with another checkpoint.

## Smoke test Atlas before enabling Builder work

Check the OpenAI-compatible endpoint:

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

The API key is a local client value. Keep this endpoint local-only; do not expose
it through Cloudflare or a public firewall rule.

## Execution and publish boundary

For each natural Slack turn the worker:

1. reconstructs recent conversation context from the Slack thread;
2. checks whether the thread has an existing open Builder PR;
3. prepares an isolated worktree from either that PR branch or `main`;
4. lets AEON/Aider inspect the repository and respond to the request;
5. if no files or commits changed, returns the answer to Slack and stops;
6. if the repository changed, runs `BUILDER_TEST_COMMAND` locally;
7. when validation fails, sends bounded validation output back to AEON with an
   instruction not to skip, weaken or xfail tests;
8. reruns validation after each repair;
9. refuses to publish if required validation remains red; and
10. only after green validation pushes to GitHub, either creating the session PR
    or updating the open PR already associated with that Slack thread.

This removes the need for a human to pull every Builder PR merely to discover
whether the repository's lint and test gates pass.

## Deployment order

Use this order so a model-serving problem cannot interrupt the current Builder
before the endpoint is proven:

```bash
# 1. Deploy/pull the application change.
# 2. Update .env with the Builder-only values above.
# 3. Ensure the worker venv has .[dev] dependencies.
# 4. Start atlas-builder.service and complete the two smoke tests.
# 5. Confirm the Slack app can receive #builder messages and read thread history.
# 6. Restart builder-worker.service.
sudo systemctl restart builder-worker.service
sudo journalctl -u builder-worker.service -f
```

Do not migrate `LLM_BASE_URL`, `SUPPORT_EXTRACTION_MODEL`, embeddings or the
frontend-support agent in the same change. Atlas should earn that wider role
with Builder telemetry first.

## Rollback

Atlas can be removed from Builder without touching any other agent. Restore the
previous Builder model setting:

```dotenv
BUILDER_AIDER_MODEL=ollama_chat/qwen3-coder:30b
```

Then:

```bash
sudo systemctl restart builder-worker.service
sudo systemctl disable --now atlas-builder.service
```

The Builder task queue, Slack routing, thread/session model, worktrees and GitHub
PR flow remain intact; only the inference provider changes.

## Security boundary

Builder intentionally runs repository code to prove changes. Keep the Slack
allowlist narrow, keep the inference endpoint local-only, and run the worker
under a dedicated non-root account with access only to the Builder checkout,
worktree paths and Git credentials required to push branches.

An uncensored model can reduce inappropriate refusals for legitimate engineering
work, but it is not a security control. The allowlist, host permissions, isolated
worktree, deterministic validation gate and GitHub review remain the controls.
