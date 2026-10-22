package com.yanyuliu.pockethub;

import android.content.Context;
import android.net.Uri;
import android.os.Handler;
import android.os.Looper;
import java.io.File;
import java.io.FileInputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/** Binary downloads have a separate executor and bounded storage, never the chat/JSON queue. */
final class ArtifactClient {
    private static final ExecutorService WORKER = Executors.newFixedThreadPool(2);
    private static final Handler MAIN = new Handler(Looper.getMainLooper());
    interface Callback { void ready(File file, String error); }
    interface TextCallback { void ready(String text, String error); }

    static void readText(File file, TextCallback callback) {
        WORKER.execute(() -> {
            String text = null, error = null;
            try {
                if (file.length() > 2L * 1024 * 1024) throw new java.io.IOException("正文超过 2 MB，请保存后用文件应用打开");
                try (InputStream input = new FileInputStream(file)) {
                    java.io.ByteArrayOutputStream bytes = new java.io.ByteArrayOutputStream();
                    byte[] buffer = new byte[8192]; int count;
                    while ((count = input.read(buffer)) != -1) bytes.write(buffer, 0, count);
                    text = new String(bytes.toByteArray(), java.nio.charset.StandardCharsets.UTF_8);
                }
            } catch (Exception failure) { error = failure.getMessage(); }
            String value = text, detail = error;
            MAIN.post(() -> callback.ready(value, detail));
        });
    }

    static File cached(Context context, RuntimeClient.Target target, Attachment item, boolean thumb) {
        File directory = new File(context.getFilesDir(), "artifacts");
        return new File(directory, ArtifactDownload.key(target.url + "\n" + item.id + "\n" + item.sha256 + (thumb ? "\nthumb" : "\ncontent")) + ".bin");
    }

    /** The phone already holds what it just uploaded; seed the cache so the sent bubble never re-downloads it. */
    static void seed(Context context, RuntimeClient.Target target, Attachment item, byte[] content, byte[] thumb) {
        try {
            write(cached(context, target, item, false), content);
            if (thumb != null) write(cached(context, target, item, true), thumb);
        } catch (java.io.IOException ignored) { /* Falls back to downloading from the Runtime. */ }
    }

    private static void write(File file, byte[] bytes) throws java.io.IOException {
        File directory = file.getParentFile();
        if (directory != null && !directory.isDirectory() && !directory.mkdirs()) throw new java.io.IOException("cache");
        File partial = new File(file.getPath() + ".part");
        try (java.io.FileOutputStream out = new java.io.FileOutputStream(partial)) { out.write(bytes); }
        if (!partial.renameTo(file)) { partial.delete(); throw new java.io.IOException("cache"); }
    }

    static void fetch(Context context, RuntimeClient.Target target, Attachment item, boolean thumb, Callback callback) {
        Context app = context.getApplicationContext();
        WORKER.execute(() -> {
            File file = null;
            String error = null;
            try {
                if (target == null) throw new java.io.IOException("还没有连接电脑");
                file = cached(app, target, item, thumb);
                if (!file.isFile()) ArtifactDownload.download(target.url, target.token, item, thumb, file);
            } catch (Exception failure) { error = failure.getMessage(); file = null; }
            File result = file;
            String detail = error;
            MAIN.post(() -> callback.ready(result, detail));
        });
    }

    static void save(Context context, File file, Uri destination, Callback callback) {
        WORKER.execute(() -> {
            String error = null;
            try (InputStream input = new FileInputStream(file); OutputStream output = context.getContentResolver().openOutputStream(destination)) {
                if (output == null) throw new java.io.IOException("无法写入所选位置");
                byte[] buffer = new byte[16384]; int count;
                while ((count = input.read(buffer)) != -1) output.write(buffer, 0, count);
            } catch (Exception failure) { error = failure.getMessage(); }
            String detail = error;
            MAIN.post(() -> callback.ready(detail == null ? file : null, detail));
        });
    }

}
