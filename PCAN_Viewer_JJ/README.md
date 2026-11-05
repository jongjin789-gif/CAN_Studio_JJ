# Universal CAN Monitor (R012_1)

아래 실행·빌드 명령은 프로젝트 루트 폴더에서 수행합니다.

## R009 업데이트

- 등록 패킷의 주기 송신을 BUS별 스레드로 처리하고, 지연/누락 주기를 시스템 로그에 표시합니다.
- DBC counter에 최근 수신값 동기화 + 업/다운/왕복 fallback을 추가했습니다.
- 실시간/통합 그래프에 최근 30초 Reset Zoom, Auto Y, Fit All Data를 제공합니다.
- 시퀀스별 강제 실행(RCV 생략)과 패킷 제어(CMD/RCV 생략)를 제공합니다. 전역 Force RUN은 없습니다.
- Linux는 이전 CAN 연결 상태와 관계없이 Close로 시작하고, 권한이 필요한 연결 명령에서만 비밀번호를 묻습니다.
- INIT 영역 splitter 크기 조절, 슬라이더 레이아웃 및 로그 줄바꿈/가독성을 개선했습니다.

상세 내용: [사용자 매뉴얼](MANUAL.md), [시퀀스 사용법](USER_PANEL_SEQUENCE.md), [변경 내역](USER_PANEL_RELEASE_NOTES.md).

**Universal CAN Monitor**는 Python 및 PyQt5 기반의 CAN 통신 모니터링 및 분석 도구입니다. Windows PCAN과 Linux SocketCAN 실행 경로를 제공하며, 실제 하드웨어 지원은 OS 드라이버와 장비 환경 확인이 필요합니다. DBC/SYM 실시간 디코딩 및 시계열 그래프를 지원합니다.

---

## ✨ 주요 기능 (Features)

- **크로스 플랫폼 지원**: Windows(PEAK-System PCAN) 및 Linux(SocketCAN / vcan) 환경에서 동일한 UI 및 기능 제공.
- **멀티 채널 모니터링**: 4개의 CAN 버스 채널을 동시에 연결하고 모니터링 가능.
- **CAN FD 지원**: Classic CAN 뿐만 아니라 CAN FD(Flexible Data-rate) 통신 및 ISO/Non-ISO, Data Bitrate 설정 지원.
- **CAN 송신 (Tx) 및 패킷 관리**: 송신 패킷을 생성하여 CAN/FD 프레임 전송 가능. DBC 심볼과 연동하여 물리 값을 입력하면 자동으로 Raw Data(HEX) 연산 처리. Cycle Time 지원, 단축키(스페이스바 단발 전송, Ctrl+C/V 복사 붙여넣기, Delete 삭제) 지원, `.xmt` 파일 저장/불러오기 지원.
- **자동 상태 저장 및 복구**: 사용자 데이터 폴더의 통합 세션에 장치/속도, DB, 패킷, 그래프, 유저패널 구성을 저장합니다. Windows는 저장 당시 연결된 BUS를 재연결하고, Linux는 장치/속도만 복원한 뒤 BUS를 Close 상태로 시작합니다.
- **데이터베이스(DBC/SYM) 연동**: `.dbc` 파일 및 PEAK `.sym` 파일(v5.0, v6.0)을 불러와 Raw CAN 데이터를 물리 값(Physical Value)으로 실시간 자동 변환. 메시지(부모) 체크박스를 통해 하위 시그널 일괄 선택/해제 기능 지원.
- **실시간 그래프 렌더링**: Reset Zoom은 최근 30초, Auto Y는 현재 보이는 데이터, Fit All Data는 보관된 전체 구간을 표시합니다. 통합 그래프는 X축 범위를 공유합니다.
- **데이터 로깅 (Record)**: 실시간으로 수신되는 메시지를 `.trc` (Trace) 파일 포맷으로 저장 기능 제공.
- **TRC 로그 뷰어 (Log Viewer)**: 저장된 `.trc` 로그 파일을 오프라인에서 불러와 DBC/SYM 파일을 기준으로 재해석(Parsing)하여 분석할 수 있는 내장 뷰어 제공.
- **독립 뷰어 모드 (Log Viewer Mode)**: `PCAN_Viewer_JJ.pk` 설정 파일의 `"viewer_mode_only"` 값을 `true`로 변경하여 하드웨어 연결 및 송신(Tx) 기능이 숨겨진 뷰어 전용 UI로 전환할 수 있습니다. (뷰어 모드 시 기존 패킷/설정 데이터 덮어쓰기 보호)
- **타이틀 바 버전 표시**: 실행 파일명과 `build_exe.py`의 `APP_VERSION = "R012_1"`에서 버전을 확인합니다. 소스 변경은 재빌드해야 실행 파일에 반영됩니다.

