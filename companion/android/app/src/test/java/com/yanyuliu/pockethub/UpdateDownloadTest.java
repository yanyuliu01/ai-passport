package com.yanyuliu.pockethub;

import static org.junit.Assert.*;
import java.io.File;
import java.io.IOException;
import java.net.InetAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import java.util.HashMap;
import java.util.Map;
import java.util.concurrent.atomic.AtomicReference;
import java.nio.file.Files;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.List;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicInteger;
import org.junit.After;
import org.junit.Before;
import org.junit.Test;

public class UpdateDownloadTest {
    private static final byte[] APK = new byte[8192];
    static { for (int i = 0; i < APK.length; i++) APK[i] = (byte) (i * 37); }
    private ServerSocket server;
    private final AtomicReference<Throwable> serverFailure = new AtomicReference<>();
    private ExecutorService worker;
    private File directory, file, part;
    private String address, sha;
    private final AtomicInteger requests = new AtomicInteger();
    private final List<String> ranges = Collections.synchronizedList(new ArrayList<>());
    private final List<Integer> progress = new ArrayList<>();
    private interface Reply { void send(Request request, int attempt) throws IOException; }
    private volatile Reply reply;

    @Before public void start() throws Exception {
        StringBuilder hash = new StringBuilder();
        for (byte b : MessageDigest.getInstance("SHA-256").digest(APK)) hash.append(String.format("%02x", b & 255));
        sha = hash.toString();
        directory = Files.createTempDirectory("update-download-test").toFile();
        file = new File(directory, sha + ".bin"); part = new File(file.getPath() + ".part");
        server = new ServerSocket(0, 16, InetAddress.getByName("127.0.0.1"));
        worker = Executors.newCachedThreadPool();
        reply = (request, attempt) -> send(request, false, -1);
        worker.execute(() -> {
            while (!server.isClosed()) {
                try {
                    Socket socket = server.accept();
                    worker.execute(() -> {
                        try (Request request = new Request(socket)) {
                            ranges.add(request.headers.get("range"));
                            assertEquals("Bearer fixture-token", request.headers.get("authorization"));
                            assertEquals("\"" + sha + "\"", request.headers.get("if-match"));
                            reply.send(request, requests.incrementAndGet());
                        } catch (IOException expectedDisconnect) {
                            // Fault tests deliberately close connections before the body ends.
                        } catch (Throwable failure) { serverFailure.compareAndSet(null, failure); }
                    });
                } catch (IOException closed) {
                    if (!server.isClosed()) serverFailure.compareAndSet(null, closed);
                }
            }
        });
        address = "http://127.0.0.1:" + server.getLocalPort() + "/apk";
    }

    private static final class Request implements java.io.Closeable {
        final Socket socket;
        final Map<String, String> headers = new HashMap<>();
        final Map<String, String> responseHeaders = new HashMap<>();
        Request(Socket socket) throws IOException {
            this.socket = socket;
            socket.setSoTimeout(5000);
            BufferedReader input = new BufferedReader(new InputStreamReader(socket.getInputStream(), StandardCharsets.US_ASCII));
            input.readLine();
            String line;
            while ((line = input.readLine()) != null && !line.isEmpty()) {
                int colon = line.indexOf(':');
                headers.put(line.substring(0, colon).toLowerCase(java.util.Locale.ROOT), line.substring(colon + 1).trim());
            }
        }
        void sendResponseHeaders(int status, long length) throws IOException {
            StringBuilder output = new StringBuilder("HTTP/1.1 " + status + " Fixture\r\n");
            output.append("Content-Length: ").append(Math.max(0, length)).append("\r\nConnection: close\r\n");
            for (Map.Entry<String, String> header : responseHeaders.entrySet())
                output.append(header.getKey()).append(": ").append(header.getValue()).append("\r\n");
            output.append("\r\n");
            socket.getOutputStream().write(output.toString().getBytes(StandardCharsets.US_ASCII));
        }
        OutputStream getResponseBody() throws IOException { return socket.getOutputStream(); }
        public void close() throws IOException { socket.close(); }
    }

    @After public void stop() throws Exception {
        if (server != null) server.close();
        if (worker != null) { worker.shutdownNow(); worker.awaitTermination(2, java.util.concurrent.TimeUnit.SECONDS); }
        assertNull(String.valueOf(serverFailure.get()), serverFailure.get());
        if (directory != null) {
            File[] files = directory.listFiles(); if (files != null) for (File item : files) item.delete();
            directory.delete();
        }
    }

