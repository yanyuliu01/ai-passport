package com.yanyuliu.pockethub;

import java.io.ByteArrayOutputStream;

/** Hold-to-record lifecycle. Hardware operations and draining never block the UI thread. */
final class VoiceCapture {
    interface Source {
        void start() throws Exception;
        int read(byte[] buffer) throws Exception;
        void stop();
        void release();
    }
    interface Factory { Source create() throws Exception; }
    interface Callback {
        void ready();
        void level(float value);
        default void samples(byte[] pcm, int count) { }
        default void limitReached() { }
        void finished(byte[] wav, String error, boolean cancelled);
    }

    private volatile boolean running, stopping, cancelled;
    private final int minimumBytes, maximumBytes;
    // Keep brief words: 100 ms rejects accidental empty taps without a half-second floor.
    VoiceCapture() { this(VoiceRecording.SAMPLE_RATE * 2 / 10, 30 * VoiceRecording.SAMPLE_RATE * 2); }
    VoiceCapture(int minimumBytes, int maximumBytes) {
        this.minimumBytes = minimumBytes; this.maximumBytes = maximumBytes;
    }

    synchronized boolean start(Factory factory, Callback callback) {
        if (running) return false;
        running = true; stopping = false; cancelled = false;
        new Thread(() -> record(factory, callback), "phone-mic").start();
        return true;
    }

    synchronized void stop(boolean cancel) { cancelled |= cancel; stopping = true; notifyAll(); }
    boolean running() { return running; }

    private void record(Factory factory, Callback callback) {
        Source source = null;
        ByteArrayOutputStream pcm = new ByteArrayOutputStream();
        String error = null;
        boolean didCancel;
        try {
            source = factory.create();
            if (!stopping) {
                source.start(); callback.ready();
                byte[] buffer = new byte[2048];
                long noDataSince = System.nanoTime();
                while (!stopping && pcm.size() < maximumBytes) {
                    int read = source.read(buffer);
                    if (read < 0) throw new IllegalStateException("录音读取失败（" + read + "）");
                    if (read == 0) {
                        if (System.nanoTime() - noDataSince > 3_000_000_000L)
                            throw new IllegalStateException("麦克风没有返回声音，请检查系统麦克风开关或是否被其他应用占用");
                        Thread.sleep(10); continue;
                    }
                    noDataSince = System.nanoTime();
                    int count = Math.min(read, maximumBytes - pcm.size());
                    pcm.write(buffer, 0, count);
                    callback.samples(buffer, count);
                    int peak = 0;
                    for (int i = 0; i + 1 < count; i += 2)
                        peak = Math.max(peak, Math.abs((short) ((buffer[i] & 0xff) | (buffer[i + 1] << 8))));
                    callback.level(Math.min(1f, peak / 12000f));
                }
            }
        } catch (SecurityException denied) {
            error = "麦克风权限未允许，请在应用权限中开启麦克风";
        } catch (Exception failure) {
            error = failure.getMessage();
            if (error == null || error.isEmpty()) error = "麦克风启动失败，请检查系统麦克风开关或其他应用的占用";
            if (failure instanceof InterruptedException) Thread.currentThread().interrupt();
        } finally {
            if (source != null) {
                try { source.stop(); } catch (RuntimeException ignored) { }
                try { source.release(); } catch (RuntimeException ignored) { }
            }
        }
        // The microphone is released at the duration limit, but sending still requires release.
        if (error == null && pcm.size() >= maximumBytes && !stopping) callback.limitReached();
        synchronized (this) {
            while (error == null && pcm.size() >= maximumBytes && !stopping) {
                try { wait(); }
                catch (InterruptedException interrupted) {
                    cancelled = true; stopping = true; Thread.currentThread().interrupt();
                }
            }
            didCancel = cancelled; running = false;
        }
        byte[] data = pcm.toByteArray();
        if (!didCancel && error == null && data.length < minimumBytes)
            error = "录音太短或没有采到声音，请按住说话后重试";
        callback.finished(!didCancel && error == null ? VoiceRecording.wav(data) : null, error, didCancel);
    }
}
