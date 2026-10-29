package com.yanyuliu.pockethub;

import android.content.Context;
import android.content.SharedPreferences;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.security.KeyStore;
import java.util.Map;
import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;

/** Atomic sealed confirmation envelope. Legacy body-only records are preserved, never reassigned. */
final class ReviewStorage implements ReviewRecovery.Store {
    private static final String ALIAS = "xiaoyu-effect-review-v1";
    private final SharedPreferences storage, legacy;
    ReviewStorage(Context context) {
        storage = context.getSharedPreferences("effect-review-v1", Context.MODE_PRIVATE);
        legacy = context.getSharedPreferences("ds_ui", Context.MODE_PRIVATE);
    }
    @Override public synchronized ReviewRecovery.Pending load(String key) throws Exception {
        String sealed = storage.getString(key, null);
        if (sealed == null) {
            if (storage.contains(key + ":saved") || legacy.contains(key)) throw unavailable();
            return null;
        }
        try {
            Map<String, Object> data = Json.parseObject(decrypt(key, sealed));
            if (data == null || Json.number(data, "version") != 1 || !data.containsKey("token")) throw unavailable();
            ReviewRecovery.Source source = new ReviewRecovery.Source(Json.string(data, "url"), Json.string(data, "token"),
                    Json.string(data, "runtime"), Json.string(data, "subject"), 0);
            String body = Json.string(data, "body");
            Map<String, Object> fields = Json.parseObject(body);
            if (source.url.isEmpty() || source.runtime.isEmpty() || fields == null
                    || Json.string(fields, "review_id").isEmpty() || Json.object(fields.get("outcomes")) == null) throw unavailable();
            return new ReviewRecovery.Pending(source, body);
        } catch (Exception failure) { throw unavailable(); }
    }
    @Override public synchronized void save(String key, ReviewRecovery.Pending pending) throws Exception {
        ReviewRecovery.Pending previous = load(key);
        if (previous != null && (!previous.body.equals(pending.body) || !previous.source.sameRoute(pending.source)
                || !previous.source.token.equals(pending.source.token))) throw new IOException("已有保存的确认，请重新打开核对");
        ReviewRecovery.Source s = pending.source;
        String value = RelayCrypto.json(RelayCrypto.map("version", 1, "url", s.url, "token", s.token,
                "runtime", s.runtime, "subject", s.subject, "body", pending.body));
        try {
            // A marker distinguishes missing ciphertext from a review that was never submitted.
            if (!storage.edit().putString(key, encrypt(key, value)).putBoolean(key + ":saved", true).commit()) throw unavailable();
        } catch (Exception failure) { throw unavailable(); }
    }
    @Override public synchronized void clear(String key, ReviewRecovery.Pending expected) throws Exception {
        ReviewRecovery.Pending actual = load(key);
        if (actual == null || !actual.body.equals(expected.body) || !actual.source.sameRoute(expected.source)
                || !actual.source.token.equals(expected.source.token)) return;
        if (!storage.edit().remove(key).remove(key + ":saved").commit()) throw unavailable();
    }
    private static IOException unavailable() { return new IOException("系统安全存储不可用，原确认已保留，请恢复后重试"); }
    private static synchronized SecretKey key() throws Exception {
        KeyStore store = KeyStore.getInstance("AndroidKeyStore"); store.load(null);
        if (!store.containsAlias(ALIAS)) {
            KeyGenerator generator = KeyGenerator.getInstance("AES", "AndroidKeyStore");
            generator.init(new KeyGenParameterSpec.Builder(ALIAS, KeyProperties.PURPOSE_ENCRYPT | KeyProperties.PURPOSE_DECRYPT)
                    .setBlockModes(KeyProperties.BLOCK_MODE_GCM).setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE).build());
            generator.generateKey();
        }
        return (SecretKey) store.getKey(ALIAS, null);
    }
    private static String encrypt(String record, String text) throws Exception {
        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding"); cipher.init(Cipher.ENCRYPT_MODE, key());
        cipher.updateAAD(record.getBytes(StandardCharsets.UTF_8));
        return RelayCrypto.encode(cipher.getIV()) + "." + RelayCrypto.encode(cipher.doFinal(text.getBytes(StandardCharsets.UTF_8)));
    }
    private static String decrypt(String record, String text) throws Exception {
        String[] parts = text.split("\\.", 2);
        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
        cipher.init(Cipher.DECRYPT_MODE, key(), new GCMParameterSpec(128, RelayCrypto.decode(parts[0], 12)));
        cipher.updateAAD(record.getBytes(StandardCharsets.UTF_8));
        return new String(cipher.doFinal(RelayCrypto.decode(parts[1], -1)), StandardCharsets.UTF_8);
    }
}
