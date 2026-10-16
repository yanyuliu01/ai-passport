package com.yanyuliu.pockethub;

import android.media.AudioFormat;
import android.media.AudioRecord;
import android.media.MediaRecorder;

/** Native PCM capture; the phone's installed SpeechRecognizer is not a prerequisite. */
final class PhoneMic {
    private final VoiceCapture capture = new VoiceCapture();

    boolean start(VoiceCapture.Callback callback) { return capture.start(PhoneMic::source, callback); }
    void stop() { capture.stop(false); }
    void cancel() { capture.stop(true); }
    boolean recording() { return capture.running(); }

    static VoiceCapture.Source source() {
        int min = AudioRecord.getMinBufferSize(VoiceRecording.SAMPLE_RATE,
                AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT);
        if (min <= 0) throw new IllegalStateException("手机不支持当前录音格式");
        AudioRecord recorder;
        try {
            recorder = new AudioRecord(MediaRecorder.AudioSource.MIC, VoiceRecording.SAMPLE_RATE,
                    AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, Math.max(min * 2, 4096));
        } catch (SecurityException denied) {
            throw new IllegalStateException("麦克风权限未允许，请在应用权限中开启麦克风", denied);
        }
        if (recorder.getState() != AudioRecord.STATE_INITIALIZED) {
            recorder.release(); throw new IllegalStateException("麦克风初始化失败，请检查权限和其他应用的占用");
        }
        return new VoiceCapture.Source() {
            @Override public void start() {
                recorder.startRecording();
                if (recorder.getRecordingState() != AudioRecord.RECORDSTATE_RECORDING)
                    throw new IllegalStateException("麦克风没有开始录音");
            }
            @Override public int read(byte[] buffer) {
                return recorder.read(buffer, 0, buffer.length, AudioRecord.READ_NON_BLOCKING);
            }
            @Override public void stop() { recorder.stop(); }
            @Override public void release() { recorder.release(); }
        };
    }
}
