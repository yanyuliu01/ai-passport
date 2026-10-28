package com.yanyuliu.pockethub;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.*;
import javax.crypto.Cipher;
import javax.crypto.spec.GCMParameterSpec;
import javax.crypto.spec.SecretKeySpec;
import org.bouncycastle.crypto.generators.SCrypt;

/** Validate the desktop account recovery envelope before trusting its device directory. */
final class AccountIdentity {
    static void verify(String password,String account,String space,Map<String,Object> vault) throws Exception {
        if(!account.matches("[a-f0-9]{32}") || !space.matches("[a-f0-9]{32}") || vault==null || Json.number(vault,"v")!=1)
            throw new Exception("请先在电脑登录并配置小幽，再在手机登录同一账号");
        byte[] key=SCrypt.generate(password.getBytes(StandardCharsets.UTF_8),("xiaoyou-account-recovery-v1:"+account).getBytes(StandardCharsets.UTF_8),32768,8,1,32);
        byte[] secret=null;
        try {
            byte[] ciphertext=hex(Json.string(vault,"ciphertext"),16,4096),tag=hex(Json.string(vault,"tag"),16,16);
            byte[] sealed=Arrays.copyOf(ciphertext,ciphertext.length+tag.length);System.arraycopy(tag,0,sealed,ciphertext.length,tag.length);
            Cipher cipher=Cipher.getInstance("AES/GCM/NoPadding");cipher.init(Cipher.DECRYPT_MODE,new SecretKeySpec(key,"AES"),new GCMParameterSpec(128,hex(Json.string(vault,"nonce"),12,12)));
            cipher.updateAAD((account+":"+space).getBytes(StandardCharsets.UTF_8));secret=cipher.doFinal(sealed);
            MessageDigest digest=MessageDigest.getInstance("SHA-256");digest.update("xiaoyou-subject\0".getBytes(StandardCharsets.UTF_8));
            String actual=hex(digest.digest(secret)).substring(0,32);
            if(!actual.equals(space))throw new Exception("账号空间不一致");
        } finally {Arrays.fill(key,(byte)0);if(secret!=null)Arrays.fill(secret,(byte)0);}
    }
    static byte[] hex(String value,int min,int max) throws Exception {
        if(!value.matches("(?:[a-f0-9]{2}){"+min+","+max+"}"))throw new Exception("账号互通备份无效");
        byte[] bytes=new byte[value.length()/2];for(int i=0;i<bytes.length;i++)bytes[i]=(byte)Integer.parseInt(value.substring(i*2,i*2+2),16);return bytes;
    }
    static String hex(byte[] bytes){StringBuilder out=new StringBuilder();for(byte b:bytes)out.append(String.format(Locale.ROOT,"%02x",b&255));return out.toString();}
}
