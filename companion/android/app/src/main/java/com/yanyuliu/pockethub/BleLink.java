package com.yanyuliu.pockethub;

import android.annotation.SuppressLint;
import android.bluetooth.BluetoothAdapter;
import android.bluetooth.BluetoothDevice;
import android.bluetooth.BluetoothGatt;
import android.bluetooth.BluetoothGattCallback;
import android.bluetooth.BluetoothGattCharacteristic;
import android.bluetooth.BluetoothGattDescriptor;
import android.bluetooth.BluetoothGattService;
import android.bluetooth.BluetoothManager;
import android.bluetooth.BluetoothProfile;
import android.bluetooth.le.BluetoothLeScanner;
import android.bluetooth.le.ScanCallback;
import android.bluetooth.le.ScanFilter;
import android.bluetooth.le.ScanResult;
import android.bluetooth.le.ScanSettings;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.os.Handler;
import android.os.Looper;
import android.os.ParcelUuid;

import java.util.ArrayDeque;
import java.util.Collections;
import java.util.List;
import java.util.UUID;

/**
 * 和小幽设备之间的蓝牙连接：扫描、配对、订阅、收发一行行的文本。
 *
 * 设备是外设，暴露 Nordic UART Service，三个特征都要求加密，所以流程是：
 * 连接 → 配对（设备屏幕显示六位数字，在手机上输入）→ 发现服务 → 订阅 TX → 就绪。
 * 所有回调都转到主线程处理，状态只在主线程读写。
 */
@SuppressLint("MissingPermission")
final class BleLink {
    interface Listener {
        void onReady();

        void onLine(String line);

        void onClosed();
    }

    private static final UUID SERVICE = UUID.fromString("6e400001-b5a3-f393-e0a9-e50e24dcca9e");
    private static final UUID RX = UUID.fromString("6e400002-b5a3-f393-e0a9-e50e24dcca9e");
    private static final UUID TX = UUID.fromString("6e400003-b5a3-f393-e0a9-e50e24dcca9e");
    private static final UUID CCCD = UUID.fromString("00002902-0000-1000-8000-00805f9b34fb");
    private static final String NAME_PREFIX = "Claude";
    private static final long SCAN_TIMEOUT_MS = 20000;
    private static final long RETRY_MS = 5000;
    private static final long STEP_TIMEOUT_MS = 30000;

    private final Context context;
    private final Listener listener;
    private final Handler main = new Handler(Looper.getMainLooper());
    private final ArrayDeque<byte[]> outbox = new ArrayDeque<>();
    private final BuddyProtocol.LineAssembler assembler = new BuddyProtocol.LineAssembler();

    private BluetoothGatt gatt;
    private BluetoothGattCharacteristic rx;
    private boolean wanted;
    private boolean scanning;
    private boolean ready;
    private boolean writing;
    private boolean receiverRegistered;
    private int mtu = 23;

    BleLink(Context context, Listener listener) {
        this.context = context.getApplicationContext();
        this.listener = listener;
    }

    boolean isReady() {
        return ready;
    }

    void start() {
        wanted = true;
        if (!receiverRegistered) {
            context.registerReceiver(bondReceiver,
                    new IntentFilter(BluetoothDevice.ACTION_BOND_STATE_CHANGED));
            receiverRegistered = true;
        }
        if (gatt == null && !scanning) {
            scan();
        }
    }

    void stop() {
        wanted = false;
        main.removeCallbacksAndMessages(null);
        stopScan();
        closeGatt();
        if (receiverRegistered) {
            try {
                context.unregisterReceiver(bondReceiver);
            } catch (IllegalArgumentException ignored) {
                // 没注册成功过。
            }
            receiverRegistered = false;
        }
        HubStore.get().setLinkState("已断开");
    }

    /** 排队发送一行（必须以换行结尾）。没连上时直接丢弃：状态类消息下次心跳会重发。 */
    void send(String line) {
        if (!ready || rx == null) {
            return;
        }
        // 写入有应答，每包最多 MTU-3 字节。队列太长说明对端没在收，丢掉旧的。
        if (outbox.size() > 400) {
            outbox.clear();
        }
        outbox.addAll(BuddyProtocol.chunk(line, Math.max(20, mtu - 3)));
        pump();
    }

    private BluetoothAdapter adapter() {
        BluetoothManager manager =
                (BluetoothManager) context.getSystemService(Context.BLUETOOTH_SERVICE);
        return manager == null ? null : manager.getAdapter();
    }

