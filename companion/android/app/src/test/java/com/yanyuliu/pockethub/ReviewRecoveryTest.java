package com.yanyuliu.pockethub;

import org.junit.Test;
import java.io.IOException;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import static org.junit.Assert.*;

public class ReviewRecoveryTest {
    private static final String MESSAGE = "messages/m1", TIMER = "timers/t1";
    private static final Map<String, String> ANSWERS = Map.of("e1", "applied");
    private static ReviewRecovery.Source source(String token, long generation) {
        return new ReviewRecovery.Source("http://original", token, "runtime-1", "subject-1", generation);
    }
    private static Map<String, Object> view(String status, String revision) {
        return Json.parseObject("{\"runtime_id\":\"runtime-1\",\"operation_id\":\"op-1\",\"revision\":\"" + revision
                + "\",\"status\":\"" + status + "\",\"effects\":[{\"id\":\"e1\",\"name\":\"remind\",\"state\":\"unknown\"}]}");
    }
    private static final class Memory implements ReviewRecovery.Store {
        final Map<String, ReviewRecovery.Pending> values = new HashMap<>();
        boolean readFails, saveFails;
        Runnable afterSave = () -> {};
        int saves, clears;
        public ReviewRecovery.Pending load(String key) throws IOException {
            if (readFails) throw new IOException("sealed identity unavailable");
            return values.get(key);
        }
        public void save(String key, ReviewRecovery.Pending value) throws IOException {
            if (saveFails) throw new IOException("disk full");
            saves++; values.put(key, value); afterSave.run();
        }
        public void clear(String key, ReviewRecovery.Pending expected) {
            if (values.get(key) == expected) { clears++; values.remove(key); }
        }
    }
    private static final class Fixture {
        final Memory memory = new Memory();
        ReviewRecovery.Source current = source("original-credential", 1);
        Map<String, Object> response = view("needs_review", "v1");
        Map<String, Object> receipt = view("reviewed", "v1");
        final List<String> tokens = new ArrayList<>(), bodies = new ArrayList<>();
        String postFailure;
        Runnable afterGet = () -> {}, afterPost = () -> {};
        ReviewRecovery recovery = fresh();
        ReviewRecovery fresh() {
            return new ReviewRecovery(memory, (source, record, body) -> {
                if (body == null) { afterGet.run(); return response; }
                tokens.add(source.token); bodies.add(body); afterPost.run();
                if (postFailure != null) throw new IOException(postFailure);
                return receipt;
            }, s -> s.sameSession(current));
        }
        EffectReview shown() { return EffectReview.from(view("needs_review", "v1")); }
        ReviewRecovery.View submit(String record) throws Exception {
            return recovery.submit(current, record, shown(), ANSWERS);
        }
        void pending(String record) {
            memory.values.put(current.key(record), new ReviewRecovery.Pending(current, shown().body("saved-id", ANSWERS)));
        }
    }
    interface Attempt { void run() throws Exception; }
    private static void fails(Attempt action, String message) throws Exception {
        try { action.run(); fail("expected failure"); }
        catch (IOException expected) { assertTrue(expected.getMessage(), expected.getMessage().contains(message)); }
    }

