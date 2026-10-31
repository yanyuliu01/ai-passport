package com.yanyuliu.pockethub;

import org.junit.Test;
import static org.junit.Assert.*;
import java.util.List;
import java.util.TimeZone;

public class UsageTest {
    private final TimeZone zone = TimeZone.getTimeZone("Asia/Shanghai");

    private Usage usage(String body) {
        return Usage.from(Json.parseObject(body));
    }

    @Test public void unavailableShortWindowIsDistinctFromUnknown() {
        Usage value = usage("{\"accounts\":[{\"id\":\"a\",\"state\":\"ok\",\"windows\":["
                + "{\"kind\":\"5h\",\"left\":null,\"unavailable\":true},"
                + "{\"kind\":\"7d\",\"left\":88}]}],\"agents\":[{\"name\":\"codex\",\"account\":\"a\"}]}");
        assertTrue(value.windowSummary("codex", 1000, zone).contains("5 小时：当前未提供此额度"));
        assertTrue(value.energy("codex", 1000, zone).contains("5 小时 未提供"));
        assertTrue(value.describe(1000, 0, zone).get(0).contains("本周 剩 88%"));
        assertEquals(88, value.left("codex", 1000));
    }

    @Test public void failedRefreshKeepsDetailButDoesNotShowCachedQuotaAsCurrent() {
        Usage value = usage("{\"at\":1000,\"accounts\":[{\"id\":\"old\",\"note\":\"401 Unauthorized\",\"windows\":[{\"kind\":\"7d\",\"left\":28}]}],\"agents\":[{\"name\":\"codex\",\"account\":\"old\"}]}");
        assertEquals(-1,value.left("codex",1000));
        assertTrue(value.windowSummary("codex",1000,zone).contains("上次收到的数据"));
        assertTrue(value.windowSummary("codex",1000,zone).contains("28%"));
    }

    @Test public void unknownWindowsAndModelsRemainVisible() {
        Usage value = usage("{\"rev\":1,\"at\":1000,\"accounts\":["
                + "{\"id\":\"codex-account\",\"state\":\"unknown\",\"agents\":[\"codex\"],\"windows\":[]}],"
                + "\"agents\":[{\"name\":\"codex\",\"account\":\"codex-account\"}]}");
        assertFalse(value.empty());
        String text = value.describe(1000, 0, zone).get(0);
        assertTrue(text.contains("codex（模型未报告 · 档位未报告）"));
        assertTrue(text.contains("5 小时：还不知道剩多少，重置时间未知"));
        assertTrue(text.contains("本周：还不知道剩多少，重置时间未知"));
        assertTrue(text.contains("更新时间未知"));
        assertFalse(text.contains("0%"));
        String line = BuddyProtocol.usage(value.deviceEntries(0));
        assertTrue(line.contains("\"st\":\"unknown\""));
        assertFalse(line.contains("\"w5\""));
        assertFalse(line.contains("\"w7\""));
    }

    @Test public void missingAccountRosterUsesAgentAccountLinkWithoutDuplicating() {
        Usage value = usage("{\"rev\":2,\"at\":1000,\"accounts\":["
                + "{\"id\":\"shared\",\"state\":\"ok\",\"agents\":[\"first\",\"first\"],"
                + "\"windows\":[{\"kind\":\"5h\",\"left\":0}]}],\"agents\":["
                + "{\"name\":\"first\",\"model\":\"m1\",\"account\":\"shared\"},"
                + "{\"name\":\"second\",\"model\":\"m2\",\"effort\":\"high\",\"account\":\"shared\"}]}");
        List<String> text = value.describe(1000, 0, zone);
        assertEquals(1, text.size());
        assertTrue(text.get(0).contains("first（m1"));
        assertTrue(text.get(0).contains("second（m2 · 高档）"));
        assertTrue(text.get(0).contains("5 小时 剩 0%"));
        assertEquals(2, value.deviceEntries(0).get(0).who.size());
    }

