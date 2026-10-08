package com.yanyuliu.pockethub;

import java.io.ByteArrayOutputStream;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * 小幽固件说的蓝牙协议（Hardware Buddy 线协议）：一行一个 UTF-8 JSON 对象，以 \n 结尾。
 * 这个类只做字符串和字节处理，不依赖 Android，可以在电脑上直接跑测试。
 *
 * 各字段的字节上限与固件 main/buddy_types.h 一致；超出的在这里按字符边界截断，
 * 这样固件不会因为请求编号过长而拒收整条消息。
 */
public final class BuddyProtocol {
    public static final int MESSAGE_MAX = 159;
    public static final int ENTRY_MAX = 95;
    public static final int ENTRY_COUNT = 4;
    public static final int PROMPT_ID_MAX = 95;
    public static final int TOOL_MAX = 47;
    public static final int HINT_MAX = 319;
    public static final int TURN_TEXT_MAX = 3000;
    public static final int LINE_MAX = 4096;

    private BuddyProtocol() {
    }

    /** 需要用户在设备上回答“可以 / 不行”的一条请求。 */
    public static final class Prompt {
        public final String id;
        public final String tool;
        public final String hint;

        public Prompt(String id, String tool, String hint) {
            this.id = id;
            this.tool = tool;
            this.hint = hint;
        }
    }

    /** 设备发回来的决定。 */
    public static final class Decision {
        public final String id;
        public final boolean allow;

        Decision(String id, boolean allow) {
            this.id = id;
            this.allow = allow;
        }
    }

    public static String heartbeat(int total, int running, int waiting, String message,
                                   List<String> entries, long tokens, long tokensToday,
                                   Prompt prompt) {
        StringBuilder out = new StringBuilder(512);
        out.append("{\"total\":").append(Math.max(0, total));
        out.append(",\"running\":").append(Math.max(0, running));
        out.append(",\"waiting\":").append(Math.max(0, waiting));
        out.append(",\"msg\":");
        quote(out, clip(message, MESSAGE_MAX));
        out.append(",\"entries\":[");
        int count = entries == null ? 0 : Math.min(entries.size(), ENTRY_COUNT);
        for (int index = 0; index < count; index++) {
            if (index > 0) {
                out.append(',');
            }
            quote(out, clip(entries.get(index), ENTRY_MAX));
        }
        out.append("],\"tokens\":").append(Math.max(0L, tokens));
        out.append(",\"tokens_today\":").append(Math.max(0L, tokensToday));
        if (prompt != null && prompt.id != null && !prompt.id.isEmpty()
                && utf8Length(prompt.id) <= PROMPT_ID_MAX) {
            out.append(",\"prompt\":{\"id\":");
            quote(out, prompt.id);
            out.append(",\"tool\":");
            quote(out, clip(prompt.tool, TOOL_MAX));
            out.append(",\"hint\":");
            quote(out, clip(prompt.hint, HINT_MAX));
            out.append('}');
        }
        out.append("}\n");
        return out.toString();
    }

    /** 一条完整消息的正文，显示在设备的“最新回复”页。 */
    public static String turn(String text) {
        StringBuilder out = new StringBuilder(256);
        out.append("{\"evt\":\"turn\",\"role\":\"assistant\",\"content\":[{\"type\":\"text\",\"text\":");
        quote(out, clip(text, TURN_TEXT_MAX));
        out.append("}]}\n");
        return out.toString();
    }

    public static String time(long epochSeconds, int offsetSeconds) {
        return "{\"time\":[" + epochSeconds + "," + offsetSeconds + "]}\n";
    }

    /** 连上后告诉设备：这头是小幽中枢，能接收语音。旧固件不认识这一行，会回一个错误应答。 */
    public static String hubHello() {
        return "{\"cmd\":\"hub\",\"voice\":true}\n";
    }

    /** 设备发来的语音控制行里的 state（start / end / cancel）；不是语音控制行返回 null。 */
    public static String parseVoiceState(String line) {
        Map<String, String> fields = parseFlatObject(line);
        if (fields == null || !"voice".equals(fields.get("cmd"))) {
            return null;
        }
        String state = fields.get("state");
        return "start".equals(state) || "end".equals(state) || "cancel".equals(state)
                ? state : null;
    }

