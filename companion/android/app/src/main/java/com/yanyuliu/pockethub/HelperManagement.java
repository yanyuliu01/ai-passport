package com.yanyuliu.pockethub;

import android.app.Activity;
import android.graphics.Color;
import android.graphics.drawable.GradientDrawable;
import android.text.InputType;
import android.view.Gravity;
import android.view.View;
import android.widget.*;
import java.util.*;

/** Account-authorized management of the selected computer, using the existing sheet style. */
final class HelperManagement {
    private final Activity activity;
    private final Runnable saved;
    private final RuntimeClient.Target target;
    private static final int INK=0xff1f3149, HINT=0xff6b7a8f, BLUE=0xff2f6fe0;
    HelperManagement(Activity activity, Runnable saved) {
        this.activity=activity; this.saved=saved; target=RuntimeClient.selected(activity);
    }
    void create() {
        if(target==null){notice("请先连接电脑");return;}
        RuntimeClient.helperRequest(activity,target,"/v1/helpers/options",null,(options,error)->{
            if(error!=null){notice(error);return;} add(options);
        });
    }
    void actions(View anchor,String name,String title) {
        if(target==null){notice("请先连接电脑");return;}
        new AnchoredMenu(activity).option("重命名",false,()->{
            Sheet edit=Sheet.centered(activity,"重命名帮手");EditText input=field("帮手名称",false);input.setText(title);
            TextView errorView=label("");errorView.setTextColor(0xffb3402f);TextView submit=choice("保存");
            submit.setOnClickListener(v->{
                Map<String,Object> body=new LinkedHashMap<>();body.put("title",input.getText().toString().trim());submit.setEnabled(false);
                RuntimeClient.helperRequest(activity,target,"/v1/helpers/"+name+"/rename",body,(value,problem)->{
                    submit.setEnabled(true);if(problem!=null){errorView.setText(problem);return;}edit.dismiss();saved.run();
                });
            });edit.add(input).add(errorView).add(submit).show();
        }).option("删除帮手",true,()->{
            RuntimeClient.helperRequest(activity,target,"/v1/helpers/options",null,(options,error)->{
                if(error!=null){notice(error);return;}
                if(!Json.list(options,options.containsKey("removable")?"removable":"added").contains(name)){notice("此帮手用于主对话或当前电脑不支持删除");return;}
                Sheet.centered(activity,"删除 "+title+"？").message("此帮手将不再接收新任务。已有任务和旁聊记录保留。")
                    .action("删除帮手",true,()->RuntimeClient.helperRequest(activity,target,"/v1/helpers/"+name+"/remove",new LinkedHashMap<>(),(value,problem)->{
                        if(problem!=null){notice(problem);return;}saved.run();
                    })).action("取消",false,()->{}).show();
            });
        }).show(anchor);
    }
    void open() {
        if(target==null){notice("请先在小鱼账号中选择并连接电脑");return;}
        RuntimeClient.helperRequest(activity,target,"/v1/helpers/options",null,(options,error)->{
            if(activity.isFinishing())return;
            if(error!=null){notice(error);return;}
            if(options.containsKey("agents")){show(options);return;}
            // Older Runtime options lack the additive roster field.
            RuntimeClient.helperRequest(activity,target,"/v1/agents",null,(agents,problem)->{
                if(problem!=null){notice(problem);return;}
                options.put("agents",agents.get("agents"));show(options);
            });
        });
    }
    private void show(Map<String,Object> options) {
        Sheet sheet=Sheet.centered(activity,"帮手管理");
        sheet.message("管理 · "+target.name+"\n修改只影响这台电脑。已有任务和历史保留。");
        List<Object> removable=Json.list(options,options.containsKey("removable")?"removable":"added");
        int count=0;
        for(Object raw:Json.list(options,"agents")){
            Map<String,Object> agent=Json.object(raw);String name=Json.string(agent,"name");
            if(Boolean.TRUE.equals(agent.get("default")) || "mesh".equals(Json.string(agent,"type")))continue;
            count++;
            String model=Json.string(agent,"configured_model");
            // 名字用侧边栏里那个（cc 显示成 Claude），电脑上的配置名放在第二行。
            String shown=AppPresentation.name(AppPresentation.role(name));
            String sub=(shown.equalsIgnoreCase(name)?"":"配置名 "+name+" · ")+(model.isEmpty()?"默认模型":model);
            sheet.option(shown,sub,false,()->{
                Sheet details=Sheet.centered(activity,name).message("所在电脑 · "+target.name);
                if(removable.contains(name))details.option("移除帮手","不再接收新任务，已有任务继续完成",true,()->
                    new android.app.AlertDialog.Builder(activity).setTitle("移除 "+name+"？")
                        .setMessage("从 "+target.name+" 移除，已有任务和历史保留。")
                        .setNegativeButton("取消",null).setPositiveButton("移除",(dialog,which)->
                            mutate("/v1/helpers/"+name+"/remove",new LinkedHashMap<>())).show());
                else details.message("当前 Runtime 不支持移除此帮手，或它用于小鱼的主对话。");
                details.show();
            });
        }
        if(count==0)sheet.message("这台电脑还没有可管理的帮手。");
        // 以前移除过、电脑配置里还在的帮手：放在列表后面，写清楚是“加回来”。
        for(Object raw:Json.list(options,"restorable"))if(raw instanceof String){String name=(String)raw;
            sheet.option("加回 "+AppPresentation.name(AppPresentation.role(name)),"之前移除过，按电脑上原来的配置加回",false,()->{Map<String,Object> body=new LinkedHashMap<>();body.put("restore",name);mutate("/v1/helpers",body);});
        }
        sheet.action("添加帮手",true,()->add(options)).show();
    }
    private void mutate(String path,Map<String,Object> body){
        RuntimeClient.helperRequest(activity,target,path,body,(value,error)->{
            if(error!=null){notice(error);return;}
            saved.run();open();
        });
    }
    private void add(Map<String,Object> options){
        List<Object> harnesses=Json.list(options,"harnesses");
        if(harnesses.isEmpty()){notice("所选电脑没有提供添加选项");return;}
        Sheet sheet=Sheet.centered(activity,"添加帮手");
        sheet.message("添加到 · "+target.name);
        final Map<String,Object>[] chosen=new Map[]{Json.object(harnesses.get(0))};
        for(Object item:harnesses)if("codex".equals(Json.string(Json.object(item),"harness")))chosen[0]=Json.object(item);
        final String[] access={Json.list(chosen[0],"ways").contains("login")?"login":"key"};
        EditText name=field("帮手名称",false),model=field("留空使用默认模型",false),gateway=field("Magpie 地址",false),key=field("访问凭据",true);
        gateway.setText("http://127.0.0.1:3425");name.setText(Json.string(chosen[0],"harness").replace("_code",""));
        final String[] provider={"codex".equals(Json.string(chosen[0],"harness"))?"openai":"anthropic"};
        final String[] effort={""};
        TextView harness=choice(""),way=choice(""),service=choice(""),level=choice("努力程度 · 默认"),readModels=choice("读取模型");
        TextView hint=label(""),error=label("");error.setTextColor(0xffb3402f);error.setVisibility(View.GONE);
        Runnable update=()->{
            harness.setText("执行工具 · "+Json.string(chosen[0],"label"));
            way.setText("接入方式 · "+accessLabel(access[0]));
            service.setText("服务商 · "+provider[0]);
            boolean subscription="login".equals(access[0]), magpie="magpie".equals(access[0]);
            gateway.setVisibility(magpie?View.VISIBLE:View.GONE);readModels.setVisibility(magpie?View.VISIBLE:View.GONE);
            key.setVisibility(subscription?View.GONE:View.VISIBLE);service.setVisibility("key".equals(access[0])?View.VISIBLE:View.GONE);
            hint.setText(subscription?Json.string(chosen[0],"login_hint"):magpie?"地址由所选电脑访问。先读取模型，再选择。":"凭据仅保存在所选电脑，不写入手机设置。");
        };
        harness.setOnClickListener(view->{Sheet picker=Sheet.centered(activity,"选择执行工具");
            for(Object raw:harnesses){Map<String,Object> row=Json.object(raw);picker.option(Json.string(row,"label"),Boolean.TRUE.equals(row.get("installed"))?"已安装":"这台电脑还未安装",false,()->{
                chosen[0]=row;access[0]=Json.list(row,"ways").contains("login")?"login":"key";
                provider[0]="claude_code".equals(Json.string(row,"harness"))?"anthropic":"openai";
                name.setText(Json.string(row,"harness").replace("_code",""));key.setText("");model.setText("");effort[0]="";level.setText("努力程度 · 默认");update.run();});}
            picker.show();});
        way.setOnClickListener(view->{Sheet picker=Sheet.centered(activity,"接入方式");
            for(Object raw:Json.list(chosen[0],"ways"))if(raw instanceof String){String value=(String)raw;picker.option(accessLabel(value),null,false,()->{access[0]=value;key.setText("");model.setText("");update.run();});}picker.show();});
        service.setOnClickListener(view->{Sheet picker=Sheet.centered(activity,"选择服务商");
            for(Object raw:Json.list(chosen[0],"providers"))if(raw instanceof String && !"custom".equals(raw)){String value=(String)raw;picker.option(value,null,false,()->{provider[0]=value;key.setText("");update.run();});}picker.show();});
        level.setOnClickListener(view->{Sheet picker=Sheet.centered(activity,"努力程度");
            String[] values="dsh".equals(Json.string(chosen[0],"harness"))?new String[]{"","off","low","high"}:new String[]{"","low","medium","high"};
            for(String value:values)picker.option(value.isEmpty()?"默认":value,null,false,()->{effort[0]=value;level.setText("努力程度 · "+(value.isEmpty()?"默认":value));});picker.show();});
        readModels.setOnClickListener(view->{
            Map<String,Object> body=new LinkedHashMap<>();body.put("gateway_url",gateway.getText().toString().trim());
            String secret=key.getText().toString();if(!secret.isEmpty())body.put("key",secret);
            readModels.setEnabled(false);RuntimeClient.helperRequest(activity,target,"/v1/helpers/models",body,(value,problem)->{
                readModels.setEnabled(true);if(problem!=null){error.setText(problem);error.setVisibility(View.VISIBLE);return;}
                Sheet picker=Sheet.centered(activity,"选择模型");List<Object> models=Json.list(value,"models");
                if(models.isEmpty())picker.message("模型服务还没有可用模型。");
                for(Object raw:models){Map<String,Object> row=Json.object(raw);String id=Json.string(row,"id");picker.option(Json.string(row,"name"),id,false,()->model.setText(id));}picker.show();
            });
        });
        sheet.add(harness).add(way).add(hint).add(label("名称")).add(name).add(service).add(gateway).add(key).add(readModels).add(label("模型")).add(model).add(level).add(error);
        TextView submit=choice("添加");submit.setTextColor(Color.WHITE);submit.setGravity(Gravity.CENTER);submit.setBackground(round(BLUE));
        submit.setOnClickListener(view->{
            String helper=name.getText().toString().trim();
            if(!helper.matches("[A-Za-z0-9][A-Za-z0-9_-]{0,23}")){error.setText("名称请用字母、数字、下划线或连字符，最多 24 个字符");error.setVisibility(View.VISIBLE);return;}
            Map<String,Object> body=new LinkedHashMap<>();body.put("name",helper);body.put("harness",Json.string(chosen[0],"harness"));body.put("access",access[0]);
            String requested=model.getText().toString().trim();if(!requested.isEmpty())body.put("model",requested);
            if(!effort[0].isEmpty())body.put("effort",effort[0]);
            if("key".equals(access[0]))body.put("provider",provider[0]);
            if("magpie".equals(access[0]))body.put("gateway_url",gateway.getText().toString().trim());
            if(!"login".equals(access[0]) && !key.getText().toString().isEmpty())body.put("key",key.getText().toString());
            submit.setEnabled(false);submit.setText("正在添加…");error.setVisibility(View.GONE);
            RuntimeClient.helperRequest(activity,target,"/v1/helpers",body,(value,problem)->{
                submit.setEnabled(true);submit.setText("添加");key.setText("");
                if(problem!=null){error.setText(problem);error.setVisibility(View.VISIBLE);return;}
                sheet.dismiss();saved.run();
                String warning=Json.string(value,"problem");if(!warning.isEmpty())notice("已添加，但启动检查未通过："+warning);else open();
            });
        });
        sheet.add(submit);update.run();sheet.show();
    }
    private static String accessLabel(String value){return "login".equals(value)?"使用电脑的订阅登录":"key".equals(value)?"API Key":"模型服务";}
    private void notice(String text){if(!activity.isFinishing())Sheet.centered(activity,"帮手管理").message(text).show();}
    private TextView label(String value){TextView view=new TextView(activity);view.setText(value);view.setTextSize(13);view.setTextColor(HINT);view.setPadding(dp(8),dp(10),dp(8),dp(8));return view;}
    private TextView choice(String value){TextView view=label(value);view.setTextSize(15);view.setTextColor(INK);view.setMinimumHeight(dp(52));view.setGravity(Gravity.CENTER_VERTICAL);view.setPadding(dp(16),dp(12),dp(16),dp(12));view.setBackground(round(Color.WHITE));view.setFocusable(true);LinearLayout.LayoutParams p=new LinearLayout.LayoutParams(-1,-2);p.setMargins(0,dp(4),0,dp(4));view.setLayoutParams(p);return view;}
    private EditText field(String hint,boolean secret){EditText view=new EditText(activity);view.setHint(hint);view.setSingleLine(true);view.setTextSize(15);view.setTextColor(INK);view.setHintTextColor(HINT);view.setInputType(InputType.TYPE_CLASS_TEXT|(secret?InputType.TYPE_TEXT_VARIATION_PASSWORD:InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS));view.setPadding(dp(16),dp(12),dp(16),dp(12));view.setMinimumHeight(dp(52));view.setBackground(round(Color.WHITE));LinearLayout.LayoutParams p=new LinearLayout.LayoutParams(-1,-2);p.setMargins(0,dp(4),0,dp(4));view.setLayoutParams(p);return view;}
    private GradientDrawable round(int color){GradientDrawable shape=new GradientDrawable();shape.setColor(color);shape.setCornerRadius(dp(16));return shape;}
    private int dp(float value){return Math.round(value*activity.getResources().getDisplayMetrics().density);}
}
