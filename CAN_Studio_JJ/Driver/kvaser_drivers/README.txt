Kvaser CAN 드라이버 (Kvaser Drivers for Windows)
==========================================================

내용
  - kvaser_drivers_setup-5.53.673.exe : Kvaser AB 공식 드라이버 설치 프로그램 (서명: Kvaser AB)
  - 장치 드라이버와 CANlib 라이브러리(canlib32.dll)를 함께 설치합니다.
    CAN Studio JJ 는 이 CANlib 를 사용하므로 드라이버 전체 설치가 필요합니다.

사용 방법
  1. kvaser_drivers_setup-*.exe 를 더블클릭합니다. (관리자 권한 확인 창에서 "예")
  2. 설치 화면의 안내에 따라 설치합니다.
     처음 설치하는 PC 에서는 Windows 가 "이 장치 소프트웨어를 설치하시겠습니까?" 를 물을 수 있습니다.
  3. 설치가 끝나면 Kvaser 장치를 연결하고, CAN Studio JJ 를 실행 중이었다면 종료 후 다시 실행합니다.
     (Kvaser 라이브러리는 프로그램을 다시 시작해야 인식됩니다.)
  4. CAN Studio JJ 의 CAN 방식에서 Kvaser(또는 ALL)를 선택하고 Refresh 합니다.

CAN Studio JJ 에서의 자동 안내
  Kvaser 장치가 연결되어 있는데 드라이버가 없으면, Refresh(또는 CAN 방식 선택) 시
  이 설치 프로그램을 실행할지 묻습니다.

최신 버전
  https://kvaser.com/download/  (Kvaser Drivers for Windows)
  새 버전으로 바꿀 때는 이 폴더의 설치 파일만 교체하면 됩니다.
  (파일 이름이 kvaser_drivers_setup 으로 시작하면 자동으로 찾습니다.)

참고
  - Linux 에서는 커널 기본 드라이버(kvaser_usb)로 can0 등이 생성되며 SocketCAN 으로 사용합니다.
