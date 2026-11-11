#!/bin/bash

# 스크립트 실행 중 에러 발생 시 즉시 종료
set -e

# 어느 위치에서 실행해도 requirements.txt 등을 찾도록 스크립트 폴더로 이동
cd "$(dirname "$0")"

echo "=== 사전 요구사항 확인: python3 (3.10 이상) ==="
if ! command -v python3 &> /dev/null
then
    echo "[오류] python3가 설치되어 있지 않습니다. 'sudo apt-get install python3' 명령어로 설치 후 다시 시도해주세요."
    exit 1
fi

# pyqtgraph 0.14 / cantools 41 은 Python 3.10 이상이 필요합니다 (Ubuntu 22.04 이상, Debian 12 이상).
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'
then
    echo "[오류] Python 3.10 이상이 필요합니다. 현재 버전: $(python3 --version 2>&1)"
    echo "       Ubuntu 22.04 이상 또는 Debian 12 이상을 사용하거나, Python 3.10 이상을 설치한 뒤 다시 시도해주세요."
    exit 1
fi

echo "=== 리눅스 시스템 패키지 업데이트 ==="
sudo apt-get update

echo "=== 필수 시스템 패키지 설치 ==="
# - python3-venv: 파이썬 가상환경(venv) 생성에 필요합니다(venv 안에 pip가 함께 설치됩니다).
# - iproute2: 프로그램이 CAN 인터페이스를 설정할 때 사용하는 'ip' 명령입니다(필수).
# - can-utils: candump/cansend 등 SocketCAN 점검 도구입니다(선택, 문제 확인용).
# - libxcb-*, libxkbcommon-x11-0, libqt5x11extras5, libgl1, libegl1, libopengl0:
#   PyQt5 GUI 실행에 필요한 화면 표시 라이브러리입니다. 없으면 'qt.qpa.plugin' 오류로 창이 뜨지 않습니다.
# - fonts-nanum: 한글 표시용 글꼴입니다. 없으면 한글이 네모(□)로 보입니다.
sudo apt-get install -y python3-venv iproute2 can-utils \
    libxcb-xinerama0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 \
    libxcb-randr0 libxcb-render-util0 libxcb-xfixes0 libxcb-shape0 libxcb-xkb1 \
    libxkbcommon-x11-0 libqt5x11extras5 libgl1 libegl1 libopengl0 fonts-nanum

# PyPI에는 x86_64 용 PyQt5 바이너리(wheel)만 있습니다. ARM(라즈베리파이 등)에서 pip로 설치하면
# 소스 빌드를 시도하다 'specify a working qmake' 오류로 실패하므로, 시스템 패키지(apt)의
# PyQt5/numpy를 설치하고 가상환경에서 이를 함께 사용(--system-site-packages)합니다.
ARCH="$(uname -m)"
USE_SYSTEM_PYQT=0
if [ "$ARCH" != "x86_64" ]; then
    USE_SYSTEM_PYQT=1
    echo "=== ARM 계열($ARCH) 감지: 시스템 PyQt5/numpy 설치 ==="
    sudo apt-get install -y python3-pyqt5 python3-numpy
fi

echo "=== 파이썬 가상환경(venv) 생성 ==="
if [ -d "venv_linux" ]; then
    echo "기존 가상환경 폴더를 발견하여 삭제합니다..."
    rm -rf venv_linux
fi
if [ "$USE_SYSTEM_PYQT" = "1" ]; then
    python3 -m venv --system-site-packages venv_linux
else
    python3 -m venv venv_linux
fi

# 가상환경 활성화
source venv_linux/bin/activate

echo "=== 파이썬 필수 라이브러리 설치 ==="
pip install --upgrade pip
if [ "$USE_SYSTEM_PYQT" = "1" ]; then
    # PyQt5는 apt로 설치된 시스템 패키지를 사용하므로 pip 설치 대상에서 제외합니다.
    REQ_TMP="$(mktemp)"
    grep -viE '^[[:space:]]*PyQt5([^-_a-zA-Z0-9]|$)' requirements.txt > "$REQ_TMP"
    pip install -r "$REQ_TMP"
    rm -f "$REQ_TMP"
else
    pip install -r requirements.txt
fi

echo "=== 설치 확인 ==="
python3 -c "import PyQt5.QtCore, can, cantools, pyqtgraph; print('PyQt5', PyQt5.QtCore.PYQT_VERSION_STR, '/ python-can', can.__version__, '/ cantools', cantools.__version__, '/ pyqtgraph', pyqtgraph.__version__)"

echo "========================================================================"
echo "설치가 완료되었습니다!"
echo ""
echo "실행하려면 아래 명령어로 가상환경을 활성화한 후 실행하세요:"
echo "  $ source venv_linux/bin/activate && python3 main.py"
echo ""
echo "참고: 프로그램은 일반 사용자로 실행하세요(sudo로 전체 실행 비권장)."
echo "실제 CAN 하드웨어(예: can0) 설정에 권한이 필요하면 프로그램이 창에서 sudo 비밀번호를 묻고"
echo "해당 ip 명령만 권한 상승하여 실행합니다. 실행 계정이 sudo를 사용할 수 있어야 합니다."
echo "root로 실행하면 세션/캐시 파일이 root 소유가 됩니다."
echo ""
echo "CAN 장치 드라이버: PCAN(peak_usb), Kvaser(kvaser_usb) 등은 리눅스 커널 기본 드라이버로"
echo "can0, can1 ... 으로 인식됩니다. PEAK 홈페이지의 별도 드라이버(chardev 방식)를 설치하면"
echo "can0이 나타나지 않으므로 설치하지 마세요. 확인: ip link show"
echo ""
echo "💡 [팁] 실제 하드웨어 없이 가상 CAN 인터페이스(vcan)로 테스트하는 방법:"
echo "1. 새 터미널을 열고 아래 명령어를 실행하여 vcan0 인터페이스를 생성하고 활성화합니다."
echo "   sudo modprobe vcan"
echo "   sudo ip link add dev vcan0 type vcan"
echo "   sudo ip link set vcan0 mtu 72     # CAN FD 프레임까지 테스트할 때만 필요"
echo "   sudo ip link set up vcan0"
echo "2. 프로그램의 Connection Control에서 'vcan0' 채널을 선택하고 Open 하세요."
echo "========================================================================"
