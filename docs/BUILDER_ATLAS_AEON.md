# Builder Agent: natural Slack engineering harness on Atlas + AEON Qwen3.8

This deployment introduces Atlas only for the Slack `#builder` path. The rest of
the platform keeps its existing LLM and embedding endpoints, so the migration is
reversible and a serving failure is isolated to Builder work.

## Target experience

`#builder` is a conversation and an on-device engineering control plane, not a
command interface. No `build:` prefix or bot mention is required.

Examples:

```text
Can you look at why the frontend tests are failing?
```

```text
Fix it and add a regression test. Then run the focused tests.
```

```text
Take PR #83 and action it on the GX10. Make sure it actually works.
```

```text
Start the local app, check the logs and curl the endpoint.
```

The user should not need to copy generated shell commands from ChatGPT, Claude,
Grok or Slack into the GX10 terminal. The resident Builder model has a real
`run_shell` tool executed by `builder-worker.service` on the GX10 and an
`edit_code` tool backed by Aider.

`status <task-id>` and `cancel <task-id>` remain optional deterministic escape
hatches.

## External PR handoff

GitHub is the interchange layer between external coding assistants and the local
GX10 Builder. ChatGPT, Claude, Grok or another agent may create a PR in the
configured repository. The user can then say naturally in Slack:

```text
Take PR #83 and action it.
```

Builder will:

1. resolve PR #83 through the GitHub API;
2. reject a closed/merged PR or a fork PR it cannot safely mutate;
3. fetch and check out the actual PR head branch in an isolated GX10 worktree;
4. give AEON the PR title/body plus the user's Slack request;
5. let AEON call real terminal commands and make code edits on that branch;
6. always run the deterministic local validation gate for an explicit PR handoff;
7. repair validation failures when possible;
8. push any repair commits back to the **same** PR; and
9. bind the Slack thread to that PR so natural follow-ups continue the session.

An explicit handoff overrides any older PR associated with that Slack thread.

## Slack thread = coding session

If the thread already has an open PR, later code-changing turns continue that
same branch/PR. A new Slack thread starts fresh. If the old PR has been merged or
closed, the next turn starts fresh from `main` rather than mutating historical
work.

## Real GX10 terminal tool loop

The primary Builder loop is no longer just “ask Aider to edit files.” AEON talks
to Atlas through `/v1/chat/completions` with OpenAI-compatible function tools:

```text
AEON Qwen3.8
    |
    +-- run_shell(command, timeout)
    |      -> real /bin/bash -lc on GX10
    |      -> stdout/stderr + exit code returned to AEON
    |
    +-- edit_code(instruction)
           -> Aider edits the current isolated worktree
           -> result returned to AEON
```

AEON can therefore iterate:

```text
inspect -> execute -> diagnose -> edit -> execute -> verify -> answer
```

Typical `run_shell` uses include:

- `git status`, `git diff`, `git log`;
- focused pytest/Ruff/mypy commands;
- Python scripts and CLIs installed for the service account;
- `curl` against local endpoints;
- application logs;
- Docker `compose`, `ps`, `logs`, `inspect` and `exec` when the service account
  has Docker permission; and
- other non-interactive engineering commands available to that account.

The final deterministic Ruff + pytest gate still runs outside the model-driven
tool loop. The model cannot talk a red gate into becoming green.

## Terminal security boundary

“Terminal control” means the Builder can execute commands **as the account that
runs `builder-worker.service`**. It is not granted an invented root capability.

The implementation adds several layers:

- Slack Builder remains protected by `BUILDER_AGENT_ALLOWED_USER_IDS`;
- Atlas is localhost-only;
- child shell processes receive a scrubbed environment without Slack, GitHub or
  Builder model credentials;
- direct reads of `.env`, SSH key paths, process environments and common
  credential material are blocked by the shell guard;
- `sudo`, `doas`, `pkexec`, user switching, host power operations, raw disk
  writes and destructive root deletion are blocked;
- `docker run` / `docker create` are blocked by default;
- systemd uses `NoNewPrivileges`, `RestrictSUIDSGID`, kernel/control-group
  protections and a private temp area; and
- the publish boundary still requires deterministic local validation.

These string-level command guards are defence in depth, not a substitute for OS
permissions. Run Builder under a dedicated non-root account in production. If
that account is added to the Docker group, treat that as a deliberate high-trust
permission because Docker access can be host-powerful.

## Target architecture

```text
ChatGPT / Claude / Grok / other agent
        -> GitHub PR (optional handoff source)

Slack #builder
    -> natural root message / thread follow-up
    -> recent Slack thread context
    -> explicit PR number/URL detection when present
    -> Builder task queue
    -> builder-worker.service on GX10
    -> isolated git worktree
         -> new session: origin/main
         -> thread continuation: existing open PR branch
         -> external handoff: named PR head branch
    -> Atlas :8888 / AEON Qwen3.8 tool loop
         -> run_shell (real GX10 subprocess)
         -> edit_code (Aider)

If ordinary turn makes no repository change:
    -> normal conversational answer in Slack
    -> no PR manufactured

If repository changed:
    -> deterministic local Ruff + pytest gate
         -> if red: send validation output back to AEON/Aider and repair
    -> only when green: commit/push
    -> create a PR OR update the existing/handoff PR

If explicit PR handoff:
    -> deterministic local gate runs even when no edit was required
    -> status reports whether a repair commit was necessary
```

