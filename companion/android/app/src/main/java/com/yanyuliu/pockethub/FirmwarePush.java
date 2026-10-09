package com.yanyuliu.pockethub;

/**
 * 把一份固件镜像经蓝牙传给设备的那一段：发什么、什么时候发、设备说缺了从哪儿重发。
 * 纯 Java，不依赖安卓，不碰蓝牙：外面把设备说的话喂进来，再来问“下一帧发什么”。
 *
 * 顺序：begin → 设备应答（从哪个偏移开始、一帧多大、窗口多大）→ 一帧一帧发数据，
 * 发出去的不超过“设备已写进闪存的 + 窗口”→ 发完说 end → 设备核对通过后自己重启。
 * 线上格式见固件的 main/pocket_update_core.h。
 */
final class FirmwarePush {
    enum State {
        /** begin 发出去了，等设备应答。 */
        BEGINNING,
        SENDING,
        /** end 发出去了，等设备核对。 */
        ENDING,
        /** 设备核对通过，马上重启进新固件。 */
        DONE,
        FAILED,
    }

    static final long BEGIN_TIMEOUT_MS = 10000;
    /** 这么久设备写进闪存的字节数一点没涨，就当这一次传不下去了。 */
    static final long STALL_TIMEOUT_MS = 20000;
    /** 数据都发出去之后，这么久还没等到“全写进去了”，就直接问 end（设备会说还差哪儿）。 */
    static final long END_AFTER_MS = 1500;
    /** 设备核对整份镜像要几秒。 */
    static final long END_TIMEOUT_MS = 30000;
    private static final int DEFAULT_WINDOW = 2048;
    /** 重发这么多次还没传完，说明这条连接传不动。 */
    private static final int MAX_REWINDS = 500;

    private final byte[] image;
    private final String sha256;
    private State state = State.BEGINNING;
    private String error = "";
    private int chunk;
    private int window = DEFAULT_WINDOW;
    /** 下一帧从哪个偏移开始。 */
    private int next;
    /** 设备说已经写进闪存的字节数。 */
    private int done;
    private int lastGot = -1;
    private int stalls;
    private int rewinds;
    private long stateSinceMs;
    private long progressMs;
    private long allSentMs;

    FirmwarePush(byte[] image, String sha256, long nowMs) {
        this.image = image;
        this.sha256 = sha256;
        this.stateSinceMs = nowMs;
        this.progressMs = nowMs;
    }

    String beginLine() {
        return BuddyProtocol.fwBegin(image.length, sha256);
    }

    State state() {
        return state;
    }

    /** 没成的原因：设备给的英文词（size、sha256、flash…），或者这一头发现的（no answer、stalled）。 */
    String error() {
        return error;
    }

    int size() {
        return image.length;
    }

    int sent() {
        return next;
    }

    int done() {
        return done;
    }

    int rewinds() {
        return rewinds;
    }

    int percent() {
        return image.length == 0 ? 0 : (int) ((long) Math.min(done, image.length) * 100 / image.length);
    }

    boolean active() {
        return state != State.DONE && state != State.FAILED;
    }

    private void fail(String reason) {
        state = State.FAILED;
        error = reason;
    }

    private void rewindTo(int offset) {
        next = Math.max(0, Math.min(offset, image.length));
        stalls = 0;
        if (++rewinds > MAX_REWINDS) {
            fail("link too lossy");
        } else if (state == State.ENDING) {
            state = State.SENDING;
        }
    }

    /**
     * 设备说了一句关于换固件的话。ownChunk 是这部手机一帧最多能带的固件字节数
     * （一次写入的上限减去帧头），和设备给的取小。
     */
    void onLine(BuddyProtocol.FwLine line, int ownChunk, long nowMs) {
        if (!active() || line == null) {
            return;
        }
        if (!line.ack) {
            if (state != State.SENDING && state != State.ENDING) {
                return;
            }
            int got = line.number("got", -1);
            int written = line.number("done", -1);
            if (got < 0 || written < 0 || got > image.length || written > got) {
                return;
            }
            if (written > done) {
                done = written;
                progressMs = nowMs;
            }
            if (line.flag("rewind")) {
                if (got < next) {
                    rewindTo(got);
                }
            } else if (got == lastGot && next > got) {
                // 连着两次报告设备都停在同一个地方，而我们明明发过后面的：那几帧没到。
                if (++stalls >= 2) {
                    rewindTo(got);
                }
            } else {
                stalls = 0;
            }
            lastGot = got;
            return;
        }
        if ("begin".equals(line.op) && state == State.BEGINNING) {
            if (!line.ok) {
                fail(line.error.isEmpty() ? "refused" : line.error);
                return;
            }
            chunk = Math.min(line.number("chunk", 0), ownChunk);
            window = line.number("window", DEFAULT_WINDOW);
            int offset = line.number("offset", 0);
            if (chunk <= 0 || window <= 0 || offset > image.length) {
                fail("link");
                return;
            }
            next = offset;
            done = offset;
            state = State.SENDING;
            stateSinceMs = nowMs;
            progressMs = nowMs;
            if (next == image.length) {
                allSentMs = nowMs;
            }
        } else if ("end".equals(line.op) && (state == State.ENDING || state == State.SENDING)) {
            if (line.ok) {
                done = image.length;
                state = State.DONE;
            } else if ("incomplete".equals(line.error)) {
                // 还没收全：从设备说的地方接着发，发完再问一次。
                progressMs = nowMs;
                state = State.ENDING;
                rewindTo(line.number("got", 0));
            } else {
                fail(line.error.isEmpty() ? "refused" : line.error);
            }
        } else if (line.op.isEmpty() && !line.ok) {
            // 旧固件不认识 fw 这个命令。
            fail(line.error.isEmpty() ? "unsupported" : line.error);
        }
    }

    /** 现在可以发的下一帧；窗口满了、发完了或者不在发的阶段返回 null。 */
    byte[] nextFrame(long nowMs) {
        if (state != State.SENDING || next >= image.length) {
            return null;
        }
        long limit = (long) done + window;
        if (next >= limit) {
            return null;
        }
        int length = (int) Math.min(Math.min(chunk, image.length - next), limit - next);
        byte[] frame = BuddyProtocol.fwFrame(image, next, length);
        next += length;
        if (next == image.length) {
            allSentMs = nowMs;
        }
        return frame;
    }

    /** 现在该发的一行控制命令（只有 end），没有返回 null。返回过就不再返回同一次。 */
    String takeLine(long nowMs) {
        if (state == State.SENDING && next == image.length
                && (done == image.length || nowMs - allSentMs >= END_AFTER_MS)) {
            state = State.ENDING;
            stateSinceMs = nowMs;
            return BuddyProtocol.fwOp("end");
        }
        return null;
    }

    /** 定时调用：等得太久就认输。 */
    void onTick(long nowMs) {
        if (state == State.BEGINNING && nowMs - stateSinceMs > BEGIN_TIMEOUT_MS) {
            fail("no answer");
        } else if (state == State.SENDING && nowMs - progressMs > STALL_TIMEOUT_MS) {
            fail("stalled");
        } else if (state == State.ENDING && nowMs - stateSinceMs > END_TIMEOUT_MS) {
            fail("no answer");
        }
    }
}
