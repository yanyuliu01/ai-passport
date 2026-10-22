package com.yanyuliu.pockethub;

import org.junit.Test;
import java.io.BufferedReader;
import java.io.StringReader;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.*;
import static org.junit.Assert.*;

public class PresentationPolicyTest {
    private List<Object> cases() throws Exception {
        Path root = Path.of(System.getProperty("user.dir")).toAbsolutePath();
        while (root != null && !Files.isRegularFile(root.resolve("tests/fixtures/presentation-v1.json"))) root = root.getParent();
        assertNotNull("Shared presentation fixture must be available", root);
        return Json.list(Json.parseObject(new String(Files.readAllBytes(root.resolve("tests/fixtures/presentation-v1.json")),java.nio.charset.StandardCharsets.UTF_8)),"cases");
    }
    @Test public void gatewayGoldenBlocksAreNotReinterpretedByAndroid() throws Exception {
        for (Object value : cases()) {
            Map<String,Object> row = Json.object(value), wire = Json.object(row.get("wire"));
            Presentation view = Presentation.from(wire.get("presentation"));
            assertNotNull(view); assertTrue(view.supported);
            assertEquals(Json.string(row,"name"), Json.list(row,"visible"), view.blocks());
            assertEquals(Json.string(row,"placement"), view.value("placement"));
            List<String> rendered = new ArrayList<>();
            for (int i=0; i<view.blocks().size(); i++) {
                StringBuilder text = new StringBuilder();
                for (Object run : view.runs(i)) text.append(Json.string(Json.object(run),"text"));
                rendered.add(text.toString());
            }
            assertEquals(Json.list(row,"rendered"),rendered);
            assertEquals(view.json(), Presentation.restore(view.json()).json());
        }
    }
    @Test public void syncAndPersistedChatKeepTheSameProjection() throws Exception {
        Map<String,Object> row = Json.object(cases().get(0)), wire = Json.object(row.get("wire"));
        SyncCache cache = new SyncCache("*");
        SyncCache.Result result = cache.apply(Json.parseObject(UsageEvents.json(Map.of("seq",1,"epoch","test","messages",List.of(wire),"tasks",List.of()))));
        ChatHistory history = new ChatHistory();
        assertTrue(history.pending(result.pending.get(0)));
        assertEquals(1,history.main().size());
        assertEquals("done",history.main().get(0).status);
        assertEquals(Json.list(row,"visible"),history.main().get(0).presentation.blocks());
        ChatHistory restored = ChatHistory.restore(history.json());
        assertEquals(history.main().get(0).presentation.json(),restored.main().get(0).presentation.json());
        assertFalse(history.pending(result.pending.get(0)));
    }
    @Test public void stalePartialCannotReplaceFinalAndEmptySnapshotRetractsBlocks() throws Exception {
        Map<String,Object> streamed = new LinkedHashMap<>(Json.object(Json.object(cases().get(1)).get("wire")));
        Map<String,Object> retracted = new LinkedHashMap<>(Json.object(Json.object(cases().get(3)).get("wire")));
        Map<String,Object> ended = new LinkedHashMap<>(Json.object(Json.object(cases().get(0)).get("wire")));
        SyncCache cache = new SyncCache("*"); ChatHistory history = new ChatHistory();
        int revision = 0;
        for (Map<String,Object> wire : List.of(streamed,retracted,ended,streamed)) {
            wire = new LinkedHashMap<>(wire); wire.put("rev",++revision);
            SyncCache.Result page=cache.apply(Json.parseObject(UsageEvents.json(Map.of("seq",revision,"messages",List.of(wire),"tasks",List.of()))));
            history.pending(page.pending.get(0));
            if (revision==2) assertTrue(history.main().get(0).presentation.blocks().isEmpty());
        }
        assertEquals(List.of("First message","Second message"),history.main().get(0).presentation.blocks());
        assertEquals("done",history.main().get(0).status);
    }
    @Test public void sseAndPollingPreserveTheNestedContract() throws Exception {
        Map<String,Object> wire=Json.object(Json.object(cases().get(1)).get("wire"));
        String json=UsageEvents.json(Map.of("id","turn","status","running","presentation",wire.get("presentation")));
        Map<String,String> event=MessageStream.readEvent(new BufferedReader(new StringReader("data: "+json+"\n\n")));
        assertEquals(Presentation.snapshot(json).get("presentation_json"),event.get("presentation_json"));
        assertEquals(List.of("First message"),Presentation.restore(event.get("presentation_json")).blocks());
    }
    @Test public void unsupportedPolicyIsExplicit() {
        Presentation view=Presentation.restore("{\"version\":2,\"blocks\":[]}");
        assertFalse(view.supported); assertEquals("显示规则版本不兼容",view.label());
    }
}
