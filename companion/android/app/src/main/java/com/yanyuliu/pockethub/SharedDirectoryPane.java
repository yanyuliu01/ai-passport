package com.yanyuliu.pockethub;

import android.app.Activity;
import android.app.AlertDialog;
import android.text.Editable;
import android.text.TextWatcher;
import android.widget.*;
import java.util.*;

/** Shared directory is a projection of the same objects, never a new inbox. */
final class SharedDirectoryPane {
    private final Activity activity;
    private final Sheet sheet;
    private final LinearLayout rows;
    private final TextView status;
    private final EditText query;
    private final CheckBox archived, internal;
    private final android.os.Handler handler = new android.os.Handler(android.os.Looper.getMainLooper());
    private final Button more;
    private String cursor;
    private int generation;
    private List<Object> entries = new ArrayList<>();

    static void show(Activity activity) { SharedDirectoryPane pane = new SharedDirectoryPane(activity); pane.sheet.show(); pane.load(); }
    private SharedDirectoryPane(Activity activity) {
        this.activity = activity; sheet = new Sheet(activity, "统一会话目录", false);
        status = new TextView(activity); status.setTextSize(12); sheet.add(status);
        query = new EditText(activity); query.setSingleLine(true); query.setHint("搜索名称、项目或来源电脑"); sheet.add(query);
        archived = new CheckBox(activity); archived.setText("显示已归档记录"); archived.setOnCheckedChangeListener((b,v) -> render()); sheet.add(archived);
        internal = new CheckBox(activity); internal.setText("显示内部帮手会话"); internal.setOnCheckedChangeListener((b,v) -> render()); sheet.add(internal);
        Button refresh = new Button(activity); refresh.setText("刷新目录"); refresh.setOnClickListener(v -> load()); sheet.add(refresh);
        more = new Button(activity); more.setText("加载更多会话"); more.setVisibility(android.view.View.GONE); more.setOnClickListener(v -> load(true));
        rows = new LinearLayout(activity); rows.setOrientation(LinearLayout.VERTICAL); sheet.add(rows); sheet.add(more);
        query.addTextChangedListener(new TextWatcher() {
            public void beforeTextChanged(CharSequence s,int start,int count,int after) { }
            public void onTextChanged(CharSequence s,int start,int before,int count) {
                final int expected=++generation; cursor=null;
                handler.postDelayed(() -> { if (expected==generation && sheet.isShowing()) load(); }, 350);
            }
            public void afterTextChanged(Editable value) { }
        });
    }
    private void load() { load(false); }
    private void load(boolean append) {
        final int expected=++generation;
        status.setText("正在读取同一份会话目录…"); more.setEnabled(false);
        RuntimeClient.directoryPage(activity, query.getText().toString().trim(), append?cursor:null, (result,error) -> {
            if (expected!=generation || !sheet.isShowing() || activity.isFinishing()) return;
            more.setEnabled(true);
            if (error != null || result == null) { status.setText(error == null ? "目录不可用" : error); return; }
            Map<String,Object> merged=new LinkedHashMap<>();
            if(append)for(Object value:entries)merged.put(Json.string(Json.object(value),"uid"),value);
            for(Object value:Json.list(result,"entries"))merged.put(Json.string(Json.object(value),"uid"),value);
            entries=new ArrayList<>(merged.values());
            entries.sort((a,b)-> { Map<String,Object> x=Json.object(a),y=Json.object(b); int pin=Boolean.compare(Json.flag(y,"pinned"),Json.flag(x,"pinned"));return pin!=0?pin:Double.compare(Json.number(y,"updated_at"),Json.number(x,"updated_at")); });
            cursor=Json.string(result,"cursor");if(cursor.isEmpty())cursor=null;
            more.setVisibility(Json.flag(result,"more")?android.view.View.VISIBLE:android.view.View.GONE);
            Map<String,Object> discovery = Json.object(result.get("discovery"));
            status.setText(Json.string(discovery,"phase").equals("discovering") ? "正在补齐来源目录，可先查看已有记录。" : "统一名称与管理状态由来源 Runtime 保存。");
            if(!Json.list(result,"storage_errors").isEmpty())status.setText("共享整理资料不可用，原文件已保留；整理操作暂时停用。");
            render();
        });
    }
    private void render() {
        rows.removeAllViews(); String filter = query.getText().toString().trim().toLowerCase(Locale.ROOT); int shown=0;
        for (Object value : entries) {
            Map<String,Object> entry = Json.object(value);
            if ((!archived.isChecked() && Json.flag(entry,"archived")) || (!internal.isChecked() && Json.flag(entry,"internal"))) continue;
            if (Json.string(entry,"kind").equals("native") && Json.flag(entry,"bound") && filter.isEmpty()) continue;
            String title=Json.string(entry,"title");
            String source=Json.string(entry,"source_name")+" · "+AppPresentation.name(Json.string(entry,"agent"))+" · "+Json.string(entry,"project");
            if (!(title+" "+source+" "+Json.string(entry,"original_title")).toLowerCase(Locale.ROOT).contains(filter)) continue;
            Button row=new Button(activity); row.setAllCaps(false); row.setText(title+"\n"+source); row.setTextSize(13);
            row.setOnClickListener(v -> actions(entry)); rows.addView(row); if (++shown>=100) break;
        }
        if (shown==0) { TextView empty=new TextView(activity); empty.setText("没有匹配的记录"); rows.addView(empty); }
        if (shown>=100) { TextView hint=new TextView(activity); hint.setText("当前显示前 100 条，请搜索缩小范围。"); rows.addView(hint); }
    }
    private void edit(Map<String,Object> entry, String fields) {
        RuntimeClient.directoryEdit(activity,Json.string(entry,"uid"),(long)Json.number(entry,"metadata_revision"),fields,(result,error)->{
            if (!sheet.isShowing()) return;
            if(error!=null) status.setText(error); else if(result!=null&&Json.string(result,"status").equals("queued"))status.setText("已排队，等待来源电脑应用"); else load();
        });
    }
    private void actions(Map<String,Object> entry) {
        String id=Json.string(entry,"uid"),title=Json.string(entry,"title");
        Sheet menu=new Sheet(activity,title,false);
        String displayGap = Json.string(Json.object(entry.get("native_presentation")), "label");
        if (!displayGap.isEmpty()) menu.message(displayGap);
        if(!Json.string(entry,"binding_error").isEmpty())menu.message("执行关联记录有缺口，请结合来源记录核对。");
        Map<String,Object> nativeOpen=Json.object(entry.get("native_open"));
        if(Json.flag(nativeOpen,"supported"))menu.option("在来源电脑打开原生会话","请求打开现有会话；实际显示需在电脑核对",false,()->RuntimeClient.directoryOpen(activity,id,(result,error)->{
            if(sheet.isShowing())status.setText(error!=null?error:"已请求来源电脑打开原会话，请在电脑核对。");
        }));
        else if(!nativeOpen.isEmpty())menu.message(Json.string(nativeOpen,"reason"));
        menu.option("查看共享记录",null,false,()->SharedHistoryPane.show(activity,id,title));
        if(!Json.string(entry,"conversation_id").equals("default")||!Json.string(entry,"kind").equals("conversation")) {
        menu.option("统一命名","保留原生原名和真实执行目录",false,()->{
            EditText name=new EditText(activity);name.setText(title);
            new AlertDialog.Builder(activity).setTitle("统一命名").setView(name).setNegativeButton("取消",null).setPositiveButton("保存",(d,w)->{
                StringBuilder text=new StringBuilder();BuddyProtocol.quote(text,name.getText().toString().trim());edit(entry,"{\"title\":"+text+"}");
            }).show();
        });
        }
        menu.option("项目分类", "只改展示分类，真实目录保持原值", false, () -> {
            EditText name=new EditText(activity); name.setText(Json.string(entry,"project"));
            new AlertDialog.Builder(activity).setTitle("项目分类").setView(name).setNegativeButton("取消",null).setPositiveButton("保存",(d,w)->{
                StringBuilder text=new StringBuilder(); BuddyProtocol.quote(text,name.getText().toString().trim()); edit(entry,"{\"project\":"+text+"}");
            }).show();
        });
        menu.option("生成标题与摘要建议", "通过同一 Gateway 生成；不会自动覆盖命名",false,()->RuntimeClient.directoryMetadata(activity,id,(result,error)->{
            if(sheet.isShowing())status.setText(error!=null?error:"已请求生成，稍后刷新目录查看建议。");
        }));
        Map<String,Object> generated=Json.object(entry.get("generated"));
        if(!generated.isEmpty()) {
            menu.message((Json.flag(generated,"stale")?"摘要待更新":"摘要")+" · "+Json.string(generated,"source")+"\n"+Json.string(generated,"summary"));
            if(!Json.string(entry,"conversation_id").equals("default")||!Json.string(entry,"kind").equals("conversation"))menu.option("使用建议名称",Json.string(generated,"title"),false,()->{
                StringBuilder text=new StringBuilder();BuddyProtocol.quote(text,Json.string(generated,"title"));edit(entry,"{\"title\":"+text+"}");
            });
        }
        menu.option(Json.flag(entry,"pinned")?"取消置顶":"置顶",null,false,()->edit(entry,"{\"pinned\":"+!Json.flag(entry,"pinned")+"}"));
        if(!Json.string(entry,"conversation_id").equals("default")||!Json.string(entry,"kind").equals("conversation"))
            menu.option(Json.flag(entry,"archived")?"恢复记录":"归档记录","归档不删除历史，不停止执行",false,()->edit(entry,"{\"archived\":"+!Json.flag(entry,"archived")+"}"));
        menu.message("原名："+Json.string(entry,"original_title")+"\n目录："+Json.string(entry,"cwd"));menu.show();
    }
}
