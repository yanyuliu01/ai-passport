package com.yanyuliu.pockethub;

import java.io.IOException;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;
import java.util.function.BooleanSupplier;

/** A transport write may start only after its exact local intent is durable. */
final class TurnAdmission {
    static void await(CompletableFuture<Boolean> durable,long timeoutMs,BooleanSupplier owner,BooleanSupplier intent) throws IOException {
        try {
            if(!Boolean.TRUE.equals(durable.get(timeoutMs,TimeUnit.MILLISECONDS)))
                throw new IOException("消息未能保存，未发送，请重试");
        } catch(TimeoutException | ExecutionException failure) {
            throw new IOException("消息保存尚未完成，未发送，请重试",failure);
        } catch(InterruptedException interrupted) {
            Thread.currentThread().interrupt();throw new IOException("消息保存已中断，未发送",interrupted);
        }
        if(!owner.getAsBoolean() || !intent.getAsBoolean())throw new IOException("账号、电脑或对话已变化，消息未发送");
    }
    private TurnAdmission(){}
}
