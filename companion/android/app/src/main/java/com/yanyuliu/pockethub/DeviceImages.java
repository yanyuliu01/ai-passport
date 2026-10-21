package com.yanyuliu.pockethub;
import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.Rect;
import android.os.Handler;
import android.os.Looper;
import android.util.Base64;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.Executors;
import java.util.zip.CRC32;

/** One requested 64x64 RGB565 preview at a time. JSON stop-and-wait never
 * changes the OTA binary frame source, and text/voice are not delayed by decode. */
final class DeviceImages {
    private final Context context;
    private final BleLink link;
    private final Handler main = new Handler(Looper.getMainLooper());
    private final java.util.concurrent.ExecutorService worker = Executors.newSingleThreadExecutor();
    private final Map<String, List<Attachment>> offered = new LinkedHashMap<>();
    private final Map<String, String> sent = new LinkedHashMap<>();
    private boolean enabled;
    private long serial, token;
    private String id = "", focusedCard = "", waiting;
    private byte[] pixels;
    private int next, retries, stagnant;
    private long epoch;
    private final Runnable timeout = () -> retry();
    DeviceImages(Context context, BleLink link) { this.context = context; this.link = link; }
    void reset(boolean enabled) {
        this.enabled = enabled; ++serial; focusedCard = ""; offered.clear(); sent.clear(); stop();
    }
    void clear() { reset(enabled); if (enabled) link.send("{\"cmd\":\"image\",\"op\":\"clear\"}\n"); }
    void close() { reset(false); worker.shutdownNow(); }
    private void stop() { main.removeCallbacks(timeout); waiting = null; pixels = null; id = ""; ++serial; }
    void offer(Card card) {
        if (!enabled) return;
        LinkedHashMap<String, Attachment> unique = new LinkedHashMap<>();
        for (Card.Entry entry : card.entries) for (Attachment item : entry.attachments)
            if (item.image() && item.ready()) unique.put(item.id, item);
        for (Attachment item : card.attachments) if (item.image() && item.ready()) unique.put(item.id, item);
        List<Attachment> images = new ArrayList<>(unique.values());
        // A bounded recent image window, same order on every update.
        if (images.size() > 4) images = new ArrayList<>(images.subList(images.size() - 4, images.size()));
        offered.put(card.id, images);
        while (offered.size() > 12) { String oldest = offered.keySet().iterator().next(); offered.remove(oldest); sent.remove(oldest); }
        StringBuilder line = new StringBuilder("{\"cmd\":\"image\",\"op\":\"list\",\"card\":");
        BuddyProtocol.quote(line, card.id); line.append(",\"ids\":[");
        for (int i = 0; i < images.size(); i++) { if (i > 0) line.append(','); BuddyProtocol.quote(line, images.get(i).id); }
        String message = line.append("]}\n").toString();
        if (card.id.equals(focusedCard) && !message.equals(sent.get(card.id))) { link.send(message); sent.put(card.id, message); }
    }
    boolean onLine(String line) {
        Map<String, Object> raw = Json.parseObject(line);
        if (raw == null || !"image".equals(Json.string(raw, "cmd"))) return false;
        if (!enabled) return true;
        if ("catalog".equals(Json.string(raw, "op"))) {
            String cardId = Json.string(raw, "card");
            if (!cardId.matches("[A-Za-z0-9_-]{1,11}")) return true;
            focusedCard = cardId; sent.remove(cardId);
            Card card = CardViews.find(HubStore.get().cards(), cardId);
            if (card != null) offer(card);
            else {
                StringBuilder response = new StringBuilder("{\"cmd\":\"image\",\"op\":\"catalog_error\",\"card\":");
                BuddyProtocol.quote(response, cardId); link.send(response.append("}\n").toString());
            }
            return true;
        }
        if ("get".equals(Json.string(raw, "op"))) {
            stop();
            String wanted = Json.string(raw, "id");
            String cardId = Json.string(raw, "card");
            if (!cardId.equals(focusedCard)) return true;
            Card card = CardViews.find(HubStore.get().cards(), cardId);
            if (card != null) offer(card);
            List<Attachment> images = offered.get(cardId);
            Attachment match = null;
            if (images != null) for (Attachment item : images) if (item.id.equals(wanted)) match = item;
            id = wanted; token = (long)Json.number(raw, "token");
            if (match == null || token <= 0 || token > 0xffffffffL) { error(); return true; }
            RuntimeClient.Target target = RuntimeClient.selected(context);
            if (target == null) { error(); return true; }
            long request = ++serial; epoch = HubStore.get().cardsEpoch();
            ArtifactClient.fetch(context, target, match, true, (file, failure) -> {
                if (request != serial) return;
                if (file == null) { error(); return; }
                worker.execute(() -> {
                    byte[] decoded = null;
                    try {
                        BitmapFactory.Options bounds = new BitmapFactory.Options(); bounds.inJustDecodeBounds = true;
                        BitmapFactory.decodeFile(file.getAbsolutePath(), bounds);
                        if (bounds.outWidth <= 0 || bounds.outHeight <= 0 || (long)bounds.outWidth * bounds.outHeight > 1048576)
                            throw new IllegalArgumentException("Preview exceeds pixel limit");
                        Bitmap source = BitmapFactory.decodeFile(file.getAbsolutePath());
                        if (source == null) throw new IllegalArgumentException("Cannot decode preview");
                        Bitmap preview = Bitmap.createBitmap(64, 64, Bitmap.Config.ARGB_8888);
                        Canvas canvas = new Canvas(preview); canvas.drawColor(Color.rgb(21, 23, 42));
                        float scale = Math.min(64f / source.getWidth(), 64f / source.getHeight());
                        int width = Math.max(1, Math.round(source.getWidth() * scale)), height = Math.max(1, Math.round(source.getHeight() * scale));
                        int left = (64 - width) / 2, top = (64 - height) / 2;
                        canvas.drawBitmap(source, null, new Rect(left, top, left + width, top + height), new Paint(Paint.FILTER_BITMAP_FLAG));
                        int[] colors = new int[4096]; preview.getPixels(colors, 0, 64, 0, 0, 64, 64);
                        decoded = ImagePreview.rgb565(colors);
                        source.recycle(); preview.recycle();
                    } catch (RuntimeException | OutOfMemoryError ignored) { }
                    byte[] data = decoded;
                    main.post(() -> {
                        RuntimeClient.Target selected = RuntimeClient.selected(context);
                        if (request != serial || !enabled || epoch != HubStore.get().cardsEpoch()
                                || selected == null || !selected.url.equals(target.url) || !selected.token.equals(target.token)) return;
                        if (data == null) { error(); return; }
                        pixels = data; next = stagnant = 0;
                        CRC32 crc = new CRC32(); crc.update(data);
                        send(base("begin") + ",\"size\":8192,\"crc\":" + crc.getValue() + "}\n");
                    });
                });
            });
        } else if ("ack".equals(Json.string(raw, "op")) && waiting != null
                && token == (long)Json.number(raw, "token")) {
            main.removeCallbacks(timeout);
            String status = Json.string(raw, "status");
            if (status.equals("done") || status.equals("error")) { stop(); return true; }
            int offset = (int)Json.number(raw, "next");
            if (pixels == null || offset < 0 || offset > pixels.length || offset > next + 384) { error(); return true; }
            if (offset < next) return true;
            stagnant = offset == next ? stagnant + 1 : 0;
            if (stagnant > 3) { error(); return true; }
            next = offset;
            if (next == pixels.length) send(base("end") + "}\n");
            else {
                int end = Math.min(pixels.length, next + 384);
                String data = Base64.encodeToString(pixels, next, end - next, Base64.NO_WRAP);
                send(base("chunk") + ",\"offset\":" + next + ",\"data\":\"" + data + "\"}\n");
            }
        }
        return true;
    }
    private String base(String op) { return "{\"cmd\":\"image\",\"op\":\"" + op + "\",\"id\":\"" + id + "\",\"token\":" + token; }
    private void error() { if (id.matches("a[0-9a-f]{32}")) link.send(base("error") + "}\n"); stop(); }
    private void send(String line) { waiting = line; retries = 0; link.send(line); main.removeCallbacks(timeout); main.postDelayed(timeout, 2000); }
    private void retry() {
        if (waiting == null) return;
        if (++retries > 3 || !link.isReady() || epoch != HubStore.get().cardsEpoch()) { error(); return; }
        link.send(waiting); main.postDelayed(timeout, 2000);
    }
}
