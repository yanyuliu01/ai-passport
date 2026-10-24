package com.yanyuliu.pockethub;

import org.junit.Test;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.*;
import static org.junit.Assert.*;

public class FormatRunsTest {
    private Presentation fixture(String name) throws Exception {
        Path root = Path.of(System.getProperty("user.dir")).toAbsolutePath();
        while (root != null && !Files.isRegularFile(root.resolve("tests/fixtures/presentation-v1.json"))) root = root.getParent();
        assertNotNull(root);
        for (Object value : Json.list(Json.parseObject(new String(Files.readAllBytes(root.resolve("tests/fixtures/presentation-v1.json")),
                java.nio.charset.StandardCharsets.UTF_8)), "cases")) {
            Map<String, Object> row = Json.object(value);
            if (Json.string(row, "name").equals(name)) return Presentation.from(Json.object(row.get("wire")).get("presentation"));
        }
        throw new AssertionError("missing fixture " + name);
    }

    private static String styles(FormatRuns format, String piece) {
        int start = format.text.indexOf(piece);
        assertTrue(piece, start >= 0);
        List<String> found = new ArrayList<>();
        for (FormatRuns.Span span : format.spans)
            if (span.start <= start && span.end >= start + piece.length()) found.add(span.style + (span.value.isEmpty() ? "" : "=" + span.value));
        Collections.sort(found);
        return String.join(",", found);
    }

    @Test public void everyGatewayStyleBecomesARangeWithoutMarkers() throws Exception {
        Presentation view = fixture("complete-formatting-runs");
        FormatRuns format = FormatRuns.of(view.runs(0));
        assertFalse(format.text.contains("## "));
        assertFalse(format.text.contains("`make"));
        assertEquals("BOLD,HEADING=2", styles(format, "Plan"));
        assertEquals("CODE", styles(format, "make test"));
        assertEquals("ITALIC", styles(format, "the"));
        assertEquals("LINK=https://example.com/a", styles(format, "docs"));
        assertEquals("", styles(format, "this"));  // javascript: link stays plain text
        assertEquals("QUOTE,STRIKE", styles(format, "old"));
        assertEquals("CODE_BLOCK=sh", styles(format, "echo **literal**"));
        assertEquals("", styles(format, "snake_case and 2*3*4"));
    }

    @Test public void onelinePlacesUseTheGatewayPlainText() throws Exception {
        Presentation view = fixture("complete-formatting-runs");
        assertEquals(view.value("plain"), Presentation.plain(view, "## ignored"));
        assertTrue(Presentation.plain(view, "").startsWith("Plan\nRun make test"));
        assertEquals("粗体 · 一项", Presentation.plain(null, "**粗体**\n- 一项").replace("\n", " "));
    }

    @Test public void unsafeLinksNeverBecomeClickable() {
        List<Object> runs = new ArrayList<>(List.of(Json.parseObject("{\"text\":\"x\",\"href\":\"javascript:alert(1)\"}"),
                Json.parseObject("{\"text\":\"y\",\"href\":\"mailto:a@example.com\"}")));
        FormatRuns format = FormatRuns.of(runs);
        assertEquals(1, format.spans.size());
        assertEquals("mailto:a@example.com", format.spans.get(0).value);
    }
}
