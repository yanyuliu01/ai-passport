package com.yanyuliu.pockethub;

import java.io.IOException;
import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.InetAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.util.*;
import java.util.concurrent.*;
import org.junit.Test;
import static org.junit.Assert.*;

public class FirmwareDownloadTest {
    private void directRequest(int status, int size, boolean shouldSucceed) throws Exception {
        ExecutorService executor = Executors.newSingleThreadExecutor();
        try (ServerSocket server = new ServerSocket(0, 1, InetAddress.getByName("127.0.0.1"))) {
            server.setSoTimeout(5000);
            Future<?> serverResult = executor.submit(() -> {
                try (Socket socket = server.accept()) {
                    socket.setSoTimeout(5000);
                    BufferedReader input = new BufferedReader(new InputStreamReader(socket.getInputStream(), StandardCharsets.US_ASCII));
                    assertEquals("GET /v1/firmware/0123456789ab/image HTTP/1.1", input.readLine());
                    boolean authorized = false;
                    for (String line; (line = input.readLine()) != null && !line.isEmpty();) {
                        if (line.equalsIgnoreCase("Authorization: Bearer fixture-token")) authorized = true;
                    }
                    assertTrue("missing Runtime authorization", authorized);
                    OutputStream output = socket.getOutputStream();
                    output.write(("HTTP/1.1 " + status + " Fixture\r\nContent-Length: " + size
                            + "\r\nConnection: close\r\n\r\n").getBytes(StandardCharsets.US_ASCII));
                    byte[] bytes = new byte[16384]; Arrays.fill(bytes, (byte) 42);
                    try {
                        for (int sent = 0; sent < size; sent += bytes.length)
                            output.write(bytes, 0, Math.min(bytes.length, size - sent));
                    } catch (IOException expectedDisconnect) {
                        if (shouldSucceed) throw expectedDisconnect;
                    }
                } catch (IOException error) { throw new RuntimeException(error); }
            });
            RuntimeClient.Target target = new RuntimeClient.Target("fixture", "http://127.0.0.1:" + server.getLocalPort(), "fixture-token");
            try {
                byte[] result = RuntimeClient.firmwareImage(target, "0123456789ab");
                if (!shouldSucceed) fail("invalid direct response accepted");
                assertEquals(size, result.length);
                for (byte value : result) assertEquals(42, value);
            } catch (IOException expected) {
                if (shouldSucceed) throw expected;
                assertTrue(expected.getMessage().contains(status == 200 ? "8 MB" : String.valueOf(status)));
            }
            serverResult.get(10, TimeUnit.SECONDS);
        } finally { executor.shutdownNow(); }
    }

    @Test public void directRuntimePreservesAuthenticationAndSizeLimit() throws Exception {
        directRequest(200, 4096, true);
        directRequest(401, 0, false);
        directRequest(200, FirmwareDownload.MAX + 1, false);
    }

    private Map<String, Object> chunk(byte[] bytes, int offset, int total) {
        Map<String, Object> result = new HashMap<>();
        result.put("offset", offset); result.put("total", total);
        result.put("_relay_binary", Base64.getUrlEncoder().withoutPadding().encodeToString(bytes));
        return result;
    }
    @Test public void largerThanOnePacketReassemblesExactBytesOnSameOrigin() throws Exception {
        byte[] image = new byte[4 * 1024 * 1024 + 17]; new Random(7).nextBytes(image);
        List<String> requests = new ArrayList<>();
        byte[] result = FirmwareDownload.read("relay://original/v1/firmware/0123456789ab/image", address -> {
            requests.add(address);
            assertTrue(address.startsWith("relay://original/"));
            int offset = Integer.parseInt(address.split("offset=")[1].split("&")[0]);
            return chunk(Arrays.copyOfRange(image, offset, Math.min(image.length, offset + FirmwareDownload.CHUNK)), offset, image.length);
        });
        assertArrayEquals(image, result); assertEquals(5, requests.size());
    }
    @Test public void truncatedOversizedAndInconsistentRepliesFailBeforeUse() throws Exception {
        for (Map<String, Object> reply : Arrays.asList(chunk(new byte[0], 0, 1), chunk(new byte[1], 1, 1),
                chunk(new byte[1], 0, FirmwareDownload.MAX + 1))) {
            try { FirmwareDownload.read("relay://original/image", address -> reply); fail("invalid chunk accepted"); }
            catch (IOException expected) { }
        }
        final int[] calls = {0};
        try {
            FirmwareDownload.read("relay://original/image", address -> calls[0]++ == 0
                    ? chunk(new byte[FirmwareDownload.CHUNK], 0, FirmwareDownload.CHUNK + 1)
                    : chunk(new byte[2], FirmwareDownload.CHUNK, FirmwareDownload.CHUNK + 2));
            fail("changed total accepted");
        } catch (IOException expected) { assertEquals(2, calls[0]); }
    }
}
