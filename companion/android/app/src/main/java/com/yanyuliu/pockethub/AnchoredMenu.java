package com.yanyuliu.pockethub;

import android.app.Activity;
import android.graphics.Color;
import android.graphics.drawable.GradientDrawable;
import android.content.res.ColorStateList;
import android.graphics.drawable.RippleDrawable;
import android.view.Gravity;
import android.view.View;
import android.widget.LinearLayout;
import android.widget.PopupWindow;
import android.widget.TextView;

/**
 * Compact context actions anchored to the pressed row; no sheet or slide animation.
 * The row stays visible and tinted while the menu is open; danger actions sit last, after a divider.
 * Only one menu is open at a time, and it closes when its row leaves the window.
 */
final class AnchoredMenu {
    private static AnchoredMenu open;
    private final Activity activity;
    private final LinearLayout rows;
    private final PopupWindow popup;
    private boolean divided;
    private final java.util.Map<String,Runnable> afterClose=new java.util.LinkedHashMap<>();
    AnchoredMenu(Activity activity) {
        this.activity=activity;rows=new LinearLayout(activity);rows.setOrientation(LinearLayout.VERTICAL);rows.setPadding(dp(6),dp(6),dp(6),dp(6));
        rows.setBackground(round(Color.WHITE));
        popup=new PopupWindow(rows,Math.min(dp(208),activity.getResources().getDisplayMetrics().widthPixels-dp(32)),-2,true);
        popup.setBackgroundDrawable(round(Color.WHITE));popup.setElevation(dp(12));popup.setOutsideTouchable(true);popup.setClippingEnabled(true);popup.setAnimationStyle(0);
    }
    AnchoredMenu option(String title,boolean danger,Runnable action){
        if(danger&&!divided&&rows.getChildCount()>0){
            View line=new View(activity);line.setBackgroundColor(0xffe7ecf2);
            LinearLayout.LayoutParams params=new LinearLayout.LayoutParams(-1,Math.max(1,dp(1)));params.setMargins(dp(10),dp(4),dp(10),dp(4));rows.addView(line,params);divided=true;
        }
        TextView row=new TextView(activity);row.setText(title);row.setTextSize(15);row.setTextColor(danger?0xffb3402f:0xff1f3149);row.setGravity(Gravity.CENTER_VERTICAL);
        row.setPadding(dp(16),0,dp(16),0);row.setMinimumHeight(dp(46));row.setFocusable(true);
        row.setBackground(new RippleDrawable(ColorStateList.valueOf(0x14000000),round(Color.WHITE),null));
        row.setOnClickListener(v->{popup.dismiss();action.run();});rows.addView(row,new LinearLayout.LayoutParams(-1,dp(46)));return this;
    }
    void show(View anchor){
        if(activity.isFinishing()||!anchor.isAttachedToWindow())return;
        dismissOpen();open=this;
        anchor.setForeground(round(0x142f6fe0));
        View.OnAttachStateChangeListener detached=new View.OnAttachStateChangeListener(){
            @Override public void onViewAttachedToWindow(View view){}
            @Override public void onViewDetachedFromWindow(View view){popup.dismiss();}
        };
        anchor.addOnAttachStateChangeListener(detached);
        popup.setOnDismissListener(()->{
            anchor.setForeground(null);anchor.removeOnAttachStateChangeListener(detached);if(open==this)open=null;
            java.util.List<Runnable> next=new java.util.ArrayList<>(afterClose.values());afterClose.clear();for(Runnable action:next)action.run();
        });
        // Below the row, right-aligned; PopupWindow flips it above when the row is near the bottom.
        popup.showAsDropDown(anchor,-dp(8),dp(4),Gravity.END);
    }
    static boolean showing(){return open!=null&&open.popup.isShowing();}
    /** Runs once after the open menu closes; used to defer list rebuilds that would pull the row out from under it. One per key. */
    static void afterClose(String key,Runnable action){if(open!=null)open.afterClose.put(key,action);}
    static void dismissOpen(){if(open!=null)open.popup.dismiss();}
    private GradientDrawable round(int color){GradientDrawable shape=new GradientDrawable();shape.setColor(color);shape.setCornerRadius(dp(14));return shape;}
    private int dp(float value){return Math.round(value*activity.getResources().getDisplayMetrics().density);}
}
