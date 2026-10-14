package com.yanyuliu.pockethub;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Paint;
import android.graphics.Path;
import android.graphics.RectF;
import android.view.View;

/** Small code-native line icons; selected state is also expressed by the navigation label. */
final class NavIconView extends View {
    private final String icon;
    private final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private int color = 0xff93a1b3;
    NavIconView(Context context, String icon) {
        super(context);
        this.icon = icon;
        setImportantForAccessibility(IMPORTANT_FOR_ACCESSIBILITY_NO);
    }
    void color(int color) { this.color = color; invalidate(); }
    @Override protected void onDraw(Canvas canvas) {
        float unit = Math.min(getWidth(), getHeight()) / 24f;
        canvas.save();
        canvas.translate((getWidth() - 24 * unit) / 2, (getHeight() - 24 * unit) / 2);
        canvas.scale(unit, unit);
        paint.setColor(color); paint.setStyle(Paint.Style.STROKE); paint.setStrokeWidth(1.7f);
        paint.setStrokeCap(Paint.Cap.ROUND); paint.setStrokeJoin(Paint.Join.ROUND);
        if (icon.equals("chat")) {
            canvas.drawRoundRect(new RectF(3, 4, 21, 19), 5, 5, paint);
            canvas.drawLine(4, 18, 3, 22, paint);
            canvas.drawLine(7, 9, 16, 9, paint); canvas.drawLine(7, 13, 13, 13, paint);
        } else if (icon.equals("tasks")) {
            canvas.drawRoundRect(new RectF(4, 4, 20, 22), 4, 4, paint);
            canvas.drawRoundRect(new RectF(8, 2, 16, 7), 2, 2, paint);
            canvas.drawLine(8, 12, 10, 14, paint); canvas.drawLine(10, 14, 13, 10, paint);
            canvas.drawLine(8, 18, 16, 18, paint);
        } else if (icon.equals("mine")) {
            canvas.drawCircle(12, 7, 4, paint);
            canvas.drawArc(new RectF(4, 13, 20, 29), 180, 180, false, paint);
        } else if (icon.equals("bell")) {
            Path path = new Path(); path.moveTo(5, 17); path.lineTo(7, 13); path.lineTo(7, 9);
            path.cubicTo(7, 2, 17, 2, 17, 9); path.lineTo(17, 13); path.lineTo(19, 17); path.close();
            canvas.drawPath(path, paint); canvas.drawLine(10, 21, 14, 21, paint);
        } else if (icon.equals("menu")) {
            canvas.drawLine(4, 7, 20, 7, paint); canvas.drawLine(4, 12, 15, 12, paint); canvas.drawLine(4, 17, 20, 17, paint);
        } else if (icon.equals("more")) {
            paint.setStyle(Paint.Style.FILL);
            canvas.drawCircle(5.5f, 12, 1.7f, paint); canvas.drawCircle(12, 12, 1.7f, paint); canvas.drawCircle(18.5f, 12, 1.7f, paint);
        } else if (icon.equals("send")) {
            paint.setStrokeWidth(2.2f);
            canvas.drawLine(12, 19, 12, 5, paint); canvas.drawLine(6, 11, 12, 5, paint); canvas.drawLine(18, 11, 12, 5, paint);
        } else if (icon.equals("pin")) {
            Path path = new Path(); path.moveTo(9, 4); path.lineTo(15, 4); path.lineTo(14, 10); path.lineTo(18, 13);
            path.lineTo(6, 13); path.lineTo(10, 10); path.close();
            canvas.drawPath(path, paint); canvas.drawLine(12, 13, 12, 20, paint);
        } else if (icon.equals("gear")) {
            canvas.drawCircle(12, 12, 3, paint);
            for (int i = 0; i < 8; i++) {
                double angle = Math.PI / 4 * i;
                canvas.drawLine(12 + 6 * (float) Math.cos(angle), 12 + 6 * (float) Math.sin(angle),
                        12 + 9 * (float) Math.cos(angle), 12 + 9 * (float) Math.sin(angle), paint);
            }
        } else if (icon.equals("device")) {
            canvas.drawRoundRect(new RectF(5, 3, 19, 21), 4, 4, paint);
            canvas.drawRoundRect(new RectF(8, 6, 16, 12), 1.5f, 1.5f, paint);
        } else if (icon.equals("computer")) {
            canvas.drawRoundRect(new RectF(3.5f, 4.5f, 20.5f, 15.5f), 2, 2, paint);
            canvas.drawLine(2, 19, 22, 19, paint);
        } else {
            canvas.drawLine(12, 5, 12, 19, paint); canvas.drawLine(5, 12, 19, 12, paint);
        }
        canvas.restore();
    }
}
