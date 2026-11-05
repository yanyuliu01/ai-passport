package com.yanyuliu.pockethub;

import java.util.function.BooleanSupplier;

/** Merge a validated alias without ever modifying or removing its source. */
final class AliasHistory {
    static boolean adopt(String source,ChatHistory target,BooleanSupplier persist){
        ChatHistory valid=HistoryBootstrap.merge(source,new ChatHistory());
        // The current archive owns deletion decisions and conflicting message IDs.
        if(target.importLegacy(valid).added==0)return true;
        return persist.getAsBoolean();
    }
    private AliasHistory(){}
}
