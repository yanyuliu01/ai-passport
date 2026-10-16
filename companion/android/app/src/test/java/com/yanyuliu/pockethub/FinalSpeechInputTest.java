package com.yanyuliu.pockethub;

import static org.junit.Assert.*;
import org.junit.Test;

public class FinalSpeechInputTest {
    @Test public void multipleCaptureChunksReachOfflineRecognizerOnlyOnce() {
        FinalSpeechInput input = new FinalSpeechInput(8);
        float[] reusable = {0.25f, -0.5f};
        input.samples(reusable);
        reusable[0] = 0.75f;
        input.samples(reusable);
        final int[] accepts = {0}, decodes = {0};
        FinalSpeechInput.Recognizer recognizer = new FinalSpeechInput.Recognizer() {
            @Override public void samples(float[] pcm) {
                if (++accepts[0] > 1) throw new AssertionError("Native offline input is one-shot");
                assertArrayEquals(new float[] {0.25f, -0.5f, 0.75f, -0.5f}, pcm, 0f);
            }
            @Override public String decode() {
                assertEquals(1, accepts[0]);
                decodes[0]++;
                return "accurate final text";
            }
        };
        assertEquals(0, accepts[0]);
        assertEquals("accurate final text", input.finish(recognizer));
        assertThrows(IllegalStateException.class, () -> input.finish(recognizer));
        assertThrows(IllegalStateException.class, () -> input.samples(new float[] {1f}));
        assertEquals(1, accepts[0]);
        assertEquals(1, decodes[0]);
    }

    @Test public void excessInputIsRejectedWithoutChangingAcceptedSamples() {
        FinalSpeechInput input = new FinalSpeechInput(2);
        input.samples(new float[] {1f});
        assertThrows(IllegalArgumentException.class, () -> input.samples(new float[] {2f, 3f}));
        input.samples(new float[] {-1f});
        input.finish(new FinalSpeechInput.Recognizer() {
            @Override public void samples(float[] pcm) { assertArrayEquals(new float[] {1f, -1f}, pcm, 0f); }
            @Override public String decode() { return "final"; }
        });
    }
}
