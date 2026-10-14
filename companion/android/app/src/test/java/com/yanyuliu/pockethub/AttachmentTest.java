package com.yanyuliu.pockethub;

import org.junit.Test;
import static org.junit.Assert.*;

public class AttachmentTest {
    @Test public void entryAndCardArtifactsDeduplicateWithoutLosingEntryAssociation() {
        String item = "{\"id\":\"a0123456789abcdef0123456789abcdef\",\"name\":\"完整方案.md\",\"mime\":\"text/markdown\",\"kind\":\"file\",\"status\":\"ready\",\"size\":12345}";
        Card card = Card.from(Json.parseObject("{\"id\":\"c1\",\"attachments\":[" + item + "],\"entries\":[{\"role\":\"xiaoyou\",\"text\":\"完整稿见附件\",\"attachments\":[" + item + "]}]}"));
        assertEquals(1, card.attachments.size()); assertEquals(1, card.entries.get(0).attachments.size());
        assertEquals("完整方案.md", card.attachments.get(0).label());
        assertEquals(1, card.unknown().attachments.size());
    }

    @Test public void imageAndUnavailableArtifactsStayExplicitAndUnsafeIdsAreRejected() {
        Attachment image = Attachment.from(Json.parseObject("{\"id\":\"a0123456789abcdef0123456789abcdef\",\"mime\":\"image/png\",\"kind\":\"image\",\"thumbnail\":true,\"status\":\"ready\"}"));
        assertTrue(image.image()); assertTrue(image.ready()); assertTrue(image.thumbnail);
        Attachment missing = Attachment.from(Json.parseObject("{\"id\":\"a1123456789abcdef0123456789abcdef\",\"name\":\"lost.pdf\",\"status\":\"error\",\"error\":\"源文件已丢失\"}"));
        assertFalse(missing.ready()); assertEquals("源文件已丢失", missing.error);
        assertNull(Attachment.from(Json.parseObject("{\"id\":\"../../private\"}")));
    }
}
