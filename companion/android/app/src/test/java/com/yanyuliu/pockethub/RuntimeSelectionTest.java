package com.yanyuliu.pockethub;
import org.junit.Test;
import static org.junit.Assert.*;
import java.util.*;
import java.util.concurrent.TimeUnit;

public class RuntimeSelectionTest {
    @Test public void newComputerDiscoveryDoesNotStealAManualOrExistingTarget() {
        List<String> available=Arrays.asList("mac","windows");
        assertEquals("mac",RuntimeSelection.resolve("mac","windows",available));
        assertEquals("mac",RuntimeSelection.resolve("","mac",available));
        assertEquals("mac",RuntimeSelection.resolve("",null,available));
    }
    @Test public void manualOfflineOrRemovedTargetCannotFallBackToAnotherComputer() {
        assertNull(RuntimeSelection.resolve("windows","mac",Collections.singletonList("mac")));
        assertFalse(RuntimeSelection.mayFallback("windows"));
        assertTrue(RuntimeSelection.mayFallback(""));
        assertEquals("windows",RuntimeSelection.resolve("windows","mac",Arrays.asList("mac","windows")));
    }
    @Test public void switchAndRestartKeepTheAccountChoiceSeparate() {
        Map<String,String> persisted=new HashMap<>();
        persisted.put(RuntimeSelection.preferenceKey("account-one"),"windows");
        persisted.put(RuntimeSelection.preferenceKey("account-two"),"mac");
        assertEquals("windows",RuntimeSelection.resolve(persisted.get(RuntimeSelection.preferenceKey("account-one")),"mac",Arrays.asList("mac","windows")));
        assertEquals("mac",RuntimeSelection.resolve(persisted.get(RuntimeSelection.preferenceKey("account-two")),"windows",Arrays.asList("mac","windows")));
    }
    @Test public void relayReadyNeedsAFreshComputerHeartbeatAndTransport() {
        long[] now={0};PeerAvailability peer=new PeerAvailability(()->now[0]);
        assertFalse(peer.online(true));peer.observed();assertTrue(peer.online(true));
        assertFalse(peer.online(false));now[0]=TimeUnit.SECONDS.toNanos(11);assertFalse(peer.online(true));
        peer.observed();assertTrue(peer.online(true));now[0]-=1;assertFalse(peer.online(true));
    }
}
