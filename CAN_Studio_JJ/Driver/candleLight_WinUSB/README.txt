candleLight (gs_usb) USB-CAN 어댑터 WinUSB 드라이버 전환
==========================================================

대상 장치
  - 장치 관리자 이름: "candleLight USB to CAN adapter (Interface 0)"
  - 하드웨어 ID: USB\VID_1D50&PID_606F&MI_00
  - Classic CAN 전용(ADM3053 트랜시버 사용 제품은 CAN FD 불가)

언제 필요한가
  장치 관리자에서 이 어댑터가 "포트(COM & LPT)" 아래 COM 포트로 보이면
  시리얼 드라이버가 잘못 설치된 상태라 CAN 통신을 할 수 없습니다.
  WinUSB 로 바꿔야 CAN Studio JJ 에서 사용할 수 있습니다.

사용 방법
  1. 어댑터를 USB 에 꽂습니다.
  2. Install_candleLight_WinUSB.bat 을 더블클릭합니다.
  3. 관리자 권한 확인 창에서 "예"를 누릅니다.
  4. "[완료] ... WinUSB 드라이버로 전환되었습니다" 가 나오면 끝입니다.
     장치 관리자의 "범용 직렬 버스 장치" 아래에 어댑터가 표시됩니다.
  5. "아직 WinUSB 로 확인되지 않습니다" 가 나오면 USB 를 뽑았다 다시 꽂고 한 번 더 실행하세요.

동작 내용
  - 외부 드라이버를 설치하지 않습니다. Windows 기본 WinUSB 드라이버를 사용합니다.
  - 이 어댑터에 잘못 설치된 시리얼 드라이버 패키지(oemNN.inf)만 제거한 뒤
    장치를 다시 검색하면, 장치가 요청하는 WinUSB 가 자동으로 적용됩니다.
  - 같은 드라이버 패키지를 다른 장치가 쓰고 있으면 제거하지 않고 중단합니다.
  - 이미 WinUSB 이면 아무것도 바꾸지 않습니다.
  - 변경 없이 점검만 하려면 명령 프롬프트에서:
      Install_candleLight_WinUSB.bat -DryRun

자동 전환이 안 될 때 (수동 방법)
  1. Zadig(https://zadig.akeo.ie) 실행
  2. Options → List All Devices 체크
  3. "candleLight USB to CAN adapter (Interface 0)" 선택
  4. 드라이버를 WinUSB 로 고르고 Replace Driver
  ※ "USB Serial (CDC)" 를 고르면 지금처럼 COM 포트가 되므로 선택하지 마세요.

Linux
  드라이버 설치가 필요 없습니다. 커널 gs_usb 드라이버로 can0 으로 인식되며
  CAN Studio JJ 에서 SocketCAN 으로 선택합니다.
