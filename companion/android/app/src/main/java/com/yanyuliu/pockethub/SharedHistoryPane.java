package com.yanyuliu.pockethub;

import android.app.Activity;
import android.graphics.Color;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.TextView;
import java.util.*;

/** A bounded window onto the shared source, never another writable chat. */
final class SharedHistoryPane {
    private final Activity activity;
    private final String source;
    private final Sheet sheet;
    private final LinearLayout body;
    private final TextView status;
    private final Button next;
    private final Map<String, Map<String, Object>> messages = new LinkedHashMap<>();
    private String cursor;
    private boolean loading;

    static void show(Activity activity, String source, String title) {
        if (source == null || source.isEmpty()) return;
        SharedHistoryPane pane = new SharedHistoryPane(activity, source, title);
        pane.sheet.show(); pane.load();
    }

    private SharedHistoryPane(Activity activity, String source, String title) {
        this.activity = activity; this.source = source;
        sheet = new Sheet(activity, title + " · 共享记录", false);
        status = new TextView(activity); status.setTextSize(12); status.setTextColor(0xff6b7a8f);
        status.setText("按真实来源只读查看，不会重跑任务或新增主人发言。"); sheet.add(status);
        body = new LinearLayout(activity); body.setOrientation(LinearLayout.VERTICAL); sheet.add(body);
        Button reset = new Button(activity); reset.setText("重新读取来源记录"); reset.setOnClickListener(v -> { if(!loading){cursor=null;messages.clear();load();} }); sheet.add(reset);
        next = new Button(activity); next.setText("加载记录"); next.setOnClickListener(v -> load()); sheet.add(next);
    }

    private void load() {
        if (loading) return; loading = true; next.setEnabled(false); status.setText("正在读取来源记录…");
        RuntimeClient.directoryHistory(activity, source, cursor, (result, error) -> {
            loading = false;
            if (!sheet.isShowing() || activity.isFinishing()) return;
            next.setEnabled(true);
            if (error != null || result == null) { status.setText(error == null ? "记录暂不可读" : error); next.setText("重试"); return; }
            if (Json.string(result, "status").equals("queued")) { status.setText("已请求来源电脑，尚未收到记录；可稍后重试。"); return; }
            for (Object value : Json.list(result, "messages")) {
                Map<String, Object> message = Json.object(value);
                String key = Json.string(message, "mesh_origin") + ":" + Json.string(message, "id");
                Map<String, Object> old = messages.get(key);
                if (old == null || Json.number(message, "rev") >= Json.number(old, "rev")) messages.put(key, message);
            }
            while (messages.size() > 200) messages.remove(messages.keySet().iterator().next());
            List<Map<String, Object>> ordered = new ArrayList<>(messages.values());
            ordered.sort((a,b) -> {
                Presentation left = Presentation.from(a.get("presentation")), right = Presentation.from(b.get("presentation"));
                if (left != null && right != null && left.supported && right.supported) return Presentation.compare(left,right);
                int at = Double.compare(Json.number(a,"created_at"),Json.number(b,"created_at"));
                return at != 0 ? at : Json.string(a,"id").compareTo(Json.string(b,"id"));
            });
            body.removeAllViews();
            for (Map<String, Object> message : ordered) {
                Map<String, Object> author = Json.object(message.get("author"));
                boolean nativeInput = Json.string(message, "input_origin").equals("native_protocol") && Json.string(author, "kind").equals("owner");
                String who = nativeInput ? "原生会话输入" : Json.string(author, "kind").equals("owner") ? "主人" : AppPresentation.name(Json.string(author, "name"));
                Presentation shown = Presentation.from(message.get("presentation"));
                if (shown != null && shown.supported) who = shown.author();
                List<String> blocks = shown != null && shown.supported ? shown.blocks() : Collections.singletonList(Json.string(message,"text"));
                for (int blockIndex=0; blockIndex<blocks.size(); blockIndex++) {
                TextView text = new TextView(activity); text.setText(shown != null && shown.supported
                        ? android.text.TextUtils.concat(who+"\n",Markdown.projected(shown,blockIndex)) : who+"\n"+blocks.get(blockIndex));
                text.setTextSize(14); text.setTextColor(0xff1f3149); text.setTextIsSelectable(true); text.setPadding(12, 16, 12, 16);
                text.setBackgroundColor(Color.WHITE); body.addView(text);
                }
                if (shown != null && !shown.supported) { TextView issue = new TextView(activity); issue.setText(shown.label()); body.addView(issue); }
            }
            cursor = Json.string(result, "cursor"); if (cursor.isEmpty()) cursor = null;
            boolean more = Json.flag(result, "more"); next.setVisibility(more ? android.view.View.VISIBLE : android.view.View.GONE);
            next.setText("继续翻页");
            status.setText((Json.string(result, "coverage").equals("available-native-public-text") ? "来源原生会话的公开文字" : "来源保留的共享记录，早期可能有缺口")
                    + " · 当前窗口 " + messages.size() + " 条（最多 200 条）");
        });
    }
}
