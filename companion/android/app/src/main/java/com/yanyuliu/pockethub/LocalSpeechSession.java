package com.yanyuliu.pockethub;

/** A recognition result is never sent before release, or after cancellation. */
final class LocalSpeechSession {
    private boolean released, finished, cancelled, delivered;
    private String text;

    void result(String value) { if (!cancelled && !finished) { text = value; finished = true; } }
    void release() { released = true; }
    void cancel() { cancelled = true; }
    boolean finished() { return finished; }
    String take() {
        if (cancelled || delivered || !released || !finished || text == null || text.trim().isEmpty()) return null;
        delivered = true;
        return text.trim();
    }
}
