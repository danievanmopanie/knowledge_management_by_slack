# Control Plane Collaboration UX

## Status

**Proposed target experience.** This document defines how the existing `#frontend-support` / future `#frontline-support` and `#create-knowledge` experiences should evolve as the solution moves from Slack-centric agents to an OpenBot control plane exposed through CopilotKit Channels and AG-UI.

The migration should preserve the strongest parts of the current technician experience while moving agent identity, lifecycle, policy, durable coworker context and governed action execution out of Slack-specific code.

> Naming note: the current repository and environment variables use `frontend_support` / `#frontend-support`. The target human-facing name in this document is `#frontline-support`. Do not rename the Slack channel or environment variables as part of this design PR; treat that as a separate rollout decision so links, channel IDs and existing deployment configuration are not broken.

---

## 1. Experience statement

A technician should feel as if a capable support team is present in Slack, not as if several bots have been installed.

A knowledge curator should feel as if they are collaborating with a knowledgeable editorial coworker, not operating an ingestion command line inside chat.

The same coworkers should also be reachable from OpenBot native channels without duplicating agent logic. Slack is the default mobile collaboration surface; OpenBot is the control-plane and deeper coworker surface; private domain applications remain available for bulk or specialist work.

The user should not need to understand AG-UI, routing, agent registrations, MCP, LangGraph, model selection, or which process owns the Slack socket.

---

## 2. User mental model

### Today

The codebase exposes the implementation model directly to the user:

- Slack channel IDs select a Python agent in `src/bot/router.py`.
- `#frontend-support` has a dedicated Slack Socket Mode runtime in `src/bot/frontend_support_app.py`.
- Slack thread history and channel-local state are used as important parts of conversational state.
- `#create-knowledge` and knowledge uploads expose command-like controls such as `confirm <stage-id>` and `cancel <stage-id>`.
- Slack Block Kit handlers directly own important workflow decisions.

This has produced useful UX, but it couples user experience, transport, agent selection and workflow state.

### Target

Users see one coherent AI team.

- One Slack app identity represents the team.
- OpenBot owns durable coworkers, permissions, policy, audit and coworker execution.
- CopilotKit Channels receives and renders collaboration turns and native interaction components.
- An Orchestrator / Front Desk coworker decides which specialist should help.
- Specialist hand-offs may be visible as lightweight context, but never as separate bot identities that users must manage.
- Domain systems remain authoritative for incidents, knowledge, inventory and work records.
- The same work can be resumed from Slack or an OpenBot channel without recreating the agent or reimplementing its logic.

For current OpenBot, durable conversational threads are physically persisted through CopilotKit Intelligence. Treat them conceptually as part of the control-plane experience, but do not create a competing custom Slack transcript store unless a domain record requires one.

---

## 3. Surface strategy

The architectural principle **"one primary Slack entry point"** does not mean **"one Slack channel for every business process"**.

Keep purpose-built collaboration spaces because they help humans understand why a conversation exists:

| Surface | Human purpose | AI routing behavior |
|---|---|---|
| `#frontline-support` | Daily technician collaboration, troubleshooting and operational learning | Front Desk observes the thread and delegates to Frontline Support, Knowledge, Analytics or other specialists as needed |
| `#create-knowledge` | Curate, review and publish reusable operational knowledge | Front Desk delegates primarily to the Knowledge coworker and governance/publishing tools |
| Slack DM with the app | Private coaching or a private working session | Front Desk opens a private durable control-plane conversation and applies privacy boundaries |
| OpenBot channel | Continue or inspect deeper coworker work | Direct or orchestrated coworker channel using the same agent identities and tools |
| Private domain web UI | Bulk edit, detailed reconciliation, administration | Human uses structured system directly; agents may provide deep links and summaries |

The Slack app is therefore a **distribution identity**, not an agent identity.

---

# 4. `#frontline-support` target UX

## 4.1 What the channel is

