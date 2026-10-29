<p align="right"><a href="accounts.zh_CN.md">简体中文</a> · <strong>English</strong></p>

# Account discovery and default public relay

Desktop 0.1.13 adds an owner-hosted account and discovery service. Every computer
continues to run its own Runtime. Account login discovers the owner's computers;
it does not turn a new computer into a remote-only client. The existing mesh
identity, task ownership, record synchronization and configured shared-folder
rules remain authoritative. A fresh send prefers the local Runtime; accepted
pending work stays bound to its original Runtime. Main-conversation ownership
and forwarding still follow the Runtime mesh configuration.

## First use

Sign in or create a Xiaoyu account, then configure the computer
from the account panel. Existing accounts recover their original matching Runtime. The desktop already knows the public relay address and its
certificate fingerprint; no domain or JSON import is needed. These are public
routing data, not an embedded access credential. The account is independent of
cloud-provider, OpenAI and model-provider accounts.

On a new computer, sign in with that account and configure its own local Runtime.
The original computer need not be present or online: the client restores the mesh
identity from the account's encrypted recovery envelope, and authenticated device
discovery connects computers automatically. A new private Runtime configuration
receives that identity; model keys and helper execution remain local. Existing
Runtime configurations are not overwritten or restarted to change identity. A
conflicting existing space is reported and preserved for an explicit migration.
Accounts created with 0.1.11 must sign in once on their original computer to
create the recovery envelope; subsequent new computers do not need it online.

Desktop 0.1.17 and Android 0.34.0 use this account server as the only remote
connection entry. Sign in on the phone with the same account after configuring
one computer. The phone validates the encrypted recovery envelope and discovers
account devices automatically; no QR, invitation, LAN address, Tailscale, Funnel,
custom relay, or computer-hosted relay setup is offered. Local computer helpers
still use loopback internally. TLS pinning and endpoint HPKE remain enabled.

Upgrading does not delete legacy pairing records, history, or source-bound pending
requests. New clients do not restore those routes or transfer accepted work to
another computer automatically. Sign in again on the upgraded phone. Account
sessions, endpoint identities, and phone routes are scoped to the account;
signing out stops subscriptions without erasing the local archive. A phone uses
one shared relay socket for its identity when connected to multiple computers.
The old invitation wire protocol remains available internally for account presence
and supported older peers; it is no longer a user-selectable connection mode.

## Preserved boundaries

Successful account authentication authorizes computer and phone enrollment without
approval on another computer. New phones must verify the existing account recovery
envelope before connecting; they cannot create a different mesh identity.
The client encrypts the mesh identity using AES-256-GCM and a password-derived
scrypt key (N=32768, r=8, p=1), with account ID and space bound as authenticated
data. An immutable first-write recovery envelope survives relay restarts; invalid
or altered envelopes fail closed. The server stores ciphertext, not the plaintext
mesh identity. This is not a zero-knowledge password protocol: login sends the
password over pinned TLS to the owner's server, which must be trusted as the
account authority. It sees device identities and routing metadata. Chat remains
endpoint-encrypted and is not archived by the account service.
Passwords must contain at least 8 characters and are stored as salted scrypt hashes; session tokens are stored as hashes.
The desktop keeps the recovery key and login token in OS safe storage, never the
password. Sessions last 30 days, with 16 concurrent computer logins per account.
The private deployment supports at most 32 accounts with bounded auth concurrency
and rate limits. It has no password reset or email recovery flow. A future password
change must rewrap the existing envelope, never silently create a new identity.

Each account receives its own relay credential and room; a credential for one
account cannot access another room. The existing pre-account relay credential
continues working. Account transport has its own local peer database and sealed
keys, preserving legacy pairing and pending work. Logout revokes that computer's
login session, stops its account connection and retains local history; it does
not revoke other enrolled devices or rotate a QR-shared relay credential.

Remote file access follows existing mesh and artifact rules: only explicitly
configured shared folders and existing authorized artifact routes are used.
There is no new arbitrary disk or Runtime-state directory export. Provider keys
are not implicitly copied; existing mesh.share.env entries retain their meaning.
Offline owners retain source-bound pending requests; missing computers do not
cause execution to move silently to another source.

## Deployment and recovery

