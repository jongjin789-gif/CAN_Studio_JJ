import PyInstaller.__main__
import os
import platform
import sys
import json
import importlib.util
import shlex
import shutil
import stat
import time

from src.app_info import (APP_FILE_PREFIX, VIEWER_FILE_PREFIX, PK_FILE_NAME,
                          pk_path as resolve_pk_path)

try:
    from PyInstaller.utils.hooks import collect_submodules
except ImportError:
    collect_submodules = None

# =====================================================================
# 소프트웨어 버전 설정 (빌드 시 생성되는 실행 파일명에 반영됩니다)
# =====================================================================
APP_VERSION = "R100"

# Linux 배포 PC에 필요한 시스템 패키지(빌드 결과물 실행용, 소스/파이썬 불필요).
# 배포 폴더의 install_runtime.sh 와 README 가 이 목록을 함께 사용합니다.
# 실행 파일에 포함되지 않는 라이브러리(ldd 로 확인: libGL/EGL, libdrm, libxcb, wayland)와
# 프로그램이 호출하는 명령(ip, sudo), 글꼴, 점검 도구를 설치합니다.
LINUX_RUNTIME_APT_PACKAGES = [
    # CAN 설정/점검: ip 명령(필수), modprobe(vcan 등 모듈 로드), candump/cansend(점검용)
    "iproute2", "kmod", "can-utils",
    # 화면 표시(X11/xcb)
    "libxcb1", "libxcb-xinerama0", "libxcb-icccm4", "libxcb-image0", "libxcb-keysyms1",
    "libxcb-randr0", "libxcb-render-util0", "libxcb-xfixes0", "libxcb-shape0", "libxcb-xkb1",
    "libxkbcommon-x11-0",
    # 화면 표시(Wayland) 및 OpenGL
    "libwayland-client0", "libwayland-cursor0", "libwayland-egl1",
    "libgl1", "libegl1", "libopengl0", "libdrm2",
    # 글꼴(한글 표시)
    "fontconfig", "fonts-nanum",
]

# run.sh / install_runtime*.sh 공통: 현재 PC의 CPU에 맞는 실행 파일을 bin/<CPU>/ 에서 골라
# 패키지 최상위 폴더로 복사합니다(세션/설정/로그가 최상위 폴더에 저장되도록).
_SELECT_BINARY_SNIPPET = r'''EXE="__EXE__"
HOST_ARCH="$(uname -m)"
SRC="bin/$HOST_ARCH/$EXE"
if [ ! -f "$SRC" ]; then
    echo "[오류] 이 PC의 CPU($HOST_ARCH)용 실행 파일이 패키지에 없습니다."
    echo "       패키지에 포함된 CPU: $(ls bin 2>/dev/null | tr '\n' ' ')"
    exit 1
fi
BUILD_GLIBC=""; BUILD_OS=""
if [ -f "bin/$HOST_ARCH/BUILD_INFO.txt" ]; then
    . "bin/$HOST_ARCH/BUILD_INFO.txt"
fi
HOST_GLIBC="$(getconf GNU_LIBC_VERSION 2>/dev/null | awk '{print $2}')"
if [ -n "$BUILD_GLIBC" ] && [ -n "$HOST_GLIBC" ] &&
   [ "$(printf '%s\n%s\n' "$BUILD_GLIBC" "$HOST_GLIBC" | sort -V | head -1)" != "$BUILD_GLIBC" ]; then
    echo "[오류] 이 PC의 리눅스가 너무 오래되어 실행할 수 없습니다."
    echo "       필요: glibc $BUILD_GLIBC 이상 (빌드 OS: $BUILD_OS) / 현재: glibc $HOST_GLIBC"
    exit 1
fi
if ! cmp -s "$SRC" "$EXE"; then
    cp -f "$SRC" "$EXE.tmp"
    chmod +x "$EXE.tmp"
    mv -f "$EXE.tmp" "$EXE"
fi
'''

# 영어판 설치 스크립트(install_runtime_en.sh)용 - 한글을 표시하지 못하는 터미널 대비
_SELECT_BINARY_SNIPPET_EN = r'''EXE="__EXE__"
HOST_ARCH="$(uname -m)"
SRC="bin/$HOST_ARCH/$EXE"
if [ ! -f "$SRC" ]; then
    echo "[ERROR] This package has no executable for the CPU of this PC ($HOST_ARCH)."
    echo "        CPUs included in this package: $(ls bin 2>/dev/null | tr '\n' ' ')"
    exit 1
fi
BUILD_GLIBC=""; BUILD_OS=""
if [ -f "bin/$HOST_ARCH/BUILD_INFO.txt" ]; then
    . "bin/$HOST_ARCH/BUILD_INFO.txt"
fi
HOST_GLIBC="$(getconf GNU_LIBC_VERSION 2>/dev/null | awk '{print $2}')"
if [ -n "$BUILD_GLIBC" ] && [ -n "$HOST_GLIBC" ] &&
   [ "$(printf '%s\n%s\n' "$BUILD_GLIBC" "$HOST_GLIBC" | sort -V | head -1)" != "$BUILD_GLIBC" ]; then
    echo "[ERROR] The Linux on this PC is too old to run this program."
    echo "        Required: glibc $BUILD_GLIBC or later (build OS: $BUILD_OS) / Current: glibc $HOST_GLIBC"
    exit 1
fi
if ! cmp -s "$SRC" "$EXE"; then
    cp -f "$SRC" "$EXE.tmp"
    chmod +x "$EXE.tmp"
    mv -f "$EXE.tmp" "$EXE"
fi
'''

_RUN_SCRIPT_TEMPLATE = r'''#!/bin/bash
# __EXE__ 실행 스크립트 - 이 PC의 CPU(x86_64 / aarch64)에 맞는 실행 파일을 자동으로 골라 실행합니다.
#   사용법: ./run.sh
set -e
cd "$(dirname "$(readlink -f "$0")")"
__SELECT__
exec "./$EXE" "$@"
'''

