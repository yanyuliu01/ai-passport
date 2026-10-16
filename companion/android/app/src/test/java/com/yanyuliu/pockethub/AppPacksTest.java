package com.yanyuliu.pockethub;

import org.junit.Test;
import static org.junit.Assert.*;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

public class AppPacksTest {
    private static final String MODEL = "a".repeat(64), TOKENS = "b".repeat(64), NEW_TOKENS = "c".repeat(64);

    private static List<AppPacks.Pack> packs(String tokens, String url) {
        return AppPacks.parse(Json.parseObject("{\"packs\":[{\"name\":\"speech\",\"files\":["
                + "{\"path\":\"sense-voice/model.int8.onnx\",\"sha256\":\"" + MODEL + "\",\"size\":239233841,\"download_url\":\"" + url + "\"},"
                + "{\"path\":\"sense-voice/tokens.txt\",\"sha256\":\"" + tokens + "\",\"size\":315,\"download_url\":\"" + url + "\"},"
                + "{\"path\":\"../escape\",\"sha256\":\"" + MODEL + "\",\"size\":1},"
                + "{\"path\":\"zh/bad\",\"sha256\":\"short\",\"size\":1}]},"
                + "{\"name\":\"Bad Name\",\"files\":[]}]}"));
    }

    @Test public void onlyChangedFilesAreDownloaded() {
        List<AppPacks.Pack> listed = packs(TOKENS, "");
        assertEquals(1, listed.size());
        AppPacks.Pack speech = AppPacks.find(listed, AppPacks.SPEECH);
        assertEquals(2, speech.files.size());  // unsafe path and bad checksum are dropped
        Map<String, String> installed = new HashMap<>();
        assertEquals(2, AppPacks.missing(speech, installed).size());
        assertEquals(239233841L + 315, AppPacks.bytes(AppPacks.missing(speech, installed)));
        installed.put("sense-voice/model.int8.onnx", MODEL);
        installed.put("sense-voice/tokens.txt", TOKENS);
        assertTrue(AppPacks.missing(speech, installed).isEmpty());
        // A new pack that only changes the tokens: the 228 MB model is not downloaded again.
        List<AppPacks.Item> next = AppPacks.missing(AppPacks.find(packs(NEW_TOKENS, ""), "speech"), installed);
        assertEquals(1, next.size());
        assertEquals("sense-voice/tokens.txt", next.get(0).path);
        assertEquals(315, AppPacks.bytes(next));
    }

    @Test public void internetDownloadOnlyWhenEveryFileHasAMirror() {
        assertFalse(AppPacks.find(packs(TOKENS, ""), "speech").hosted());
        assertTrue(AppPacks.find(packs(TOKENS, "https://github.com/o/r/releases/download/t/x"), "speech").hosted());
        assertFalse(new AppPacks.Pack("empty", new java.util.ArrayList<>()).hosted());
    }

    @Test public void pathsStayInsideThePackFolder() {
        assertTrue(AppPacks.safePath("zh/model.int8.onnx"));
        for (String bad : new String[] {"../x", "/etc/x", "a/../b", ".index.json", "a//b", "a b", ""})
            assertFalse(bad, AppPacks.safePath(bad));
    }
}
