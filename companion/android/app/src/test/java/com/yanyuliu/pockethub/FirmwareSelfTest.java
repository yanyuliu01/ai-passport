package com.yanyuliu.pockethub;

import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.Random;

/**
 * 换固件那一段的自检：线上的文本和数据帧长什么样，以及 FirmwarePush 在各种情况下
 * 能不能把一份镜像完整地交到设备手里。单元测试和电脑上的命令行都调用 run()。
 *
 * 这里的“设备”是照着固件的 main/pocket_update_core.c 写的替身：按偏移收数据、
 * 缓冲区有限、隔一段报一次进度。它证明的是手机这一头守不守规矩，不是固件本身。
 */
public final class FirmwareSelfTest {
    private FirmwareSelfTest() {
    }

    private static void check(boolean condition, String what) {
        if (!condition) {
            throw new AssertionError(what);
        }
    }

    static String sha256(byte[] data) {
        try {
            byte[] digest = MessageDigest.getInstance("SHA-256").digest(data);
            StringBuilder out = new StringBuilder();
            for (byte value : digest) {
                out.append(String.format("%02x", value & 0xFF));
            }
            return out.toString();
        } catch (NoSuchAlgorithmException error) {
            throw new AssertionError(error);
        }
    }

    /** 固件那一头的替身。常数和固件的 pocket_update_core.h 一致。 */
    static final class FakeDevice {
        static final int WINDOW = 2048;
        static final int BUFFER = WINDOW + 244;
        static final int REPORT_BYTES = 1024;
        static final long REPORT_IDLE_MS = 1000;

        final List<String> out = new ArrayList<>();
        byte[] flash = new byte[0];
        byte[] buffer = new byte[BUFFER];
        int buffered;
        int size;
        String sha = "";
        int received;
        int done;
        boolean active;
        boolean gap;
        boolean restarted;
        int overruns;
        int resumeAt;
        /** 第一次问 end 时假装还差最后这么多字节没收到。 */
        int forgetTail;
        private int reportedDone;
        private boolean reportedGap;
        private boolean reported;
        private long reportedMs;

        void line(String line, int chunk) {
            java.util.Map<String, String> fields = BuddyProtocol.parseFlatObject(line.trim());
            check(fields != null && "fw".equals(fields.get("cmd")), "device got a fw command: " + line);
            String op = fields.get("op");
            if ("begin".equals(op)) {
                size = Integer.parseInt(fields.get("size"));
                sha = fields.get("sha256");
                flash = Arrays.copyOf(flash, size);
                received = resumeAt;
                done = resumeAt;
                buffered = 0;
                gap = false;
                active = true;
                reported = false;
                out.add("{\"ack\":\"fw\",\"ok\":true,\"op\":\"begin\",\"offset\":" + resumeAt
                        + ",\"chunk\":" + chunk + ",\"window\":" + WINDOW + "}");
            } else if ("end".equals(op)) {
                write(Integer.MAX_VALUE);
                if (forgetTail > 0) {
                    received -= forgetTail;
                    done -= forgetTail;
                    forgetTail = 0;
                }
                if (done != size) {
                    out.add("{\"ack\":\"fw\",\"ok\":false,\"op\":\"end\",\"error\":\"incomplete\","
                            + "\"got\":" + received + "}");
                } else if (!sha256(flash).equals(sha)) {
                    active = false;
                    out.add("{\"ack\":\"fw\",\"ok\":false,\"op\":\"end\",\"error\":\"sha256\"}");
                } else {
                    active = false;
                    restarted = true;
                    out.add("{\"ack\":\"fw\",\"ok\":true,\"op\":\"end\"}");
                }
            }
        }

        void frame(byte[] frame) {
            check((frame[0] & 0xFF) == 0xFE && frame.length > 5 && frame.length <= 244,
                    "a frame is one write that starts with 0xFE");
            int offset = (frame[1] & 0xFF) | (frame[2] & 0xFF) << 8 | (frame[3] & 0xFF) << 16
                    | (frame[4] & 0xFF) << 24;
            int length = frame.length - 5;
            if (!active || offset > size || length > size - offset || offset < received) {
                return;
            }
            if (offset > received || length > BUFFER - buffered) {
                if (length > BUFFER - buffered) {
                    ++overruns;
                }
                gap = true;
                return;
            }
            System.arraycopy(frame, 5, buffer, buffered, length);
            buffered += length;
            received += length;
            gap = false;
        }

