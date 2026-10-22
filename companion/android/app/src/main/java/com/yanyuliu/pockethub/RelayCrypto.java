package com.yanyuliu.pockethub;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.util.*;
import org.bouncycastle.crypto.AsymmetricCipherKeyPair;
import org.bouncycastle.crypto.hpke.HPKE;

/** RFC 9180 authenticated HPKE, wire-compatible with desktop EndpointCrypto. No Android dependencies. */
final class RelayCrypto {
    static final int MAX_BYTES = 4 * 1024 * 1024;
    private static final byte[] INFO = "xiaoyou/endpoint-envelope/v1".getBytes(StandardCharsets.UTF_8);
    private final HPKE suite = new HPKE(HPKE.mode_auth, HPKE.kem_X25519_SHA256, HPKE.kdf_HKDF_SHA256, HPKE.aead_AES_GCM128);
    private final AsymmetricCipherKeyPair keys;
    final String seed;
    final Map<String,Object> identity;
    RelayCrypto(String name, String savedSeed) throws Exception {
        byte[] bytes = new byte[32]; if (savedSeed == null) new SecureRandom().nextBytes(bytes); else bytes = decode(savedSeed,32);
        seed = encode(bytes); keys = suite.deriveKeyPair(bytes);
        String key = encode(suite.serializePublicKey(keys.getPublic()));
        identity = map("id",deviceId(key),"name",name.substring(0,Math.min(80,name.length())),"publicKey",key);
    }
    static String encode(byte[] bytes) { return Base64.getUrlEncoder().withoutPadding().encodeToString(bytes); }
    static byte[] decode(String text, int length) throws Exception {
        if (text == null || text.length() > MAX_BYTES * 3 || !text.matches("[A-Za-z0-9_-]+")) throw new Exception("加密编码无效");
        byte[] bytes = Base64.getUrlDecoder().decode(text);
        if (!encode(bytes).equals(text) || (length >= 0 && bytes.length != length)) throw new Exception("加密长度无效");
        return bytes;
    }
    static String deviceId(String key) throws Exception {
        byte[] digest = MessageDigest.getInstance("SHA-256").digest(decode(key,32));
        StringBuilder out = new StringBuilder(); for(int i=0;i<16;i++)out.append(String.format(Locale.ROOT,"%02x",digest[i]&255)); return out.toString();
    }
    static void validateDevice(Map<String,Object> peer) throws Exception {
        if (peer == null || !deviceId(Json.string(peer,"publicKey")).equals(Json.string(peer,"id")) || !(peer.get("name") instanceof String) || Json.string(peer,"name").length()>80) throw new Exception("设备身份不匹配");
    }
    static Map<String,Object> map(Object... items) {
        Map<String,Object> value = new LinkedHashMap<>(); for(int i=0;i<items.length;i+=2)value.put((String)items[i],items[i+1]); return value;
    }
    private static byte[] header(Map<String,Object> packet) {
        return json(map("v",1,"id",packet.get("id"),"from",packet.get("from"),"to",packet.get("to"),"space",packet.get("space"),"key",packet.get("key"),"created",((Number)packet.get("created")).longValue(),"expires",((Number)packet.get("expires")).longValue(),"kind",packet.get("kind"))).getBytes(StandardCharsets.UTF_8);
    }
    synchronized Map<String,Object> seal(Map<String,Object> peer,String space,String kind,Map<String,Object> body) throws Exception {
        validateDevice(peer); long now=System.currentTimeMillis();
        Map<String,Object> packet=map("v",1,"id",UUID.randomUUID().toString().replace("-",""),"from",identity.get("id"),"to",peer.get("id"),"space",space,"key",identity.get("publicKey"),"created",now,"expires",now+120000,"kind",kind);
        byte[] plain=json(body).getBytes(StandardCharsets.UTF_8);if(plain.length>MAX_BYTES)throw new Exception("内容超过加密通道单次上限");
        byte[][] sealed=suite.seal(suite.deserializePublicKey(decode(Json.string(peer,"publicKey"),32)),INFO,header(packet),plain,null,null,keys);
        packet.put("enc",encode(sealed[1]));packet.put("ct",encode(sealed[0]));return packet;
    }
    synchronized Map<String,Object> open(Map<String,Object> packet,Map<String,Object> peer) throws Exception {
        validateDevice(peer); long now=System.currentTimeMillis();
        if(packet==null || !(packet.get("v") instanceof Number) || ((Number)packet.get("v")).intValue()!=1 || !Json.string(packet,"id").matches("[a-f0-9]{32}") || !identity.get("id").equals(packet.get("to")) || !peer.get("id").equals(packet.get("from")) || !peer.get("publicKey").equals(packet.get("key")))throw new Exception("消息来源不匹配");
        long created=((Number)packet.get("created")).longValue(),expires=((Number)packet.get("expires")).longValue();
        if(created>now+30000 || expires<now || expires<created || expires-created>86400000 || Json.string(packet,"space").length()>100 || Json.string(packet,"kind").length()>30)throw new Exception("加密消息已过期");
        byte[] plain=suite.open(decode(Json.string(packet,"enc"),32),keys,INFO,header(packet),decode(Json.string(packet,"ct"),-1),null,null,suite.deserializePublicKey(decode(Json.string(peer,"publicKey"),32)));
        if(plain.length>MAX_BYTES)throw new Exception("解密内容过大");
        Map<String,Object> value=Json.parseObject(new String(plain,StandardCharsets.UTF_8));if(value==null)throw new Exception("加密消息格式无效");return value;
    }
    static String json(Object value) {
        if(value==null)return "null";if(value instanceof Boolean || value instanceof Number)return value.toString();
        if(value instanceof Map){List<String> fields=new ArrayList<>();for(Map.Entry<?,?> e:((Map<?,?>)value).entrySet())fields.add(json(e.getKey().toString())+":"+json(e.getValue()));return "{"+String.join(",",fields)+"}";}
        if(value instanceof List){List<String> fields=new ArrayList<>();for(Object item:(List<?>)value)fields.add(json(item));return "["+String.join(",",fields)+"]";}
        StringBuilder out=new StringBuilder("\"");for(char c:value.toString().toCharArray()){switch(c){case '"':out.append("\\\"");break;case '\\':out.append("\\\\");break;case '\n':out.append("\\n");break;case '\r':out.append("\\r");break;case '\t':out.append("\\t");break;default:if(c<32)out.append(String.format(Locale.ROOT,"\\u%04x",(int)c));else out.append(c);}}return out.append('"').toString();
    }
}
