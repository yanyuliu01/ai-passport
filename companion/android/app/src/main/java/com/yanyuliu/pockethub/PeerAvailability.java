package com.yanyuliu.pockethub;
import java.util.concurrent.TimeUnit;
import java.util.function.LongSupplier;

/** A live relay alone does not prove that the selected computer is still present. */
final class PeerAvailability {
    private final LongSupplier clock;
    private volatile long lastSeen;
    private volatile boolean seen;
    PeerAvailability(){this(System::nanoTime);}
    PeerAvailability(LongSupplier clock){this.clock=clock;}
    void observed(){lastSeen=clock.getAsLong();seen=true;}
    boolean online(boolean transportReady){
        long elapsed=clock.getAsLong()-lastSeen;
        return transportReady && seen && elapsed>=0 && elapsed<=TimeUnit.SECONDS.toNanos(10);
    }
}