`#frontline-support` remains a human collaboration channel first.

It must **not** become a ticket queue and every root message must **not** become a support case. Normal encouragement, coordination, handovers and team conversation should remain natural.

The AI team listens to the channel context, but only becomes visible when one of the following is true:

- somebody explicitly asks for help;
- a technical problem is emerging and there is useful internal evidence;
- a meaningful troubleshooting state change occurs;
- a repeat-incident pattern is detected with sufficient confidence;
- a useful expert or knowledge item can be surfaced;
- a likely resolution has been reached;
- the thread exposes a knowledge gap worth promoting to `#create-knowledge`.

The existing suppression/resume behaviour should be retained. Humans must be able to tell the assistant to stay out of a thread without stopping it from retaining the permitted thread context.

## 4.2 Thread as the unit of collaboration

When a support problem does develop, the Slack thread remains the human-facing unit of collaboration.

The backing unit is a durable control-plane thread/work item with references to:

- Slack workspace, channel and thread timestamp;
- Slack actor identity;
- OpenBot coworker/channel/thread identity;
- ServiceNow incident number when known;
- domain work item or knowledge candidate IDs when created;
- files and voice-note transcripts with provenance;
- human and coworker contributions;
- approval and audit records.

Slack message history may be supplied as channel context, but it must no longer be the authoritative session store.

## 4.3 Root-message behaviour

A top-level message should be classified into one of a small number of experience intents:

1. **Social / collaboration** — observe only.
2. **Possible support problem** — quietly establish thread context; do not immediately turn it into a form.
3. **Direct help request** — answer in the thread.
4. **Operational signal** — surface a concise proactive insight only when useful.
5. **Likely resolution** — offer resolution capture.

If the requester did not include an incident number, do not interrupt the first natural troubleshooting exchange simply to demand it. Ask for the ServiceNow incident number at the point where referenceability becomes valuable: once useful support is being generated or when resolution capture begins.

## 4.4 Progressive response pattern

Keep the current fast acknowledgement / progressive answer behaviour, but express it through Channels/AG-UI rather than Slack-specific update code.

Recommended states:

- **Checking context…** — immediate acknowledgement.
- **Using internal support history…** — optional context when retrieval is material.
- **Waiting for one detail** — clarification state with native controls.
- **Working with Knowledge coworker** — only when a hand-off would help the user understand the delay or responsibility.
- **Done** — completed response or action.
- **Approval required** — a write or consequential action is paused for a human.

Avoid a noisy play-by-play of internal agent routing.

## 4.5 Answer layout

Technician-facing answers should remain compact and mobile-readable.

Default order:

1. **Next best action** — one or two low-risk steps.
2. **Why** — the relevant evidence or reasoning.
3. **What the thread already ruled out** — only when important.
4. **Relevant internal evidence** — concise, with incident/knowledge references.
5. **Optional actions** — buttons or selections only when a real workflow action is available.

Do not dump retrieval results or show generic "agent thoughts".

## 4.6 Clarification UI

Replace avoidable free-text back-and-forth with native interaction controls where the answer space is bounded.

Examples:

- affected device type;
- location/site;
- network vs application symptom;
- error category;
- whether a proposed diagnostic was already attempted.

Use buttons/selects for bounded choices and free text only when needed. The control-plane thread receives the interaction result as another turn; Slack callback IDs must not encode business logic that only exists in Slack.

## 4.7 Evidence and repeat-incident UI

When prior evidence materially supports a next step, render a compact evidence component such as:

**Similar internal case**  
`INC0012345` · Outlook authentication · resolved 14 days ago  
**What matched:** repeated credential prompts  
**Successful fix:** cleared WAM token cache  
[View source] [Use this approach]

The card is a view over domain evidence. It must not become the source of truth.

For repeat patterns, prefer a short signal:

> This looks similar to 6 incidents from the last 30 days, 4 from the same location. The most common successful fix was X.

