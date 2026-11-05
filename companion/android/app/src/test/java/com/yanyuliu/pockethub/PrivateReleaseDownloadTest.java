package com.yanyuliu.pockethub;

import static org.junit.Assert.*;
import java.io.ByteArrayInputStream;
import java.io.File;
import java.io.IOException;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.file.Files;
import java.security.MessageDigest;
import java.util.Arrays;
import java.util.HashMap;
import java.util.Map;
import java.util.concurrent.atomic.AtomicInteger;
import org.junit.After;
import org.junit.Before;
import org.junit.Test;

public class PrivateReleaseDownloadTest {
    private static final String ACCOUNT = "a".repeat(32), TOKEN = "z".repeat(43);
    private static final byte[] APK = "synthetic-private-apk-payload".getBytes(java.nio.charset.StandardCharsets.UTF_8);
    private File directory, file, part;
    private String sha, url;
    private final AtomicInteger connections = new AtomicInteger();
    private Reply head, reply;

    private static final class Reply extends HttpURLConnection {
        final int status; final byte[] body; final Map<String,String> headers = new HashMap<>();
        boolean closed;
        Reply(URL url, int status, byte[] body) { super(url); this.status=status; this.body=body; }
        public void connect() { }
        public boolean usingProxy() { return false; }
        public void disconnect() { closed=true; }
        public int getResponseCode() { return status; }
        public String getHeaderField(String name) { return headers.get(name); }
        public long getContentLengthLong() { return body.length; }
        public InputStream getInputStream() { return new ByteArrayInputStream(body); }
    }

    @Before public void setup() throws Exception {
        StringBuilder digest = new StringBuilder();
        for (byte b : MessageDigest.getInstance("SHA-256").digest(APK)) digest.append(String.format("%02x", b & 255));
        sha=digest.toString();
        url="https://150.158.176.23/v1/private-releases/apps/"+ACCOUNT+"/"+sha+".apk";
        directory=Files.createTempDirectory("private-update-test").toFile();
        file=new File(directory,sha+".bin"); part=new File(file.getPath()+".part");
    }

    @After public void cleanup() {
        File[] files=directory.listFiles(); if(files!=null) for(File item:files)item.delete(); directory.delete();
    }

    private HttpURLConnection connect(URL target) {
        int count=connections.incrementAndGet(); assertEquals(url,target.toString());
        return head!=null && count==1 ? head : reply;
    }
    private Reply authorizedHead() throws Exception {
        Reply value=new Reply(new URL(url),200,APK); value.headers.put("ETag","\""+sha+"\""); return value;
    }

    @Test public void accountPinnedRangeResumesAndVerifiesBeforeRenaming() throws Exception {
        Files.write(part.toPath(),Arrays.copyOf(APK,7));
        head=authorizedHead();
        reply=new Reply(new URL(url),206,Arrays.copyOfRange(APK,7,APK.length));
        reply.headers.put("Content-Range","bytes 7-"+(APK.length-1)+"/"+APK.length);
        reply.headers.put("ETag","\""+sha+"\"");
        UpdateDownload.fetchPrivate(url,ACCOUNT,TOKEN,sha,APK.length,file,ignored->{},this::connect);
        assertEquals(2,connections.get()); assertEquals("Bearer "+TOKEN,reply.getRequestProperty("Authorization"));
        assertEquals("bytes=7-",reply.getRequestProperty("Range"));
        assertEquals("\""+sha+"\"",reply.getRequestProperty("If-Range"));
        assertFalse(reply.getInstanceFollowRedirects()); assertTrue(reply.closed);
        assertArrayEquals(APK,Files.readAllBytes(file.toPath())); assertFalse(part.exists());
    }

    @Test public void noCredentialsAreSentForWrongAccountOriginPathOrToken() throws Exception {
        String[] invalid={url.replace(ACCOUNT,"b".repeat(32)),url.replace("150.158.176.23","example.com"),
            url.replace("https:","http:"),url.replace("150.158.176.23","150.158.176.23:444"),
            url+"?x=1",url+"#fragment",url.replace(sha,"b".repeat(64)),
            url.replace("https://","https://user@")};
        for(String address:invalid){
            try { UpdateDownload.fetchPrivate(address,ACCOUNT,TOKEN,sha,APK.length,file,ignored->{},this::connect); fail(address); }
            catch(IOException expected){assertTrue(expected.getMessage().contains("账号") || expected.getMessage().contains("地址"));}
        }
        try { UpdateDownload.fetchPrivate(url,ACCOUNT,"",sha,APK.length,file,ignored->{},this::connect); fail(); }
        catch(IOException expected){assertTrue(expected.getMessage().contains("账号"));}
        assertEquals(0,connections.get()); assertFalse(file.exists());
    }