Compile the desktop main process. Deploy dist-main/relay/server.js and accounts.js,
start-ip.mjs, the locked ws dependency and compose-ip.yaml as compose.yaml. Retain
the existing .env and tls directory, and create an owner-only data directory
owned by UID 1000. The service stores data/accounts.json there; preserve that file
along with .env and TLS identity during updates. The fixed-IP compose template
uses the image digest verified for this deployment and publishes TCP 443 only.
The ordinary domain-based compose template remains available separately.

The owner's default endpoint and certificate pin live in core/account.ts.
A different deployment or renewed certificate requires updating those public
values and issuing a new App build. Never substitute a private relay token into
source control. Back up account data privately; it contains password hashes and
metadata. The cloud server is a live relay and discovery service, not an offline
message mailbox, model worker, or permanent conversation archive.

Rollback can restore the previous relay application and compose configuration
while retaining .env, TLS, account data and all endpoint databases. Legacy paths
remain usable; account connections require an account-capable relay and App.
The previous server does not understand new account logins. Re-enabling the new
version restores the preserved account records. Do not clear data to recover.

## Validation scope

Host coverage includes account-room isolation, credential rejection, bounded
login attempts, old-computer-offline account enrollment, encrypted identity recovery, two real
Runtime processes, configured-file replication, source-bound RPC, and existing
legacy desktop regression. Runtime tests use isolated echo fixtures and do not
establish real-model or rendered first response within three seconds. Installed
Windows, physical Android, BLE and real-user full cross-network acceptance must
be reported separately. Build and deployment do not prove those checks passed.

The server-only source increment adds the phone account API/recovery checks,
account-scoped phone routes, one shared phone identity connected to two computers,
closing one link without dropping the other, and server logout rejection.
Run `node tools/test-android-account.mjs` from `companion/desktop` after building
its main process, with JDK 17 and the Gradle dependency cache available.
The adjacent `node tools/test-android-relay.mjs` checks mixed legacy protocol
compatibility, text/voice/image RPC and reconnection using synthetic local data.
These checks do not measure installed-client rendering, real model TTFT,
cellular recovery, BLE, or cross-device message convergence. The server remains
an online discovery/forwarding service; this increment does not centralize chat
storage or move helpers onto the server.

### Validation checkpoint: 2026-10-28

Candidate identities: desktop 0.1.17 (source/native test window), Android 0.34.0
(code 74), fixed pinned WSS account transport. The running owner's Runtime reports
0.24.0 and was not restarted; firmware behavior was not changed. The Android
candidate preserves the previously published 0.33.4 numeric update-metadata fix.

- Regression: PASS for 115 desktop and 229 Android unit tests, account-only
  two-computer Java interoperability, adjacent legacy RPC, and isolated native
  conversation/task/approval/presentation windows.
- Build: PASS for desktop and Android; the complete repository gate also passed
  its host suite and ESP-IDF 5.5.3 firmware/image validation.
- Connection recovery: PASS in synthetic protocol/window fixtures; the current
  reply-window recovery sample was 181 ms. Real WAN/cellular recovery: NOT RUN.
- First meaningful model response within 3 seconds: NOT RUN, zero real samples.
- Multi-end message synchronization: NOT RUN on installed physical clients.
- Device tests: NOT RUN. Windows native, phone installation, BLE, real provider
  latency and installed-version convergence remain unverified.
- Recovery: original routes, credentials, local archives and source-bound pending
  work remain on disk. Do not clear application data to reconnect. Desktop
  production replacement and any active Runtime restart remain separate from
  source/native-window validation.

### Numeric account route correction: 0.34.1

Android 0.34.0 used `URI.getHost()` for `relay://computer-id.account-id`.
When an account ID started with a digit, Java treated that opaque identifier as
an invalid DNS name and returned no host. Login and discovery succeeded, but
subsequent reads and synchronization could not find their route.
Version 0.34.1 (code 75) reads and strictly validates the raw authority instead.
It preserves stored routes and account/computer identities, without another
login, pairing operation or history reset.

All 231 Android unit tests and the two-computer account interoperability check
passed. Regression covers every hexadecimal leading character, legacy device
identifiers, account scoping and rejection of ports, credentials and encoding.
One USB-connected physical phone retained its login after the 0.34.0 upgrade,
read the computer identity and conversation data, and displayed connected in its
sidebar. No test chat was sent. Owner acceptance remains pending; real model
three-second response, multi-end message convergence and BLE remain unverified.

### Android recipient selection: 0.35.0

