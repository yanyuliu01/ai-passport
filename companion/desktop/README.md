<p align="right"><a href="README.zh_CN.md">简体中文</a> · <strong>English</strong></p>

# Xiaoyou desktop client

Implementation in progress, source version 0.1.17. Follow the
[development plan](../../docs/development/xiaoyou-desktop-plan.md) and
[reliability contract](../../docs/development/cross-stack-reliability.md).
Source/build availability does not establish installed-client acceptance.

The owner approved the v8 interaction reference on 2026-10-21. The onboarding
increment uses its existing portraits, gray-blue background and blue actions.
The 0.1.2 increment implements the conversation/work-record shell below;
source availability does not establish complete multi-platform acceptance.

## Server account connection (0.1.17 source)

The only remote connection entry is the existing self-hosted account server.
Desktop and Android 0.34.1 sign in to the same account. Computers retain their
local helpers; the phone discovers computers without pairing QR codes.
Old direct/relay routes are retained for recovery but are not automatically
restored or offered in settings. See the [account connection guide](deploy/accounts.md)
for migration, security boundaries and test scope. Earlier checkpoints below
describe their respective historical versions. No installed-version acceptance
is implied by this source increment.

## Late local-service startup (0.1.10 source)

If an owned Runtime is still starting when the initial startup wait expires,
the existing supervisor keeps checking that same process. Once it is ready,
the client reconnects only if its authenticated identity was already confirmed.
It does not create another process, confirm a new identity, or repeatedly replace
a healthy connection. Initial startup and recovery cannot run concurrently, and
a response arriving after shutdown cannot reconnect the client. Signal-terminated
children count as exited and retain the existing three-restarts-per-ten-minutes
limit. External services remain outside the client's process ownership.

The original startup wait and supervision interval remain unchanged. This fixes
late recovery; it does not establish fast cold startup, a three-second model
response, or acceptance of the installed desktop version.

## Unified request replies (0.1.9 source)

When the Runtime advertises `sync_requests_v1`, pending accepted requests join
the existing sync subscription. Adding a request cancels only that idle sync
read, leaving concurrent review reads and the durable outbox intact. Current
canonical reply snapshots retain their revisions without advancing the history
cursor. Missing request state remains accepted and is never reposted. Older
Runtimes keep their supported per-message reads; a Runtime rollback rechecks
identity and capabilities before changing the read protocol. Feed/wait/stream
server adapters remain until installed-client migration and version gates permit
R8 removal. This source increment does not establish real-model timing or an
installed desktop update.

## Replies cut short (0.1.8 source)

A reply the owner cut into keeps the words already said and shows the localized "interrupted" label
under the bubble when the Runtime marks it (`presentation.interrupted`). The
status-line rule is shared with Android through the golden fixture.

## Owner review version checks (0.1.8 source)

Request and timer confirmations carry the exact revision displayed to the owner.
If the selection changes before submission, the desktop sends no confirmation,
reloads the operations and clears the old answers. A saved confirmation keeps its
original body and answers for a lost-receipt retry; it never adopts a newer selection.
When a review is already recorded but this client has no saved retry, the page
directs the owner to the device retaining that confirmation instead of issuing a
new review identity. These source checks do not establish phone or live multi-end
acceptance; the Runtime API and stored confirmation body remain unchanged.

The desktop also retains each pending review's original route and credential in
the existing encrypted client vault before any POST. Reopening the client or
upgrading its device credential does not change the actor used for that retry.
Revoked credentials are refused without trying a different actor. Missing vault
data or a changed route leaves the confirmation saved and asks for restoration;
older pending bodies without identity metadata are not silently reassigned.
Keep the client database and its OS-protected vault together for recovery. A
rollback to older client code does not enforce these additional retry checks.

Connection replacement or shutdown cancels the associated review request, and
late responses cannot clear the saved confirmation. Concurrent submissions are
rejected while the first is pending. The peer relay retains the same originating
HTTP credential across its own restart/upgrade and pins requests to the requested
Runtime. No credential is included in peer packets or plaintext cache fields.
Relay identity metadata is retained for later repeats; old relay versions do not
provide this protection. Source tests use synthetic state; installed-client and
Android recovery remain separate acceptance work.

