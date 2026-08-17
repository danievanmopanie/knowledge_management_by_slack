# Builder trusted Slack ingress

The Builder Slack runtime is intentionally separate from the shared Slack bot. It listens only to `CHANNEL_BUILDER_AGENT` and treats ordinary messages as natural engineering turns; `build:` remains backward-compatible syntax, not a requirement.

## Required environment

```env
CHANNEL_BUILDER_AGENT=C0123456789
BUILDER_AGENT_ALLOWED_USER_IDS=U0123456789
BUILDER_TRUSTED_SLACK_SENDER_IDS=A_TRUSTED_APP,B_TRUSTED_BOT
```

`BUILDER_AGENT_ALLOWED_USER_IDS` is for humans who may use Builder. `BUILDER_TRUSTED_SLACK_SENDER_IDS` is an explicit trust list for external Slack app/bot identities that may hand work into Builder. Do not use a wildcard and do not add the Builder bot's own identity.

## UX contract

Natural requests include `Check my PRs, please`, `Why are the tests failing?`, and `Take PR #79 and run it on the GX10`.

Accepted work gets a short conversational acknowledgement. The acknowledgement must not promise that every task will create a pull request. The background worker owns the durable progress card and updates it in place while work is active.

PR-list answers must use the authoritative GitHub tool result. The explicit `list_result_count` metadata is the source of truth for the total; the model must not estimate or recount it.

## Isolation and loop prevention

- Events outside `CHANNEL_BUILDER_AGENT` are ignored.
- Arbitrary bot/app messages are rejected.
- The Builder bot's own output is not trusted, preventing recursive tasks.
- Slack redeliveries are deduplicated by `client_msg_id` or channel/message timestamp.
- Frontend Support, Create Knowledge and Inventory keep their existing listener protections.

## Deployment

After CI and the GX10 local gate are green:

```bash
sudo cp deploy/systemd/builder-slack-agent.service /etc/systemd/system/kms-builder-slack-agent.service
sudo systemctl daemon-reload
sudo systemctl enable --now kms-builder-slack-agent.service
```

Do not run the old generic listener against the Builder channel at the same time. During cutover, verify exactly one active listener owns `CHANNEL_BUILDER_AGENT`.

## Acceptance

1. `Check my PRs, please` works without `build:` and receives a natural acknowledgement.
2. The PR total exactly matches the number returned by GitHub.
3. Slack output contains no escaped heading, bold, table or list markers.
4. Mentions and thread replies preserve recent Builder context.
5. Approved external-agent/app-assisted handoffs are accepted; untrusted bot/app events are rejected.
6. One Slack message cannot produce two Builder tasks after reconnect/redelivery.