    private void scan() {
        BluetoothAdapter adapter = adapter();
        if (adapter == null || !adapter.isEnabled()) {
            HubStore.get().setLinkState("手机蓝牙没有打开");
            retryLater();
            return;
        }
        BluetoothLeScanner scanner = adapter.getBluetoothLeScanner();
        if (scanner == null) {
            HubStore.get().setLinkState("无法扫描");
            retryLater();
            return;
        }
        try {
            // 不按服务过滤：设备的广播包里放的是名字，服务 UUID 在扫描响应里，
            // 有的手机带过滤条件时收不到。
            List<ScanFilter> filters = Collections.emptyList();
            ScanSettings settings = new ScanSettings.Builder()
                    .setScanMode(ScanSettings.SCAN_MODE_LOW_LATENCY).build();
            scanner.startScan(filters, settings, scanCallback);
        } catch (SecurityException error) {
            HubStore.get().setLinkState("缺少蓝牙权限");
            return;
        }
        scanning = true;
        HubStore.get().setLinkState("正在找小幽…");
        main.postDelayed(scanTimeout, SCAN_TIMEOUT_MS);
    }

    private final Runnable scanTimeout = () -> {
        if (scanning) {
            stopScan();
            HubStore.get().setLinkState("没找到设备，稍后再找");
            retryLater();
        }
    };

    private void stopScan() {
        main.removeCallbacks(scanTimeout);
        if (!scanning) {
            return;
        }
        scanning = false;
        BluetoothAdapter adapter = adapter();
        BluetoothLeScanner scanner = adapter == null ? null : adapter.getBluetoothLeScanner();
        if (scanner != null) {
            try {
                scanner.stopScan(scanCallback);
            } catch (RuntimeException ignored) {
                // 蓝牙刚被关掉时会抛异常，忽略。
            }
        }
    }

    private void retryLater() {
        main.removeCallbacks(retry);
        if (wanted) {
            main.postDelayed(retry, RETRY_MS);
        }
    }

    private final Runnable retry = () -> {
        if (wanted && gatt == null && !scanning) {
            scan();
        }
    };

    private final ScanCallback scanCallback = new ScanCallback() {
        @Override
        public void onScanResult(int callbackType, ScanResult result) {
            main.post(() -> {
                if (!scanning || result == null || result.getDevice() == null) {
                    return;
                }
                String name = result.getScanRecord() == null ? null
                        : result.getScanRecord().getDeviceName();
                List<ParcelUuid> services = result.getScanRecord() == null ? null
                        : result.getScanRecord().getServiceUuids();
                boolean byName = name != null && name.startsWith(NAME_PREFIX);
                boolean byService = services != null && services.contains(new ParcelUuid(SERVICE));
                if (!byName && !byService) {
                    return;
                }
                stopScan();
                connect(result.getDevice(), name);
            });
        }

        @Override
        public void onScanFailed(int errorCode) {
            main.post(() -> {
                scanning = false;
                HubStore.get().setLinkState("扫描失败（" + errorCode + "）");
                retryLater();
            });
        }
    };

    private void connect(BluetoothDevice device, String name) {
        HubStore.get().setLinkState("正在连接 " + (name == null ? device.getAddress() : name));
        ready = false;
        mtu = 23;
        gatt = device.connectGatt(context, false, gattCallback, BluetoothDevice.TRANSPORT_LE);
        armStepTimeout();
    }

    /** 连接、配对、订阅任何一步卡住太久，就断开重来。 */
    private void armStepTimeout() {
        main.removeCallbacks(stepTimeout);
        main.postDelayed(stepTimeout, STEP_TIMEOUT_MS);
    }

    private final Runnable stepTimeout = () -> {
        if (gatt != null && !ready) {
            HubStore.get().log("连接步骤超时，重新开始");
            closeGatt();
            retryLater();
        }
    };

    private void closeGatt() {
        main.removeCallbacks(stepTimeout);
        boolean wasOpen = gatt != null;
        if (gatt != null) {
            try {
                gatt.disconnect();
                gatt.close();
            } catch (RuntimeException ignored) {
                // 已经断开。
            }
        }
        gatt = null;
        rx = null;
        ready = false;
        writing = false;
        outbox.clear();
        assembler.reset();
        if (wasOpen) {
            listener.onClosed();
        }
    }

    private final BroadcastReceiver bondReceiver = new BroadcastReceiver() {
        @Override
        public void onReceive(Context ignored, Intent intent) {
            BluetoothDevice device = intent.getParcelableExtra(BluetoothDevice.EXTRA_DEVICE);
            int state = intent.getIntExtra(BluetoothDevice.EXTRA_BOND_STATE,
                    BluetoothDevice.BOND_NONE);
            int previous = intent.getIntExtra(BluetoothDevice.EXTRA_PREVIOUS_BOND_STATE,
                    BluetoothDevice.BOND_NONE);
            if (gatt == null || device == null
                    || !device.getAddress().equals(gatt.getDevice().getAddress())) {
                return;
            }
            if (state == BluetoothDevice.BOND_BONDED) {
                HubStore.get().setLinkState("配对成功，正在准备");
                armStepTimeout();
                gatt.discoverServices();
            } else if (state == BluetoothDevice.BOND_NONE
                    && previous == BluetoothDevice.BOND_BONDING) {
                HubStore.get().setLinkState("配对没有完成");
                closeGatt();
                retryLater();
            }
        }
    };

