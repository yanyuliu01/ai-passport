#!/usr/bin/env bash
# Runs the firmware updater (main/pocket_update.c, unchanged) on an emulated
# ESP32-C3 with the real ESP-IDF bootloader and OTA code: a transfer with lost,
# repeated and reordered frames and an interrupted link, the restart into the
# new slot, going back when the new image is not confirmed, confirming, and
# switching back to the previous slot. There is no radio in the emulator;
# main/test_main.c plays the phone.
#
#   tests/update_emulator/run.sh [BUILD_DIR]
#
# Needs an activated ESP-IDF 5.5 environment (idf.py) and Espressif's QEMU
# (qemu-system-riscv32 with the esp32c3 machine; set QEMU to its path if it is
# not on PATH). It is not part of tools/validate.sh.
#
# The emulator sometimes stops delivering timer interrupts during a flash
# operation, after which everything in it waits forever (the tick count stays
# frozen while the timer keeps its interrupt raised). When the console has been
# silent for too long the emulator is started again on the same flash contents.
# To the firmware that is a power cut, which it has to survive anyway.
set -euo pipefail

here="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
build="${1:-${TMPDIR:-/tmp}/pocket-update-emulator}"
qemu="${QEMU:-qemu-system-riscv32}"
max_restarts="${MAX_RESTARTS:-15}"

command -v idf.py >/dev/null 2>&1 || { echo "idf.py not found: activate ESP-IDF first" >&2; exit 2; }
command -v "${qemu}" >/dev/null 2>&1 || { echo "${qemu} not found: install Espressif's QEMU or set QEMU" >&2; exit 2; }

mkdir -p "${build}"
echo "Building the emulator test in ${build} ..."
idf.py -C "${here}" -B "${build}" -D SDKCONFIG="${build}/sdkconfig" \
    build merge-bin -o "${build}/merged.bin" > "${build}/build.log" 2>&1 ||
    { echo "Build failed; see ${build}/build.log" >&2; tail -n 20 "${build}/build.log" >&2; exit 1; }

# An 8 MB flash image, and eFuses that say chip revision v0.3.
python3 - "${build}" <<'PY'
import sys
from pathlib import Path

build = Path(sys.argv[1])
image = (build / "merged.bin").read_bytes()
(build / "flash.bin").write_bytes(image + b"\xff" * (8 * 1024 * 1024 - len(image)))
efuse = bytearray(1024)
efuse[38] = 0x0C
(build / "efuse.bin").write_bytes(bytes(efuse))
PY

rm -f "${build}"/console-*.log
qemu_pid=""
stop_qemu() {
    if [[ -n "${qemu_pid}" ]]; then
        kill "${qemu_pid}" 2>/dev/null || true
        wait "${qemu_pid}" 2>/dev/null || true
        qemu_pid=""
    fi
}
trap stop_qemu EXIT

show() {
    tr -d '\r' < "$1" | grep -E "ota-test:|update:|Loaded app|Rollback" || true
}

restarts=0
run=0
while :; do
    run=$((run + 1))
    log="${build}/console-${run}.log"
    "${qemu}" -M esp32c3 -m 4M \
        -drive "file=${build}/flash.bin,if=mtd,format=raw" \
        -drive "file=${build}/efuse.bin,if=none,format=raw,id=efuse" \
        -global driver=nvram.esp32c3.efuse,property=drive,value=efuse \
        -global driver=timer.esp32c3.timg,property=wdt_disable,value=true \
        -nic none -nographic -serial "file:${log}" -monitor none > "${build}/qemu.out" 2>&1 &
    qemu_pid=$!
    quiet=0
    last=-1
    while :; do
        sleep 3
        if grep -q "ALL STEPS PASSED" "${log}" 2>/dev/null; then
            stop_qemu
            show "${log}"
            echo "Firmware update emulator test: PASS (${restarts} emulator restarts)"
            exit 0
        fi
        if grep -q "TEST FAILED" "${log}" 2>/dev/null; then
            stop_qemu
            show "${log}"
            echo "Firmware update emulator test: FAIL (console logs in ${build})" >&2
            exit 1
        fi
        if ! kill -0 "${qemu_pid}" 2>/dev/null; then
            echo "The emulator exited; see ${build}/qemu.out" >&2
            exit 1
        fi
        size="$(wc -c < "${log}" 2>/dev/null || echo 0)"
        if [[ "${size}" == "${last}" ]]; then quiet=$((quiet + 3)); else quiet=0; last="${size}"; fi
        limit=30
        # One step is silent on purpose while the confirmation deadline (180 s) runs.
        if tail -n 3 "${log}" 2>/dev/null | grep -q "not confirming"; then limit=240; fi
        if [[ ${quiet} -ge ${limit} ]]; then break; fi
    done
    stop_qemu
    show "${log}"
    restarts=$((restarts + 1))
    echo "-- the emulator went silent for ${quiet} s; starting it again on the same flash (${restarts})"
    if [[ ${restarts} -ge ${max_restarts} ]]; then
        echo "Firmware update emulator test: GAVE UP after ${restarts} emulator restarts" >&2
        exit 2
    fi
done
