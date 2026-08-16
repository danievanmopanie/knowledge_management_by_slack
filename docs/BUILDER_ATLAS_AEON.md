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
  Builder model credentials — this is a real boundary (the variables are
  removed before the subprocess exists), not a string match;
- the most common naive attempts to read `.env`, SSH key paths, process
  environments and common credential material are pattern-matched and
  rejected by the shell guard;
- `sudo`, `doas`, `pkexec`, user switching, host power operations, raw disk
  writes and destructive root deletion are blocked;
- `docker run` / `docker create` are blocked by default;
- systemd uses `NoNewPrivileges`, `RestrictSUIDSGID`, `ProtectHome`,
  kernel/control-group protections and a private temp area; and
- the publish boundary still requires deterministic local validation.

**Be honest with yourself about what the shell guard is.** It is a raw-string
pattern match over the command text, run before the shell interprets it. It
catches the naive case (`cat .env`, `cat ~/.ssh/id_ed25519`) but cannot catch
every form of indirection — `python -c "print(open('.env').read())"`,
string concatenation, base64, or character-code composition can all defeat a
regex in principle. Treat it as a deterrent against accidents and unsophisticated
misuse, never as the reason secrets are safe from a misbehaving or
adversarially-prompted model.

### One-time secret-permission bootstrap (the fix that actually matters)

The boundary that matters is whether the OS account running
`builder-worker.service` can read secret material off disk at all — because if
it can, every `run_shell` subprocess it spawns can too, no matter what the
regex catches.

For `.env`: `deploy/systemd/builder-worker.service` and `atlas-builder.service`
already load secrets with `EnvironmentFile=-/opt/.../.env`. That line is read
by **systemd itself (PID 1, running as root)** before it drops privileges to
`User=danie` — the resulting variables are injected straight into the spawned
process's environment. The service account does **not** need read access to
the file on disk for that to keep working, which means you can lock it down:

```bash
sudo scripts/harden_builder_secrets.sh /opt/knowledge_management_by_slack
# chown root:root .env && chmod 600 .env
```

Run this once as part of deployment, after `.env` is populated.

For SSH keys, `chmod` cannot help if the service account owns its own key —
any process running as that account can always read a key it owns, with
normal `600` permissions. The honest fix is architectural: **don't put a
personal or deploy SSH private key under this account's home directory at
all.** `push_branch()` just runs `git push <remote> <branch>`; configure the
Builder repo's `origin` remote as an HTTPS URL authenticated with
`GITHUB_TOKEN` (already required for PR creation/handoff) instead of an SSH
remote, and there is no SSH key for this account to expose.

Because file permissions can be misconfigured or reverted, the worker also
runs a startup self-check (`src/worker/secret_exposure_check.py`,
`BUILDER_SECRET_CHECK_ENABLED=true` by default): it probes whether the worker
process itself can read `.env` and common SSH key paths, and if so, logs a
`critical` line and posts a one-time warning to `#builder` mentioning the
allowlisted users. This detects a misconfigured deployment; it does not fix
one.

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
spam the thread with a new progress message for every internal step. While a
turn is `running`/`repairing`, the card also shows the last tool call executed
(e.g. `run_shell: pytest -q`), throttled to `BUILDER_PROGRESS_MIN_INTERVAL_SECONDS`
so it does not exceed Slack's practical `chat.update` rate.

The card exposes an **Open pull request** action once a PR exists, a **Cancel**
action while the turn is `running`/`repairing`, and a **Merge & Deploy** action
once the turn is `completed` and green (see below). The repository-wide rule is
documented in `docs/SLACK_UX_RULES.md`.

## Reliability: deadline, cancel, crash recovery

- **Overall turn deadline** (`BUILDER_TURN_DEADLINE_SECONDS`, default 5400s):
  Builder is a single-worker FIFO queue — one GPU, one turn at a time — so a
  runaway turn silently blocks every other queued Slack conversation without
  this. The deadline covers the whole turn (tool loop + repair + validation),
  independent of the per-call timeouts that bound a single step.
- **Cancel** (typed `cancel <task-id>` or the card's Cancel button): stops a
  queued turn immediately, or asks a running turn to stop cooperatively at its
  next safe checkpoint (between tool-loop steps or repair attempts). This is
  **cooperative, not preemptive** — it cannot interrupt an already in-flight
  shell command or Aider call, so worst-case latency to actually stop is bounded
  by the longest single configured timeout (up to `BUILDER_TEST_TIMEOUT_SECONDS`),
  not instant.
- **Crash recovery** (`BUILDER_STARTUP_RECOVERY_ENABLED`, default true): if the
  worker process dies or the GX10 reboots mid-turn, the next startup marks any
  turn still `running` as `failed` and updates its Slack card, rather than
  leaving a stale "Working on it" card forever. This is deliberately fail-safe,
  not auto-retry: retrying blindly risks opening a duplicate PR if the crash
  happened after a PR was already created but before the turn was recorded as
  succeeded. Resend the request if this happens.
- **Queue position**: when a turn is queued behind others, the initial Slack
  reply says how many turns are ahead of it instead of leaving you guessing why
  nothing has started yet.

## Merge & Deploy

Once a turn's PR is green, the persistent card can show a **Merge & Deploy**
button (behind a native Slack confirmation dialog, since merging and restarting
a live service is not easily reversible). Clicking it, as an allowlisted user:

1. re-fetches the PR's current head SHA and merges it (idempotent — if it's
   already merged, e.g. a repeat click, this skips straight to step 2);
