package com.yanyuliu.pockethub;

import org.junit.Test;
import java.util.ArrayList;
import java.util.List;
import static org.junit.Assert.*;

public class NetworkTransitionsTest {
    private static final class Clock implements NetworkTransitions.Scheduler {
        long now;
        final List<Runnable> tasks = new ArrayList<>();
        final List<Long> deadlines = new ArrayList<>();
        boolean closed;
        public void schedule(Runnable task, long delay) { tasks.add(task); deadlines.add(now + delay); }
        public void close() { closed = true; }
        void advance(long ms) {
            now += ms;
            for (int i = 0; i < tasks.size();) {
                if (deadlines.get(i) <= now) { Runnable task = tasks.remove(i); deadlines.remove(i); task.run(); }
                else i++;
            }
        }
    }

    @Test public void one_switch_is_one_reset_despite_lost_available_and_validation_callbacks() {
        int[] changes = {0}; Clock clock = new Clock();
        NetworkTransitions<String> state = new NetworkTransitions<>("wifi", true, () -> changes[0]++, clock);
        state.lost("wifi"); state.available("cell"); state.lost("wifi");
        state.capabilities("cell", false); state.capabilities("cell", true); state.capabilities("cell", true);
        clock.advance(249); assertEquals(0, changes[0]);
        clock.advance(1); assertEquals(1, changes[0]);
        state.lost("wifi"); state.available("cell"); state.capabilities("cell", true);
        clock.advance(1000); assertEquals(1, changes[0]);
        state.close(); assertTrue(clock.closed);
    }

    @Test public void late_first_validation_does_not_tear_down_same_new_route_twice() {
        int[] changes = {0}; Clock clock = new Clock();
        NetworkTransitions<String> state = new NetworkTransitions<>("wifi", true, () -> changes[0]++, clock);
        state.available("cell"); clock.advance(250); assertEquals(1, changes[0]);
        state.capabilities("cell", true); clock.advance(250); assertEquals(1, changes[0]);
        state.capabilities("cell", false); state.capabilities("cell", true);
        clock.advance(250); assertEquals(2, changes[0]);
        state.close();
    }

    @Test public void no_validation_and_further_route_changes_cannot_starve_recovery() {
        int[] changes = {0}; Clock clock = new Clock();
        NetworkTransitions<String> state = new NetworkTransitions<>("wifi", true, () -> changes[0]++, clock);
        state.lost("wifi"); clock.advance(200); state.available("cell");
        clock.advance(50); assertEquals(1, changes[0]);
        state.available("wifi2"); state.lost("cell"); clock.advance(250); assertEquals(2, changes[0]);
        state.lost("wifi2"); clock.advance(250); assertEquals(3, changes[0]);
        state.close();
    }

    @Test public void initial_unvalidated_network_and_shutdown_handle_late_callbacks() {
        int[] changes = {0}; Clock clock = new Clock();
        NetworkTransitions<String> state = new NetworkTransitions<>("wifi", false, () -> changes[0]++, clock);
        state.capabilities("wifi", true); clock.advance(250); assertEquals(1, changes[0]);
        state.available("cell"); state.close(); clock.advance(250);
        state.capabilities("cell", true); state.lost("cell"); state.available("wifi"); clock.advance(1000);
        assertEquals(1, changes[0]); assertTrue(clock.closed);
    }
}
