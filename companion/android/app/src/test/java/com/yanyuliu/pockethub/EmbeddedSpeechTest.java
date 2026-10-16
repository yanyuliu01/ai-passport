package com.yanyuliu.pockethub;

import org.junit.Test;
import static org.junit.Assert.*;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;

public class EmbeddedSpeechTest {
    private static class Decoder implements EmbeddedSpeech.Decoder {
        final CountDownLatch samples = new CountDownLatch(1);
        int closes, finishes;
        float first, second;
        @Override public void samples(float[] pcm) { first = pcm[0]; second = pcm[1]; samples.countDown(); }
        @Override public String partial() { return "测试"; }
        @Override public String finish() { finishes++; return "测试完成"; }
        @Override public void close() { closes++; }
    }
    private static final class Mic implements VoiceCapture.Source {
        final CountDownLatch samples = new CountDownLatch(1);
        final CountDownLatch released = new CountDownLatch(1);
        int reads, closes;
        @Override public void start() { }
        @Override public int read(byte[] buffer) {
            if (reads++ > 0) { java.util.Arrays.fill(buffer, (byte) 0); return 0; }
            buffer[0] = 0; buffer[1] = (byte) 0x80;
            buffer[2] = (byte) 0xff; buffer[3] = 0x7f; samples.countDown(); return 4;
        }
        @Override public void stop() { }
        @Override public void release() { closes++; released.countDown(); }
    }
    private static final class Result implements EmbeddedSpeech.Callback {
        final CountDownLatch ready = new CountDownLatch(1);
        final CountDownLatch partial = new CountDownLatch(1), done = new CountDownLatch(1), limit = new CountDownLatch(1);
        String text, error; boolean cancelled; byte[] wav; boolean wavBeforeDone;
        @Override public void ready() { ready.countDown(); }
        @Override public void recording(byte[] value) { wav = value; wavBeforeDone = done.getCount() == 1; }
        @Override public void level(float value) { }
        @Override public void partial(String value) { assertEquals("测试", value); partial.countDown(); }
        @Override public void limitReached() { limit.countDown(); }
        @Override public void finished(String text, String error, boolean cancelled) {
            this.text = text; this.error = error; this.cancelled = cancelled; done.countDown();
        }
        void await() throws Exception { assertTrue(done.await(2, TimeUnit.SECONDS)); }
    }
    @Test public void streamsLocallyButOnlyReturnsFinalTextAfterRelease() throws Exception {
        EmbeddedSpeech speech = new EmbeddedSpeech(new VoiceCapture(4, 100));
        Decoder decoder = new Decoder(); Mic mic = new Mic(); Result result = new Result();
        assertTrue(speech.start(() -> mic, () -> decoder, result));
        assertTrue(result.partial.await(2, TimeUnit.SECONDS)); assertEquals(1, result.done.getCount());
        assertEquals(-1f, decoder.first, 0f); assertEquals(32767f / 32768f, decoder.second, 0f);
        speech.stop(false); result.await();
        assertEquals("测试完成", result.text); assertNull(result.error);
        assertEquals(1, decoder.finishes); assertEquals(1, decoder.closes); assertEquals(1, mic.closes);
    }
    @Test public void recordingIsHandedOverBeforeFinishedEvenWhenDecodingFails() throws Exception {
        EmbeddedSpeech speech = new EmbeddedSpeech(new VoiceCapture(4, 100));
        Result ok = new Result();
        speech.start(Mic::new, Decoder::new, ok);
        assertTrue(ok.partial.await(2, TimeUnit.SECONDS)); speech.stop(false); ok.await();
        assertNotNull(ok.wav); assertTrue(ok.wavBeforeDone);
        assertEquals(44 + 4, ok.wav.length);  // WAV header plus the two captured samples
        // A decoder failure still leaves the recording, so the computer can recognize it instead.
        Decoder broken = new Decoder() { @Override public String finish() { throw new IllegalStateException("识别引擎出错"); } };
        Result failed = new Result();
        speech.start(Mic::new, () -> broken, failed);
        assertTrue(broken.samples.await(2, TimeUnit.SECONDS)); speech.stop(false); failed.await();
        assertEquals("识别引擎出错", failed.error); assertNotNull(failed.wav);
        // Cancelled recordings go nowhere.
        Result cancelled = new Result(); Decoder decoder = new Decoder();
        speech.start(Mic::new, () -> decoder, cancelled);
        assertTrue(decoder.samples.await(2, TimeUnit.SECONDS)); speech.stop(true); cancelled.await();
        assertNull(cancelled.wav);
    }

