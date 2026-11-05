package com.yanyuliu.pockethub;

import android.app.Activity;
import android.app.AlertDialog;
import android.app.Dialog;
import android.widget.Toast;
import java.util.List;
import java.util.Map;

/** A deliberately separate, read-only view of explicitly linked legacy conversations. */
final class LegacyArchivePane {
    static void show(Activity activity){
        LegacyArchiveController.Scope scope=LegacyArchiveController.scope(activity);
        if(scope==null){toast(activity,"请先登录并连接要关联的电脑");return;}
        menu(activity,scope);
    }
    private static void menu(Activity activity,LegacyArchiveController.Scope scope){
        if(!scope.current())return;
        Sheet sheet=new Sheet(activity,"旧聊天记录",false)
                .message("旧记录按关联时的快照单独保存并只读查看，之后的旧来源变化不会自动同步；当前聊天和原连接记录都保留。")
                .option("关联旧连接记录","选择与这台电脑相符的旧记录",false,()->sources(activity,scope))
                .option("查看已关联的旧聊天","按页浏览主对话",false,()->archives(activity,scope,false))
                .option("撤销旧记录关联","原记录和备份都会保留",false,()->archives(activity,scope,true));
        guarded(scope,sheet);
    }
    private static void sources(Activity activity,LegacyArchiveController.Scope scope){
        LegacyArchiveController.sources(scope,(items,error)->{
            if(error!=null){toast(activity,error);return;}
            Sheet sheet=new Sheet(activity,"选择旧连接",false);
            if(items==null || items.isEmpty())sheet.message("没有找到与这台电脑相符且记录完整的旧连接。原记录仍然保留。");
            else for(int i=0;i<items.size();i++){
                RuntimeClient.Target source=items.get(i);String name=source.name==null || source.name.isEmpty()?"旧连接 "+(i+1):source.name;
                sheet.option(name,"旧连接身份无法自动验证，请核对后选择",false,()->guarded(scope,new AlertDialog.Builder(activity)
                        .setTitle("关联这份旧记录？")
                        .setMessage("请核对你选择的旧连接。其身份无法自动验证；关联后供当前账号只读浏览。原记录不删除，也不会覆盖当前聊天。")
                        .setNegativeButton("取消",null).setPositiveButton("确认关联",(dialog,which)->{
                            if(!scope.current()){toast(activity,"账号或电脑已变化，请重新打开");return;}
                            toast(activity,"正在后台关联旧记录");
                            LegacyArchiveController.importSource(scope,source,(ok,issue)->{
                                if(issue!=null){toast(activity,issue);return;}
                                toast(activity,"旧记录已关联");archives(activity,scope,false);
                            });
                        }).create()));
            }
            guarded(scope,sheet);
        });
    }
    private static void archives(Activity activity,LegacyArchiveController.Scope scope,boolean undo){
        LegacyArchiveController.list(scope,(items,error)->{
            if(error!=null){toast(activity,error);return;}
            Sheet sheet=new Sheet(activity,undo?"撤销关联":"已关联的旧聊天",false);
            if(items==null || items.isEmpty())sheet.message("目前没有已关联的旧记录。");
            else for(int i=0;i<items.size();i++){
                LegacyArchiveStore.ArchiveRef ref=items.get(i);String label="旧记录 "+(i+1);
                sheet.option(label,undo?"撤销后仍保留原记录和备份":"只读浏览",undo,()->{
                    if(!undo){page(activity,scope,ref,-1);return;}
                    guarded(scope,new AlertDialog.Builder(activity).setTitle("撤销这份记录的关联？")
                            .setMessage("撤销后不再显示这份旧聊天；原连接记录和归档备份仍会保留。")
                            .setNegativeButton("取消",null).setPositiveButton("撤销关联",(dialog,which)->{
                                if(!scope.current())return;
                                LegacyArchiveController.rollback(scope,ref,(ok,issue)->toast(activity,issue==null?"已撤销关联":issue));
                            }).create());
                });
            }
            guarded(scope,sheet);
        });
    }
    private static void page(Activity activity,LegacyArchiveController.Scope scope,LegacyArchiveStore.ArchiveRef ref,long before){
        LegacyArchiveController.page(scope,ref,before,(page,error)->{
            if(error!=null){toast(activity,error);return;}
            Sheet sheet=new Sheet(activity,"旧聊天 · 只读",false);
            if(HubStore.get().archiveConversationDeleted("default")){sheet.message("当前对话已删除，旧记录不会显示。");guarded(scope,sheet);return;}
            java.util.Set<String> current=HubStore.get().archiveMessageIds();
            if(page==null || page.messages.isEmpty())sheet.message(page!=null && page.more?"这部分消息已在当前聊天中，可继续查看更早记录。":"没有更多旧消息。");
            else for(String json:page.messages){
                Map<String,Object> item=Json.parseObject(json);if(item==null)continue;
                if(current.contains(Json.string(item,"id")))continue;
                String text=Json.string(item,"text"),rawRole=Json.string(item,"role");
                String role=rawRole.equals("you")?"我":rawRole.equals("xiaoyou")?"小鱼":"其他";
                int attachments=(int)Json.number(item,"attachments_count");String id=Json.string(item,"id");
                String snippet=text.isEmpty()?(attachments>0?"图片或其他附件":"消息"):text.replace('\n',' ');
                if(snippet.length()>60)snippet=snippet.substring(0,60)+"…";
                sheet.option(role+" · "+snippet,attachments>0?"含 "+attachments+" 个附件":"点开查看",false,
                        ()->LegacyArchiveController.detail(scope,ref,id,(full,problem)->{
                            if(problem!=null){toast(activity,problem);return;}
                            if(HubStore.get().archiveConversationDeleted("default") || HubStore.get().archiveMessageIds().contains(id)){
                                toast(activity,"消息已在当前聊天中或已删除");return;
                            }
                            if(full==null){toast(activity,"旧消息暂时无法读取，原记录保留");return;}
                            detail(activity,scope,full.text,full.attachments,0);
                        }));
            }
            if(page!=null && page.more)sheet.option("查看更早的对话",null,false,()->page(activity,scope,ref,page.before));
            guarded(scope,sheet);
        });
    }
    private static void detail(Activity activity,LegacyArchiveController.Scope scope,String text,int attachments,int offset){
        if(!scope.current())return;
        int end=Math.min(text.length(),offset+4000);
        if(end<text.length() && end>offset && Character.isHighSurrogate(text.charAt(end-1)) && Character.isLowSurrogate(text.charAt(end)))end--;
        final int next=end;
        Sheet sheet=new Sheet(activity,"旧消息 · 只读",false).message(text.isEmpty()?"（无文字内容）":text.substring(offset,end));
        if(attachments>0)sheet.message("原消息含 "+attachments+" 个附件；此视图暂不打开附件，原记录仍保留。");
        if(end<text.length())sheet.option("继续查看",null,false,()->detail(activity,scope,text,attachments,next));
        guarded(scope,sheet);
    }
    private static void guarded(LegacyArchiveController.Scope scope,Dialog dialog){
        HubStore.Listener listener=()->{if(!scope.current())dialog.dismiss();};
        HubStore.get().addListener(listener);dialog.setOnDismissListener(ignored->HubStore.get().removeListener(listener));
        boolean[] shown={false};
        boolean accepted=AccountManager.installHistory(scope.generation,scope.account,scope.target.url,()->{
            if(scope.current()){dialog.show();shown[0]=true;}
        });
        if(!accepted || !shown[0])HubStore.get().removeListener(listener);
    }
    private static void toast(Activity activity,String text){Toast.makeText(activity,text,Toast.LENGTH_LONG).show();}
    private LegacyArchivePane(){}
}
