# Frontend Support live pilot fixes

These fixes were driven by the first live Slack pilot of the standalone Frontend Support agent.

## Natural thread context

A technician should not need to restate an issue after opening a thread. Terse turns such as `help with this`, `tried that`, `still broken`, `what next?` and explicit `@Frontend Support` requests are resolved against the stored root message and preceding attributed contributions.

The standalone runtime now records an explicit mention into the same collaboration memory and builds the retrieval query from the complete thread before asking the support agent.

## Broader natural support detection

The collaboration classifier remains deliberately conservative, but the Slack runtime has a field-support safety net for ordinary device/peripheral language including Bluetooth, headset, pairing, connection and common endpoint accessories. This lets a root message such as `User says their bluetooth headset isn't connecting` invoke assistance without requiring ticket-like wording.

## Evidence discipline

The support prompt now treats historical incident notes as evidence rather than truth, ignores mismatched evidence, avoids calling a loosely related historical fix `proven`, and no longer dumps every retrieved incident into a footer.

Formal governed knowledge can still be shown when it actually supports the answer.

## General guidance fallback

When no strong internal match exists, the agent can still help from general technical knowledge. It labels this clearly as general guidance, does not fabricate internal evidence, and prioritises one or two reversible diagnostic steps over a generic checklist.

## Latency instrumentation

The runtime logs end-to-end request latency by lane (`public`, `mention`, `private`) and the support agent logs evidence-retrieval and LLM-generation timings. This allows the live pilot to identify whether delay is caused by retrieval, the local LLM or Slack/runtime overhead before optimising model choices.
