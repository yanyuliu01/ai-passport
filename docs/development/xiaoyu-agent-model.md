<p align="right"><a href="xiaoyu-agent-model.zh_CN.md">简体中文</a> · <strong>English</strong></p>

# Xiaoyu complete plan

Status: design plan, 2026-10-23. The product direction is confirmed; implementation and acceptance below are not complete.

## 1. Just talk to Xiaoyu

The phone, computers and device present the same Xiaoyu. You can continue chatting, assign work, add requirements or stop a task. Xiaoyu chooses a computer that can do the work and brings the result back to the originating conversation. To choose a computer yourself, name it in the conversation.

The core principle is **as simple as possible**: one entry and one processing path for each feature; decisions the system can make do not become settings for you. Stay quiet when healthy; when something fails, explain what happened and give one actionable next step.

The interface keeps only five places:

| Entry | Content |
| --- | --- |
| Main chat | Chat immediately on opening; busy helpers appear at the top |
| Side chats | Create and switch in the drawer; do not retain empty conversations |
| Work in progress | Only tasks assigned to helpers, grouped by waiting for you, running and ended; detail is one timeline with an add-requirement action |
| Helpers | One entry per helper, with activity and quota; the computer is an internal attribute |
| Settings | Xiaoyu pairing, device, add computer and preferences; technical information lives on the developer page |

Phone and desktop use the same screens and wording. The device keeps avatar, chat, tasks and usage screens. This plan does not rewrite its firmware protocol or replace existing helpers.

For first pairing, scan a code and confirm once on the computer. Connections, network switches and reconnection then happen automatically, without address lists or choosing which computer answers. Connection problems say only “cannot connect, retry”, “pair again” or “update needed”; individual feature failures appear at that feature.

## 2. One server processing path

Each computer runs a Runtime for its local helpers and durable records. Nodes synchronize records to provide Xiaoyu together. Phone and desktop interfaces display content, send requests and retain offline queues; the relay forwards ciphertext and helps discovery only.

Every input follows the same path:

**Receive input → save the record → decide → save the decision → reply → return execution results to the originating conversation.**

- **Save acceptance first.** User messages, helper results and system changes enter one event record. Preserve source, original occurrence time, conversation, actor and cause. Current state derives from records instead of each module storing its own answer.
- **Decide, then speak.** Before saying work was assigned, a fact remembered or a reminder scheduled, check permissions and persist the task or commitment. Give the speaking stage the actual acceptance result. Background execution does not wait for successful reply generation or delivery. Replies describe actual state; queued never means completed. Chat creates messages, not tasks.
- **Always close the loop.** Persist completion, failure or confirmed cancellation and respond once in the originating conversation. Recover from durable records after restart without losing accepted work or executing it again when events replay.
- **Keep context.** Each conversation retains its current topic, open commitments and relevant history. Long-term memory contains preferences, rules and lasting circumstances only. Read versions, availability and task progress from current state; acknowledge stale or unknown facts.

External notifications and observed desktop sessions are data, not user commands. New perception adds an event adapter; action still passes through the same decision and authorization rules. By default, speak proactively only when an agreed commitment or explicit watch has an outcome. This refactoring adds no new proactive features.

## 3. Several computers still do the work once

**Changing connections does not restart execution.** Each request retains a stable ID and its responsible node. Tasks, commitments and their closing replies also have one responsible node. Other nodes hold copies and forward commands instead of executing or waking independently.

- Before sending, the phone saves the request ID, payload and receiving node. Input remains available offline and sends in order after recovery. A retry with the same ID returns the same result; a new ID remains a new message even when its text is identical.
- After a lost acknowledgement, query the original node. Other nodes may forward the query. If the original is unreachable or execution is uncertain, retain the request and show waiting; never independently retry on another computer. New requests not yet sent may select another ready node.
- Process accepted turns in order within a conversation. Before continuing a completed turn on another computer, reconcile the conversation records and open work. Coordinate contention or unknown turns first. Running tool tasks stay on their original execution node.
- Only the origin modifies a record. Bind approvals to a specific node, task, run, operation and expiry. Allow or deny in the interface submits an answer to the execution node; it cannot expand permissions.

Tasks use six states: `queued`, `running`, `waiting`, `done`, `failed`, `cancelled`. They normally move from queued to running and may resume running after approval; work needing no approval ends directly. Queued or waiting work may also fail or be confirmed cancelled. The execution node durably confirms the terminal state, which old events cannot overwrite. Until a stop is confirmed, show “stopping”, not “stopped”. When completion races cancellation, use the terminal state confirmed by the execution node.

The system stores these responsibilities without adding user roles or settings. The first version does not automatically migrate running tasks.

## 4. Messages stay in their conversation; notifications are selective

Save ordinary replies and task results in the originating conversation, then synchronize them to all ends. Direct questions receive replies normally. Side chats do not jump to the main chat, and do-not-disturb does not suppress answers within a conversation.

Only proactive alerts choose a notification channel: speak on the device when nearby; otherwise, if the App is foregrounded, indicate the update in the originating conversation. At other times, only approvals and due reminders create phone notifications; other results wait for the user to return. During do-not-disturb, proactively notify only approvals.

Record message persistence, notification delivery and device playback separately. Reconnection can recover messages without repeatedly notifying or speaking because synchronization repeats. Retry failed image uploads or voice recognition on the affected bubble.

## 5. One synchronized view and one interface set