Then provide a link or action to inspect more detail rather than filling the thread with analytics.

## 4.8 Human contributions remain first-class

The agent must explicitly incorporate what humans in the thread have contributed:

- what was observed;
- what was tried;
- what failed;
- what fixed the issue;
- who contributed a useful action or resolution.

Attribution must flow into the knowledge graph/domain record. The Slack app should not flatten every contribution into "the bot solved it".

## 4.9 Private coaching

The existing private coaching concept should remain, but the implementation changes.

A DM with the one Slack app opens or resumes a **private control-plane conversation**. It is not a special Python route attached to a separate Slack bot.

Rules:

- private coaching context is never copied into a public thread automatically;
- the coworker may offer a sanitized summary for the technician to publish;
- public sharing requires an explicit human action;
- private coaching can use the same Frontline Support specialist and governed tools;
- audit records should show the actor and action without leaking private transcript content unnecessarily.

## 4.10 Resolution capture

When the thread appears resolved, show a native action component instead of forcing a separate command flow:

**Looks resolved**  
I can turn the confirmed fix into reusable operational knowledge.

[Capture resolution] [Not yet] [Ignore for this thread]

If `Capture resolution` is selected:

1. Resolve or request the ServiceNow incident number.
2. Build a draft from the current thread plus governed incident evidence.
3. Show the proposed structured capture:
   - symptom;
   - environment / technology;
   - failed or attempted actions;
   - successful resolution;
   - root cause only if evidence supports it;
   - contributors/resolver;
   - source incident and source thread.
4. Let the requester/resolver edit or confirm.
5. Commit the confirmed domain record through a governed tool.
6. Return a compact published/queued status with a deep link.

The Slack message is an interaction surface; the confirmed knowledge item lives in the knowledge system of record.

## 4.11 Knowledge-gap promotion

A support thread should be able to create work for `#create-knowledge` without copying the whole conversation into another channel.

When the Frontline Support coworker detects useful but uncurated learning, show:

**Knowledge gap found**  
This thread contains a reusable resolution that is not covered by formal knowledge.

[Create knowledge task] [Dismiss]

Creating the task should:

- create a knowledge candidate/work item in the domain system;
- retain links to the incident and source thread;
- assign an owner/reviewer if policy can resolve one;
- post a compact linked work card into `#create-knowledge`;
- avoid duplicating the authoritative content in Slack.

---

# 5. `#create-knowledge` target UX

## 5.1 What the channel becomes

`#create-knowledge` becomes a **knowledge workshop**, not an upload inbox.

It accepts four primary entry types:

1. a document/file or runbook to ingest;
2. a free-text knowledge idea or draft;
3. a resolution promoted from `#frontline-support`;
4. a system-created knowledge gap or review task.

The Knowledge coworker helps turn those inputs into structured, governed knowledge.

## 5.2 Remove command-line UX

The current `KnowledgeIngestAgent` exposes stage IDs and commands such as:

- `confirm <stage-id>`;
- `confirm all`;
- `cancel <stage-id>`.

Keep staged/confirmed domain semantics, but do not make users operate them as chat commands.

Target component:

**Knowledge draft staged**  
`VPN troubleshooting at Sishen`  
Source: `VPN_Runbook.docx` · Owner: @Danie  
12 sections extracted · possible duplicate found

[Review draft] [Publish] [Discard]

For multiple files, show a compact list and allow bulk review only within a safe bounded size. Bulk import remains a private/admin workflow when the volume is large.

## 5.3 Knowledge item lifecycle

Use a visible lifecycle that maps to domain state:

`Draft → Needs review → Ready to publish → Published → Superseded/Retired`

Each Slack card is only a projection of that state.

A candidate should carry:

- title;
- knowledge type;
- source and provenance;
- owner;
- reviewer/approver where required;
- linked incidents/threads;
- confidence or extraction warnings;
- duplicate/similarity warnings;
- current lifecycle state;
- last meaningful change;
- deep link to detailed editor when available.

