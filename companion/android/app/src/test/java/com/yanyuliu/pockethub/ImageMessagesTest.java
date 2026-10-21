package com.yanyuliu.pockethub;
import org.junit.Test;
import static org.junit.Assert.*;
public class ImageMessagesTest {
    private Attachment image() {
        return Attachment.from(Json.parseObject("{\"id\":\"a11111111111111111111111111111111\",\"name\":\"owner.jpg\",\"kind\":\"image\",\"mime\":\"image/jpeg\",\"status\":\"ready\",\"sha256\":\"abc\",\"size\":2048,\"width\":120,\"height\":80,\"thumbnail\":true}"));
    }
    @Test public void attachmentsSurviveLocalAcceptanceAndRestore() {
        ChatHistory history = new ChatHistory();
        history.begin("client",null,"look",10); history.image("client",image()); history.accepted("client","server");
        ChatHistory restored = ChatHistory.restore(history.json());
        ChatHistory.Message message = restored.main().get(0);
        assertEquals("server:you",message.id); assertEquals(1,message.attachments.size());
        assertEquals("owner.jpg",message.attachments.get(0).name);
        assertEquals(2048,message.attachments.get(0).size); assertTrue(message.attachments.get(0).thumbnail);
    }
    @Test public void cardImagesRemainBoundToTheOwnerMessage() {
        ChatHistory history = new ChatHistory();
        Card card = Card.from(Json.parseObject("{\"id\":\"c1\",\"created_at\":10,\"state\":\"working\",\"agent\":\"codex\",\"entries\":[{\"id\":\"m1:you\",\"role\":\"you\",\"scope\":\"main\",\"text\":\"look\",\"attachments\":["+image().json()+"]},{\"id\":\"m1:reply\",\"role\":\"xiaoyou\",\"scope\":\"main\",\"text\":\"working\"}]}"));
        history.ingest(card);
        assertEquals(1,history.main().get(0).attachments.size());
        assertTrue(history.main().get(1).attachments.isEmpty());
        assertEquals(image().id,ChatHistory.restore(history.json()).main().get(0).attachments.get(0).id);
    }
    @Test public void pendingImagesArriveBeforeTheCardAndKeepTheirIdentity() {
        ChatHistory history = new ChatHistory();
        SyncCache.Pending pending = new SyncCache.Pending("m1:you","","main","you","look","final",10);
        pending.attachments = java.util.Collections.singletonList(image());
        assertTrue(history.pending(pending)); assertFalse(history.pending(pending));
        assertEquals(image().id,history.main().get(0).attachments.get(0).id);
    }
    @Test public void previewColorsUseFirmwareRgb565LittleEndian() {
        int[] colors = new int[64 * 64]; colors[0] = 0xffff0000; colors[1] = 0xff00ff00; colors[2] = 0xff0000ff;
        byte[] bytes = ImagePreview.rgb565(colors); assertEquals(8192, bytes.length);
        assertEquals(0, bytes[0] & 255); assertEquals(248, bytes[1] & 255);
        assertEquals(224, bytes[2] & 255); assertEquals(7, bytes[3] & 255);
        assertEquals(31, bytes[4] & 255); assertEquals(0, bytes[5] & 255);
    }
    @Test(expected=IllegalArgumentException.class) public void invalidPreviewDimensionsAreRejected() {
        ImagePreview.rgb565(new int[1]);
    }
}
