package com.yanyuliu.pockethub;

import org.junit.Test;
import java.util.Map;
import static org.junit.Assert.*;

public class QuickReviewTest {
    private QuickReview snapshot() {
        return new QuickReview(Json.parseObject("{\"epoch\":\"boot-1\",\"revision\":4,"
                + "\"conversation\":\"default\",\"mode\":\"manual\",\"automatic_tools\":[\"Read\",\"Glob\",\"Grep\",\"LS\"],\"pending\":["
                + "{\"id\":\"a1\",\"key\":\"digest-1\",\"detail\":\"private command\"}]}"));
    }

    @Test public void batchCarriesOnlyTheReviewedIdsAndFingerprints() {
        String body = snapshot().batchBody();
        Map<String, Object> parsed = Json.parseObject(body);
        assertEquals("boot-1", Json.string(parsed, "epoch"));
        assertEquals("default", Json.string(parsed, "conversation"));
        assertEquals(1, Json.list(parsed, "items").size());
        assertEquals("digest-1", Json.string(Json.object(Json.list(parsed, "items").get(0)), "key"));
        assertFalse(body.contains("private command"));
    }

    @Test public void modeWritesAreAbsoluteAndCarryTheObservedRevision() {
        Map<String, Object> enable = Json.parseObject(snapshot().modeBody(true));
        assertEquals("auto_allow", Json.string(enable, "mode"));
        assertEquals(3600, Json.number(enable, "seconds"), 0);
        assertEquals(4, Json.number(enable, "revision"), 0);
        assertEquals("manual", Json.string(Json.parseObject(snapshot().modeBody(false)), "mode"));
        assertFalse(snapshot().automatic());
    }

    @Test(expected = IllegalArgumentException.class) public void oldOrBrokenResponseCannotEnableControls() {
        new QuickReview(Json.parseObject("{\"error\":\"old runtime\"}"));
    }

    @Test public void missingOrBroaderScopeCannotEnableAutomaticApprovalButCanBeDisabled() {
        assertTrue(snapshot().readOnlyModeSupported());
        for (String tools : new String[]{"[]", "[\"Read\",\"Bash\"]", "[\"Unknown\"]"}) {
            QuickReview unsupported = new QuickReview(Json.parseObject("{\"epoch\":\"b\",\"conversation\":\"default\","
                    + "\"revision\":0,\"mode\":\"auto_allow\",\"automatic_tools\":" + tools + "}"));
            assertFalse(unsupported.readOnlyModeSupported());
            assertEquals("manual", Json.string(Json.parseObject(unsupported.modeBody(false)), "mode"));
            try { unsupported.modeBody(true); fail("Unknown or write scope must require a Runtime upgrade"); }
            catch (IllegalStateException expected) { }
        }
    }

    private QuickReview configurable() {
        Map<String, Object> data = snapshot().data;
        data.put("duration_options", Json.parseObject("{\"persistent\":true,\"survives_restart\":true,"
                + "\"min_seconds\":60,\"max_seconds\":31536000}"));
        return new QuickReview(data);
    }

    @Test public void persistentAndCustomDurationsAreExplicitWrites() {
        QuickReview review = configurable();
        assertTrue(review.durationConfigSupported());
        for (int seconds : new int[]{0, 900, 86400, 604800, 31536000}) {
            Map<String, Object> body = Json.parseObject(review.modeBody(true, seconds));
            assertEquals(seconds, Json.number(body, "seconds"), 0);
            assertEquals("auto_allow", Json.string(body, "mode"));
            assertEquals(4, Json.number(body, "revision"), 0);
        }
        review.data.put("mode", "auto_allow"); review.data.put("persistent", true);
        assertTrue(review.persistent());
        assertEquals("manual", Json.string(Json.parseObject(review.modeBody(false)), "mode"));
    }

    @Test public void olderRuntimeCannotBeSentPersistentOrCustomConfiguration() {
        assertFalse(snapshot().durationConfigSupported());
        for (int seconds : new int[]{0, 900, 86400}) {
            try { snapshot().modeBody(true, seconds); fail("upgrade required"); }
            catch (IllegalStateException expected) { }
        }
        assertEquals(3600, Json.number(Json.parseObject(snapshot().modeBody(true)), "seconds"), 0);
    }

    @Test public void customMinutesValidateBeforeNetworkSubmission() {
        assertEquals(60, QuickReview.customSeconds("1"));
        assertEquals(5400, QuickReview.customSeconds(" 90 "));
        assertEquals(31536000, QuickReview.customSeconds("525600"));
        for (String value : new String[]{"", "0", "-1", "1.5", "NaN", "525601", "9999999999999999999999"}) {
            try { QuickReview.customSeconds(value); fail(value); }
            catch (IllegalArgumentException expected) { }
        }
    }
}
