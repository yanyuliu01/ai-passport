package com.yanyuliu.pockethub;

import java.io.IOException;

/** The remote may have performed the write; retain its identity for reconciliation. */
final class RelayOutcomeUnknownException extends IOException {
    RelayOutcomeUnknownException() { super("发送结果暂未核实，原请求已保留"); }
}
