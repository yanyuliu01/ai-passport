package com.yanyuliu.pockethub;

import org.junit.Test;
import java.util.*;
import static org.junit.Assert.*;

public class HistorySharingTest {
    private static final String A = "a".repeat(32), B = "b".repeat(32);
    private static final String A1 = "relay://" + "1".repeat(32) + "." + A;
    private static final String A2 = "relay://" + "2".repeat(32) + "." + A;
    private static final String B1 = "relay://" + "3".repeat(32) + "." + B;
    private static final String OLD = "http://192.168.1.1:8765";

    private ChatHistory history(String id, String text, long at) {
        ChatHistory h = new ChatHistory(); h.begin(id, null, text, at);
        h.finish(id, null, "answer-" + text, false, null); return h;
    }

    @Test public void account_history_never_reads_or_uploads_other_accounts_or_unscoped_legacy() {
        Map<String,String> saved = new HashMap<>();
        saved.put(ArtifactDownload.key(A1), history("a1", "first", 1).json());
        saved.put(ArtifactDownload.key(A2), history("a2", "second", 2).json());
        saved.put(ArtifactDownload.key(B1), history("b", "other account", 3).json());
        saved.put(ArtifactDownload.key(OLD), history("old", "old direct", 4).json());
        List<String> reads = new ArrayList<>();
        List<ChatTurn> found = HistorySharing.recent(A1, List.of(A1,A2,B1,OLD), key -> {
            reads.add(key); return saved.get(key);
        }, B1, history("live-b", "other live account", 6),
                List.of(new ChatTurn("unscoped", "private", "answer", 5)), 20);
        assertEquals(List.of("a1", "a2"), found.stream().map(turn -> turn.id).toList());
        assertEquals(Set.of(ArtifactDownload.key(A1), ArtifactDownload.key(A2)), new HashSet<>(reads));
        assertFalse(HistorySharing.allowsLegacy(A1,A));
        assertFalse(HistorySharing.allowsLegacy(OLD,A));
        assertFalse(HistorySharing.allowsLegacy(null,""));
    }

    @Test public void legacy_routes_and_matching_live_history_remain_available_in_legacy_mode() {
        ChatHistory current = history("live", "here", 3);
        String old2 = "https://other.example";
        Map<String,String> stored = Map.of(ArtifactDownload.key(OLD), history("old", "here", 1).json(),
                ArtifactDownload.key(old2), history("other", "there", 2).json(),
                ArtifactDownload.key(A1), history("account", "private", 4).json());
        List<ChatTurn> found = HistorySharing.recent(OLD, List.of(OLD,old2,A1), stored::get, OLD, current,
                List.of(new ChatTurn("legacy", "legacy", "reply", 0)), 20);
        assertEquals(List.of("legacy","old","other","live"), found.stream().map(turn -> turn.id).toList());
        assertTrue(HistorySharing.allowsLegacy(OLD,""));
    }

    @Test public void malformed_relay_and_unknown_active_archive_do_not_share() {
        assertTrue(HistorySharing.recent("relay://bad.account", List.of(A1), key -> failRead(),
                A1, history("a", "private", 1), List.of(), 20).isEmpty());
        assertTrue(HistorySharing.recent(A1, List.of(A1), key -> null,
                B1, history("b", "private", 1), List.of(), 20).isEmpty());
    }

    @Test public void old_account_and_empty_archive_are_not_visible_after_switch_or_logout() {
        assertTrue(HistorySharing.visible(A1,A));
        assertFalse(HistorySharing.visible(A1,B));
        assertFalse(HistorySharing.visible(A1,""));
        assertFalse(HistorySharing.visible("", ""));
        assertFalse(HistorySharing.visible(OLD,A));
        assertFalse(HistorySharing.visible(OLD,""));
    }

    @Test public void prepared_archive_overlay_preserves_newer_memory_and_deletions() {
        ChatHistory stored=history("same", "old", 1);
        stored.begin("deleted",null,"removed",2);stored.scope("deleted","side",true);
        ChatHistory live=history("same", "new", 3);live.eraseConversation("side");
        live.begin("pending",null,"still sending",4);
        stored.overlay(live);
        assertEquals("new",stored.main().get(0).text);
        assertTrue(stored.main("side").isEmpty());
        assertTrue(stored.main().stream().anyMatch(message -> message.id.equals("pending:you") && message.status.equals("sending")));
    }

    private String failRead() { throw new AssertionError("unrecognized target must not read history"); }
}
