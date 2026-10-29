<p align="right"><a href="app-sidebar-polish-handoff.zh_CN.md">简体中文</a> · <strong>English</strong></p>

# Phone App Sidebar Polish: Codex Handoff

Handoff date: 2026-10-28, Asia/Shanghai. This is a checkpoint, not proof of implementation. Re-check the source and the version on the phone before starting. UI strings are paraphrased in English here; the Chinese peer quotes them exactly.

## Start instruction for Codex

> Read `docs/development/app-sidebar-polish-handoff.md` and continue polishing the Android sidebar and image sending in `/Users/weihaoliu/ai-passport-synced-20261028`. Follow `passport-develop`: fix the two bugs first, then rearrange the sidebar as described in section 3, with helpers pinned above Settings as on the desktop client; then make image sending work like WeChat (send on pick) as described in section 4. Change only `companion/android`, on branch `feature/account-login-app-polish`; do not push. After each part, install over USB and check it against section 7. When everything is done, publish with `app publish` and a plain-language `--note`.

## 1. Current state

- The source is in `/Users/weihaoliu/ai-passport-synced-20261028`, on branch `feature/account-login-app-polish` (based on `b8b0886`). Earlier rounds are committed separately for Runtime, desktop and Android, and not pushed.
- The phone and the Runtime have **Android 0.37.4 (82)**. Since 0.37.3: anchored long-press menus with Delete last, round selection checks, archive selected, and centered actions for archived side chats. 0.37.4 fixes the image upload transport; see section 4. The next version starts at 0.37.5 (83).
- Desktop **0.1.20** lets the relay pass messages that carry images. It is installed on the Mac (the previous bundle is kept beside it with the suffix `-before-image-update`), and the Runtime was not restarted. Windows still runs 0.1.19, so images sent through that route still fail.
- 0.37.4 also fixes Retry in a side chat, which sent the message to the main chat because `RuntimeClient.retry` hard-coded `default`. Retries now go back to their own conversation. This was not separately verified on the device.
- Local build:

  ```bash
  cd /Users/weihaoliu/ai-passport-synced-20261028/companion/android
  T=/Users/weihaoliu/.local/share/ai-passport-toolchain
  JAVA_HOME=$T/jdk/jdk-17.0.20.1+1/Contents/Home ANDROID_HOME=$T/android-sdk ./gradlew -q testDebugUnitTest assembleDebug
  ```

  Baseline: all 240 unit tests pass.
- Install on the phone (serial `20c3b39d`) with `$T/android-sdk/platform-tools/adb install -r app/build/outputs/apk/debug/app-arm64-v8a-debug.apk`. Locate text with `uiautomator dump` before tapping; fixed coordinates are unreliable.
- Publish the same way Codex did before. From `runtime/`, run `python3 -m xiaoyu_runtime --config /Users/weihaoliu/ai-passport/runtime/config.json app publish <universal.apk> --variant <abi>=<apk> … --note '…'`.

## 2. Fix two bugs first

### 2.1 Side chats with a title open empty

Two side chats have a title and an update time, but open to the new-chat empty state ("just say what you want to talk about") with no messages: the "refactor integration test" and "refactor desktop display test" chats, whose first messages asked for a fixed sync-check reply. The other four side chats load normally. They may have been created during Windows sync tests, so the directory metadata reached the Mac but the messages did not. **This is a guess and has not been checked.**

Required:

1. Ask the Mac Runtime whether these two conversations have messages, and find which layer loses them: the Runtime itself, the phone's sync cache (`SyncCache` / `ChatHistory`), or rendering on the phone.
2. If the fault is on the phone side, fix it. If the messages exist only on another computer, the phone must say clearly that the record is on that computer and has not synced yet. **Never** show the new-chat empty state for a conversation that has a title and an update time.
3. Two-way history sync is a separate known issue and is out of scope here. If the cause is sync, record and report it; do not change the Runtime along the way.

### 2.2 Side chats open at the oldest message

`MainActivity.onHubChanged()` calls `scrollDown()` only when the view was already at the bottom (`atBottom`) and new messages arrived (`grew`). `atBottom` uses the previous conversation's scroll position, so after switching to another conversation the view stays at the top.

Required: after entering a conversation through `enterConversation()`, `openMainChat()` or Back, scroll to the newest message once the first screen is drawn. When history arrives in pages (`catchingUp`), land at the bottom after it finishes. Once the user has scrolled up on their own, do not pull them back down.

## 3. Sidebar rearrangement (confirmed by the owner)

Match the desktop client's `<aside className="sidebar">` in `companion/desktop/src/ui/app.tsx`. On desktop the top is fixed, the side-chat area scrolls on its own, and Helpers and Settings stay fixed at the bottom.

