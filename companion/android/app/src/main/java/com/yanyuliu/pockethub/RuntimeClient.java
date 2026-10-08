package com.yanyuliu.pockethub;

import android.content.Context;
import android.content.SharedPreferences;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * 和小幽 Runtime 说话：把一句话发过去，等它处理完，把回复交给中枢。
 *
 * Runtime 的地址和令牌用一条“连接串”设置：http://主机:端口#令牌，
 * 由电脑上的 `python3 -m xiaoyou_runtime --pair` 打印出来。
 * 消息一条一条顺序处理，和 Runtime 那边的做法一致。
 */
final class RuntimeClient {
    private static final String PREFS = "runtime";
    private static final String KEY_URL = "url";
    private static final String KEY_TOKEN = "token";
    private static final int POLL_SECONDS = 50;
    private static final long GIVE_UP_MS = 15 * 60 * 1000L;
    private static final ExecutorService WORKER = Executors.newSingleThreadExecutor();

    private RuntimeClient() {
    }

    /** 保存连接串；格式不对时返回说明，成功返回 null。 */
    static String savePairing(Context context, String pairing) {
        String text = pairing == null ? "" : pairing.trim();
        int hash = text.indexOf('#');
        if (hash <= 0 || hash == text.length() - 1) {
            return "连接串应该像 http://192.168.1.5:8765#令牌";
        }
        String url = text.substring(0, hash);
        while (url.endsWith("/")) {
            url = url.substring(0, url.length() - 1);
        }
        String token = text.substring(hash + 1).trim();
        if (!url.startsWith("http://") && !url.startsWith("https://")) {
            return "地址要以 http:// 或 https:// 开头";
        }
        if (token.length() < 16 || token.contains(" ")) {
            return "令牌不对：应该是至少 16 位、不含空格的一串字符";
        }
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit()
                .putString(KEY_URL, url).putString(KEY_TOKEN, token).apply();
        return null;
    }

    /** 已保存的 Runtime 地址（不含令牌）；没设置过返回 null。 */
    static String savedUrl(Context context) {
        return context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString(KEY_URL, null);
    }

    static final String VOICE_PLACEHOLDER = "（语音）";

    /** 发一句话。结果通过 HubStore 的聊天状态反映出来，这里不返回。 */
    static void send(Context context, String text) {
        submit(context, text, null);
    }

    /** 发一段录音（16 位单声道 WAV）。Runtime 先识别成文字，再像打字一样回答。 */
    static void sendVoice(Context context, byte[] wav) {
        submit(context, VOICE_PLACEHOLDER, wav);
    }

    private static void submit(Context context, String text, byte[] wav) {
        SharedPreferences prefs = context.getApplicationContext()
                .getSharedPreferences(PREFS, Context.MODE_PRIVATE);
        final String url = prefs.getString(KEY_URL, null);
        final String token = prefs.getString(KEY_TOKEN, null);
        final HubStore store = HubStore.get();
        if (url == null || token == null) {
            store.chatFailed(text, "还没有设置 Runtime 的连接串");
            return;
        }
        store.chatAsked(text);
        final String clientId = UUID.randomUUID().toString().replace("-", "");
        WORKER.execute(() -> {
            try {
                Map<String, String> message;
                if (wav != null) {
                    message = request("POST", url + "/v1/voice?client_id=" + clientId, token,
                            wav, "audio/wav", 30);
                } else {
                    StringBuilder body = new StringBuilder("{\"text\":");
                    BuddyProtocol.quote(body, text);
                    body.append(",\"client_id\":\"").append(clientId).append("\"}");
                    message = request("POST", url + "/v1/messages", token,
                            body.toString().getBytes(StandardCharsets.UTF_8),
                            "application/json; charset=utf-8", 20);
                }
                String id = message.get("id");
                if (id == null) {
                    throw new IOException("Runtime 没有返回消息编号");
                }
                long deadline = System.currentTimeMillis() + GIVE_UP_MS;
                boolean heard = wav == null;
                while (true) {
                    String status = message.get("status");
                    String recognized = message.get("text");
                    if (!heard && recognized != null && !recognized.isEmpty()) {
                        heard = true;
                        store.chatHeard(VOICE_PLACEHOLDER, recognized);
                    }
                    if ("done".equals(status)) {
                        String reply = message.get("reply");
                        String brief = message.get("brief");
                        store.chatAnswered(brief == null ? "" : brief, reply == null ? "" : reply);
                        return;
                    }
                    if ("failed".equals(status)) {
                        String error = message.get("error");
                        store.chatFailed(null, error == null ? "Runtime 没有说明原因" : error);
                        return;
                    }
                    if (System.currentTimeMillis() > deadline) {
                        throw new IOException("等了太久还没有结果");
                    }
                    // 语音还没识别出来时短轮询，好让“我说了什么”尽快显示出来。
                    int wait = heard ? POLL_SECONDS : 2;
                    message = request("GET", url + "/v1/messages/" + id + "?wait=" + wait,
                            token, null, null, wait + 15);
                }
            } catch (IOException | RuntimeException error) {
                String detail = error.getMessage();
                store.chatFailed(null, "连不上 Runtime：" + (detail == null ? error.toString() : detail));
            }
        });
    }

    private static Map<String, String> request(String method, String address, String token,
                                               byte[] body, String contentType,
                                               int timeoutSeconds)
            throws IOException {
        HttpURLConnection connection = (HttpURLConnection) new URL(address).openConnection();
        try {
            connection.setRequestMethod(method);
            connection.setConnectTimeout(10000);
            connection.setReadTimeout(timeoutSeconds * 1000);
            connection.setRequestProperty("Authorization", "Bearer " + token);
            if (body != null) {
                connection.setDoOutput(true);
                connection.setRequestProperty("Content-Type", contentType);
                connection.setFixedLengthStreamingMode(body.length);
                try (OutputStream out = connection.getOutputStream()) {
                    out.write(body);
                }
            }
            int code = connection.getResponseCode();
            InputStream stream = code >= 400 ? connection.getErrorStream()
                    : connection.getInputStream();
            String text = stream == null ? "" : readAll(stream);
            Map<String, String> fields = BuddyProtocol.parseFlatObject(text);
            if (code == 401) {
                throw new IOException("令牌不对（401）");
            }
            if (code >= 400) {
                String error = fields == null ? null : fields.get("error");
                throw new IOException("Runtime 返回 " + code + (error == null ? "" : "：" + error));
            }
            if (fields == null) {
                throw new IOException("Runtime 返回的内容看不懂");
            }
            return fields;
        } finally {
            connection.disconnect();
        }
    }

    private static String readAll(InputStream stream) throws IOException {
        try (InputStream in = stream) {
            ByteArrayOutputStream out = new ByteArrayOutputStream();
            byte[] buffer = new byte[4096];
            int count;
            while ((count = in.read(buffer)) > 0) {
                out.write(buffer, 0, count);
                if (out.size() > 512 * 1024) {
                    throw new IOException("Runtime 返回的内容太长");
                }
            }
            return new String(out.toByteArray(), StandardCharsets.UTF_8);
        }
    }
}
