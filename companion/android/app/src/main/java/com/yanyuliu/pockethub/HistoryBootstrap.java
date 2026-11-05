package com.yanyuliu.pockethub;

/** Preserve unsaved in-memory messages while the first authorized archive is discovered. */
final class HistoryBootstrap {
    static final class Plan {
        final String previousKey; final ChatHistory previous,current; final boolean merged;
        Plan(String previousKey,ChatHistory previous,ChatHistory current,boolean merged){
            this.previousKey=previousKey;this.previous=previous;this.current=current;this.merged=merged;
        }
    }
    static Plan plan(boolean loaded,String previousKey,ChatHistory previous,String target,ChatHistory candidate){
        boolean same=loaded && previousKey.equals(target);
        ChatHistory old=loaded && !same && !previousKey.isEmpty()?previous.copy():null;
        ChatHistory current=candidate;
        if(same)current.overlay(previous);
        return new Plan(old==null?"":previousKey,old,current,same);
    }
    /** Merge early in-memory edits into the existing archive, preserving its older rows. */
    static ChatHistory merge(String stored,ChatHistory early){
        if(stored!=null){
            java.util.Map<String,Object> root=Json.parseObject(stored);
            if(root==null || !(root.get("messages") instanceof java.util.List))
                throw new IllegalArgumentException("原历史格式无效");
            java.util.Set<String> ids=new java.util.HashSet<>();
            for(Object raw:Json.list(root,"messages")){
                if(!(raw instanceof java.util.Map))throw new IllegalArgumentException("原历史消息无效");
                java.util.Map<String,Object> row=Json.object(raw);String id=Json.string(row,"id");
                if(id.isEmpty() || !ids.add(id))throw new IllegalArgumentException("原历史编号无效");
                if(row.containsKey("attachments")){
                    if(!(row.get("attachments") instanceof java.util.List))throw new IllegalArgumentException("原历史附件无效");
                    for(Object item:Json.list(row,"attachments"))
                        if(!(item instanceof java.util.Map) || Attachment.from(Json.object(item))==null)
                            throw new IllegalArgumentException("原历史附件无效");
                }
                if(row.containsKey("presentation") && row.get("presentation")!=null && !(row.get("presentation") instanceof java.util.Map))
                    throw new IllegalArgumentException("原历史显示信息无效");
            }
            if(root.containsKey("erased")){
                if(!(root.get("erased") instanceof java.util.List))throw new IllegalArgumentException("原历史删除记录无效");
                for(Object id:Json.list(root,"erased"))if(!(id instanceof String))throw new IllegalArgumentException("原历史删除记录无效");
            }
        }
        ChatHistory value=ChatHistory.restore(stored==null?"{}":stored);
        value.overlay(early);
        return value;
    }
    private HistoryBootstrap(){}
}