# 한글판 설치 스크립트. 4/6 단계에서 한글 입력기(ibus-hangul)를 기본으로 설치합니다(--no-korean 으로 생략).
# 실행 파일에 Qt ibus 입력 플러그인이 포함되어 있어 배포판/데스크톱과 관계없이 ibus 를 사용합니다.
_RUNTIME_INSTALLER_TEMPLATE = r'''#!/bin/bash
# __EXE__ 실행 환경 설치 스크립트 (PC당 최초 1회)
# 소스 코드나 파이썬 없이, 이 패키지의 실행 파일을 쓰는 데 필요한 시스템 구성만 설치합니다.
#   사용법: ./install_runtime.sh              (한글 글꼴 + 한글 입력기까지 설치)
#           ./install_runtime.sh --no-korean  (한글 입력기 설치/설정 생략)
set -e
cd "$(dirname "$(readlink -f "$0")")"

KOREAN_INPUT=1
for arg in "$@"; do
    case "$arg" in
        --no-korean) KOREAN_INPUT=0 ;;
        -h|--help)
            echo "사용법: $0 [--no-korean]"
            echo "  --no-korean   한글 입력기(ibus-hangul) 설치/설정을 건너뜁니다"
            exit 0 ;;
        *) echo "[오류] 알 수 없는 옵션: $arg  (도움말: $0 --help)"; exit 1 ;;
    esac
done

echo "=== 1/6 PC 확인 및 실행 파일 선택 ==="
__SELECT__
if ! command -v apt-get > /dev/null 2>&1; then
    echo "[오류] apt-get 이 없습니다. Ubuntu/Debian 계열 리눅스에서 실행해 주세요."
    exit 1
fi
if [ "$(id -u)" -eq 0 ]; then
    SUDO=""
    echo "[안내] root 로 실행 중입니다. 프로그램 자체는 일반 사용자로 실행하세요."
elif command -v sudo > /dev/null 2>&1; then
    SUDO="sudo"
else
    echo "[오류] sudo 가 없습니다. root 로 'apt-get install sudo' 후 사용자를 sudo 그룹에 추가하세요."
    exit 1
fi
echo "CPU: $HOST_ARCH / OS: $(. /etc/os-release && echo "$PRETTY_NAME") / glibc: ${HOST_GLIBC:-?}"
echo "실행 파일: bin/$HOST_ARCH/$EXE -> ./$EXE"

echo "=== 2/6 시스템 패키지 설치 (sudo 비밀번호가 필요할 수 있습니다) ==="
$SUDO apt-get update
$SUDO apt-get install -y __PACKAGES__
fc-cache -f > /dev/null 2>&1 || true

echo "=== 3/6 CAN 커널 드라이버 확인 ==="
# 모든 CAN 장치는 SocketCAN(can0~)으로 사용합니다. 제조사 드라이버는 설치하지 않습니다.
# Ubuntu 는 USB-CAN / vcan 커널 모듈이 linux-modules-extra 패키지에 들어 있습니다.
MISSING=0
for m in vcan peak_usb kvaser_usb gs_usb; do
    modinfo "$m" > /dev/null 2>&1 || MISSING=1
done
if [ "$MISSING" = "1" ]; then
    EXTRA_PKG="linux-modules-extra-$(uname -r)"
    if apt-cache show "$EXTRA_PKG" > /dev/null 2>&1; then
        echo "CAN 드라이버 패키지를 설치합니다: $EXTRA_PKG"
        $SUDO apt-get install -y "$EXTRA_PKG" || echo "[경고] $EXTRA_PKG 설치 실패. 실제 CAN 장치 인식이 안 될 수 있습니다."
    fi
fi
for item in "can:SocketCAN 기본" "can_raw:SocketCAN 기본" "vcan:가상 CAN" \
            "peak_usb:PCAN-USB" "kvaser_usb:Kvaser USB" "gs_usb:candleLight"; do
    m="${item%%:*}"; desc="${item#*:}"
    if modinfo "$m" > /dev/null 2>&1; then
        echo "  [OK]   $m ($desc)"
    else
        echo "  [없음] $m ($desc) - 이 커널은 해당 장치를 SocketCAN으로 지원하지 않습니다"
    fi
done
if ! modinfo kvaser_usb > /dev/null 2>&1 && [ -f /etc/rpi-issue ]; then
    echo "  [안내] Raspberry Pi OS 기본 커널에는 Kvaser USB 드라이버가 없습니다."
    echo "         라즈베리파이에서는 PCAN-USB 또는 candleLight 장치를 사용하세요."
fi

echo "=== 4/6 한글 입력기 설치 (ibus-hangul) ==="
# 프로그램(Qt)에 ibus 연동 모듈이 포함되어 있으므로, 배포판/데스크톱에 관계없이 ibus 를 사용합니다.
# gsettings 목록 값에 항목을 추가합니다. (이미 있으면 그대로 둠)
#   사용: gsettings_append <schema> <key> <추가 항목> <목록이 비어 있을 때 앞에 둘 기본 항목>
gsettings_append() {
    local cur new
    cur="$(gsettings get "$1" "$2" 2>/dev/null)" || return 1
    case "$cur" in *"$3"*) return 0 ;; esac
    case "$cur" in
        *"[]") new="[$4, $3]" ;;
        *)     new="${cur%]}, $3]" ;;
    esac
    gsettings set "$1" "$2" "$new"
}
setup_korean_input() {
    $SUDO apt-get install -y ibus ibus-hangul im-config || return 1
    if [ -z "$SUDO" ]; then
        echo "  [안내] root 로 실행 중이라 사용자별 입력기 설정은 건너뜁니다."
        echo "         일반 사용자로 로그인한 뒤 'im-config -n ibus' 를 실행하고 재로그인하세요."
        return 0
    fi
    # 로그인 시 ibus 를 기본 입력기로 사용 (~/.xinputrc, GTK/Qt/X11 공통)
    im-config -n ibus || return 1
    echo "  [OK]   기본 입력기: ibus"
    if ! command -v gsettings > /dev/null 2>&1 || [ -z "${DBUS_SESSION_BUS_ADDRESS:-}" ]; then
        echo "  [안내] 데스크톱 세션이 아니어서(SSH 등) 한글 엔진 등록은 건너뜁니다."
        echo "         재로그인 후 'ibus-setup' → 입력기 → 추가 → 한국어 → Hangul 을 추가하세요."
        return 0
    fi
    if echo "${XDG_CURRENT_DESKTOP:-}" | grep -qi gnome; then
        # GNOME(Ubuntu 기본 데스크톱): 입력 소스에 '한국어(Hangul)' 추가, 오른쪽 Alt 를 한/영 키로 사용
        gsettings_append org.gnome.desktop.input-sources sources "('ibus', 'hangul')" "('xkb', 'us')" &&
            echo "  [OK]   GNOME 입력 소스에 한국어(Hangul) 추가"
        gsettings_append org.gnome.desktop.input-sources xkb-options "'korean:ralt_hangul'" "'korean:rctrl_hanja'" &&
            echo "  [OK]   오른쪽 Alt = 한/영 키"
    else
        # 그 외 데스크톱(Raspberry Pi OS, XFCE, KDE 등): ibus 엔진 목록에 Hangul 추가
        gsettings_append org.freedesktop.ibus.general preload-engines "'hangul'" "'xkb:us::eng'" &&
        gsettings_append org.freedesktop.ibus.general engines-order "'hangul'" "'xkb:us::eng'" &&
            echo "  [OK]   ibus 엔진에 Hangul 추가"
    fi
    return 0
}
if [ "$KOREAN_INPUT" = "1" ]; then
    if setup_korean_input; then
        echo "  [안내] 재로그인(또는 재부팅) 후 한글 입력이 가능합니다."
        echo "         한/영 전환: 한/영 키 또는 Shift+Space"
    else
        echo "[경고] 한글 입력기 설정 중 오류가 있었습니다. 프로그램 실행에는 영향이 없습니다."
    fi
else
    echo "  (--no-korean: 건너뜀)"
fi

echo "=== 5/6 실행 권한 설정 ==="
chmod +x run.sh "./$EXE"

echo "=== 6/6 사용자 권한 확인 ==="
if [ -n "$SUDO" ] && ! id -nG | grep -qwE 'sudo|wheel|admin'; then
    echo "[주의] 현재 사용자($(id -un))가 sudo 그룹이 아닙니다."
    echo "       CAN 장치 속도 설정(ip link set) 시 프로그램이 sudo 비밀번호를 묻습니다."
    echo "       관리자에게 요청: sudo usermod -aG sudo $(id -un)  (적용하려면 재로그인)"
else
    echo "  [OK]   sudo 사용 가능"
fi

echo "========================================================================"
echo "설치가 완료되었습니다. 아래 명령으로 실행하세요(일반 사용자로, sudo 없이):"
echo "  ./run.sh"
echo ""
echo "CAN 장치 확인:  ip link show        (can0, can1 ... 이 보이면 정상)"
if [ "$KOREAN_INPUT" = "1" ]; then
echo "한글 입력:      재로그인 후 한/영 키 또는 Shift+Space 로 전환"
fi
echo "자세한 사용법:  README_Linux_Guide.md"
echo "========================================================================"
'''

