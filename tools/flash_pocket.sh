#!/usr/bin/env bash
# Writes a Claude Pocket firmware image to an AI Passport over USB (macOS, Linux).
#
#   tools/flash_pocket.sh                    the newest "firmware-build-N" prerelease
#   tools/flash_pocket.sh FILE.bin           a merged image you already have
#   tools/flash_pocket.sh --port PORT ...    when more than one serial device is connected
#   tools/flash_pocket.sh --yes ...          do not ask before writing
#
# The image is the merged one (bootloader, partition table, application) and is
# written at 0x0. That resets the settings area: the device forgets its name and
# its Bluetooth pairing and has to be paired again. Nothing is erased beyond
# what the image covers, and the previous firmware is not backed up.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CACHE="${XDG_CACHE_HOME:-${HOME}/.cache}/pocket-flash"
ASSET="FoloToy-AI-Passport-full.bin"
port=""
image=""
assume_yes=0

say() { printf '%s\n' "$*"; }
fail() { printf '没成功：%s\n' "$*" >&2; exit 1; }

while [[ $# -gt 0 ]]; do
    case "$1" in
        --port) [[ $# -ge 2 ]] || fail "--port 后面要跟串口，比如 /dev/cu.usbmodem101"; port="$2"; shift 2 ;;
        --yes|-y) assume_yes=1; shift ;;
        -h|--help) sed -n '2,13p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
        -*) fail "不认识的参数 $1" ;;
        *) image="$1"; shift ;;
    esac
done

command -v python3 >/dev/null 2>&1 || fail "找不到 python3"

# ---- the image ----
repo_slug() {
    if [[ -n "${POCKET_FIRMWARE_REPO:-}" ]]; then
        printf '%s\n' "${POCKET_FIRMWARE_REPO}"
        return
    fi
    git -C "${ROOT}" remote -v 2>/dev/null |
        sed -nE 's#.*github\.com[:/]([^/ ]+/[^/ ]+) \(fetch\)#\1#p' | sed -E 's#\.git$##' | head -n 1
}

if [[ -z "${image}" ]]; then
    slug="$(repo_slug)"
    [[ -n "${slug}" ]] || fail "不知道去哪个 GitHub 仓库找固件；设置 POCKET_FIRMWARE_REPO=用户名/仓库名，或者直接给一个 .bin 文件"
    mkdir -p "${CACHE}"
    say "在 ${slug} 找最新的固件……"
    listing="$(curl -fsSL "https://api.github.com/repos/${slug}/releases?per_page=30")" ||
        fail "连不上 GitHub。可以在浏览器里下载 ${ASSET}，再运行：tools/flash_pocket.sh 下载的文件"
    found="$(printf '%s' "${listing}" | python3 -c '
import json, sys
asset = sys.argv[1]
for release in json.load(sys.stdin):
    if not str(release.get("tag_name", "")).startswith("firmware-build-"):
        continue
    urls = {item["name"]: item["browser_download_url"] for item in release.get("assets", [])}
    if asset in urls:
        print(release["tag_name"])
        print(urls[asset])
        print(urls.get(asset + ".sha256", ""))
        break
' "${ASSET}")"
    [[ -n "${found}" ]] || fail "${slug} 里还没有 firmware-build-N 这样的预发布版本"
    tag="$(printf '%s\n' "${found}" | sed -n 1p)"
    url="$(printf '%s\n' "${found}" | sed -n 2p)"
    sum_url="$(printf '%s\n' "${found}" | sed -n 3p)"
    image="${CACHE}/${tag}.bin"
    say "下载 ${tag} ……"
    curl -fL --progress-bar -o "${image}.part" "${url}" || fail "下载失败：${url}"
    mv "${image}.part" "${image}"
    if [[ -n "${sum_url}" ]]; then
        expected="$(curl -fsSL "${sum_url}" | cut -d' ' -f1)" || expected=""
    fi
fi

