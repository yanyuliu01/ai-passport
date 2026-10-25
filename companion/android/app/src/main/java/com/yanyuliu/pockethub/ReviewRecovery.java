package com.yanyuliu.pockethub;

import java.io.IOException;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Objects;
import java.util.Set;
import java.util.UUID;

/** Durable owner confirmation, independent of Android UI and transport. Never repeats an effect. */
final class ReviewRecovery {
    static final class Source {
        final String url, token, runtime, subject;
        final long generation;
        Source(String url, String token, String runtime, String subject, long generation) {
            this.url = url; this.token = token; this.runtime = runtime; this.subject = subject; this.generation = generation;
        }
        boolean sameRoute(Source other) {
            return other != null && url.equals(other.url) && runtime.equals(other.runtime) && subject.equals(other.subject);
        }
        boolean sameSession(Source other) {
            return sameRoute(other) && token.equals(other.token) && generation == other.generation;
        }
        String key(String record) { return "effect_review:" + url + ":" + record; }
    }

    static final class Pending {
        final Source source;
        final String body;
        Pending(Source source, String body) { this.source = source; this.body = body; }
        Map<String, Object> fields() { return Json.parseObject(body); }
        boolean matches(EffectReview review) {
            Map<String, Object> fields = fields();
            return fields != null && review.operationId.equals(Json.string(fields, "operation_id"))
                    && Objects.equals(review.revision, fields.get("revision"));
        }
        Map<String, String> outcomes() {
            Map<String, String> result = new LinkedHashMap<>();
            Map<String, Object> values = Json.object(fields().get("outcomes"));
            if (values != null) for (Map.Entry<String, Object> entry : values.entrySet())
                if (entry.getValue() instanceof String) result.put(entry.getKey(), (String) entry.getValue());
            return result;
        }
    }

    static final class View {
        final EffectReview review;
        final Pending pending;
        final String problem;
        View(EffectReview review, Pending pending, String problem) {
            this.review = review; this.pending = pending; this.problem = problem;
        }
        boolean editable() { return problem == null && pending == null && review.status.equals("needs_review"); }
        Map<String, String> outcomes() { return pending == null ? review.recorded : pending.outcomes(); }
    }

    interface Store {
        Pending load(String key) throws Exception;
        /** Returns only once the entire original identity and body are durably saved. */
        void save(String key, Pending pending) throws Exception;
        /** Remove only this exact pending record; a late result must not erase a replacement. */
        void clear(String key, Pending pending) throws Exception;
    }
    interface Transport { Map<String, Object> request(Source source, String record, String body) throws Exception; }
    interface Current { boolean isCurrent(Source source); }

    private final Store store;
    private final Transport transport;
    private final Current current;
    private final Set<String> busy = new HashSet<>();
    ReviewRecovery(Store store, Transport transport, Current current) {
        this.store = store; this.transport = transport; this.current = current;
    }
    private void check(Source source) throws IOException {
        if (!current.isCurrent(source)) throw new IOException("连接已变化，请重新打开核对；原确认仍保留");
        if (source.runtime.isEmpty()) throw new IOException("尚未确认电脑身份，请连接后重新打开核对");
    }
    private EffectReview read(Source source, Map<String, Object> data) throws IOException {
        EffectReview view = EffectReview.from(data);
        if (view == null) throw new IOException("这轮请求没有可核对的操作");
        if (!source.runtime.equals(view.runtimeId)) throw new IOException("电脑身份已变化；原确认仍保留");
        return view;
    }
    private void enter(String key) throws IOException {
        synchronized (busy) { if (!busy.add(key)) throw new IOException("正在核对，请稍后再试"); }
    }
    private void leave(String key) { synchronized (busy) { busy.remove(key); } }

    View load(Source source, String record) throws Exception {
        String key = source.key(record); enter(key);
        try {
            check(source);
            EffectReview review = read(source, transport.request(source, record, null)); check(source);
            Pending pending;
            try { pending = store.load(key); }
            catch (Exception failure) { return new View(review, null, "原确认身份未能恢复，记录已保留；请检查系统安全存储"); }
            check(source);
            if (pending != null) {
                if (!source.sameRoute(pending.source)) return new View(review, pending, "请回到原电脑和原连接重试；原确认仍保留");
                // Only an authoritative new review or a matching completion can retire a saved body.
                if ((pending.matches(review) && review.status.equals("reviewed"))
                        || (!pending.matches(review) && review.status.equals("needs_review"))) {
                    check(source); store.clear(key, pending); pending = null;
                } else if (!pending.matches(review)) return new View(review, pending, "记录已变化；原确认仍保留");
            }
            String problem = pending == null && review.status.equals("recorded")
                    ? "确认已保存，请在保留原确认的设备上重试发出说明" : null;
            return new View(review, pending, problem);
        } finally { leave(key); }
    }

    View submit(Source source, String record, EffectReview shown, Map<String, String> outcomes) throws Exception {
        String key = source.key(record); enter(key);
        try {
            check(source);
            Pending pending = store.load(key); check(source);
            EffectReview latest = read(source, transport.request(source, record, null)); check(source);
            if (!shown.operationId.equals(latest.operationId) || !Objects.equals(shown.revision, latest.revision))
                throw new IOException("记录已变化，请重新打开核对");
            if (pending != null && !source.sameRoute(pending.source)) throw new IOException("请回到原电脑和原连接重试");
            if (pending != null && !pending.matches(latest)) throw new IOException("记录已变化，请重新打开核对");
            if (latest.status.equals("reviewed")) {
                if (pending != null) { check(source); store.clear(key, pending); }
                return new View(latest, null, null);
            }
            if (pending == null) {
                if (!latest.status.equals("needs_review"))
                    throw new IOException("确认已保存，请在保留原确认的设备上重试发出说明");
                // Validate against the current operation without applying old answers to a new revision.
                pending = new Pending(source, latest.body("review-" + UUID.randomUUID().toString().replace("-", ""), outcomes));
            }
            // SharedPreferences can update memory even when commit fails. Recommit the same envelope
            // on every attempt, so a later retry never sends an in-memory-only confirmation.
            check(source); store.save(key, pending); check(source);
            EffectReview result = read(source, transport.request(pending.source, record, pending.body)); check(source);
            if (!pending.matches(result)) throw new IOException("核对回执与原确认不匹配；原确认仍保留");
            if (result.status.equals("reviewed")) { check(source); store.clear(key, pending); pending = null; }
            return new View(result, pending, null);
        } finally { leave(key); }
    }
}