# 영어판 설치 스크립트. 한글 입력기는 --korean 옵션을 줄 때만 설치합니다.
_RUNTIME_INSTALLER_TEMPLATE_EN = r'''#!/bin/bash
# __EXE__ runtime setup script (run once per PC)
# Installs only the system components needed to run the executable in this package (no source code or Python required).
#   Usage: ./install_runtime_en.sh           (runtime only)
#          ./install_runtime_en.sh --korean  (also install and configure the Korean input method)
set -e
cd "$(dirname "$(readlink -f "$0")")"

KOREAN_INPUT=0
for arg in "$@"; do
    case "$arg" in
        --korean) KOREAN_INPUT=1 ;;
        -h|--help)
            echo "Usage: $0 [--korean]"
            echo "  --korean   also install and configure the Korean input method (ibus-hangul)"
            exit 0 ;;
        *) echo "[ERROR] Unknown option: $arg  (help: $0 --help)"; exit 1 ;;
    esac
done

echo "=== 1/6 Checking this PC and selecting the executable ==="
__SELECT__
if ! command -v apt-get > /dev/null 2>&1; then
    echo "[ERROR] apt-get not found. Please run this on an Ubuntu/Debian-based Linux."
    exit 1
fi
if [ "$(id -u)" -eq 0 ]; then
    SUDO=""
    echo "[INFO] Running as root. Run the program itself as a normal user."
elif command -v sudo > /dev/null 2>&1; then
    SUDO="sudo"
else
    echo "[ERROR] sudo not found. As root, run 'apt-get install sudo' and add your user to the sudo group."
    exit 1
fi
echo "CPU: $HOST_ARCH / OS: $(. /etc/os-release && echo "$PRETTY_NAME") / glibc: ${HOST_GLIBC:-?}"
echo "Executable: bin/$HOST_ARCH/$EXE -> ./$EXE"

echo "=== 2/6 Installing system packages (sudo password may be required) ==="
$SUDO apt-get update
$SUDO apt-get install -y __PACKAGES__
fc-cache -f > /dev/null 2>&1 || true

echo "=== 3/6 Checking CAN kernel drivers ==="
# All CAN devices are used via SocketCAN (can0, ...). Vendor drivers are NOT installed.
# On Ubuntu, the USB-CAN / vcan kernel modules are in the linux-modules-extra package.
MISSING=0
for m in vcan peak_usb kvaser_usb gs_usb; do
    modinfo "$m" > /dev/null 2>&1 || MISSING=1
done
if [ "$MISSING" = "1" ]; then
    EXTRA_PKG="linux-modules-extra-$(uname -r)"
    if apt-cache show "$EXTRA_PKG" > /dev/null 2>&1; then
        echo "Installing CAN driver package: $EXTRA_PKG"
        $SUDO apt-get install -y "$EXTRA_PKG" || echo "[WARNING] Failed to install $EXTRA_PKG. Real CAN devices may not be detected."
    fi
fi
for item in "can:SocketCAN core" "can_raw:SocketCAN core" "vcan:Virtual CAN" \
            "peak_usb:PCAN-USB" "kvaser_usb:Kvaser USB" "gs_usb:candleLight"; do
    m="${item%%:*}"; desc="${item#*:}"
    if modinfo "$m" > /dev/null 2>&1; then
        echo "  [OK]      $m ($desc)"
    else
        echo "  [MISSING] $m ($desc) - this kernel does not support the device via SocketCAN"
    fi
done
if ! modinfo kvaser_usb > /dev/null 2>&1 && [ -f /etc/rpi-issue ]; then
    echo "  [INFO] The default Raspberry Pi OS kernel does not include the Kvaser USB driver."
    echo "         On a Raspberry Pi, use a PCAN-USB or candleLight device."
fi

echo "=== 4/6 Korean input method (ibus-hangul) ==="
# The program (Qt) ships with the ibus input-context plugin, so ibus is used on any distro/desktop.
# Appends an item to a gsettings list value (left unchanged if already present).
#   Usage: gsettings_append <schema> <key> <item> <default item placed first when the list is empty>
gsettings_append() {
    local cur new
    cur="$(gsettings get "$1" "$2" 2>/dev/null)" || return 1
    case "$cur" in *"$3"*) return 0 ;; esac
    case "$cur" in
        *"[]") new="[$4, $3]" ;;
        *)     new="${cur%]}, $3]" ;;
    esac
    gsettings set "$1" "$2" "$new"
}
setup_korean_input() {
    $SUDO apt-get install -y ibus ibus-hangul im-config || return 1
    if [ -z "$SUDO" ]; then
        echo "  [INFO] Running as root, so per-user input method settings are skipped."
        echo "         Log in as a normal user, run 'im-config -n ibus', then log out and back in."
        return 0
    fi
    # Use ibus as the default input method at login (~/.xinputrc; applies to GTK/Qt/X11)
    im-config -n ibus || return 1
    echo "  [OK]   Default input method: ibus"
    if ! command -v gsettings > /dev/null 2>&1 || [ -z "${DBUS_SESSION_BUS_ADDRESS:-}" ]; then
        echo "  [INFO] Not in a desktop session (e.g. SSH), so the Hangul engine was not registered."
        echo "         After re-login, run 'ibus-setup' -> Input Method -> Add -> Korean -> Hangul."
        return 0
    fi
    if echo "${XDG_CURRENT_DESKTOP:-}" | grep -qi gnome; then
        # GNOME (Ubuntu default desktop): add Korean (Hangul) input source, use Right Alt as the Han/Eng key
        gsettings_append org.gnome.desktop.input-sources sources "('ibus', 'hangul')" "('xkb', 'us')" &&
            echo "  [OK]   Added Korean (Hangul) to GNOME input sources"
        gsettings_append org.gnome.desktop.input-sources xkb-options "'korean:ralt_hangul'" "'korean:rctrl_hanja'" &&
            echo "  [OK]   Right Alt = Han/Eng key"
    else
        # Other desktops (Raspberry Pi OS, XFCE, KDE, ...): add Hangul to the ibus engine list
        gsettings_append org.freedesktop.ibus.general preload-engines "'hangul'" "'xkb:us::eng'" &&
        gsettings_append org.freedesktop.ibus.general engines-order "'hangul'" "'xkb:us::eng'" &&
            echo "  [OK]   Added Hangul to ibus engines"
    fi
    return 0
}
if [ "$KOREAN_INPUT" = "1" ]; then
    if setup_korean_input; then
        echo "  [INFO] Korean input is available after logging out and back in (or rebooting)."
        echo "         Toggle Korean/English: Han/Eng key or Shift+Space"
    else
        echo "[WARNING] An error occurred while setting up Korean input. This does not affect running the program."
    fi
else
    echo "  (skipped - use --korean to enable)"
fi

echo "=== 5/6 Setting execute permissions ==="
chmod +x run.sh "./$EXE"

echo "=== 6/6 Checking user permissions ==="
if [ -n "$SUDO" ] && ! id -nG | grep -qwE 'sudo|wheel|admin'; then
    echo "[NOTE] The current user ($(id -un)) is not in the sudo group."
    echo "       The program will ask for a sudo password when setting the CAN bitrate (ip link set)."
    echo "       Ask your administrator to run: sudo usermod -aG sudo $(id -un)  (log in again to apply)"
else
    echo "  [OK]   sudo available"
fi

echo "========================================================================"
echo "Setup complete. Start the program with the command below (as a normal user, without sudo):"
echo "  ./run.sh"
echo ""
echo "Check CAN devices:  ip link show        (OK if can0, can1 ... are listed)"
if [ "$KOREAN_INPUT" = "1" ]; then
echo "Korean input:       after re-login, toggle with the Han/Eng key or Shift+Space"
fi
echo "Detailed guide:     README_Linux_Guide_en.md"
echo "========================================================================"
'''


def _apt_package_lines(per_line=5):
    """apt-get install 인자 목록을 줄바꿈(백슬래시) 포함 문자열로 만듭니다."""
    return " \\\n    ".join(
        " ".join(LINUX_RUNTIME_APT_PACKAGES[i:i + per_line])
        for i in range(0, len(LINUX_RUNTIME_APT_PACKAGES), per_line)
    )


def _linux_build_info():
    """빌드한 PC 정보(CPU, glibc, OS) - 받는 PC에서 실행 가능 여부 판단에 사용합니다."""
    try:
        build_os = platform.freedesktop_os_release().get('PRETTY_NAME', 'Linux')
    except OSError:
        build_os = 'Linux'
    return {
        'BUILD_ARCH': platform.machine(),
        'BUILD_GLIBC': platform.libc_ver()[1] or '',
        'BUILD_OS': build_os,
        'BUILD_DATE': time.strftime('%Y-%m-%d'),
        'BUILD_VERSION': APP_VERSION,
    }


def _write_script(path, content):
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(content)
    os.chmod(path, 0o755)


def _write_linux_package_files(dist_path, exe_dist_path, exe_name):
    """통합 리눅스 패키지 파일을 생성합니다.

    dist_path/
      run.sh, install_runtime(_en).sh  CPU 자동 판별 (공통)
      bin/<CPU>/<exe>, BUILD_INFO.txt  이번 빌드의 CPU용 실행 파일
    다른 CPU에서 빌드한 bin/<CPU>/ 폴더를 같은 bin/ 아래로 복사하면 하나의 패키지로 합쳐집니다.
    """
    info = _linux_build_info()
    with open(os.path.join(exe_dist_path, 'BUILD_INFO.txt'), 'w', encoding='utf-8', newline='\n') as f:
        for key, value in info.items():
            f.write(f'{key}={shlex.quote(str(value))}\n')

    select = _SELECT_BINARY_SNIPPET.replace('__EXE__', exe_name)
    _write_script(os.path.join(dist_path, 'run.sh'),
                  _RUN_SCRIPT_TEMPLATE.replace('__SELECT__', select).replace('__EXE__', exe_name))
    select_en = _SELECT_BINARY_SNIPPET_EN.replace('__EXE__', exe_name)
    for script_name, template, snippet in (
            ('install_runtime.sh', _RUNTIME_INSTALLER_TEMPLATE, select),
            ('install_runtime_en.sh', _RUNTIME_INSTALLER_TEMPLATE_EN, select_en)):
        _write_script(os.path.join(dist_path, script_name),
                      template
                      .replace('__SELECT__', snippet)
                      .replace('__EXE__', exe_name)
                      .replace('__PACKAGES__', _apt_package_lines()))

    # 이전 구조(실행 파일이 최상위에 바로 있던 빌드)의 남은 파일 정리
    stale = os.path.join(dist_path, exe_name)
    if os.path.isfile(stale):
        os.remove(stale)

    archs = sorted(d for d in os.listdir(os.path.join(dist_path, 'bin'))
                   if os.path.isfile(os.path.join(dist_path, 'bin', d, exe_name)))
    print(f"[안내] run.sh / install_runtime.sh / install_runtime_en.sh 생성. 패키지에 포함된 CPU: {', '.join(archs)}")
    print(f"       (다른 CPU용을 합치려면 그 PC에서 빌드한 bin/<CPU> 폴더를 {os.path.join(dist_path, 'bin')} 아래에 복사)")
    return info

