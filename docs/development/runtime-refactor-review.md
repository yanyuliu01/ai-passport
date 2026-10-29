<p align="right"><a href="runtime-refactor-review.zh_CN.md">简体中文</a> · <strong>English</strong></p>

# Runtime refactor review and CC handoff

Date: 2026-10-23. The owner accepted these design corrections and requested a handoff for Claude Code.

This review covers the Raft and Xiaoyu-Bot-App lessons in section 8 of the [refactor design](runtime-refactor-design.md) at commit `99cf439`. This is a bounded review handoff, not a second architecture specification. CC should merge the corrections below into the main design before implementing the affected contracts, then record their destination here. Repository rules and the [cross-stack reliability requirements](cross-stack-reliability.md) remain applicable.

At writing time CC is changing R3's `world.py`, `cards.py`, tests and main design. This document neither reviews nor accepts that new implementation, and does not require discarding it. Do not redo R2 because of this review or restart the live Runtime. The owner will give CC this document; its creation does not mean a message was sent.

## 1. Conclusion and retained decisions

Continue the three-layer structure and incremental migration. Retain the single write entry, persistence before subsequent effects, separate acceptance/application progress, snapshot plus delta, flow control, identity-based deduplication, source isolation, pairing bound to host identity, separate notification/state synchronization and segments that do not duplicate commitments or tasks.

Correct the missing prerequisites, especially automatic ownership transfer on timeout. Omitting Raft is an architectural choice; having only two or three computers is not proof of single-writer safety.

## 2. Automatic takeover blocker: who may change ownership

**Do not implement the current rule as written:** the first receiver owns the conversation, another node takes over after a heartbeat timeout, and increments a token.

Counterexample: Mac and Windows cannot reach each other but both can reach models and tools. Windows takes over while Mac still believes it owns the conversation. Both independently incrementing token 7 to 8 does not revoke either executor. First receipt also cannot establish a globally agreed order during a partition.

### First-version default

- Bind the conversation to an explicit durable owner. Initial assignment for the same conversation must pass through a designated authority, not independent guesses.
- A timeout proves unreachability, not that the original owner stopped. Do not transfer execution of the same conversation solely on timeout.
- When safe transfer cannot be confirmed, preserve requests and original IDs, show queued/owner-unreachable state, and distinguish local pending from authoritative acceptance. Do not claim work started.
- Fixed ownership does not promise automatic availability when the owner is unreachable. Do not migrate existing tasks automatically; query their operation identity and mark unreconciled effects unknown.
- A manual takeover click cannot by itself stop an old machine from executing. Transfer still needs the fencing conditions below.

### Contracts required before automatic takeover

| Question | Required answer |
| --- | --- |
| Token issuer | An explicit durable coordination authority or a coordination protocol with safety guarantees; never independent local increments |
| Grant operation | Atomically check/update owner and old/new generation; retrying the same request returns the same grant |
| Old-token rejection | At authoritative writes and actual action execution boundaries; checking only the client or the start of `Loop.step` is insufficient |
| Check timing | Before committing decisions, dispatching actions and publishing replies; checks and commits must not leave a race that accepts a stale generation |
| Model call spans transfer | Bind results to their operation and ownership generation; obsolete results may be audited but cannot become new replies or actions |
| Tools without fencing support | Prevent the old executor from issuing further actions; reconcile effects already started that cannot be fenced, without claiming duplicate effects are impossible |
| Coordinator failure/restart | Define whether grants/execution stop and how existing grants recover; never silently fall back to each node deciding independently |
| Lease clocks | If leases are used, specify monotonic clocks, drift assumptions, sleep/restart and expiry behavior; heartbeat timeouts do not replace these rules |

This document does not choose a new server or require Raft. Implement the conservative behavior first; enable automatic takeover only after a concrete coordination design and fault tests. R4 must define and implement grants and write fencing; R6 integrates model/executor paths. Do not enable automatic switching in R4 while postponing stale-executor rejection until R6.

## 3. Separate log epochs from ownership generations

- `log_epoch` (the existing wire field `epoch` may remain): the history generation of one origin's log, used to validate replay cursors.
- `owner_generation`: the grant generation for one conversation, managed by the coordination authority to reject stale executors.
- `accepted_seq` / `applied_seq`: durable acceptance and projection application positions within one origin and log epoch. Local durable acceptance is not Raft quorum commitment.

These are conceptual distinctions, not an immediate requirement to rename compatible fields. Ordinary restarts must not gratuitously reset durable identities or generations. Track multi-origin progress as `(origin, log_epoch, seq)`; comparing unrelated local numbers does not determine freshness.

Repair gaps with retained deltas where possible; use snapshots when cursors are invalid or required deltas are unavailable. Fix the snapshot at S and retain later deltas within a bounded window. Clients that outlive it must restart snapshot reconciliation; do not retain logs indefinitely for every slow client.

## 4. Deterministic cores do not repeat model calls

R3 can proceed: implement state reduction as old state plus a recorded event producing new state/pending intents. Time, random identities, model responses, network outcomes and tool results are explicit inputs; persist inputs needed for recovery.

- Replay does not call models, execute tools, send notifications or regenerate decisions.
- Outer drivers perform model requests and tool execution; their results return as events.
- Persist projection state consistently with `applied_seq`; do not save a cursor ahead of the state it describes.
- Fixed seeds help but tests must also control time, delivery and failure points. Preserve failing seeds and the necessary event trace for reproduction.
- Simulate loss, duplication, reordering, partitions and crashes. Retain real-thread, disk, HTTP and cross-client integration tests; simulation does not replace concurrency or device validation.

Keep R3's crash boundaries: before durable acceptance, after acceptance before execution, external action possibly started before result commit, and result committed before receipt delivery. Replay must not automatically repeat external effects with unknown outcomes.

