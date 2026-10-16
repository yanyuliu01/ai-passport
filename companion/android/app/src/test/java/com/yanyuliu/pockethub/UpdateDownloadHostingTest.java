package com.yanyuliu.pockethub;

import static org.junit.Assert.*;
import java.io.ByteArrayInputStream;
import java.io.File;
import java.io.IOException;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.file.Files;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import org.junit.After;
import org.junit.Before;
import org.junit.Test;

/** Exercise public HTTPS transport without real credentials or network access. */
public class UpdateDownloadHostingTest {
    private static final byte[] APK = new byte[8192];
    static { for (int i = 0; i < APK.length; i++) APK[i] = (byte) (i * 37); }
    private static final String GITHUB = "https://github.com/owner/app/releases/download/v25/app.apk";
    private static final String ASSET = "https://release-assets.githubusercontent.com/file.apk?signature=fixture";
    private File directory, file, part;
    private String sha;
    private final List<Reply> replies = new ArrayList<>();
    private final List<URL> urls = new ArrayList<>();

    private static final class Reply extends HttpURLConnection {
        final int status;
        final byte[] body;
        final Map<String, String> headers = new HashMap<>();
        boolean closed;
        Reply(int status, byte[] body) throws Exception {
            super(new URL(GITHUB)); this.status = status; this.body = body;
            headers.put("Content-Length", Integer.toString(body.length));
        }
        public void connect() { }
        public boolean usingProxy() { return false; }
        public void disconnect() { closed = true; }
        public int getResponseCode() { return status; }
        public String getHeaderField(String key) { return headers.get(key); }
        public long getContentLengthLong() { return Long.parseLong(headers.get("Content-Length")); }
        public InputStream getInputStream() { return new ByteArrayInputStream(body); }
    }

    @Before public void start() throws Exception {
        StringBuilder hash = new StringBuilder();
        for (byte b : MessageDigest.getInstance("SHA-256").digest(APK)) hash.append(String.format("%02x", b & 255));
        sha = hash.toString();
        directory = Files.createTempDirectory("hosted-update-test").toFile();
        file = new File(directory, sha + ".bin"); part = new File(file.getPath() + ".part");
    }

    @After public void stop() {
        File[] files = directory.listFiles();
        if (files != null) for (File item : files) item.delete();
        directory.delete();
    }

    private Reply response(int status, byte[] body) throws Exception {
        Reply reply = new Reply(status, body); replies.add(reply); return reply;
    }

    private HttpURLConnection connection(URL url) throws IOException {
        urls.add(url);
        if (urls.size() > replies.size()) throw new AssertionError("unexpected connection/retry");
        return replies.get(urls.size() - 1);
    }

    private void fetch(long size) throws Exception {
        UpdateDownload.fetchExternal(GITHUB, sha, size, file, ignored -> { }, this::connection);
    }

    private void assertPublicHeaders() {
        for (int i = 0; i < urls.size(); i++) {
            Reply reply = replies.get(i);
            assertNull(reply.getRequestProperty("Authorization"));
            assertNull(reply.getRequestProperty("If-Match"));
            assertNull(reply.getRequestProperty("If-Range"));
            assertEquals("identity", reply.getRequestProperty("Accept-Encoding"));
            assertFalse(reply.getInstanceFollowRedirects());
            assertTrue(reply.closed);
        }
    }

    @Test public void githubRedirectUsesNoRuntimeTokenOrRuntimeEtagAndVerifiesBytes() throws Exception {
        response(302, new byte[0]).headers.put("Location", ASSET);
        response(200, APK).headers.put("ETag", "\"cdn-generated-value\"");
        fetch(APK.length);
        assertEquals(Arrays.asList(new URL(GITHUB), new URL(ASSET)), urls);
        assertPublicHeaders();
        assertArrayEquals(APK, Files.readAllBytes(file.toPath())); assertFalse(part.exists());
    }

    @Test public void resumesPublicAssetAcrossRedirectWithoutAssumingItsEtag() throws Exception {
        Files.write(part.toPath(), Arrays.copyOf(APK, 1024));
        response(307, new byte[0]).headers.put("Location", ASSET);
        response(206, Arrays.copyOfRange(APK, 1024, APK.length)).headers.put("Content-Range", "bytes 1024-8191/8192");
        fetch(APK.length);
        for (Reply reply : replies) assertEquals("bytes=1024-", reply.getRequestProperty("Range"));
        assertPublicHeaders(); assertArrayEquals(APK, Files.readAllBytes(file.toPath()));
    }

    @Test public void rejectsInsecureOrCredentialBearingUrlsBeforeConnecting() throws Exception {
        for (String address : new String[]{"http://example.com/app.apk", "file:///tmp/app.apk",
                "https://owner:secret@example.com/app.apk", "https://example.com/app.apk#fragment",
                "https://example.com:99999/app.apk", "https://example.com:0/app.apk"}) {
            try {
                UpdateDownload.fetchExternal(address, sha, APK.length, file, ignored -> { }, this::connection);
                fail(address);
            } catch (IOException expected) { assertTrue(expected.getMessage().contains("HTTPS")); }
        }
        assertTrue(urls.isEmpty()); assertFalse(file.exists());
    }

    @Test public void rejectsDowngradeAndCredentialsOnRedirectWithoutFollowingOrRetrying() throws Exception {
        for (String location : new String[]{"http://example.com/app.apk", "https://owner:secret@example.com/app.apk"}) {
            replies.clear(); urls.clear();
            response(302, new byte[0]).headers.put("Location", location);
            try { fetch(APK.length); fail(); }
            catch (IOException expected) { assertTrue(expected.getMessage().contains("HTTPS")); }
            assertEquals(1, urls.size()); assertPublicHeaders(); assertFalse(file.exists());
        }
    }

    @Test public void redirectLoopIsBoundedAndCannotStartAnUnboundedRetryLoop() throws Exception {
        for (int i = 0; i < 6; i++) response(302, new byte[0]).headers.put("Location", GITHUB);
        try { fetch(APK.length); fail(); }
        catch (IOException expected) { assertTrue(expected.getMessage().contains("重定向")); }
        assertEquals(6, urls.size()); assertPublicHeaders(); assertFalse(file.exists());
    }

    @Test public void hostedChecksumFailureDiscardsTheUntrustedPackage() throws Exception {
        byte[] corrupt = APK.clone(); corrupt[100] ^= 1;
        response(200, corrupt);
        try { fetch(APK.length); fail(); }
        catch (IOException expected) { assertTrue(expected.getMessage().contains("校验")); }
        assertEquals(1, urls.size()); assertPublicHeaders(); assertFalse(file.exists()); assertFalse(part.exists());
    }

    @Test public void supportsLargerSpeechPackagesButKeepsAHardUpperBound() throws Exception {
        for (long size : new long[]{128L * 1024 * 1024 + 1, 512L * 1024 * 1024}) {
            replies.clear(); urls.clear(); response(403, new byte[0]);
            try { fetch(size); fail(); }
            catch (IOException expected) { assertTrue(expected.getMessage().contains("下载地址不可用")); }
            assertEquals(1, urls.size()); assertPublicHeaders();
        }
        replies.clear(); urls.clear();
        try { fetch(512L * 1024 * 1024 + 1); fail(); }
        catch (IOException expected) { assertTrue(expected.getMessage().contains("安装包信息不完整")); }
        assertTrue(urls.isEmpty());
    }
}
