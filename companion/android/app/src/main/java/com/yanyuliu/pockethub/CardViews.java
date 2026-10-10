package com.yanyuliu.pockethub;

import java.util.ArrayList;
import java.util.List;

/**
 * 卡在手机上怎么分、怎么写成字。纯 Java，不依赖安卓。
 *
 * 卡分两种：小幽自己直接答的是“对话”，交给过帮手的是“任务”。对话那一页把对话连着
 * 显示，任务只留一行指路的；任务各有各的页面，在里面说的话只归到它自己。
 */
final class CardViews {
    static final int PREVIEW_CHARS = 60;

    private CardViews() {
    }

    /** 交给过帮手的事是任务；小幽自己答的（包括没答成的）是对话。 */
    static boolean isTask(Card card) {
        return !card.agent.isEmpty();
    }

    /** 任务：还在做的在前，然后是结束的；各自新的在前。最多 limit 件。 */
    static List<Card> tasks(List<Card> cards, int limit) {
        List<Card> out = new ArrayList<>();
        for (int pass = 0; pass < 2; pass++) {
            for (int index = cards.size() - 1; index >= 0; index--) {
                Card card = cards.get(index);
                if (isTask(card) && card.active() == (pass == 0) && out.size() < limit) {
                    out.add(card);
                }
            }
        }
        return out;
    }

    /** 还在做的任务有几件。 */
    static int doing(List<Card> cards) {
        int count = 0;
        for (Card card : cards) {
            if (isTask(card) && card.active()) {
                count++;
            }
        }
        return count;
    }

    static Card find(List<Card> cards, String id) {
        if (id != null) {
            for (Card card : cards) {
                if (card.id.equals(id)) {
                    return card;
                }
            }
        }
        return null;
    }

    /** 一张卡里说过的话，一句一行：“我：……”“小幽：……”。 */
    static String thread(Card card) {
        StringBuilder out = new StringBuilder();
        for (Card.Entry entry : card.entries) {
            if (out.length() > 0) {
                out.append("\n\n");
            }
            out.append(entry.role.equals("you") ? "我：" : "小幽：").append(entry.text);
        }
        return out.toString();
    }

    /** “c3 · codex · 在做（改过 1 次）”。 */
    static String status(Card card) {
        StringBuilder out = new StringBuilder(card.id);
        if (!card.agent.isEmpty()) {
            out.append(" · ").append(card.agent);
        }
        out.append(" · ").append(card.stateLabel());
        if (card.edits > 0) {
            out.append("（改过 ").append(card.edits).append(" 次）");
        }
        return out.toString();
    }

    /** 对话那一页里给任务留的一行：只说有这么件事、现在怎么样，内容在任务里看。 */
    static String pointer(Card card) {
        return "↪ 任务 " + status(card) + "\n" + card.title;
    }

    /** 任务列表里的一项：状态、标题、最新的一句。 */
    static String listItem(Card card) {
        String latest = card.active() && !card.progress.isEmpty()
                ? "› " + card.progress.get(card.progress.size() - 1)
                : (card.brief.isEmpty() ? card.lastSay() : card.brief);
        latest = oneLine(latest, PREVIEW_CHARS);
        return status(card) + "\n" + card.title + (latest.isEmpty() ? "" : "\n" + latest);
    }

    static String oneLine(String text, int limit) {
        String flat = text.replace('\n', ' ').trim();
        if (flat.length() <= limit) {
            return flat;
        }
        // 不把一个代理对（表情等）切成两半。
        int end = Character.isHighSurrogate(flat.charAt(limit - 1)) ? limit - 1 : limit;
        return flat.substring(0, end) + "…";
    }

    /** 发一句话的请求体。card 不为 null 表示是在那件事里面说的：一定归到它。 */
    static String messageJson(String text, String clientId, String card) {
        StringBuilder body = new StringBuilder("{\"text\":");
        BuddyProtocol.quote(body, text);
        body.append(",\"client_id\":\"").append(clientId).append('"');
        if (BuddyProtocol.cardId(card)) {
            body.append(",\"card\":\"").append(card).append("\",\"pin\":true");
        }
        return body.append('}').toString();
    }
}
