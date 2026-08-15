# Slack human-interaction UX standard

This repository uses a **button-first / control-first** interaction model for human-in-the-loop Slack workflows.

## Rule

Whenever an agent needs a human choice, approval, confirmation, selection, or structured input before it can continue, the primary Slack UX must be a Block Kit control. Do not require the user to type a command when Slack can represent the choice directly.

Typed commands may remain available as a fallback for accessibility, automation, troubleshooting, or power users, but they are not the primary interaction.

## Control choice

- Binary or one-step decisions: buttons.
- Small finite choices: static select / radio buttons.
- People: users_select or multi_users_select.
- Dates: datepicker/timepicker.
- Structured multi-field input: modal.
- Free-form clarification: plain-text input in a modal when practical; otherwise a normal thread reply.

## Interaction behavior

1. Acknowledge Slack actions immediately.
2. Route the action through the same agent/domain command path as typed fallbacks so authorization, validation, and audit behavior stay identical.
3. On success, remove/disable stale controls or update the card in place.
4. Prefer evolving one persistent card through staged -> queued -> running -> complete rather than posting duplicate status messages.
5. Authorization must be checked server-side; hiding a button is not authorization.
6. Errors/denials should not destroy a valid action card. Prefer an ephemeral error to the actor when the workflow can still be completed by an authorized user.
7. Action IDs belong in the central Block Kit ID registry where practical.
8. Reuse `src.bot.blockkit.decisions` for standard decision rows.

## Current implementations

- Inventory staged PO/receipt/stock-count flows: Confirm / Cancel buttons.
- Frontend Support resolution capture: Capture knowledge / Not now buttons.
- Create Knowledge staged uploads: Build knowledge / Cancel buttons; the staged message becomes the persistent build-progress card after approval.

This standard applies to new Slack workflows and to existing workflows whenever they are touched or found to require typed human commands to proceed.
