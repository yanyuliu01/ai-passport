package com.yanyuliu.pockethub;

import java.nio.charset.StandardCharsets;
import java.util.Map;
import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import org.bouncycastle.crypto.hpke.HPKE;
import org.junit.Test;
import static org.junit.Assert.*;

public class RelayDeviceProofTest {
    private static final String ROOM = "a".repeat(32), ID = "b".repeat(32);
    private static final long EXPIRES = 1900000000123L;
    private static final String INFO = "xiaoyou/relay-device-proof/v1";

    @Test public void interoperatesWithNodeRelayChallenge() throws Exception {
        RelayCrypto phone = new RelayCrypto("phone", "BwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwc");
        assertEquals("a3ab38441d3ed6d2af1d75fc22e59768",phone.identity.get("id"));
        String challenge = "eyJ2IjoxLCJpZCI6IjBkYzJjZGQ3MWFhOGYzMjllNmY5ZTA4YzVlNDA5MGNiIiwiZXhwaXJlcyI6MTEwMDAwLCJlbmMiOiJ4QXpZQ1ZYWDJjZl9kNFFqZ3k0eG1LTDI3YlFWdTRac2Z6VFNEU3FyZUJJIiwiY3QiOiJRbUZoY3cwQWJ3alBXR1BTaE41LVdqcjZzeXRHX0RBcXplLS1seUY0T2k5bm14YlRCMTRaMDZjWkQ0U2RNX3doIn0";
        assertEquals("0dc2cdd71aa8f329e6f9e08c5e4090cb.yTzAFTYxRWfYaFjk_rAtx1Yi3efriUc9GD_OJbNBzYY",
                phone.relayProof("11111111111111111111111111111111",challenge));
    }

    static String challenge(RelayCrypto recipient, String room, byte[] secret) throws Exception {
        HPKE hpke = new HPKE(HPKE.mode_base, HPKE.kem_X25519_SHA256, HPKE.kdf_HKDF_SHA256, HPKE.aead_AES_GCM128);
        String aad = INFO + "\0" + room + "\0" + recipient.identity.get("id") + "\0" + ID + "\0" + EXPIRES;
        byte[][] result = hpke.seal(hpke.deserializePublicKey(RelayCrypto.decode(Json.string(recipient.identity,"publicKey"),32)),
                INFO.getBytes(StandardCharsets.UTF_8), aad.getBytes(StandardCharsets.UTF_8), secret, null, null, null);
        Map<String,Object> value = RelayCrypto.map("v",1,"id",ID,"expires",EXPIRES,
                "enc",RelayCrypto.encode(result[1]),"ct",RelayCrypto.encode(result[0]));
        return RelayCrypto.encode(RelayCrypto.json(value).getBytes(StandardCharsets.UTF_8));
    }

    @Test public void validChallengeProvesDeviceAndRoom() throws Exception {
        RelayCrypto phone = new RelayCrypto("phone", null);
        byte[] secret = new byte[32]; for (int i=0;i<secret.length;i++) secret[i]=(byte)i;
        String value = challenge(phone, ROOM, secret);
        String aad = INFO + "\0" + ROOM + "\0" + phone.identity.get("id") + "\0" + ID + "\0" + EXPIRES;
        Mac mac = Mac.getInstance("HmacSHA256"); mac.init(new SecretKeySpec(secret,"HmacSHA256"));
        assertEquals(ID + "." + RelayCrypto.encode(mac.doFinal(aad.getBytes(StandardCharsets.UTF_8))), phone.relayProof(ROOM,value));
    }

    @Test public void differentDeviceOrRoomCannotAnswer() throws Exception {
        RelayCrypto owner = new RelayCrypto("phone", null), other = new RelayCrypto("other", null);
        String value = challenge(owner, ROOM, new byte[32]);
        assertThrows(Exception.class, () -> other.relayProof(ROOM,value));
        assertThrows(Exception.class, () -> owner.relayProof("c".repeat(32),value));
    }

    @Test public void malformedAndOversizedChallengesFailClosed() throws Exception {
        RelayCrypto phone = new RelayCrypto("phone", null);
        assertThrows(Exception.class, () -> phone.relayProof(ROOM,"a".repeat(1025)));
        assertThrows(Exception.class, () -> phone.relayProof(ROOM,RelayCrypto.encode("{}".getBytes(StandardCharsets.UTF_8))));
        assertThrows(Exception.class, () -> phone.relayProof("bad",challenge(phone,ROOM,new byte[32])));
    }
}
