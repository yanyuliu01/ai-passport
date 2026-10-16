package com.yanyuliu.pockethub;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.Map;

/** One continuous response connection; snapshots and message ids survive transport fallback. */
final class MessageStream implements AutoCloseable {
    private final HttpURLConnection connection;
    private final BufferedReader input;

    private MessageStream(HttpURLConnection connection) throws IOException {
        this.connection = connection;
        input = new BufferedReader(new InputStreamReader(connection.getInputStream(), StandardCharsets.UTF_8));
    }

    static MessageStream open(String base, String token, String id, String rev) throws IOException {
        HttpURLConnection connection = (HttpURLConnection) new URL(base + "/v1/messages/" + id
                + "/stream" + (rev == null ? "" : "?rev=" + rev)).openConnection();
        boolean owned = false;
        try {
            connection.setRequestProperty("Authorization", "Bearer " + token);
            connection.setRequestProperty("Accept", "text/event-stream");
            connection.setInstanceFollowRedirects(false);
            connection.setConnectTimeout(10000); connection.setReadTimeout(35000);
            int status = connection.getResponseCode();
            if (status != 200) throw new IOException("回复事件连接不可用（" + status + "）");
            String type = connection.getContentType();
            if (type == null || !type.startsWith("text/event-stream")) throw new IOException("回复事件格式不匹配");
            MessageStream stream = new MessageStream(connection); owned = true;
            return stream;
        } finally { if (!owned) connection.disconnect(); }
    }

    Map<String, String> next() throws IOException { return readEvent(input); }

    static Map<String, String> readEvent(BufferedReader input) throws IOException {
        StringBuilder data = new StringBuilder();
        String line;
        while ((line = input.readLine()) != null) {
            if (line.isEmpty()) {
                if (data.length() == 0) continue;
                Map<String, String> message = BuddyProtocol.parseFlatObject(data.toString());
                if (message == null || message.get("id") == null || message.get("status") == null)
                    throw new IOException("回复事件内容不完整");
                return message;
            }
            if (!line.startsWith("data:")) continue;
            String value = line.substring(5);
            if (value.startsWith(" ")) value = value.substring(1);
            if (data.length() + value.length() > Json.RESPONSE_MAX_BYTES) throw new IOException("回复事件内容过长");
            if (data.length() > 0) data.append('\n'); data.append(value);
        }
        // A partial event is discarded; polling retrieves the authoritative full snapshot.
        return null;
    }

    @Override public void close() { connection.disconnect(); }
}