    @Test public void cancelNeverFinalizesOrReturnsRecognizedText() throws Exception {
        EmbeddedSpeech speech = new EmbeddedSpeech(new VoiceCapture(4, 100));
        Decoder decoder = new Decoder(); Result result = new Result();
        speech.start(Mic::new, () -> decoder, result);
        assertTrue(decoder.samples.await(2, TimeUnit.SECONDS)); speech.stop(true); result.await();
        assertTrue(result.cancelled); assertNull(result.text); assertEquals(0, decoder.finishes); assertEquals(1, decoder.closes);
    }
    @Test public void modelFailureReleasesMicrophoneAndCanBeRetried() throws Exception {
        EmbeddedSpeech speech = new EmbeddedSpeech(new VoiceCapture(4, 100));
        Mic mic = new Mic(); Result failed = new Result();
        speech.start(() -> mic, () -> {
            if (!mic.samples.await(2, TimeUnit.SECONDS)) throw new IllegalStateException("mic did not start");
            throw new IllegalStateException("模型加载失败");
        }, failed);
        failed.await(); assertEquals(1, mic.closes); assertEquals("模型加载失败", failed.error);
        assertFalse(speech.running());
        Decoder decoder = new Decoder(); Result next = new Result();
        assertTrue(speech.start(Mic::new, () -> decoder, next));
        assertTrue(decoder.samples.await(2, TimeUnit.SECONDS)); speech.stop(false); next.await(); assertNotNull(next.text);
    }
    @Test public void permissionFailureClosesTheModel() throws Exception {
        EmbeddedSpeech speech = new EmbeddedSpeech(new VoiceCapture(4, 100));
        Decoder decoder = new Decoder(); Result result = new Result();
        speech.start(() -> { throw new SecurityException(); }, () -> decoder, result); result.await();
        assertNull(result.text); assertTrue(result.error.contains("权限")); assertEquals(1, decoder.closes);
    }

    @Test public void decoderFailureStopsAndReleasesCaptureBeforeRetry() throws Exception {
        EmbeddedSpeech speech = new EmbeddedSpeech(new VoiceCapture(4, 100));
        Decoder decoder = new Decoder() {
            @Override public void samples(float[] pcm) { throw new IllegalStateException("识别引擎出错"); }
        };
        Mic mic = new Mic(); Result failed = new Result();
        speech.start(() -> mic, () -> decoder, failed); failed.await();
        assertEquals("识别引擎出错", failed.error); assertNull(failed.text);
        assertEquals(1, mic.closes); assertEquals(1, decoder.closes); assertEquals(0, decoder.finishes);
        Decoder nextDecoder = new Decoder(); Result next = new Result();
        assertTrue(speech.start(Mic::new, () -> nextDecoder, next));
        assertTrue(nextDecoder.samples.await(2, TimeUnit.SECONDS)); speech.stop(false); next.await();
        assertNull(next.error); assertEquals("测试完成", next.text);
    }
    @Test public void durationLimitWaitsForReleaseAndDoesNotSendByItself() throws Exception {
        EmbeddedSpeech speech = new EmbeddedSpeech(new VoiceCapture(4, 4));
        Decoder decoder = new Decoder(); Result result = new Result();
        speech.start(Mic::new, () -> decoder, result); assertTrue(result.limit.await(2, TimeUnit.SECONDS));
        assertEquals(1, result.done.getCount()); assertEquals(0, decoder.finishes);
        speech.stop(false); result.await(); assertEquals("测试完成", result.text);
    }

