package com.yanyuliu.pockethub;

import org.junit.Test;
import static org.junit.Assert.*;

/** A fresh app may poll before discovering sync_requests_v1; relay JSON must remain valid there. */
public class MessageRevisionTest {
    @Test public void relayRoundTripKeepsIntegerPollingRevision() {
        String wire = RelayCrypto.json(Json.parseObject("{\"id\":\"request-a\",\"rev\":1,\"status\":\"running\"}"));
        assertEquals(1, Long.parseLong(Presentation.snapshot(wire).get("rev")));
        assertEquals("running", Presentation.snapshot(wire).get("status"));
    }

    @Test public void exactLargeAndExponentRevisionsDoNotRoundOrTruncate() {
        assertEquals("9007199254740993", Presentation.snapshot("{\"rev\":9007199254740993}").get("rev"));
        assertEquals("1000", Presentation.snapshot("{\"rev\":1e3}").get("rev"));
        assertEquals("1.5", Presentation.snapshot("{\"rev\":1.5}").get("rev"));
        assertEquals("9223372036854775808", Presentation.snapshot("{\"rev\":9223372036854775808}").get("rev"));
        assertFalse(Presentation.snapshot("{\"status\":\"done\"}").containsKey("rev"));
    }
}