2. **syncs the live checkout** at `BUILDER_DEPLOY_CHECKOUT_PATH` to the exact
   merged commit (`git fetch` + `git reset --hard`) — restarting a systemd
   unit only re-execs whatever is already on disk, it does not pull anything,
   so skipping this step would restart the *old* code while reporting the
   change as deployed. This refuses to run (no units are restarted) if the
   checkout has uncommitted changes or has diverged from the merged branch;
3. restarts the systemd unit(s) named in `BUILDER_DEPLOY_RESTART_UNITS`,
   deferring `BUILDER_DEPLOY_SELF_UNIT` (if configured) to last — see below;
4. checks `systemctl is-active` on each restarted unit and reports the real
   result; and
5. updates the same Slack card to a final `deployed` or `deploy_failed` state.

This is a **deterministic, non-LLM code path** (`src/worker/deploy.py`), never
reachable from the AEON tool loop — the terminal harness stays fully no-sudo by
design. Merge & Deploy runs as the account behind the main Bolt app
(`knowledge-management-by-slack.service`), a separate trust boundary from
`builder-worker.service`, using a narrowly-scoped sudoers rule
(`deploy/sudoers/builder-deploy`) that permits `sudo systemctl restart` on
exactly the named unit(s) and nothing else.

**Restarting the process that is running Merge & Deploy is a special case.**
If `BUILDER_DEPLOY_RESTART_UNITS` includes the unit that the main Bolt app
itself runs under (typically `knowledge-management-by-slack.service`), set
`BUILDER_DEPLOY_SELF_UNIT` to that same unit name. Restarting your own unit
kills the process mid-function, so nothing after that call is guaranteed to
run — without this setting, the deployment audit record and the final Slack
card update could simply never happen. When `BUILDER_DEPLOY_SELF_UNIT` is set:
every *other* configured unit is restarted and health-checked first, then the
deployment record and Slack card are finalized (this cannot itself be
health-checked from within the dying process, so the card explains that in its
message), and only then is the self-restart issued, fire-and-forget, as the
last action.

**The button stays hidden by default.** `BUILDER_DEPLOY_RESTART_UNITS` ships
empty; until you set it to your actual unit name(s), Merge & Deploy is not
offered. There is no in-repo systemd unit for the main bot process on `main`
today — `deploy/systemd/knowledge-management-by-slack.service` is provided as a
default, but you must verify it matches (or rename it to match) whatever
actually runs your production bot before enabling the button.

One-time bootstrap, alongside the existing Atlas/worker install steps:

```bash
# Validate and install the sudoers rule (edit the unit names first if yours differ)
sudo visudo -cf deploy/sudoers/builder-deploy
sudo cp deploy/sudoers/builder-deploy /etc/sudoers.d/builder-deploy
sudo chmod 440 /etc/sudoers.d/builder-deploy

# Install the main bot's systemd unit if you don't already have one
sudo cp deploy/systemd/knowledge-management-by-slack.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now knowledge-management-by-slack.service
```

```dotenv
BUILDER_DEPLOY_RESTART_UNITS=knowledge-management-by-slack.service,builder-worker.service
BUILDER_DEPLOY_SELF_UNIT=knowledge-management-by-slack.service
BUILDER_MERGE_METHOD=squash
# Leave BUILDER_DEPLOY_CHECKOUT_PATH unset to use the process's own working
# directory (/opt/knowledge_management_by_slack per the systemd units above).
```

Every Merge & Deploy click is recorded in the `builder_deployments` table
(`src/agents/builder/deployment_store.py`) — who triggered it, which PR, the
merge SHA, which units were restarted, and whether it succeeded — independent
of the ordinary Builder turn queue.

Clicking Merge & Deploy re-checks the PR's head SHA before merging (a stale
branch fails safely with a 409 rather than merging unreviewed commits), but it
does **not** re-run the GX10 validation gate. If the branch changed since the
card last went green, ask Builder to re-action the PR before merging.

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

# Reliability, live progress and Merge & Deploy — see .env.example for the
# full commented list (secret check, crash recovery, turn deadline, progress
# throttling, Merge & Deploy restart units/method).
BUILDER_SECRET_CHECK_ENABLED=true
BUILDER_STARTUP_RECOVERY_ENABLED=true
BUILDER_TURN_DEADLINE_SECONDS=5400
BUILDER_PROGRESS_MIN_INTERVAL_SECONDS=4.0
BUILDER_DEPLOY_RESTART_UNITS=
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

To disable Merge & Deploy without touching anything else, clear the restart
units — the button simply stops appearing:

```dotenv
BUILDER_DEPLOY_RESTART_UNITS=
```

To disable the startup secret-exposure check or crash recovery (not
recommended, but available for debugging):

```dotenv
BUILDER_SECRET_CHECK_ENABLED=false
BUILDER_STARTUP_RECOVERY_ENABLED=false
```

The Builder queue, Slack routing, session model, worktrees and GitHub PR flow
remain intact; only the inference/tool path changes.
