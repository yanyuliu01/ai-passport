package com.yanyuliu.pockethub;

import java.net.URI;
import java.net.URLDecoder;
import java.util.Map;

/** Metadata only: never reconstruct identity from message text. */
final class AcceptedReceipt {
    static boolean supports(Map<String,Object> request, String runtime) { return identity(request, runtime) != null; }
    static Map<String,Object> from(Map<String,Object> request, Map<String,Object> value, String runtime) {
        Map<String,Object> identity = identity(request, runtime);
        if (identity == null || value == null) return null;
        String remote = Json.string(value, "id");
        if (!remote.matches("[a-f0-9]{32}")
                || !Json.string(identity, "client_id").equals(Json.string(value, "client_id"))
                || !Json.string(identity, "conversation").equals(Json.string(value, "conversation"))) return null;
        identity.put("message_id", remote); identity.put("result", "accepted"); return identity;
    }

    /** Only definitive validation rejections, never timeouts or uncertain conflicts. */
    static Map<String,Object> rejected(Map<String,Object> request, Map<String,Object> response, String runtime) {
        if (response == null || !response.containsKey("error")) return null;
        Object code = response.get("status");
        if (!(code instanceof Number)) return null;
        double exact = ((Number) code).doubleValue();
        int status = (int) exact;
        if (exact != status) return null;
        if (status != 400 && status != 413 && status != 422) return null;
        Map<String,Object> identity = identity(request, runtime);
        if (identity != null) identity.put("result", "rejected");
        return identity;
    }

    static Map<String,Object> identity(Map<String,Object> request, String runtime) {
        if (request == null || !"POST".equals(Json.string(request, "method"))
                || !runtime.matches("[a-f0-9]{32}") || !runtime.equals(Json.string(request, "runtime_id"))) return null;
        String path = Json.string(request, "path"), client = "", conversation = "";
        if (path.equals("/v1/messages") || path.matches("/v1/sessions/[^/?]+/continue")) {
            Object raw = request.get("body");
            if (!(raw instanceof Map)) return null;
            @SuppressWarnings("unchecked") Map<String,Object> body = (Map<String,Object>) raw;
            client = Json.string(body, "client_id"); conversation = Json.string(body, "conversation");
        } else if (path.startsWith("/v1/voice?")) {
            try {
                String query = URI.create("https://localhost" + path).getRawQuery();
                for (String field : query.split("&")) {
                    String[] pair = field.split("=", 2);
                    if (pair.length != 2) continue;
                    if (pair[0].equals("client_id")) client = URLDecoder.decode(pair[1], "UTF-8");
                    if (pair[0].equals("conversation")) conversation = URLDecoder.decode(pair[1], "UTF-8");
                }
            } catch (Exception invalid) { return null; }
        } else return null;
        if (client.isEmpty() || conversation.isEmpty()) return null;
        return RelayCrypto.map("client_id", client, "conversation", conversation, "runtime_id", runtime);
    }
    private AcceptedReceipt() {}
}
