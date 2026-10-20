package com.yanyuliu.pockethub;

import java.util.ArrayList;
import java.util.List;

/**
 * 聊天列表怎么更新。每条消息一行，带一个「样子」签名（失败没、有没有气泡、回执写的什么）。
 * 前面的行都还在、顺序没变时：没变的不动，变了的就地换掉，新来的接在后面；
 * 只有窗口挪了、有行被删了或顺序变了，才整个重画。整个重画会闪一下、滚动位置会跳。
 */
final class ChatPlan {
    /** 要整个重画。 */
    final boolean rebuild;
    /** 不重画时：要就地换掉的行。 */
    final List<Integer> replace;
    /** 不重画时：从这一行起是新来的，接在后面。 */
    final int appendFrom;

    private ChatPlan(boolean rebuild, List<Integer> replace, int appendFrom) {
        this.rebuild = rebuild; this.replace = replace; this.appendFrom = appendFrom;
    }

    static ChatPlan of(String oldHeader, List<String> oldIds, List<String> oldLooks,
                       String newHeader, List<String> newIds, List<String> newLooks) {
        List<Integer> replace = new ArrayList<>();
        if (oldIds.isEmpty() || !oldHeader.equals(newHeader) || newIds.size() < oldIds.size())
            return new ChatPlan(true, replace, 0);
        for (int index = 0; index < oldIds.size(); index++) {
            if (!oldIds.get(index).equals(newIds.get(index))) return new ChatPlan(true, new ArrayList<>(), 0);
            if (!oldLooks.get(index).equals(newLooks.get(index))) replace.add(index);
        }
        return new ChatPlan(false, replace, oldIds.size());
    }

    boolean unchanged(int total) { return !rebuild && replace.isEmpty() && appendFrom >= total; }
}
