# Frontend Support clarification UX

Frontend Support may ask up to **three** clarification questions before running the normal evidence retrieval and response flow. Clarification is used only when a missing discriminator materially changes retrieval or diagnosis.

## Principles

- Do not turn Slack into a ticket form.
- Ask one focused question at a time.
- Offer 2-5 high-value choices when useful.
- Always allow a normal free-text thread reply.
- Stop after three clarification rounds and give the best answer available.
- Store selected/free-text clarification as part of the support thread evidence so later retrieval, resolution capture and knowledge creation see the enriched context.
- Do not ask for information already present in the thread.

## Example

Root message: `App keeps timing out`

Round 1: `Which application is affected?`
- Microsoft Teams
- Outlook
- SAP
- Other / type it

Round 2 (if still needed): `When does the timeout happen?`
- Opening the app
- Signing in
- During normal use
- During calls / meetings
- Other / type it

After enough context is captured, Frontend Support runs the normal thread-aware RAG/LLM flow.

## Implementation

- `src/agents/frontend_support/clarification.py`: bounded clarification state, rules and Block Kit rendering.
- `src/bot/frontend_support_app.py`: invokes clarification before retrieval and consumes free-text answers.
- `src/bot/frontend_interactivity.py`: handles option clicks, records them into the collaborative thread, asks a next clarification when needed, and then resumes normal support routing.
- `tests/test_frontend_clarification.py`: regression coverage including the three-round cap and free-text escape hatch.
