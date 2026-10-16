package com.yanyuliu.pockethub;

import java.io.EOFException;
import java.io.IOException;
import java.net.SocketException;
import java.net.SocketTimeoutException;
import java.net.URI;
import java.net.UnknownHostException;
import java.nio.charset.StandardCharsets;
import java.util.Map;

/** Retry reads and deduplicated message submissions, preserving the exact request bytes. */
final class RuntimeRetry {
    interface Attempt<T> { T run() throws IOException; }
    interface Delay { void waitFor(long millis) throws InterruptedException; }

    static boolean safe(String method, String address, byte[] body) {
        if ("GET".equals(method)) return true;
        if (!"POST".equals(method) || body == null) return false;
        URI uri = URI.create(address);
        if ("/v1/messages".equals(uri.getPath())) {
            Map<String, String> fields = BuddyProtocol.parseFlatObject(new String(body, StandardCharsets.UTF_8));
            return fields != null && fields.get("client_id") != null && !fields.get("client_id").isEmpty();
        }
        if ("/v1/voice".equals(uri.getPath()) && uri.getRawQuery() != null) {
            for (String field : uri.getRawQuery().split("&"))
                if (field.startsWith("client_id=") && field.length() > 10) return true;
        }
        return false;
    }

    static <T> T run(boolean safe, Attempt<T> attempt, Delay delay) throws IOException {
        for (int index = 0; ; index++) {
            try { return attempt.run(); }
            catch (IOException error) {
                if (!safe || index >= 2 || !transientFailure(error)) throw error;
                try { delay.waitFor(500L << index); }
                catch (InterruptedException cancelled) {
                    Thread.currentThread().interrupt(); throw new IOException("连接重试已取消", cancelled);
                }
            }
        }
    }

    private static boolean transientFailure(IOException error) {
        return error instanceof SocketException || error instanceof SocketTimeoutException
                || error instanceof EOFException || error instanceof UnknownHostException;
    }
}
