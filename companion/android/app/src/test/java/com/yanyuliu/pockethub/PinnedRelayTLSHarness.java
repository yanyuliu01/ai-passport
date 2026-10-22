package com.yanyuliu.pockethub;
import okhttp3.*;
/** Negative transport check: a mismatched node must receive no HTTP headers. */
public class PinnedRelayTLSHarness {
 public static void main(String[] args)throws Exception{
  OkHttpClient.Builder builder=new OkHttpClient.Builder();
  PinnedRelayTLS.configure(builder,RelayCrypto.map("url",args[0],"tlsFingerprint",args[1]));
  OkHttpClient client=builder.build();
  try{
   try(Response response=client.newCall(new Request.Builder().url(args[0].replace("wss:","https:")+"/healthz").header("Authorization","synthetic-must-not-arrive").build()).execute()){
    throw new Exception("Mismatched certificate was accepted");
   }catch(javax.net.ssl.SSLException expected){System.out.println("wrong-pin-blocked-before-http");}
  }finally{client.dispatcher().executorService().shutdown();client.connectionPool().evictAll();}
 }
}
