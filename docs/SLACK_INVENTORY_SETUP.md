# Slack Inventory Pilot Setup

This repository uses Slack Bolt in **Socket Mode**. No public HTTP endpoint is required for the pilot.

## 1. Create the Slack app

Create a Slack app from scratch in the target workspace.

Recommended app name: `Inventory Agent`.

## 2. Enable Socket Mode

In **Settings → Socket Mode**:

1. Enable Socket Mode.
2. Create an app-level token with the `connections:write` scope.
3. Copy the generated `xapp-...` value into `SLACK_APP_TOKEN`.

## 3. Add bot token scopes

In **OAuth & Permissions → Bot Token Scopes**, add:

- `app_mentions:read`
- `chat:write`
- `channels:history`
- `channels:read`
- `files:read`

If the inventory pilot will run in private channels, also add:

- `groups:history`
- `groups:read`

Reinstall the app to the workspace after changing OAuth scopes.

Copy the resulting `xoxb-...` token into `SLACK_BOT_TOKEN`.

## 4. Enable event subscriptions

In **Features → Event Subscriptions**, enable events and subscribe the bot to:

- `app_mention`
- `app_home_opened`
- `message.channels`

For a private inventory channel, also subscribe to:

- `message.groups`

The application currently acts on normal messages automatically only for the configured knowledge-upload channel. Inventory commands should be sent as `@Inventory Agent ...` mentions during the pilot.

## 5. Get the signing secret

Copy **Basic Information → App Credentials → Signing Secret** into:

`SLACK_SIGNING_SECRET`.

Socket Mode does not require a public request URL, but the Bolt application still initializes with the signing secret.

## 6. Create the inventory channel

Create a pilot channel, for example:

`#inventory-agent-pilot`

Invite the Slack app to the channel.

Copy the Slack channel ID (not the channel name) into:

`CHANNEL_INVENTORY=C...`

The inventory pilot only requires `CHANNEL_INVENTORY`. The other agent channels may remain blank initially; startup will log warnings for them rather than block the inventory pilot.

Do not assign the same Slack channel ID to more than one agent. Startup validation rejects ambiguous channel routing.

## 7. Configure `.env`

Copy `.env.example` to `.env` and configure at minimum:

```text
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...
SLACK_SIGNING_SECRET=...
CHANNEL_INVENTORY=C...
INVENTORY_INTERACTIVE_ALLOWED_USER_IDS=U0123456789,U9876543210
```

For inventory-only testing, the frontend-support, work-management and knowledge-upload channel variables can be left blank.

The inventory database uses the configured platform database path. Keep the `data/` directory persistent when running the application in a container or service.

## 8. Start the bot

From the repository environment:

```bash
python main.py
```

Startup performs a Slack readiness preflight. It will refuse to start when:

- Slack tokens are missing/placeholders or have invalid token prefixes;
- `CHANNEL_INVENTORY` is missing or malformed;
- the same Slack channel is mapped to multiple agents.

## 9. First smoke test

In the inventory pilot channel:

```text
@Inventory Agent help
```

Then create the minimum governed master data before attempting stock movements. A representative sequence is:

```text
@Inventory Agent create location SITE-A type site site SITE-A name Pilot Site
@Inventory Agent create location STORE-A type storeroom site SITE-A name Main Store parent SITE-A
@Inventory Agent create item MOUSE-01 tracking quantity class peripheral name USB Mouse reorder point 5 quantity 20
@Inventory Agent create customer EMP-42 type employee name Test User
@Inventory Agent inventory summary
```

For serialized-asset testing, create the appropriate item master first and then exercise the PO/receiving flow rather than inserting asset rows manually.

## 10. File-based receiving test

The receiving workflow is intentionally staged:

1. attach one PO/quote document and mention the bot with `create po <PO> supplier <supplier-id>`;
2. review the preview and run `confirm po PO-STAGE-...`;
3. attach one delivery note/photo and use `receive <PO> at <location>`;
4. review reconciliation results and use `confirm receipt RCV-...`.

OCR/extraction output cannot directly change authoritative inventory. Confirmation is always a separate action.

## 11. Enable interactivity (required for the Block Kit UI — App Home, modals, buttons)

The bot now ships an App Home dashboard, fill-in-the-blank modals (Issue Asset, Return
Asset, Create Customer, Create Location), and Confirm/Cancel buttons on staged PO/receipt/
count replies. None of this works until the following is done **manually** in the Slack
app admin dashboard at https://api.slack.com/apps — no code change can do this for you.

1. Select the inventory bot's app in https://api.slack.com/apps.
2. **Features → Interactivity & Shortcuts**: toggle **On**. Because this app runs in
   Socket Mode, no Request URL needs to be filled in — Bolt receives button/modal events
   over the existing socket connection automatically once this is enabled.
3. **Features → App Home**: toggle **Home Tab** on.
4. **Features → Event Subscriptions**: ensure `app_home_opened` is included under bot
   events. The view APIs and Slack-populated `users_select` element do not require
   `views:write` or `users:read`; the existing `chat:write` scope is used when an action
   posts its result to the inventory channel.
5. Add the Slack user IDs allowed to run App Home inventory operations to `.env`:

   ```text
   INVENTORY_INTERACTIVE_ALLOWED_USER_IDS=U0123456789,U9876543210
   ```

   App Home remains visible without this setting, but its inventory actions are disabled.
6. Reinstall the app only if you changed event subscriptions or existing OAuth scopes.
7. Restart the bot process so it picks up the updated configuration.
8. Sanity check: open the bot under Slack's left sidebar **Apps**. The **Home** tab should
   show a dashboard with buttons (Issue Asset, Return Asset, New Customer, New Location,
   Inventory Summary) the first time you open it after the bot restarts.

No Socket Mode / app-level token changes are needed — the existing `connections:write`
app-token scope already covers receiving interactivity events over the socket.

## Pilot safety recommendation

Start with a dedicated test workspace/channel or clearly identified pilot data. Do not load the organisation's full production inventory before validating the end-to-end Slack flows, permissions, backups and operational procedures with a small representative dataset.
