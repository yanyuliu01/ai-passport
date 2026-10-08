package com.yanyuliu.pockethub;

import java.util.List;

/**
 * 一轮已经完成的对话：我说了什么，小幽答了什么。纯 Java，不依赖安卓。
 *
 * 手机记着最近的若干轮。换一台 Runtime 时把它们带过去，那台电脑上的小幽就能接上话。
 */
final class ChatTurn {
    /** 带给 Runtime 的轮数上限，以及每轮文字的长度上限（和 Runtime 的限制一致）。 */
    static final int SHARE_LIMIT = 12;
    static final int TEXT_LIMIT = 4000;
    static final int REPLY_LIMIT = 8000;

    final String id;
    final String text;
    final String reply;
    /** 完成时间，Unix 秒。 */
    final long at;

    ChatTurn(String id, String text, String reply, long at) {
        this.id = id;
        this.text = text;
        this.reply = reply;
        this.at = at;
    }

    private static String cut(String value, int limit) {
        if (value.length() <= limit) {
            return value;
        }
        // 不把一个代理对（表情等）切成两半。
        int end = Character.isHighSurrogate(value.charAt(limit - 1)) ? limit - 1 : limit;
        return value.substring(0, end);
    }

    /** POST /v1/conversations/&lt;名字&gt;/history 的请求体：最近的 SHARE_LIMIT 轮，旧的在前。 */
    static String historyJson(List<ChatTurn> turns) {
        StringBuilder out = new StringBuilder("{\"turns\":[");
        int start = Math.max(0, turns.size() - SHARE_LIMIT);
        for (int index = start; index < turns.size(); index++) {
            ChatTurn turn = turns.get(index);
            if (index > start) {
                out.append(',');
            }
            out.append("{\"id\":");
            BuddyProtocol.quote(out, turn.id);
            out.append(",\"text\":");
            BuddyProtocol.quote(out, cut(turn.text, TEXT_LIMIT));
            out.append(",\"reply\":");
            BuddyProtocol.quote(out, cut(turn.reply, REPLY_LIMIT));
            out.append(",\"at\":").append(turn.at).append('}');
        }
        return out.append("]}").toString();
    }
}
