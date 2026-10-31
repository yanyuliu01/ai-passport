package com.yanyuliu.pockethub;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.Base64;
import java.util.Map;

/** Bounded firmware chunks over the existing authenticated relay. FirmwareSync verifies SHA-256. */
final class FirmwareDownload {
    static final int CHUNK = 1024 * 1024, MAX = 8 * 1024 * 1024;
    interface Reader { Map<String, Object> read(String address) throws Exception; }

    /** Retain the authenticated direct Runtime path for existing LAN/Funnel targets. */
    static byte[] readDirect(String address, String token) throws IOException {
        HttpURLConnection connection = (HttpURLConnection) new URL(address).openConnection();
        try {
            connection.setConnectTimeout(10000);
            connection.setReadTimeout(60000);
            connection.setRequestProperty("Authorization", "Bearer " + token);
            int code = connection.getResponseCode();
            if (code != 200) throw new IOException("Runtime 返回 " + code);
            try (InputStream in = connection.getInputStream()) {
                ByteArrayOutputStream output = new ByteArrayOutputStream(2 * 1024 * 1024);
                byte[] buffer = new byte[16384];
                int count;
                while ((count = in.read(buffer)) != -1) {
                    if (count > MAX - output.size()) throw new IOException("固件超过 8 MB");
                    output.write(buffer, 0, count);
                }
                return output.toByteArray();
            }
        } finally {
            connection.disconnect();
        }
    }

    static byte[] read(String address, Reader reader) throws IOException {
        ByteArrayOutputStream output = new ByteArrayOutputStream();
        int total = -1;
        try {
            do {
                int offset = output.size();
                Map<String, Object> reply = reader.read(address + "?offset=" + offset + "&length=" + CHUNK);
                int reported = integer(reply, "total"), start = integer(reply, "offset");
                if (reported <= 0 || reported > MAX || start != offset || total != -1 && reported != total)
                    throw new IOException("固件分块信息不一致");
                total = reported;
                Object encoded = reply.get("_relay_binary");
                if (!(encoded instanceof String) || ((String) encoded).length() > (CHUNK * 4 + 2) / 3)
                    throw new IOException("固件分块超过大小限制");
                byte[] data = Base64.getUrlDecoder().decode((String) encoded);
                if (data.length != Math.min(CHUNK, total - offset))
                    throw new IOException("固件分块下载不完整");
                output.write(data);
            } while (output.size() < total);
            return output.toByteArray();
        } catch (IOException error) { throw error; }
        catch (Exception error) { throw new IOException("固件下载失败：" + error.getMessage(), error); }
    }

    private static int integer(Map<String, Object> reply, String key) throws IOException {
        Object value = reply == null ? null : reply.get(key);
        if (!(value instanceof Number) || ((Number) value).doubleValue() != ((Number) value).intValue())
            throw new IOException("固件分块信息无效");
        return ((Number) value).intValue();
    }
}
