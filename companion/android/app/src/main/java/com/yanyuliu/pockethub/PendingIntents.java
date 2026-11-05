package com.yanyuliu.pockethub;

import java.util.LinkedHashMap;
import java.util.Map;

/** Small durable records bridge a send and the next durable full archive snapshot. */
final class PendingIntents {
    interface Store {
        Map<String, ?> all();
        boolean put(String key, String value);
        boolean remove(String key);
    }

    private static String prefix(String archive) {
        if (archive == null || !archive.matches("[a-f0-9]{64}")) throw new IllegalArgumentException("Invalid archive");
        return archive + ":";
    }

    private static ChatHistory parse(String json, String client) {
        ChatHistory intent = HistoryBootstrap.merge(json, new ChatHistory());
        // Validate shape before importing, even when this record is already covered.
        new ChatHistory().mergeIntent(intent, client);
        return intent;
    }

    static boolean save(Store store, String archive, String client, ChatHistory intent) {
        if (client == null || !client.matches("[a-f0-9]{32}") || intent == null) return false;
        String key = prefix(archive) + client, value = intent.json();
        try {
            parse(value, client);
            Object previous = store.all().get(key);
            if (previous != null) {
                if (!(previous instanceof String)) return false;
                if (!parse((String) previous, client).sameIntent(intent, client)) return false;
                // A failed SharedPreferences commit can still update its in-memory map.
                // Force a new durable write; observing an equal value proves nothing.
                return store.put(key, (String) previous + " ");
            }
            return store.put(key, value);
        } catch (IllegalArgumentException error) { return false; }
    }

    /** Snapshot only one account/source namespace; malformed records remain untouched. */
    static Map<String, String> records(Store store, String archive) {
        String prefix = prefix(archive);
        Map<String, String> result = new LinkedHashMap<>();
        for (Map.Entry<String, ?> entry : store.all().entrySet())
            if (entry.getKey().startsWith(prefix))
                result.put(entry.getKey().substring(prefix.length()), entry.getValue() instanceof String ? (String) entry.getValue() : "");
        return result;
    }

    static boolean hasInvalid(Map<String, String> records) {
        for (Map.Entry<String, String> entry : records.entrySet()) {
            try {
                if (!entry.getKey().matches("[a-f0-9]{32}")) return true;
                parse(entry.getValue(), entry.getKey());
            } catch (IllegalArgumentException error) { return true; }
        }
        return false;
    }

    static int recover(ChatHistory history, Map<String, String> records) {
        int count = 0; boolean invalid = false;
        for (Map.Entry<String, String> entry : records.entrySet()) {
            try {
                String client = entry.getKey();
                if (!client.matches("[a-f0-9]{32}")) { invalid = true; continue; }
                if (history.mergeIntent(parse(entry.getValue(), client), client)) count++;
            } catch (IllegalArgumentException ignored) { invalid = true; /* Keep the original record for recovery. */ }
        }
        return invalid ? -1 : count;
    }

    /** Called only after the snapshot was committed; never remove a newer or corrupt record. */
    static void clearCovered(Store store, String archive, ChatHistory snapshot, Map<String, String> captured) {
        String prefix = prefix(archive);
        for (Map.Entry<String, String> entry : captured.entrySet()) {
            String client = entry.getKey(), key = prefix + client;
            try {
                if (client.matches("[a-f0-9]{32}") && entry.getValue().equals(store.all().get(key))
                        && snapshot.coversIntent(parse(entry.getValue(), client), client)) store.remove(key);
            } catch (IllegalArgumentException ignored) { /* Preserve unreadable records. */ }
        }
    }

    private PendingIntents() {}
}
