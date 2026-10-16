package com.yanyuliu.pockethub;

import java.util.Arrays;

/** Offline recognizers receive one complete waveform, regardless of capture chunking. */
final class FinalSpeechInput {
    interface Recognizer {
        void samples(float[] pcm);
        String decode();
    }

    private float[] pcm;
    private int size;
    private boolean finished;

    FinalSpeechInput(int maximumSamples) {
        if (maximumSamples <= 0) throw new IllegalArgumentException("A recording limit is required");
        pcm = new float[maximumSamples];
    }

    void samples(float[] samples) {
        if (finished) throw new IllegalStateException("Final speech input has already finished");
        if (samples.length > pcm.length - size) throw new IllegalArgumentException("Recording exceeds limit");
        System.arraycopy(samples, 0, pcm, size, samples.length);
        size += samples.length;
    }

    String finish(Recognizer recognizer) {
        if (finished) throw new IllegalStateException("Final speech input has already finished");
        if (size == 0) throw new IllegalStateException("Final speech input is empty");
        finished = true;
        float[] utterance = size == pcm.length ? pcm : Arrays.copyOf(pcm, size);
        pcm = null;
        recognizer.samples(utterance);
        return recognizer.decode();
    }
}
