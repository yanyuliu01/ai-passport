package com.yanyuliu.pockethub;

import org.junit.Test;
import java.util.HashMap;
import java.util.Map;
import static org.junit.Assert.*;

/** Shapes follow Runtime service.effect_review / review_effects (request_effect_review). */
public class EffectReviewTest {
    private static final String VIEW = "{\"message_id\":\"m1\",\"operation_id\":\"op-1\",\"conversation\":\"default\","
            + "\"runtime_id\":\"r\",\"revision\":\"rev-7\",\"status\":\"needs_review\",\"effects\":["
            + "{\"version\":1,\"id\":\"m1:0\",\"name\":\"remember\",\"args\":{\"text\":\"note\"},\"state\":\"succeeded\"},"
            + "{\"version\":1,\"id\":\"m1:1\",\"name\":\"remind\",\"args\":{\"at\":\"9:00\"},\"state\":\"unknown\"},"
            + "{\"version\":1,\"id\":\"m1:2\",\"name\":\"forget\",\"args\":{},\"state\":\"pending\"}],\"review\":null}";

    @Test public void onlyUnfinishedOperationsAreAskedAndPendingCannotBeApplied() {
        EffectReview review = EffectReview.from(Json.parseObject(VIEW));
        assertNotNull(review);
        assertEquals(2, review.items.size());
        assertEquals("m1:1", review.items.get(0).id);
        assertTrue(review.items.get(0).canApply());
        assertFalse(review.items.get(1).canApply());
        Map<String, String> chosen = new HashMap<>();
        chosen.put("m1:1", EffectReview.APPLIED);
        assertNotNull(review.missing(chosen));
        chosen.put("m1:2", EffectReview.APPLIED);
        assertNotNull(review.missing(chosen));
        chosen.put("m1:2", EffectReview.NOT_APPLIED);
        assertNull(review.missing(chosen));
    }

    @Test public void bodyKeepsTheOpaqueRevisionAndExactlyTheFourFields() {
        EffectReview review = EffectReview.from(Json.parseObject(VIEW));
        Map<String, String> chosen = new HashMap<>();
        chosen.put("m1:1", EffectReview.NOT_APPLIED); chosen.put("m1:2", EffectReview.NOT_APPLIED);
        Map<String, Object> body = Json.parseObject(review.body("review-1", chosen));
        assertEquals(4, body.size());
        assertEquals("op-1", Json.string(body, "operation_id"));
        assertEquals("rev-7", Json.string(body, "revision"));
        assertEquals("review-1", Json.string(body, "review_id"));
        Map<String, Object> outcomes = Json.object(body.get("outcomes"));
        assertEquals(2, outcomes.size());
        assertEquals("not_applied", outcomes.get("m1:2"));
        try { review.body("review-1", new HashMap<>()); fail(); } catch (IllegalArgumentException expected) { }
    }

    @Test public void statusesAndRecordedOutcomesAreKept() {
        EffectReview recorded = EffectReview.from(Json.parseObject(VIEW.replace("needs_review", "recorded")
                .replace("\"review\":null", "\"review\":{\"id\":\"review-1\",\"outcomes\":{\"m1:1\":\"applied\",\"m1:2\":\"not_applied\"}}")));
        assertEquals("recorded", recorded.status);
        assertEquals("applied", recorded.recorded.get("m1:1"));
        assertNull(EffectReview.from(Json.parseObject("{\"status\":\"weird\"}")));
        assertNull(EffectReview.from(null));
        assertEquals("m1", EffectReview.requestId("m1:you"));
        assertEquals("m1", EffectReview.requestId("m1:reply"));
        assertEquals("", EffectReview.requestId("c3"));
    }
}
