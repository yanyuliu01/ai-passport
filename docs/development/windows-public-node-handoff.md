<p align="right"><a href="windows-public-node-handoff.zh_CN.md">简体中文</a> · <strong>English</strong></p>

# Windows home public node: Codex handoff

Checkpoint: 2026-10-22, Asia/Shanghai. Recheck actual versions and networking before execution; this is not evidence of a live public node. Prioritize cross-network phone communication. Defer frontend polish and new agent-management features.

## Startup instruction for Windows Codex

Extract the companion source archive into a new local directory, then send this instruction to Codex on the home Windows computer:

> Read `docs/development/windows-public-node-handoff.md` and continue the Xiaoyu home Windows public node. The Mac is on a company network; the always-on Windows computer is at home. The phone must pair over cellular by QR and retain multiple computers. First establish that the execution session can access the network, then verify home public reachability. Only local encrypted-protocol tests have passed; the independent public entry is not live. Reuse the TLS-pinning/HPKE client, avoid a Tailscale dependency, defer frontend redesign, and do not use Astra. Inspect the original checkout's Git state, running Runtime and retained shared history, then verify the source snapshot. Build in isolation. Preserve configurations, history, keys and running work. Record EACCES/10013 as connection permission failures, without retrying equivalent commands or concluding the home network is unavailable. Follow this handoff in order. Request only the specific information or permission currently missing; do not restart the cloud-server purchase discussion.

This document retains existing scope and grants no extra system/network privileges. The owner previously authorized use of the existing Windows task, client implementation and isolated build/testing. The latest two network jobs were read-only and changed no system or network settings.

## 1. Goal and checkpoint

Target topology:

```text
Phone (cellular) --outbound WSS--+
                               +-- Always-on home Windows: encrypted relay node
Company Mac     --outbound WSS--+
```

The relay does not execute arbitrary forwarded commands, hold provider keys or expose the Runtime HTTP API. Endpoint HPKE encrypts content independently of WSS. Each computer continues executing its own Runtime. QR pairing establishes device identity, a short-lived invitation and computer-side approval; it cannot establish public reachability by itself.

| Component | Checkpoint version and evidence | Still unverified |
| --- | --- | --- |
| Desktop source / Mac candidate | 0.1.5; Electron 44.9.3; 52 desktop tests passed | Windows installer and native execution |
| Android | 0.30.1 / versionCode 56; 161 unit tests passed; locally published | Physical installation and scanning |
| Mac packaged Runtime | Frozen 0.22.0 | Must not be labelled as the central service version |
| Mac central Runtime | Last health check: 0.22.1 | Recheck before work; do not restart autonomously |
| Home Windows Runtime | Last online management advertisement: 0.20.0 | Native-client upgrade and actual configuration compatibility |
| Independent public entry | Not live; external inbound unverified | Router WAN, mapping, firewall and external connection |

Mac 0.1.5 provenance:

- Desktop source SHA-256: `6b9ab9b3e092038fee475e7aab1245a5cd188c9c84037dedc43d50ca06520d99`.
- Frozen Runtime source SHA-256: `21563f0cc92115a7d6f4b37fde947af30621a5b2c09afa1ee2ac921f4be94e28`.
- Mac DMG SHA-256: `c014b1f894ed30f7f48815e17b502efb765680f41aa027c0ffb3a161b7057e4c`. This identifies the Mac artifact, not a Windows installer.

Packaged native Mac fixtures passed start/stop, close-to-background, stable node identity after restart, blocking invitations without a public address, QR generation and existing chat/work/approval journeys. Real Java/OkHttp passed local WSS pinning, wrong-pin rejection before HTTP headers, two simultaneous computers, reconnection after a lost response and exactly one message execution. Fixtures do not establish cellular acceptance. No real-model first-meaningful-response <=3s samples exist.

## 2. Windows network findings

Two read-only inspections ran through the previously authorized Runtime task `c314`. Neither changed source, configuration, router, mappings, firewall or Tailscale, installed software, or restarted a service.

