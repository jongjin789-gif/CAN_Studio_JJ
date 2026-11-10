PEAK-System PCAN-USB 장치 드라이버 (경량 설치)
==========================================================

내용
  - pcan_usb.inf / PCAN_USB.sys / PCAN_USB.cat (약 550 KB)
  - 버전 5.1.1.20077 (2026-04-24), Microsoft WHQL 서명, 64비트(x64) Windows 전용
  - PEAK 공식 설치 프로그램(약 185 MB)에서 장치 드라이버만 포함한 것입니다.
    PCAN-View 등 부가 프로그램은 설치되지 않습니다.
    CAN Studio JJ 는 PCANBasic.dll 을 자체 포함하므로 이 드라이버만 있으면 됩니다.

지원 장치 (pcan_usb.inf 기준)
  PCAN-USB, PCAN-USB FD, PCAN-USB Pro FD, PCAN-Chip USB, PCAN-USB X6,
  PCAN-USB Hub, PCAN-USB XL(PID 0030)
  ※ PCI/PCIe 카드 등 다른 제품은 PEAK 공식 설치 프로그램을 사용하세요.

사용 방법
  1. Install_PCAN_USB_Driver.bat 을 더블클릭합니다.
  2. 관리자 권한 확인 창에서 "예"를 누릅니다.
  3. "[OK] Driver installed" 가 나오면 PCAN USB 장치를 뽑았다가 다시 꽂습니다.
     장치 관리자의 "CAN-Hardware" 아래에 장치가 표시되면 정상입니다.
  - "already installed" 는 같은 버전 이상의 드라이버가 이미 있다는 뜻입니다.

설치가 안 될 때
  PEAK 공식 드라이버 설치 프로그램을 사용하세요:
  https://www.peak-system.com/Drivers.523.0.html

참고
  - 드라이버 파일은 PEAK-System 의 저작물입니다. 외부 배포 시 PEAK 사용권 계약을 확인하세요.
  - Linux 에서는 커널 기본 드라이버(peak_usb)를 사용하므로 필요 없습니다.
