# Clarification behavior examples

These examples define when Frontend Support should and should not interrupt the normal RAG/LLM flow.

## Should clarify

`App keeps timing out`

Reason: application identity materially changes retrieval. Ask application first, then failure stage if still missing.

`Application errors after a few minutes`

Reason: application identity is missing; ask one focused discriminator.

## Should answer immediately

`Outlook repeatedly prompts for credentials after a password reset`

Reason: application, symptom and relevant recent action are already present.

`Microsoft Teams times out during calls`

Reason: application and failure stage are both present.

`User's screen shows BSOD`

Reason: high-signal symptom; start with safe troubleshooting and request stop code only if it is required for the next step.

## Three-round ceiling

Frontend Support must never force more than three clarification rounds before producing its best available answer. Free-text thread replies can satisfy any pending clarification question.
