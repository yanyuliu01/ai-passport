package com.yanyuliu.pockethub;

import java.io.BufferedReader;
import java.io.StringReader;
import java.io.IOException;
import org.junit.Test;
import static org.junit.Assert.*;

public class MessageStreamTest {
    @Test public void supportsStandardMultilineData() throws Exception {
        assertEquals("m", MessageStream.readEvent(new BufferedReader(new StringReader(
                "data: {\"id\":\"m\",\ndata: \"status\":\"running\"}\n\n"))).get("id"));
    }
    private BufferedReader input(String text) { return new BufferedReader(new StringReader(text)); }

    @Test public void heartbeatAndMetadataDoNotInterruptMessages() throws Exception {
        BufferedReader stream = input(": connected\n\n: heartbeat\n\nid: 2\nevent: message\n"
                + "data: {\"id\":\"m1\",\"status\":\"working\",\"partial\":\"你好\"}\n\n"
                + "data: {\"id\":\"m1\",\"status\":\"done\",\"reply\":\"你好呀\"}\n\n");
        assertEquals("你好", MessageStream.readEvent(stream).get("partial"));
        assertEquals("你好呀", MessageStream.readEvent(stream).get("reply"));
        assertNull(MessageStream.readEvent(stream));
    }

    @Test public void disconnectedPartialEventIsDiscardedForPollingFallback() throws Exception {
        assertNull(MessageStream.readEvent(input("data: {\"id\":\"m1\",\"status\":\"working\"}")));
    }

    @Test(expected = IOException.class) public void malformedEventCannotBecomeAReply() throws Exception {
        MessageStream.readEvent(input("data: {\"partial\":\"wrong message\"}\n\n"));
    }
}
