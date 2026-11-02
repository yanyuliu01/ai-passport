<p align="right"><a href="windows-runtime-recovery-handoff.zh_CN.md">简体中文</a> · <strong>English</strong></p>

# Windows Runtime synchronization recovery handoff

This is a source handoff, not a Windows installer or a statement that the two
computers have passed live acceptance. Read the root agent instructions and
shared history first; inspect the current Windows checkout, running processes,
task state and configuration without printing credentials. Preserve existing
uncommitted Windows work and keep a compatible rollback package and user data.

## Source and current boundary

The `codex/runtime-sync-recovery-20261102` GitHub branch is based on the
Mac/Windows integration branch. It includes synchronization recovery, shared
Runtime history and the directory performance fix installed on the Mac as
Runtime **0.27.7**, with Python source-tree SHA-256
`9b428a6960574b6e76e1350f06ad35cd7f6d473933ee02bdd15c269392683e72`.
Earlier builds with the same version string had different source digests, so
the version alone does not identify the build. The digest uses
the packager's ordered file names and bytes, so Windows line-ending conversion
can change it; compare the Git revision and build manifest as well.

At the last Mac check the main conversation was owned by the Mac; Windows
reported Runtime **0.24.0** and was online. Recheck both facts before acting.
The Mac has `mesh.home=true` and `mesh.require_owner` was not enabled. This
branch includes the tested Runtime source needed by the repair, including the
directory optimization adapted from the separately developed performance
candidate. It does not include later uncommitted Android and desktop work. Do
not replace the Windows desktop client or its uncommitted work with this older
integration snapshot.

To inspect the handoff without switching or resetting an existing checkout,
fetch the branch and create a separate worktree from the repository. If the
suggested branch or folder already exists, choose unused names:

```powershell
git status --short --branch
git remote -v
git fetch https://github.com/yanyuliu01/ai-passport.git refs/heads/codex/runtime-sync-recovery-20261102
git worktree add -b codex/windows-runtime-sync-review ..\ai-passport-runtime-sync-review FETCH_HEAD
```

Inspect the fetched commit and diff against the Windows work before integrating.
Never put real config, tokens, device secrets, history or logs into a commit or
transfer them as part of a build. Do not clear or re-pair profiles to make an
upgrade appear successful.

## Native build and staged upgrade

1. Record the installed Runtime and desktop versions, active work, owner and
   peer state, and the retained Runtime binary, manifest and configuration
   needed for rollback. Do not publish a phone update, flash a device, or
   replace the desktop client as part of this Runtime handoff.
2. Build on Windows with the pinned dependencies in
   `companion/desktop/requirements-build.txt` and
   `companion/desktop/tools/bundle_runtime.py`; a Mac PyInstaller executable is
   not a Windows executable. Work inside the isolated worktree, and verify the
   generated manifest and bundled dependency closure. Run the Runtime suite,
   including `test_projection_recovery.py`, `test_replica_failure.py`,
   `test_owner_fencing_fault.py`, `test_runtime_conversation_sync.py`,
   `test_mesh.py`, `test_ownership.py` and `test_conversation_directory.py`, and
   the applicable repository gate. Report platform-specific failures and skips;
   do not treat Mac results as a Windows pass.
3. Preserve the Windows Runtime's identity, existing mesh secret, peer addresses,
   history and pending work. **Do not set Windows `mesh.home=true`** while the
   Mac owns the main conversation. Verify the actual resolved owner on both
   computers. A setting in a file is not proof that the running process loaded
   it or that the other peer received the binding.
4. Restart or install only within the owner's authorization and the repository's
   active-task rules. Check process health, identity, restored client connection
   and peer synchronization; retain the old package and data until rollback is
   no longer needed. A source push does not install this Runtime.

`mesh.require_owner` defaults to `false`. Only after **every participating
Runtime** supports the option and the owner has been verified, coordinate enabling
it on each member, including the Mac. Unknown or conflicting ownership then
queues ordinary turns on an enabled member; an old or unconfigured member can
still answer locally. Confirm the setting on the running processes after their
authorized restarts. Do not claim complete split-brain protection merely because
Windows was upgraded or the Mac was configured as home.

## Fault, performance and acceptance checks

Check user and assistant messages from both Runtimes appear once under the
correct conversation and remain available after a restart. Explicit `recall`
can search bounded retained archives and already-received peer records; it
reports partial coverage and cannot recover history never received or retained.
Reset-hidden, delegated-worker, fenced and other-conversation records must stay
excluded. Existing automatic local context selection is unchanged; its triggers
and budgets require separate design.

The Mac installation passed 1,392 Runtime host tests and the complete repository
gate. A live Runtime recall returned one retained Windows Runtime conversation;
client-rendered first-response timing, live Windows convergence and network
fault recovery remain unverified.
The installed package reports the same pre-existing missing `sherpa-onnx`
dependency as the previous package; voice was not verified.

Test a temporary projection write failure, retry and process restart; a lost
command acknowledgement and final result; duplicate delivery; unavailable owner
and reconnection. An uncertain side effect must stay unknown until its target
state is checked, not be replayed automatically. Before rolling back to a
version that does not track application progress, resolve pending projections
and verify affected peers have caught up. Process-restart recovery is tested;
power-loss durability is not established.

Measure normal synchronization latency and idle/busy CPU before and after under
the same workload, without adding frequent polling or full-history scans. On
real Windows, Mac and phone clients, check one message identity and final state,
disconnect/reconnect recovery, and first meaningful rendered reply measured
from Send against the 3-second requirement. Report all samples, versions,
transport and remaining failures. Build, mock fault tests, peer-online status
and a GitHub push do not establish those end-to-end results.
