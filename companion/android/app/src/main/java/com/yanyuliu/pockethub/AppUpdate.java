package com.yanyuliu.pockethub;

import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageInfo;
import android.net.Uri;
import android.os.Handler;
import android.os.Build;
import android.os.Looper;
import android.provider.Settings;
import java.io.File;
import java.io.FileInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.security.MessageDigest;
import java.util.Map;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * App 自己的更新：电脑上发布一版（`python3 -m xiaoyou_runtime app publish`），手机经 Runtime 取走，
 * 校验后交给系统安装。安装一定要主人在系统界面上点确认，这里不能也不该绕过。
 */
final class AppUpdate {
    private static final ExecutorService WORKER = Executors.newSingleThreadExecutor();
    private static Handler main;
    private static final long MAX_BYTES = UpdateDownload.MAX_BYTES;

    /** 电脑上发布的那一版。 */
    static final class Release {
        final long code;
        final String name, sha256, note, abi, downloadUrl;
        final long size;
        final double publishedAt;
        Release(Map<String, String> fields) {
            code = number(fields.get("version_code"));
            name = text(fields.get("version_name"));
            sha256 = text(fields.get("sha256"));
            note = text(fields.get("note"));
            abi = preferredAbi(new String[]{fields.get("abi")});
            downloadUrl = text(fields.get("download_url"));
            size = number(fields.get("size"));
            double at = 0;
            try { at = Double.parseDouble(text(fields.get("published_at"))); } catch (NumberFormatException ignored) { }
            publishedAt = at;
        }
        String label() { return name.isEmpty() ? "版本 " + code : name; }
    }

    interface CheckCallback { void done(Release newer, String error); }
    interface Progress { void at(int percent); }
    interface DownloadCallback { void done(File apk, String error); }

    /** 最近一次检查的结果；null 表示没有更新或还没查过。 */
    static volatile Release available;
    /** 最近一次检查时电脑上发布的资源包（增量更新的那部分）。 */
    static volatile java.util.List<AppPacks.Pack> packs = new java.util.ArrayList<>();
    private static volatile String availableTarget = "";

    static Release availableFor(RuntimeClient.Target target) {
        return target != null && target.url.equals(availableTarget) ? available : null;
    }
    static volatile long checkedAt;
    private static String installedSha;

    /**
     * 比电脑上的版本旧就算有更新：版本号更小；或版本号一样但安装包不同、而且是在这台手机装好之后才发布的
     * （开发时常忘了改版本号）。
     */
    static boolean newer(Release release, long installedCode, String installedSha256, long installedAtMs) {
        if (release.code <= 0 || !release.sha256.matches("[0-9a-f]{64}")) return false;
        if (release.code != installedCode) return release.code > installedCode;
        return !release.sha256.equals(installedSha256) && release.publishedAt * 1000 > installedAtMs;
    }

    static void check(Context context, RuntimeClient.Target target, CheckCallback callback) {
        Context app = context.getApplicationContext();
        WORKER.execute(() -> {
            Release found = null;
            String error = null;
            try {
                if (target == null) throw new IOException("还没有连接电脑");
                Map<String, String> fields = RuntimeClient.appRelease(target);
                if ("true".equals(fields.get("available"))) {
                    Release release = new Release(fields);
                    PackageInfo info = app.getPackageManager().getPackageInfo(app.getPackageName(), 0);
                    long installedCode = Build.VERSION.SDK_INT >= 28 ? info.getLongVersionCode() : info.versionCode;
                    if (newer(release, installedCode, installedSha(app), info.lastUpdateTime)) found = release;
                }
                java.util.List<AppPacks.Pack> listed = RuntimeClient.appPacks(target);
                RuntimeClient.Target current = RuntimeClient.selected(app);
                if (current != null && current.url.equals(target.url)) {
                    available = found; availableTarget = target.url; packs = listed;
                    checkedAt = System.currentTimeMillis();
                }
            } catch (Exception failure) { error = failure.getMessage(); }
            Release result = found;
            String detail = error;
            if (callback != null) main().post(() -> callback.done(result, detail));
        });
    }

    private static synchronized String installedSha(Context context) throws Exception {
        if (installedSha == null) {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            try (InputStream input = new FileInputStream(context.getApplicationInfo().sourceDir)) {
                byte[] buffer = new byte[65536];
                int count;
                while ((count = input.read(buffer)) != -1) digest.update(buffer, 0, count);
            }
            installedSha = hex(digest.digest());
        }
        return installedSha;
    }

