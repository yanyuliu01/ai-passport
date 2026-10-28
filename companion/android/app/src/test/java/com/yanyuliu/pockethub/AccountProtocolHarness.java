package com.yanyuliu.pockethub;
import java.util.*;
import java.util.concurrent.*;
/** Synthetic local account login with the actual phone API, recovery crypto and encrypted RPC. */
public class AccountProtocolHarness {
 @SuppressWarnings("unchecked") public static void main(String[] args)throws Exception {
  RelayCrypto phone=new RelayCrypto("Account phone fixture",null);
  try(AccountAPI api=new AccountAPI(args[0],args[1])){
   Map<String,Object> login=api.call("login",RelayCrypto.map("username","fixture_owner","password","synthetic-account-passphrase","device",phone.identity),null);
   String account=Json.string(login,"id"),token=Json.string(login,"token"),space=Json.string(login,"space");
   AccountIdentity.verify("synthetic-account-passphrase",account,space,(Map<String,Object>)login.get("vault"));
   Map<String,Object> directory=api.call("presence",Collections.emptyMap(),token);
   List<RelayConnection> links=new ArrayList<>();CountDownLatch ready=new CountDownLatch(2);
   try {
    for(Object row:Json.list(directory,"devices")){
     Map<String,Object> device=(Map<String,Object>)((Map<?,?>)row).get("device");RelayCrypto.validateDevice(device);
     Map<String,Object> config=RelayCrypto.map("account",account,"device",device,"space",space,"seed",phone.seed,"name","Phone fixture","paired",true,
       "relay",RelayCrypto.map("url",args[0],"tlsFingerprint",args[1],"room",account,"token",login.get("relayToken")));
     links.add(new RelayConnection(config,new RelayConnection.Observer(){public void save(Map<String,Object> value){}public void state(String value){}public void ready(String url,String name){ready.countDown();}}));
    }
    if(links.size()!=2 || !ready.await(18,TimeUnit.SECONDS))throw new Exception("Account-only device discovery did not become ready");
    Map<String,RelayConnection> routes=new HashMap<>();for(RelayConnection link:links)routes.put(link.url(),link);
    Set<String> runtimes=new HashSet<>();
    for(RelayConnection link:links){
     if(!link.url().endsWith("."+account))throw new Exception("Route is not account-scoped");
     String target=RelayConnection.target(java.net.URI.create(link.url()+"/v1/info"));
     if(!routes.containsKey(target))throw new Exception("Phone dispatch lost its account route");
     Map<String,Object> info=routes.get(target).request("GET","/v1/info",null,10);runtimes.add(Json.string(info,"runtime_id"));
    }
    if(runtimes.size()!=2)throw new Exception("Source identities mixed");
    List<String> urls=new ArrayList<>();for(RelayConnection link:links)urls.add(link.url());
    String selected=RuntimeSelection.resolve(urls.get(0),urls.get(1),urls);
    if(!selected.equals(urls.get(0)))throw new Exception("Discovery changed the selected recipient");
    String firstRuntime=Json.string(routes.get(selected).request("GET","/v1/info",null,10),"runtime_id");
    selected=RuntimeSelection.resolve(urls.get(1),selected,urls);
    if(firstRuntime.equals(Json.string(routes.get(selected).request("GET","/v1/info",null,10),"runtime_id")))throw new Exception("Manual recipient switch did not reach the second Runtime");
    if(RuntimeSelection.resolve(urls.get(0),urls.get(1),Collections.singletonList(urls.get(1)))!=null)throw new Exception("Unavailable manual target silently rerouted");
    links.get(0).close();links.remove(0);
    links.get(0).request("GET","/v1/info",null,10);
    api.call("logout",Collections.emptyMap(),token);
    try{api.call("presence",Collections.emptyMap(),token);throw new AssertionError("Signed-out session accepted");}catch(AccountAPI.AuthExpired expected){}
    System.out.println("accountLogin=true identityRecovery=true twoComputers=true manualRuntimeSwitch=true noSilentFallback=true sharedIdentity=true closeOneKeepsOther=true logoutRevoked=true realPhone=false");
   }finally{for(RelayConnection link:links)link.close();}
  }
 }
}
