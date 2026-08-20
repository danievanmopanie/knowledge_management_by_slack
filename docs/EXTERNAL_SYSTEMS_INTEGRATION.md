# External Systems Integration (Snipe-IT + Taskwondo)

Slack is the primary access point for techs. Snipe-IT (hardware assets) and
Taskwondo (work items) are the **external systems of record**. Agents call those
systems with a single service-account token but **stamp the real human** on every
action, so a tech sees their own name inside the tools — not a generic bot.

```
Tech (Slack mobile/desktop)
        ↓
Inventory / Work Management agents
        ↓  (service-account token + resolved human identity)
Snipe-IT  &  Taskwondo   ← action recorded as the real person
```

## Identity mapping

Source of truth for *who* someone is = the Slack user. A small mapping table
(`identity_map` on the platform SQLite DB) links a Slack user to their external
accounts, preferring **email** as the join key.

- `src/identity/store.py` — `IdentityStore` (SQLite table: `slack_user_id → email,
  display_name, snipeit_user_id, taskwondo_user_id`).
- `src/identity/resolver.py` — `IdentityResolver.resolve(context, want_snipeit=…,
  want_taskwondo=…)`. Resolution order per system: (1) cached mapping, then
  (2) if `IDENTITY_AUTO_LINK_BY_EMAIL` is on and an email is known, look the user
  up by email in the external system and cache the result.
- Techs link themselves once from Slack: `link me your.name@company.com`.
- Every external write is stamped `Requested by <name> (Slack <user_id>)`.

## Service accounts / configuration

One privileged API token per system (never individual tech tokens). See
`.env.example`:

| Setting | Purpose |
|---------|---------|
| `SNIPEIT_BASE_URL`, `SNIPEIT_API_TOKEN` | Snipe-IT site root (+`/api/v1`) and service token |
| `SNIPEIT_DEEP_LINK_BASE` | Private (Tailscale) base for deep links in Slack replies |
| `TASKWONDO_BASE_URL`, `TASKWONDO_API_TOKEN` | Taskwondo site root and `twk_` API key |
| `TASKWONDO_DEFAULT_PROJECT` | Project key for new work items when unspecified |
| `TASKWONDO_DEEP_LINK_BASE` | Private base for work-item deep links |
| `IDENTITY_AUTO_LINK_BY_EMAIL` | Match unmapped users by email on first use |

Clients: `src/integrations/snipeit_client.py`, `src/integrations/taskwondo_client.py`
(module-level functions mirroring the existing `github_client.py` pattern).

## Slack commands

Inventory channel (asset custody runs against Snipe-IT when configured):

- `checkout asset A-1042 to me`
- `checkout asset A-1042 to jane@company.com`
- `checkin asset A-1042`
- `asset status A-1042`
- `link me <email>`

Work Management channel (Taskwondo):

- `create task <title>` / `create bug <title>` / `create task <title> in <PROJECT>`
- `my tasks`
- `task OPS-7 status in_progress`
- `link me <email>`

## Deep links (private access)

Replies include a deep link (e.g. `https://snipeit.tailnet/hardware/42`) for the
occasional deep dive / bulk edit. Run Snipe-IT and Taskwondo on the GX10 reachable
only via Tailscale/WireGuard; set the `*_DEEP_LINK_BASE` values to the tailnet host
so mobile devices on the same private network open the UIs securely.

## Scope and follow-ups

- Snipe-IT is now the system of record for **asset custody** (checkout / checkin /
  status). The remaining local serialized-asset verbs (`issue` / `return` / `store`
  / `repair` under `src/inventory/`) are not yet migrated and continue to use the
  local store; migrating them to Snipe-IT is a follow-up.
- App-layer auto-population of the Slack profile email into `RequestContext`
  (via `users.info`, requires the `users:read.email` scope) is a follow-up; today
  email is established with `link me <email>` or passed in context.
- Optional SSO (SAML for Snipe-IT / OIDC for Taskwondo) is an ops concern and not
  required for the Slack-first model.
