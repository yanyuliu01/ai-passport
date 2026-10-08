#!/usr/bin/env bash
set -euo pipefail

mode="${1:---all}"
repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"

usage() {
    echo "Usage: $0 [--all|--static|--firmware]" >&2
}

# Claude Pocket application logic. The protocol, state-machine and orchestrator
# tests parse real JSON and need cJSON, which ships with ESP-IDF; without
# IDF_PATH they are reported as skipped instead of silently passing.
cjson_dir() {
    if [[ -n "${IDF_PATH:-}" && -f "${IDF_PATH}/components/json/cJSON/cJSON.c" ]]; then
        echo "${IDF_PATH}/components/json/cJSON"
    fi
}

run_app_host_tests() {
    local test_dir="$1"
    local cjson
    local name

    "${CC:-cc}" -std=c11 -Wall -Wextra -Werror -Imain \
        tests/test_pocket_view.c main/pocket_view.c \
        -o "${test_dir}/test_pocket_view"
    "${test_dir}/test_pocket_view"
    "${CC:-cc}" -std=c11 -Wall -Wextra -Werror -Imain \
        tests/test_pocket_pet.c main/pocket_pet.c \
        -o "${test_dir}/test_pocket_pet"
    "${test_dir}/test_pocket_pet"
    "${CC:-cc}" -std=c11 -Wall -Wextra -Werror -Imain \
        tests/test_buddy_line.c main/buddy_line.c \
        -o "${test_dir}/test_buddy_line"
    "${test_dir}/test_buddy_line"
    "${CC:-cc}" -std=c11 -Wall -Wextra -Werror -Itests/test_shims -Imain \
        -DBUDDY_BLE_HOST_TEST \
        tests/test_buddy_ble.c main/buddy_ble.c main/buddy_ble_store.c \
        main/buddy_ble_lifecycle.c \
        -o "${test_dir}/test_buddy_ble"
    "${test_dir}/test_buddy_ble"
    "${CC:-cc}" -std=c11 -Wall -Wextra -Werror -Itests/test_shims -Imain \
        -DBUDDY_SETTINGS_TESTING \
        tests/test_buddy_settings.c main/buddy_settings.c \
        -o "${test_dir}/test_buddy_settings"
    "${test_dir}/test_buddy_settings"
    "${CC:-cc}" -std=c11 -Wall -Wextra -Werror -Itests/test_shims -Imain \
        tests/test_buddy_app_logic.c main/buddy_app_logic.c \
        -o "${test_dir}/test_buddy_app_logic"
    "${test_dir}/test_buddy_app_logic"

    cjson="$(cjson_dir)"
    if [[ -z "${cjson}" ]]; then
        echo "Host tests (protocol/state/orchestrator): SKIPPED - set IDF_PATH for cJSON" >&2
        return 0
    fi
    "${CC:-cc}" -std=c11 -w -c "${cjson}/cJSON.c" -o "${test_dir}/cJSON.o"
    for name in protocol state orchestrator; do
        "${CC:-cc}" -std=c11 -Wall -Wextra -Werror -Itests/test_shims -Imain -I"${cjson}" \
            "tests/test_buddy_${name}.c" main/buddy_protocol.c main/buddy_state.c \
            main/buddy_orchestrator.c "${test_dir}/cJSON.o" -lm \
            -ffunction-sections -fdata-sections -Wl,--gc-sections \
            -o "${test_dir}/test_buddy_${name}"
        "${test_dir}/test_buddy_${name}"
    done
    echo "Host tests (protocol/state/orchestrator): PASS"
}

