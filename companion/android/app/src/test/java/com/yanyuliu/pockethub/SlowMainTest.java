package com.yanyuliu.pockethub;

import org.junit.Test;
import java.util.ArrayList;
import java.util.List;
import static org.junit.Assert.*;

public class SlowMainTest {
    @Test public void onlyLongMessagesAreNamedWithoutTheirDetail() {
        long[] now = {0};
        List<String> lines = new ArrayList<>();
        SlowMain watch = new SlowMain(() -> now[0], lines::add);
        watch.println(">>>>> Dispatching to Handler (android.os.Handler) {3f2a1b} com.yanyuliu.pockethub.HubStore$$Lambda@9c1d: 0");
        now[0] = 40;
        watch.println("<<<<< Finished to Handler (android.os.Handler) {3f2a1b} com.yanyuliu.pockethub.HubStore$$Lambda@9c1d");
        watch.println(">>>>> Dispatching to Handler (android.view.Choreographer$FrameHandler) {77aa} android.view.Choreographer$FrameDisplayEventReceiver@1e: 0");
        now[0] = 520;
        watch.println("<<<<< Finished to Handler (android.view.Choreographer$FrameHandler) {77aa} android.view.Choreographer$FrameDisplayEventReceiver@1e");
        assertEquals(List.of("slow_main ms=480 at=Handler (android.view.Choreographer$FrameHandler) android.view.Choreographer$FrameDisplayEventReceiver"), lines);
    }

    @Test public void aFinishWithoutAStartIsIgnored() {
        List<String> lines = new ArrayList<>();
        new SlowMain(() -> 1000, lines::add).println("<<<<< Finished to Handler (x) {1} y");
        assertTrue(lines.isEmpty());
    }
}
