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
}
