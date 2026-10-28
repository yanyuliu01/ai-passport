package com.yanyuliu.pockethub;

import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Matrix;
import android.media.ExifInterface;
import android.net.Uri;
import android.os.Handler;
import android.os.Looper;
import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.util.concurrent.Executors;

final class ImageUpload {
    interface Callback {
        void preview(Bitmap bitmap);
        void progress(String status);
        void ready(Attachment item, String error);
    }
    // One upload at a time: each relayed request has a 30 s deadline, and parallel bodies share the uplink.
    private static final java.util.concurrent.ExecutorService WORK = Executors.newSingleThreadExecutor();
    private static final Handler UI = new Handler(Looper.getMainLooper());
    /** Matches the sent bubble's decode size, so the same bytes serve the draft and the bubble. */
    private static final int PREVIEW_EDGE = 900;

    static final class Task implements RuntimeClient.UploadProgress {
        volatile boolean cancelled;
        private volatile HttpURLConnection connection;
        private final Callback callback;
        private volatile boolean finished;
        private int percent = -1;
        private final Runnable timeout;
        Task(Callback callback) {
            this.callback = callback;
            timeout = () -> {
                if (cancelled || finished) return;
                cancel(); callback.ready(null, "上传超时，请检查连接后重试");
            };
        }
        void cancel() {
            cancelled = true;
            UI.removeCallbacks(timeout);
            HttpURLConnection active = connection;
            if (active != null) new Thread(active::disconnect, "image-upload-cancel").start();
        }
        void check() throws IOException { if (cancelled) throw new IOException("已取消"); }
        public void connection(HttpURLConnection value) throws IOException { connection = value; check(); }
        public void sent(int bytes, int total) throws IOException {
            check();
            int next = (int) ((long) bytes * 100 / total);
            if (next == percent) return;
            percent = next;
            status(next == 100 ? "正在等待电脑确认…" : "上传中 " + next + "%");
        }
        void status(String text) { UI.post(() -> { if (!cancelled) callback.progress(text); }); }
    }

    static Task start(Context context, Uri uri, RuntimeClient.Target target, Callback callback) {
        Context app = context.getApplicationContext();
        Task task = new Task(callback);
        WORK.execute(() -> {
            // 几张图排队传：超时从真正开始传这张时算，排队等的不算。
            UI.postDelayed(task.timeout, 120000);
            Attachment item = null; String error = null;
            try {
                task.check();
                ByteArrayOutputStream original = new ByteArrayOutputStream();
                try (InputStream input = app.getContentResolver().openInputStream(uri)) {
                    if (input == null) throw new IOException("无法打开所选图片");
                    byte[] buffer = new byte[8192]; int count;
                    while ((count = input.read(buffer)) != -1) {
                        task.check();
                        if (original.size() + count > ImagePreparation.SOURCE_BYTES) throw new IOException("图片超过 40 MB，请换一张");
                        original.write(buffer, 0, count);
                    }
                }
                byte[] source = original.toByteArray();
                BitmapFactory.Options bounds = new BitmapFactory.Options(); bounds.inJustDecodeBounds = true;
                BitmapFactory.decodeByteArray(source, 0, source.length, bounds);
                if (bounds.outWidth <= 0 || bounds.outHeight <= 0) throw new IOException("无法读取这张图片");
                if ((long) bounds.outWidth * bounds.outHeight > ImagePreparation.SOURCE_PIXELS) throw new IOException("图片尺寸过大，请换一张");
                int orientation = ExifInterface.ORIENTATION_NORMAL;
                try { orientation = new ExifInterface(new ByteArrayInputStream(source)).getAttributeInt(
                        ExifInterface.TAG_ORIENTATION, ExifInterface.ORIENTATION_NORMAL); }
                catch (IOException ignored) { /* Formats without EXIF still have a preview. */ }
                Bitmap preview = decode(source, bounds, PREVIEW_EDGE, orientation);
                ByteArrayOutputStream thumb = new ByteArrayOutputStream();
                preview.compress(Bitmap.CompressFormat.JPEG, 85, thumb);
                task.check();
                UI.post(() -> { if (!task.cancelled) callback.preview(preview); else preview.recycle(); });
                // The relay checks the declared type, so send the real one; anything it would refuse
                // (HEIC, GIF, large photos, huge screenshots) becomes a JPEG small enough to cross it.
                String mime = bounds.outMimeType;
                if (!ImagePreparation.keepOriginal(mime, source.length, bounds.outWidth, bounds.outHeight)) {
                    task.status("正在优化照片…");
                    source = compact(source, bounds, orientation, task); mime = "image/jpeg";
                }
                task.check();
                item = RuntimeClient.uploadImage(target, source, mime, task);
                if (item != null) ArtifactClient.seed(app, target, item, source, thumb.toByteArray());
            } catch (OutOfMemoryError exhausted) { error = "手机内存不足，请选择更小的图片";
            } catch (Exception failure) { error = failure.getMessage(); }
            Attachment result = item; String detail = error;
            task.connection = null; task.finished = true; UI.removeCallbacks(task.timeout);
            UI.post(() -> { if (!task.cancelled) callback.ready(result, detail); });
        });
        return task;
    }

