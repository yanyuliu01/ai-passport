package com.yanyuliu.pockethub;
import org.junit.Test;
import static org.junit.Assert.*;
import java.util.*;
import java.lang.reflect.Method;

public class RelayDeclineTest {
 @Test public void onlyAuthenticatedCurrentInvitationCanCancelPendingPairing() throws Exception {
  RelayCrypto desktop=new RelayCrypto("Computer",null),phone=new RelayCrypto("Phone",null),other=new RelayCrypto("Other",null);
  String space="a".repeat(32),token="t".repeat(43);List<String> states=new ArrayList<>();
  Map<String,Object> config=RelayCrypto.map("device",desktop.identity,"space",space,"name","Phone","seed",phone.seed,"token",token,"expires",System.currentTimeMillis()+120000,
   "relay",RelayCrypto.map("url","ws://127.0.0.1:1","room","b".repeat(32),"token","r".repeat(43)));
  try(RelayConnection link=new RelayConnection(Json.parseObject(RelayCrypto.json(config)),new RelayConnection.Observer(){public void save(Map<String,Object> state){}public void ready(String url,String name){fail("Cancellation must not pair");}public void state(String value){states.add(value);}})){
   Method receive=RelayConnection.class.getDeclaredMethod("receive",String.class);receive.setAccessible(true);
   Map<String,Object> forged=other.seal(phone.identity,space,"decline",RelayCrypto.map("token",token));
   try{receive.invoke(link,RelayCrypto.json(RelayCrypto.map("type","packet","packet",forged)));fail();}catch(java.lang.reflect.InvocationTargetException expected){}
   assertTrue(link.isActive());
   Map<String,Object> stale=desktop.seal(phone.identity,space,"decline",RelayCrypto.map("token","old-token"));
   receive.invoke(link,RelayCrypto.json(RelayCrypto.map("type","packet","packet",stale)));assertTrue(link.isActive());
   Map<String,Object> valid=desktop.seal(phone.identity,space,"decline",RelayCrypto.map("token",token));
   receive.invoke(link,RelayCrypto.json(RelayCrypto.map("type","packet","packet",valid)));assertFalse(link.isActive());assertFalse(link.isPaired());assertTrue(states.stream().anyMatch(s->s.contains("连接已取消")));
  }
 }
}