## 5.4 Conversational editing

Users should be able to reply naturally:

- "Make the fix clearer for a field technician."
- "Remove the reboot step; that did not work."
- "Add Jacob as a contributor."
- "This only applies to Windows 11."
- "Merge this with the existing VPN article."

The Knowledge coworker converts those turns into proposed structured changes and shows the material delta before a governed write.

Do not bury important edits in a long regenerated article. Prefer a compact change summary plus a `Review draft` action.

## 5.5 Review and approval

For writes that change governed knowledge, use human-in-the-loop controls surfaced through Channels/AG-UI:

**Ready to publish**

- 1 new troubleshooting step
- 1 obsolete step removed
- applies to Windows 11 only
- linked to `INC0012345`, `INC0012411`

[Approve & publish] [Request changes] [Open full draft]

The approval action must carry the Slack human identity into the OpenBot policy gateway and then into the domain write/audit record.

## 5.6 Duplicate and conflict handling

When a new candidate overlaps existing knowledge, do not simply ingest both.

The Knowledge coworker should offer a clear choice:

- update the existing item;
- merge into a consolidated item;
- publish as a scoped variant;
- keep separate with an explicit reason;
- discard the duplicate.

The recommendation can be agent-generated, but the consequential write remains governed.

## 5.7 Deep-work escape hatch

Slack should remain the default daily interface, but it is a poor bulk editor.

Use `Open full draft` / `Open knowledge record` links for:

- large documents;
- complex comparison/merge work;
- batch imports;
- metadata-heavy governance;
- bulk retirement or publication;
- administrative policy changes.

The linked UI is private-network only where required. Returning to Slack should show the latest authoritative domain state, not a stale copy.

---

# 6. Agent routing and visible identity

## 6.1 One Slack identity

Use one Slack app identity for the AI team.

Do not expose Frontline Support, Knowledge, Inventory, Builder, Analytics and other specialists as separate Slack bot users unless a future UX test demonstrates a strong reason.

## 6.2 Front Desk / Orchestrator

Every incoming Slack turn enters the control plane through an Orchestrator / Front Desk coworker or equivalent routing policy.

Routing inputs can include:

- Slack channel purpose;
- explicit mention or requested capability;
- thread state;
- current work item type;
- available specialist skills;
- policy constraints;
- confidence/need for delegation.

Channel purpose is a strong hint, not a hard-coded agent lookup.

Example:

- `#frontline-support` defaults to Frontline Support specialist;
- a resolution-capture action delegates to Knowledge;
- a "show aging tickets" request delegates to Analytics/Work specialist;
- an inventory issue discussed in the support thread can delegate to Inventory without requiring the user to move to another bot.

## 6.3 Handoffs

Most handoffs should be invisible implementation detail.

Show a handoff only when it provides useful context, for example:

> I’m bringing in the Knowledge coworker to compare this with the formal runbook.

The response still comes from the single Slack app identity.

---

# 7. Interaction contract between surfaces and control plane

The implementation should introduce a surface-neutral collaboration envelope before replacing the Slack runtime.

## Incoming turn

Minimum semantic fields:

```text
surface                  slack | openbot | web
workspace_id             source workspace/tenant
channel_id               source collaboration channel
thread_id                source thread/conversation
actor.id                 stable mapped human identity
actor.surface_user_id    Slack user ID when applicable
actor.email              when policy permits/resolution succeeds
text                     normalized user text
files[]                  source references + metadata
voice_transcript         optional transcript with provenance
interaction              button/select/form response when applicable
source_links[]           incident/thread/domain references
surface_context          channel purpose, locale, capabilities
```

## Outgoing experience events

The agent/control plane may emit:

```text
message_delta            progressive answer text
status                    working / waiting / done / approval_required
component                 evidence, clarification, draft, comparison, summary
approval_request          governed human-in-the-loop action
handoff_notice            optional visible specialist delegation
tool_result               user-meaningful outcome, not raw tool payload
deep_link                 OpenBot/domain view
error                     safe, referenceable failure state
```