## Slack UX / Block Kit rule

Conversation stays conversational. Normal explanations, questions and answers
are normal Slack thread messages.

Block Kit is used where structure materially helps: durable progress, validation
state, warnings, choices and links to created artefacts. Builder creates one
persistent status card per turn and updates that card in place as work moves
through execution, repair, validation, failure or PR-ready state. It does not
spam the thread with a new progress message for every internal step.

The final card exposes an **Open pull request** action only when a PR exists. The
repository-wide rule is documented in `docs/SLACK_UX_RULES.md`.

## Why Atlas is a canary

Atlas has a GB10 `qwen3.8-27b` kernel target, OpenAI-compatible chat APIs and a
Qwen tool-call parser. The launcher enables prefix caching and
`--tool-call-parser qwen3_coder` because Builder now performs multi-turn tool
loops rather than single completions.

The exact AEON Qwen3.8 BF16 checkpoint + Atlas combination must still be smoke-
tested on the target GX10. In particular, test both ordinary completion and a
function/tool call before restarting Builder. If Atlas rejects the checkpoint or
cannot parse AEON's tool calls, the rest of the Slack platform is unaffected.

## Configuration

Copy these values into the deployment `.env` while preserving the real Slack and
GitHub credentials already present there:

```dotenv
BUILDER_AIDER_MODEL=openai/aeon-builder
BUILDER_LLM_MODEL=aeon-builder
BUILDER_LLM_BASE_URL=http://127.0.0.1:8888/v1
BUILDER_LLM_API_KEY=atlas-local
BUILDER_MODEL_CHECKPOINT=AEON-7/Qwen3.8-27B-AEON-ULTIMATE-UNCENSORED-BF16

BUILDER_TERMINAL_ENABLED=true
BUILDER_TERMINAL_MAX_STEPS=16
BUILDER_TERMINAL_COMMAND_TIMEOUT_SECONDS=180
BUILDER_TERMINAL_MAX_COMMAND_TIMEOUT_SECONDS=900
BUILDER_TERMINAL_OUTPUT_CHARS=12000
BUILDER_TERMINAL_MAX_TOKENS=4096
BUILDER_TERMINAL_TEMPERATURE=0.1
BUILDER_TERMINAL_ALLOW_DOCKER_RUN=false

BUILDER_TEST_COMMAND={python} -m ruff check src tests scripts && {python} -m pytest -q
BUILDER_TEST_TIMEOUT_SECONDS=1200
BUILDER_MAX_REPAIR_ATTEMPTS=2
BUILDER_REQUIRE_TESTS_PASS=true
BUILDER_VALIDATION_OUTPUT_CHARS=8000
```

The validation command mirrors the repository's high-signal GitHub CI checks.
Install the worker environment with development dependencies:

```bash
/opt/knowledge_management_by_slack/.venv/bin/pip install -e '.[dev]'
```

## Slack permissions for natural thread memory

The Slack app must receive ordinary messages in `#builder`, not only mentions,
and must have the applicable channel-history permission needed for
`conversations.replies`. This lets a follow-up such as “also cover the error
path” carry the preceding thread context into the harness.

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
sudo cp deploy/systemd/builder-worker.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now atlas-builder.service
sudo systemctl restart builder-worker.service
```

The Atlas launcher pins the Qwen3.8 kernel target, enables a 64K context by
default, enables prefix caching and enables the Qwen tool-call parser.

## Smoke tests before enabling Builder

First check the model endpoint:

```bash
curl -s http://127.0.0.1:8888/v1/models
```

Then test an ordinary completion. Finally, test an OpenAI-compatible function
call with a harmless tool definition. The deployment is not considered ready
until AEON returns a parseable `tool_calls` response through Atlas.

After Atlas passes, use Slack itself for the end-to-end proof:

```text
In #builder: Run `pwd` and `git status` on the GX10 and tell me what you observe. Do not change files.
```

Then hand off a low-risk existing PR:

```text
Take PR #<number> and action it on the GX10. Run the local gates and do not merge it.
```

The expected experience is a persistent Block Kit status card, real local
execution, a natural summary, and updates pushed to the same PR only if repairs
were required.

## Deployment boundary

There is still a **one-time deployment/bootstrap step** to install this version
of the worker and Atlas service on the GX10. ChatGPT in this conversation does
not have a direct shell connection to the physical GX10. Once the deployed
Builder worker is running, however, normal engineering work can be driven from
Slack without copying code/commands between ChatGPT and the GX10 terminal.

## Rollback

To disable real shell execution while keeping the old Builder path:

```dotenv
BUILDER_TERMINAL_ENABLED=false
```

To remove Atlas from Builder, restore the previous Aider model setting and
restart the worker:

```dotenv
BUILDER_AIDER_MODEL=ollama_chat/qwen3-coder:30b
```

The Builder queue, Slack routing, session model, worktrees and GitHub PR flow
remain intact; only the inference/tool path changes.