[[ -f "${image}" ]] || fail "找不到文件 ${image}"
# A merged ESP32-C3 image starts with the bootloader at 0x0, whose first byte is
# the image magic, and carries the partition table at 0x8000. An application-only
# image must never be written at 0x0.
actual="$(python3 -c '
import hashlib, sys
data = open(sys.argv[1], "rb").read()
if len(data) < 256 * 1024 or len(data) > 8 * 1024 * 1024 or data[0] != 0xE9:
    sys.exit("not a merged image")
if data[0x8000:0x8002] != bytes([0xAA, 0x50]):
    sys.exit("no partition table at 0x8000")
print(hashlib.sha256(data).hexdigest())
' "${image}" 2>/dev/null)" || fail "${image} 不像是合并镜像（开头不是引导程序，或者 0x8000 处没有分区表），不能写到 0x0"
if [[ -n "${expected:-}" && "${expected}" != "${actual}" ]]; then
    fail "下载的文件校验值不对（应为 ${expected}，实际 ${actual}），请重试"
fi

# ---- the port ----
if [[ -z "${port}" ]]; then
    count=0
    for candidate in /dev/cu.usbmodem* /dev/cu.usbserial* /dev/cu.wchusbserial* /dev/ttyACM* /dev/ttyUSB*; do
        if [[ -e "${candidate}" ]]; then
            count=$((count + 1))
            port="${candidate}"
            say "  发现串口 ${candidate}"
        fi
    done
    if [[ ${count} -eq 0 ]]; then
        fail "没有发现设备。请先把设备开机，再用能传数据的线（不能是只充电的线）接到电脑的 USB 口，然后重新运行"
    fi
    if [[ ${count} -gt 1 ]]; then
        fail "发现了不止一个串口，不确定哪个是 Passport。拔掉别的设备，或者用 --port 指定"
    fi
fi
[[ -e "${port}" ]] || fail "找不到串口 ${port}"

# ---- esptool ----
python="python3"
if ! python3 -c 'import esptool' >/dev/null 2>&1; then
    if [[ ! -x "${CACHE}/venv/bin/python" ]]; then
        say "第一次使用：在 ${CACHE}/venv 里安装 esptool……"
        mkdir -p "${CACHE}"
        python3 -m venv "${CACHE}/venv" || fail "建不了 Python 虚拟环境"
        "${CACHE}/venv/bin/python" -m pip install --quiet --upgrade pip esptool ||
            fail "装不上 esptool（需要能访问 PyPI）"
    fi
    python="${CACHE}/venv/bin/python"
fi

say ""
say "固件：${image}"
say "校验：${actual}"
say "串口：${port}"
say "写入位置 0x0。设备上的名称和蓝牙配对会被清掉，刷完要重新配对。"
if [[ ${assume_yes} -ne 1 ]]; then
    printf '继续吗？输入 y 回车：'
    read -r answer || answer=""
    [[ "${answer}" == "y" || "${answer}" == "Y" ]] || { say "没有写入。"; exit 0; }
fi

# esptool 5 renamed the command; older versions only know the old spelling.
write="write_flash"
if "${python}" -m esptool --help 2>&1 | grep -q 'write-flash'; then
    write="write-flash"
fi
"${python}" -m esptool --chip esp32c3 --port "${port}" --baud 460800 "${write}" 0x0 "${image}" ||
    fail "写入失败。检查数据线和端口是否被别的程序（串口监视器）占用，然后重试"

say ""
say "刷好了，设备会自己重启。接下来："
say "  1. 手机的蓝牙设置里把原来的 Claude-XXXXXX 取消配对（忽略此设备）"
say "  2. 打开小幽中枢，连接设备，输入设备上显示的 6 位配对码"
say "  3. 在跑小幽 Runtime 的电脑上（runtime/ 目录里）运行"
say "       python3 -m xiaoyou_runtime firmware fetch"
say "     把这一版记进固件仓库。之后的新固件经蓝牙传，不用再插线；记进去了以后才能换回这一版"