    @Test public void leftIsTheTighterLiveWindowForTheHomeRing() {
        Usage value = usage("{\"rev\":5,\"at\":1000,\"accounts\":["
                + "{\"id\":\"codex-account\",\"state\":\"ok\",\"agents\":[\"codex\"],"
                + "\"windows\":[{\"kind\":\"5h\",\"left\":70,\"resets_at\":5000},{\"kind\":\"7d\",\"left\":35,\"resets_at\":90000}]},"
                + "{\"id\":\"claude-account\",\"state\":\"ok\",\"agents\":[\"cc\"],"
                + "\"windows\":[{\"kind\":\"5h\",\"left\":10,\"resets_at\":900},{\"kind\":\"7d\",\"left\":80,\"resets_at\":90000}]}],"
                + "\"agents\":[{\"name\":\"codex\",\"account\":\"codex-account\"},{\"name\":\"cc\",\"account\":\"claude-account\"},"
                + "{\"name\":\"dsh\",\"model\":\"deepseek\"}]}");
        assertEquals(35, value.left("codex", 1000));
        String detail = value.windowSummary("codex", 1000, zone);
        assertTrue(detail.contains("5 小时 剩 70%"));
        assertTrue(detail.contains("本周 剩 35%"));
        assertTrue(detail.contains("重置"));
        // The 5-hour window already reset: only the weekly one still says something.
        assertEquals(80, value.left("claude", 1000));
        assertEquals(-1, value.left("dsh", 1000));
        assertEquals(-1, value.left("nobody", 1000));
    }

    @Test public void cachedAgeAdvancesAndExpiredResetDoesNotImplyFreshQuota() {
        Usage value = usage("{\"rev\":3,\"at\":1000,\"accounts\":["
                + "{\"id\":\"account\",\"state\":\"ok\",\"updated_at\":880,"
                + "\"windows\":[{\"kind\":\"5h\",\"left\":58,\"resets_at\":990}]}]}");
        String text = value.describe(1300, 300, zone).get(0);
        assertTrue(text.contains("7 分钟前更新"));
        assertTrue(text.contains("剩 58%，上次报告的重置时间已过"));
        assertEquals(420, value.deviceEntries(300).get(0).age);
    }

    @Test public void noModelHelperIsNotTreatedAsAnEmptyFeedOrBilledAccount() {
        Usage value = usage("{\"rev\":4,\"agents\":[{\"name\":\"helper\"}]}");
        assertFalse(value.empty());
        String text = value.describe(1000, 0, zone).get(0);
        assertTrue(text.contains("helper（模型未报告 · 档位未报告）"));
        assertTrue(text.contains("尚未报告用量"));
        assertFalse(text.contains("按量计费"));
        assertTrue(usage("{}").empty());
        assertNull(Usage.from(null));
    }
    @Test public void energyUsesRealSharedWindowsAndRoleAliases() {
        Usage value = usage("{\"at\":1000,\"accounts\":[{\"id\":\"shared\",\"updated_at\":999,"
                + "\"windows\":[{\"kind\":\"5h\",\"left\":0,\"resets_at\":2000},"
                + "{\"kind\":\"7d\",\"left\":72,\"resets_at\":9000}]}],"
                + "\"agents\":[{\"name\":\"cc\",\"account\":\"shared\"},"
                + "{\"name\":\"codex\",\"account\":\"shared\"}]}");
        String energy = value.energy("claude", 1000, zone);
        assertTrue(energy.contains("5 小时 0%"));
        assertTrue(energy.contains("本周 72%"));
        assertTrue(energy.contains("共用额度"));
        assertTrue(value.energy("cc", 2001, zone).contains("5 小时 未知"));
        assertEquals("精力值：未知", value.energy("ds", 1000, zone));
        Usage billed = usage("{\"agents\":[{\"name\":\"deepseek\",\"model\":\"deepseek-chat\"}]}");
        assertEquals("精力值：按量计费", billed.energy("ds", 1000, zone));
    }
}