## 5. New-node and membership boundaries

Before serving a conversation, a node must catch up its **required state at an explicit cut**: messages, pending work, commitments, permissions and necessary execution bindings, then consume contiguous subsequent deltas. It need not wait for every node's entire history; track each origin separately. Catch-up is necessary but still requires an ownership grant and verified execution capability.

Serialize membership changes through the designated identity/permission authority. Completion means durable application there and enforcement at the required execution boundaries, not acknowledgment from every offline device. Returning nodes refresh permission versions before processing commands. Revocation must reject credentials at every boundary accepting commands, not merely remove a UI entry. Reject sensitive operations when required permission freshness cannot be established rather than trusting stale membership.

If the permission authority has not been selected, record it as an unresolved R4 prerequisite. One-change-at-a-time is not a substitute for a consistency protocol.

## 6. Limits of the Xiaoyu-Bot-App lessons

| Topic | Corrected requirement |
| --- | --- |
| Address filtering | Label LAN, private-network and public reachability; verify identity and health. LAN addresses may serve local clients, but are not universally reachable and must not all be removed. Link-local addresses need matching interface/network scope |
| Manual routes | Discovery must not overwrite manual configuration. When a manual route fails, an authorized policy may switch to another verified route while retaining the configuration and failure state |
| Pairing identity | Distinguish host identity from route certificates. Renewal or tunnel endpoint changes do not automatically imply a new host. All routes authenticate the same host using a mechanism appropriate to the transport |
| Old notifications | Avoid repeated historical alerts; unresolved approvals, failures and pending work remain visible in state/notification views. Read does not mean resolved |
| Background polling | Bounded backoff is useful but continuous OS execution is not guaranteed. Background notification delay is not a foreground sync or first-response latency pass |
| Read after effects | Invalidate affected resource snapshots and reread before dependent actions, not every unrelated cache. Failed reconciliation means unknown state, not automatic repetition |
| Conversation segments | Preserve conversation/task/commitment identities. Summaries declare coverage and reference source history. Old-segment results refer to the original task and arrive in the current segment without cloning work |
| Installation/rollback | Keep pinned versions, source verification, offline packages and failure rollback in the preinstallation plan, separate from Runtime projection changes |

## 7. CC implementation order and acceptance

1. Merge sections 2-6 into the corresponding main-design sections, removing contradictory claims such as epoch equals term or timeout ensures safe transfer. Record destinations here to avoid permanent competing specifications.
2. Continue R3: state reduction, durable progress, snapshot rebuild, legacy-data migration, crash boundaries and deterministic tests. A rebuildable message projection alone does not establish complete state and pending-work recovery.
3. In R4 implement bounded sync, source/permission isolation and fixed ownership first. Enable automatic takeover only after coordination authority and execution fencing are specified and tested.
4. In R6 integrate model driving, segmented context and execution checks. Preserve real end-to-end acceptance for a meaningful complete first bubble within three seconds; a receipt does not count.

Add or verify cases for: two mutually disconnected nodes with model access; an old model result arriving after ownership changes; waking an old owner from sleep; coordinator crashes; takeover racing dispatch; the same request retried through two entry points; a revoked offline device returning; unrelated offline nodes not blocking conversation catch-up; snapshot/delta interleaving; an unread/resolved distinction for approvals; and LAN/remote route switching.

Report evidence and remaining gaps for each item. Unimplemented checks or unavailable devices/environments remain NOT RUN. Use repository validation and delivery fields without weakening the gate. This documentation handoff adds no authorization to commit, push, restart or deploy.

## 8. Primary references and incorporation record

- [etcd-io/raft README](https://github.com/etcd-io/raft/blob/main/README.md): deterministic state machines, I/O boundaries, Ready persistence/application constraints and lease prerequisites.
- [Xiaoyu-Bot-App overview](https://github.com/QingJ01/Xiaoyu-Bot-App/blob/main/README.zh.md): host/mobile structure, segments and task bindings.
- [Xiaoyu-Bot-App connection guide](https://github.com/QingJ01/Xiaoyu-Bot-App/blob/main/docs/mobile-install.zh.md): LAN, HTTPS tunnels and Tailscale reachability.
- [Xiaoyu-Bot-App feature guide](https://github.com/QingJ01/Xiaoyu-Bot-App/blob/main/docs/mobile-features.zh.md): operation receipts, browser snapshots and background-notification limitations.

These are design references, not an audit of their entire implementations. Recommendations here are engineering judgments for this repository.

Status (2026-10-23, merged into the main design by CC, documentation only):

| This document | Location in the main design |
| --- | --- |
| Section 2 (responder and automatic takeover) | 3.1 "Conversation responder (first version)"; 8.3 item 1 (the "first message, timeout takeover, token increment" wording is withdrawn and the takeover preconditions are listed) |
| Section 3 (three generations, deltas first, bounded retention) | 1.2 "Three generations and positions"; 2.2 gaps, snapshots and the bounded retention window; 8.1 rows 3-4 ("the epoch plays the role of the term" removed) |
| Section 4 (the deterministic core never re-calls models) | 3.1 "Deterministic core"; 8.1 rows 1-2; 8.3 item 2 |
| Section 5 (catch-up and membership) | 8.3 items 3-4 (the permission authority is an open R4 condition) |
| Section 6 (scope of the Xiaoyu-Bot-App lessons) | 8.2 table; 8.3 items 5-6 |
| Section 7 (order and fault cases) | "Required fault cases" at the end of 8.3; R3 continues with R3.1-R3.4 in the section 7 handoff |

Verification: repository document checks pass. Implementation and fault-case acceptance are reported
with R3, R4 and R6.
