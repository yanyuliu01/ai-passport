package com.yanyuliu.pockethub;

import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.function.Function;

/** Select only archives belonging to the target account before sharing turns. */
final class HistorySharing {
    static boolean allowsLegacy(String target, String account) {
        return target != null && RuntimeClient.historyAccountScope(target).equals("legacy")
                && (account == null || account.isEmpty());
    }
    static boolean visible(String archive, String account) {
        if (archive == null || archive.isEmpty()) return false;
        String scope=RuntimeClient.historyAccountScope(archive);
        return account != null && !account.isEmpty() && scope.equals("account:"+account);
    }
    static Set<String> keys(String target, List<String> routes) {
        Set<String> keys = new LinkedHashSet<>();
        String scope = RuntimeClient.historyAccountScope(target);
        if (target == null || scope.startsWith("invalid:")) return keys;
        keys.add(ArtifactDownload.key(target));
        for (String route : routes)
            if (route != null && scope.equals(RuntimeClient.historyAccountScope(route)))
                keys.add(ArtifactDownload.key(route));
        return keys;
    }

    static List<ChatTurn> recent(String target, List<String> routes, Function<String, String> read,
                                 String active, ChatHistory current, List<ChatTurn> legacy, int limit) {
        Map<String, ChatTurn> found = new LinkedHashMap<>();
        Set<String> allowed = keys(target, routes);
        if (allowed.isEmpty()) return new ArrayList<>();
        if (RuntimeClient.historyAccountScope(target).equals("legacy"))
            for (ChatTurn turn : legacy) found.put(turn.id, turn);
        for (String key : allowed) {
            String stored = read.apply(key);
            if (stored != null) for (ChatTurn turn : ChatHistory.restore(stored).turns()) found.put(turn.id, turn);
        }
        if (current != null && active != null && allowed.contains(ArtifactDownload.key(active)))
            for (ChatTurn turn : current.turns()) found.put(turn.id, turn);
        List<ChatTurn> all = new ArrayList<>(found.values());
        all.sort(Comparator.comparingLong((ChatTurn turn) -> turn.at));
        return all.subList(Math.max(0, all.size() - Math.max(0, limit)), all.size());
    }
    private HistorySharing() {}
}