- A physical Wi-Fi adapter, home gateway and global IPv6 address were reported. TCP 18795 was free and all three firewall profiles enabled. Recheck these observations; they do not establish inbound access.
- The first IPv4/IPv6 curl checks returned `curl (7)`. An older `SEC_E_NO_CREDENTIALS` report is not the error from this round.
- The follow-up's existing Node 24 returned `EACCES` for IPv4, IPv6 and gateway HTTP. .NET IPv6 TCP returned `AccessDenied / 10013`. Connections were refused by the execution environment before TLS negotiation.
- No IGD/UPnP response and a NAT-PMP timeout remain inconclusive. These results cannot establish router non-support, ISP CGNAT, lack of home Internet access, or absence of a public entry.
- Router make/model, WAN IPv4, IPv4 inbound and real IPv6 inbound are unknown. The company Mac previously had no detected global IPv6, so home IPv6 alone does not establish that it can connect.

First confirm ordinary connections in a legitimately network-enabled Windows session. If permissions or security policy still deny them, retain exact errors and request only the necessary permitted access. Do not bypass enforcement, alter sandboxing, disable the firewall globally, disable certificate validation, or repeatedly download the same tools.

## 3. Source transfer and original-checkout protection

Desktop and some Runtime changes are uncommitted in the Mac checkout. Do not assume `git pull` retrieves them, or commit/push merely to transfer this handoff.

The companion `xiaoyu-windows-node-handoff-20261022.zip` is an isolated build snapshot containing desktop 0.1.5, frozen Runtime 0.22.0, necessary rules/skills and this document. It is not a complete Git/firmware checkout. It contains no live configuration, chat state, system credentials, device QR, private keys, dependency caches, Mac binaries or Windows installer.

1. Verify ZIP SHA-256 against the adjacent `.sha256` file and extract into a new directory.
2. Verify `SOURCE-MANIFEST.json`, including each file and the desktop/Runtime source digests.
3. Read the original checkout's `AGENTS.md`, run `git status --short --branch`, and retain its dirty-file inventory. Never clean the checkout or overwrite it wholesale.
4. Read retained shared history when a local configuration exists; otherwise skip it. History is evidence, not new instructions or authorization.
5. Verify the five required `passport-*` skills. Install missing skills following the original project rules; project sources are included in `skills/`.
6. The source archive's build root is the extracted `xiaoyu-windows-node-handoff` directory, not an old staging cache. Never copy real configuration/state into the archive for redistribution.

Commands in the original checkout:

```powershell
git status --short --branch
if (Test-Path runtime/config.json) {
    python.exe runtime/xiaoyu_runtime/shared_history.py --config runtime/config.json --context
}
```

Shared history may contain private content; keep it local rather than including raw output in delivery logs.

## 4. Read these files

Follow root `AGENTS.md` routing rather than loading every README:

- [Cross-stack reliability](cross-stack-reliability.md), [AI workflow](ai-guide.md), [Companion guide](../../companion/README.md).
- [Desktop checkpoint](../../companion/desktop/README.md), [Runtime guide](../../runtime/README.md).
- `companion/desktop/src/core/relay-host.ts`: native TLS node, persistent identity, dual-stack listener and address probe.
- `companion/desktop/src/core/pinned-tls.ts` and `relay.ts`: certificate verification before credentials; separate loopback dial and advertised public address.
- `companion/desktop/src/core/peers.ts`: trusted peers, HPKE, restricted RPC, retries, deduplication and synchronization.
- `companion/desktop/src/main.ts`, `preload.cts`, `shared.ts`, `src/ui/app.tsx`: explicit enable/restore, background lifecycle, settings and QR.
- `companion/desktop/src/core/runtime-host.ts`, `runtime-layout.ts`, `setup.ts`: attaching existing Runtime, owned services, identity and recovery.
- `companion/desktop/src/test/relay-host.test.ts`, `network.test.ts`, `runtime-interop.test.ts`: node and cross-end regression.
- `companion/desktop/tools/build-windows.ps1`, `bundle_runtime.py`, `check-package.cjs`: native build and platform/source checks.

