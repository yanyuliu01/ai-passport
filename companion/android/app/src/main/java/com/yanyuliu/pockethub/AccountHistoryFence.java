package com.yanyuliu.pockethub;

import java.util.function.BooleanSupplier;

/** Serialize account changes with the final installation of a prepared archive. */
final class AccountHistoryFence {
    private volatile long generation;
    synchronized long capture() { return generation; }
    long peek() { return generation; }
    synchronized void change(Runnable change) { change.run(); generation++; }
    synchronized void update(Runnable update) { update.run(); }
    synchronized boolean install(long expected, BooleanSupplier authorized, Runnable action) {
        if (generation != expected || !authorized.getAsBoolean()) return false;
        action.run(); return true;
    }
}