Android 0.35.0 (code 76) promotes recipient selection into the Xiaoyu account
sheet. Login uses the same bottom sheet, rounded fields and blue action as the
existing app. Validation and login failures stay inline, with retry available.
Known authenticated Runtime computers appear with their heartbeat availability;
a connected relay alone does not mark a computer online. Other phones are not
listed as Runtime recipients.

An explicit choice is saved per account and survives restart. Discovery of a new
computer never replaces that choice; an unavailable chosen source never causes
a silent fallback. New messages use the selected entry Runtime, while existing
accepted requests remain bound to their original source. Runtime helper routing
is unchanged. Recording and image drafts must finish before switching.

Host tests cover stable selection, account isolation, unavailable targets and
heartbeat expiry. The encrypted two-computer harness verifies each chosen
recipient and the absence of silent fallback. Physical Windows switching,
first-response timing and multi-end message convergence still need live checks.

Validation checkpoint (2026-10-28): the source at base `b8b0886` passed 235
Android unit tests, APK build and the complete repository gate (including the
ESP-IDF 5.5.3 firmware build and merged-image verification). The encrypted
account harness passed login/recovery, two Runtime recipients, explicit switching,
no silent fallback, shared-identity sockets and logout revocation. On the USB
phone, 0.35.0 retained the encrypted account session and saved manual recipient
after an update/relaunch; the account sheet showed the selected Mac and its
heartbeat state against the existing Runtime 0.24.0. This is scoped device
observation, not owner acceptance of the login form or full messaging regression.
Real Windows switching, first meaningful response <= 3 seconds, interruption
recovery during an accepted message and multi-end convergence remain unverified.

### Helper and quota checkpoint (2026-10-28)

Versions: desktop 0.1.18, Android 0.36.0/code 77, Runtime 0.24.1; fixed-server
account transport. Firmware is unchanged. The deployment candidate applies the
helper increment to the prior running Runtime baseline, retaining its deadline
fix and leaving the separate undeployed persona work out. Its Runtime source
SHA-256 is `6c69537ceda635d520ca08128724ec3aa4bb874d5d45033c5ba47c2d137932f5`
on both Mac and Windows bundles.

The actual Codex probe reproduced 401 Unauthorized while old weekly quota
remained visible. Native `account/read` with `refreshToken:true` restored the
same account's query without copying credentials. The corrected ring uses the
limiting live window and suppresses percentages after a failed refresh. Android
also redraws immediately when its asynchronous roster arrives, ignoring old
source callbacks.

- Build: PASS; Android, native Mac app, Windows x64 NSIS installer and the
  complete repository/ESP-IDF 5.5.3 merged-image gate. Apple Silicon installer
  compilation uses the checksum-verified NSIS toolset 1.2.1.
- Host tests: desktop 119 and Android 236 PASS; 36 focused Runtime candidate
  tests PASS. Runtime final full-suite confirmation: PASS (1,183 tests, 204.137s).
- Regression: native source and packaged UI tests passed the synthetic 82%/28%
  quota case and add/remove through renderer/IPC. A temporary live helper
  appeared in the installed Mac roster and usage, then was removed; original
  helpers remained intact. The phone's installed APK and Runtime arm64 update
  metadata both match the final tested APK checksum.
- Device tests: scoped Mac UI and USB phone update/reconnect/main roster observed;
  owner acceptance of phone add/remove and native Windows execution NOT RUN.
- First response <=3s: NOT RUN; no live model messages were sent for this increment.
- Connection recovery: Runtime/desktop restart reconnection observed, but the
  complete timed fault matrix is NOT RUN.
- Multi-end sync: helper membership API/Mac observation and synthetic routing
  passed; live shared-message convergence across phone/Windows/board NOT RUN.
- A first cold frozen-Runtime startup did not become ready in the test's 8-second
  startup window; direct version query and unchanged interop rerun passed.
  The cause and cold-start timing remain unverified. A full Runtime run also
  hit two mesh-offline timing failures; both isolated reruns passed without
  changing assertions or deadlines.
- Recovery: original Mac bundle and prior running Runtime source remain available;
  account/config/history files are retained. Downgrading Runtime ignores the new
  disabled-helper overlay and can make config helpers reappear. An active Runtime
  must not be interrupted for rollback without owner approval.

The unsigned Windows preview installer has SHA-256
`8703f97a4cd0ee13499aee4687b9d95ff952fa94a3e283bbb9c96c8f502e8523`.
Creating this installer is separate from installing and accepting it on Windows.

