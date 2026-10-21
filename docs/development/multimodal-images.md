<p align="right"><a href="multimodal-images.zh_CN.md">简体中文</a> · <strong>English</strong></p>

# Image messages

## Baseline and changes

The baseline firmware carried only text in `chat` and `card`. Its binary RX
channel is exclusively for OTA firmware. Pocket Hub could already download and
open task-output images, but the composer and `/v1/messages` accepted only text
or audio; registered output attachments were not an owner-image upload path.

The image increment adds an authenticated upload, durable owner attachments,
native Codex image input, phone composer selection and previews, and an optional
BLE image-preview capability. Existing text, voice, approvals, pairing, firmware
updates, partitions and task routing remain acceptance checks, not assumed passes.

## Supported interaction and limits

- In the phone's main conversation, choose **Add image**, inspect its preview,
  optionally type text, then Send. Tap the selection button to remove a draft.
  One image can be chosen per send; the Runtime protocol allows up to four.
- Received images appear with their original message. Tap to open the original
  or save it through the existing file picker. JPEG EXIF orientation is applied
  to the original viewer; Runtime thumbnails also apply orientation.
- On the device's conversation/task-exchange page, hold UP to open images,
  press UP/DOWN to change images, and OK to return. The existing OK page cycle,
  double-OK menu and hold-OK voice actions remain available outside the viewer.
  Image attachment selection/upload uses the phone; no device gallery or camera
  workflow is introduced.
- The device shows a static, letterboxed **64 x 64 RGB565 preview**, never an
  original-resolution image. A card exposes at most four recent images. Only the focused card catalog is cached on the device, fetched again on focus changes. Decode
  and resize run on the phone, off its UI thread. The device needs 8,192 bytes
  for pixels and retains at least a 16 KiB free-heap reserve before allocation. The added static state budget is 512 bytes.
  If that reserve is unavailable, the owner is told to view the image on the phone.
- New owner uploads are limited to 8 MiB and 16 million pixels, validated by
  Pillow before registration. PNG, JPEG, GIF and WebP are accepted; animated
  formats use a static preview. Uploads are immutable, retained in the existing
  bounded artifact store, and removing a draft does not delete a saved upload.
- This increment supports image sends from the main conversation to a configured
  local **Codex** agent. The current text-only speaker is not given a fake image
  filename as visual input. Task continuation, desktop sessions, mesh forwarding,
  other model types and combined recorded-audio/image sends are not supported;
  unsupported image routes fail before message acceptance. Existing text/voice
  journeys keep their original route.

## Runtime contract

Install `runtime/requirements-images.txt` in the Runtime's Python environment.
`POST /v1/images` takes bounded binary image bytes under the existing bearer
authentication and returns registered public artifact metadata. It never takes a
server filesystem path or arbitrary download URL.

`POST /v1/messages` accepts additive `image_ids`. The Runtime resolves only
registered owner uploads, persists metadata before acknowledging the request,
and includes ordered image identities and hashes in its retry fingerprint.
Reusing `client_id` with different text or images returns 409. `:you` messages,
card entries, sync and history retain the attachments; bytes use the existing
authenticated `/v1/artifacts/<id>/content` and `/thumbnail` endpoints.

The worker receives trusted immutable snapshot paths through `Job.images`:
Codex exec uses `--image`, and app-server uses [`localImage` inputs](https://github.com/openai/codex/blob/main/sdk/python/src/openai_codex/_inputs.py). Paths are
internal only, not substituted for the owner's public message text. No real
provider vision acceptance is established by the fixture tests.

On Windows, artifact readers use CreateFile handles, inspect the final resolved
handle path and reject reparse points/directories. This fixes the baseline use
of POSIX-only `O_DIRECTORY`/`O_NOFOLLOW` without replacing it with a racy
check-then-open fallback. POSIX keeps its existing component-wise no-follow path.

## BLE contract

Firmware announces `"images":1` in its existing hub acknowledgement. Older
firmware continues receiving its usual text protocol; no image packets are sent
unless this capability is present. Older Runtimes reject the new upload endpoint,
so a phone cannot silently send a selected image as text only.

All new commands are bounded UTF-8 JSON lines on the existing secured channel.
The OTA `0xFE` frame path and its frame source are unchanged.

| Direction | `cmd:image` operation | Purpose |
| --- | --- | --- |
| Phone to device | `list`, `card`, `ids` | Bind up to four artifact IDs to a card |
| Phone to device | `clear` | Clear image state on Runtime/source changes |
| Device to phone | `catalog`, `card` | Request the focused card catalog |
| Phone to device | `catalog_error`, `card` | Focused card catalog unavailable |
| Device to phone | `get`, `card`, `id`, `token` | Request one offered preview |
| Phone to device | `begin`, `id`, `token`, `size`, `crc` | Declare 8,192 RGB565 bytes and CRC32 |
| Phone to device | `chunk`, `id`, `token`, `offset`, `data` | At most 384 decoded bytes, Base64 encoded |
| Device to phone | `ack`, `token`, `next`, `status` | Stop-and-wait acknowledgement; next contiguous offset |
| Phone to device | `end`, `id`, `token` | Publish only a complete CRC-checked buffer |
| Phone to device | `error`, `id`, `token` | Unavailable/download/decode failure |

There is only one transfer at a time. Connection generation and transfer tokens
reject stale packets. The phone retries a pending packet at most three times,
and also bounds acknowledgements that make no progress. The device abandons an
incomplete buffer after 15 seconds without a packet. Closing, changing source,
leaving the page, voice/approval/update preemption and disconnect detach LVGL
references under its lock before freeing the buffer. A failed preview leaves
text and firmware recovery usable.

## Validation and recovery

`runtime/tests/test_image_inputs.py` covers bounded registration, decoding,
authenticated upload/send/download, immutable reads across store reload,
request identity, unsupported routing and native Codex image arguments.
`tests/test_pocket_images.c` covers complete CRC transfer, duplicate chunks,
invalid metadata, timeout, low heap and generation invalidation. Its codec shim
supplies fixed pixel fixtures; the firmware links the actual mbedTLS codec.
The existing gate runs this test when cJSON is available.

Real installed phone/device rendering, image colors, glyphs, voice coexistence,
BLE reconnect, OTA/rollback, provider visual understanding and cross-client
convergence still require device evidence. A canned upload acknowledgement or
handoff is not meaningful model text and does not pass the 3-second response
contract. Report sample timings separately.

Keep the previous compatible App and Runtime publication and verified firmware
archive. No partition or pairing migration is introduced. Rolling back code
leaves additive attachment metadata readable as existing artifacts. Updating an
active Runtime follows its existing restart-approval rule; publishing an App is
not proof of installation, and a BLE push is not proof of hardware acceptance.
