package com.yanyuliu.pockethub;

import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URI;
import java.net.URL;
import java.security.MessageDigest;
import java.util.Locale;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** Hash-pinned APK download. Partial bytes survive retries and process restarts. */
final class UpdateDownload {
    interface Progress { void at(int percent); }
    interface ConnectionFactory { HttpURLConnection open(URL url) throws IOException; }
    static final long MAX_BYTES = 512L * 1024 * 1024;
    private static final Pattern RANGE = Pattern.compile("bytes (\\d+)-(\\d+)/(\\d+)");
    private static final class Invalid extends IOException {
        Invalid(String message) { super(message); }
    }

    static void fetch(String address, String token, String sha, long size, File file,
                      Progress progress) throws Exception {
        fetch(address, token, sha, size, file, progress, false,
                PinnedRelayTLS::openDownload);
    }

    static void fetchExternal(String address, String sha, long size, File file, Progress progress) throws Exception {
        fetchExternal(address, sha, size, file, progress, PinnedRelayTLS::openDownload);
    }

    static void fetchExternal(String address, String sha, long size, File file, Progress progress,
                              ConnectionFactory connections) throws Exception {
        hostedUrl(address);
        fetch(address, "", sha, size, file, progress, true, connections);
    }

    static void fetchPreferred(String mirror, String runtime, String token, String sha, long size,
                               File file, Progress progress) throws Exception {
        fetchPreferred(mirror, runtime, token, sha, size, file, progress,
                PinnedRelayTLS::openDownload);
    }

    static void fetchPreferred(String mirror, String runtime, String token, String sha, long size,
                               File file, Progress progress, ConnectionFactory connections) throws Exception {
        if (mirror != null && !mirror.isEmpty()) {
            try {
                fetchExternal(mirror, sha, size, file, progress, connections);
                return;
            } catch (Invalid failure) {
                throw failure; // Never hide a checksum, protocol, or unsafe URL failure.
            } catch (IOException unavailable) {
                // The manifest pins identical bytes on both sources. Resume the
                // same partial on the paired Runtime; its token stays on that URL.
            }
        }
        fetch(runtime, token, sha, size, file, progress, false, connections);
    }

    private static void fetch(String address, String token, String sha, long size, File file,
                              Progress progress, boolean external, ConnectionFactory connections) throws Exception {
        if (!sha.matches("[0-9a-f]{64}") || size <= 0 || size > MAX_BYTES)
            throw new IOException("安装包信息不完整，请重新检查更新");
        File folder = file.getParentFile();
        if (!folder.isDirectory() && !folder.mkdirs()) throw new IOException("没地方放安装包");
        File part = new File(file.getPath() + ".part");
        if (part.length() > size) discard(part);
        if (part.length() == size) {
            if (matches(part, sha)) { finish(part, file); return; }
            discard(part);
        }
        IOException last = null;
        int attempt = 0;
        for (int response = 0; response < 256 && attempt < 3; response++) {
            try {
                transfer(address, token, sha, size, part, progress, external, connections);
                // A valid server may cap a 206 response before the end of the file.
                if (part.length() < size) continue;
                if (!matches(part, sha)) {
                    discard(part);
                    throw new Invalid("安装包校验没通过，请重新检查更新");
                }
                finish(part, file);
                return;
            } catch (Invalid failure) {
                throw failure; // Authorization and protocol failures are never retried.
            } catch (IOException failure) {
                last = failure;
                if (++attempt < 3) Thread.sleep(250L * attempt);
            }
        }
        long percent = Math.min(99, part.length() * 100 / size);
        throw new IOException("下载中断，已保留 " + percent + "%，再点下载可继续", last);
    }

