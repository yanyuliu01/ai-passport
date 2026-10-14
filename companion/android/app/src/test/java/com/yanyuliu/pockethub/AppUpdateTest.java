package com.yanyuliu.pockethub;

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import java.util.HashMap;
import java.util.Map;
import org.junit.Test;

public class AppUpdateTest {
    private static final String SHA = "a".repeat(64);
    private static final String OTHER = "b".repeat(64);

    private static AppUpdate.Release release(long code, String sha, double publishedAt) {
        Map<String, String> fields = new HashMap<>();
        fields.put("version_code", String.valueOf(code));
        fields.put("sha256", sha);
        fields.put("published_at", String.valueOf(publishedAt));
        return new AppUpdate.Release(fields);
    }

    @Test public void higherVersionCodeIsNewer() {
        assertTrue(AppUpdate.newer(release(18, SHA, 1), 17, SHA, 5_000));
        assertFalse(AppUpdate.newer(release(16, OTHER, 9_999), 17, SHA, 5_000));
    }

    @Test public void sameCodeNeedsADifferentLaterPackage() {
        assertTrue(AppUpdate.newer(release(17, OTHER, 6), 17, SHA, 5_000));
        assertFalse(AppUpdate.newer(release(17, OTHER, 4), 17, SHA, 5_000));
        assertFalse(AppUpdate.newer(release(17, SHA, 6), 17, SHA, 5_000));
    }

    @Test public void ignoresBrokenReleases() {
        assertFalse(AppUpdate.newer(release(18, "short", 6), 17, SHA, 5_000));
        assertFalse(AppUpdate.newer(release(0, OTHER, 6), 17, SHA, 5_000));
    }
}
