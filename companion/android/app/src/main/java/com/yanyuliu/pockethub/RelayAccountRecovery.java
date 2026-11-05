package com.yanyuliu.pockethub;

import java.io.IOException;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Objects;

/** Merge refreshed relay credentials only into the same persisted account identity. */
final class RelayAccountRecovery {
    @SuppressWarnings("unchecked")
    static Map<String,Object> merge(String account,Map<String,Object> saved,Map<String,Object> fresh) throws IOException {
        Object priorDevice=saved.get("device"),newDevice=fresh.get("device");
        if(!(priorDevice instanceof Map) || !(newDevice instanceof Map))throw new IOException("原电脑身份不一致，原记录保留");
        Map<String,Object> oldDevice=(Map<String,Object>)priorDevice,device=(Map<String,Object>)newDevice;
        if(!Objects.equals(saved.get("account"),account) || !Objects.equals(fresh.get("account"),account)
                || !Objects.equals(saved.get("space"),fresh.get("space")) || !Objects.equals(saved.get("seed"),fresh.get("seed"))
                || !Objects.equals(oldDevice.get("id"),device.get("id")) || !Objects.equals(oldDevice.get("publicKey"),device.get("publicKey")))
            throw new IOException("原电脑身份不一致，原记录保留");
        Map<String,Object> result=new LinkedHashMap<>(saved);
        result.put("relay",fresh.get("relay"));result.put("device",device);
        return result;
    }
    private RelayAccountRecovery(){}
}