run_static_checks() {
    local actionlint_bin
    local test_dir

    python3 tools/check_repo.py

    actionlint_bin="${ACTIONLINT_BIN:-}"
    if [[ -z "${actionlint_bin}" ]]; then
        actionlint_bin="$(command -v actionlint || true)"
    fi
    if [[ -z "${actionlint_bin}" || ! -x "${actionlint_bin}" ]]; then
        actionlint_bin="$(./tools/install-actionlint.sh)"
    fi
    "${actionlint_bin}" -color .github/workflows/*.yml

    test_dir="$(mktemp -d /tmp/ai-passport-host-tests.XXXXXX)"
    "${CC:-cc}" -std=c11 -Wall -Wextra -Werror -Icomponents/bsp/src \
        tests/test_bsp_display_rounding.c components/bsp/src/bsp_display_rounding.c \
        -o "${test_dir}/test_bsp_display_rounding"
    "${test_dir}/test_bsp_display_rounding"
    "${CC:-cc}" -std=c11 -Wall -Wextra -Werror -Icomponents/bsp/src \
        tests/test_bsp_es8311_sleep_check.c components/bsp/src/bsp_es8311_sleep_check.c \
        -o "${test_dir}/test_bsp_es8311_sleep_check"
    "${test_dir}/test_bsp_es8311_sleep_check"
    "${CC:-cc}" -std=c11 -Wall -Wextra -Werror \
        -Itests/bsp_stubs -Icomponents/bsp/include \
        tests/test_bsp_button.c -o "${test_dir}/test_bsp_button"
    "${test_dir}/test_bsp_button"
    "${CC:-cc}" -std=c11 -Wall -Wextra -Werror \
        -Itests/bsp_stubs -Icomponents/bsp/include \
        tests/test_bsp_lvgl_init.c components/bsp/src/bsp_display_rounding.c \
        -o "${test_dir}/test_bsp_lvgl_init"
    "${test_dir}/test_bsp_lvgl_init"
    "${CC:-cc}" -std=c11 -Wall -Wextra -Werror \
        -Itests/audio_stubs -Icomponents/bsp/include -Icomponents/bsp/src \
        tests/test_bsp_audio_recovery.c components/bsp/src/bsp_es8311_sleep_check.c \
        -o "${test_dir}/test_bsp_audio_recovery"
    "${test_dir}/test_bsp_audio_recovery"
    run_app_host_tests "${test_dir}"
    PYTHONDONTWRITEBYTECODE=1 python3 tests/test_pocket_fonts.py
    PYTHONDONTWRITEBYTECODE=1 python3 tests/test_check_repo.py
    PYTHONDONTWRITEBYTECODE=1 python3 tests/test_verify_firmware.py
    PYTHONDONTWRITEBYTECODE=1 python3 tests/test_archive_firmware.py
    PYTHONDONTWRITEBYTECODE=1 python3 tests/test_install_passport_skills.py
    rm -rf "${test_dir}"
    echo "Host tests: PASS"
}

run_firmware_checks() (
    local validation_build_dir

    if ! command -v idf.py >/dev/null 2>&1; then
        echo "ERROR: idf.py is not available; activate ESP-IDF 5.5.3 first." >&2
        return 1
    fi

    if [[ -z "$(cjson_dir)" ]]; then
        echo "ERROR: IDF_PATH does not provide cJSON; protocol host tests cannot run." >&2
        return 1
    fi
    if [[ "${mode}" == "--firmware" ]]; then
        # --all already ran these in the static stage.
        local host_test_dir
        host_test_dir="$(mktemp -d /tmp/ai-passport-host-tests.XXXXXX)"
        run_app_host_tests "${host_test_dir}"
        rm -rf "${host_test_dir}"
    fi

    validation_build_dir="$(mktemp -d /tmp/ai-passport-firmware.XXXXXX)"
    trap 'case "${validation_build_dir}" in /tmp/ai-passport-firmware.*) rm -rf -- "${validation_build_dir}" ;; esac' EXIT

    SDKCONFIG_DEFAULTS="${repo_root}/sdkconfig.defaults" \
        idf.py -B "${validation_build_dir}" \
        -D "SDKCONFIG=${validation_build_dir}/sdkconfig" build
    idf.py -B "${validation_build_dir}" merge-bin \
        -o "${validation_build_dir}/FoloToy-AI-Passport-full.bin"
    python3 tools/verify_firmware.py "${validation_build_dir}"
    PYTHONDONTWRITEBYTECODE=1 python3 tools/archive_firmware.py create \
        "${validation_build_dir}" --archive-root "${repo_root}/build/firmware"
    mkdir -p "${repo_root}/build"
    install -m 0644 \
        "${validation_build_dir}/FoloToy-AI-Passport-full.bin" \
        "${repo_root}/build/FoloToy-AI-Passport-full.bin"
    echo "Firmware build: PASS"
)

cd "${repo_root}"
case "${mode}" in
    --all)
        run_static_checks
        run_firmware_checks
        ;;
    --static)
        run_static_checks
        ;;
    --firmware)
        run_firmware_checks
        ;;
    *)
        usage
        exit 2
        ;;
esac