        void write(int limit) {
            int count = Math.min(buffered, limit);
            System.arraycopy(buffer, 0, flash, done, count);
            System.arraycopy(buffer, count, buffer, 0, buffered - count);
            buffered -= count;
            done += count;
        }

        void maybeReport(long nowMs) {
            boolean due = !reported || done - reportedDone >= REPORT_BYTES
                    || (done == size && reportedDone != size) || (gap && !reportedGap)
                    || nowMs - reportedMs >= REPORT_IDLE_MS;
            if (!active) {
                return;
            }
            if (!due) {
                if (!gap) {
                    reportedGap = false;
                }
                return;
            }
            reported = true;
            reportedDone = done;
            reportedGap = gap;
            reportedMs = nowMs;
            out.add("{\"evt\":\"fw\",\"got\":" + received + ",\"done\":" + done
                    + (gap ? ",\"rewind\":true" : "") + "}");
        }
    }

    private static byte[] image(int size, long seed) {
        byte[] data = new byte[size];
        new Random(seed).nextBytes(data);
        return data;
    }

    /** 把一份镜像传完（或者传到失败），返回走了多少个“毫秒”。lossPercent 是每帧丢掉的概率。 */
    private static long transfer(FirmwarePush push, FakeDevice device, int lossPercent, long seed,
                                 int ownChunk) {
        Random random = new Random(seed);
        long now = 0;
        device.line(push.beginLine(), 239);
        while (push.active() && now < 600000) {
            for (String line : new ArrayList<>(device.out)) {
                push.onLine(BuddyProtocol.parseFw(line), ownChunk, now);
            }
            device.out.clear();
            for (int burst = 1 + random.nextInt(5); burst > 0; --burst) {
                byte[] frame = push.nextFrame(now);
                if (frame == null) {
                    break;
                }
                check(push.sent() <= push.done() + FakeDevice.WINDOW,
                        "never further ahead than the window");
                if (random.nextInt(100) >= lossPercent) {
                    device.frame(frame);
                }
            }
            String line = push.takeLine(now);
            if (line != null) {
                device.line(line, 239);
            }
            device.write(200 + random.nextInt(900));
            now += 20;
            device.maybeReport(now);
            push.onTick(now);
        }
        for (String line : device.out) {
            push.onLine(BuddyProtocol.parseFw(line), ownChunk, now);
        }
        device.out.clear();
        return now;
    }

