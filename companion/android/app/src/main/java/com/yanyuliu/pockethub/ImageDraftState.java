package com.yanyuliu.pockethub;

/** A removed/replaced draft must never be resurrected by a late upload callback. */
final class ImageDraftState {
    private long generation;
    private boolean uploading;
    long begin() { uploading = true; return ++generation; }
    void clear() { ++generation; uploading = false; }
    boolean accepts(long selection) { return selection == generation; }
    void finish(long selection) { if (accepts(selection)) uploading = false; }
    boolean uploading() { return uploading; }
}
