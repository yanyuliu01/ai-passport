package com.yanyuliu.pockethub;

import org.junit.Test;
import static org.junit.Assert.*;

/** 同一个小鱼网络里的几台电脑算一组（授权只盯一份、连不上时换着用）。 */
public class RuntimeGroupTest {
    @Test public void membersOfOneMeshShareAGroupEvenOnDifferentRuntimes() {
        assertEquals(RuntimeClient.groupKeyOf("s1", "mac", "https://mac.test"),
                RuntimeClient.groupKeyOf("s1", "windows", "https://windows.test"));
    }

    @Test public void withoutAMeshTheRuntimeOrTheAddressDecides() {
        assertEquals(RuntimeClient.groupKeyOf("", "mac", "http://192.168.1.2:8765"),
                RuntimeClient.groupKeyOf("", "mac", "https://mac.test"));
        assertNotEquals(RuntimeClient.groupKeyOf("", "mac", "https://mac.test"),
                RuntimeClient.groupKeyOf("", "windows", "https://windows.test"));
        assertNotEquals(RuntimeClient.groupKeyOf("", "", "https://a.test"),
                RuntimeClient.groupKeyOf("", "", "https://b.test"));
        // 主体编号和 Runtime 编号不会撞成一组。
        assertNotEquals(RuntimeClient.groupKeyOf("x", "", "u"), RuntimeClient.groupKeyOf("", "x", "u"));
    }
    @org.junit.Test public void accountUpgradeRetainsAuthenticatedOldArchiveWithoutRestoringItsRoute() {
        java.util.List<String[]> known=java.util.Collections.singletonList(new String[]{"http://old-lan","runtime","account-space"});
        org.junit.Assert.assertEquals("http://old-lan",RuntimeClient.retainedHistoryKey("relay://new.account","runtime","account-space",known));
        org.junit.Assert.assertEquals("relay://new.account",RuntimeClient.retainedHistoryKey("relay://new.account","runtime","another-space",known));
        org.junit.Assert.assertEquals("relay://new.account",RuntimeClient.retainedHistoryKey("relay://new.account","runtime","",known));
    }
    @org.junit.Test public void queuedReadyFromASignedOutAccountCannotRegisterOrSelectAComputer() {
        RuntimeClient.registerRelay(null,"relay://old.account","Stale computer");
        RuntimeClient.select(null,"relay://old.account");
    }
}