---

## ⚙️ 시스템 요구 사항 및 설치 (Installation)

R012_1 자동 테스트는 **Python 3.13** 환경에서 수행합니다. 새 개발 환경도 Python 3.13을 기준으로 구성하세요.

### 🪟 Windows 환경
1. **사전 준비**: PEAK-System PCAN 드라이버가 설치되어 있어야 합니다. (PCAN-USB 등 연결 필요)
2. **의존성 패키지 자동 설치**: 프로젝트 폴더 내의 `install_windows.bat` 파일을 실행합니다.
   - 자동으로 `venv_win` 가상환경이 생성되고 필요한 파이썬 패키지들이 한 번에 설치됩니다.

### 🐧 Linux (Ubuntu/Debian) 환경
소스 코드를 직접 실행하기 위한 개발 환경 설정 가이드입니다.

1. **의존성 설치 스크립트 실행 (최초 1회)**
   - 프로젝트 폴더에서 아래 스크립트를 실행하면, 개발에 필요한 시스템 패키지와 Python 라이브러리가 자동으로 설치되고 `venv_linux` 가상환경이 생성됩니다.
```bash
# 스크립트에 실행 권한을 부여하고 실행합니다.
chmod +x install_linux.sh
./install_linux.sh
```
*참고: 위 스크립트는 PCAN 드라이버 자체를 설치하지 않습니다. 실제 하드웨어(can0) 외에 가상 CAN(vcan0) 테스트도 가능합니다.*

---

## 🚀 사용법 (Usage)

1. **프로그램 실행**:
   - Windows: 터미널에서 `venv_win\Scripts\activate` 입력 후 `python main.py` 실행
   - Linux (가상 CAN): 터미널에서 `source venv_linux/bin/activate && python3 main.py` 실행
   - Linux (실제 CAN): 일반 사용자로 실행합니다. 인터페이스 설정에 권한이 필요하면 앱이 sudo 비밀번호를 묻고 해당 명령만 재시도합니다. sudo 인증 캐시가 살아 있으면 다시 묻지 않습니다. 입력한 비밀번호는 연속 명령(down/up)을 위해 최대 60초만 메모리에 두고 버리며, 파일이나 로그에는 저장하지 않습니다. 각 명령은 10초 안에 끝나지 않으면 중단됩니다.

2. **상세 사용법**:
   - 프로그램의 모든 기능에 대한 자세한 설명은 **MANUAL.md (사용자 매뉴얼)** 파일을 참고하세요.
   - User Panel 인수 점검은 **USER_PANEL_ACCEPTANCE_CHECKLIST.md**를 참고하세요.
   - User Panel 변경 요약은 **USER_PANEL_RELEASE_NOTES.md**를 참고하세요.

3. **채널 연결 (Connection)**:
   - 상단의 **Connection Control** 패널에서 사용하려는 Bus의 채널과 통신 속도(Baudrate)를 선택합니다.
   - CAN FD 채널인 경우 FD 체크박스 및 Data Baudrate 옵션이 활성화됩니다.
   - **Open** 버튼을 클릭하여 통신을 시작합니다.