    /** 设备对 hubHello 的应答：true 能说话，false 是旧固件，null 表示这一行不是应答。 */
    public static Boolean parseHubAck(String line) {
        Map<String, String> fields = parseFlatObject(line);
        if (fields == null || !"hub".equals(fields.get("ack"))) {
            return null;
        }
        return line.contains("\"ok\":true");
    }

    /** 解析设备发来的一行；不是权限决定就返回 null。 */
    public static Decision parseDecision(String line) {
        Map<String, String> fields = parseFlatObject(line);
        if (fields == null || !"permission".equals(fields.get("cmd"))) {
            return null;
        }
        String id = fields.get("id");
        String decision = fields.get("decision");
        if (id == null || id.isEmpty() || decision == null) {
            return null;
        }
        if ("once".equals(decision) || "always".equals(decision)) {
            return new Decision(id, true);
        }
        if ("deny".equals(decision)) {
            return new Decision(id, false);
        }
        return null;
    }

    /** 把一行切成不超过 size 字节的若干段，按顺序写入蓝牙特征。 */
    public static List<byte[]> chunk(String line, int size) {
        byte[] bytes = line.getBytes(StandardCharsets.UTF_8);
        List<byte[]> parts = new ArrayList<>();
        int step = Math.max(1, size);
        for (int offset = 0; offset < bytes.length; offset += step) {
            int length = Math.min(step, bytes.length - offset);
            byte[] part = new byte[length];
            System.arraycopy(bytes, offset, part, 0, length);
            parts.add(part);
        }
        return parts;
    }

    /** 把蓝牙通知里零散到达的字节拼回一行一行的文本。 */
    public static final class LineAssembler {
        private final ByteArrayOutputStream buffer = new ByteArrayOutputStream();
        private boolean discarding;

        public List<String> feed(byte[] data) {
            List<String> lines = new ArrayList<>();
            if (data == null) {
                return lines;
            }
            for (byte value : data) {
                if (value == '\n') {
                    if (!discarding && buffer.size() > 0) {
                        lines.add(new String(buffer.toByteArray(), StandardCharsets.UTF_8));
                    }
                    buffer.reset();
                    discarding = false;
                } else if (!discarding) {
                    if (buffer.size() >= LINE_MAX) {
                        // 超长的一行整行丢弃，直到下一个换行。
                        buffer.reset();
                        discarding = true;
                    } else {
                        buffer.write(value);
                    }
                }
            }
            return lines;
        }

        public void reset() {
            buffer.reset();
            discarding = false;
        }
    }

    public static int utf8Length(String text) {
        return text == null ? 0 : text.getBytes(StandardCharsets.UTF_8).length;
    }

    /** 截到不超过 maxBytes 个 UTF-8 字节，不切断字符；换行和制表符换成空格以外的保留。 */
    public static String clip(String text, int maxBytes) {
        if (text == null) {
            return "";
        }
        StringBuilder out = new StringBuilder();
        int used = 0;
        for (int index = 0; index < text.length(); ) {
            int codePoint = text.codePointAt(index);
            int width = codePoint < 0x80 ? 1 : codePoint < 0x800 ? 2 : codePoint < 0x10000 ? 3 : 4;
            if (used + width > maxBytes) {
                break;
            }
            // 落单的代理项不是合法文本，固件会因此拒收整行，直接跳过。
            if (codePoint >= 0xD800 && codePoint <= 0xDFFF) {
                index += Character.charCount(codePoint);
                continue;
            }
            out.appendCodePoint(codePoint);
            used += width;
            index += Character.charCount(codePoint);
        }
        return out.toString();
    }

