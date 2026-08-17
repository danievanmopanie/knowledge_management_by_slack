# Background Activity UX Rule

Any user-triggered operation that may take more than a brief moment must remain visibly alive in the user interface.

This is a project-wide design rule, not a Builder-specific convention.

## Required behaviour

1. **Acknowledge immediately.** Tell the user that the request was accepted and give the task/turn identifier when one exists.
2. **Use one durable progress surface.** Prefer one Slack Block Kit message/card that is updated in place. Do not spam a thread with operational status messages.
3. **Show meaningful phases.** Use observable phases such as queued, inspecting, executing, editing, validating, repairing, publishing, completed and failed.
4. **Heartbeat during quiet phases.** If a phase has not changed for `BACKGROUND_HEARTBEAT_SECONDS`, refresh the same progress surface with a liveness indicator, elapsed time and time since the last phase activity.
5. **Always terminate the state.** Completion, cancellation, timeout and failure must replace the working state. Never leave a stale `Working…` card behind.
6. **Keep progress safe.** Progress describes observable execution only. Never expose chain-of-thought, prompts, secrets, credentials or noisy raw logs.
7. **Status commands are an escape hatch, not the UX.** Users should not need to type `status <id>` simply to learn whether a process is alive.
8. **Avoid duplicate responders.** Dedicated Slack runtimes must only update progress for the channels/workflows they own.

## Reusable implementation

`src.ux.background_activity.BackgroundActivity` provides immediate phase callbacks plus periodic heartbeats for blocking work. The caller owns the actual UI update so the helper works for Builder, support workflows, ingestion and future agents.

The callback should update one durable message in place. `ActivitySnapshot` supplies:

- phase
- safe user-facing summary
- elapsed seconds
- seconds since the last phase activity
- whether the update is a heartbeat

UI failures must not crash the underlying work.

## Builder reference implementation

Builder stores `progress_message_ts` with each turn. The worker uses that timestamp to update a single Block Kit card throughout the turn. Long model/tool execution, validation and repair phases are wrapped in `BackgroundActivity`, so a multi-minute operation continues to visibly report that it is alive.

The card shows the turn ID, branch, elapsed time, validation/repair state and a periodic `Still working` heartbeat. Terminal success/failure replaces the working card.

## Applying this elsewhere

When adding a new background workflow:

- identify the user-visible durable status surface;
- acknowledge before enqueueing/dispatching expensive work;
- wrap long blocking phases in `BackgroundActivity` or an equivalent async heartbeat;
- update phase names when observable work changes;
- stop the heartbeat before the terminal update;
- test immediate acknowledgement, heartbeat, terminal replacement and channel/workflow isolation.
