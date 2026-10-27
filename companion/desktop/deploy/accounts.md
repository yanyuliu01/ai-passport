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

On the existing computer, connect the existing local Runtime and create a Xiaoyou
account in Settings. The desktop already knows the public relay address and its
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

On the phone, use the existing Scan computer action to scan the desktop's Add
phone QR. It carries the private relay connection, including the TLS fingerprint.
The existing Android invitation protocol is unchanged; no Android binary was
changed. QR generation validates the active account invitation when signed in;
legacy invitations retain their own validation. TLS and endpoint HPKE remain
enabled on numeric-IP connections.

## Preserved boundaries

Successful account authentication authorizes computer enrollment without approval
on another computer. Phone QR pairing retains its existing explicit confirmation.
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
