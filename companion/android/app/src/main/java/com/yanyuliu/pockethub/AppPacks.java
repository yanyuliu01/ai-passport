package com.yanyuliu.pockethub;

import android.content.Context;
import android.os.Handler;
import android.os.Looper;
import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * Incremental app updates: large files that rarely change (the speech models) are not in the
 * APK but in resource packs the Runtime publishes per file by SHA-256
 * (`python3 -m xiaoyu_runtime app pack`). The phone keeps each pack under files/packs/&lt;name&gt;/
 * with an index of what it installed, and downloads only files whose checksum changed, from
 * the computer or, when the pack lists one, from a public HTTPS mirror. Every file is verified
 * before it replaces anything, and downloads resume.
 */
final class AppPacks {
    static final String SPEECH = "speech";
    /** Files the phone's speech engine needs from the speech pack. */
    static final String[] SPEECH_FILES = {"zh/model.int8.onnx", "zh/tokens.txt",
            "sense-voice/model.int8.onnx", "sense-voice/tokens.txt"};
    private static final String INDEX = ".index.json";
    private static final ExecutorService WORKER = Executors.newSingleThreadExecutor();
    private static Handler main;

    private static synchronized Handler main() {
        if (main == null) main = new Handler(Looper.getMainLooper());
        return main;
    }

    static final class Item {
        final String path, sha256, downloadUrl;
        final long size;
        Item(String path, String sha256, long size, String downloadUrl) {
            this.path = path; this.sha256 = sha256; this.size = size; this.downloadUrl = downloadUrl;
        }
    }

    static final class Pack {
        final String name;
        final List<Item> files;
        Pack(String name, List<Item> files) { this.name = name; this.files = files; }

        /** Every file has a public mirror, so "download from the internet" is possible. */
        boolean hosted() {
            for (Item item : files) if (item.downloadUrl.isEmpty()) return false;
            return !files.isEmpty();
        }
    }

    interface Progress { void at(int percent); }
    interface Done { void done(String error); }

    /** Packs from GET /v1/app/packs. Unknown or malformed entries are skipped. */
    static List<Pack> parse(Map<String, Object> raw) {
        List<Pack> packs = new ArrayList<>();
        for (Object value : Json.list(raw, "packs")) {
            Map<String, Object> pack = Json.object(value);
            String name = Json.string(pack, "name");
            if (!name.matches("[a-z][a-z0-9-]{0,31}")) continue;
            List<Item> files = new ArrayList<>();
            for (Object entry : Json.list(pack, "files")) {
                Map<String, Object> file = Json.object(entry);
                String path = Json.string(file, "path"), sha = Json.string(file, "sha256");
                long size = (long) Json.number(file, "size");
                if (!safePath(path) || !sha.matches("[0-9a-f]{64}") || size <= 0) continue;
                files.add(new Item(path, sha, size, Json.string(file, "download_url")));
            }
            packs.add(new Pack(name, files));
        }
        return packs;
    }

    static boolean safePath(String path) {
        return path.matches("[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)*") && !path.contains("..") && !path.startsWith(".");
    }

    static Pack find(List<Pack> packs, String name) {
        for (Pack pack : packs) if (pack.name.equals(name)) return pack;
        return null;
    }

    /** Files of the pack the phone does not have in this exact version: what an update downloads. */
    static List<Item> missing(Pack pack, Map<String, String> installed) {
        List<Item> result = new ArrayList<>();
        for (Item item : pack.files) if (!item.sha256.equals(installed.get(item.path))) result.add(item);
        return result;
    }

    static long bytes(List<Item> items) {
        long total = 0;
        for (Item item : items) total += item.size;
        return total;
    }

    static File dir(Context context, String name) { return new File(new File(context.getFilesDir(), "packs"), name); }

