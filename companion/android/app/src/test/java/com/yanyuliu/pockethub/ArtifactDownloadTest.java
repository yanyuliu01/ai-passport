package com.yanyuliu.pockethub;

import java.io.File;
import java.nio.file.Files;
import java.nio.charset.StandardCharsets;
import org.junit.Test;
import static org.junit.Assert.*;

public class ArtifactDownloadTest {
    private static final String ID = "a0123456789abcdef0123456789abcdef";
    private Attachment item(String sha, int size) {
        return Attachment.from(Json.parseObject("{\"id\":\"" + ID + "\",\"status\":\"ready\",\"size\":" + size + ",\"sha256\":\"" + sha + "\"}"));
    }

    @Test public void authAndVerifiedSnapshotRoundTripPreservesBinaryBytes() throws Exception {
        byte[] payload = new byte[] {0, (byte) 255, 32, 10, 13};
        LocalServer server = new LocalServer(payload, "test-token");
        File directory = Files.createTempDirectory("artifact-test").toFile();
        File output = new File(directory, "content.bin");
        try {
            String sha = hex(java.security.MessageDigest.getInstance("SHA-256").digest(payload));
            ArtifactDownload.download(server.address(), "test-token", item(sha, payload.length), false, output);
            assertArrayEquals(payload, Files.readAllBytes(output.toPath()));
            assertEquals(1, directory.list().length);
        } finally { server.close(); output.delete(); directory.delete(); }
    }

    @Test public void wrongHashAndRedirectNeverCreateCachedArtifact() throws Exception {
        byte[] payload = "bad".getBytes(StandardCharsets.UTF_8);
        LocalServer server = new LocalServer(payload, "token");
        File directory = Files.createTempDirectory("artifact-test").toFile();
        File output = new File(directory, "content.bin");
        try {
            String address = server.address();
            try { ArtifactDownload.download(address, "token", item("0".repeat(64), 3), false, output); fail("wrong hash accepted"); }
            catch (java.io.IOException expected) { assertTrue(expected.getMessage().contains("校验")); }
            assertFalse(output.exists()); assertEquals(0, directory.list().length);
            try { ArtifactDownload.download(address, "token", item("", 3), true, output); fail("redirect followed"); }
            catch (java.io.IOException expected) { assertTrue(expected.getMessage().contains("302")); }
            assertEquals(0, directory.list().length);
        } finally { server.close(); output.delete(); directory.delete(); }
    }

    @Test public void runtimeAndRevisionArePartOfCacheIdentity() {
        assertNotEquals(ArtifactDownload.key("http://one\n" + ID), ArtifactDownload.key("http://two\n" + ID));
        assertNotEquals(ArtifactDownload.key("http://one\n" + ID + "\nold"), ArtifactDownload.key("http://one\n" + ID + "\nnew"));
    }
    private String hex(byte[] bytes) { StringBuilder value = new StringBuilder(); for (byte b : bytes) value.append(String.format("%02x", b & 255)); return value.toString(); }
    private static final class LocalServer implements AutoCloseable {
        final java.net.ServerSocket socket;
        final Thread thread;
        LocalServer(byte[] payload, String token) throws Exception {
            socket = new java.net.ServerSocket(0, 50, java.net.InetAddress.getByName("127.0.0.1"));
            thread = new Thread(() -> {
                while (!socket.isClosed()) {
                    try (java.net.Socket client = socket.accept()) {
                        java.io.BufferedReader reader = new java.io.BufferedReader(new java.io.InputStreamReader(client.getInputStream(), StandardCharsets.US_ASCII));
                        String request = reader.readLine(), line;
                        boolean authenticated = false;
                        while ((line = reader.readLine()) != null && !line.isEmpty()) if (line.equals("Authorization: Bearer " + token)) authenticated = true;
                        boolean redirect = request != null && request.contains("/thumbnail ");
                        byte[] body = redirect ? new byte[0] : payload;
                        String response = "HTTP/1.1 " + (redirect ? "302 Found" : authenticated ? "200 OK" : "401 Unauthorized")
                                + "\r\nContent-Length: " + body.length + "\r\nConnection: close\r\n"
                                + (redirect ? "Location: /unexpected\r\n" : "") + "\r\n";
                        client.getOutputStream().write(response.getBytes(StandardCharsets.US_ASCII));
                        client.getOutputStream().write(body);
                    } catch (java.io.IOException ignored) { if (socket.isClosed()) break; }
                }
            });
            thread.setDaemon(true); thread.start();
        }
        String address() { return "http://127.0.0.1:" + socket.getLocalPort(); }
        @Override public void close() throws Exception { socket.close(); thread.join(1000); }
    }
}
