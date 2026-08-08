# Work Management

How `#work-management` works: a LangGraph graph for live conversation, plus the Routine Keeper's
scheduled checks running independently on systemd timers. See `docs/BRS.md` §6.4 and §8 for the
formal requirements and architecture diagram this implements.

## The graph

Six nodes, entered through `orchestrator`:

```
orchestrator ─┬─> work_approver ──────────────────────> END
              ├─> planner -> scheduler -> resource_coordinator -> END
              ├─> work_executioner ───────────────────> END
              └─> END   (help / status / lookup / new request)
```

`orchestrator` does cheap, deterministic intent routing (regex/keyword matching - no LLM call) and
handles read-only intents (help, status, item lookup) and new-request intake directly. The only
node that calls the local LLM is `planner`, to turn a rough description into a short what/how/
duration plan. Everything else is deterministic - approve/reject/lock decisions, day-picking,
resource confirmation, and execution status transitions are all template responses, not
LLM-generated. This is deliberate: enforcement/status-transition logic should be predictable, not
subject to model variance.

Source: `src/agents/work_management/` (`state.py`, `nodes.py`, `graph.py`, `agent.py`,
`reactions.py`). Business logic and persistence live one level up in `src/work_management/`
(`models.py`, `store.py`, `routine_keeper.py`) so they're usable from both the live bot and the
scheduled scripts without going through Slack at all.

## Talking to it

No special syntax is required to raise a request - just describe the problem in `#work-management`
and it becomes a numbered item (`WI-0001`, ...) awaiting approval.

Once an item has an ID, two ways to act on it - reactions are the primary path (see BRS FR-33/34),
text is the fallback:

| Action | Reaction | Text |
|---|---|---|
| Approve | ✅ `white_check_mark` | `approve WI-0007` |
| Reject | ❌ `x` | `reject WI-0007 <reason>` |
| Start | 🚧 `construction` | `start WI-0007` |
| Done | ✔️ `heavy_check_mark` | `done WI-0007` |
| Blocked | ⛔ `no_entry` | `blocked WI-0007 <reason>` |
| Lock next week's plan | - (multi-item, text only) | `lock the plan` |
| Plan an approved item | - | `plan WI-0007 [for <day>]` (chains planner → scheduler → resource_coordinator) |
| Status | - | `status` (backlog + today) or `status WI-0007` |

**Reactions are only honoured on a work item's root message** - the message that created it
(`WorkItem.slack_channel` / `slack_ts`). Reacting on any other message in a thread is a silent
no-op. Raise new requests as top-level messages in `#work-management`, not as thread replies, so
this anchor is unambiguous. If the root message has scrolled out of easy reach, the text commands
work regardless of which message you're looking at - use those instead of hunting for the original
message to react on.

## Permissions (v1)

Only the configured `Work Approver` (by Slack user ID, in the `roles` table) can approve, reject,
or lock - reactions or text commands from anyone else are acknowledged but not applied. Execution
status changes (start/done/blocked) are **not** identity-restricted in v1 - anyone can update
status. This matches the relative stakes: an approval is a real decision, a status update is not.
If `roles` has no `slack_user_id` configured yet for Work Approver, the restriction doesn't apply
(nothing blocks on unconfigured state) - configure it once you know who that is.

```python
from src.work_management.store import WorkManagementStore
from src.work_management.models import Role
from src.core.config import settings

store = WorkManagementStore(settings.work_management_db_path)
store.upsert_role(Role(role_name="Work Approver", primary_person="<name>", slack_user_id="U0123ABC"))
store.upsert_role(Role(role_name="Work Executioner", primary_person="<name>", slack_user_id="U0456DEF"))
```

## The Routine Keeper

Four checks, each idempotent per day (won't double-post if a timer misfires or a script is re-run
manually), each escalating to a named role rather than acting itself:

| Check | Schedule | Escalates when | To |
|---|---|---|---|
| Weekly planning locked | Fridays 17:00 | Something's scheduled for next week but not locked | Work Approver |
| Approval SLA | Weekday mornings 08:00 | A request has sat `Submitted` past `WORK_APPROVAL_SLA_HOURS` | Work Approver |
| Daily execution | Weekday middays 12:00 | Today's locked work hasn't been started | Work Executioner |
| Weekly accountability review | Mondays 08:00 | Always posts - done/blocked/in-progress/not-started rollup for the week just finished | (informational) |

These run **outside the Slack message path entirely** - triggered by systemd timers, not by
anything said in Slack - and post via the same `publish_report_to_channel()` already used for
Frontend Support's daily/weekly reports (`src/reporting/publisher.py`). They never approve, plan,
schedule, resource, lock, or execute anything themselves (BRS FR-35) - only read the Work
Management store and post.

### Manual run

```bash
python scripts/check_weekly_planning.py
python scripts/check_approval_sla.py
python scripts/check_daily_execution.py
python scripts/run_weekly_accountability_review.py
```

### Enable on the GX10 (systemd)

```bash
sudo cp deploy/systemd/wm-*.service deploy/systemd/wm-*.timer /etc/systemd/system/
# Edit WorkingDirectory / User / venv paths as needed

sudo systemctl daemon-reload
sudo systemctl enable --now wm-planning-check.timer
sudo systemctl enable --now wm-approval-sla.timer
sudo systemctl enable --now wm-execution-check.timer
sudo systemctl enable --now wm-weekly-review.timer

sudo systemctl list-timers | grep wm-
```

Ensure the bot is invited to `#work-management` and `CHANNEL_WORK_MANAGEMENT` is set in `.env`.

## Data

Single SQLite file at `WORK_MANAGEMENT_DB_PATH` (default `./data/work_management/workmgmt.db`),
created automatically on first use - schema lives in `src/work_management/store.py` (`SCHEMA`
constant), not as a separate `.sql` file, so it's always in sync with the Python that reads/writes
it. Three tables: `work_items` (the shared record every node reads/writes), `roles` (who's
accountable for what, with backups), `routines_log` (the Routine Keeper's own audit trail - lets
you see whether the cadence is actually holding up over time, not just this week).

This is a separate concern from LangGraph's own checkpointer (BRS §9, conversation/thread state) -
don't point both at the same file expecting them to interact; they don't need to.

## Known v1 limitations

- Reactions anchor to a single message per item (see "Talking to it" above) - no multi-message
  tracking yet. Text commands are the workaround.
- The Scheduler's clash detection counts items on the same day; it doesn't check per-person
  double-booking (there's no employee roster in this system yet).
- The Resource Coordinator does a keyword-based unavailability check (`not available`,
  `out of stock`, ...) against the plan/description/message text - it doesn't integrate with
  `src/inventory/` yet. That's a natural next step once both systems have real data flowing.
