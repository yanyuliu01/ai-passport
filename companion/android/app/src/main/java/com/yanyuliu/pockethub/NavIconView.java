package com.yanyuliu.pockethub;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Paint;
import android.graphics.Path;
import android.graphics.RectF;
import android.view.View;

/** Small code-native line icons; selected state is also expressed by the navigation label. */
final class NavIconView extends View {
    private String icon;
    private final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private int color = 0xff93a1b3;
    NavIconView(Context context, String icon) {
        super(context);
        this.icon = icon;
        setImportantForAccessibility(IMPORTANT_FOR_ACCESSIBILITY_NO);
    }
    void color(int color) { this.color = color; invalidate(); }
    void icon(String icon) { this.icon = icon; invalidate(); }
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
        } else if (icon.equals("mail")) {
            canvas.drawRoundRect(new RectF(3, 6, 21, 18), 2.5f, 2.5f, paint);
            canvas.drawLine(4, 7, 12, 13, paint); canvas.drawLine(20, 7, 12, 13, paint);
        } else if (icon.equals("spinner")) {
            paint.setStrokeWidth(2.2f);
            paint.setAlpha(60); canvas.drawCircle(12, 12, 9, paint);
            paint.setAlpha(255); canvas.drawArc(new RectF(3, 3, 21, 21), -90, 100, false, paint);
        } else if (icon.equals("keyboard")) {
            canvas.drawRoundRect(new RectF(3, 6, 21, 18), 2.5f, 2.5f, paint);
            canvas.drawLine(7, 10, 8, 10, paint); canvas.drawLine(11, 10, 12, 10, paint); canvas.drawLine(15, 10, 16, 10, paint);
            canvas.drawLine(8, 14, 16, 14, paint);
        } else if (icon.equals("mic")) {
            paint.setStrokeWidth(1.9f);
            canvas.drawRoundRect(new RectF(9, 3, 15, 14), 3, 3, paint);
            canvas.drawArc(new RectF(5.5f, 4.5f, 18.5f, 17.5f), 0, 180, false, paint);
            canvas.drawLine(12, 17.5f, 12, 21, paint);
        } else if (icon.equals("close")) {
            canvas.drawLine(6, 6, 18, 18, paint); canvas.drawLine(18, 6, 6, 18, paint);
        } else if (icon.equals("copy")) {
            canvas.drawRoundRect(new RectF(8, 8, 20, 20), 2.5f, 2.5f, paint);
            Path back = new Path(); back.moveTo(16, 5); back.lineTo(16, 4); back.cubicTo(16, 4, 16, 4, 15, 4);
            back.lineTo(6, 4); back.cubicTo(5, 4, 4, 5, 4, 6); back.lineTo(4, 15); back.cubicTo(4, 16, 5, 16, 5, 16);
            canvas.drawPath(back, paint);
        } else if (icon.equals("quote")) {
            // 引用：一对实心的前引号。
            Paint.Style was = paint.getStyle(); paint.setStyle(Paint.Style.FILL);
            for (float left : new float[] {5, 13}) {
                canvas.drawCircle(left + 3, 13, 3, paint);
                Path tail = new Path(); tail.moveTo(left, 13); tail.quadTo(left, 7, left + 5, 6); tail.lineTo(left + 5, 7.6f);
                tail.quadTo(left + 2.2f, 8.6f, left + 2.4f, 12); tail.close(); canvas.drawPath(tail, paint);
            }
            paint.setStyle(was);
        } else if (icon.equals("retry")) {
            canvas.drawArc(new RectF(5, 5, 19, 19), -60, 300, false, paint);
            canvas.drawLine(18.5f, 4, 18.5f, 8.5f, paint); canvas.drawLine(18.5f, 8.5f, 14, 8.5f, paint);
        } else if (icon.equals("history")) {
            canvas.drawCircle(12, 12, 8.5f, paint);
            canvas.drawLine(12, 7.5f, 12, 12, paint); canvas.drawLine(12, 12, 15, 14, paint);
        } else if (icon.equals("back")) {
            canvas.drawLine(15, 5, 8, 12, paint); canvas.drawLine(8, 12, 15, 19, paint);
        } else if (icon.equals("chevron")) {
            canvas.drawLine(9, 6, 15, 12, paint); canvas.drawLine(15, 12, 9, 18, paint);
        } else if (icon.equals("menu")) {
            canvas.drawLine(4, 7, 20, 7, paint); canvas.drawLine(4, 12, 15, 12, paint); canvas.drawLine(4, 17, 20, 17, paint);
        } else if (icon.equals("more")) {
            paint.setStyle(Paint.Style.FILL);
            canvas.drawCircle(5.5f, 12, 1.7f, paint); canvas.drawCircle(12, 12, 1.7f, paint); canvas.drawCircle(18.5f, 12, 1.7f, paint);
        } else if (icon.equals("camera")) {
            Path body = new Path();
            body.moveTo(3, 9); body.quadTo(3, 7, 5, 7); body.lineTo(8, 7); body.lineTo(9.5f, 4.5f);
            body.lineTo(14.5f, 4.5f); body.lineTo(16, 7); body.lineTo(19, 7); body.quadTo(21, 7, 21, 9);
            body.lineTo(21, 18); body.quadTo(21, 20, 19, 20); body.lineTo(5, 20); body.quadTo(3, 20, 3, 18); body.close();
            canvas.drawPath(body, paint);
            canvas.drawCircle(12, 13.5f, 3.6f, paint);
        } else if (icon.equals("image")) {
            canvas.drawRoundRect(new RectF(3, 3, 21, 21), 3, 3, paint);
            canvas.drawCircle(8, 8, 1.5f, paint);
            Path mountains = new Path(); mountains.moveTo(4, 18); mountains.lineTo(10, 12);
            mountains.lineTo(13, 15); mountains.lineTo(16, 11); mountains.lineTo(21, 17);
            canvas.drawPath(mountains, paint);
        } else if (icon.equals("send")) {
            paint.setStrokeWidth(2.2f);
            canvas.drawLine(12, 19, 12, 5, paint); canvas.drawLine(6, 11, 12, 5, paint); canvas.drawLine(18, 11, 12, 5, paint);
        } else if (icon.equals("stop")) {
            paint.setStyle(Paint.Style.FILL);
            canvas.drawRoundRect(new RectF(7, 7, 17, 17), 2, 2, paint);
        } else if (icon.equals("pin")) {
            Path path = new Path(); path.moveTo(9, 4); path.lineTo(15, 4); path.lineTo(14, 10); path.lineTo(18, 13);
            path.lineTo(6, 13); path.lineTo(10, 10); path.close();
            canvas.drawPath(path, paint); canvas.drawLine(12, 13, 12, 20, paint);
        } else if (icon.equals("trash")) {
            canvas.drawLine(4,6,20,6,paint);canvas.drawLine(9,3,15,3,paint);
            canvas.drawRoundRect(new RectF(6,6,18,21),2,2,paint);canvas.drawLine(10,10,10,17,paint);canvas.drawLine(14,10,14,17,paint);
        } else if (icon.equals("archive")) {
            canvas.drawRoundRect(new RectF(3,4,21,9),1,1,paint);canvas.drawRoundRect(new RectF(5,9,19,21),1,1,paint);canvas.drawLine(10,13,14,13,paint);
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
        } else if (icon.equals("info")) {
            canvas.drawCircle(12, 12, 9, paint);
            canvas.drawLine(12, 11, 12, 16.5f, paint);
            paint.setStyle(Paint.Style.FILL); canvas.drawCircle(12, 7.8f, 1.1f, paint);
        } else if (icon.equals("unchecked")) {
            paint.setColor(0xffb7c2d0); canvas.drawCircle(12, 12, 8.5f, paint);
        } else if (icon.equals("checked")) {
            paint.setStyle(Paint.Style.FILL); canvas.drawCircle(12, 12, 9.3f, paint);
            paint.setStyle(Paint.Style.STROKE); paint.setColor(0xffffffff); paint.setStrokeWidth(2f);
            Path path = new Path(); path.moveTo(8, 12.3f); path.lineTo(11, 15.2f); path.lineTo(16.2f, 9.4f); canvas.drawPath(path, paint);
        } else if (icon.equals("computer")) {
            canvas.drawRoundRect(new RectF(3.5f, 4.5f, 20.5f, 15.5f), 2, 2, paint);
            canvas.drawLine(2, 19, 22, 19, paint);
        } else {
            canvas.drawLine(12, 5, 12, 19, paint); canvas.drawLine(5, 12, 19, 12, paint);
        }
        canvas.restore();
    }
}