## Current reply delivery (0.1.7 / Runtime 0.23.1)

An accepted request remains pending until its assistant reply reaches a terminal
state. Syncing the owner message alone does not remove the loading indicator.
The indicator also covers accepted requests not yet reached by history replay,
including after restart or a temporary disconnect; connection errors remain visible.

Runtime polling/acceptance adds `message_snapshot`: the authoritative owner and
reply log records, their original revisions, Runtime/subject identity and epoch.
The desktop merges these through the normal cache revision rules without advancing
the history cursor. Current replies can render while older history is still paging;
later replay cannot duplicate the owner or replace a completed reply with a partial.
Cache reads reuse epoch lookups, and window notifications batch for 100 ms to
reduce repeated work during history catch-up. Older Runtime versions keep the
pending indicator and use ordinary sync to obtain reply content.

Validation: 58 desktop tests and isolated source/packaged Mac windows pass. The window
checks accepted waiting, owner-only synchronization, restart with an accepted
request, offline feedback and current reply delivery during blocked history replay.
Synthetic recovery rendered in 785 ms from source and 795 ms from the packaged app;
these are not real-model TTFT measurements. The installed 0.1.7 Mac window also
rendered the previously stuck accepted request from the existing user cache,
while history catch-up continued; its outbox cleared without resubmission.
Installed-client, phone and native Codex/Claude acceptance remain separate.

## Shared display rules (0.1.6 / Runtime 0.23.0 / Android 0.31.1)

The client renders the Gateway's versioned message blocks, formatting runs,
author labels, ordering, placement and task status/groups. It no longer waits
for an entire reply when the Gateway has already closed an earlier block.
The ordinary chat and shared-source history consume the same projection;
SQLite preserves it through restart. Enabling the policy refreshes cached
legacy records once without deleting history, the outbox or pending approvals.
Old Runtime records remain readable; unsupported policy revisions are labelled.

The Python, Android and desktop tests read the same golden fixtures. Those
checks do not prove real native Codex/Claude window rendering or continuation;
native directory details explicitly retain that gap as incomplete. Mac/Windows
installed-client and phone acceptance are separate from a successful source build.

## Embedded node on an always-on computer (0.1.5 / Android 0.30.1)

Connection settings now offer **Use this always-on computer as the communication
node**. After confirming the local Runtime, explicitly enable the role, choose a
TCP port (default 18795), and enter the public IP/domain and external port when
available. Closing the window keeps the node running; a normal client restart
restores the enabled role and the same identity. Exiting, sleeping or shutting
down the computer interrupts its entry. Login startup remains an explicit setting.
The relay runs alongside the Runtime and does not expose or restart its HTTP API.
Stopping the node restores the previous external entry, if one was configured;
trusted devices, cached history and pending request IDs are retained.

The native node generates a P-256 TLS certificate locally with selfsigned 5.5.0.
Its key, random entry token and room remain in Electron's OS-encrypted vault.
The invitation carries the exact SHA-256 leaf-certificate pin, which Android
0.30.1 and desktop 0.1.5 validate before sending HTTP credentials. This works
with a numeric public IP without requiring a purchased domain or public CA.
Existing ordinary WSS entries retain platform CA validation. HPKE still protects
end-to-end content and device identity independently of TLS. A mismatched,
not-yet-valid or expired node certificate is rejected. Certificates last one year;
rotation/revocation automation remains follow-up work. Do not silently change a
pinned identity or bypass certificate checks to recover a failed connection.

The hosting computer connects through its private loopback route, avoiding a
router hairpin dependency. That route never appears in a QR invitation. A public
address is required before generating a node invitation. The UI distinguishes
local listening, a successful computer-side public-address probe, and actual
phone acceptance. A successful probe is **not** an external phone test.

An always-on computer still needs inbound reachability. For router public IPv4,
forward an external TCP port to the computer's stable LAN address and listening
port; allow only that relay port through the relevant firewall. For global IPv6,
allow the selected port through the IPv6 firewall (the node listens dual-stack).
No router, firewall, VPN or Tailscale settings are changed automatically. The
current Mac has no verified inbound route; no public-node deployment or cellular
phone test is claimed. A different network may require ISP/router access or a
different reachable always-on computer. This increment implements a hub entry,
not automatic NAT traversal, a full virtual LAN, or automatic node failover.