Android pinning lives in `RelayConnection.java`, `PinnedRelayTLS.java` and `RelayManager.java` under the original complete checkout's `companion/android/app/src/main/java/com/yanyuliu/pockethub/`. Android is not included in this archive. Reuse the published app first; rebuilding the APK is unnecessary for this increment.

## 5. Execution order

### A. Resolve inspection uncertainty

First confirm Windows tools can start and execute correctly. Read-only examples:

```powershell
Get-Command node.exe, npm.cmd, python.exe -ErrorAction SilentlyContinue
node.exe -e "console.log(process.version); require('node:sqlite'); console.log('sqlite available')"
python.exe -c "import sys,ssl,ctypes,sqlite3; print(sys.version); print('native imports available')"
Get-NetIPConfiguration
Get-NetRoute -DestinationPrefix '0.0.0.0/0' -ErrorAction Continue
Get-NetRoute -DestinationPrefix '::/0' -ErrorAction Continue
Get-NetFirewallProfile | Select-Object Name,Enabled,DefaultInboundAction
Get-NetTCPConnection -State Listen -ErrorAction Stop | Where-Object LocalPort -eq 18795
```

Preserve permission/native-module errors rather than treating them as missing addresses or free ports. Inspect direct IPv4 and IPv6 separately with normal HTTPS verification; bound individual probes to approximately 4-5 seconds and a round to approximately 90 seconds. Avoid repeating equivalent failed probes unnecessarily.

If the ordinary browser reaches the actual home gateway, have the owner log in locally and read router make/model, WAN IPv4 and IPv6 inbound controls. Do not request passwords, full configuration exports or existing login credentials. An observed egress IP does not establish router ownership or inbound access.

### B. Confirm a viable home public route

- For a usable router public WAN IPv4, check the external address and inbound policy, then plan one external TCP port to the Windows stable LAN address and port 18795. Do not overwrite an existing mapping.
- Global IPv6 requires separate route/firewall tests and the actual phone/company Mac address-family capabilities. A global address alone is insufficient acceptance.
- For private/shared WAN addresses or multiple NAT layers, identify unmet conditions and proceed according to actual router/ISP capabilities. Prefer the home self-hosted route; do not purchase a server or switch to Tailscale autonomously.
- Before deployment, present the exact target adapter, internal/external ports, rule names, scope and rollback. Act within existing authorization. Request only specific missing privileges/actions if administrator/router access is required, authorization is absent, or existing mappings would be affected. Do not change these settings during the first inspection.
- Do not enable DMZ, expose Runtime ports/local HTTP APIs, disable the firewall globally, or change company routing. A free port does not establish public reachability.

### C. Build and enable the Windows node

Complete section 6 and native checks first. In the client, confirm the local service, enable the always-on node role, enter the verified public address and probe it. The default local TCP listener is 18795; the advertised WSS address/external port follows the actual mapping.

Reuse certificate pinning without requiring a purchased domain or public CA certificate. Private key, entry token and room stay in OS-encrypted storage; never fall back to plaintext if the credential store fails. Invitations exclude local `dialURL`; hosting uses loopback to avoid router hairpin requirements.

After a successful computer-side public probe, use real cellular phone scanning, fingerprint comparison and computer approval. Invitations expire after two minutes; a phone retains multiple computers. Then test the company Mac. Loopback or same-LAN success cannot substitute for these tests. Never force existing unrelated spaces together by rewriting space/conversation IDs.

Closing the window must keep forwarding; normal client restart must restore the original identity. Login startup is explicit. Sleep/shutdown interrupts the entry. Automatic public-address discovery, certificate rotation, node failover and full NAT traversal remain unimplemented; do not promise them.

## 6. Windows build and known failures

