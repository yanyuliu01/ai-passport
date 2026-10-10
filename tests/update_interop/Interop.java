package com.yanyuliu.pockethub;

import java.io.BufferedReader;
import java.io.BufferedWriter;
import java.io.File;
import java.io.IOException;
import java.io.InputStreamReader;
import java.io.OutputStreamWriter;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.Random;

/**
 * 手机 App 的传输代码（FirmwarePush、BuddyProtocol）和固件自己的协议代码
 * （tests/update_interop/peer.c 编出来的程序）面对面跑一遍：两头对线上格式的
 * 理解只要有一处不一样，这里就过不了。由 tools/validate.sh 调用：
 *
 *   java … com.yanyuliu.pockethub.Interop <peer 可执行文件> <临时目录>
 */
public final class Interop {
    private final Process process;
    private final BufferedWriter toPeer;
    private final BufferedReader fromPeer;

    private Interop(String peer) throws IOException {
        process = new ProcessBuilder(peer).redirectErrorStream(true).start();
        toPeer = new BufferedWriter(
                new OutputStreamWriter(process.getOutputStream(), StandardCharsets.UTF_8));
        fromPeer = new BufferedReader(
                new InputStreamReader(process.getInputStream(), StandardCharsets.UTF_8));
    }

    /** 送一条记录，取回设备这时会说的所有话。 */
    private List<String> exchange(String record) throws IOException {
        toPeer.write(record);
        toPeer.write('\n');
        toPeer.flush();
        List<String> lines = new ArrayList<>();
        for (String line = fromPeer.readLine(); line != null; line = fromPeer.readLine()) {
            if (line.equals(".")) {
                return lines;
            }
            lines.add(line);
        }
        throw new IOException("peer exited");
    }

    private static String hex(byte[] data) {
        StringBuilder out = new StringBuilder(data.length * 2);
        for (byte value : data) {
            out.append(String.format("%02x", value & 0xFF));
        }
        return out.toString();
    }

    private static void check(boolean condition, String what) {
        if (!condition) {
            throw new AssertionError(what);
        }
    }

    private static void transfer(String peer, File folder, int size, int lossPercent, long seed)
            throws IOException, InterruptedException {
        byte[] image = new byte[size];
        Random random = new Random(seed);
        random.nextBytes(image);
        Interop link = new Interop(peer);
        long now = 0;

        // 设备先说说自己：这一行由固件的代码写出，由 App 的代码读。
        BuddyProtocol.FwLine info =
                BuddyProtocol.parseFw(link.exchange("L " + BuddyProtocol.fwOp("info").trim()).get(0));
        check(info != null && info.ok && info.op.equals("info")
                && info.text("build").equals("00112233aabbccdd") && info.text("state").equals("valid")
                && info.text("slot").equals("ota_0") && info.number("max", 0) == 4 * 1024 * 1024,
                "info round trip");
        // confirm、abort、rollback 这几行固件都认得。
        for (String op : new String[] {"confirm", "abort", "rollback"}) {
            BuddyProtocol.FwLine answer =
                    BuddyProtocol.parseFw(link.exchange("L " + BuddyProtocol.fwOp(op).trim()).get(0));
            check(answer != null && answer.ok && answer.op.equals(op), op + " round trip");
        }

        FirmwarePush push = new FirmwarePush(image, FirmwareSelfTest.sha256(image), now);
        List<String> pending = link.exchange("L " + push.beginLine().trim());
        while (push.active() && now < 600000) {
            for (String line : pending) {
                BuddyProtocol.FwLine parsed = BuddyProtocol.parseFw(line);
                check(parsed != null, "the app understands what the device says: " + line);
                push.onLine(parsed, 239, now);
            }
            pending = new ArrayList<>();
            for (int burst = 1 + random.nextInt(5); burst > 0; --burst) {
                byte[] frame = push.nextFrame(now);
                if (frame == null) {
                    break;
                }
                if (random.nextInt(100) >= lossPercent) {
                    pending.addAll(link.exchange("F " + hex(frame)));
                }
            }
            String line = push.takeLine(now);
            if (line != null) {
                pending.addAll(link.exchange("L " + line.trim()));
            }
            now += 20;
            pending.addAll(link.exchange("T 20 " + (200 + random.nextInt(900))));
            push.onTick(now);
        }
        for (String line : pending) {
            push.onLine(BuddyProtocol.parseFw(line), 239, now);
        }
        check(push.state() == FirmwarePush.State.DONE,
                size + " bytes, loss " + lossPercent + "%: " + push.state() + " " + push.error());
        File flash = new File(folder, "flash-" + size + "-" + lossPercent + ".bin");
        link.toPeer.write("Q " + flash.getPath() + "\n");
        link.toPeer.flush();
        check(link.process.waitFor() == 0, "peer wrote its flash");
        check(Arrays.equals(Files.readAllBytes(flash.toPath()), image),
                "what the firmware code received is what the app sent");
        check(flash.delete(), "clean up");
    }

    public static void main(String[] arguments) throws Exception {
        String peer = arguments[0];
        File folder = new File(arguments[1]);
        transfer(peer, folder, 70001, 0, 1);
        transfer(peer, folder, 70001, 10, 2);
        transfer(peer, folder, 4096, 0, 3);
        transfer(peer, folder, 200003, 25, 4);
        // 固件拒绝的 begin，App 拿到的是固件给的原因。
        Interop link = new Interop(peer);
        FirmwarePush tiny = new FirmwarePush(new byte[100], FirmwareSelfTest.sha256(new byte[100]), 0);
        tiny.onLine(BuddyProtocol.parseFw(link.exchange("L " + tiny.beginLine().trim()).get(0)), 239, 0);
        check(tiny.state() == FirmwarePush.State.FAILED && tiny.error().equals("size"),
                "a refused begin: " + tiny.error());
        link.process.destroy();
        System.out.println("Firmware update interop (app code against firmware code): PASS");
    }
}
