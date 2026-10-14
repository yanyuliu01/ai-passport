package com.yanyuliu.pockethub;

import android.media.AudioFormat;
import android.media.AudioRecord;
import android.media.MediaRecorder;

import java.io.ByteArrayOutputStream;

/**
 * 手机自己的「按住说话」：按下开始录，松开交出一段 16 kHz 单声道 WAV，和设备上按住 OK
 * 录的格式一样，交给 Runtime 识别。录音在自己的线程里，按钮回调不等它。
 */
final class PhoneMic {
    interface Level { void on(float level); }

    private AudioRecord recorder;
    private Thread worker;
    private volatile boolean running;
    private final ByteArrayOutputStream pcm = new ByteArrayOutputStream();

    /** 开始录；没有权限或麦克风被占用时返回 false。level 在录音线程里回调 0..1 的音量。 */
    boolean start(Level level) {
        if (running) return true;
        int min = AudioRecord.getMinBufferSize(VoiceRecording.SAMPLE_RATE,
                AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT);
        if (min <= 0) return false;
        try {
            recorder = new AudioRecord(MediaRecorder.AudioSource.VOICE_RECOGNITION, VoiceRecording.SAMPLE_RATE,
                    AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, min * 2);
        } catch (SecurityException | IllegalArgumentException refused) {
            return false;
        }
        if (recorder.getState() != AudioRecord.STATE_INITIALIZED) { recorder.release(); recorder = null; return false; }
        synchronized (pcm) { pcm.reset(); }
        running = true;
        recorder.startRecording();
        final AudioRecord source = recorder;
        worker = new Thread(() -> {
            byte[] buffer = new byte[min];
            while (running) {
                int read = source.read(buffer, 0, buffer.length);
                if (read <= 0) continue;
                synchronized (pcm) {
                    if (pcm.size() + read <= VoiceRecording.MAX_PCM_BYTES) pcm.write(buffer, 0, read);
                }
                int peak = 0;
                for (int i = 0; i + 1 < read; i += 2) peak = Math.max(peak, Math.abs((short) ((buffer[i] & 0xff) | (buffer[i + 1] << 8))));
                if (level != null) level.on(Math.min(1f, peak / 12000f));
            }
        }, "phone-mic");
        worker.start();
        return true;
    }

    /** 停下并交出录音；不到半秒的当作误触，返回 null。 */
    byte[] stop() {
        if (!running) return null;
        running = false;
        try { if (worker != null) worker.join(500); } catch (InterruptedException ignored) { Thread.currentThread().interrupt(); }
        try { recorder.stop(); } catch (IllegalStateException ignored) { }
        recorder.release(); recorder = null; worker = null;
        byte[] data;
        synchronized (pcm) { data = pcm.toByteArray(); }
        if (data.length < VoiceRecording.SAMPLE_RATE) return null;
        return VoiceRecording.wav(data);
    }

    boolean recording() { return running; }
}