Changing a public address requires updating the entry and pairing affected devices
again; automatic address discovery/propagation remains unimplemented. Keep the
old entry operational while testing a new one. The previous 0.1.3 candidate and
Android 0.29.0 preserve old settings and caches, but cannot connect to this new
self-signed node; both endpoints need the pin-aware versions for this role.
Physical phone installation, public-network recovery, Windows native execution
and meaningful model response <=3s remain unverified. Local WSS/Java protocol and
native Mac lifecycle evidence is recorded in `build/xiaoyou-node-validation.json`.

Validation for this checkpoint: Mac arm64 build and DMG integrity PASS; 52 desktop
and 161 Android unit tests PASS; complete repository gate and six supplementary
Pillow tests PASS. The complete gate passed on retry after an unchanged Runtime
restart fixture hit a temporary-file write race once; the failure is retained in
the report and the race was not changed by this transport increment. The actual packaged app passed enable/stop, no-address invitation
blocking, portable pinned QR, background operation, stable-identity restart, and
previous chat/work/approval journeys using isolated fixtures. Real Java/OkHttp
passed pinned WSS, wrong-pin rejection before HTTP headers, two simultaneous
computers, relay restart/lost-response recovery and one message execution. The
package includes the frozen Runtime 0.22.0 source digest `21563f0c…`.
Android 0.30.1/code 56 was published locally after code 55; `/v1/app` and APK hash
agree. The phone will offer the update on its existing connection; installation
is NOT RUN. The owner profile's old running desktop was not replaced or restarted,
and the new node has not been enabled there. The Mac unsigned private candidate
is in `release/node-0.1.5/`. Native Windows is NOT RUN (the existing Windows task's
host is currently unavailable). Build PASS; Host tests PASS; Device tests PASS
only for isolated native Mac; phone/Windows/board NOT RUN. First meaningful model
response <=3s has zero samples; real-network recovery and multi-end convergence
are NOT RUN. Do not report cross-network phone acceptance from these fixtures.

## Independent phone transport and QR pairing (0.1.3 / Android 0.29.0)

The current priority is phone communication across different networks, without a
Tailscale dependency. Desktop generates a two-minute local QR code. Android has a
Scan to connect a computer action, shows its fingerprint for comparison, and waits
for explicit approval on the computer. One phone retains up to eight computer
targets; a new pairing does not replace old devices or their pinned keys. The
independent transport uses outbound WSS and the same authenticated HPKE protocol
as the desktop, with X25519/HKDF-SHA256/AES-128-GCM. Runtime tokens stay on the
computer. Android pairing material and pending write intents use AndroidKeyStore
AES-GCM storage. No provider or model is called to pair.

Text and ordinary polling/sync, approvals, task follow-up, voice and image upload,
and bounded attachment reads pass through the authenticated relay allowlist.
Voice/images/attachment payloads have a 2.8 MB single-transfer bound; oversized
payloads return an explicit error. Long polls are capped at 20 seconds to fit the
30-second RPC deadline. Endpoint identity, source space, request identity and
packet expiry are checked. The mobile connection retries after interruption;
writes are journaled before sending, and Runtime/desktop receipts preserve the
same client ID after a lost response. A relay is not an offline mailbox. Scanning
unrelated spaces does not silently merge their histories. Per-device revocation
and large file chunking remain follow-up work.

A public-reachable **independent WSS entry is still required and has not been
deployed**. No Tailscale configuration was changed or used for this new path.
This Mac has no verified direct public route; missing UPnP/NAT-PMP responses do
not prove every NAT traversal method impossible. QR pairing cannot create that
route. A separately purchased server is optional if an existing public-reachable
host is available. The reviewed deployment template is in [deploy/](deploy/README.md),
and a generated template archive is under ignored
`build/xiaoyou-relay-deployment-0.1.3.tar.gz`. External deployment and a phone on
cellular data remain NOT RUN. Do not describe this checkpoint as proven stable
cross-network phone communication.