    private static void transfer(String address, String token, String sha, long size,
                                 File part, Progress progress, boolean external,
                                 ConnectionFactory connections) throws Exception {
        long offset = part.length();
        String etag = "\"" + sha + "\"";
        HttpURLConnection connection = connect(address, token, sha, offset, external, connections);
        try {
            int code = connection.getResponseCode();
            if (code == 401 || code == 403)
                throw new Invalid(external ? "下载地址不可用，请重新检查更新" : "连接令牌已失效");
            if (code == 404 || code == 409 || code == 412)
                throw new Invalid("安装包已更新，请重新检查更新");
            if (code == 416) { discard(part); throw new Invalid("续传位置失效，请重新下载"); }
            if (code != 200 && code != 206) {
                if (code >= 500 || code == 408 || code == 429) throw new IOException("下载暂时不可用");
                throw new Invalid("下载失败（" + code + "）");
            }
            String returnedTag = connection.getHeaderField("ETag");
            if (!external && returnedTag != null && !etag.equals(returnedTag)) {
                discard(part);
                throw new Invalid("安装包已更新，请重新检查更新");
            }
            boolean append = code == 206;
            long end = size - 1;
            if (append) {
                Matcher range = RANGE.matcher(String.valueOf(connection.getHeaderField("Content-Range")));
                try {
                    if (!range.matches() || Long.parseLong(range.group(1)) != offset
                            || Long.parseLong(range.group(3)) != size
                            || (end = Long.parseLong(range.group(2))) < offset || end >= size)
                        throw new NumberFormatException();
                } catch (NumberFormatException invalid) {
                    discard(part);
                    throw new Invalid("续传响应不正确，请重新下载");
                }
            } else {
                // Old Runtime versions ignore Range and return the whole APK.
                offset = 0;
            }
            long length = connection.getContentLengthLong();
            if (length >= 0 && length != end - offset + 1) {
                discard(part);
                throw new Invalid("安装包大小发生变化，请重新检查更新");
            }
            long total = offset;
            int shown = (int) (total * 100 / size);
            progress.at(Math.min(99, shown));
            try (InputStream input = connection.getInputStream();
                 FileOutputStream output = new FileOutputStream(part, append)) {
                byte[] buffer = new byte[65536];
                int count;
                while ((count = input.read(buffer)) != -1) {
                    if (total + count > end + 1) {
                        // Close the output before deleting on all Android filesystems.
                        throw new Invalid("安装包大小不正确，请重新检查更新");
                    }
                    output.write(buffer, 0, count);
                    total += count;
                    int percent = (int) Math.min(99, total * 100 / size);
                    if (percent != shown) { shown = percent; progress.at(percent); }
                }
            } catch (Invalid invalid) {
                discard(part);
                throw invalid;
            }
            if (total != end + 1) throw new IOException("安装包尚未下载完整");
        } finally { connection.disconnect(); }
    }

    static URL hostedUrl(String address) throws IOException {
        try {
            URI uri = new URI(address);
            if (address.length() > 4096 || !"https".equalsIgnoreCase(uri.getScheme())
                    || uri.getHost() == null || uri.getRawUserInfo() != null || uri.getRawFragment() != null
                    || uri.getPort() == 0 || uri.getPort() > 65535)
                throw new IllegalArgumentException();
            return uri.toURL();
        } catch (Exception invalid) {
            throw new Invalid("下载地址必须是无账号或片段的 HTTPS 网址");
        }
    }

    private static HttpURLConnection connect(String address, String token, String sha, long offset,
                                              boolean external, ConnectionFactory connections) throws IOException {
        URL url = external ? hostedUrl(address) : new URL(address);
        for (int redirects = 0; ; redirects++) {
            HttpURLConnection connection = connections.open(url);
            connection.setInstanceFollowRedirects(false);
            connection.setConnectTimeout(5000);
            connection.setReadTimeout(15000);
            connection.setRequestProperty("Accept-Encoding", "identity");
            connection.setRequestProperty("Cache-Control", "no-cache");
            connection.setRequestProperty("User-Agent", "PocketHub-Updater");
            if (!external) {
                connection.setRequestProperty("Authorization", "Bearer " + token);
                connection.setRequestProperty("If-Match", "\"" + sha + "\"");
            }
            if (offset > 0) {
                connection.setRequestProperty("Range", "bytes=" + offset + "-");
                if (!external) connection.setRequestProperty("If-Range", "\"" + sha + "\"");
            }
            try {
                int code = connection.getResponseCode();
                if (code != 301 && code != 302 && code != 303 && code != 307 && code != 308) return connection;
                if (!external || redirects >= 5) throw new Invalid("更新下载重定向异常，请重新检查更新");
                String location = connection.getHeaderField("Location");
                if (location == null || location.isEmpty()) throw new Invalid("更新下载地址无效，请重新检查更新");
                url = hostedUrl(new URL(url, location).toString());
            } catch (IOException | RuntimeException failure) {
                connection.disconnect();
                throw failure;
            }
            connection.disconnect();
        }
    }

    private static boolean matches(File file, String sha) throws Exception {
        MessageDigest digest = MessageDigest.getInstance("SHA-256");
        try (InputStream input = new FileInputStream(file)) {
            byte[] buffer = new byte[65536]; int count;
            while ((count = input.read(buffer)) != -1) digest.update(buffer, 0, count);
        }
        StringBuilder actual = new StringBuilder();
        for (byte value : digest.digest()) actual.append(String.format(Locale.ROOT, "%02x", value & 255));
        return actual.toString().equals(sha);
    }

    private static void discard(File part) throws IOException {
        if (part.exists() && !part.delete()) throw new IOException("无法清理损坏的安装包");
    }

    private static void finish(File part, File file) throws IOException {
        if (!part.renameTo(file)) throw new Invalid("存不下安装包");
    }
}