def _collect_hidden_imports():
    """
    python-can은 CAN 인터페이스 모듈을 런타임에 동적으로 import하므로, PyInstaller가
    이를 자동으로 찾지 못하는 경우가 많습니다. 이 함수는 현재 환경에 설치된 python-can
    인터페이스들을 최대한 수집하여 빌드에 포함시켜 'Module not found' 오류를 방지합니다.
    """
    # 1. 필수 및 기본 모듈 추가
    hidden_imports = {
        "PyQt5.QtSvg",
        "cantools",
        "can.interfaces",
    }

    # 2. PyInstaller의 유틸리티를 사용하여 'can.interfaces' 및 그 하위의 모든 모듈을 수집
    #    Linux에서 'socketcan' 관련 모듈 누락 오류를 해결하기 위해 'can.interfaces.socketcan'도 명시적으로 수집합니다.
    if collect_submodules:
        packages_to_scan = ['can.interfaces']
        # socketcan은 Linux 전용이므로, Windows/Mac에서 불필요한 오류 메시지가 표시되는 것을 막기 위해 Linux에서만 스캔합니다.
        if platform.system() == 'Linux':
            packages_to_scan.append('can.interfaces.socketcan')

        for package_name in packages_to_scan:
            try:
                hidden_imports.update(collect_submodules(package_name))
                print(f"[안내] '{package_name}' 하위 모듈 자동 수집 완료.")
            except Exception as exc:
                print(f"[경고] '{package_name}' 하위 모듈 자동 수집 실패: {exc}")
    
    # 3. 자동 수집이 실패하거나, 구버전 PyInstaller를 위해 주요 인터페이스를 수동으로 확인하여 추가
    potential_imports = [
        "can.interfaces.pcan",
        "can.interfaces.virtual",
        "can.interfaces.vector",
        "can.interfaces.kvaser",
        "can.interfaces.ixxat",
        "can.interfaces.slcan",
        "can.interfaces.gs_usb",
    ]
    if platform.system() == 'Linux':
        potential_imports.append("can.interfaces.socketcan")
    else:
        # candleLight / gs_usb (src/can_devices/device_gsusb.py) imports these lazily.
        # libusb_package ships its own PyInstaller hook that bundles libusb-1.0.dll.
        potential_imports += ["gs_usb", "gs_usb.gs_usb", "usb.backend.libusb1", "libusb_package"]

    for module_name in potential_imports:
        # Windows에서 socketcan을 찾지 못해도 오류가 아니므로 find_spec으로 존재 여부만 확인
        if importlib.util.find_spec(module_name):
            hidden_imports.add(module_name)

    return sorted(list(hidden_imports))

def robust_rmtree(path, max_retries=10, delay=1.0):
    """
    shutil.rmtree의 Windows 권한 오류(PermissionError)를 회피하기 위해,
    재시도 후 최종적으로 폴더 이름 변경을 시도하는 강화된 버전입니다.
    """
    if not os.path.exists(path):
        return

    def _onerror(func, target, exc_info):
        exc_type, exc_value, _ = exc_info
        if not os.access(target, os.W_OK):
            os.chmod(target, stat.S_IWRITE)
            func(target)
        elif isinstance(exc_value, PermissionError):
            os.chmod(target, stat.S_IWRITE)
            func(target)
        else:
            raise exc_value

    for i in range(max_retries):
        try:
            shutil.rmtree(path, onerror=_onerror)
            return # 성공 시 즉시 종료
        except OSError as e:
            if i < max_retries - 1:
                print(f"[경고] '{os.path.basename(path)}' 폴더 삭제 실패 (오류: {e}). {delay}초 후 재시도합니다... ({i+1}/{max_retries-1})")
                time.sleep(delay)
            else:
                # 모든 재시도 실패 시, 마지막 수단으로 폴더 이름 변경 시도
                try:
                    new_name = f"{path}_todelete_{int(time.time())}"
                    os.rename(path, new_name)
                    print(f"[경고] 폴더를 삭제할 수 없어 '{os.path.basename(new_name)}'(으)로 이름을 변경했습니다. 빌드는 계속됩니다.")
                    return # 이름 변경 성공 시, 빌드 계속 진행
                except OSError as rename_error:
                    print(f"[오류] '{os.path.basename(path)}' 폴더를 삭제하거나 이름을 변경하지 못했습니다. (Rename Error: {rename_error})")
                    raise e # 이름 변경도 실패하면, 원래의 오류를 발생시켜 빌드 중단

