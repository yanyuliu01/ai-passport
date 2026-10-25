package com.yanyuliu.pockethub;

import org.junit.Test;
import static org.junit.Assert.*;

/** Golden values computed with the Runtime's devices.hash_secret and identity.host_proof. */
public class HostProofTest {
    private static final String DEVICE = "dev.0123456789abcdef." + "ab".repeat(32);
    private static final String LEGACY = "synthetic-legacy-token";
    private static final String HOST = "a".repeat(32), NONCE = "00112233445566778899aabbccddeeff";

    @Test public void keyHashMatchesTheRuntimeForDeviceAndLegacyCredentials() {
        assertEquals("c3f9d969e5bae4f4ac38e0c4e1c483918fe4e2e2bbd5404ad2f8b75c106f2ef3", HostProof.keyHash(DEVICE));
        assertEquals("64e48e4d7c819a192bd8e3b8471e2c39c77b71e8e9b754cc7f76ea852b13c71a", HostProof.keyHash(LEGACY));
        assertTrue(HostProof.device(DEVICE));
        assertFalse(HostProof.device(LEGACY));
        assertFalse(HostProof.device("dev.short.secret"));
    }

    @Test public void proofMatchesOnlyTheRuntimeHoldingThisCredential() {
        assertTrue(HostProof.matches(DEVICE, HOST, NONCE, "421a98a146b626bc626fad23a1788e10fd14365e162993bf11ef8ad7cd22b2ba"));
        assertTrue(HostProof.matches(LEGACY, HOST, NONCE, "ce80809bf2bd4297e233bbbc8e67d60821eb3ac8486e75b066be43d4f98306f0"));
        assertFalse(HostProof.matches(LEGACY, HOST, NONCE, "421a98a146b626bc626fad23a1788e10fd14365e162993bf11ef8ad7cd22b2ba"));
        assertFalse(HostProof.matches(DEVICE, "b".repeat(32), NONCE, "421a98a146b626bc626fad23a1788e10fd14365e162993bf11ef8ad7cd22b2ba"));
        assertFalse(HostProof.matches(DEVICE, HOST, NONCE, ""));
        assertFalse(HostProof.matches(DEVICE, HOST, NONCE, "é".repeat(64)));
        assertFalse(HostProof.matches(DEVICE, HOST, NONCE, "z".repeat(64)));
        assertFalse(HostProof.matches(DEVICE, "", NONCE, "421a98a146b626bc626fad23a1788e10fd14365e162993bf11ef8ad7cd22b2ba"));
    }

    @Test public void noncesAreFreshAndInTheAcceptedLength() {
        String one = HostProof.nonce(), two = HostProof.nonce();
        assertNotEquals(one, two);
        assertTrue(one.length() >= 16 && one.length() <= 128);
    }
}
