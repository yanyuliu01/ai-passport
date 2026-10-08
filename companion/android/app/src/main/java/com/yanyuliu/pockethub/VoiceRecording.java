package com.yanyuliu.pockethub;

import java.io.ByteArrayOutputStream;

/**
 * 把设备发来的语音帧还原成一段录音。纯 Java，不依赖安卓。
 *
 * 一条蓝牙通知就是一帧：
 *   [0xFF][seq][predictor 低][predictor 高][step index][IMA ADPCM 数据…]
 * 每帧自带解码起点，所以丢掉一帧只影响这一帧；丢的那一段用静音补上，录音长度不变。
 * ADPCM 每字节两个采样，低 4 位在前；16 kHz 单声道。
 */
final class VoiceRecording {
    static final int MAGIC = 0xFF;
    static final int HEADER_BYTES = 5;
    static final int SAMPLE_RATE = 16000;
    /** 设备最长录 30 秒；多留一倍余量，超出的丢弃。 */
    static final int MAX_PCM_BYTES = 60 * SAMPLE_RATE * 2;

    private static final int[] STEP = {
        7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 19, 21, 23, 25, 28, 31, 34, 37, 41, 45, 50, 55, 60,
        66, 73, 80, 88, 97, 107, 118, 130, 143, 157, 173, 190, 209, 230, 253, 279, 307, 337, 371,
        408, 449, 494, 544, 598, 658, 724, 796, 876, 963, 1060, 1166, 1282, 1411, 1552, 1707,
        1878, 2066, 2272, 2499, 2749, 3024, 3327, 3660, 4026, 4428, 4871, 5358, 5894, 6484, 7132,
        7845, 8630, 9493, 10442, 11487, 12635, 13899, 15289, 16818, 18500, 20350, 22385, 24623,
        27086, 29794, 32767,
    };
    private static final int[] INDEX = {-1, -1, -1, -1, 2, 4, 6, 8, -1, -1, -1, -1, 2, 4, 6, 8};

    private final ByteArrayOutputStream pcm = new ByteArrayOutputStream();
    private int nextSeq = -1;
    private int frames;
    private int lost;
    private int predictor;
    private int index;

    /** 这条通知是不是语音帧（否则是文本）。0xFF 不会出现在 UTF-8 文本里。 */
    static boolean isFrame(byte[] value) {
        return value != null && value.length > 0 && (value[0] & 0xFF) == MAGIC;
    }

    /** 加入一帧；格式不对返回 false。 */
    boolean add(byte[] frame) {
        if (!isFrame(frame) || frame.length <= HEADER_BYTES) {
            return false;
        }
        int seq = frame[1] & 0xFF;
        int data = frame.length - HEADER_BYTES;
        if (nextSeq >= 0 && seq != nextSeq) {
            // 中间丢了几帧：除最后一帧外每帧一样长，按这一帧的长度补静音。
            int missing = (seq - nextSeq) & 0xFF;
            lost += missing;
            silence(missing * data * 4);
        }
        nextSeq = (seq + 1) & 0xFF;
        ++frames;
        predictor = (short) ((frame[2] & 0xFF) | (frame[3] << 8));
        index = Math.min(88, frame[4] & 0xFF);
        for (int offset = HEADER_BYTES; offset < frame.length; ++offset) {
            int value = frame[offset] & 0xFF;
            sample(value & 15);
            sample(value >> 4);
        }
        return true;
    }

    private void sample(int code) {
        int step = STEP[index];
        int diff = step >> 3;
        if ((code & 4) != 0) {
            diff += step;
        }
        if ((code & 2) != 0) {
            diff += step >> 1;
        }
        if ((code & 1) != 0) {
            diff += step >> 2;
        }
        predictor += (code & 8) != 0 ? -diff : diff;
        predictor = Math.max(-32768, Math.min(32767, predictor));
        index = Math.max(0, Math.min(88, index + INDEX[code]));
        if (pcm.size() + 2 <= MAX_PCM_BYTES) {
            pcm.write(predictor & 0xFF);
            pcm.write((predictor >> 8) & 0xFF);
        }
    }

    private void silence(int bytes) {
        for (int count = 0; count < bytes && pcm.size() < MAX_PCM_BYTES; ++count) {
            pcm.write(0);
        }
    }

    int frames() {
        return frames;
    }

    int lostFrames() {
        return lost;
    }

    int millis() {
        return (int) (pcm.size() / 2 * 1000L / SAMPLE_RATE);
    }

    /** 16 位单声道 WAV 文件的全部字节。 */
    byte[] toWav() {
        byte[] data = pcm.toByteArray();
        ByteArrayOutputStream out = new ByteArrayOutputStream(data.length + 44);
        ascii(out, "RIFF");
        int32(out, 36 + data.length);
        ascii(out, "WAVE");
        ascii(out, "fmt ");
        int32(out, 16);
        int16(out, 1);            // PCM
        int16(out, 1);            // 单声道
        int32(out, SAMPLE_RATE);
        int32(out, SAMPLE_RATE * 2);
        int16(out, 2);
        int16(out, 16);
        ascii(out, "data");
        int32(out, data.length);
        out.write(data, 0, data.length);
        return out.toByteArray();
    }

    private static void ascii(ByteArrayOutputStream out, String text) {
        for (int position = 0; position < text.length(); ++position) {
            out.write(text.charAt(position));
        }
    }

    private static void int16(ByteArrayOutputStream out, int value) {
        out.write(value & 0xFF);
        out.write((value >> 8) & 0xFF);
    }

    private static void int32(ByteArrayOutputStream out, int value) {
        int16(out, value & 0xFFFF);
        int16(out, (value >> 16) & 0xFFFF);
    }
}