Validation: desktop build PASS, 45 desktop tests PASS; Android build and 159 unit
tests PASS. QR output was decoded by Android's ZXing core. The real Java protocol
implementation paired with the desktop, held two independent computer connections,
transferred synthetic text/voice/image data, recovered from a relay restart with a
lost response, and executed the submitted message exactly once. Those tests use a
local synthetic relay and do not establish physical phone or public-network
acceptance. Native packaged Mac QR/settings/onboarding checks pass. Evidence is
under `build/xiaoyou-android-relay-interop.json`, `build/xiaoyou-remote-shell-qa.json`
and `build/xiaoyou-remote-onboarding-packaged-qa.json`. Bouncy Castle 1.86 handles
HPKE, OkHttp 4.12.0 handles WSS, and ZXing Embedded 4.3.0/core 3.5.4 handles scanning.

Android 0.29.0 (code 54) was published locally through the existing Runtime; the
phone can obtain the update through its existing connection. Installation/scanning
on the physical phone remains unverified. The Mac 0.1.3 unsigned private candidate
is in `release/remote-0.1.3/`, retaining the isolated Runtime 0.20.0 / `62cc399`
bundle. Existing Runtime 0.21.1 was not restarted. Keep Mac 0.1.2 and App 0.28.1 as
recovery candidates with caches, pending IDs and sealed credentials preserved;
older apps ignore new relay pairing settings. The independent Home was restored,
helper products are grouped across devices, and array-shaped Runtime quota windows
now display reported values; further presentation work is deferred.

## Main conversation and work records (0.1.2)

The desktop home is now the main conversation. The sidebar keeps flat side chats,
independent work records, helper portraits and connection settings. Owner/assistant
messages use complete blue/white bubbles; task activity and private native sessions
stay out of ordinary chat. Helper capsules project reported models, quota windows
and freshness, with unknown values when unavailable. Avatars open usage; capsule
bodies open current work. The right-side plus opens photo/file entries.

Work records show waiting decisions, active work and collapsed ended work. Unknown
or old-epoch records have a separate collapsed verification group. Details show
recent/all progress, source chat, quiet execution metadata and a read-only native
transcript. Supplements stay bound to the origin task/conversation and distinguish
queued delivery. New services support main-scope supplements, shared side-chat
metadata and message-context branching; the bundled older service uses legacy task
continuation and local side-chat titles from the first durable message. Malformed
or private catalog rows cannot stall message synchronization. Native opening stays
hidden until a local service advertises a verified control-handoff capability.

Desktop voice recording and photo/file transport remain unimplemented; the entries
explain availability without sending empty messages. Full provider/agent management
and onboarding refinements remain follow-up work.

Validation: UI build PASS; 39 desktop host tests PASS; complete repository gate PASS
(ESP-IDF 5.5.3 firmware build, no flashing) and six supplementary Pillow tests PASS.
Packaged Mac 0.1.2 passed first-launch consent, discovery/registration, owned service
startup, encrypted pairing, background/restart, main/side isolation, task-bound
supplements, single approval convergence, read-only transcript, responsive sidebar
and credential isolation. CLI/HTTP fixtures were used; no real model was invoked.
Source Runtime encrypted interoperability also passed. Evidence is under ignored
`build/xiaoyou-main-validation.json`, `build/xiaoyou-shell-qa.json` and
`build/xiaoyou-main-onboarding-packaged-qa.json`; the complete-gate log for this run
is `/tmp/xiaoyou-shell-full-gate.log`. The offline `build/desktop-main-preview/`
uses explicitly labelled sample data and does not connect to a service.

Versions/configuration: desktop 0.1.2, Electron 44.9.3, Mac arm64; packaged Runtime
0.20.0 / `62cc399`, unchanged firmware, isolated loopback and encrypted local relay.
Regression PASS within that scope; connection recovery PASS for host failure paths
and confirmed packaged restart; real-network recovery timing NOT RUN. Local relay
multi-end host tests and packaged pairing PASS; real Mac/Windows/public-network
convergence NOT RUN. First meaningful model response <=3s NOT RUN (0 samples;
p50/p95/max and misses unavailable). Native Windows, real dsh/DeepSeek, voice/file
sending and native control handoff remain unverified. No production Runtime was
restarted. Build PASS; Host tests PASS; Device tests PASS for isolated native Mac
profiles only; board tests NOT RUN.

