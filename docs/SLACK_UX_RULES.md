# Slack UX rules

These rules apply to every Slack agent in this repository, including future
channels and workflows.

## Conversation first

Slack should feel like Slack, not like a command-line interface embedded in a
channel. Users should be able to write normal sentences, short follow-ups and
thread replies. Magic prefixes such as `build:`, `ask:` or `run:` must not be a
requirement for ordinary agent use. Deterministic commands may remain as
optional escape hatches for operations such as `status` and `cancel`.

## Block Kit rule

**Use normal Slack messages for conversation. Use Block Kit when structure or
interaction materially improves the user's ability to understand state or take
action.**

Block Kit is expected for:

- durable workflow state and progress;
- approvals, confirmations and choices;
- validation/test results and warnings;
- links to created artefacts such as pull requests;
- forms, selectors and actions;
- compact summaries where labelled fields improve scanability.

Block Kit should not turn every conversational answer into a card. A helpful
explanation, question or troubleshooting reply should normally remain ordinary
thread text.

For long-running work, prefer **one persistent status card updated in place**
over a stream of progress messages. The card must always include meaningful
plain-text fallback/notification text for accessibility and non-Block-Kit
clients.

## Builder-specific application

`#builder` follows the same pattern:

1. the user speaks naturally;
2. the Builder acknowledges conversationally;
3. one Block Kit card represents durable execution state;
4. the same card is updated for working, repair, validation, failure or PR-ready
   state;
5. repo questions that require no code change return a normal conversational
   answer and do not create a PR;
6. when code changes are made, the final card exposes the pull request action
   only after local validation passes.