## Sidebar management delivery — 2026-10-28

Owner scope: phone sidebar long-press management; helper-heading creation; side
chat pin/rename/delete/archive and multi-delete; local helper membership on Mac.
Deletion clears client caches/navigation, keeps diagnostic logs, and has no
restore entry. Archive keeps visible history and can be restored. Different
agents with the same provider/portrait remain individually manageable.

Candidate: Android 0.37.1 (79), desktop 0.1.19, Runtime 0.25.0. Mac is installed
at the previous application path, its account remains connected, and the prior
bundle is retained beside the original application. Runtime was restarted
only after checking zero unfinished requests and zero active owned tasks; its
PID is 3731. The release stages only this chat's helper/sidebar modules over
the previous installed Runtime baseline; unrelated persona changes are excluded.
Android was installed by USB without clearing app data and published through
the Runtime update flow; owner interaction acceptance remains pending.

Windows 0.1.19 preview installer under `sidebar-management-release/companion/desktop/release/`,
127687139 bytes, SHA-256 `6aa97bc428bd557f66d4870208fcd6cb111d1cfa880a769276902e5a0c3df241`. Native Windows execution is NOT RUN.
Mac and Windows Runtime source hash: `322b5d1f4bc083a7c117cad5729f1acb5e80fc46393f188fde0338e36f5644b9`.

Evidence: desktop 121 tests, Android 240 tests, Runtime full suite 1,189 tests;
staged Runtime directory 27, helpers 13, mesh 20 checks. Packaged native Mac
UI passed quota/add/remove checks and excluded a synthetic `cc@Windows` sidebar
entry. Shared deletion tests clear peer/client views, keep logs and reject stale
replay; archive/restore and active-chat deletion refusal pass. Full repository
gate including firmware merged-image verification also passed after the final
Android identity patch. Android update source and the USB-installed arm64 APK
were checked against the final build SHA-256
`429cbf8b99383da2f862b051da0795cd9403002797703bcc11606d77bd052d9a`.
Real model first response <=3s, timed connection recovery, live phone gesture
acceptance, live multi-device chat convergence, and Windows execution remain
unverified. No owner conversation was deleted for testing; firmware was not
flashed. No commit or push was made.

## Context menu follow-up — 2026-10-28

Android 0.37.2 (80) replaces sidebar long-press sheets with anchored floating
menus. Helper menus open immediately; authenticated writes remain bound to the
captured Runtime. Rename/delete confirmations and helper forms are centered,
without sliding or drag handles. Side-chat multi-selection adds Select all /
Deselect all for visible unarchived side chats and excludes the main chat.
Android build and 240 unit tests pass. The app was published and the arm64
update metadata matches the build. USB is now disconnected, so this version
has not been installed or gesture-tested on the phone; it is offered through
the Runtime updater. Runtime 0.25.0 and desktop 0.1.19 remain in place.
The complete repository gate passed for this Android-only follow-up, including firmware merged-image verification. Live first-response timing, connection recovery and multi-device convergence were not exercised.

### Relay APK hosting, 2026-10-29

Android 0.39.1/code 88 adds exact-origin account-certificate pinning for immutable relay APK URLs. The relay streams only hash-named APKs with bounded ranges, HEAD and ETag support; it never exposes account storage. Publisher verification covers Gradle ABI/version consistency, SSH-side hashes and actual pinned HTTPS range responses before updating Runtime metadata. Deployment retained the original certificate, account data and relay secret; only the relay container restarted.

Build/full repository gate PASS; desktop 124 tests, Android 249 tests and two publisher checks PASS. The USB phone reports installed 0.39.1 and connected; the owner confirmed updating. Production Android download classes fetched all 21,941,155 bytes from the real relay and verified SHA-256, then resumed another transfer from 6,045,696 retained bytes and verified the same hash. This is scoped physical download/upgrade evidence, not message timing or full multi-end acceptance. The reported desktop-to-phone reply delay remains a separate active investigation.

Legacy Android preference and keystore names remain compatibility identities. The one-time old-client mirror was removed from Runtime metadata after upgrading; all five ABI URLs now point at the relay. Future owner releases use `tools/publish_app_relay.py`. Previous relay source is retained under its deployment backup directory for rollback; account/TLS/profile data are not cleared.