The unsigned private Mac candidate and packed manifest are in `release/main-0.1.2/`.
It retains the isolated verified Runtime 0.20.0 bundle rather than including the
concurrent Runtime task's changes. Installer SHA-256:
`e4d5263138fa9ec10c483009de2543a79e7583b27e1709fc5df3ad9ab01a7a51`.
Desktop source fingerprint:
`96a4dddba8d2fe4d422b9d2bbb7826b5e138d766f9e270487cd6af1bb27f7eed`.
Preserve `release/onboarding-0.1.1/` for rollback, along with the SQLite cache/outbox
and OS credentials. New catalog/read metadata is additive and ignored by 0.1.1.

## First connection and local agents (0.1.1)

First launch now asks whether to enable a local Xiaoyou or pair with an existing
one. There is no silent legacy Runtime attach or restore of unconfirmed saved
endpoints/relay. An explicit review restores old connections without erasing
history, pending requests, cursors or dormant endpoint settings. Subsequent
launches restore only confirmed Runtime identities and enabled local services.

The local path scans known installations of DeepSeek Harness, Codex and Claude
Code. Installation, configuration presence and CLI-reported login state are
separate facts. Bounded version/status probes never run an inference call or
return raw status output, account identifiers or credentials to the renderer.
Missing/unsupported status remains unknown. The owner selects optional helpers
and reviews service/device, model policy, transport and optional login startup
before enabling. Choosing an existing Runtime preserves its agents/models and
process ownership. It is never restarted or rewritten by this import.

New managed services use dsh as both the default and voice agent, with
`deepseek-flash` for chat and `deepseek-v4-pro` for tool work. A detected dsh
executable and a DeepSeek API key are required. Keys entered in onboarding are
sealed with OS storage and passed only to the owned child, never stored in
plaintext Runtime config. An existing sealed key or `DEEPSEEK_API_KEY` can be
reused. Importing arbitrary provider stores is not yet implemented. The dsh
executable is detected, not yet included in the installer. Selected Codex/Claude
helpers use their existing local sign-in, with workspace-scoped/manual approval
settings. Calls from this client use gpt-6.1-sol/Sonnet respectively, shown in
review; their global settings and past conversations are not modified/imported.

Pairing waits for approval on the original device, then reconciles history.
It does not create an unrelated local subject. Same-space provisioning of a
new local Runtime after joining remains unavailable and is explicitly blocked.
The result page distinguishes transport/record readiness from actual model
response, which remains unverified until a real request. No paid model probe
runs automatically. Full agent model/provider management is follow-up work.