    @Test public void capturesAndRetainsAudioBeforeModelIsReadyIncludingReleaseDuringLoad() throws Exception {
        EmbeddedSpeech speech = new EmbeddedSpeech(new VoiceCapture(4, 100));
        Decoder decoder = new Decoder(); Mic mic = new Mic(); Result result = new Result();
        CountDownLatch loading = new CountDownLatch(1), loaded = new CountDownLatch(1);
        speech.start(() -> mic, () -> {
            loading.countDown();
            if (!loaded.await(2, TimeUnit.SECONDS)) throw new IllegalStateException("model load timed out");
            return decoder;
        }, result);
        assertTrue(loading.await(2, TimeUnit.SECONDS));
        assertTrue(result.ready.await(2, TimeUnit.SECONDS));
        assertTrue(mic.samples.await(2, TimeUnit.SECONDS));
        assertEquals(1, decoder.samples.getCount());
        speech.stop(false);
        assertTrue(mic.released.await(2, TimeUnit.SECONDS));
        assertEquals(1, result.done.getCount()); assertTrue(speech.running());
        assertFalse(speech.start(Mic::new, () -> new Decoder(), new Result()));
        loaded.countDown(); result.await();
        assertNull(result.error); assertEquals("测试完成", result.text);
        assertEquals(-1f, decoder.first, 0f); assertEquals(32767f / 32768f, decoder.second, 0f);
        assertEquals(1, decoder.finishes); assertEquals(1, decoder.closes); assertFalse(speech.running());
    }

    @Test public void cancelDuringModelLoadReleasesMicAndDiscardsQueuedAudio() throws Exception {
        EmbeddedSpeech speech = new EmbeddedSpeech(new VoiceCapture(4, 100));
        Decoder decoder = new Decoder(); Mic mic = new Mic(); Result result = new Result();
        CountDownLatch loaded = new CountDownLatch(1);
        speech.start(() -> mic, () -> {
            if (!loaded.await(2, TimeUnit.SECONDS)) throw new IllegalStateException("model load timed out");
            return decoder;
        }, result);
        assertTrue(mic.samples.await(2, TimeUnit.SECONDS));
        speech.stop(true); assertTrue(mic.released.await(2, TimeUnit.SECONDS));
        loaded.countDown(); result.await();
        assertTrue(result.cancelled); assertNull(result.text); assertEquals(1, decoder.samples.getCount());
        assertEquals(0, decoder.finishes); assertEquals(1, decoder.closes); assertFalse(speech.running());
    }

    @Test public void slowDecodeNeverBlocksCaptureOrLosesTheNextAudioChunk() throws Exception {
        EmbeddedSpeech speech = new EmbeddedSpeech(new VoiceCapture(8, 8));
        CountDownLatch decoding = new CountDownLatch(1), resume = new CountDownLatch(1);
        AtomicInteger decoded = new AtomicInteger(); Result result = new Result();
        Decoder decoder = new Decoder() {
            @Override public void samples(float[] pcm) {
                decoded.incrementAndGet(); decoding.countDown();
                try { if (!resume.await(2, TimeUnit.SECONDS)) throw new IllegalStateException("decode timed out"); }
                catch (InterruptedException failure) { throw new IllegalStateException(failure); }
            }
        };
        VoiceCapture.Source mic = new VoiceCapture.Source() {
            @Override public void start() { }
            @Override public int read(byte[] buffer) { return 4; }
            @Override public void stop() { }
            @Override public void release() { }
        };
        speech.start(() -> mic, () -> decoder, result);
        assertTrue(decoding.await(2, TimeUnit.SECONDS));
        assertTrue(result.limit.await(2, TimeUnit.SECONDS));
        speech.stop(false); resume.countDown(); result.await();
        assertEquals(2, decoded.get()); assertNull(result.error); assertEquals("测试完成", result.text);
    }

    @Test public void cancelWhileFinalizingDoesNotBlockTheCallingThreadOrSendText() throws Exception {
        EmbeddedSpeech speech = new EmbeddedSpeech(new VoiceCapture(4, 100));
        CountDownLatch finishing = new CountDownLatch(1), finish = new CountDownLatch(1);
        Decoder decoder = new Decoder() {
            @Override public String finish() {
                finishing.countDown();
                try { if (!finish.await(2, TimeUnit.SECONDS)) throw new IllegalStateException("finish timed out"); }
                catch (InterruptedException failure) { throw new IllegalStateException(failure); }
                return "测试完成";
            }
        };
        Result result = new Result(); speech.start(Mic::new, () -> decoder, result);
        assertTrue(decoder.samples.await(2, TimeUnit.SECONDS)); speech.stop(false);
        assertTrue(finishing.await(2, TimeUnit.SECONDS));
        CountDownLatch cancelled = new CountDownLatch(1);
        new Thread(() -> { speech.stop(true); cancelled.countDown(); }).start();
        assertTrue(cancelled.await(1, TimeUnit.SECONDS));
        finish.countDown(); result.await(); assertTrue(result.cancelled); assertNull(result.text);
        assertEquals(1, decoder.closes);
    }
}
