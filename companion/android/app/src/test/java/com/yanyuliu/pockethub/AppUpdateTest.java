package com.yanyuliu.pockethub;

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;
import static org.junit.Assert.assertEquals;

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
    @Test public void choosesSupportedNativeAbiAndPinsTheSelectedPackage() {
        assertEquals("arm64-v8a", AppUpdate.preferredAbi(new String[]{"arm64-v8a", "armeabi-v7a"}));
        assertEquals("x86_64", AppUpdate.preferredAbi(new String[]{"x86_64", "x86"}));
        assertEquals("universal", AppUpdate.preferredAbi(new String[]{null, "unknown"}));
        Map<String, String> fields = new HashMap<>();
        fields.put("abi", "arm64-v8a"); fields.put("sha256", SHA);
        assertEquals("/v1/app/apk?abi=arm64-v8a&sha256=" + SHA,
                AppUpdate.packagePath(new AppUpdate.Release(fields)));
    }
    @Test public void additiveVariantMetadataKeepsTheLegacyFlatParserWorking() {
        Map<String, String> fields = BuddyProtocol.parseFlatObject("{\"available\":true,\"version_code\":24,"
                + "\"abi\":\"arm64-v8a\",\"size\":100,\"sha256\":\"" + SHA + "\","
                + "\"variants\":{\"x86\":{\"size\":200,\"sha256\":\"" + OTHER + "\"}}}");
        AppUpdate.Release selected = new AppUpdate.Release(fields);
        assertEquals(24, selected.code); assertEquals(100, selected.size); assertEquals(SHA, selected.sha256);
    }
    @Test public void additiveHostedUrlKeepsVersionHashAbiAndLegacyDefaults() {
        Map<String, String> fields = BuddyProtocol.parseFlatObject("{\"available\":true,\"version_code\":25,"
                + "\"abi\":\"arm64-v8a\",\"size\":200000000,\"sha256\":\"" + SHA + "\","
                + "\"download_url\":\"https://github.com/owner/app/releases/download/v25/arm.apk\"}");
        AppUpdate.Release hosted = new AppUpdate.Release(fields);
        assertEquals(25, hosted.code); assertEquals(200000000L, hosted.size);
        assertEquals(SHA, hosted.sha256); assertEquals("arm64-v8a", hosted.abi);
        assertEquals("https://github.com/owner/app/releases/download/v25/arm.apk", hosted.downloadUrl);
        assertEquals("", release(24, SHA, 1).downloadUrl);
    }
    @Test public void cachedDownloadMustStillMatchReleaseHashAndSize() throws Exception {
        java.io.File file = java.io.File.createTempFile("app-update-test", ".apk");
        try {
            java.nio.file.Files.write(file.toPath(), "apk".getBytes(java.nio.charset.StandardCharsets.UTF_8));
            byte[] hash = java.security.MessageDigest.getInstance("SHA-256").digest(
                    java.nio.file.Files.readAllBytes(file.toPath()));
            StringBuilder hex = new StringBuilder();
            for (byte value : hash) hex.append(String.format(java.util.Locale.ROOT, "%02x", value & 255));
            assertTrue(AppUpdate.verified(file, release(20, hex.toString(), 1)));
            java.nio.file.Files.write(file.toPath(), new byte[]{1});
            assertFalse(AppUpdate.verified(file, release(20, hex.toString(), 1)));
        } finally { file.delete(); }
    }
    @Test public void relayJsonRoundTripMustNotHideAnAvailableUpdate() {
        String reply="{\"available\":true,\"version_code\":72,\"version_name\":\"0.33.3\",\"size\":63680000,\"sha256\":\""+SHA+"\",\"published_at\":1793090735.487}";
        String relayed=RelayCrypto.json(Json.parseObject(reply));
        AppUpdate.Release selected=new AppUpdate.Release(Presentation.snapshot(relayed));
        assertEquals(72,selected.code);assertEquals(63680000L,selected.size);
        assertTrue(AppUpdate.newer(selected,71,OTHER,1793000000000L));
    }
    @Test public void fractionalAndOverflowingMetadataIsNotRoundedIntoARelease() {
        for(String code:new String[]{"72.5","9223372036854775808","NaN","Infinity"}) {
            Map<String,String> fields=new HashMap<>();fields.put("version_code",code);fields.put("sha256",SHA);
            assertFalse(AppUpdate.newer(new AppUpdate.Release(fields),71,OTHER,1));
        }
    }
}