The design/code review reference is [Magpie](https://github.com/yetone/magpie),
MIT, revision `f5b0157420a4491f6e707b49a8af8e87ea8050a1`:
`internal/agent/agent.go`, `rescan.go`, `dsh.go` and
`internal/gui/assets/app.js`. The implementation uses its descriptor/adapter,
explicit review and rescan approach; no upstream source is copied, no Magpie
binary/gateway is bundled, and no global agent config is rewritten. Portraits
reuse the existing Android `portrait_*_idle.png` derivatives of the repository's
AI companion assets.

Validation for 0.1.1: build PASS and 28 desktop tests PASS. Isolated native Mac development and packaged
profiles passed first-launch consent, read-only discovery, selected-helper
registration, dsh-managed service startup, credential isolation, approved
pairing, remote-only joining, background close and confirmed restart. CLI
fixtures were used for those lifecycle tests; no real model was called. A
separate read-only real installation scan found dsh, Codex and Claude Code,
with the latter two reporting signed in. The complete repository gate and six supplementary Pillow image checks pass.
Native Windows and real-model latency remain NOT RUN. Synthetic UI evidence is under ignored `build/`; the standalone
onboarding preview explicitly labels simulated detection and makes no network
calls. It never saves typed keys.

The 0.1.1 installer, packed provenance and checksum live under
`release/onboarding-0.1.1/`; isolated packaged evidence is
`build/xiaoyou-onboarding-packaged-qa.json` at the repository root.
Installer SHA-256:
`10a1ea594de43309418f8e6fd0783d9c295096db76105c8dae5945fd1cf5b2ca`.

The Mac 0.1.1 package uses an isolated source snapshot with the existing verified
Runtime 0.20.0 bundle from `62cc399`. Runtime work in the concurrent App task is
not silently included in this candidate. Preserve the 0.1.0 installer for
rollback; its silent-connect behavior is a known earlier limitation. New consent
metadata is additive; keep the client DB and OS-key access intact. The older
client ignores that metadata and may reconnect automatically if rolled back.

## Current increment

The Electron client provides a Chinese conversation/task/connection UI, local
SQLite cache and durable request outbox, shared conversation catalog/selection,
progressive history display, current Runtime API access, explicit
origin-bound approvals/cancellation and close-to-background behavior. It can
attach to an existing local Runtime without restarting it. A fresh private
Runtime uses installed dsh with DeepSeek and selected optional helpers; it never
installs a helper or copies login credentials automatically. Provider readiness
must be tested; a missing helper/login is a setup limitation, not a working AI.

On request, local detection recognizes known configs under `~/ai-passport`,
`~/xiaoyou/ai-passport` or `~/Documents/GitHub/ai-passport`. It reads metadata
without connecting; the owner confirms a source before loopback attachment.
Other locations can be selected under Advanced connection settings. Isolated QA
profiles skip these legacy locations.

The desktop-to-desktop transport uses authenticated HPKE envelopes through a
self-hosted WebSocket relay. Inviting needs an existing local Runtime and a
configured relay; joining needs an invitation and approval on the existing
device. The invite expires in two minutes. Endpoint keys and connection tokens
use Electron OS-backed safeStorage. Runtime bearer/mesh tokens remain local;
the renderer and relay do not receive them. The relay is not an offline mailbox.

Same-subject, already mesh-enabled Runtimes can additionally exchange their
existing mesh records through the encrypted channel, preserving Runtime-owned
context/task semantics. Pairing alone does not silently merge unrelated local
subjects or reconfigure an active Runtime. A joining client can access the
inviter's Runtime; provisioning a fresh independent Runtime into that same
subject still needs the later onboarding stage.

Android adaptation, production membership/revocation review, automatic LAN
discovery, attachment transfer/server-side paging, native Windows verification,
and real network/latency acceptance remain required work. Do not advertise this
increment as the complete Thursday candidate. HPKE is not a ratchet and does
not provide historical forward secrecy after recipient-key compromise.

## Develop and test

Use a maintained Node version compatible with the locked Electron/Vite packages
(Node 26 used locally). Dependencies are locked in `package-lock.json`.

```bash
cd companion/desktop
npm ci
npm run build
npm test
npm start
```

`npm test` includes a real loopback relay test. A sandbox that denies binding
`127.0.0.1` cannot run that test; report the restriction instead of skipping it
and claiming a pass. Unit tests use synthetic content and call no model.

## Native Runtime bundle and installers

Build a PyInstaller Runtime on the target OS/architecture; a Mac Python bundle
cannot be used in a Windows installer. The package hook verifies architecture
and the Runtime source digest, and refuses a stale/missing bundle. A separate
Windows path assembles official Windows binaries without executing them on the
build host; it still requires native Windows acceptance.

```bash
python3 -m venv .runtime-venv
.runtime-venv/bin/python -m pip install -r requirements-build.txt
.runtime-venv/bin/python tools/bundle_runtime.py
npm run package:mac
```

On Windows x64, run `./tools/build-windows.ps1` from PowerShell in this folder.
It creates an isolated build venv/cache, runs the bundled Runtime integration
tests and produces the NSIS installer. It needs Node 24+ and Python 3.11+.
Alternatively use `.runtime-venv\Scripts\python.exe` for the Python steps and
`npm run package:win`. `python3` may be named `python` there. Generated Runtime
files and installers are ignored by Git. Do not include real Runtime config,
state, keys or CLI homes in a package. Helper executables/login are external
to this bundle. The build pins Pillow 11.3.0; the local Mac bundle uses Python 3.9;
it does not change an existing system Python or Runtime environment.

The embedded Windows preview uses CPython 3.13.16 and a matching Pillow wheel,
with dependency URLs/checksums in `tools/windows-runtime-lock.json` and isolated
module search paths. It installs no system interpreter. On a host authorized to
download the official Electron distribution:

```bash
python3 tools/bundle_windows_embedded.py
node tools/download_windows_electron.mjs
XIAOYOU_RUNTIME_BUNDLE="$PWD/runtime-dist-win" npm run package:win-preview
python3 tools/release_manifest.py
```

This preview configuration skips Windows executable resource editing/signing.
The current run assembled its Python bundle, but the Windows Electron download
was denied execution authorization (`owner denied`); no Windows installer was
produced. Do not bypass that denial with another download route. Native Windows
module loading, installation, provider readiness and UI remain unverified.
The original native PyInstaller route remains available in the build script.

Installers need appropriate signing/notarization for ordinary distribution.
Private unsigned/ad-hoc builds may trigger OS warnings and do not satisfy a
frictionless clean-install claim. Do not disable OS security to hide this gap.

## Self-hosted relay

The operator supplies a random access token of at least 32 characters and a
public WSS endpoint. Start the relay on loopback behind a TLS reverse proxy:

```bash
npm run build:main
XIAOYOU_RELAY_TOKEN='<random-access-token>' npm run relay
```

The default listener is `127.0.0.1:8795`; the CLI refuses public plaintext bind.
The TLS proxy forwards `/v1/connect` including WebSocket upgrades. Configure
limits and service supervision on the actual host before deployment. The
relay applies authentication, room separation, frame/buffer/connection/rate
limits and heartbeat cleanup; it never runs a model or arbitrary local command.

The operator privately prepares an endpoint JSON containing `url` (a WSS
origin), `token`, and optional random 32-hex `room`. Import it under Devices and
connections -> Advanced -> Import communication endpoint. These credentials
are never committed. Once configured, ordinary joining devices obtain their
connection settings from the invitation; users do not type addresses/tokens.
Only loopback test relays may use `ws://`.

This source does not deploy or purchase a relay. Existing always-on computers
can host one only after inbound reachability is established. Process uptime
alone cannot overcome NAT/firewalls.

## Data and recovery

Client data lives under the OS user-data folder. Tokens/key seeds are sealed
with OS-backed storage; the conversation SQLite cache is not separately
encrypted by this implementation. Protect the OS account/disk accordingly.
Closing the window keeps the app running in the tray/menu bar; Quit stops only
the Runtime child this process owns. It never stops an externally managed
Runtime. Login-start registration is an explicit UI action.

Do not delete the user-data folder to repair connectivity. Keep the DB and OS
key access together across upgrades. An uncertain accepted request keeps its
original Runtime and `client_id`. Known task origins and connection generations
are enforced; a missing Runtime waits rather than being re-executed elsewhere.
Recovery after both computers become unreachable and independent key recovery
remain outside the normal always-on baseline.

For isolated native QA, set `XIAOYOU_DESKTOP_TEST_DATA` to a new temporary
directory and `XIAOYOU_DESKTOP_SKIP_DISCOVERY=1` before launch. This works with
packaged clients and keeps smoke tests away from ordinary profiles. Do not
import real configuration or register login startup in that test profile.

## Previous 0.1.0 validation checkpoint, 2026-10-21

Desktop source is 0.1.0 against Runtime 0.20.0 at repository base `62cc399`.
`npm run build` and all 19 desktop tests pass: 17 unit checks plus a real loopback
relay check and a two-Runtime integration check. They cover approved pairing,
authenticated RPC, concurrent long polls, durable request deduplication,
replicated model context, conversation selection and relay-restart recovery.
The Runtime integration uses the bundled Runtime and an echo provider.

The Mac ARM64 DMG was produced and checksum-verified. Two packaged Mac profiles
passed approved pairing, actual complete-bubble display on both windows,
owner-before-reply ordering, close-to-background sync, relay restart and
renderer credential isolation. These were isolated echo fixtures, not a
real-model or real cross-network acceptance run. `release/manifest.json` reads
the source/lock/runtime fingerprints from the packed app and lists installer
SHA-256 values. The current Mac build is an unsigned private preview.

The owner's existing Windows task reported a passing frontend build and 16/16
tests on its earlier snapshot after the shutdown repair. It could not load its
native build tools; updating the source and returning later reports also hit
access restrictions. No native Windows installer/window pass is established.
That report does not substitute for this source revision's 19-test acceptance.

The complete repository gate passed after activating existing ESP-IDF 5.5.3 and
JDK 17. Its IDF Python environment skipped six optional Pillow image checks;
all six then passed separately in the desktop build venv. Online production npm audit reports zero
advisories; the complete audit reports eight moderate development-tool findings
through the builder's `sprintf-js` dependency. No automatic downgrade was made.

Build: Mac PASS / Windows NOT COMPLETE / complete gate PASS. Host tests:
desktop PASS (19); repository gate PASS, supplementary Pillow checks PASS (6).
Device tests: NOT RUN.
Real-model first meaningful complete bubble <= 3s, real Mac/Windows cross-network
recovery, Android secure-transport interoperability, clean installation,
provider login and signing/notarization are NOT RUN. No existing Runtime was
restarted and no firmware was flashed by this implementation.

The read-only Mac network check found working IPv4 egress, no usable global
IPv6, and no UPnP/NAT-PMP WAN-address response. It does not establish inbound reachability
or carrier-grade NAT. The Windows task reported a global IPv6/default route,
but its direct probes were denied and no reachability was verified. No router,
firewall, proxy or port-mapping settings were changed. A usable owner-controlled
public endpoint remains a deployment prerequisite, not a completed deliverable.

Gate-only firmware evidence (no firmware change or flashing):

```text
archive: build/firmware/7dbbaac6a521c74956dc578228ef9c282a27d65a97f417eade45aabcacb2ea79/
full_bin_sha256: 7dbbaac6a521c74956dc578228ef9c282a27d65a97f417eade45aabcacb2ea79
elf_sha256: 93c71a2fdc1d11936a66b84512ce35b9254ab05cb8a336a9724b0db1dfac902a
archive_verification: PASS
```

Account-based computer discovery and the default numeric-IP relay are described in [Account discovery](deploy/accounts.md). Each computer keeps its own local Runtime.

After account login or restoration, the client reuses a single known local configuration
whose Mesh identity matches the authenticated account. Its provider settings, state
directory and Runtime identity stay in place. An offline matching service can start
from that same configuration; a reachable service is attached without taking process
ownership. Ambiguous configurations and occupied ports with unverified identities
require connection settings instead of starting a second Runtime. No model credentials
are copied from another computer. Account shutdown drains pending peer work before
closing its SQLite cache, including on Windows.

### Helper model selection (0.1.16)

Settings > Helpers > Choose model saves the local helper default for future tasks.
Running jobs retain their original agent and model. Codex choices come from its
local visible model catalog; Claude Code offers native aliases. Other model
accepts an explicit provider-supported name. Saving does not invoke a model;
availability and billing depend on the provider. Newly managed Codex helpers use
`gpt-6.1-sol`; Astra requires an explicit choice. Older runtimes keep the read-only
label. Preferences live in `state/helper-models.json`, separate from credentials
and native CLI settings. Gateway helpers retain their existing setup flow.

## Manage helpers across clients (0.1.18)

Settings > Helpers includes an explicit computer selector. Adding, removing,
model selection and tool grants bind to that connected Runtime; an unavailable
selected computer never silently redirects a write to the local machine.
Runtime 0.24.1 also supports reversible removal of config helpers, while
protecting the primary dialogue/voice helper and retaining accepted work/history.
Native subscriptions and API credentials remain local to the target computer.
Quota rings use the limiting known, unexpired 5-hour/weekly remainder.

The native-window synthetic acceptance script is
`node tools/test-helper-management-shell.mjs` (Playwright supplied through
`XIAOYOU_QA_PLAYWRIGHT` when needed). It checks the 82%/28% quota case, source
label, adding and removing through the actual renderer/IPC path. It does not
measure a real model's first response or Windows/phone acceptance.

The Windows preview builder pins NSIS toolset 1.2.1, whose native Apple Silicon
compiler avoids the legacy x86-only compiler on this build host. Building an
installer on macOS does not verify execution on Windows.

## Local helper roster (0.1.19)

The sidebar and home helper chips follow the desktop's local Runtime. Remote
mesh aliases such as `cc@Windows` remain task routes but do not appear as local
helpers. Display aliases from Runtime 0.25.0 are respected. Shared side-chat
deletion markers purge cached messages, tasks, and pending sends and prevent
stale peer snapshots from restoring them.