    /** path -> sha256 of what is installed, read from the pack's index; empty when nothing is. */
    static synchronized Map<String, String> installed(Context context, String name) {
        Map<String, String> result = new HashMap<>();
        File index = new File(dir(context, name), INDEX);
        if (!index.isFile()) return result;
        try {
            Map<String, Object> raw = Json.parseObject(new String(java.nio.file.Files.readAllBytes(index.toPath()),
                    StandardCharsets.UTF_8));
            if (raw != null) for (Map.Entry<String, Object> entry : raw.entrySet()) {
                File file = new File(dir(context, name), entry.getKey());
                if (entry.getValue() instanceof String && safePath(entry.getKey()) && file.isFile())
                    result.put(entry.getKey(), (String) entry.getValue());
            }
        } catch (IOException ignored) { }
        return result;
    }

    /** All the given files are installed (whatever their version). */
    static boolean ready(Context context, String name, String[] paths) {
        Map<String, String> have = installed(context, name);
        for (String path : paths) if (!have.containsKey(path)) return false;
        return true;
    }

    private static synchronized void writeIndex(Context context, String name, Map<String, String> index) throws IOException {
        StringBuilder out = new StringBuilder("{");
        boolean first = true;
        for (Map.Entry<String, String> entry : index.entrySet()) {
            if (!first) out.append(',');
            first = false;
            BuddyProtocol.quote(out, entry.getKey()); out.append(':'); BuddyProtocol.quote(out, entry.getValue());
        }
        File folder = dir(context, name);
        File temp = new File(folder, INDEX + ".part");
        try (FileOutputStream output = new FileOutputStream(temp)) {
            output.write(out.append('}').toString().getBytes(StandardCharsets.UTF_8));
            output.getFD().sync();
        }
        if (!temp.renameTo(new File(folder, INDEX))) throw new IOException("资源索引写不下去");
    }

    /**
     * Brings the pack up to date: downloads only files whose checksum changed (resuming partial
     * ones), verifies each before it replaces the old one, then removes files the pack no longer
     * has. external picks the public mirror over the computer for files that have one.
     */
    static void update(Context context, RuntimeClient.Target target, Pack pack, boolean external,
                       Progress progress, Done done) {
        Context app = context.getApplicationContext();
        WORKER.execute(() -> {
            String error = null;
            try {
                if (target == null) throw new IOException("还没有连接电脑");
                File folder = dir(app, pack.name);
                File downloads = new File(folder.getParentFile(), ".downloads");
                if (!folder.isDirectory() && !folder.mkdirs() || !downloads.isDirectory() && !downloads.mkdirs())
                    throw new IOException("没地方放资源文件");
                Map<String, String> index = installed(app, pack.name);
                List<Item> todo = missing(pack, index);
                long total = Math.max(1, bytes(todo)), finished = 0;
                for (Item item : todo) {
                    File file = new File(downloads, item.sha256 + ".bin");
                    final long before = finished;
                    UpdateDownload.Progress step = percent -> {
                        int overall = (int) Math.min(99, (before + item.size * percent / 100) * 100 / total);
                        main().post(() -> progress.at(overall));
                    };
                    UpdateDownload.fetchPreferred(external ? item.downloadUrl : "",
                            target.url + "/v1/app/file?sha256=" + item.sha256, target.token,
                            item.sha256, item.size, file, step);
                    File placed = new File(folder, item.path);
                    File parent = placed.getParentFile();
                    if (!parent.isDirectory() && !parent.mkdirs()) throw new IOException("没地方放资源文件");
                    // The engine may hold the old file open; it keeps reading the old inode.
                    if (placed.exists() && !placed.delete() || !file.renameTo(placed))
                        throw new IOException("资源文件放不进去：" + item.path);
                    index.put(item.path, item.sha256);
                    writeIndex(app, pack.name, index);
                    finished += item.size;
                }
                // Files the pack dropped.
                Map<String, Item> wanted = new HashMap<>();
                for (Item item : pack.files) wanted.put(item.path, item);
                for (String path : new ArrayList<>(index.keySet())) {
                    if (wanted.containsKey(path)) continue;
                    new File(folder, path).delete();
                    index.remove(path);
                }
                writeIndex(app, pack.name, index);
            } catch (Exception failure) {
                error = failure.getMessage() == null ? "资源下载失败" : failure.getMessage();
            }
            String result = error;
            main().post(() -> done.done(result));
        });
    }
}