    /** 安装包放在 ArtifactProvider 认得的位置（updates/<sha256>.bin），好把只读权限交给系统安装器。 */
    static File file(Context context, Release release) {
        return new File(new File(context.getFilesDir(), ArtifactProvider.UPDATES), release.sha256 + ".bin");
    }

    /** external：从网上的镜像下（发布时传到了 GitHub 才有）；否则从电脑下。两处是同一个文件。 */
    static void download(Context context, RuntimeClient.Target target, Release release, boolean external,
                         Progress progress, DownloadCallback callback) {
        Context app = context.getApplicationContext();
        WORKER.execute(() -> {
            File done = null;
            String error = null;
            try {
                if (target == null) throw new IOException("还没有连接电脑");
                File file = file(app, release);
                if (!verified(file, release)) fetch(target, release, external, file, percent -> {
                    if (progress != null) main().post(() -> progress.at(percent));
                });
                done = file;
            } catch (Exception failure) { error = failure.getMessage(); }
            File result = done;
            String detail = error;
            main().post(() -> callback.done(result, detail));
        });
    }

    static boolean verified(File file, Release release) throws Exception {
        if (!file.isFile() || file.length() <= 0 || file.length() > MAX_BYTES
                || release.size > 0 && file.length() != release.size) return false;
        MessageDigest digest = MessageDigest.getInstance("SHA-256");
        try (InputStream input = new FileInputStream(file)) {
            byte[] buffer = new byte[65536]; int count;
            while ((count = input.read(buffer)) != -1) digest.update(buffer, 0, count);
        }
        return hex(digest.digest()).equals(release.sha256);
    }

    private static void fetch(RuntimeClient.Target target, Release release, boolean external, File file,
                              Progress progress) throws Exception {
        File folder = file.getParentFile();
        if (!folder.isDirectory() && !folder.mkdirs()) throw new IOException("没地方放安装包");
        File part = new File(file.getPath() + ".part");
        // Keep this release's partial bytes across retries and process restarts.
        File[] old = folder.listFiles();
        if (old != null) for (File stale : old)
            if (!stale.equals(file) && !stale.equals(part)) stale.delete();
        if (!external || release.downloadUrl.isEmpty()) {
            UpdateDownload.fetch(target.url + packagePath(release), target.token, release.sha256,
                    release.size, file, progress::at);
        } else {
            // Runtime authenticates the manifest; its private token must never
            // be included in a public host's request or redirect chain.
            UpdateDownload.fetchPreferred(release.downloadUrl, target.url + packagePath(release), target.token,
                    release.sha256, release.size, file, progress::at);
        }
    }

    static String preferredAbi(String[] abis) {
        if (abis != null) for (String abi : abis)
            if ("arm64-v8a".equals(abi) || "armeabi-v7a".equals(abi)
                    || "x86".equals(abi) || "x86_64".equals(abi) || "universal".equals(abi)) return abi;
        return "universal";
    }

    static String packagePath(Release release) {
        return "/v1/app/apk?abi=" + release.abi + "&sha256=" + release.sha256;
    }

    /**
     * 交给系统安装。还没允许「安装未知应用」时先带主人去打开，返回 false，打开后再点一次。
     */
    static boolean install(Context context, File apk) {
        if (!context.getPackageManager().canRequestPackageInstalls()) {
            context.startActivity(new Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,
                    Uri.parse("package:" + context.getPackageName())).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
            return false;
        }
        Uri uri = ArtifactProvider.uri(context, apk, "Pocket Hub.apk", "application/vnd.android.package-archive");
        context.startActivity(new Intent(Intent.ACTION_VIEW).setDataAndType(uri, "application/vnd.android.package-archive")
                .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION | Intent.FLAG_ACTIVITY_NEW_TASK));
        return true;
    }

    private static synchronized Handler main() {
        if (main == null) main = new Handler(Looper.getMainLooper());
        return main;
    }

    private static long number(String value) {
        try { return Long.parseLong(text(value)); } catch (NumberFormatException ignored) { return 0; }
    }

    private static String text(String value) { return value == null ? "" : value; }

    private static String hex(byte[] bytes) {
        StringBuilder result = new StringBuilder();
        for (byte value : bytes) result.append(String.format(java.util.Locale.ROOT, "%02x", value & 255));
        return result.toString();
    }
}
