package com.yanyuliu.pockethub;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.*;
import java.util.concurrent.TimeUnit;
import okhttp3.*;
/** Pinned, bounded account API shared by the Android lifecycle and protocol tests. */
final class AccountAPI implements AutoCloseable {
    static final class AuthExpired extends IOException {}
    private final String server;
    private final OkHttpClient http;
    AccountAPI(String server,String pin) throws Exception {
        this.server=server;
        OkHttpClient.Builder builder=new OkHttpClient.Builder().followRedirects(false).followSslRedirects(false).callTimeout(6,TimeUnit.SECONDS);
        PinnedRelayTLS.configure(builder,RelayCrypto.map("url",server,"tlsFingerprint",pin));http=builder.build();
    }
    Map<String,Object> call(String path,Map<String,Object> body,String token) throws Exception {
        Request.Builder request=new Request.Builder().url(server.replace("wss:","https:")+"/v1/account/"+path)
            .post(RequestBody.create(RelayCrypto.json(body).getBytes(StandardCharsets.UTF_8),MediaType.parse("application/json")));
        if(token!=null)request.header("Authorization","Bearer "+token);
        try(Response response=http.newCall(request.build()).execute()){
            if(response.code()==401 && token!=null)throw new AuthExpired();
            if(response.body()==null || response.body().contentLength()>128000)throw new IOException("账号服务响应无效");
            java.io.ByteArrayOutputStream out=new java.io.ByteArrayOutputStream();java.io.InputStream input=response.body().byteStream();byte[] buffer=new byte[4096];int count;
            while((count=input.read(buffer))!=-1){if(out.size()+count>128000)throw new IOException("账号服务响应过大");out.write(buffer,0,count);}byte[] bytes=out.toByteArray();
            Map<String,Object> value=Json.parseObject(new String(bytes,StandardCharsets.UTF_8));if(value==null)throw new IOException("账号服务响应无效");
            if(!response.isSuccessful())throw new IOException(response.code()==401?"账号或密码不正确":response.code()==429?"登录繁忙，请稍后重试":"账号服务暂不可用");return value;
        }
    }
    @Override public void close(){http.dispatcher().executorService().shutdownNow();http.connectionPool().evictAll();}
}