def build_executable():
    current_dir = os.path.dirname(os.path.abspath(__file__))
    
    # 타겟 스크립트를 분리된 구조에 맞게 main.py로 변경
    target_script = os.path.join(current_dir, 'main.py')
    icon_path = os.path.join(current_dir, 'icon', 'viewer.ico')
    
    # 실행 플래그 파일(.pk)에서 뷰어 모드 여부 파악 (옛 PCAN_Viewer_JJ.pk도 읽음)
    is_viewer_mode = False
    pk_path = resolve_pk_path(current_dir)
    if os.path.exists(pk_path):
        try:
            with open(pk_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if isinstance(data, dict) and data.get("viewer_mode_only"):
                    is_viewer_mode = True
        except Exception:
            pass
                
    app_prefix = VIEWER_FILE_PREFIX if is_viewer_mode else APP_FILE_PREFIX
    
    icon_dir = os.path.join(current_dir, 'icon')
    current_os = platform.system()
    if current_os == 'Windows':
        os_suffix = "_win"
        pcan_lib = os.path.join(current_dir, 'src', 'PCANBasic.dll')
        binary_option = f'--add-binary={pcan_lib};.' 
        data_option = f'--add-data={icon_dir};icon'
    elif current_os == 'Linux':
        os_suffix = "_linux"
        pcan_lib = os.path.join(current_dir, 'src', 'libpcanbasic.so')
        binary_option = f'--add-binary={pcan_lib}:.' 
        data_option = f'--add-data={icon_dir}:icon'
    else:
        os_suffix = "_mac"
        pcan_lib = os.path.join(current_dir, 'src', 'libPCBUSB.dylib')
        binary_option = f'--add-binary={pcan_lib}:.'
        data_option = f'--add-data={icon_dir}:icon'

    print("=== 실행 파일 빌드를 시작합니다 ===")
    
    dist_path = os.path.join(current_dir, 'dist', f'{app_prefix}_{APP_VERSION}{os_suffix}')
    work_path = os.path.join(current_dir, 'build', f'{app_prefix}_{APP_VERSION}{os_suffix}')
    # Linux: CPU별 실행 파일을 bin/<CPU>/ 에 두는 통합 패키지 (x86_64 + aarch64 를 한 폴더로 합칠 수 있음)
    if current_os == 'Linux':
        exe_dist_path = os.path.join(dist_path, 'bin', platform.machine())
    else:
        exe_dist_path = dist_path
    
    # PyInstaller의 --clean 옵션은 Windows에서 파일 잠금으로 인한 권한 오류를 자주 일으킵니다.
    # 이를 방지하기 위해, 빌드 시작 전에 수동으로 build 및 dist 폴더를 삭제합니다.
    print("[안내] 이전 빌드/배포 폴더를 정리합니다...")
    try:
        robust_rmtree(work_path)
        # Linux 는 이번 CPU 의 bin/<CPU>/ 만 지워, 다른 CPU 용으로 합쳐 둔 실행 파일을 보존합니다.
        robust_rmtree(exe_dist_path if current_os == 'Linux' else dist_path)
    except Exception as e:
        print(f"[오류] 폴더 정리 중 심각한 오류 발생: {e}")
        if platform.system() == 'Linux' and isinstance(e, PermissionError):
            error_msg = "\n[Linux 사용자 안내]\n이전에 'sudo'를 사용하여 빌드를 실행한 경우, 생성된 폴더/파일의 소유자가 root로 되어있을 수 있습니다."
            error_msg += "\n이 경우, 일반 사용자 권한으로는 삭제할 수 없습니다."
            error_msg += "\n터미널에서 아래 명령어를 실행하여 폴더를 직접 삭제한 후 다시 시도해 보세요:"
            error_msg += f"\n<code>sudo rm -rf \"{work_path}\" \"{dist_path}\"</code>"
        else:
            error_msg = "다른 프로그램(예: 파일 탐색기, 이전 실행 파일, 바이러스 백신)이 해당 폴더/파일을 사용하고 있는지 확인해주세요."
            if platform.system() == 'Windows':
                error_msg += "\n- VS Code 등 IDE의 터미널에서 실행 중이라면, IDE를 완전히 종료하고 외부 터미널(PowerShell, CMD)에서 다시 시도해보세요."
                error_msg += "\n- 또는, 현재 터미널을 '관리자 권한으로 실행'한 후 다시 시도해보세요."
        print(error_msg)
        sys.exit(1)

    build_args = [
        target_script,
        f'--name={app_prefix}_{APP_VERSION}{os_suffix}',
        '--onefile',       
        '--noconsole',
        f'--paths={current_dir}', # 패키지 인식을 위해 루트 디렉토리 추가
        f'--distpath={exe_dist_path}',
        f'--workpath={work_path}',
        f'--specpath={work_path}',
        # '--clean',  # Windows에서 PermissionError를 유발할 수 있어 수동 처리로 대체함
    ]
    
    # python-can 동적 인터페이스 모듈 포함
    for module_name in _collect_hidden_imports():
        build_args.append(f"--hidden-import={module_name}")

    # 아이콘 폴더/파일이 있을 때만 포함 (빌드 오류 방지)
    if os.path.isdir(icon_dir):
        build_args.append(data_option)
    else:
        print(f"[안내] icon 폴더가 없어 add-data에서 제외합니다: {icon_dir}")

    if os.path.isfile(icon_path):
        build_args.append(f'--icon={icon_path}')
    else:
        print(f"[안내] 아이콘 파일이 없어 icon 옵션에서 제외합니다: {icon_path}")

    # OS별 라이브러리(dll, so) 필수 포함 여부 분기 처리
    if current_os == 'Windows':
        if not os.path.exists(pcan_lib):
            print(f"\n[에러] 윈도우 빌드에는 {os.path.basename(pcan_lib)} 파일이 필수입니다!")
            print("빌드를 강제 중단합니다. src 폴더에 DLL 파일을 추가한 후 다시 시도해주세요.\n")
            sys.exit(1)
        build_args.append(binary_option)
    elif current_os == 'Linux':
        # Linux 는 모든 CAN 장치를 커널 SocketCAN(can0~)으로만 사용합니다.
        # 제조사 라이브러리(libpcanbasic.so / libcanlib.so)는 포함하지 않습니다.
        print("\n[안내] Linux 빌드: SocketCAN 전용 (PCAN/Kvaser 제조사 라이브러리 미포함)\n")
    else:
        if os.path.exists(pcan_lib):
            build_args.append(binary_option)
        else:
            print(f"\n[안내] {os.path.basename(pcan_lib)} 라이브러리가 없어 빌드에서 제외합니다.\n")
            
    PyInstaller.__main__.run(build_args)
    
    # 배포 모드(viewer_mode_only/user_panel_only) 전환용 설정 파일을 exe 옆에 배치
    # (세션 매니저가 있으면 앱이 자동 생성하지 않으므로 빌드 시 직접 복사/생성한다)
    dist_pk_path = os.path.join(dist_path, PK_FILE_NAME)
    if os.path.exists(pk_path):
        shutil.copy2(pk_path, dist_pk_path)
    else:
        with open(dist_pk_path, 'w', encoding='utf-8') as f:
            json.dump({"viewer_mode_only": False, "user_panel_only": False}, f, indent=4)
    print(f"[안내] {PK_FILE_NAME} 파일이 배포 폴더에 생성되었습니다.")

    # 배포 파일과 함께 최신 사용법 및 변경 내역 제공
    for document in ('MANUAL.md', 'USER_PANEL_SEQUENCE.md', 'USER_PANEL_RELEASE_NOTES.md',
                     'MANUAL_en.md', 'USER_PANEL_SEQUENCE_en.md', 'USER_PANEL_RELEASE_NOTES_en.md'):
        document_path = os.path.join(current_dir, document)
        if os.path.exists(document_path):
            shutil.copy2(document_path, os.path.join(dist_path, document))
            print(f"[안내] {document} 파일이 배포 폴더에 복사되었습니다.")

    # Windows 장치 드라이버 설치 도구(Kvaser 드라이버 설치 파일, candleLight WinUSB 전환)를
    # 배포 폴더의 Driver 폴더로 복사합니다. Linux는 커널 드라이버를 사용하므로 복사하지 않습니다.
    driver_src = os.path.join(current_dir, 'Driver')
    if current_os == 'Windows' and os.path.isdir(driver_src):
        driver_dst = os.path.join(dist_path, 'Driver')
        shutil.copytree(driver_src, driver_dst, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns('__pycache__', '*.tmp', 'Thumbs.db'))
        for root, _, files in os.walk(driver_dst):
            for name in files:
                print(f"[안내] 드라이버 도구 복사: {os.path.relpath(os.path.join(root, name), dist_path)}")


    # OS별 배포용 README 파일(실행 가이드) 자동 생성
    if current_os == 'Linux':
        exe_name = f'{app_prefix}_{APP_VERSION}{os_suffix}'
        _write_linux_package_files(dist_path, exe_dist_path, exe_name)
        apt_packages = _apt_package_lines()
        pkg_name = os.path.basename(dist_path)
        # 파일명은 영문으로 만듭니다(한글을 지원하지 않는 리눅스에서 파일명이 깨지지 않도록).
        stale_readme = os.path.join(dist_path, 'README_Linux_실행_가이드.md')
        if os.path.isfile(stale_readme):
            os.remove(stale_readme)
        readme_ko = f"""# {app_prefix.replace('_', ' ')} {APP_VERSION} - 리눅스 실행 가이드

이 패키지는 **파이썬·소스 코드 없이** 바로 실행되는 리눅스용 프로그램입니다.
**데스크톱 PC(x86_64)와 라즈베리파이(aarch64, 64비트)용 실행 파일이 함께 들어 있고**, PC의 CPU에 맞는 것을 자동으로 골라 실행합니다.

```bash
./install_runtime.sh   # 처음 한 번 (실행 환경 설치)
./run.sh               # 실행
```

## 📁 0. 패키지 구성

| 파일 | 설명 |
|---|---|
| `run.sh` | **실행** — CPU 자동 판별 후 실행 |
| `install_runtime.sh` | **실행 환경 설치** (PC당 최초 1회) |
| `bin/x86_64/`, `bin/aarch64/` | CPU별 실행 파일과 빌드 정보(`BUILD_INFO.txt`) |
| `{exe_name}` | 이 PC용으로 선택된 실행 파일 (설치/실행 시 자동 생성) |
| `{PK_FILE_NAME}` | 실행 모드 설정 파일 (지우지 마세요) |
| `README_Linux_Guide.md` | 이 문서 |
| `MANUAL.md`, `USER_PANEL_*.md` | 상세 사용법 / 변경 내역 |
| `*_en.md`, `install_runtime_en.sh` | 영어 버전 |

세션(`session.pjjsettings`)과 로그는 이 폴더(최상위)에 저장됩니다.

## ✅ 1. 지원 환경 확인

```bash
uname -m                  # x86_64(데스크톱) 또는 aarch64(라즈베리파이 64비트)
ls bin                    # 패키지에 들어 있는 CPU 목록
cat bin/*/BUILD_INFO.txt  # CPU별 빌드 OS / 필요한 glibc 버전
getconf GNU_LIBC_VERSION  # 이 PC의 glibc 버전 (BUILD_GLIBC 이상이어야 실행됨)
```

- Ubuntu / Debian / Raspberry Pi OS 등 **apt 를 쓰는 배포판**을 지원합니다.
- 32비트 라즈베리파이 OS(`armv7l`)는 지원하지 않습니다.
- CPU나 OS 버전이 맞지 않으면 `run.sh` / `install_runtime.sh` 가 이유를 알려주고 멈춥니다.

## 📦 2. 압축 풀기

쓰기 권한이 있는 위치(예: 홈 폴더)에 풉니다.

```bash
mkdir -p ~/CAN_Studio
tar xzf ~/Downloads/{pkg_name}.tar.gz -C ~/CAN_Studio
cd ~/CAN_Studio/{pkg_name}
ls
```

> `/opt`, `/usr` 처럼 일반 사용자가 쓸 수 없는 곳에 두면 세션 자동 저장이 실패합니다.
> 압축 파일 대신 USB 등으로 폴더를 복사했다면 `chmod +x run.sh install_runtime.sh` 를 먼저 실행하세요.

## 🛠️ 3. 실행 환경 설치 (PC당 최초 1회)

```bash
./install_runtime.sh               # 한글 입력기까지 설치 (기본)
./install_runtime.sh --no-korean   # 한글 입력기 설치/설정 생략
```

스크립트가 하는 일:
1. CPU / OS(glibc) 확인 후 이 PC용 실행 파일 선택 (`bin/<CPU>/` → 최상위 폴더로 복사)
2. 시스템 패키지 설치 — 화면 표시 라이브러리(X11/Wayland/OpenGL), 한글 글꼴, `ip`·`candump` 등 CAN 도구
3. CAN 커널 드라이버 확인 — Ubuntu는 USB-CAN 드라이버가 든 `linux-modules-extra-<커널버전>` 자동 설치
4. 한글 입력기 설치·설정 (`ibus`, `ibus-hangul`) — `--no-korean` 이면 생략
5. 실행 권한 설정
6. 현재 사용자의 sudo 사용 가능 여부 확인

<details>
<summary>스크립트 없이 직접 설치하려면</summary>

```bash
sudo apt-get update
sudo apt-get install -y {apt_packages}
# Ubuntu 에서 실제 USB-CAN 장치를 쓸 때
sudo apt-get install -y linux-modules-extra-$(uname -r)
```
</details>

### ⌨️ 한글 입력

`install_runtime.sh` 실행 후 **로그아웃 → 다시 로그인(또는 재부팅)** 하면 프로그램에서 한글을 입력할 수 있습니다.

- 한/영 전환: **한/영 키** 또는 **Shift+Space** (GNOME 에서는 오른쪽 Alt 도 한/영 키로 동작)
- 프로그램에 Qt ibus 연동 모듈이 포함되어 있어 Ubuntu / Debian / Raspberry Pi OS 모두 같은 `ibus` 설정을 사용합니다.

직접 설정하려면:

```bash
sudo apt-get install -y ibus ibus-hangul im-config
im-config -n ibus        # 일반 사용자로 실행(sudo 없이) 후 재로그인
ibus-setup               # 입력기 → 추가 → 한국어 → Hangul
# GNOME(Ubuntu): 설정 → 키보드 → 입력 소스 → + → 한국어(Hangul)
```

## 🚀 4. 실행

```bash
./run.sh
```

- **일반 사용자로 실행하세요(sudo 없이).** CAN 속도 설정처럼 권한이 필요한 작업은 프로그램 창에서 sudo 비밀번호를 묻습니다.
- 한 번 실행한 뒤에는 `./{exe_name}` 로 직접 실행해도 됩니다.
- 터미널을 닫아도 계속 실행하려면:
  ```bash
  nohup ./run.sh > /dev/null 2>&1 &
  ```

## 🔌 5. 실제 CAN 장치 사용

리눅스에서는 **모든 CAN 장치를 SocketCAN으로만 사용합니다.** 프로그램의 CAN 방식에도 SocketCAN만 표시됩니다.
USB-CAN 장치는 리눅스 커널 기본 드라이버로 `can0`, `can1` … 으로 인식됩니다.
**PCAN / Kvaser 등 제조사 드라이버를 설치하지 마세요.** (예: PEAK 홈페이지의 chardev 드라이버를 설치하면 `can0`이 나타나지 않습니다.)

| 장치 | 커널 드라이버 | Ubuntu / Debian PC | Raspberry Pi OS |
|---|---|---|---|
| PCAN-USB (PEAK) | `peak_usb` | ✅ | ✅ |
| candleLight / CANable (gs_usb) | `gs_usb` | ✅ | ✅ |
| Kvaser USB | `kvaser_usb` | ✅ (Ubuntu 는 `linux-modules-extra` 필요 — `install_runtime.sh` 가 자동 설치) | ❌ 기본 커널에 없음 |

```bash
# 1) 장치를 USB에 꽂은 뒤 인식 확인
ip link show                     # can0 이 보이면 정상
dmesg | grep -i can | tail       # 드라이버 로드 메시지 확인

# 2) 프로그램에서 사용: Connection Control → CAN 방식 SocketCAN → 채널 can0 → 속도 선택 → Open
```

프로그램 없이 터미널에서 직접 점검하려면:

```bash
# Classic CAN 500 kbit/s 설정 후 켜기
sudo ip link set can0 down
sudo ip link set can0 type can bitrate 500000
sudo ip link set can0 up

# CAN FD (중재 500 kbit/s / 데이터 2 Mbit/s)
sudo ip link set can0 down
sudo ip link set can0 type can bitrate 500000 dbitrate 2000000 fd on
sudo ip link set can0 up

# 상태 확인 / 수신 / 송신
ip -details -statistics link show can0
candump can0                       # 수신 확인 (Ctrl+C 로 종료)
cansend can0 123#1122334455667788  # ID 0x123, 8바이트 송신
cansend can0 123##1112233          # CAN FD 프레임 송신 (## 뒤 첫 자리는 플래그)

# 끄기
sudo ip link set can0 down
```

## 🧪 6. 장치 없이 가상 CAN(vcan)으로 테스트

```bash
sudo modprobe vcan
sudo ip link add dev vcan0 type vcan
sudo ip link set vcan0 mtu 72    # CAN FD 까지 테스트할 때만 (up 전에 실행)
sudo ip link set up vcan0
```

프로그램에서 채널 `vcan0`을 선택해 Open 합니다. 다른 터미널에서 프레임을 보내 수신을 확인할 수 있습니다.

```bash
cansend vcan0 123#DEADBEEF       # 프로그램에 수신되는지 확인
candump vcan0                    # 프로그램이 보낸 프레임 확인
```

삭제: `sudo ip link delete vcan0` (재부팅하면 자동으로 사라집니다)

## 🩺 7. 문제 해결

| 증상 | 원인 / 해결 |
|---|---|
| `이 PC의 CPU(...)용 실행 파일이 패키지에 없습니다` | 패키지에 해당 CPU용이 없습니다. `ls bin` 확인 후 해당 CPU가 포함된 패키지를 받으세요. |
| `이 PC의 리눅스가 너무 오래되어 실행할 수 없습니다` | OS를 업데이트하거나, 더 오래된 OS에서 빌드한 패키지를 받으세요. |
| `Permission denied` | `chmod +x run.sh install_runtime.sh` |
| `qt.qpa.plugin: Could not load the Qt platform plugin` | `./install_runtime.sh` 를 다시 실행하세요. 그래도 안 되면 `QT_QPA_PLATFORM=xcb ./run.sh` |
| 한글이 네모(□)로 보임 | `sudo apt-get install -y fonts-nanum && fc-cache -f` |
| 한글 입력이 안 됨 | `./install_runtime.sh` 실행 후 재로그인했는지 확인. `pgrep ibus-daemon` 으로 ibus 실행 여부, `ibus-setup` 에 Hangul 추가 여부 확인. 필요하면 `QT_IM_MODULE=ibus ./run.sh` |
| `ip link show` 에 `can0` 이 없음 | 장치 재연결 → `dmesg` 끝부분에서 드라이버 메시지 확인. Ubuntu는 `sudo apt-get install linux-modules-extra-$(uname -r)` 후 재부팅. |
| CAN Open 시 권한 오류 | 실행 계정이 sudo 그룹인지 확인: `groups`. 아니면 관리자가 `sudo usermod -aG sudo <계정>` 후 재로그인. |
| 설정이 저장되지 않음 | 프로그램 폴더에 쓰기 권한이 있는지 확인: `ls -ld .` (홈 폴더 아래로 옮기세요). |
| 툴팁/창이 어색하게 표시됨 (Wayland) | `QT_QPA_PLATFORM=xcb ./run.sh` 로 X11 모드 실행 |

## 🔄 8. 다른 PC로 옮기기 / 업데이트 / 삭제

- **다른 PC로 옮기기**: 폴더째 복사 → 새 PC에서 `./install_runtime.sh` → `./run.sh`. CPU가 달라도 같은 패키지를 그대로 쓰면 됩니다.
- **업데이트**: 새 버전을 풀고 기존 폴더의 `session.pjjsettings`, `{PK_FILE_NAME}` 을 새 폴더로 복사하면 설정이 유지됩니다.
- **삭제**: 폴더를 지우면 됩니다. (`install_runtime.sh` 로 설치한 시스템 패키지는 다른 프로그램도 쓸 수 있어 그대로 두는 것을 권장합니다.)

## 📝 {APP_VERSION} 기능 및 사용 안내
- User Panel: Add TX Tool → sequence → 명령 시퀀스 설정에서 CMD/RCV/DEL을 등록합니다.
- DBC 없이 저장된 CMD 데이터와 RCV 비트 조건을 수정·실행할 수 있습니다.
- sequence Force는 RCV를 건너뛰고, Packet Control은 CMD/RCV를 제외해 START/STOP/DEL만 실행합니다. 전역 Force RUN 모드는 없습니다.
- TX counter에서 최신 RX DBC raw값 동기화와 업/다운/왕복 fallback을 선택할 수 있습니다.
- 실시간 그래프: Reset Zoom(최근 30초), Auto Y(표시 중인 구간), Fit All Data(보관 구간).
- 패널 닫기·모드 변경 시 패널 송신과 대기를 모두 중지합니다.
- 양수 주기의 등록 패킷은 BUS별 스레드에서 전송하며, deadline 누락은 시스템 로그에 표시합니다. 실제 주기 성능은 하드웨어/드라이버에서 확인해야 합니다.
- 패널 FD/BRS, 슬라이더 Home, 최소 격자와 창 확대 동작을 지원합니다.
- LOG 탭: Extended ID 필터, 필터 결과 재전송, Save로 원본 형식 추출 저장을 지원합니다.
- CAN 세션은 이전 저장 상태와 관계없이 Close로 시작합니다. sudo 비밀번호는 실행 중에만 사용하며 파일이나 로그에 저장하지 않습니다.
- 상세 사용법: MANUAL.md / USER_PANEL_SEQUENCE.md / USER_PANEL_RELEASE_NOTES.md.
- Consolas가 없거나 지원하지 않는 문자는 시스템 대체 글꼴로 표시됩니다.
"""
        readme_en = f"""# {app_prefix.replace('_', ' ')} {APP_VERSION} - Linux Run Guide

This package is a Linux program that runs directly **without Python or source code**.
**It contains executables for desktop PCs (x86_64) and Raspberry Pi (aarch64, 64-bit)**, and automatically selects the one that matches the PC's CPU.

```bash
./install_runtime_en.sh   # once (install the runtime environment)
./run.sh                  # run
```

## 📁 0. Package Contents

| File | Description |
|---|---|
| `run.sh` | **Run** — detects the CPU automatically and starts the program |
| `install_runtime_en.sh` | **Runtime setup** (once per PC). Korean version: `install_runtime.sh` |
| `bin/x86_64/`, `bin/aarch64/` | Executables and build info (`BUILD_INFO.txt`) per CPU |
| `{exe_name}` | Executable selected for this PC (created automatically on setup/run) |
| `{PK_FILE_NAME}` | Run mode settings file (do not delete) |
| `README_Linux_Guide_en.md` | This document (Korean version: `README_Linux_Guide.md`) |
| `MANUAL_en.md`, `USER_PANEL_*_en.md` | Detailed usage / change history (Korean versions: files without `_en`) |

The session (`session.pjjsettings`) and logs are saved in this (top-level) folder.

## ✅ 1. Check Supported Environment

```bash
uname -m                  # x86_64 (desktop) or aarch64 (Raspberry Pi 64-bit)
ls bin                    # list of CPUs included in the package
cat bin/*/BUILD_INFO.txt  # build OS / required glibc version per CPU
getconf GNU_LIBC_VERSION  # glibc version of this PC (must be BUILD_GLIBC or later)
```

- Distributions **that use apt**, such as Ubuntu / Debian / Raspberry Pi OS, are supported.
- 32-bit Raspberry Pi OS (`armv7l`) is not supported.
- If the CPU or OS version does not match, `run.sh` / `install_runtime_en.sh` tells you why and stops.

## 📦 2. Extract the Archive

Extract to a location where you have write permission (e.g. your home folder).

```bash
mkdir -p ~/CAN_Studio
tar xzf ~/Downloads/{pkg_name}.tar.gz -C ~/CAN_Studio
cd ~/CAN_Studio/{pkg_name}
ls
```

> If you put it where normal users cannot write, such as `/opt` or `/usr`, automatic session saving will fail.
> If you copied the folder via USB etc. instead of extracting the archive, run `chmod +x run.sh install_runtime_en.sh` first.

## 🛠️ 3. Runtime Setup (once per PC)

```bash
./install_runtime_en.sh            # runtime only
./install_runtime_en.sh --korean   # also set up Korean keyboard input
```

What the script does:
1. Checks CPU / OS (glibc) and selects the executable for this PC (copies `bin/<CPU>/` → top-level folder)
2. Installs system packages — display libraries (X11/Wayland/OpenGL), Korean fonts, CAN tools such as `ip` and `candump`
3. Checks CAN kernel drivers — on Ubuntu, automatically installs `linux-modules-extra-<kernel version>`, which contains the USB-CAN drivers
4. (Only with `--korean`) Installs and configures the Korean input method (`ibus`, `ibus-hangul`)
5. Sets execute permissions
6. Checks whether the current user can use sudo

<details>
<summary>To install manually without the script</summary>

```bash
sudo apt-get update
sudo apt-get install -y {apt_packages}
# When using a real USB-CAN device on Ubuntu
sudo apt-get install -y linux-modules-extra-$(uname -r)
```
</details>

### ⌨️ Korean input (optional)

Korean text is **displayed** without any extra setup (the `fonts-nanum` font is always installed). To **type** Korean in the program, run `./install_runtime_en.sh --korean`, then log out and back in (or reboot).

- Toggle Korean/English: **Han/Eng key** or **Shift+Space** (on GNOME, Right Alt also works as the Han/Eng key).
- The program includes the Qt ibus plugin, so the same `ibus` setup works on Ubuntu, Debian, and Raspberry Pi OS.

To set it up manually:

```bash
sudo apt-get install -y ibus ibus-hangul im-config
im-config -n ibus        # run as a normal user (not sudo), then log out and back in
ibus-setup               # Input Method → Add → Korean → Hangul
# GNOME (Ubuntu): Settings → Keyboard → Input Sources → + → Korean (Hangul)
```

## 🚀 4. Run

```bash
./run.sh
```

- **Run as a normal user (without sudo).** For tasks that need privileges, such as setting the CAN bitrate, the program asks for the sudo password in its window.
- After running once, you can also start `./{exe_name}` directly.
- To keep it running after closing the terminal:
  ```bash
  nohup ./run.sh > /dev/null 2>&1 &
  ```

## 🔌 5. Using Real CAN Devices

On Linux, **all CAN devices are used only through SocketCAN.** Only SocketCAN is shown as the CAN type in the program.
USB-CAN devices are recognized as `can0`, `can1` … by the default Linux kernel drivers.
**Do not install vendor drivers such as PCAN / Kvaser.** (For example, installing the chardev driver from the PEAK website prevents `can0` from appearing.)

| Device | Kernel driver | Ubuntu / Debian PC | Raspberry Pi OS |
|---|---|---|---|
| PCAN-USB (PEAK) | `peak_usb` | ✅ | ✅ |
| candleLight / CANable (gs_usb) | `gs_usb` | ✅ | ✅ |
| Kvaser USB | `kvaser_usb` | ✅ (Ubuntu needs `linux-modules-extra` — installed automatically by `install_runtime_en.sh`) | ❌ not in the default kernel |

```bash
# 1) Plug the device into USB and check detection
ip link show                     # OK if can0 is listed
dmesg | grep -i can | tail       # check driver load messages

# 2) Use in the program: Connection Control → CAN type SocketCAN → channel can0 → select bitrate → Open
```

To check directly from the terminal without the program:

```bash
# Set Classic CAN 500 kbit/s and bring up
sudo ip link set can0 down
sudo ip link set can0 type can bitrate 500000
sudo ip link set can0 up

# CAN FD (arbitration 500 kbit/s / data 2 Mbit/s)
sudo ip link set can0 down
sudo ip link set can0 type can bitrate 500000 dbitrate 2000000 fd on
sudo ip link set can0 up

# Status / receive / send
ip -details -statistics link show can0
candump can0                       # check reception (Ctrl+C to quit)
cansend can0 123#1122334455667788  # send ID 0x123, 8 bytes
cansend can0 123##1112233          # send a CAN FD frame (first digit after ## is the flags)

# Bring down
sudo ip link set can0 down
```

## 🧪 6. Testing with Virtual CAN (vcan) without a Device

```bash
sudo modprobe vcan
sudo ip link add dev vcan0 type vcan
sudo ip link set vcan0 mtu 72    # only when testing CAN FD (run before up)
sudo ip link set up vcan0
```

Select channel `vcan0` in the program and Open. You can send frames from another terminal to check reception.

```bash
cansend vcan0 123#DEADBEEF       # check that the program receives it
candump vcan0                    # check frames sent by the program
```

Delete: `sudo ip link delete vcan0` (it disappears automatically on reboot)

## 🩺 7. Troubleshooting

| Symptom | Cause / Solution |
|---|---|
| `This package has no executable for the CPU of this PC` | The package has no build for this CPU. Check `ls bin` and get a package that includes your CPU. |
| `The Linux on this PC is too old to run this program` | Update the OS, or get a package built on an older OS. |
| `Permission denied` | `chmod +x run.sh install_runtime_en.sh` |
| `qt.qpa.plugin: Could not load the Qt platform plugin` | Run `./install_runtime_en.sh` again. If it still fails, `QT_QPA_PLATFORM=xcb ./run.sh` |
| Korean text appears as boxes (□) | `sudo apt-get install -y fonts-nanum && fc-cache -f` |
| Cannot type Korean | Run `./install_runtime_en.sh --korean` and log in again. Check that `ibus` is running (`pgrep ibus-daemon`) and Hangul is added in `ibus-setup`. If needed: `QT_IM_MODULE=ibus ./run.sh` |
| `can0` not shown in `ip link show` | Reconnect the device → check the driver messages at the end of `dmesg`. On Ubuntu, `sudo apt-get install linux-modules-extra-$(uname -r)` and reboot. |
| Permission error on CAN Open | Check that the account is in the sudo group: `groups`. If not, the administrator runs `sudo usermod -aG sudo <account>`, then log in again. |
| Settings are not saved | Check write permission for the program folder: `ls -ld .` (move it under your home folder). |
| Tooltips/windows look odd (Wayland) | Run in X11 mode with `QT_QPA_PLATFORM=xcb ./run.sh` |

## 🔄 8. Moving to Another PC / Updating / Removing

- **Moving to another PC**: copy the whole folder → on the new PC, `./install_runtime_en.sh` → `./run.sh`. Even if the CPU is different, the same package can be used as is.
- **Updating**: extract the new version and copy `session.pjjsettings` and `{PK_FILE_NAME}` from the old folder to the new folder to keep your settings.
- **Removing**: just delete the folder. (System packages installed by `install_runtime_en.sh` may be used by other programs, so it is recommended to leave them.)

## 📝 {APP_VERSION} Features and Usage Notes
- User Panel: register CMD/RCV/DEL in Add TX Tool → sequence → Command Sequence Settings.
- Saved CMD data and RCV bit conditions can be edited and run without a DBC.
- sequence Force skips RCV, and Packet Control excludes CMD/RCV and runs only START/STOP/DEL. There is no global Force RUN mode.
- In TX counter, you can choose to sync with the latest RX DBC raw value, with Up/Down/Round-trip fallback.
- Real-time graph: Reset Zoom (last 30 seconds), Auto Y (currently displayed range), Fit All Data (retained range).
- Closing the panel or changing the mode stops all panel transmissions and waits.
- Registered packets with a positive cycle are sent from a per-BUS thread, and missed deadlines are shown in the system log. Actual cycle performance must be verified with the hardware/driver.
- Panel FD/BRS, slider Home, minimum grid, and window enlargement are supported.
- LOG tab: supports Extended ID filter, retransmission of filtered results, and Save to extract in the original format.
- CAN sessions start in Close state regardless of the previously saved state. The sudo password is used only while running and is never stored in files or logs.
- Detailed usage: MANUAL_en.md / USER_PANEL_SEQUENCE_en.md / USER_PANEL_RELEASE_NOTES_en.md.
- If Consolas is not available or a character is not supported, the system fallback font is used.
"""
        for readme_name, readme_content in (('README_Linux_Guide.md', readme_ko),
                                            ('README_Linux_Guide_en.md', readme_en)):
            with open(os.path.join(dist_path, readme_name), 'w', encoding='utf-8', newline='\n') as f:
                f.write(readme_content)
    elif current_os == 'Windows':
        readme_path = os.path.join(dist_path, 'README_Windows_Run_Guide.txt')
        readme_content = f"""{app_prefix.replace('_', ' ')} - 윈도우 환경 실행 가이드

이 프로그램은 단일 실행 파일(.exe)로 제작되어 파이썬 설치 없이 바로 실행 가능합니다.

[실행 방법]
{app_prefix}_{APP_VERSION}_win.exe 파일을 더블클릭하여 실행하세요.

[주의 사항]
- PCAN 장치는 PEAK-System PCAN 드라이버, Kvaser 장치는 Kvaser Drivers for Windows가 필요합니다.
- Kvaser 드라이버: https://kvaser.com/download/ (설치 후 장치 연결 및 프로그램 재실행)
- 통신 설정의 CAN 방식에서 PCAN/Kvaser 선택 → Refresh → Channel/속도 설정 → Open.
- Kvaser FD 지원 채널은 Data Bitrate에서 Off 이외의 값을 선택합니다. 지원 속도/ISO 모드는 장치에 따라 다릅니다.
- candleLight(gs_usb) 어댑터: CAN 방식에서 "candleLight" 선택. Classic CAN 전용입니다.
  장치 관리자에서 COM 포트로 보이면 Driver\\candleLight_WinUSB\\Install_candleLight_WinUSB.bat 으로 WinUSB 드라이버로 바꾸세요.
- 드라이버 도구는 이 폴더의 Driver 폴더에 있습니다:
  Driver\\pcan_drivers\\Install_PCAN_USB_Driver.bat (PEAK PCAN-USB 드라이버, x64),
  Driver\\kvaser_drivers\\kvaser_drivers_setup-*.exe (Kvaser 드라이버 설치), Driver\\candleLight_WinUSB (candleLight WinUSB 전환).
- 가상환경 폴더나 소스코드(src)를 같이 들고 다닐 필요 없이, 이 폴더 내용물만 복사해서 배포/사용하시면 됩니다!

[{APP_VERSION} 기능 및 사용 안내]
- CAN 방식(PCAN/Kvaser)을 BUS별로 선택합니다. 서로 다른 BUS에서 PCAN과 Kvaser를 함께 사용할 수 있습니다.
- Kvaser 채널은 S/N과 장치 내부 채널 번호(#)로 구분하며, Refresh로 새로 연결한 장치를 다시 검색합니다.
- 가상 채널([Virtual])만 보이면 드라이버 설치 후 재부팅 여부와 장치 관리자의 Kvaser 장치 인식 상태를 확인하세요.
- State 칸: 버스 부하율·fps·오류 프레임 수. Log View: Vector ASC/BLF 열기, CSV 내보내기.
- 수신부 Trigger 열(더블클릭): 신호 조건 트리거 시 앞뒤 1~2초를 Log 폴더에 TRC로 저장합니다.
- Record 창 ID 필터: 범위 제외 / 범위만 기록 (예: 100-1FF, 321).
- GW: BUS 간 게이트웨이(시작 시 항상 꺼짐). Stats: 신호 통계와 수신 타임아웃 알림(알림 전용).
- User Panel: Add TX Tool → sequence → 명령 시퀀스 설정에서 CMD/RCV/DEL을 등록합니다.
- DBC 없이 CMD 데이터와 RCV 비트 조건을 수정·실행하며, 패널 Save에 함께 저장됩니다.
- sequence Force/Packet Control은 각 sequence 도구에서 실행하며, 전역 Force RUN 모드는 없습니다.
- TX counter의 RX 동기화 모드, 그래프 Reset Zoom/Auto Y/Fit All Data, BUS별 주기 송신 진단을 포함합니다.
- 패널 닫기·모드 변경 시 패널의 모든 송신과 대기를 중지합니다.
- 패널 FD/BRS, 슬라이더 Home, 최소 격자와 창 확대 동작을 지원합니다.
- LOG 탭: Extended ID 필터, 필터 결과 재전송, Save로 원본 형식 추출 저장을 지원합니다.
- UI 기본 글꼴은 Consolas입니다.
- 상세 사용법: MANUAL.md / USER_PANEL_SEQUENCE.md / USER_PANEL_RELEASE_NOTES.md.
- 소스 수정은 기존 EXE에 자동 반영되지 않습니다. 새로 빌드된 {APP_VERSION} 실행 파일을 사용하세요.
"""
        with open(readme_path, 'w', encoding='utf-8') as f:
            f.write(readme_content)

    print(f"=== 빌드가 완료되었습니다! 'dist/{app_prefix}_{APP_VERSION}{os_suffix}' 폴더를 확인해주세요. ===")

if __name__ == '__main__':
    build_executable()
