package com.yanyuliu.pockethub;
import org.junit.Test;import static org.junit.Assert.*;import java.util.*;
public class RelayCryptoTest {
 @Test public void sameSeedSameIdentityAndUnicodeRoundTrip()throws Exception{
  RelayCrypto a=new RelayCrypto("Mac",null),b=new RelayCrypto("Phone",null),copy=new RelayCrypto("New name",b.seed);
  assertEquals(b.identity.get("id"),copy.identity.get("id"));
  Map<String,Object> packet=a.seal(b.identity,"test-space","request",RelayCrypto.map("text","异地手机：接着聊"));
  assertEquals("异地手机：接着聊",b.open(packet,a.identity).get("text"));
 }
 @Test public void changedCiphertextAndSenderCannotBeAccepted()throws Exception{
  RelayCrypto a=new RelayCrypto("Mac",null),b=new RelayCrypto("Phone",null),attacker=new RelayCrypto("Other",null);
  Map<String,Object> packet=a.seal(b.identity,"test-space","request",RelayCrypto.map("text","secret"));
  try{b.open(packet,attacker.identity);fail();}catch(Exception expected){}
  String cipher=(String)packet.get("ct");packet.put("ct",(cipher.startsWith("A")?"B":"A")+cipher.substring(1));
  try{b.open(packet,a.identity);fail();}catch(Exception expected){}
 }
 @Test public void expiredPacketsAndInsecureRemoteRelaysAreRejected()throws Exception{
  RelayCrypto a=new RelayCrypto("Mac",null),b=new RelayCrypto("Phone",null);
  Map<String,Object> packet=a.seal(b.identity,"test-space","request",RelayCrypto.map("text","secret"));packet.put("expires",1L);
  try{b.open(packet,a.identity);fail();}catch(Exception expected){}
  try{RelayConnection.validateRelay(RelayCrypto.map("url","ws://example.com","room","a".repeat(32),"token","x".repeat(32)));fail();}catch(Exception expected){}
 }
 @Test public void malformedPinsNeverEnableAnInsecureOverride()throws Exception{
  for(String pin:Arrays.asList("","x".repeat(64),"a".repeat(63))){
   try{RelayConnection.validateRelay(RelayCrypto.map("url","wss://node.example","room","a".repeat(32),"token","x".repeat(32),"tlsFingerprint",pin));fail();}catch(Exception expected){}
  }
  try{PinnedRelayTLS.configure(new okhttp3.OkHttpClient.Builder(),RelayCrypto.map("url","ws://127.0.0.1:18795","tlsFingerprint","a".repeat(64)));fail();}catch(Exception expected){}
  RelayConnection.validateRelay(RelayCrypto.map("url","wss://node.example","room","a".repeat(32),"token","x".repeat(32)));
 }
}
