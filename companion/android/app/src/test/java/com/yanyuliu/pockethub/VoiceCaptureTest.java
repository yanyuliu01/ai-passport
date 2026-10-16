package com.yanyuliu.pockethub;

import org.junit.Test;
import static org.junit.Assert.*;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;

public class VoiceCaptureTest {
    private static final class Result implements VoiceCapture.Callback {
        final CountDownLatch ready = new CountDownLatch(1), finished = new CountDownLatch(1), limit = new CountDownLatch(1);
        final AtomicInteger calls = new AtomicInteger();
        volatile byte[] wav; volatile String error; volatile boolean cancelled;
        @Override public void ready() { ready.countDown(); }
        @Override public void level(float value) { assertTrue(value >= 0 && value <= 1); }
        @Override public void limitReached() { limit.countDown(); }
        @Override public void finished(byte[] wav, String error, boolean cancelled) {
            this.wav = wav; this.error = error; this.cancelled = cancelled; calls.incrementAndGet(); finished.countDown();
        }
        void await() throws Exception { assertTrue(finished.await(2, TimeUnit.SECONDS)); }
    }
    private static final class Source implements VoiceCapture.Source {
        final CountDownLatch samples = new CountDownLatch(1);
        int starts, stops, releases, reads;
        boolean fail;
        @Override public void start() { starts++; }
        @Override public int read(byte[] data) {
            if (fail) return -6;
            if (reads++ > 0) return 0;
            data[0] = 1; data[2] = 2; samples.countDown(); return 8;
        }
        @Override public void stop() { stops++; }
        @Override public void release() { releases++; }
    }

    @Test public void holdingRecordsButDoesNotSendUntilRelease() throws Exception {
        VoiceCapture capture = new VoiceCapture(4, 100); Source source = new Source(); Result result = new Result();
        assertTrue(capture.start(() -> source, result));
        assertTrue(source.samples.await(2, TimeUnit.SECONDS)); assertEquals(0, result.calls.get());
        capture.stop(false); result.await();
        assertNotNull(result.wav); assertEquals(52, result.wav.length); assertNull(result.error);
        assertEquals(1, source.stops); assertEquals(1, source.releases); assertFalse(capture.running());
    }

    @Test public void cancelDiscardsRecordedAudioAndCleansUpOnce() throws Exception {
        VoiceCapture capture = new VoiceCapture(4, 100); Source source = new Source(); Result result = new Result();
        capture.start(() -> source, result); assertTrue(source.samples.await(2, TimeUnit.SECONDS));
        capture.stop(true); capture.stop(true); result.await();
        assertTrue(result.cancelled); assertNull(result.wav); assertEquals(1, result.calls.get());
        assertEquals(1, source.releases);
    }

    @Test public void quickReleaseDuringStartupDoesNotBlockOrStartLateRecording() throws Exception {
        VoiceCapture capture = new VoiceCapture(4, 100); Source source = new Source(); Result result = new Result();
        CountDownLatch creating = new CountDownLatch(1), allow = new CountDownLatch(1);
        capture.start(() -> { creating.countDown(); assertTrue(allow.await(2, TimeUnit.SECONDS)); return source; }, result);
        assertTrue(creating.await(2, TimeUnit.SECONDS));
        capture.stop(false); assertEquals(0, source.starts); assertEquals(0, result.calls.get());
        allow.countDown(); result.await();
        assertEquals(0, source.starts); assertNull(result.wav); assertTrue(result.error.contains("录音太短"));
        assertEquals(1, source.releases);
    }

    @Test public void readFailureDoesNotLeaveTheNextPressStuck() throws Exception {
        VoiceCapture capture = new VoiceCapture(4, 8); Source failed = new Source(); failed.fail = true;
        Result first = new Result(); capture.start(() -> failed, first); first.await();
        assertTrue(first.error.contains("-6")); assertNull(first.wav); assertFalse(capture.running());
        Source working = new Source(); Result second = new Result();
        assertTrue(capture.start(() -> working, second)); assertTrue(second.limit.await(2, TimeUnit.SECONDS));
        capture.stop(false); second.await();
        assertNotNull(second.wav); assertNull(second.error); assertEquals(1, failed.releases);
    }

    @Test public void permissionFailureHasFeedbackAndCanBeRetriedAfterGrant() throws Exception {
        VoiceCapture capture = new VoiceCapture(4, 8); Result first = new Result();
        capture.start(() -> { throw new SecurityException(); }, first); first.await();
        assertTrue(first.error.contains("权限")); assertFalse(capture.running());
        Result next = new Result(); assertTrue(capture.start(Source::new, next));
        assertTrue(next.limit.await(2, TimeUnit.SECONDS)); capture.stop(false); next.await(); assertNotNull(next.wav);
    }

    @Test public void doubleStartIsRejectedAndLengthIsBounded() throws Exception {
        VoiceCapture capture = new VoiceCapture(4, 8); Result result = new Result();
        CountDownLatch create = new CountDownLatch(1), allow = new CountDownLatch(1);
        capture.start(() -> { create.countDown(); allow.await(2, TimeUnit.SECONDS); return new Source(); }, result);
        assertTrue(create.await(2, TimeUnit.SECONDS)); assertFalse(capture.start(Source::new, new Result()));
        allow.countDown(); assertTrue(result.limit.await(2, TimeUnit.SECONDS));
        assertEquals(0, result.calls.get()); capture.stop(false); result.await();
        assertEquals(52, result.wav.length); assertEquals(1, result.calls.get());
    }

    @Test public void defaultCaptureKeepsAWordShorterThanHalfASecond() throws Exception {
        VoiceCapture capture = new VoiceCapture(); Result result = new Result();
        CountDownLatch recorded = new CountDownLatch(1);
        VoiceCapture.Source source = new VoiceCapture.Source() {
            int reads;
            @Override public void start() { }
            @Override public int read(byte[] buffer) {
                // Two 64 ms PCM blocks: 128 ms of audio must reach recognition.
                if (reads++ >= 2) return 0;
                java.util.Arrays.fill(buffer, (byte) 1);
                if (reads == 2) recorded.countDown();
                return buffer.length;
            }
            @Override public void stop() { }
            @Override public void release() { }
        };
        assertTrue(capture.start(() -> source, result));
        assertTrue(recorded.await(2, TimeUnit.SECONDS)); capture.stop(false); result.await();
        assertNull(result.error); assertEquals(4096 + 44, result.wav.length);
    }
}
