package com.yanyuliu.pockethub;

import org.junit.Test;
import java.util.Map;
import static org.junit.Assert.*;

public class AcceptedReceiptTest {
    private final String runtime = "a".repeat(32), remote = "b".repeat(32), client = "c".repeat(32);
    private Map<String,Object> request(String conversation) {
        return RelayCrypto.map("method", "POST", "path", "/v1/messages", "runtime_id", runtime,
                "body", RelayCrypto.map("client_id", client, "conversation", conversation, "text", "private"));
    }
    private Map<String,Object> response(String conversation) {
        return RelayCrypto.map("id", remote, "client_id", client, "conversation", conversation, "reply", "private");
    }
    @Test public void receiptContainsOnlyBoundIdentityAndNoText() {
        Map<String,Object> receipt = AcceptedReceipt.from(request("side"), response("side"), runtime);
        assertNotNull(receipt); assertEquals(remote, receipt.get("message_id"));
        assertFalse(receipt.containsKey("text")); assertFalse(receipt.containsKey("reply"));
        assertNull(AcceptedReceipt.from(request("side"), response("other"), runtime));
        assertNull(AcceptedReceipt.from(request("side"), response("side"), "d".repeat(32)));
        Map<String,Object> forged = response("side"); forged.put("client_id", "d".repeat(32));
        assertNull(AcceptedReceipt.from(request("side"), forged, runtime));
    }
    @Test public void voiceAndSessionRequestsAreBoundToo() {
        Map<String,Object> voice = RelayCrypto.map("method", "POST", "runtime_id", runtime,
                "path", "/v1/voice?conversation=side&client_id=" + client);
        assertNotNull(AcceptedReceipt.from(voice, response("side"), runtime));
        Map<String,Object> session = request("side"); session.put("path", "/v1/sessions/card/continue");
        assertNotNull(AcceptedReceipt.from(session, response("side"), runtime));
        session.put("path", "/v1/other");
        assertNull(AcceptedReceipt.from(session, response("side"), runtime));
    }
    @Test public void onlyDefinitiveRejectionIsRecorded() {
        assertEquals("rejected", AcceptedReceipt.rejected(request("side"), RelayCrypto.map("status",400,"error","bad"),runtime).get("result"));
        assertNull(AcceptedReceipt.rejected(request("side"), RelayCrypto.map("status",409,"error","unknown"),runtime));
        assertNull(AcceptedReceipt.rejected(request("side"), RelayCrypto.map("status",504,"error","timeout"),runtime));
    }
}