4. **데이터베이스 파일 적용**:
   - **Database Files** 패널에서 **Load DBC/SYM** 버튼을 클릭하여 해당 Bus에 맞는 DB 파일을 로드합니다.
   - DB 파일이 로드되면, 수신되는 CAN Raw ID가 트리에서 메시지 이름 및 하위 시그널로 묶여 표시됩니다.

5. **송신 (Write) 패킷 생성 및 전송**:
   - 메인 창 하단의 패널에서 **패킷 생성하기**를 눌러 전송할 데이터를 기입합니다. DB 심볼을 선택하면 하위 항목에 값을 바로 기입할 수 있습니다.
   - 생성된 패킷을 선택하고 **스페이스바**를 누르면 1회 전송되며, **Start** 버튼을 누르면 설정된 Cycle Time에 맞춰 주기적으로 전송됩니다.
   - 여러 패킷을 선택해 단축키로 지우거나(`Delete`) 복사(`Ctrl+C` / `Ctrl+V`)할 수 있습니다.
   - `PCAN-Explorer`, `PCAN-View`에서 생성한 `.xmt` 송신 목록 파일을 불러오거나 현재 목록을 저장할 수 있습니다.

6. **실시간 그래프 뷰어**:
   - 모니터링 트리 하위의 Signal 체크박스를 클릭하여 활성화합니다.
   - **[선택된 항목 실시간 그래프 보기]** 버튼을 클릭하여 시계열 데이터의 변화를 확인합니다.

7. **데이터 기록 및 로그 확인**:
   - **Record** 버튼을 눌러 모니터링 중인 데이터를 `.trc` 파일로 기록합니다.
   - 기록이 완료된 후 **Open TRC Log Viewer** 버튼을 눌러 기록된 데이터를 분석할 수 있습니다.

---

## 🛠️ 배포 및 빌드 (Build Executable)

이 프로그램은 `PyInstaller`를 이용해 파이썬이 설치되지 않은 환경에서도 실행 가능한 단일 독립 실행 파일(.exe, 로컬 바이너리)로 빌드할 수 있습니다.

1. 터미널(또는 명령 프롬프트)에서 운영체제에 맞는 가상환경을 활성화한 후 빌드 스크립트를 실행합니다.
   
   **Windows**:
   ```cmd
   venv_win\Scripts\activate
   python build_exe.py
   ```
   
   **Linux**:
   ```bash
   source venv_linux/bin/activate
   python build_exe.py
   ```
2. 빌드가 성공적으로 완료되면 프로젝트 폴더 내 `dist` 디렉토리 하위에 플랫폼별 배포 폴더가 생성됩니다.
   - Windows: `dist/PCAN_Viewer_JJ_R012_1_win/PCAN_Viewer_JJ_R012_1_win.exe`
   - Linux: `dist/PCAN_Viewer_JJ_R012_1_linux/PCAN_Viewer_JJ_R012_1_linux` (리눅스용 README 파일 포함)

3. 새로 생성된 R012_1 배포 폴더의 실행 파일을 실행합니다. 이전 실행 파일은 소스 수정만으로 갱신되지 않습니다.
4. 배포 폴더에는 `MANUAL.md`, `USER_PANEL_SEQUENCE.md`, `USER_PANEL_RELEASE_NOTES.md`와 OS별 실행 안내가 포함됩니다.
5. 패널의 **Save**로 시퀀스·도구 설정을 별도로 저장하세요. 실행 로그와 실행 중 상태는 패널 파일에 저장되지 않습니다.

검증 명령: `python -B -m unittest discover -s tests`. 실제 CAN 장비 검증과 OS별 R012_1 배포 바이너리 검증은 별도로 수행해야 합니다.

*(빌드 스크립트는 PCANBasic.dll 및 아이콘 파일 등 필요 리소스들을 실행 파일 내부에 자동으로 함께 패키징하도록 설계되어 있습니다.)*
