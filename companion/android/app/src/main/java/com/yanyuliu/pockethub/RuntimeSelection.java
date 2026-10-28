package com.yanyuliu.pockethub;
import java.util.List;

/** A manual recipient is account-scoped and never replaced by discovery or a failed route. */
final class RuntimeSelection {
    static String preferenceKey(String account) { return "runtime-choice:"+account; }
    static String resolve(String preferred,String previous,List<String> available) {
        if(!preferred.isEmpty())return available.contains(preferred)?preferred:null;
        if(previous!=null && available.contains(previous))return previous;
        return available.isEmpty()?null:available.get(0);
    }
    static boolean mayFallback(String preferred) {return preferred.isEmpty();}
}