    /** JPEG under the relay limit: lower quality first, then shrink; transparent areas become white. */
    private static byte[] compact(byte[] source, BitmapFactory.Options bounds, int orientation, Task task) throws IOException {
        int[] size = ImagePreparation.fit(bounds.outWidth, bounds.outHeight);
        for (int attempt = 0; attempt < 4; attempt++) {
            Bitmap photo = decode(source, bounds, Math.max(size[0], size[1]), orientation);
            Bitmap flat = photo;
            if (photo.hasAlpha()) {
                flat = Bitmap.createBitmap(photo.getWidth(), photo.getHeight(), Bitmap.Config.ARGB_8888);
                Canvas canvas = new Canvas(flat); canvas.drawColor(Color.WHITE); canvas.drawBitmap(photo, 0, 0, null);
                photo.recycle();
            }
            try {
                for (int quality : new int[]{88, 78, 68}) {
                    task.check();
                    ByteArrayOutputStream out = new ByteArrayOutputStream();
                    flat.compress(Bitmap.CompressFormat.JPEG, quality, out);
                    if (out.size() <= ImagePreparation.RELAY_BYTES) return out.toByteArray();
                }
            } finally { flat.recycle(); }
            size = new int[]{Math.max(1, size[0] * 4 / 5), Math.max(1, size[1] * 4 / 5)};
        }
        throw new IOException("图片压缩后仍然太大，请换一张");
    }

    private static Bitmap decode(byte[] bytes, BitmapFactory.Options bounds, int edge, int orientation) throws IOException {
        BitmapFactory.Options options = new BitmapFactory.Options();
        options.inSampleSize = ImagePreparation.sample(bounds.outWidth, bounds.outHeight, edge);
        Bitmap bitmap = BitmapFactory.decodeByteArray(bytes, 0, bytes.length, options);
        if (bitmap == null) throw new IOException("无法读取图片预览");
        int[] size = ImagePreparation.size(bitmap.getWidth(), bitmap.getHeight(), edge);
        Bitmap scaled = Bitmap.createScaledBitmap(bitmap, size[0], size[1], true);
        if (scaled != bitmap) bitmap.recycle();
        Matrix transform = new Matrix();
        switch (orientation) {
            case ExifInterface.ORIENTATION_FLIP_HORIZONTAL: transform.setScale(-1, 1); break;
            case ExifInterface.ORIENTATION_ROTATE_180: transform.setRotate(180); break;
            case ExifInterface.ORIENTATION_FLIP_VERTICAL: transform.setScale(1, -1); break;
            case ExifInterface.ORIENTATION_TRANSPOSE: transform.setRotate(90); transform.postScale(-1, 1); break;
            case ExifInterface.ORIENTATION_ROTATE_90: transform.setRotate(90); break;
            case ExifInterface.ORIENTATION_TRANSVERSE: transform.setRotate(270); transform.postScale(-1, 1); break;
            case ExifInterface.ORIENTATION_ROTATE_270: transform.setRotate(270); break;
        }
        Bitmap oriented = Bitmap.createBitmap(scaled, 0, 0, scaled.getWidth(), scaled.getHeight(), transform, true);
        if (oriented != scaled) scaled.recycle();
        return oriented;
    }
}
