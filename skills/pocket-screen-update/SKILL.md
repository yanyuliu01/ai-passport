---
name: pocket-screen-update
description: Turn one spoken remark about the device's screen into new firmware pushed to the device, or put the device back on an earlier version. Use for requests Xiaoyou hands over such as "this looks bad", "make it look like this" or "go back to the previous one"; updates over Bluetooth, never by cable.
---

<p align="right"><a href="SKILL.zh_CN.md">简体中文</a> · <strong>English</strong></p>

# Change the screen and push it to the device

The owner is probably not at a keyboard: they said one sentence to the device and
Xiaoyou handed it over. Do not keep asking questions. Take the smallest sensible
reading, do it, and say in two or three sentences what changed and how to undo it.

Before starting, run `git status --short --branch` in the repository root and read
`AGENTS.md` and the section "Replacing the firmware over Bluetooth" in
`docs/claude-pocket.md`. The runtime commands below run in `runtime/`; the
configuration file defaults to `config.json` there.

## Only going back to a version

When the owner says "go back to the previous one", "restore version 5" or "that
one looks worse, undo it", no code changes:

```bash
python3 -m xiaoyou_runtime firmware list              # the versions; ● is what the device runs
python3 -m xiaoyou_runtime firmware restore previous  # the previous one; or a number, such as 5
```

While the previous version is still in the device's other slot, the device
switches back in seconds. An older version is transferred again, a minute or two.

Going back changes only the firmware on the device; the code in the repository
stays as it is. So it matters why the owner goes back:

- They do not want the last change ("looks worse", "undo it", "never mind"): take
  it out of the code as well, or the next screen change will bring it back.
  `firmware list` shows the commit each version was built from. For every commit
  after that one that only changed the screen (it touches nothing outside
  `main/`, `assets/fonts/` and `tools/ui_preview/`), run
  `git revert --no-edit <commit>`, newest first (and push if this computer pushes). Do not build or fetch
  a new firmware for this revert: the device already runs the version they asked
  for. If one of those commits also changed something else, do not revert it;
  stop and say so in your answer.
- They only want to look at an older one ("switch to version 5 for a moment"):
  leave the code alone, and say in your answer that the code has not changed and
  the next screen change still starts from the newest code.

## Changing the screen

1. Read what you are about to change. Layout, colors and spacing are in
   `main/pocket_ui.c`; the wording in `main/pocket_text.h`; which view is shown in
   `main/pocket_view.c`; Xiaoyou's sprite in `main/pocket_pet.c`; what a key press
   leads to in `main/buddy_state.c`. Change only what the remark is about.
2. Make the change. Chinese text on the screen lives only in `main/pocket_text.h`;
   after changing it run `python3 tools/gen_pocket_fonts.py` to regenerate the fonts.
3. Leave these alone (if this change breaks them, the next fix cannot be pushed):
   `main/pocket_update.c`, `main/pocket_update_core.*`, the receive path in
   `main/buddy_ble.c`, `partitions.csv`, and the "replace the firmware over
   Bluetooth" block in `sdkconfig.defaults`. Keep the progress view shown during an
   update working as it is. If these really have to change, stop and tell the owner
   that this one needs them at the computer.
4. Run `./tools/validate.sh --static`. Fix what fails; if you cannot, say so. Do not
   push a version that did not pass.
5. Commit to the current feature branch (commit messages follow
   `docs/contribution/commit-and-pr.md`). The commit only has to exist on this
   computer: the history is kept by the local git and the runtime's firmware library.
6. Get the new firmware to the device. Look at `firmware.build_command` in
   `runtime/config.json` first:
   - It is set (this computer has ESP-IDF, the usual case): build here. GitHub is
     not needed and nothing is pushed.

     ```bash
     python3 -m xiaoyou_runtime firmware build --push --note "one sentence on what this version changes"
     ```

     The first build takes a few minutes, later ones under a minute. If it does
     not compile, fix it; if you cannot, say so.
   - It is not set: let GitHub build. That needs `git push` to work on this
     computer; if the push fails, stop and tell the owner the two ways out: run
     `tools/install_idf.sh` to build locally, or sign in to GitHub. After the push:

     ```bash
     python3 -m xiaoyou_runtime firmware fetch --commit HEAD --wait 900 --push --detach \
         --note "one sentence on what this version changes"
     ```
7. Answer: what changed; that in a few minutes the device will show its "changing into
   something new" progress screen and restart by itself when done; that saying "go back to the previous one" undoes it.
   Say that the result has not been seen on a device.

## What happens next

The runtime records this version as the one the device should run. The phone app,
while connected to the device, fetches the image and writes it over Bluetooth. The
device checks it and restarts; the phone then confirms the new firmware, and
without that confirmation within three minutes the device goes back to the
previous version by itself. A failed build, or two failed installs in a row, is
recorded by the runtime and shown on the "device firmware" line of the phone app.
`python3 -m xiaoyou_runtime firmware status` shows where things stand at any time.

Every version stays in the runtime's `state/firmware/`; nothing is removed
automatically.

## Not done here

No flashing by cable, no partition table changes, no `--force` to push an old
version that "can only be replaced by cable afterwards", no deleting versions from
the library. Those need the owner present.
