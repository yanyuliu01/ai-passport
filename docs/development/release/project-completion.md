<p align="right">
  <a href="project-completion.zh_CN.md">简体中文</a> · <strong>English</strong>
</p>

# Project Completion

When development on a project is finished, the completion flow offers two
optional closing actions. This page is the single authoritative index: it
describes the trigger, the two actions, and the shared safety and consent gates.

The completion flow is not a fixed pipeline and is not tied to a release. The
developer selects either action or both, in any order. Each action runs only
after the developer confirms it.

## When the completion flow is offered

Offer the two actions when either signal occurs:

- The developer says the project is complete (development is done).
- The developer asks to run one of the actions directly.

The [on-device testing invitation](../ai-guide.md#offer-on-device-testing)
is required after every completed firmware implementation, independently of
this menu; do not defer it until a release or a closing action is selected.
Asking is mandatory, but flashing still requires the user's approval. Follow
that handoff for device discovery, the power-on/data-cable/USB prompt when no
device is detected, and reporting tests that remain unperformed.

## The two actions

| ID | Action | Detail |
| --- | --- | --- |
| A | Publish to Git and update the release | [Action A](#action-a-publish-to-git-and-update-the-release) |
| B | Update the root README | [Action B](#action-b-update-the-root-readme) |

## Shared safety and consent gates

Every action follows the same non-negotiable rules:

- Confirm consent before starting.
- Confirm a GitHub channel (GitHub MCP, a GitHub skill, or `gh`) before any
  push or release; if none is available, prepare the content for manual use and stop.
- Do not push, tag or publish until the developer has reviewed and authorized it.
- Do not commit on or modify the developer's current branch without being asked;
  carry the change on a dedicated branch or worktree.
- Never include credentials, device QR secrets, private device links, personal
  data, or unsanitized logs.

## Action A: Publish to Git and Update the Release

This action publishes the firmware or code to the project's repository and,
when intended, updates the GitHub release.

Each step is an external, authorizing mutation. Confirm each one with the
developer separately; one up-front confirmation does not cover commit, push,
tag, and release.

### Steps

1. When preparing a repository release, review the merged user-visible changes
   since the previous release and update both changelog languages as described
   in [Automated Build and Release](../ci/CI-build-and-release.md). Ordinary
   feature, application, and documentation pull requests skip this step.
2. Commit the change and push it — confirm separately.
3. Create and push a tag to trigger the release workflow — confirm separately.
4. Let the tagged build produce the merged firmware `.bin`.
5. Create or update the GitHub release with the artifact — confirm separately.
   The workflow sets the default release title to the version/tag name; after
   the release is up, refine it to the feature name plus the version number.
6. Write release notes in English and Simplified Chinese covering what is new,
   how to build, and how to use.
7. Verify the released full build on hardware (below).

### Rules

- Follow the repository [commit and pull-request rules](../../contribution/commit-and-pr.md).
- A tag-triggered build runs `build-firmware.yml`, which publishes the release
  only for a tag. See [CI-build-and-release.md](../ci/CI-build-and-release.md).
- For day-to-day compilation prefer `idf.py build` (fast, incremental); use
  `./tools/validate.sh --firmware` only when the merged, byte-verified `0x0`
  full image is needed, such as before a release or delivery.
- Release notes must explain the build to a user who has not read the
  repository: what is new, how to build, and how to use.

### Post-release hardware verification

When the release produced a merged full build, verify it on real hardware
before treating the project as complete. Download the release's merged full
firmware (`FoloToy-AI-Passport-full.bin`, the flashable complete build from
`0x0`), flash it to a device, and confirm it runs normally. A successful build
or upload is not hardware validation. If it does not run, stop and fix before
closing out. See [CI-build-and-release.md](../ci/CI-build-and-release.md) for
the artifact and flashing.

Apply the same [device-access and consent checks](../ai-guide.md#offer-on-device-testing)
to this release artifact. Approval to publish is not approval to flash. If
device testing cannot proceed, keep it explicitly unverified.

## Action B: Update the Root README

This action updates the repository's root `README.md` / `README.zh_CN.md` so it
describes the newly released application and keeps its Documentation section
current.

### Rules

- The root README pair is the project's own overview and documentation index;
  update both languages in the same change.
- Describe what the application does and how to use it (its interactions,
  modes, keys, persistence, and notes), not a one-line intro followed by a link.
- Register any new or moved document in the Documentation section.
- Follow the repository language rule: English at the default `.md` path and
  Simplified Chinese at the paired `.zh_CN.md`, with reciprocal switches.

### Steps

1. Confirm consent.
2. Update the README pair on the branch that hosts the application.
3. Commit; pushing is a separate confirmation.

## Related documents

- Commit and pull-request rules: [commit-and-pr.md](../../contribution/commit-and-pr.md)
- Documentation conventions: [doc-conventions.md](../../contribution/doc-conventions.md)
- Tagged firmware builds and releases: [CI-build-and-release.md](../ci/CI-build-and-release.md)