Clients receive messages, tasks, conversations, helpers, computers, approvals and Xiaoyu's activity through one synchronization entry. The server also stores pin, archive and read state; clients retain copies and pending outgoing content only.

| Endpoint | Purpose |
| --- | --- |
| `GET /v1/info` | Identity, version, minimum supported versions and capabilities |
| `GET /v1/sync` | All interface state and message updates, including unfinished replies |
| `POST /v1/messages` | Text, voice or image messages with a request ID and conversation |
| `POST /v1/files` | Upload images and recordings |
| `POST /v1/actions` | Approvals, stops, added requirements and conversation organization; carries an idempotency ID, with actor derived from authenticated identity |
| `GET /v1/tasks/{id}/timeline` | Paged task timeline |
| `GET /v1/artifacts/{id}` | Attachment download |
| `/v1/dist/*` | App and firmware updates |

Synchronization retains the unified message model: merge by identity and revision, save data and cursor together, and repair gaps or expired cursors with snapshots without deleting older history. Validate source and synchronization progress when changing nodes; callbacks from an old connection cannot contaminate the new one. Unfinished replies replace snapshots; old partials cannot overwrite a terminal state.

Attachment references carry origin, digest and size. Fetch them on demand through an authorized encrypted channel with bounded resumable transfers. A node must be able to read required attachments before processing a message; a reference alone does not mean an upload is ready. If the source is unavailable, retain waiting state or report failure rather than proceeding as a text-only message.

Reuse the existing record hub, node forwarding and encrypted transport instead of building another system. Divide code responsibilities into events, current state, conversation and commitments, decision and execution, helper adapters, and interfaces; each state has one write entry. Clients separate transport, state, screens and device responsibilities, with shared display wording.

## 6. Implementation order

Each step can be validated and shipped independently while preserving working features. Establish execution and recovery contracts before organizing modules; moving files does not replace behavioral validation.

| Step | Outcome |
| --- | --- |
| 1. Align words and actions | Remove the hidden nudge; persist decisions before replies, return terminal results and record actors; measure the first response |
| 2. One state | Separate chat and tasks; unify task states, conversation organization, current topic and commitments; gradually converge on one write entry |
| 3. Automatic connection | Implement request ownership, retry dedupe, node switching, attachment fetching, offline queues and version negotiation before removing manual computer selection |
| 4. Interface convergence | Move clients to the shared interfaces and five entries, then delete old paths |

Keep old endpoints until supported clients have migrated. Upgrades and rollback preserve pairing, history, approvals and pending requests; version gates cannot block the update entry. Idempotently import existing phone organization marks and confirm them before removing the local authoritative copy. Retain a compatible previous program version and data recovery path.

## 7. How to verify the change

Validate and report each step under the [cross-stack reliability rules](cross-stack-reliability.md). A successful build does not establish a working experience. At minimum, cover:

- Lost acceptance acknowledgements, node switches and restarts still execute and answer one request once; identical text with different IDs is not collapsed.
- Three offline messages recover in order; simultaneous clients, old cursors and late partials converge correctly.
- Stopping running work, resuming after approval, completion racing cancellation, and an offline responsible node all show truthful state.
- Direct questions in side chats and during do-not-disturb receive replies in place; task results notify once.
- An image uploaded on one computer is processed on another; an offline source neither loses the message nor silently drops its attachment.
- With real models and networks, meaningful first responses to text, phone voice and device voice render within three seconds of sending. Report misses honestly; queued feedback does not count as success.
- Mixed versions, upgrade and rollback, cold starts and reconnection preserve pairing, history and pending work, with consistent state across ends.

Follow the reliability rules for full measurements, sample counts and fault matrices. Report builds, host tests, device tests, regression, first response, connection recovery, multi-end synchronization and unverified items separately.

## 8. Decided

| Date | Decision |
| --- | --- |
| 10-22 | Remove the hidden nudge; decide, then speak keeps words and actions consistent |
| 10-23 | Overall principle: as simple as possible |
| 10-23 | The phone connects only to Xiaoyu, with no "which computer answers" choice; name a computer in the chat when needed |
| 10-23 | Quiet by default; proactive reminders follow the delivery order in section 4, and do-not-disturb notifies only approvals |
| 10-23 | Outside notifications and observed desktop sessions are material only and never trigger actions directly |
| 10-23 | "Work in progress" lists only work handed to helpers; one helper on several computers counts once |
| 10-23 | Technical information (including firmware version selection) lives only on the developer page |
| 10-23 | Modular cleanup happens after the execution and recovery contracts are in place, not as a separate code move first |
| 10-23 | Repeated messages are deduplicated by request ID; identical text with a different ID is a new message |
| 10-23 | The desktop client ships with dsh and pi preinstalled; the user only enters a key (details decided later) |
| 10-24 | The phone home and the main chat are one entry and one conversation in two forms: the app opens in voice form, the keyboard or the reply card switches to text, and the text form switches back from its top-right button |

This plan replaces conflicting product presentation and implementation sequencing in earlier plans. Identity, encryption, permission, persistence and recovery constraints in the [unified message model](unified-message-model.md), [desktop transport and synchronization plan](xiaoyu-desktop-plan.md), and [decide, then speak](decide-then-speak.md) remain effective. Original evidence stays in the [interaction simplification issue list](app-runtime-simplification.md); this document does not maintain another set of issue IDs and metrics.