    private final BluetoothGattCallback gattCallback = new BluetoothGattCallback() {
        @Override
        public void onConnectionStateChange(BluetoothGatt source, int status, int newState) {
            main.post(() -> {
                if (source != gatt) {
                    return;
                }
                if (newState == BluetoothProfile.STATE_CONNECTED) {
                    armStepTimeout();
                    source.requestMtu(247);
                } else if (newState == BluetoothProfile.STATE_DISCONNECTED) {
                    HubStore.get().setLinkState("连接断开（" + status + "），稍后重连");
                    closeGatt();
                    retryLater();
                }
            });
        }

        @Override
        public void onMtuChanged(BluetoothGatt source, int newMtu, int status) {
            main.post(() -> {
                if (source != gatt) {
                    return;
                }
                if (status == BluetoothGatt.GATT_SUCCESS) {
                    mtu = newMtu;
                }
                BluetoothDevice device = source.getDevice();
                if (device.getBondState() == BluetoothDevice.BOND_BONDED) {
                    source.discoverServices();
                } else if (device.getBondState() == BluetoothDevice.BOND_NONE) {
                    HubStore.get().setLinkState("请输入小幽屏幕上的六位数字");
                    if (!device.createBond()) {
                        HubStore.get().log("没能发起配对");
                    }
                }
                // BOND_BONDING：等配对结果的广播。
            });
        }

        @Override
        public void onServicesDiscovered(BluetoothGatt source, int status) {
            main.post(() -> {
                if (source != gatt) {
                    return;
                }
                BluetoothGattService service = source.getService(SERVICE);
                BluetoothGattCharacteristic tx =
                        service == null ? null : service.getCharacteristic(TX);
                rx = service == null ? null : service.getCharacteristic(RX);
                BluetoothGattDescriptor cccd = tx == null ? null : tx.getDescriptor(CCCD);
                if (rx == null || cccd == null) {
                    HubStore.get().setLinkState("这台设备不是小幽");
                    closeGatt();
                    retryLater();
                    return;
                }
                rx.setWriteType(BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT);
                source.setCharacteristicNotification(tx, true);
                cccd.setValue(BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE);
                if (!source.writeDescriptor(cccd)) {
                    HubStore.get().log("订阅请求没有发出");
                }
            });
        }

        @Override
        public void onDescriptorWrite(BluetoothGatt source, BluetoothGattDescriptor descriptor,
                                      int status) {
            main.post(() -> {
                if (source != gatt) {
                    return;
                }
                if (status != BluetoothGatt.GATT_SUCCESS) {
                    // 多半是加密还没建立好；断开后会重新走一遍。
                    HubStore.get().setLinkState("订阅失败（" + status + "），重试");
                    closeGatt();
                    retryLater();
                    return;
                }
                main.removeCallbacks(stepTimeout);
                ready = true;
                HubStore.get().setLinkState("已连接");
                listener.onReady();
            });
        }

        @Override
        public void onCharacteristicChanged(BluetoothGatt source,
                                            BluetoothGattCharacteristic characteristic) {
            byte[] value = characteristic.getValue();
            byte[] copy = value == null ? new byte[0] : value.clone();
            main.post(() -> {
                if (source != gatt) {
                    return;
                }
                for (String line : assembler.feed(copy)) {
                    listener.onLine(line);
                }
            });
        }

        @Override
        public void onCharacteristicWrite(BluetoothGatt source,
                                          BluetoothGattCharacteristic characteristic, int status) {
            main.post(() -> {
                if (source != gatt) {
                    return;
                }
                writing = false;
                if (status != BluetoothGatt.GATT_SUCCESS) {
                    outbox.clear();
                    return;
                }
                pump();
            });
        }
    };

    private void pump() {
        if (writing || !ready || gatt == null || rx == null) {
            return;
        }
        byte[] next = outbox.poll();
        if (next == null) {
            return;
        }
        rx.setValue(next);
        if (gatt.writeCharacteristic(rx)) {
            writing = true;
        } else {
            // 协议栈正忙：这一行作废，下次心跳会带上最新状态。
            outbox.clear();
        }
    }
}