```text
┌ fixed ──────────────────────┐
│ avatar · Xiaoyu · ● connected│  ← the only place connection state appears
│ Main chat                    │
│ In progress            [2]   │
│ Side chats              ＋    │
├ scrolls on its own ─────────┤
│ 📌 pinned side chat…  11:52   │
│ side chat title…          ●  │
│ …                            │
│ Archived 1                   │  ← small link as the last row of the list
├ fixed at the bottom ────────┤
│ Helpers                 ＋    │
│ (Codex)  Codex       idle    │
│ (Claude) Claude      working │
│ ───────────────────────────  │
│ ⚙ Settings                   │  ← remove the "connected" label on the right
└─────────────────────────────┘
```

Details:

1. **Move Helpers above Settings.** The owner sees helpers as part of settings. The helper heading, the helper rows and Settings share the fixed bottom block, with a thin divider above the helper heading. When there are many helpers, cap their area and let it scroll on its own. Desktop uses `max-height: 220px`; on the phone, about three rows. Keep the "＋" next to the helper heading, which the owner asked for earlier. Desktop has no such button; leave that difference as it is.
2. **The side-chat area scrolls on its own.** As on desktop, list every side chat instead of cutting to the most recent ones. "Archived N" changes from a large row that looks like navigation to a small link as the last row of the side-chat list.
3. **Use one row style everywhere.** Main chat, In progress and Settings use the same `listRow` base as side chats: the same left inset, the same corner radius, and press feedback on every row. Today `groupRow` has no press background, and its icon sits further left than the side-chat text. Use 14sp everywhere. Side-chat rows must line up on the left: either reserve an icon-width gap or drop the icons everywhere; either is fine, as long as it is consistent.
4. **Highlight where the user is.** On Main chat (voice or text), In progress or Settings, tint the matching row with the same light blue as a selected side chat (`0xffeef4ff`). Desktop uses `.selected`.
5. **Show time and unread separately.** Time gets its own column. Put the unread dot left of the time or right of the title; it must never replace the time. Time format: `HH:mm` for today, "Yesterday" (Chinese) for yesterday, month and day for earlier this year, and "yyyy/M/d" for older. Keep at least 8dp between the title and the time.
6. **Show connection state once.** Keep "● connected" in the identity block at the top, with a green or gray dot. Remove `drawerState` from the right of the Settings row.
7. **Helper state wording and color.** Use the desktop state set: waiting for you in amber, working in blue, idle and unreachable in gray. Keep the phone-only "running on the computer" state in blue. Today `stateText()` paints everything blue except "confirm" and "unknown"; color each state separately.
8. **Quota ring on the avatar.** The short red arc on the Claude avatar has no explanation. Follow the desktop `.quota-value`: show a tiny remaining-percent label under the avatar, or explain it in the quota panel on tap or long-press. At minimum, it must read as quota.

Leave alone: the long-press menu, selection, select all, archive selected and delete confirmation, all verified on the device in 0.37.3. Do not change desktop code.

## 4. Image sending: work like WeChat (confirmed by the owner)

The owner asked to align image sending with WeChat, to change the interaction as well, and to send images as soon as they are picked instead of waiting for uploads to finish.

### 4.1 Transport problems already fixed (committed)

Before 0.37.4, no image could be sent through the relay, for three reasons:

1. The phone declared uploads as `application/octet-stream`, while the desktop relay (`allowedRPC` in `peers.ts`) only passes `image/jpeg|png|webp`. The phone now sends the real type and turns HEIC, GIF and other formats into JPEG.
2. `RelayConnection.tick()` resent every unacknowledged packet every second, so large packets piled up on the uplink. A timed-out image upload was also kept as a durable write and replayed every 5 seconds. Now a resend waits until the socket queue has drained, large packets wait at least 10 seconds between sends, and image uploads are no longer durable writes.
3. The desktop relay's field allowlist for `/v1/messages` had no `image_ids`, so every message carrying images was refused, and the error was flattened into "409, record mismatch". Desktop 0.1.20 allows `image_ids` (one to four) and returns the real error.

Images are also compressed like WeChat's default (non-original) send: a 1920 long edge for photos and at most 1 MB per image; tall screenshots are bounded by total pixels so text stays readable; the phone now reads originals up to 40 MB. The reason is that every relayed request has a 30-second desktop deadline, and large bodies do not finish on mobile networks.

Device result: on 0.37.4, three test images (an 11 MB photo, a 17 MB tall noise image and a screenshot) all uploaded within 18 seconds. With desktop 0.1.20 on the Mac, an image message sent from a temporary side chat landed in that side chat and Xiaoyu received the image (it then handed viewing to Codex, which was out of quota at the time).