    private void send(Request request, boolean ignoreRange, int truncate) throws IOException {
        String range = request.headers.get("range");
        int start = ignoreRange || range == null ? 0 : Integer.parseInt(range.substring(6, range.length() - 1));
        request.responseHeaders.put("ETag", "\"" + sha + "\"");
        if (start > 0) request.responseHeaders.put("Content-Range", "bytes " + start + "-" + (APK.length - 1) + "/" + APK.length);
        request.sendResponseHeaders(start > 0 ? 206 : 200, APK.length - start);
        request.getResponseBody().write(APK, start, truncate < 0 ? APK.length - start : Math.min(truncate, APK.length - start));
    }

    private void fetch() throws Exception {
        UpdateDownload.fetch(address, "fixture-token", sha, APK.length, file, progress::add);
    }

    @Test public void resumesExistingPartialAndOnlyRenamesAfterVerification() throws Exception {
        Files.write(part.toPath(), Arrays.copyOf(APK, 1024));
        fetch();
        assertEquals(Collections.singletonList("bytes=1024-"), ranges);
        assertArrayEquals(APK, Files.readAllBytes(file.toPath()));
        assertFalse(part.exists()); assertEquals(Integer.valueOf(12), progress.get(0));
    }

    @Test public void retriesInterruptedResponseFromTheActualSavedOffset() throws Exception {
        reply = (request, attempt) -> send(request, false, attempt == 1 ? 1024 : -1);
        fetch();
        assertEquals(Arrays.asList(null, "bytes=1024-"), ranges);
        assertArrayEquals(APK, Files.readAllBytes(file.toPath()));
    }

    @Test public void exhaustedRetriesKeepBytesForTheNextDownloadAction() throws Exception {
        reply = (request, attempt) -> send(request, false, 512);
        try { fetch(); fail(); } catch (IOException expected) { assertTrue(expected.getMessage().contains("已保留")); }
        assertEquals(3, requests.get()); assertEquals(1536, part.length()); assertFalse(file.exists());
        reply = (request, attempt) -> send(request, false, -1);
        fetch();
        assertEquals("bytes=1536-", ranges.get(3));
        assertArrayEquals(APK, Files.readAllBytes(file.toPath()));
    }

    @Test public void oldServerReturning200OverwritesInsteadOfAppending() throws Exception {
        Files.write(part.toPath(), Arrays.copyOf(APK, 1024));
        reply = (request, attempt) -> send(request, true, -1);
        fetch();
        assertEquals(1, requests.get()); assertArrayEquals(APK, Files.readAllBytes(file.toPath()));
    }

    @Test public void authorizationAndReplacedPackageFailWithoutRetries() throws Exception {
        for (int code : new int[]{401, 403, 409, 412}) {
            requests.set(0);
            reply = (request, attempt) -> request.sendResponseHeaders(code, -1);
            try { fetch(); fail(); } catch (IOException expected) { assertEquals(1, requests.get()); }
            assertFalse(file.exists());
        }
    }

    @Test public void malformedRangeCannotCorruptACachedPackage() throws Exception {
        Files.write(part.toPath(), Arrays.copyOf(APK, 1024));
        reply = (request, attempt) -> {
            request.responseHeaders.put("Content-Range", "bytes 0-8191/8192");
            request.sendResponseHeaders(206, APK.length);
        };
        try { fetch(); fail(); } catch (IOException expected) { assertTrue(expected.getMessage().contains("续传响应")); }
        assertFalse(file.exists()); assertFalse(part.exists()); assertEquals(1, requests.get());
    }

    @Test public void checksumMismatchDiscardsTheWholePartial() throws Exception {
        byte[] corrupt = APK.clone(); corrupt[100] ^= 1;
        reply = (request, attempt) -> {
            request.sendResponseHeaders(200, corrupt.length); request.getResponseBody().write(corrupt);
        };
        try { fetch(); fail(); } catch (IOException expected) { assertTrue(expected.getMessage().contains("校验")); }
        assertFalse(file.exists()); assertFalse(part.exists()); assertEquals(1, requests.get());
    }

    @Test public void completedPartialIsVerifiedBeforeAnyNetworkRequest() throws Exception {
        Files.write(part.toPath(), APK);
        fetch(); assertEquals(0, requests.get()); assertArrayEquals(APK, Files.readAllBytes(file.toPath()));
    }

    @Test public void runtimeRedirectDoesNotForwardTheBearerToken() throws Exception {
        reply = (request, attempt) -> {
            request.responseHeaders.put("Location", "https://github.com/owner/app/releases/download/v25/app.apk");
            request.sendResponseHeaders(302, 0);
        };
        try { fetch(); fail(); }
        catch (IOException expected) { assertTrue(expected.getMessage().contains("重定向")); }
        assertEquals(1, requests.get()); assertFalse(file.exists());
    }
}
