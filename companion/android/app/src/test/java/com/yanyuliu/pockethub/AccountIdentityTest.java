package com.yanyuliu.pockethub;
import org.junit.Test;
import static org.junit.Assert.*;
import java.util.*;
public class AccountIdentityTest {
    private final String account="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", password="synthetic-passphrase", space="dc21076d6bda5cef232460a19c813775";
    private Map<String,Object> vault(){return Json.parseObject("{\"v\":1,\"nonce\":\"010101010101010101010101\",\"tag\":\"ddd4cf05b59a383bc2d2d83ee19afe58\",\"ciphertext\":\"72f40ca16196259587528ab7243ecedce9372a8f0b4e805e2fe1971a207500e68a64f12a10474e57814652b430be4bd4d44cf774b6\"}");}
    @Test public void acceptsDesktopScryptAesGcmEnvelope() throws Exception { AccountIdentity.verify(password,account,space,vault()); }
    @Test public void refusesWrongPasswordSpaceAndTampering() throws Exception {
        for(int mode=0;mode<3;mode++){
            Map<String,Object> value=vault();if(mode==2)value.put("tag","0".repeat(32));
            try{AccountIdentity.verify(mode==0?"wrong-password":password,account,mode==1?"b".repeat(32):space,value);fail("invalid envelope admitted");}
            catch(Exception expected){}
        }
    }
    @Test public void requiresExistingComputerIdentity() throws Exception {
        try{AccountIdentity.verify(password,account,"",null);fail();}catch(Exception expected){}
    }
    @Test public void phoneRoutesAreSeparatedAcrossAccountsForTheSameComputer() {
        Map<String,Object> config=RelayCrypto.map("device",RelayCrypto.map("id","c".repeat(32)),"account","a".repeat(32));
        String first=RelayConnection.address(config);config.put("account","b".repeat(32));
        assertNotEquals(first,RelayConnection.address(config));
        assertEquals("relay://"+"c".repeat(32)+"."+"b".repeat(32),RelayConnection.address(config));
    }
    @Test public void dispatchRetainsNumericAccountAndDeviceIdsWithoutDnsParsing() throws Exception {
        for(char prefix:"0123456789abcdef".toCharArray()) {
            String computer=prefix+"c".repeat(31), account=prefix+"a".repeat(31);
            String target="relay://"+computer+"."+account;
            assertEquals(target,RelayConnection.target(java.net.URI.create(target+"/v1/sync?wait=20&after=opaque%3A1")));
            assertEquals("relay://"+computer,RelayConnection.target(java.net.URI.create("relay://"+computer+"/v1/info")));
        }
    }
    @Test public void dispatchRejectsCredentialPortsEncodingAndUnscopedNames() throws Exception {
        for(String address:Arrays.asList("relay://user@"+"a".repeat(32),"relay://"+"a".repeat(32)+":443", "relay://example.com", "relay://%61"+"a".repeat(31),"https://"+"a".repeat(32),"relay://"+"a".repeat(32)+"/v1/info#hidden")) {
            try {RelayConnection.target(java.net.URI.create(address));fail("invalid route admitted");}catch(java.io.IOException expected){}
        }
    }
}
