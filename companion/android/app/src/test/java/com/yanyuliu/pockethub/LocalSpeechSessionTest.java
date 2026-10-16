package com.yanyuliu.pockethub;

import org.junit.Test;
import static org.junit.Assert.*;

public class LocalSpeechSessionTest {
    @Test public void earlyResultWaitsForReleaseAndSendsOnce() {
        LocalSpeechSession session = new LocalSpeechSession();
        session.result("  你好  "); assertNull(session.take());
        session.release(); assertEquals("你好", session.take()); assertNull(session.take());
    }
    @Test public void releaseBeforeRecognitionWaitsForFinalText() {
        LocalSpeechSession session = new LocalSpeechSession();
        session.release(); assertNull(session.take());
        session.result("安排任务"); assertEquals("安排任务", session.take());
    }
    @Test public void leavingOrCancellingDiscardsEvenLateResults() {
        LocalSpeechSession session = new LocalSpeechSession();
        session.result("不发送"); session.cancel(); session.release(); assertNull(session.take());
        LocalSpeechSession next = new LocalSpeechSession();
        next.cancel(); next.result("延迟回调"); next.release(); assertNull(next.take());
    }
    @Test public void emptyResultsAndRepeatedFinalsCannotSendAnotherMessage() {
        LocalSpeechSession session = new LocalSpeechSession();
        session.result(" "); session.release(); assertNull(session.take());
        session.result("迟到的文字"); assertNull(session.take());
    }
}
