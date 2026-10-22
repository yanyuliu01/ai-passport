package com.yanyuliu.pockethub;

import java.net.URI;
import java.security.MessageDigest;
import java.security.cert.CertificateException;
import java.security.cert.X509Certificate;
import java.util.Map;
import javax.net.ssl.SSLContext;
import javax.net.ssl.X509TrustManager;
import okhttp3.OkHttpClient;

/** Trust only the exact certificate carried by a confirmed pairing QR. Never used for ordinary HTTPS. */
final class PinnedRelayTLS {
    private PinnedRelayTLS() {}
    static void configure(OkHttpClient.Builder builder, Map<String,Object> relay) throws Exception {
        if (!relay.containsKey("tlsFingerprint")) return; // Existing public-CA deployments keep platform trust.
        String fingerprint=Json.string(relay,"tlsFingerprint");
        if (!fingerprint.matches("[a-f0-9]{64}") || !"wss".equals(URI.create(Json.string(relay,"url")).getScheme())) throw new CertificateException("节点证书指纹无效");
        byte[] pin=new byte[32]; for(int i=0;i<32;i++)pin[i]=(byte)Integer.parseInt(fingerprint.substring(i*2,i*2+2),16);
        X509TrustManager trust=new X509TrustManager(){
            public X509Certificate[] getAcceptedIssuers(){return new X509Certificate[0];}
            public void checkClientTrusted(X509Certificate[] chain,String auth)throws CertificateException{throw new CertificateException("不接受客户端证书");}
            public void checkServerTrusted(X509Certificate[] chain,String auth)throws CertificateException{check(chain,pin);}
        };
        SSLContext context=SSLContext.getInstance("TLS");context.init(null,new javax.net.ssl.TrustManager[]{trust},null);
        String expected=URI.create(Json.string(relay,"url")).getHost();
        builder.sslSocketFactory(context.getSocketFactory(),trust).hostnameVerifier((host,session)->{
            // The QR binds a node certificate, including when its address is a numeric IP.
            if(!expected.replace("[","").replace("]","").equalsIgnoreCase(host.replace("[","").replace("]","")))return false;
            try {check(new X509Certificate[]{(X509Certificate)session.getPeerCertificates()[0]},pin);return true;}catch(Exception error){return false;}
        });
    }
    private static void check(X509Certificate[] chain,byte[] pin)throws CertificateException{
        if(chain==null||chain.length==0)throw new CertificateException("节点未提供证书");
        chain[0].checkValidity();
        try {if(!MessageDigest.isEqual(pin,MessageDigest.getInstance("SHA-256").digest(chain[0].getEncoded())))throw new CertificateException("节点证书与扫码记录不一致");}
        catch(CertificateException error){throw error;}catch(Exception error){throw new CertificateException("节点证书验证失败",error);}
    }
}
