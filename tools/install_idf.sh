#!/usr/bin/env bash
# Installs ESP-IDF 5.5.3 for this firmware (ESP32-C3 only) on macOS or Linux,
# so that the computer that runs the Xiaoyou runtime can build the firmware
# itself and nothing has to be pushed to GitHub.
#
#   tools/install_idf.sh             install into ~/esp/esp-idf-v5.5.3
#   tools/install_idf.sh --check     only say whether it is installed and usable
#
# Needs git and python3 (3.9 or newer). Downloads about 2 GB; cmake and ninja
# come with it, nothing is installed system-wide. Running it again continues an
# interrupted installation. Set IDF_GITHUB_ASSETS=dl.espressif.cn/github_assets
# to download the tools from Espressif's mirror in China.
set -euo pipefail

VERSION="v5.5.3"
DEST="${POCKET_IDF_DIR:-${HOME}/esp/esp-idf-${VERSION}}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

say() { printf '%s\n' "$*"; }
fail() { printf '没成功：%s\n' "$*" >&2; exit 1; }

usable() {
    [[ -f "${DEST}/export.sh" ]] || return 1
    bash -c "source '${DEST}/export.sh' >/dev/null 2>&1 && command -v riscv32-esp-elf-gcc >/dev/null && command -v cmake >/dev/null && command -v ninja >/dev/null && idf.py --version >/dev/null 2>&1"
}

if [[ "${1:-}" == "--check" ]]; then
    if usable; then say "ESP-IDF ${VERSION} 装好了：${DEST}"; exit 0; fi
    say "ESP-IDF ${VERSION} 还没装好（${DEST}）"; exit 1
fi

command -v git >/dev/null 2>&1 || fail "找不到 git"
command -v python3 >/dev/null 2>&1 || fail "找不到 python3"
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' ||
    fail "python3 要 3.9 或更新"

if usable; then
    say "ESP-IDF ${VERSION} 已经装好：${DEST}"
else
    mkdir -p "$(dirname "${DEST}")"
    if [[ ! -d "${DEST}/.git" ]]; then
        say "下载 ESP-IDF ${VERSION}（约 1 GB，要几分钟）……"
        git clone --depth 1 --branch "${VERSION}" https://github.com/espressif/esp-idf.git "${DEST}" ||
            fail "下载 ESP-IDF 失败；检查网络后再运行一次"
    fi
    say "下载它带的组件……"
    git -C "${DEST}" submodule update --init --recursive --depth 1 ||
        fail "下载组件失败；再运行一次会接着下"
    say "安装 ESP32-C3 的编译工具（约 1 GB）……"
    "${DEST}/install.sh" esp32c3 || fail "安装编译工具失败；再运行一次会接着装"
    # cmake and ninja are optional tools of ESP-IDF; take its own copies so that
    # nothing else has to be installed on this computer.
    python3 "${DEST}/tools/idf_tools.py" install cmake ninja || fail "安装 cmake / ninja 失败"
    usable || fail "装完了但用不起来；把上面的输出发给开发者"
    say "ESP-IDF ${VERSION} 装好了：${DEST}"
fi

say ""
say "要让小幽 Runtime 在这台电脑上自己编译固件，在 runtime/config.json 的 firmware 里写："
say "  \"source_dir\": \"..\","
say "  \"build_command\": [\"bash\", \"-c\", \"source '${DEST}/export.sh' >/dev/null && idf.py build\"]"
say "然后试一次：cd ${ROOT}/runtime && python3 -m xiaoyou_runtime firmware build --note 本机编译"