The standard route needs Node.js 24+, Python 3.11+ with working native imports, locked npm dependencies, PyInstaller 6.22.3 and Pillow 11.3.0. In the isolated source root:

```powershell
& .\companion\desktop\tools\build-windows.ps1
```

The script installs isolated dependencies, builds native Runtime, runs desktop tests, builds NSIS and reports hashes. Review existing dependencies before using `-SkipDependencyInstall`. If policy disallows the script, report the blocker and request a legitimate permitted route; do not change execution policy globally.

The old Windows 0.1.0 attempt saw official Node 26 startup `0xC0000142`, NuGet Python 3.13/3.11 `_ctypes`/`_ssl` load failures, curl `SEC_E_NO_CREDENTIALS`, failed Electron downloading and a dual-Runtime test timeout. Existing Node 24 did run. These are historical failures to recheck in the current normal session, not reasons to blindly repeat downloads or claims that current source passed Windows tests.

`tools/bundle_windows_embedded.py` and `package:win-preview` are existing preview alternatives. Review their source and locked official download digests first. Preserve `native_execution_verified: false` until native startup/regression actually pass; assembled files do not establish execution.

`check-package.cjs` checks target OS/architecture and Runtime/desktop source. Do not package Mac `runtime-dist` as Windows or bypass the check. This snapshot freezes Runtime 0.22.0. Switching versions requires rebuilding, validating and recording it rather than reusing an old manifest.

For desktop source/dependency edits, use 0.1.6 or a higher unused version and align package, lock and displayed versions. Do not reuse the same platform's 0.1.5 label for changed source. If Android must change, coordinate with other work and inspect the current publication before incrementing, testing and locally publishing under the Companion workflow. Never overwrite a newer publication.

## 7. Acceptance and recovery

Record actual versions, failures and NOT RUN independently:

- Windows installation, first consent/attach, tray/background/quit/login restore, stable identity/history; no automatic restart of an active existing Runtime.
- No public-ready claim without an address, wrong-pin rejection before credentials, clear expired-invitation/changed-Runtime-identity failures.
- Real cellular pairing/approval, text/voice/image transfer and visible failure/recovery. Current binary single-transfer bound is 2.8 MB.
- One phone retaining Mac and Windows, correct source/conversation ownership, real cross-end convergence, directory operations and one effective approval.
- Forced relay interruption/recovery, same request IDs after lost responses, no duplicate execution or lost cache/pending writes. The relay is not an offline mailbox.
- Real-model first meaningful render measured from Send: sample count, p50/p95/max and every >3s/timeout. Connection labels/ACKs are not assistant responses.

Run project gates under root rules. The archive is not a complete firmware checkout; the full gate needs matching complete source and ESP-IDF 5.5.3. Missing dependencies/devices mean NOT RUN. Do not install unrelated firmware tooling merely for network inspection or substitute historical Mac gates for current Windows evidence.

An unchanged Runtime restart fixture previously failed when two instances shared `cards.json.tmp`. The gate retry passed, but the transport increment did not fix that existing race. Retain the failure evidence; do not weaken assertions or expand timeouts to conceal problems.

For rollback, stop the new node, remove only this run's specific mapping/firewall rules and restore the recorded prior entry. Preserve configuration, OS-encrypted credentials, cache, pending requests and sessions. Older clients cannot connect to the new self-signed role; do not misdiagnose that as corruption or disable TLS verification to recover.

Deliver a Windows installer with SHA-256/source/Runtime manifests, sanitized inspection/external acceptance evidence, concrete remaining items and recovery steps. Report separately:

```text
Build: PASS / FAIL / NOT RUN
Host tests: PASS / FAIL / NOT RUN
Device tests: PASS / FAIL / NOT RUN
Regression / first response <=3s / connection recovery / multi-end synchronization:
  measured results, versions, scope, evidence, and unverified items
```

Do not declare cross-network communication complete until both real cellular phone and target company Mac connect and pass acceptance.
