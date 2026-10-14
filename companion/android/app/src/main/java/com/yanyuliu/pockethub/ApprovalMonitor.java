package com.yanyuliu.pockethub;

import android.content.Context;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;

/** Up to eight independent subscriptions; an approval keeps its computer even while typing elsewhere. */
final class ApprovalMonitor {
    private static Context app;
    private static final Map<String, Subscription> watching = new HashMap<>();

    static synchronized void start(Context context) {
        if (app != null) return;
        app = context.getApplicationContext();
        Executors.newSingleThreadScheduledExecutor().scheduleWithFixedDelay(() -> reconcile(app), 0, 3, TimeUnit.SECONDS);
    }

    static void reconcile(Context context) {
        List<RuntimeClient.Target> targets = RuntimeClient.targets(context);
        synchronized (ApprovalMonitor.class) {
        if (app == null) { start(context); return; }
        Set<String> registered = new HashSet<>();
        for (int index = 0; index < Math.min(targets.size(), RuntimeClient.MAX_TARGETS); index++) {
            RuntimeClient.Target target = targets.get(index);
            registered.add(target.url);
            Subscription current = watching.get(target.url);
            if (current != null && !current.target.token.equals(target.token)) { current.stopSubscription(); watching.remove(target.url); current = null; }
            if (current == null) {
                current = new Subscription(target); watching.put(target.url, current); current.start();
            } else current.target = target;
        }
        for (String url : new ArrayList<>(watching.keySet())) if (!registered.contains(url)) { watching.remove(url).stopSubscription(); HubStore.get().dropApprovalRuntime(url); }
        }
    }

    private static final class Subscription extends Thread {
        volatile RuntimeClient.Target target;
        volatile boolean stopped;
        volatile HttpURLConnection connection;
        long after;

        Subscription(RuntimeClient.Target target) { super("ds-approvals-" + target.name); this.target = target; setDaemon(true); }
        void stopSubscription() { stopped = true; HttpURLConnection pending = connection; if (pending != null) pending.disconnect(); interrupt(); }
        // Thread.stop is unsafe; expose only the subscription lifecycle method above.

        @Override public void run() {
            int failures = 0;
            while (!stopped) {
                RuntimeClient.Target origin = target;
                try {
                    connection = (HttpURLConnection) new URL(origin.url + "/v1/feed?after=" + after + "&wait=50").openConnection();
                    connection.setInstanceFollowRedirects(false);
                    connection.setRequestProperty("Authorization", "Bearer " + origin.token);
                    connection.setConnectTimeout(10000); connection.setReadTimeout(65000);
                    int code = connection.getResponseCode();
                    if (code != 200) throw new java.io.IOException("Runtime " + code);
                    Map<String, Object> feed;
                    try (InputStream stream = connection.getInputStream()) { feed = Json.parseObject(Json.readResponse(stream)); }
                    if (feed == null) throw new java.io.IOException("Invalid feed");
                    if (stopped) break;
                    long seq = (long) Json.number(feed, "seq");
                    if (seq < after) { after = 0; continue; }
                    after = seq;
                    List<Card.Approval> pending = new ArrayList<>();
                    for (Object value : Json.list(feed, "approvals")) {
                        Card.Approval approval = Card.Approval.from(Json.object(value));
                        if (approval != null && (approval.expiresAt <= 0 || approval.expiresAt > System.currentTimeMillis() / 1000.0)) pending.add(approval.origin(origin.url, target.name));
                    }
                    HubStore.get().applyApprovals(origin.url, pending);
                    failures = 0;
                } catch (Exception failure) {
                    if (!stopped) {
                        HubStore.get().dropApprovalRuntime(origin.url);
                        after = 0;
                        try { Thread.sleep(Math.min(30000, 2000L << Math.min(failures++, 4))); }
                        catch (InterruptedException interrupted) { if (stopped) break; }
                    }
                } finally { HttpURLConnection done = connection; connection = null; if (done != null) done.disconnect(); }
            }
        }
    }
}
