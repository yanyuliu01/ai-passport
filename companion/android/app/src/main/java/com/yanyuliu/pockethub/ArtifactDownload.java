package com.yanyuliu.pockethub;

import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;

/** Testable bounded binary download and integrity verification, independent of Android. */
final class ArtifactDownload {
    interface RelayBytes { byte[] read(String address) throws Exception; }
    static RelayBytes relayBytes;
    private static final long MAX_BYTES = 256L * 1024 * 1024;
    static void download(String address, String token, Attachment item, boolean thumb, File file) throws Exception {
        if (!item.ready()) throw new java.io.IOException(item.error.isEmpty() ? "附件还没有准备好" : item.error);
        if (!thumb && item.size > MAX_BYTES) throw new java.io.IOException("附件超过 256 MB，请在电脑端打开");
        if (!file.getParentFile().isDirectory() && !file.getParentFile().mkdirs()) throw new java.io.IOException("无法建立附件缓存");
        if (address.startsWith("relay://")) {
            if (relayBytes == null) throw new java.io.IOException("加密附件连接未就绪");
            byte[] data = relayBytes.read(address + "/v1/artifacts/" + item.id + (thumb ? "/thumbnail" : "/content"));
            if (data.length > 2_800_000) throw new java.io.IOException("附件超过加密传输上限");
            if (!thumb && item.size > 0 && data.length != item.size) throw new java.io.IOException("附件下载不完整");
            if (!thumb && item.sha256.matches("[0-9a-f]{64}") && !hex(MessageDigest.getInstance("SHA-256").digest(data)).equals(item.sha256)) throw new java.io.IOException("附件校验失败");
            File partial = File.createTempFile("relay-", ".part", file.getParentFile());
            try { try (OutputStream output = new FileOutputStream(partial)) { output.write(data); }
                if (!partial.renameTo(file)) throw new java.io.IOException("无法保存附件");
            } finally { partial.delete(); }
            return;
        }
        HttpURLConnection connection = (HttpURLConnection) new URL(address + "/v1/artifacts/" + item.id
                + (thumb ? "/thumbnail" : "/content")).openConnection();
        connection.setInstanceFollowRedirects(false);
        connection.setRequestProperty("Authorization", "Bearer " + token);
        connection.setConnectTimeout(10000);
        connection.setReadTimeout(30000);
        File temp = File.createTempFile("download-", ".part", file.getParentFile());
        try {
            int code = connection.getResponseCode();
            if (code != 200) throw new java.io.IOException(code == 401 ? "连接令牌已失效" : code == 404 ? "附件已不可用，请在电脑端重新登记" : "附件下载失败（" + code + "）");
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            long total = 0;
            try (InputStream input = connection.getInputStream(); OutputStream output = new FileOutputStream(temp)) {
                byte[] buffer = new byte[16384];
                int count;
                while ((count = input.read(buffer)) != -1) {
                    total += count;
                    if (total > (thumb ? 8L * 1024 * 1024 : MAX_BYTES)) throw new java.io.IOException("附件太大");
                    digest.update(buffer, 0, count);
                    output.write(buffer, 0, count);
                }
            }
            if (!thumb && item.size > 0 && total != item.size) throw new java.io.IOException("附件下载不完整，请重试");
            if (!thumb && item.sha256.matches("[0-9a-f]{64}") && !hex(digest.digest()).equals(item.sha256)) throw new java.io.IOException("附件校验失败，请重试");
            if (!temp.renameTo(file) && !file.isFile()) throw new java.io.IOException("无法保存附件");
        } finally { connection.disconnect(); if (temp.exists()) temp.delete(); }
    }

    static String key(String text) {
        try { return hex(MessageDigest.getInstance("SHA-256").digest(text.getBytes(StandardCharsets.UTF_8))); }
        catch (java.security.NoSuchAlgorithmException impossible) { throw new AssertionError(impossible); }
    }
    private static String hex(byte[] bytes) {
        StringBuilder result = new StringBuilder(); for (byte value : bytes) result.append(String.format(java.util.Locale.ROOT, "%02x", value & 255)); return result.toString();
    }
}
