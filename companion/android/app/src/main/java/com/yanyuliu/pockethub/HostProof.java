package com.yanyuliu.pockethub;

import java.nio.charset.StandardCharsets;
import java.security.GeneralSecurityException;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Checks that the Runtime answering is one holding this phone's credential (Runtime identity.host_proof):
 * the phone sends a random nonce to /v1/info, the Runtime answers HMAC-SHA256(key = hash of the credential,
 * "xiaoyou-host-proof\0<host>\0<nonce>"). The hash is devices.hash_secret: the secret part of a device
 * credential dev.<id>.<secret>, or the whole legacy shared token.
 */
final class HostProof {
    private static final Pattern DEVICE = Pattern.compile("^dev\\.([0-9a-f]{16})\\.([0-9a-f]{64})$");
    private static final SecureRandom RANDOM = new SecureRandom();

    private HostProof() { }

    static boolean device(String token) { return token != null && DEVICE.matcher(token).matches(); }

    static String keyHash(String token) {
        Matcher match = DEVICE.matcher(token == null ? "" : token);
        String secret = match.matches() ? match.group(2) : token == null ? "" : token;
        return hex(sha256(("xiaoyou-device\0" + secret).getBytes(StandardCharsets.UTF_8)));
    }

    static String proof(String keyHash, String host, String nonce) {
        try {
            javax.crypto.Mac mac = javax.crypto.Mac.getInstance("HmacSHA256");
            mac.init(new javax.crypto.spec.SecretKeySpec(keyHash.getBytes(StandardCharsets.UTF_8), "HmacSHA256"));
            return hex(mac.doFinal(("xiaoyou-host-proof\0" + host + "\0" + nonce).getBytes(StandardCharsets.UTF_8)));
        } catch (GeneralSecurityException impossible) { throw new AssertionError(impossible); }
    }

    static boolean matches(String token, String host, String nonce, String answer) {
        if (host == null || host.isEmpty() || answer == null || answer.isEmpty()) return false;
        return MessageDigest.isEqual(proof(keyHash(token), host, nonce).getBytes(StandardCharsets.UTF_8),
                answer.getBytes(StandardCharsets.UTF_8));
    }

    static String nonce() {
        byte[] bytes = new byte[16]; RANDOM.nextBytes(bytes); return hex(bytes);
    }

    private static byte[] sha256(byte[] value) {
        try { return MessageDigest.getInstance("SHA-256").digest(value); }
        catch (GeneralSecurityException impossible) { throw new AssertionError(impossible); }
    }

    private static String hex(byte[] bytes) {
        StringBuilder out = new StringBuilder(bytes.length * 2);
        for (byte value : bytes) out.append(String.format(java.util.Locale.ROOT, "%02x", value & 0xff));
        return out.toString();
    }
}
