package com.yanyuliu.pockethub;

import android.content.Context;
import android.content.pm.PackageManager;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.Rect;
import android.os.SystemClock;
import android.view.Gravity;
import android.view.View;
import android.widget.FrameLayout;
import android.widget.TextView;
import com.journeyapps.barcodescanner.CaptureActivity;
import com.journeyapps.barcodescanner.DecoratedBarcodeView;
import com.journeyapps.barcodescanner.Size;

/** Portrait, QR-only camera screen. CaptureManager owns permission and camera lifecycle. */
public final class PairingScanActivity extends CaptureActivity {
    private boolean torch;
    private int dp(float n){return Math.round(n*getResources().getDisplayMetrics().density);}
    private TextView label(String text,int size){TextView v=new TextView(this);v.setText(text);v.setTextSize(size);v.setTextColor(Color.WHITE);v.setGravity(Gravity.CENTER);return v;}
    @Override protected DecoratedBarcodeView initializeContent() {
        getWindow().setStatusBarColor(Color.BLACK);getWindow().setNavigationBarColor(Color.BLACK);
        FrameLayout root=new FrameLayout(this);root.setBackgroundColor(Color.BLACK);root.setFitsSystemWindows(true);setContentView(root);
        DecoratedBarcodeView scanner=new DecoratedBarcodeView(this);
        scanner.getStatusView().setVisibility(View.GONE);scanner.getViewFinder().setVisibility(View.GONE);
        scanner.getBarcodeView().setFramingRectSize(new Size(dp(260),dp(260)));
        root.addView(scanner,new FrameLayout.LayoutParams(-1,-1));
        root.addView(new ScanFrame(this,scanner),new FrameLayout.LayoutParams(-1,-1));
        TextView title=label("扫一扫",19);FrameLayout.LayoutParams heading=new FrameLayout.LayoutParams(-1,dp(60),Gravity.TOP);root.addView(title,heading);
        TextView back=label("‹",36);back.setContentDescription("返回");FrameLayout.LayoutParams exit=new FrameLayout.LayoutParams(dp(64),dp(60),Gravity.TOP|Gravity.START);root.addView(back,exit);back.setOnClickListener(v->finish());
        TextView hint=label("将电脑上的二维码放入框内",15);FrameLayout.LayoutParams note=new FrameLayout.LayoutParams(-1,dp(56),Gravity.BOTTOM);note.bottomMargin=dp(134);root.addView(hint,note);
        if(getPackageManager().hasSystemFeature(PackageManager.FEATURE_CAMERA_FLASH)) {
            TextView light=label("手电筒",16);FrameLayout.LayoutParams button=new FrameLayout.LayoutParams(dp(140),dp(64),Gravity.BOTTOM|Gravity.CENTER_HORIZONTAL);button.bottomMargin=dp(54);root.addView(light,button);
            light.setOnClickListener(v->{torch=!torch;if(torch)scanner.setTorchOn();else scanner.setTorchOff();light.setText(torch?"关闭手电筒":"手电筒");});
        }
        return scanner;
    }
    private final class ScanFrame extends View {
        private final DecoratedBarcodeView scanner;
        private final Paint paint=new Paint(Paint.ANTI_ALIAS_FLAG);
        ScanFrame(Context context,DecoratedBarcodeView scanner){super(context);this.scanner=scanner;setImportantForAccessibility(IMPORTANT_FOR_ACCESSIBILITY_NO);}
        @Override protected void onDraw(Canvas canvas){
            Rect frame=scanner.getBarcodeView().getFramingRect();
            if(frame==null){int size=Math.min(dp(260),getWidth()-dp(48));frame=new Rect((getWidth()-size)/2,(getHeight()-size)/2,(getWidth()+size)/2,(getHeight()+size)/2);}
            paint.setStyle(Paint.Style.FILL);paint.setColor(0x99000000);
            canvas.drawRect(0,0,getWidth(),frame.top,paint);canvas.drawRect(0,frame.bottom,getWidth(),getHeight(),paint);
            canvas.drawRect(0,frame.top,frame.left,frame.bottom,paint);canvas.drawRect(frame.right,frame.top,getWidth(),frame.bottom,paint);
            paint.setColor(0xff07c160);paint.setStrokeWidth(dp(3));int corner=dp(22);
            for(int x:new int[]{frame.left,frame.right})for(int y:new int[]{frame.top,frame.bottom}){
                canvas.drawLine(x,y,x+(x==frame.left?corner:-corner),y,paint);canvas.drawLine(x,y,x,y+(y==frame.top?corner:-corner),paint);
            }
            float y=frame.top+(frame.height()*(SystemClock.uptimeMillis()%2400)/2400f);
            paint.setStrokeWidth(dp(1));paint.setAlpha(180);canvas.drawLine(frame.left+dp(3),y,frame.right-dp(3),y,paint);paint.setAlpha(255);
            if(isShown()&&getWindowVisibility()==VISIBLE)postInvalidateDelayed(32);
        }
    }
}
