package com.yanyuliu.pockethub;

import org.junit.Test;
import static org.junit.Assert.*;

/** 同一个小鱼网络里的几台电脑算一组（授权只盯一份、连不上时换着用）。 */
public class RuntimeGroupTest {
    @Test public void legacyImportCandidateRequiresTheExactRuntimeAndSubject() {
        String account="relay://"+"1".repeat(32)+"."+"a".repeat(32),old="http://old-runtime";
        assertTrue(RuntimeClient.legacySourceMatches(account,old,"runtime","subject","runtime","subject"));
        assertFalse(RuntimeClient.legacySourceMatches(account,old,"runtime","subject","other","subject"));
        assertFalse(RuntimeClient.legacySourceMatches(account,old,"runtime","subject","runtime","other"));
        assertFalse(RuntimeClient.legacySourceMatches(account,old,"","subject","","subject"));
        assertFalse(RuntimeClient.legacySourceMatches(account,"relay://"+"2".repeat(32)+"."+"b".repeat(32),"runtime","subject","runtime","subject"));
        assertFalse(RuntimeClient.legacySourceMatches(old,old,"runtime","subject","runtime","subject"));
    }
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
    @org.junit.Test public void historyAliasesNeverCrossAccountsOrUnownedLegacyArchives() {
        String accountA="a".repeat(32),accountB="b".repeat(32);
        String device1="1".repeat(32),device2="2".repeat(32);
        String old="http://old-lan",a="relay://"+device1+"."+accountA;
        String b1="relay://"+device1+"."+accountB,b2="relay://"+device2+"."+accountB;
        java.util.List<String[]> known=java.util.Arrays.asList(new String[]{old,"runtime","space"},
                new String[]{a,"runtime","space"},new String[]{b1,"runtime","space"},new String[]{b2,"runtime","space"});
        assertEquals(a,RuntimeClient.retainedHistoryKey(a,"runtime","space",known));
        assertEquals(b1,RuntimeClient.retainedHistoryKey(b1,"runtime","space",known));
        assertEquals(b1,RuntimeClient.retainedHistoryKey(b2,"runtime","space",known));
        assertEquals(old,RuntimeClient.retainedHistoryKey("http://new-lan","runtime","space",known));
        assertEquals(b2,RuntimeClient.retainedHistoryKey(b2,"runtime","another-space",known));
        assertEquals(b2,RuntimeClient.retainedHistoryKey(b2,"runtime","",known));
        // A malformed account route cannot silently inherit any other archive.
        assertEquals("relay://bad.account",RuntimeClient.retainedHistoryKey("relay://bad.account","runtime","space",known));
    }
    @org.junit.Test public void queuedReadyFromASignedOutAccountCannotRegisterOrSelectAComputer() {
        RuntimeClient.registerRelay(null,"relay://old.account","Stale computer");
        RuntimeClient.select(null,"relay://old.account");
    }
}