### 4.2 Interaction to build

1. **Send on pick.** After the owner confirms in the system picker, the chosen images appear at once in the chat as an outgoing message: on the right, as aspect-correct thumbnails with upload progress on top. There is no draft strip above the input and no need to wait before pressing send. Uploads run in order in the background; when all are done, call `POST /v1/messages` with empty `text` (the Runtime fills in a default prompt). Text being typed in the input is not attached; it stays in the input, as in WeChat.
2. **Several images at once.** The Runtime takes at most four images per message (`image_inputs.MAX_IMAGES`). Allow up to nine in the picker and split the selection into messages of at most four.
3. **Send state on the bubble.** Show progress while uploading. On failure, show a red exclamation mark beside the bubble; tapping it resends. The background queue keeps going when the owner leaves the page or switches side chats. If the app is killed, unfinished image messages show as failed and can be resent; never drop them silently.
4. **How sent images look.** Today a sent image shows as a file card (name, "0 B", MIME type and a "view original / save" button) on the left, as if someone else sent it. Show it on the right as a plain rounded image bubble with no caption. Tapping opens a full-screen viewer with pinch zoom, swiping between the images of that message, and long-press to save.
5. **The ＋ panel.** Remove the "File" option from the attachment sheet; it cannot be used yet. Use a WeChat-style icon grid with Album and Camera. Camera opens the system camera and then follows item 1.
6. **Original-size toggle (optional).** WeChat offers an "original" toggle. Skip it for now: the relay takes about 2.8 MB within 30 seconds per request. Doing it would first need the desktop deadline and limit raised.

Change only `companion/android`. Keep the 0.37.4 transport fixes (`ImagePreparation`, `ImageUpload`, `RelayConnection`, `RuntimeClient.uploadImage`); do not revert them.

## 5. Other screens (lower priority; do after the sidebar)

- **"In progress" counts disagree.** Settings says one thing is in progress, the In progress page says nothing is in progress, and the helper row says Claude is running on the computer. Use one source of truth for all three. If "running on the computer" means a native session is running on the computer, it should not count as an in-progress item, but the Settings count must match the In progress page.
- **Quoted context in side chats is not rendered as Markdown.** The quoted-context block (taken from the main chat) shows raw `**bold**` markers and `---` rules. Render it like a message bubble, covering at least bold, rules and lists.
- **The In progress page is too bare.** It has one line of 12sp gray text plus a collapsed "› Finished 4". Use `emptyState()` for the empty state, matching the chat page, and make Finished a clear group entry.
- **Settings**
  - Companion persona shows the bare code "Xiaoyu" on the right; show the persona name instead.
  - Xiaoyu account and Helper management use the same computer icon; give them different icons.
  - Hide Developer under About, or show it only after tapping the version several times.
- **Cold start briefly shows the "Sign in to Xiaoyu" button.** When an account is signed in, show a loading state or skeleton until the account state is read, instead of drawing the sign-in page first.

## 6. Same issues on desktop (noted, out of scope)

- The side-chat list also lets the unread dot replace the time, and `shortTime()` shows only hours and minutes, with no date.
- There is no Archived entry; archiving is done from the directory page.

## 7. Acceptance checklist (screenshot each item on the device)

1. The two side chats either show their messages or a clear not-synced explanation, never the new-chat empty state.
2. Entering any long side chat from the sidebar shows the newest message first. After scrolling up, a new message does not pull the view back down.
3. Top to bottom, the sidebar shows identity, Main chat, In progress, Side chats, the Archived link, Helpers and Settings. With many side chats, only the middle area scrolls, and Helpers and Settings stay visible.
4. On Main chat, In progress, Settings and any side chat, the matching sidebar row is highlighted.
5. All rows line up on the left and every row gives press feedback.
6. Time and the unread dot are visible together. Side chats from yesterday or earlier show a date.
7. "Connected" appears only once in the sidebar.
8. Idle helpers are gray, working helpers blue, and waiting-for-you helpers amber.
9. 0.37.3 long-press, selection, select all, archive selected and delete still work. Test them on temporary side chats, never on the owner's real ones.
10. In a temporary side chat, pick five images: two image messages (four and one) appear at once, each shows progress, both send by themselves when uploaded, and Xiaoyu can see the images.
11. Turn the network off mid-upload: the bubble shows a red exclamation mark; after the network returns, one tap resends it successfully.
12. Tapping a sent image opens full screen, with zoom, left-right switching and long-press to save.
13. All unit tests pass and `assembleDebug` passes. If the sidebar structure changes, add matching presentation tests.

Report the result of every item, and name what was not verified.
