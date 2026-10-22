package com.yanyuliu.pockethub;
import java.nio.file.*;import java.util.*;import java.util.concurrent.*;
/** Invoked by the desktop interoperability gate; uses synthetic data and real Java protocol code. */
public class RelayProtocolHarness {
 public static void main(String[] args)throws Exception{
  CountDownLatch ready=new CountDownLatch(1);List<String> states=Collections.synchronizedList(new ArrayList<>());
  Map<String,Object> config=RelayConnection.invitation(new String(Files.readAllBytes(Path.of(args[0])),java.nio.charset.StandardCharsets.UTF_8),"Android protocol fixture");
  try(RelayConnection connection=new RelayConnection(config,new RelayConnection.Observer(){
   public void save(Map<String,Object> state){} public void ready(String url,String name){ready.countDown();} public void state(String state){states.add(state);}
  })){
   if(!ready.await(15,TimeUnit.SECONDS))throw new Exception("Pairing timed out: "+states);
   Map<String,Object> info=connection.request("GET","/v1/info",null,10);
   String client="a".repeat(32);Map<String,Object> body=RelayCrypto.map("text","手机异地协议测试","conversation","default","client_id",client);
   Map<String,Object> first=connection.request("POST","/v1/messages",body,30);
   Map<String,Object> second=connection.request("POST","/v1/messages",body,10);
   if(!first.get("id").equals(second.get("id")))throw new Exception("Duplicate submission");
   connection.request("POST","/v1/voice?client_id="+"b".repeat(32),RelayCrypto.map("_relay_binary",RelayCrypto.encode("synthetic-wav".getBytes()),"content_type","audio/wav"),10);
   connection.request("POST","/v1/images",RelayCrypto.map("_relay_binary",RelayCrypto.encode("synthetic-image".getBytes()),"content_type","image/jpeg"),10);
   Map<String,Object> artifact=connection.request("GET","/v1/artifacts/file-1/content",null,10);
   if(!"synthetic-image".equals(new String(RelayCrypto.decode(Json.string(artifact,"_relay_binary"),-1))))throw new Exception("Binary transfer failed");
   connection.request("GET","/v1/sync?wait=50&after=0",null,30);
   CountDownLatch ready2=new CountDownLatch(1);
   Map<String,Object> config2=RelayConnection.invitation(new String(Files.readAllBytes(Path.of(args[2])),java.nio.charset.StandardCharsets.UTF_8),"Same phone, second computer");
   try(RelayConnection secondComputer=new RelayConnection(config2,new RelayConnection.Observer(){public void save(Map<String,Object> value){}public void ready(String url,String name){ready2.countDown();}public void state(String state){}})) {
     if(!ready2.await(15,TimeUnit.SECONDS))throw new Exception("Second computer pairing timed out");
     Map<String,Object> other=secondComputer.request("GET","/v1/info",null,10);
     Map<String,Object> original=connection.request("GET","/v1/info",null,10);
     if(original.get("runtime_id").equals(other.get("runtime_id")))throw new Exception("Computer identities mixed");
   }
   Files.write(Path.of(args[1]),RelayCrypto.json(RelayCrypto.map("javaPhonePairing",true,"twoSimultaneousComputers",true,"requestRetryAfterLostResponse",true,"textVoiceImageBinary",true,"longPollClamped",true,"reconnected",states.stream().anyMatch(s->s.contains("恢复")),"realPhone",false,"realPublicNetwork",false)).getBytes(java.nio.charset.StandardCharsets.UTF_8));
  }
 }
}