    @Test public void lostReceiptAndRestartReuseOriginalBodyAndCredentialForMessagesAndTimers() throws Exception {
        for (String record : List.of(MESSAGE, TIMER)) {
            Fixture f = new Fixture(); f.postFailure = "503 after durable record";
            fails(() -> f.submit(record), "503");
            String saved = f.bodies.get(0);
            f.response = view("recorded", "v1"); f.current = source("upgraded-credential", 7);
            f.recovery = f.fresh(); f.postFailure = null;
            ReviewRecovery.View restored = f.recovery.load(f.current, record);
            assertFalse(restored.editable()); assertEquals(ANSWERS, restored.outcomes());
            assertEquals("reviewed", f.submit(record).review.status);
            assertEquals(List.of(saved, saved), f.bodies);
            assertEquals(List.of("original-credential", "original-credential"), f.tokens);
            assertEquals(2, f.memory.saves); assertTrue(f.memory.values.isEmpty());
        }
    }
    @Test public void remoteRecordedWithoutLocalIdentityCannotInventNewConfirmation() throws Exception {
        Fixture f = new Fixture(); f.response = view("recorded", "v1");
        assertNotNull(f.recovery.load(f.current, MESSAGE).problem);
        fails(() -> f.submit(MESSAGE), "保留原确认");
        assertTrue(f.bodies.isEmpty()); assertEquals(0, f.memory.saves);
    }
    @Test public void changedRevisionDoesNotApplyOldChoices() throws Exception {
        Fixture f = new Fixture(); f.response = view("needs_review", "v2");
        fails(() -> f.submit(MESSAGE), "记录已变化");
        assertEquals(0, f.memory.saves); assertTrue(f.bodies.isEmpty());
    }
    @Test public void freshReadRetiresStaleBodyAndRequiresNewChoices() throws Exception {
        Fixture f = new Fixture(); f.pending(MESSAGE); f.response = view("needs_review", "v2");
        ReviewRecovery.View read = f.recovery.load(f.current, MESSAGE);
        assertTrue(read.editable()); assertTrue(read.outcomes().isEmpty()); assertTrue(f.memory.values.isEmpty());
    }
    @Test public void missingVaultOrLegacyRecordBlocksSendingAndPreservesRecord() throws Exception {
        Fixture f = new Fixture(); f.pending(MESSAGE); f.memory.readFails = true;
        ReviewRecovery.View read = f.recovery.load(f.current, MESSAGE);
        assertNotNull(read.problem); assertFalse(read.editable());
        fails(() -> f.submit(MESSAGE), "unavailable"); assertEquals(1, f.memory.values.size()); assertTrue(f.bodies.isEmpty());
    }
    @Test public void failedDurableSaveCannotSend() throws Exception {
        Fixture f = new Fixture(); f.memory.saveFails = true;
        fails(() -> f.submit(MESSAGE), "disk full"); assertTrue(f.bodies.isEmpty());
    }
    @Test public void retryMustPersistEvenWhenFailedCommitLeftAnInMemoryEnvelope() throws Exception {
        Fixture f = new Fixture(); f.pending(MESSAGE); f.memory.saveFails = true;
        fails(() -> f.submit(MESSAGE), "disk full"); assertTrue(f.bodies.isEmpty());
        f.memory.saveFails = false; f.submit(MESSAGE);
        assertEquals(1, f.memory.saves); assertEquals(1, f.bodies.size());
    }
    @Test public void sourceChangesDuringReadCannotSaveOrSend() throws Exception {
        Fixture f = new Fixture(); f.afterGet = () -> f.current = source("other", 2);
        fails(() -> f.submit(MESSAGE), "连接已变化"); assertEquals(0, f.memory.saves); assertTrue(f.bodies.isEmpty());
    }
    @Test public void sourceChangesAfterSaveRetainPendingWithoutSending() throws Exception {
        Fixture f = new Fixture(); f.memory.afterSave = () -> f.current = source("other", 2);
        fails(() -> f.submit(MESSAGE), "连接已变化"); assertEquals(1, f.memory.values.size()); assertTrue(f.bodies.isEmpty());
    }
    @Test public void lateCompletionAfterSourceChangesCannotClearPending() throws Exception {
        Fixture f = new Fixture(); f.afterPost = () -> f.current = source("other", 2);
        fails(() -> f.submit(MESSAGE), "连接已变化"); assertEquals(1, f.memory.values.size()); assertEquals(0, f.memory.clears);
    }
    @Test public void switchingAwayAndBackInvalidatesOldSheet() throws Exception {
        Fixture f = new Fixture(); ReviewRecovery.Source old = f.current;
        f.current = source("original-credential", 3);
        fails(() -> f.recovery.submit(old, MESSAGE, f.shown(), ANSWERS), "连接已变化"); assertTrue(f.bodies.isEmpty());
    }
    @Test public void wrongRuntimeCannotSaveOrSend() throws Exception {
        Fixture f = new Fixture(); f.response.put("runtime_id", "replacement");
        fails(() -> f.submit(MESSAGE), "电脑身份已变化"); assertEquals(0, f.memory.saves); assertTrue(f.bodies.isEmpty());
    }
    @Test public void wrongReceiptRetainsPending() throws Exception {
        for (String field : List.of("runtime_id", "operation_id", "revision")) {
            Fixture f = new Fixture(); f.receipt.put(field, "wrong");
            fails(() -> f.submit(MESSAGE), "原确认仍保留"); assertEquals(1, f.memory.values.size());
        }
    }
    @Test public void unauthorizedAndConflictNeverDiscardOrSwitchIdentity() throws Exception {
        for (String code : List.of("401", "409")) {
            Fixture f = new Fixture(); f.pending(MESSAGE); f.current = source("new-credential", 2); f.postFailure = code;
            fails(() -> f.submit(MESSAGE), code); fails(() -> f.submit(MESSAGE), code);
            assertEquals(List.of("original-credential", "original-credential"), f.tokens);
            assertEquals(f.bodies.get(0), f.bodies.get(1)); assertEquals(1, f.memory.values.size());
        }
    }
    @Test public void savedChoicesRemainVisibleBeforeServerRecordsThem() throws Exception {
        Fixture f = new Fixture(); f.pending(MESSAGE);
        ReviewRecovery.View read = f.recovery.load(f.current, MESSAGE);
        assertEquals(ANSWERS, read.outcomes()); assertFalse(read.editable());
    }
    @Test public void concurrentSheetsCannotCreateTwoReviewIds() throws Exception {
        Fixture f = new Fixture();
        f.memory.afterSave = () -> {
            try { fails(() -> f.submit(MESSAGE), "正在核对"); }
            catch (Exception failure) { throw new AssertionError(failure); }
        };
        f.submit(MESSAGE); assertEquals(1, f.bodies.size()); assertEquals(1, f.memory.saves);
    }
    @Test public void replacementRuntimeOnSameUrlCannotReuseOriginalIdentity() throws Exception {
        Fixture f = new Fixture(); f.pending(MESSAGE);
        f.current = new ReviewRecovery.Source("http://original", "new", "replacement", "subject-1", 2);
        f.response.put("runtime_id", "replacement");
        assertNotNull(f.recovery.load(f.current, MESSAGE).problem);
        fails(() -> f.submit(MESSAGE), "原电脑"); assertTrue(f.bodies.isEmpty()); assertEquals(1, f.memory.values.size());
    }
}
