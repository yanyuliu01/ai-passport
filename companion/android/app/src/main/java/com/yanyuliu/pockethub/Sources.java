package com.yanyuliu.pockethub;

import android.content.Context;
import android.content.SharedPreferences;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * 消息来源登记表：哪个 App 的通知算作哪个“来源”。
 * 中枢本身不认识 Claude 或 Codex，只认识这张表，所以加一个新来源不用改别的代码。
 */
public final class Sources {
    public static final class Source {
        public final String packageName;
        public final String label;
        public final boolean enabled;

        Source(String packageName, String label, boolean enabled) {
            this.packageName = packageName;
            this.label = label;
            this.enabled = enabled;
        }
    }

    private static final String PREFS = "sources";
    private static final String KEY_LIST = "list";
    // 内置来源。Codex 的消息来自 ChatGPT App。
    private static final String[][] DEFAULTS = {
            {"com.anthropic.claude", "Claude"},
            {"com.openai.chatgpt", "Codex"},
    };

    private Sources() {
    }

    public static synchronized List<Source> all(Context context) {
        return new ArrayList<>(load(context).values());
    }

    /** 这个包名对应的已启用来源；没有登记或已关闭则返回 null。 */
    public static synchronized Source match(Context context, String packageName) {
        Source source = load(context).get(packageName);
        return source != null && source.enabled ? source : null;
    }

    public static synchronized int enabledCount(Context context) {
        int count = 0;
        for (Source source : load(context).values()) {
            if (source.enabled) {
                count++;
            }
        }
        return count;
    }

    public static synchronized void put(Context context, String packageName, String label,
                                        boolean enabled) {
        String cleanPackage = packageName == null ? "" : packageName.trim();
        String cleanLabel = label == null ? "" : label.trim().replace('|', ' ').replace('\n', ' ');
        if (cleanPackage.isEmpty() || cleanPackage.contains("|") || cleanPackage.contains("\n")) {
            return;
        }
        Map<String, Source> sources = load(context);
        sources.put(cleanPackage,
                new Source(cleanPackage, cleanLabel.isEmpty() ? cleanPackage : cleanLabel, enabled));
        save(context, sources);
    }

    public static synchronized void remove(Context context, String packageName) {
        Map<String, Source> sources = load(context);
        if (sources.remove(packageName) != null) {
            save(context, sources);
        }
    }

    private static Map<String, Source> load(Context context) {
        Map<String, Source> sources = new LinkedHashMap<>();
        SharedPreferences prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
        String stored = prefs.getString(KEY_LIST, null);
        if (stored == null) {
            for (String[] entry : DEFAULTS) {
                sources.put(entry[0], new Source(entry[0], entry[1], true));
            }
            return sources;
        }
        for (String line : stored.split("\n")) {
            String[] parts = line.split("\\|");
            if (parts.length == 3 && !parts[0].isEmpty()) {
                sources.put(parts[0], new Source(parts[0], parts[1], "1".equals(parts[2])));
            }
        }
        return sources;
    }

    private static void save(Context context, Map<String, Source> sources) {
        StringBuilder out = new StringBuilder();
        for (Source source : sources.values()) {
            out.append(source.packageName).append('|').append(source.label).append('|')
                    .append(source.enabled ? '1' : '0').append('\n');
        }
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit()
                .putString(KEY_LIST, out.toString()).apply();
    }
}
