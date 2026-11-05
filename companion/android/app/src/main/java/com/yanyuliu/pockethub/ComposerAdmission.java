package com.yanyuliu.pockethub;

/** Preserve newer edits and drafts belonging to a different conversation or account. */
final class ComposerAdmission {
    static boolean mayClear(boolean persisted,String originalContext,String currentContext,String sentText,String currentText){
        return persisted && originalContext.equals(currentContext) && sentText.equals(currentText);
    }
    private ComposerAdmission(){}
}
