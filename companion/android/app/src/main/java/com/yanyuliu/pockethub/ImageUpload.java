package com.yanyuliu.pockethub;
import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.net.Uri;
import android.os.Handler;
import android.os.Looper;
import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.util.concurrent.Executors;
final class ImageUpload {
    interface Callback { void ready(Attachment item, String error); }
    private static final java.util.concurrent.ExecutorService WORK = Executors.newSingleThreadExecutor();
    static void start(Context context, Uri uri, RuntimeClient.Target target, Callback callback) {
        Context app = context.getApplicationContext();
        WORK.execute(() -> {
            Attachment item = null; String error = null;
            try {
                ByteArrayOutputStream original = new ByteArrayOutputStream();
                try (InputStream input = app.getContentResolver().openInputStream(uri)) {
                    if (input == null) throw new java.io.IOException("无法打开所选图片");
                    byte[] buffer = new byte[8192]; int count;
                    while ((count = input.read(buffer)) != -1) {
                        if (original.size() + count > 8 * 1024 * 1024) throw new java.io.IOException("请选择小于 8 MB 的图片");
                        original.write(buffer, 0, count);
                    }
                }
                byte[] source = original.toByteArray();
                BitmapFactory.Options bounds = new BitmapFactory.Options(); bounds.inJustDecodeBounds = true;
                BitmapFactory.decodeByteArray(source, 0, source.length, bounds);
                if (bounds.outWidth <= 0 || bounds.outHeight <= 0 || (long)bounds.outWidth * bounds.outHeight > 16000000)
                    throw new java.io.IOException("请选择有效图片，至多 1600 万像素");
                // Upload original bytes: Runtime checks decode and keeps orientation metadata.
                item = RuntimeClient.uploadImage(target, source);
            } catch (OutOfMemoryError exhausted) { error = "手机内存不足，请选择更小的图片";
            } catch (Exception failure) { error = failure.getMessage(); }
            Attachment result = item; String detail = error;
            new Handler(Looper.getMainLooper()).post(() -> callback.ready(result, detail));
        });
    }
}
