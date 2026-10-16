package com.yanyuliu.pockethub;

import java.util.ArrayDeque;
import java.util.Arrays;

/** Capture starts independently of model loading; only released final text leaves here. */
final class EmbeddedSpeech {
    interface Decoder extends AutoCloseable {
        void samples(float[] pcm);
        String partial();
        String finish();
        void close();
    }
    interface Factory { Decoder create() throws Exception; }
    interface Callback {
        void ready();
        void level(float level);
        void partial(String text);
        void limitReached();
        void finished(String text, String error, boolean cancelled);
        /** The whole utterance as 16-bit mono WAV, before finished; not called when capture failed. */
        default void recording(byte[] wav) { }
    }

    private final VoiceCapture capture;
    private Session active;
    EmbeddedSpeech() { this(new VoiceCapture()); }
    EmbeddedSpeech(VoiceCapture capture) { this.capture = capture; }
    synchronized boolean running() { return active != null; }
    synchronized void stop(boolean cancelled) {
        if (active != null) {
            active.stop(cancelled);
            capture.stop(cancelled);
        }
    }

    synchronized boolean start(VoiceCapture.Factory microphone, Factory factory, Callback callback) {
        if (active != null) return false;
        Session session = new Session(factory, callback);
        active = session;
        if (!capture.start(microphone, session)) {
            active = null;
            return false;
        }
        new Thread(session, "phone-asr").start();
        return true;
    }

    private final class Session implements VoiceCapture.Callback, Runnable {
        private final Factory factory;
        private final Callback callback;
        // VoiceCapture bounds the total queued PCM to the same 30-second recording limit.
        private final ArrayDeque<byte[]> pending = new ArrayDeque<>();
        private boolean cancelled, captureDone, ending;
        private String captureError;
        private byte[] captured;

        Session(Factory factory, Callback callback) { this.factory = factory; this.callback = callback; }

        synchronized void stop(boolean cancel) {
            cancelled |= cancel;
            if (cancelled) pending.clear();
            notifyAll();
        }
        private synchronized boolean canReport() { return !cancelled && !ending; }
        @Override public void ready() { if (canReport()) callback.ready(); }
        @Override public void level(float value) { if (canReport()) callback.level(value); }
        @Override public synchronized void samples(byte[] bytes, int count) {
            if (!cancelled && !ending) {
                // AudioRecord reuses its buffer. Keep the bytes until the model can consume them.
                pending.addLast(Arrays.copyOf(bytes, count));
                notifyAll();
            }
        }
        @Override public void limitReached() { if (canReport()) callback.limitReached(); }
        @Override public synchronized void finished(byte[] wav, String error, boolean didCancel) {
            captureError = error; captureDone = true; cancelled |= didCancel; captured = wav;
            if (cancelled) pending.clear();
            notifyAll();
        }

        @Override public void run() {
            Decoder decoder = null;
            String text = null, error = null;
            boolean interrupted = false;
            try {
                decoder = factory.create();
                if (decoder == null) throw new IllegalStateException("手机语音模型加载失败，请重试");
                long lastPartial = 0;
                String shown = "";
                while (true) {
                    byte[] bytes;
                    synchronized (this) {
                        while (!cancelled && !captureDone && pending.isEmpty()) wait();
                        if (cancelled) break;
                        if (pending.isEmpty()) {
                            error = captureError;
                            break;
                        }
                        bytes = pending.removeFirst();
                    }
                    float[] samples = new float[bytes.length / 2];
                    for (int i = 0; i < samples.length; i++)
                        samples[i] = (short) ((bytes[2 * i] & 255) | (bytes[2 * i + 1] << 8)) / 32768f;
                    decoder.samples(samples);
                    long now = System.nanoTime();
                    if (now - lastPartial >= 150_000_000L) {
                        String partial = decoder.partial();
                        if (canReport() && !partial.equals(shown)) callback.partial(partial);
                        shown = partial; lastPartial = now;
                    }
                }
                if (canReport() && error == null) {
                    // Release may precede model readiness, but all captured bytes still decode.
                    text = decoder.finish().trim();
                    if (text.isEmpty()) error = "没有识别到文字，请按住说话后重试";
                }
            } catch (Exception | LinkageError failure) {
                error = failure.getMessage();
                if (error == null || error.isEmpty()) error = "手机离线识别失败，请重试";
                if (failure instanceof InterruptedException) interrupted = true;
            } finally {
                synchronized (this) { ending = true; pending.clear(); }
                // Loading/decoding failures must release capture before another session can start.
                capture.stop(false);
                synchronized (this) {
                    while (!captureDone) {
                        try { wait(); }
                        catch (InterruptedException failure) { interrupted = true; cancelled = true; }
                    }
                }
                if (decoder != null) {
                    try { decoder.close(); }
                    catch (RuntimeException | LinkageError ignored) { }
                }
                synchronized (EmbeddedSpeech.this) { if (active == this) active = null; }
                boolean didCancel;
                byte[] wav;
                synchronized (this) { didCancel = cancelled; wav = captured; captured = null; }
                if (interrupted) Thread.currentThread().interrupt();
                if (!didCancel && wav != null) callback.recording(wav);
                callback.finished(didCancel ? null : text, error, didCancel);
            }
        }
    }
}