    public static void run() {
        // ---- 线上的样子 ----
        check(BuddyProtocol.fwOp("info").equals("{\"cmd\":\"fw\",\"op\":\"info\"}\n"), "fw info");
        check(BuddyProtocol.fwOp("confirm").equals("{\"cmd\":\"fw\",\"op\":\"confirm\"}\n"),
                "fw confirm");
        check(BuddyProtocol.fwBegin(1694896, "ab12").equals(
                "{\"cmd\":\"fw\",\"op\":\"begin\",\"size\":1694896,\"sha256\":\"ab12\"}\n"), "fw begin");
        byte[] bytes = {10, 11, 12, 13, 14, 15, 16};
        byte[] frame = BuddyProtocol.fwFrame(bytes, 2, 3);
        check(Arrays.equals(frame, new byte[] {(byte) 0xFE, 2, 0, 0, 0, 12, 13, 14}), "frame bytes");
        byte[] far = BuddyProtocol.fwFrame(new byte[0x01020305], 0x01020304, 1);
        check(far[1] == 4 && far[2] == 3 && far[3] == 2 && far[4] == 1, "offset is little-endian");
        // 语音帧用 0xFF，固件帧用 0xFE，两个都不会出现在文本里。
        check(!VoiceRecording.isFrame(frame), "a firmware frame is not a voice frame");

        BuddyProtocol.FwLine info = BuddyProtocol.parseFw("{\"ack\":\"fw\",\"ok\":true,\"op\":\"info\","
                + "\"build\":\"0123456789abcdef\",\"ver\":\"403725c\",\"state\":\"pending\","
                + "\"slot\":\"ota_1\",\"prev\":\"\",\"max\":4128768}");
        check(info != null && info.ack && info.ok && info.op.equals("info"), "info ack");
        check(info.text("build").equals("0123456789abcdef") && info.text("state").equals("pending")
                && info.text("prev").isEmpty() && info.number("max", 0) == 4128768, "info fields");
        check(info.text("missing").isEmpty() && info.number("missing", 7) == 7
                && info.number("ver", -1) == -1, "absent or non-numeric fields fall back");
        BuddyProtocol.FwLine progress =
                BuddyProtocol.parseFw("{\"evt\":\"fw\",\"got\":4780,\"done\":4096,\"rewind\":true}");
        check(progress != null && !progress.ack && progress.number("got", 0) == 4780
                && progress.number("done", 0) == 4096 && progress.flag("rewind"), "progress line");
        BuddyProtocol.FwLine old =
                BuddyProtocol.parseFw("{\"ack\":\"fw\",\"ok\":false,\"error\":\"unknown command\"}");
        check(old != null && old.ack && !old.ok && old.op.isEmpty()
                && old.error.equals("unknown command"), "old firmware's answer");
        check(BuddyProtocol.parseFw("{\"ack\":\"hub\",\"ok\":true}") == null, "not about firmware");
        check(BuddyProtocol.parseFw("{\"cmd\":\"voice\",\"state\":\"start\"}") == null, "nor this");
        check(BuddyProtocol.parseFw("garbage") == null, "nor garbage");

        // ---- 干净的连接：一次传完，一帧不多发 ----
        byte[] firmware = image(70001, 1);
        FakeDevice device = new FakeDevice();
        FirmwarePush push = new FirmwarePush(firmware, sha256(firmware), 0);
        check(push.state() == FirmwarePush.State.BEGINNING && push.nextFrame(0) == null,
                "nothing is sent before the device says begin is fine");
        transfer(push, device, 0, 1, 239);
        check(push.state() == FirmwarePush.State.DONE, "clean transfer: " + push.error());
        check(device.restarted && Arrays.equals(device.flash, firmware), "the image arrived intact");
        check(device.overruns == 0 && push.rewinds() == 0, "no overruns and no resends");
        check(push.percent() == 100 && push.done() == firmware.length, "reports 100% when done");

        // ---- 会丢帧的连接：照样传完，内容不差 ----
        for (int loss : new int[] {3, 15, 40}) {
            for (long seed = 1; seed <= 12; ++seed) {
                device = new FakeDevice();
                push = new FirmwarePush(firmware, sha256(firmware), 0);
                transfer(push, device, loss, seed * 31 + loss, 239);
                check(push.state() == FirmwarePush.State.DONE,
                        "loss " + loss + "% seed " + seed + ": " + push.error());
                check(Arrays.equals(device.flash, firmware), "intact despite loss " + loss + "%");
                check(device.overruns == 0, "the window is respected even while resending");
            }
        }

        // ---- 手机的 MTU 比设备说的小：按小的切帧 ----
        device = new FakeDevice();
        push = new FirmwarePush(firmware, sha256(firmware), 0);
        device.line(push.beginLine(), 239);
        push.onLine(BuddyProtocol.parseFw(device.out.remove(0)), 100, 0);
        check(push.nextFrame(0).length == 105, "frame size is the smaller of the two limits");

        // ---- 断线重连后接着传：设备说从哪儿开始就从哪儿开始 ----
        device = new FakeDevice();
        device.flash = Arrays.copyOf(firmware, firmware.length);
        Arrays.fill(device.flash, 30000, firmware.length, (byte) 0);
        device.resumeAt = 30000;
        push = new FirmwarePush(firmware, sha256(firmware), 0);
        long took = transfer(push, device, 0, 5, 239);
        check(push.state() == FirmwarePush.State.DONE && Arrays.equals(device.flash, firmware),
                "resumed transfer completes");
        check(took < 3000, "and only the rest is sent (took " + took + " ms of model time)");

        // ---- 设备说还没收全：补上再问 ----
        device = new FakeDevice();
        device.forgetTail = 500;
        push = new FirmwarePush(firmware, sha256(firmware), 0);
        transfer(push, device, 0, 6, 239);
        check(push.state() == FirmwarePush.State.DONE && Arrays.equals(device.flash, firmware),
                "an incomplete end is finished off: " + push.error());

        // ---- 设备不干：原因原样带出来 ----
        push = new FirmwarePush(firmware, sha256(firmware), 0);
        push.onLine(BuddyProtocol.parseFw(
                "{\"ack\":\"fw\",\"ok\":false,\"op\":\"begin\",\"error\":\"unconfirmed\"}"), 239, 5);
        check(push.state() == FirmwarePush.State.FAILED && push.error().equals("unconfirmed"),
                "a refused begin fails with the device's reason");
        check(push.nextFrame(10) == null && push.takeLine(10) == null, "and sends nothing more");
        push = new FirmwarePush(firmware, sha256(firmware), 0);
        push.onLine(BuddyProtocol.parseFw(
                "{\"ack\":\"fw\",\"ok\":false,\"error\":\"unknown command\"}"), 239, 5);
        check(push.state() == FirmwarePush.State.FAILED && push.error().equals("unknown command"),
                "firmware from before this feature");
        // 镜像传坏了（这里是说好的 SHA-256 不对）：设备核对不过。
        device = new FakeDevice();
        push = new FirmwarePush(firmware, sha256(image(10, 9)), 0);
        transfer(push, device, 0, 7, 239);
        check(push.state() == FirmwarePush.State.FAILED && push.error().equals("sha256"),
                "a hash mismatch is a failure");
        check(!device.restarted, "and the device does not switch");

        // ---- 等不到回话 ----
        push = new FirmwarePush(firmware, sha256(firmware), 1000);
        push.onTick(1000 + FirmwarePush.BEGIN_TIMEOUT_MS);
        check(push.active(), "still waiting at the limit");
        push.onTick(1001 + FirmwarePush.BEGIN_TIMEOUT_MS);
        check(push.state() == FirmwarePush.State.FAILED && push.error().equals("no answer"),
                "begin unanswered");
        push = new FirmwarePush(firmware, sha256(firmware), 0);
        push.onLine(BuddyProtocol.parseFw("{\"ack\":\"fw\",\"ok\":true,\"op\":\"begin\",\"offset\":0,"
                + "\"chunk\":239,\"window\":2048}"), 239, 0);
        int frames = 0;
        while (push.nextFrame(1) != null) {
            ++frames;
        }
        check(frames == 9 && push.sent() == 2048, "one window is sent, then it waits");
        push.onTick(FirmwarePush.STALL_TIMEOUT_MS);
        check(push.active(), "waiting for the device to catch up");
        push.onTick(FirmwarePush.STALL_TIMEOUT_MS + 1);
        check(push.state() == FirmwarePush.State.FAILED && push.error().equals("stalled"),
                "no progress for too long");
        // 写不通的进度行不改变任何东西。
        push = new FirmwarePush(firmware, sha256(firmware), 0);
        push.onLine(BuddyProtocol.parseFw("{\"ack\":\"fw\",\"ok\":true,\"op\":\"begin\",\"offset\":0,"
                + "\"chunk\":239,\"window\":2048}"), 239, 0);
        push.nextFrame(0);
        push.onLine(BuddyProtocol.parseFw("{\"evt\":\"fw\",\"got\":999999,\"done\":5}"), 239, 1);
        push.onLine(BuddyProtocol.parseFw("{\"evt\":\"fw\",\"got\":100,\"done\":200}"), 239, 1);
        push.onLine(BuddyProtocol.parseFw("{\"evt\":\"fw\"}"), 239, 1);
        check(push.done() == 0 && push.sent() == 239, "nonsense progress is ignored");
    }

    public static void main(String[] arguments) {
        run();
        System.out.println("Firmware push self-test: PASS");
    }
}
