package com.yanyuliu.pockethub;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.InetAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.util.LinkedHashMap;
import java.util.Locale;
import java.util.Map;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import org.junit.Test;
import static org.junit.Assert.*;

public class RelayProofConnectionTest {
    private static Map<String,String> headers(Socket client) throws Exception {
        client.setSoTimeout(5000);
        BufferedReader input=new BufferedReader(new InputStreamReader(client.getInputStream(),StandardCharsets.US_ASCII));
        Map<String,String> result=new LinkedHashMap<>();result.put(":request",input.readLine());
        for(String line; (line=input.readLine())!=null && !line.isEmpty(); ){
            int at=line.indexOf(':');if(at>0)result.put(line.substring(0,at).toLowerCase(Locale.ROOT),line.substring(at+1).trim());
        }
        return result;
    }
    private static void respond(Socket socket,String message) throws Exception {
        OutputStream out=socket.getOutputStream();out.write(message.getBytes(StandardCharsets.US_ASCII));out.flush();
    }
    @Test public void duplicateChallengeTriggersAnImmediateAuthenticatedRetry() throws Exception {
        String room="a".repeat(32);
        RelayCrypto computer=new RelayCrypto("computer",null),phone=new RelayCrypto("phone",null);
        String challenge=RelayDeviceProofTest.challenge(phone,room,new byte[32]);
        String expected=phone.relayProof(room,challenge);
        ExecutorService executor=Executors.newSingleThreadExecutor();
        try(ServerSocket server=new ServerSocket(0,4,InetAddress.getByName("127.0.0.1"))){
            server.setSoTimeout(8000);
            Future<Map<String,String>> received=executor.submit(()->{
                try(Socket first=server.accept()){
                    Map<String,String> h=headers(first);
                    assertTrue(h.get(":request").contains("proof=1"));
                    assertTrue(h.get(":request").contains("key="+phone.identity.get("publicKey")));
                    assertNull(h.get("x-xiaoyu-relay-response"));
                    respond(first,"HTTP/1.1 409 Conflict\r\nX-Xiaoyu-Relay-Proof: v1\r\nX-Xiaoyu-Relay-Challenge: "+challenge+"\r\nContent-Length: 0\r\nConnection: close\r\n\r\n");
                }
                try(Socket retry=server.accept()){
                    Map<String,String> h=headers(retry);
                    respond(retry,"HTTP/1.1 401 Unauthorized\r\nContent-Length: 0\r\nConnection: close\r\n\r\n");
                    return h;
                }
            });
            Map<String,Object> config=RelayCrypto.map("device",computer.identity,"space","c".repeat(32),"name","phone",
                    "seed",phone.seed,"paired",true,"runtime_id","d".repeat(32),
                    "relay",RelayCrypto.map("url","ws://127.0.0.1:"+server.getLocalPort(),"room",room,"token","r".repeat(43)));
            try(RelayConnection link=new RelayConnection(config,new RelayConnection.Observer(){
                public void save(Map<String,Object> state){}public void ready(String url,String name){}public void state(String state){}
            })){
                Map<String,String> retry=received.get(10,TimeUnit.SECONDS);
                assertEquals(expected,retry.get("x-xiaoyu-relay-response"));
                assertFalse(retry.get(":request").contains(expected));
            }
        } finally {executor.shutdownNow();}
    }
}