Slack Block Kit is one renderer for these events. OpenBot native components are another. Business rules must not be encoded only in Slack callback IDs.

---

# 8. Identity, policy and audit UX

Every consequential action must retain the real human actor.

Required chain:

```text
Slack user
  → Channels identity mapping
  → OpenBot actor
  → coworker delegation
  → policy decision
  → governed tool call
  → domain write under attributable actor/context
  → audit outcome
```

UX rules:

- read-only assistance should feel low-friction;
- risky or consequential writes should pause for approval according to policy;
- approval cards must state **what will change**, **where**, and **on whose behalf**;
- denied actions should explain the relevant boundary in human language;
- audit IDs may be available through details/deep link, but should not clutter ordinary support conversations;
- never ask technicians to understand service accounts or backend connector identities.

---

# 9. State ownership

| State | Authority |
|---|---|
| Coworker identity, role, grants, policy | OpenBot control plane |
| Durable conversation/thread memory | Control-plane experience; current OpenBot implementation uses CopilotKit Intelligence persistence |
| Slack channel/thread coordinates | Collaboration-surface reference only |
| Incident status and incident data | ServiceNow/domain system |
| Knowledge item lifecycle/content | Knowledge domain system |
| Inventory and work records | Respective domain systems |
| UI progress state | Channels/AG-UI interaction state; disposable/reconstructable |
| Audit of governed actions | OpenBot policy/audit plus domain audit where required |

Do not create new Slack-only databases that become shadow systems of record.

---

# 10. Mobile-first rules

Slack mobile is the default frontline client.

1. Put the next action in the first screenful.
2. Prefer 1-3 buttons over command syntax.
3. Avoid wide tables; use fields/sections or a deep link.
4. Keep evidence cards compact and expandable.
5. Use one evolving progress message rather than several status posts.
6. Keep approval language short but explicit.
7. Preserve voice-note ingestion.
8. Never require a desktop browser for normal troubleshooting or simple knowledge confirmation.
9. Use private web/OpenBot views only for genuinely deep or bulk work.
10. Test every major flow on Slack mobile before calling the migration complete.

---

# 11. Migration plan

## Phase 0 — Preserve current UX contracts

Before changing runtimes, codify tests for the behaviours worth keeping:

- normal chat does not trigger noisy intervention;
- technical problems can become collaborative support threads;
- voice notes work;
- clarification works;
- private coaching stays private;
- resolution detection works;
- captured knowledge retains incident/thread/contributor provenance;
- suppress/resume works;
- errors remain safe and referenceable.

## Phase 1 — Extract a surface-neutral interaction layer

Refactor Slack-specific handlers so domain actions can return semantic UI/events instead of calling Slack directly.

Targets:

- clarification request;
- evidence presentation;
- resolution-capture proposal;
- knowledge draft/review/publish proposal;
- approval request;
- deep-link/status response.

Do not change the user-visible Slack behaviour materially in this phase.

## Phase 2 — Register control-plane coworkers

Create OpenBot coworkers for at least:

- Front Desk / Orchestrator;
- Frontline Support;
- Knowledge.

Expose existing agent logic behind AG-UI-compatible endpoints or wrappers. Give each coworker only the required tools, credentials and policy.

## Phase 3 — Introduce one CopilotKit Channels runtime

Add a long-running Channels listener that:

- receives Slack turns;
- identifies the human actor;
- normalizes thread/file/interaction context;
- delivers turns to the OpenBot control plane over AG-UI;
- renders returned components into native Slack UI.

Run this against a controlled test channel/app first. Do not leave two consumers handling the same production Slack events.

## Phase 4 — Move durable conversation ownership

Stop treating Slack history retrieval or local collaboration stores as authoritative agent memory.

Migrate only durable domain facts that must survive independently:

- incident/thread linkage;
- confirmed contributor attribution;
- knowledge candidates;
- approvals and audit references.

Conversation memory belongs to the control-plane thread.

## Phase 5 — Replace command UX in `#create-knowledge`

Retain staging semantics but replace stage IDs and magic commands with interactive draft/review/publish components.

## Phase 6 — Decommission legacy Slack runtimes

Once parity is proven:

- retire `src/bot/frontend_support_app.py` as a standalone Socket Mode runtime;
- remove channel-ID-to-agent selection from `src/bot/router.py`;
- reduce `src/bot/app.py` to legacy/compatibility code and then remove it;
- migrate reusable Block Kit content to Channels components/renderer definitions;
- remove Slack-specific workflow state that is no longer authoritative.

---

# 12. Current-code migration map

| Current code | Keep | Change |
|---|---|---|
| `src/agents/frontend_support/*` | Retrieval, troubleshooting behaviour, evidence discipline, collaboration rules | Expose as a control-plane specialist over AG-UI; remove assumptions that Slack is its runtime/state owner |
| `src/agents/knowledge_ingest/agent.py` | Validation, staging, governed commit, provenance | Convert command UX into Knowledge coworker tools/actions; surface stages as interactive drafts |
| `src/bot/router.py` | Routing intent as a concept | Remove hard channel-ID → agent singleton mapping; route through Front Desk/control-plane delegation |
| `src/bot/frontend_support_app.py` | Proactive collaboration, progress, voice, clarification, capture, private lane | Replace dedicated Bolt/Socket Mode runtime with Channels/AG-UI surface handlers |
| `src/bot/app.py` | Existing working behaviour during migration | Stop treating Slack as agent runtime and thread memory; retire after Channels parity |
| `src/bot/blockkit/*` and frontend interactivity modules | Useful UI patterns and decisions | Re-express as Channels components/semantic actions; keep Slack-specific rendering at the edge |
| `src/knowledge/*` | Structured knowledge, graph, retrieval, provenance | Remain domain services/tools behind governed control-plane actions |

---

# 13. Acceptance criteria

The migration is successful when all of the following are true:

### Architecture

- Slack no longer selects Python agent instances by channel ID.
- No specialist requires its own Slack bot or Slack runtime.
- The same Frontline Support and Knowledge coworkers can be used from Slack and OpenBot.
- Human identity is preserved from Slack through policy and domain writes.
- Domain systems remain authoritative for structured records.

### `#frontline-support`

- normal human conversation remains natural and mostly uninterrupted;
- support issues use threads without forcing every root message into a case;
- voice notes, proactive help, clarification and private coaching still work;
- repeat incidents and formal knowledge are surfaced compactly;
- human contributions remain attributable;
- resolution capture is interactive and mobile-friendly;
- knowledge gaps can be promoted without copying authoritative content between channels.

### `#create-knowledge`

- users no longer need stage IDs or confirmation commands for normal work;
- drafts show provenance, owner, status and conflicts;
- edits can be requested conversationally;
- consequential publication requires governed approval where policy says so;
- published knowledge links back to its evidence and contributors;
- bulk/deep work has a private power-user path.

### UX quality

- one Slack app identity feels like one coherent team;
- specialist routing is understandable when visible but usually unobtrusive;
- primary flows fit comfortably on Slack mobile;
- progress and approval states are clear;
- changing collaboration surface does not require rewriting agent logic.

---

# 14. Explicit non-goals for this design PR

This document does **not**:

- rename the production Slack channel;
- install OpenBot;
- add CopilotKit credentials;
- migrate Slack tokens;
- remove the current Bolt runtimes;
- change the knowledge storage model;
- rewrite the existing Frontline Support agent;
- select the final Front Desk display name/persona;
- decide whether CopilotKit Intelligence will be managed or self-hosted in production.

Those should follow as implementation PRs after the interaction contract and migration sequence are accepted.
