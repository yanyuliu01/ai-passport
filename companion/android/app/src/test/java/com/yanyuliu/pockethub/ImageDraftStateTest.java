package com.yanyuliu.pockethub;

import org.junit.Test;
import static org.junit.Assert.*;

public class ImageDraftStateTest {
    @Test public void removedUploadCannotRestoreAnAttachment() {
        ImageDraftState draft = new ImageDraftState();
        long upload = draft.begin();
        assertTrue(draft.uploading());
        draft.clear();
        assertFalse(draft.accepts(upload));
        assertFalse(draft.uploading());
    }

    @Test public void oldProgressAndCompletionCannotChangeReplacementOrRetry() {
        ImageDraftState draft = new ImageDraftState();
        long first = draft.begin();
        draft.finish(first); // Failure keeps the local selection available for retry.
        assertFalse(draft.uploading());
        long retry = draft.begin();
        assertFalse(draft.accepts(first));
        draft.finish(first);
        assertTrue(draft.uploading());
        assertTrue(draft.accepts(retry));
        draft.finish(retry);
        assertFalse(draft.uploading());
        draft.clear(); // Source switch/activity destruction invalidates every callback.
        assertFalse(draft.accepts(retry));
    }
}
