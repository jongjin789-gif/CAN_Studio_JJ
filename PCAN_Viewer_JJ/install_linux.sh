#!/bin/bash

# 스크립트 실행 중 에러 발생 시 즉시 종료
set -e

echo "=== 사전 요구사항 확인: python3, pip ==="
if ! command -v python3 &> /dev/null
then
    echo "[오류] python3가 설치되어 있지 않습니다. 'sudo apt-get install python3' 명령어로 설치 후 다시 시도해주세요."
    exit 1
fi

if ! command -v pip3 &> /dev/null
then
    echo "[오류] python3-pip가 설치되어 있지 않습니다. 'sudo apt-get install python3-pip' 명령어로 설치 후 다시 시도해주세요."
    exit 1
fi

echo "=== 리눅스 시스템 패키지 업데이트 ==="
sudo apt-get update

echo "=== 필수 시스템 패키지 설치 ==="
# - python3-venv: 파이썬 가상환경(venv) 관리를 위해 필요합니다.
# - can-utils, iproute2: 리눅스 SocketCAN 제어(vcan 생성, can-utils) 및 통신에 필수적인 도구입니다.
# - libxcb-*, libxkbcommon-*, libqt5x11extras5: 리눅스 디스플레이(X11/Wayland) 환경에서 PyQt5 GUI 실행 시 흔히 발생하는 의존성 부족(qt.qpa.plugin 에러) 문제를 해결하기 위한 라이브러리들입니다.
# - fonts-nanum: 리눅스/WSL(Windows Subsystem for Linux) 환경에서 GUI의 한글이 깨지는 현상을 방지하기 위한 나눔 폰트입니다.
sudo apt-get install -y python3-venv can-utils iproute2 \
    libxcb-cursor0 libxcb-xinerama0 libxcb-icccm4 libxcb-image0 \
    libxcb-keysyms1 libxcb-randr0 libxcb-render-util0 libxcb-xfixes0 \
    libxkbcommon-x11-0 libqt5x11extras5 fonts-nanum libopengl0

echo "=== 파이썬 가상환경(venv) 생성 ==="
if [ -d "venv_linux" ]; then
    echo "기존 가상환경 폴더를 발견하여 삭제합니다..."
    rm -rf venv_linux
fi
python3 -m venv venv_linux

# 가상환경 활성화
source venv_linux/bin/activate

echo "=== 파이썬 필수 라이브러리 설치 ==="
pip install --upgrade pip
pip install -r requirements.txt

echo "========================================================================"
echo "설치가 완료되었습니다!"
echo ""
echo "실행하려면 아래 명령어로 가상환경을 활성화한 후 실행하세요:"
echo "  $ source venv_linux/bin/activate && python3 main.py"
echo ""
echo "참고: 프로그램은 일반 사용자로 실행하세요(sudo로 전체 실행 비권장)."
echo "실제 CAN 하드웨어(예: can0) 설정에 권한이 필요하면 프로그램이 sudo 비밀번호를 묻고"
echo "해당 ip 명령만 권한 상승하여 실행합니다. root로 실행하면 세션/캐시 파일이 root 소유가 됩니다."
echo ""
echo "💡 [팁] 실제 하드웨어 없이 가상 CAN 인터페이스(vcan)로 테스트하는 방법:"
echo "1. 새 터미널을 열고 아래 명령어를 실행하여 vcan0 인터페이스를 생성하고 활성화합니다."
echo "   sudo modprobe vcan"
echo "   sudo ip link add dev vcan0 type vcan"
echo "   sudo ip link set up vcan0"
echo "2. 프로그램의 Connection Control에서 'vcan0' 채널을 선택하고 Open 하세요."
echo "========================================================================"