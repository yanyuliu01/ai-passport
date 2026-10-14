package com.yanyuliu.pockethub;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Map;

/** Registered Runtime artifacts; paths and arbitrary remote URLs never reach the downloader. */
final class Attachment {
    final String id, name, mime, kind, sha256, status, error;
    final long size;
    final int width, height;
    final boolean thumbnail;

    private Attachment(Map<String, Object> raw) {
        id = Json.string(raw, "id");
        name = Json.string(raw, "name");
        mime = Json.string(raw, "mime");
        kind = Json.string(raw, "kind");
        sha256 = Json.string(raw, "sha256");
        status = Json.string(raw, "status");
        error = Json.string(raw, "error");
        size = (long) Json.number(raw, "size");
        width = (int) Json.number(raw, "width");
        height = (int) Json.number(raw, "height");
        thumbnail = Json.flag(raw, "thumbnail");
    }

    static Attachment from(Map<String, Object> raw) {
        Attachment item = new Attachment(raw);
        return item.id.matches("a[0-9a-f]{32}") ? item : null;
    }

    static List<Attachment> parse(Map<String, Object> raw) {
        List<Attachment> result = new ArrayList<>();
        for (Object value : Json.list(raw, "attachments")) {
            Attachment item = from(Json.object(value));
            if (item != null) result.add(item);
        }
        return Collections.unmodifiableList(result);
    }

    boolean image() { return kind.equals("image") && mime.startsWith("image/"); }
    boolean ready() { return status.isEmpty() || status.equals("ready"); }
    String label() { return name.isEmpty() ? "附件" : name; }
    String sizeLabel() {
        return size >= 1024 * 1024 ? String.format(java.util.Locale.US, "%.1f MB", size / 1048576.0)
                : size >= 1024 ? String.format(java.util.Locale.US, "%.1f KB", size / 1024.0)
                : size + " B";
    }
}