    @Test public void cachedPrivatePackageStillRequiresLiveAuthorization() throws Exception {
        reply=new Reply(new URL(url),200,APK); reply.headers.put("ETag","\""+sha+"\"");
        UpdateDownload.authorizePrivate(url,ACCOUNT,TOKEN,sha,APK.length,this::connect);
        assertEquals("HEAD",reply.getRequestMethod());
        assertEquals("Bearer "+TOKEN,reply.getRequestProperty("Authorization"));
        assertFalse(reply.getInstanceFollowRedirects()); assertTrue(reply.closed);
        for(int status:new int[]{302,401,403,404}){
            connections.set(0); reply=new Reply(new URL(url),status,new byte[0]);
            reply.headers.put("Location","https://example.com/steal");
            try { UpdateDownload.authorizePrivate(url,ACCOUNT,TOKEN,sha,APK.length,this::connect); fail(); }
            catch(IOException expected){assertEquals(1,connections.get());}
            assertTrue(reply.closed);
        }
    }

    @Test public void cachedFileAndCompletePartialBothRequireLiveAuthorization() throws Exception {
        Files.write(file.toPath(),APK); head=authorizedHead();
        UpdateDownload.fetchPrivate(url,ACCOUNT,TOKEN,sha,APK.length,file,ignored->{},this::connect);
        assertEquals(1,connections.get()); assertTrue(head.closed);
        connections.set(0); head=new Reply(new URL(url),401,new byte[0]);
        try { UpdateDownload.fetchPrivate(url,ACCOUNT,TOKEN,sha,APK.length,file,ignored->{},this::connect); fail(); }
        catch(IOException expected){assertEquals(1,connections.get());}
        assertTrue(file.delete()); Files.write(part.toPath(),APK);
        connections.set(0); head=new Reply(new URL(url),401,new byte[0]);
        try { UpdateDownload.fetchPrivate(url,ACCOUNT,TOKEN,sha,APK.length,file,ignored->{},this::connect); fail(); }
        catch(IOException expected){assertEquals(1,connections.get());}
        assertFalse(file.exists()); assertTrue(part.exists());
        connections.set(0); head=authorizedHead();
        UpdateDownload.fetchPrivate(url,ACCOUNT,TOKEN,sha,APK.length,file,ignored->{},this::connect);
        assertEquals(1,connections.get()); assertTrue(head.closed);
        assertArrayEquals(APK,Files.readAllBytes(file.toPath()));
    }

    @Test public void redirectsAndAuthorizationFailuresNeverLeakOrFallBack() throws Exception {
        for(int status:new int[]{302,401,403}){
            connections.set(0); head=authorizedHead(); reply=new Reply(new URL(url),status,new byte[0]);
            reply.headers.put("Location","https://example.com/steal");
            try { UpdateDownload.fetchPrivate(url,ACCOUNT,TOKEN,sha,APK.length,file,ignored->{},this::connect); fail(); }
            catch(IOException expected) { assertEquals(2,connections.get()); }
            assertTrue(reply.closed); assertFalse(file.exists());
        }
    }

    @Test public void privateRouteRequiresSameSelectedAccountAndExactShape() throws Exception {
        assertTrue(PrivateReleaseRoute.looksPrivate(url));
        assertTrue(PrivateReleaseRoute.matches(new URL(url),ACCOUNT,sha));
        assertTrue(PrivateReleaseRoute.targetMatches("relay://"+"c".repeat(32)+"."+ACCOUNT,ACCOUNT));
        assertFalse(PrivateReleaseRoute.targetMatches("relay://"+"c".repeat(32)+"."+"b".repeat(32),ACCOUNT));
        assertFalse(PrivateReleaseRoute.targetMatches("http://127.0.0.1:8765",ACCOUNT));
        assertFalse(PrivateReleaseRoute.matches(new URL(url),"b".repeat(32),sha));
        assertFalse(PrivateReleaseRoute.looksPrivate("https://example.com/app.apk"));
    }

    @Test public void completionFailsAfterAccountOrTargetSwitchBeforeCallbackRuns() {
        String target="relay://"+"c".repeat(32)+"."+ACCOUNT, targetToken="private-target-token";
        assertTrue(PrivateReleaseRoute.completionAllowed(target,targetToken,TOKEN,target,targetToken,TOKEN));
        assertFalse(PrivateReleaseRoute.completionAllowed(target,targetToken,TOKEN,target,targetToken,""));
        assertFalse(PrivateReleaseRoute.completionAllowed(target,targetToken,TOKEN,target,targetToken,"y".repeat(43)));
        assertFalse(PrivateReleaseRoute.completionAllowed(target,targetToken,TOKEN,target,"changed",TOKEN));
        assertFalse(PrivateReleaseRoute.completionAllowed(target,targetToken,TOKEN,"relay://other",targetToken,TOKEN));
        assertFalse(PrivateReleaseRoute.completionAllowed(target,targetToken,TOKEN,null,null,TOKEN));
        java.util.concurrent.atomic.AtomicReference<String> currentSession = new java.util.concurrent.atomic.AtomicReference<>(TOKEN);
        java.util.concurrent.atomic.AtomicBoolean delivered = new java.util.concurrent.atomic.AtomicBoolean();
        Runnable queuedCallback = () -> delivered.set(PrivateReleaseRoute.completionAllowed(
            target,targetToken,TOKEN,target,targetToken,currentSession.get()));
        currentSession.set(""); // Logout occurs after the worker queues completion.
        queuedCallback.run(); assertFalse(delivered.get());
    }
}