    static void quote(StringBuilder out, String text) {
        out.append('"');
        for (int index = 0; index < text.length(); index++) {
            char value = text.charAt(index);
            switch (value) {
                case '"':
                    out.append("\\\"");
                    break;
                case '\\':
                    out.append("\\\\");
                    break;
                case '\n':
                    out.append("\\n");
                    break;
                case '\r':
                    out.append("\\r");
                    break;
                case '\t':
                    out.append("\\t");
                    break;
                default:
                    if (value == 0) {
                        // 固件拒收带 \u0000 的行。
                        out.append(' ');
                    } else if (value < 0x20) {
                        out.append(String.format("\\u%04x", (int) value));
                    } else {
                        out.append(value);
                    }
            }
        }
        out.append('"');
    }

    /** 只认一层、值为字符串的 JSON 对象；其他类型的值跳过。格式不对返回 null。 */
    static Map<String, String> parseFlatObject(String line) {
        if (line == null) {
            return null;
        }
        Map<String, String> fields = new HashMap<>();
        int[] at = {0};
        skipSpace(line, at);
        if (at[0] >= line.length() || line.charAt(at[0]) != '{') {
            return null;
        }
        at[0]++;
        skipSpace(line, at);
        if (at[0] < line.length() && line.charAt(at[0]) == '}') {
            return fields;
        }
        while (at[0] < line.length()) {
            skipSpace(line, at);
            String key = readString(line, at);
            if (key == null) {
                return null;
            }
            skipSpace(line, at);
            if (at[0] >= line.length() || line.charAt(at[0]) != ':') {
                return null;
            }
            at[0]++;
            skipSpace(line, at);
            if (at[0] < line.length() && line.charAt(at[0]) == '"') {
                String value = readString(line, at);
                if (value == null) {
                    return null;
                }
                fields.put(key, value);
            } else if (!skipValue(line, at)) {
                return null;
            }
            skipSpace(line, at);
            if (at[0] >= line.length()) {
                return null;
            }
            char next = line.charAt(at[0]++);
            if (next == '}') {
                return fields;
            }
            if (next != ',') {
                return null;
            }
        }
        return null;
    }

    private static void skipSpace(String line, int[] at) {
        while (at[0] < line.length() && Character.isWhitespace(line.charAt(at[0]))) {
            at[0]++;
        }
    }

    private static String readString(String line, int[] at) {
        if (at[0] >= line.length() || line.charAt(at[0]) != '"') {
            return null;
        }
        StringBuilder out = new StringBuilder();
        at[0]++;
        while (at[0] < line.length()) {
            char value = line.charAt(at[0]++);
            if (value == '"') {
                return out.toString();
            }
            if (value != '\\') {
                out.append(value);
                continue;
            }
            if (at[0] >= line.length()) {
                return null;
            }
            char escaped = line.charAt(at[0]++);
            switch (escaped) {
                case 'n':
                    out.append('\n');
                    break;
                case 't':
                    out.append('\t');
                    break;
                case 'r':
                    out.append('\r');
                    break;
                case 'b':
                    out.append('\b');
                    break;
                case 'f':
                    out.append('\f');
                    break;
                case 'u':
                    if (at[0] + 4 > line.length()) {
                        return null;
                    }
                    try {
                        out.append((char) Integer.parseInt(line.substring(at[0], at[0] + 4), 16));
                    } catch (NumberFormatException error) {
                        return null;
                    }
                    at[0] += 4;
                    break;
                default:
                    out.append(escaped);
            }
        }
        return null;
    }

    /** 跳过一个非字符串的值（数字、true/false/null、嵌套的对象或数组）。 */
    private static boolean skipValue(String line, int[] at) {
        int depth = 0;
        boolean inString = false;
        while (at[0] < line.length()) {
            char value = line.charAt(at[0]);
            if (inString) {
                if (value == '\\') {
                    at[0]++;
                } else if (value == '"') {
                    inString = false;
                }
            } else if (value == '"') {
                inString = true;
            } else if (value == '{' || value == '[') {
                depth++;
            } else if (value == '}' || value == ']') {
                if (depth == 0) {
                    return true;
                }
                depth--;
            } else if (value == ',' && depth == 0) {
                return true;
            }
            at[0]++;
        }
        return false;
    }
}
