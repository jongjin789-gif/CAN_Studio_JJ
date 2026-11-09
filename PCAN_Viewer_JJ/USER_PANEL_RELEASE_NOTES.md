# User Panel Release Notes

## R013

- **CAN 장비 모듈화**: PCAN-Basic·SocketCAN 연결 코드를 메인창에서 `src/can_devices/` 백엔드로 분리했습니다. 연결 설정 그리드에 BUS별 **CAN 방식** 열을 추가하고, 선택한 방식은 통합 설정에 BUS별로 저장합니다. 이전 설정 파일은 저장된 장치 종류로 방식을 판단합니다. 연결 중에는 방식을 바꿀 수 없습니다.
- **Kvaser 지원 (Windows)**: 별도 Python 패키지 없이 python-can의 CANlib 바인딩을 사용합니다. Classic CAN과 CAN FD(ISO/Non-ISO)를 지원하며, 채널이 지원하지 않는 FD 모드는 연결 전에 안내합니다. CANlib 기본값이 없는 20k/800k 속도는 비트 타이밍을 직접 지정합니다. 수신 스레드에서 송신 에코는 받지 않습니다.
- **채널 식별**: Kvaser 채널은 CANlib 채널 번호 대신 S/N과 장치 내부 채널 번호로 식별합니다. Refresh·세션 복원·BUS 간 중복 장치 검사에 같은 기준을 사용합니다. Refresh 뒤 구성이 바뀐 채널은 연결을 거부하고 재선택을 안내합니다.
- **드라이버 미설치/장치 없음**: Kvaser 드라이버가 없어도 다른 CAN 방식은 정상 동작합니다. 채널 목록에 설치 안내 또는 장치 확인 안내를 표시합니다.
- 실제 Kvaser 하드웨어 송수신은 장비 연결 환경에서 별도로 검증해야 합니다. 자동 테스트는 CANlib을 모의(mock)하여 수행합니다.

## 메인 TX·유저 패널 주기 송신 보호 (소스)

- 메인 TX의 GUI 타이머 송신을 BUS별 백그라운드 송신으로 전환하고 유저 패널과 공통 스케줄러를 사용합니다. CRC/카운터 계산·실제 송신과 화면/기록 처리를 분리합니다. Windows 타이머 해상도 요청은 작업 중에만 유지합니다.
- 지연 후 원래 목표 시각의 정수 주기로 복귀하여 누적 오차를 줄이고, 밀린 프레임은 몰아서 보내지 않습니다. 메인 TX의 누락/최대 지연도 BUS 로그에 표시합니다. 실패한 Hyundai CRC 송신은 Alive 카운터를 진행하지 않습니다.
- 로그 재생 시작 시 메인 TX를 정지시키던 동작을 제거했습니다. BUS 쓰기를 직렬화하고, 주기 송신 중이거나 예정 시각에 가까우면 로그 선택/연속 전송이 해당 패킷을 유지한 채 재시도합니다. 전체 정지·연결 해제·로그 창 닫기는 대기 중인 선택 전송도 취소합니다.
- 실행 중인 메인 패킷의 수정 확정 시 이전 작업을 정지한 뒤 새 BUS/주기로 다시 시작합니다. 기존 설정 파일의 주기 값은 그대로 적용됩니다. 실제 CAN 중재·드라이버·운영체제의 지연을 없애거나 실시간 성능을 보장하지는 않습니다.

## 로그 재전송 및 TX/RX 패널 표시 (소스)

- 로그 뷰어 LOG 탭의 선택/연속 전송과 일반 TX를 드라이버 송신 에코 없이도 유저 패널 수신 도구에 반영합니다. 원본 TX/RX 방향에 관계없이 실제 BUS·CAN ID에 맞는 값·Enum·조건 색상·그리드를 STANDARD/RUN에서 갱신합니다.
- 패널의 표시 갱신을 수신 스레드·메인 메시지 트리와 분리하고, 약 50ms 간격으로 메시지별 최신 프레임을 표시합니다. 오류 프레임과 실패한 송신은 표시 대상으로 삼지 않고, 연결 해제/초기화 시 대기 프레임을 비웁니다. 시퀀스 RCV 응답은 실제 RX로만 판정합니다.
- 선택 전송 직후 로그 뷰어를 닫았을 때 지연된 행 강조 해제 콜백이 삭제된 표에 접근하던 오류를 수정했습니다.

## 설정 표 색상 및 Apply 전 미리보기 (소스)

- 설정 표·색상 규칙 표의 교차 행 배경색을 없애고 흰색 배경과 옅은 구분선으로 통일했습니다. 추천색은 색상 견본과 이름으로, 나머지 색상은 HEX 값으로 표시합니다.
- 기존 도구의 우측/별도 설정창에서 색상·스타일 및 RX 라벨 규칙 변경을 즉시 미리봅니다. Apply/OK에서만 확정하고, 다른 도구 선택·편집 영역 밖 동작·모드 변경·도구 갱신·닫기에는 원래 색상으로 복원합니다. 미리보기는 저장 설정·자동 저장·실행 취소 이력을 바꾸지 않습니다.

## 수신 라벨 조건별 색상 및 설정 표 (소스)

- RX 라벨에 Enum RAW 값 및 물리값 비교/범위에 따른 배경색·글자색 규칙을 추가했습니다. 작음·이하·같음·다름·초과·이상·범위 안·범위 밖을 지원하고, 표에서 규칙별 사용 여부·우선순위·색상을 편집합니다. 추천색·색상표·사용자 지정 색상을 공통으로 사용합니다.
- 설정 화면을 탭으로 분리하고 일반 항목을 항목/설정값 표로 표시합니다. 색상 규칙은 별도 표에서 편집하여 세로 길이가 늘어나지 않게 했습니다.
- 우측 속성 영역에 **창으로 보기…**를 추가했습니다. 입력 중인 값을 별도 설정창으로 가져오며 OK에서 적용하고 Cancel은 원래 설정과 우측 입력을 유지합니다.
- 규칙은 수신/표시 테스트에 적용하고 패널·통합 설정 저장, 실행 취소·다시 실행을 지원합니다. Enum RAW와 Scale·Offset 물리값을 구별하며, 동일 신호명의 다른 CAN ID로부터 값을 덮어쓰지 않습니다.

## 그룹·탭 색상 및 클릭 색상 선택 (소스)

- `group_box`와 `tab_container`에 배경 채우기와 테두리 색상·두께·스타일·모서리 설정을 추가했습니다. 탭의 모든 내부 페이지에 적용하고, 중첩 그룹 및 하위 도구의 개별 색상은 유지합니다.
- 그룹·탭·사각형·선의 색상 입력을 추천 색상 10개, 색상표 48개 및 사용자 지정 색상 창에서 클릭해 선택하도록 변경했습니다. 배경색 선택 시 채우기를 자동으로 켭니다.
- 적용한 색상은 패널 및 통합 설정에 저장하며, 실행 취소·다시 실행으로 복원할 수 있습니다. 기존 EXE에 반영하려면 재빌드해야 합니다.

## R009

- **주기 송신 및 진단**: 등록 패킷 주기 송신을 BUS별 worker로 분리했습니다. 송신 deadline 지연과 건너뛴 주기가 있으면 시스템 로그에 BUS/ID, 누락 횟수, 최대 지연을 경고합니다. 밀린 주기를 연속 재전송하지 않습니다. 실제 CAN 하드웨어의 실시간 성능을 보장하지는 않습니다.
- **수신 동기화 counter**: DBC 신호의 raw값을 같은 BUS/CAN ID의 최신 RX payload에서 가져오는 업/다운/왕복 모드를 메인 TX와 유저패널 공통으로 추가했습니다. RX값이 바뀌면 동기화하고, 유지/미수신이면 설정한 min/max/step으로 진행합니다. CRC는 counter 반영 후 계산합니다.
- **Factor/Offset 정밀도**: 신호 선택/저장 시 소수 정밀도를 보존하고, 슬라이더와 메인 트리뷰가 DBC에 표현 가능한 값을 표시하도록 조정했습니다.
- **그래프 조작**: Reset Zoom은 최근 30초, Auto Y는 보이는 X 구간, Fit All Data는 보관 구간을 맞춥니다. 통합 그래프는 plot viewport 크기에 영향을 받지 않는 데이터 좌표로 X축을 동기화합니다.
- **패널 편집/시퀀스 UI**: Tool List, 속성 패널, 탭 추가/이름 변경은 EDIT에서만 표시합니다. INIT 영역을 splitter로 높이 조절/접기 가능하게 하고 RUN 종료 시 숨깁니다. 슬라이더 최소 높이 및 시퀀스 로그 간격/글자 크기를 개선했습니다.
- **시퀀스 제어**: 전역 Force RUN을 제거했습니다. 각 sequence의 Force는 통신 인증 후 RCV만 건너뛰며, Packet Control은 CMD/RCV 없이 START/STOP/DEL만 실행합니다.
- **시스템 로그 개행**: 반복 오류 집계 시 앞 로그 줄이 지워지거나 다음 로그와 붙던 문제를 수정했습니다.
- **Linux/연결 복원**: Linux 세션은 저장된 이전 연결 상태와 무관하게 BUS를 Close로 시작합니다. SocketCAN 설정에서 권한 거부가 발생할 때만 sudo 비밀번호 대화상자를 띄우며 비밀번호는 디스크에 저장하지 않습니다. Windows PCAN echo 중복 기록을 끄고 앱 송신을 단일 기록합니다.
- **검증 범위**: Python 자동 테스트를 수행했습니다. Linux/Windows 실 CAN 장비에서의 버스 주기·드라이버 동작은 별도 현장 검증이 필요합니다.

아래 항목은 과거 릴리스의 상세 기록입니다. R009 동작과 설명이 다르면 이 문서 상단의 R009 항목을 기준으로 합니다.

## 유저패널 주기 송신 스레드 분리

- 등록 패킷의 주기 송신을 GUI QTimer에서 BUS별 송신 스레드로 이동했습니다. 단조 시계를 기준으로 송신 시점을 계산하고, 지연된 주기를 몰아서 재전송하지 않습니다. 슬라이더 변경과 CMD·주기 송신은 잠금으로 동기화하여 최신 값→AliveCount→CRC→송신 순서를 보호합니다.
- STOP·STANDARD·패널 숨김/닫기 및 BUS 연결 해제 시 진행 중인 주기 송신이 끝날 때까지 기다린 뒤 종료합니다. 오류가 난 패킷은 재시작 전까지 중지합니다. 화면 기록은 별도 큐로 전달하며, 기록 시각은 화면 반영 시각이 아닌 송신 직후 PC 시각입니다. 따라서 지연 전달된 Tx 로그는 파일 행 순서와 타임스탬프 순서가 다를 수 있습니다. 기록 큐 상한 초과 시 송신은 유지하고 누락된 기록 수를 로그로 알립니다.
- 모의 CAN에서 GUI 이벤트 처리를 1초 중단했을 때 BUS1/2 각각 100프레임, 간격 중앙값 약 10.02/9.95ms, 최대 약 12.11/11.91ms를 관측했습니다. 같은 조건의 GUI QTimer 콜백은 0회였습니다. 이는 실제 장비 또는 하드 실시간 성능 보증이 아니며 PC/드라이버/GIL 지연은 여전히 존재합니다. 실제 ECU 시험은 외부 CAN 로그로 주기와 동작을 다시 확인해야 합니다.

## RUN INIT 명령 종류 제한

- RUN INIT은 CMD·START·STOP·DEL만 등록하고 실행합니다. 일반 시퀀스 도구의 RCV는 유지합니다.
- 기존 INIT에 저장된 RCV는 복원 시 보존하되 일반 RUN을 차단하고 로그로 알립니다. INIT 편집기에서 해당 단계를 삭제하거나 다른 종류로 변경해야 합니다. Force RUN의 기존 START/STOP 전용 동작은 유지합니다.

## TX 패킷 복제와 BUS 미선택

- TX 패킷 표에서 드래그·Shift 범위 선택 및 Ctrl 개별 선택 후 Ctrl+C/V로 여러 패킷을 한 번에 복제할 수 있습니다. 복제본은 표의 순서대로 끝에 추가되어 함께 선택되며, 각각 새 등록 ID와 BUS 미선택 상태를 갖습니다. 새 행에서 시작하는 드래그는 범위 선택, 이미 선택된 행에서 시작하는 드래그는 선택 행 묶음의 순서 변경입니다.

- 유저패널 패킷 등록창에서 Ctrl+V는 별도 창 없이 복제 행을 추가합니다. 새 등록 ID를 부여하고 BUS는 미선택으로 초기화하며, CAN ID·형식·BRS·길이·데이터·주기·노트는 복사합니다. 메인창 TX 붙여넣기도 BUS 미선택으로 복제합니다.
- 미선택 패킷은 저장·복원할 수 있으며 단발 송신과 주기 송신을 차단합니다. 미선택 복제본 여러 개가 있어도 다른 정상 패킷의 RUN은 유지됩니다. 패킷 수정에서 BUS를 지정하면 송신 대상으로 사용할 수 있습니다. 유저패널은 지정된 BUS/CAN ID 중복 등록을 계속 차단합니다.
- 기존 패킷 수정 시 BUS를 바꿔도 CAN 형식·BRS·길이·데이터를 유지합니다. INIT에서 미선택 패킷을 명시적으로 참조하면 기존 사전 검사에 따라 INIT을 차단하며, 전체 START 대상에서는 미선택 패킷을 제외합니다.

## RX 도구 표시 테스트 탭

- RX Simulator 상시 영역과 메뉴를 우측 편집 영역의 **표시 테스트** 탭으로 옮겼습니다. EDIT에서 RX 도구를 선택할 때만 속성/표시 테스트 탭이 나타납니다.
- 시험값 입력, 선택 RX 1개 적용, 자동 표시 테스트를 제공합니다. 전체 RX에 시험값을 일괄 적용하는 기능은 제거했습니다. 실제 CAN 수신이나 DBC 해석을 검증하는 기능은 아닙니다. RX 선택을 해제하거나 EDIT를 벗어나면 자동 테스트가 종료됩니다.

## 패널 클리어 및 DBC별 이력 확인

- EDIT 모드에 패킷 클리어·도구 클리어·전체 클리어 메뉴와 아이콘을 추가했습니다. 확인 후 실행하며 실행 취소가 가능합니다. 패킷 클리어는 등록 TX 패킷, INIT, 도구의 패킷 연결 및 시퀀스 명령을 지웁니다. 도구 클리어는 배치된 도구만, 전체 클리어는 패널의 패킷·도구·INIT·페이지를 초기화합니다. 메인 DBC와 BUS 연결은 유지합니다.
- 패널을 다시 열 때 BUS별 DBC/SYM 내용과 등록 순서를 비교합니다. 동일하면 이전 패널을 STANDARD로 열고, 다르면 이전 패널 사용·새 패널·취소를 선택합니다. 파일 위치나 이름만 달라진 경우는 같은 내용으로 판단합니다. 비교 정보가 없는 기존 이력은 함께 저장된 DBC를 기준으로 보완합니다.
- 새 패널 선택 시 이전 패널은 세션 저장 폴더의 `panel_history/*.upp.json`에 백업합니다. 패널 전용 시작에서 열기를 취소하면 프로그램을 종료합니다.
- 통합 설정 복원 시 저장된 CAN 장치가 없으면 해당 원인만 안내합니다. 장치를 찾지 못해 확인할 수 없는 FD 지원 여부와 속도를 별도 오류로 중복 표시하지 않습니다. 저장 설정은 유지하고 해당 BUS는 Close 상태로 둡니다.

## 패널 제목 및 하단 상태 표시

- 메인창 **보기 → 유저 패널 제목 변경…**에서 창 제목을 수정합니다. 제목은 패널 파일·패키지·자동 저장/통합 설정에 포함되며, 빈 입력은 `User Panel`로 복원합니다.
- 창 맨 아래 전체 너비의 상태 표시줄(QStatusBar)에 `BUS 1 | BUS 2 | BUS 3` 연결 상태를 왼쪽, `모드 | 송수신 상태`를 오른쪽으로 구획을 나누어 배치했습니다. 상세 오류는 시스템 로그로 확인합니다. 상단의 단축키 설명 나열을 제거했으며 단축키 기능은 유지합니다.

## 미등록 TX 도구가 있는 자동 저장 설정 복원

- TX 패킷 연결이 아직 없는 도구 때문에 프로그램 시작 시 패널 전체 복원이 실패하던 문제를 수정했습니다. 해당 패널은 STANDARD로 복원하고 시스템 로그에 등록/연결 필요 안내를 표시합니다. TX 패킷 연결을 완료하기 전까지 RUN은 차단하며, 잘못된 등록 패킷 데이터에 대한 검증은 유지합니다.

## 유저 패널 아이콘 도구 모음 (소스)

- 모드(EDIT/STANDARD/RUN/Force RUN), 통신 설정, TX 패킷 관리, INIT 설정, TX/RX/그룹·도형 생성 버튼을 한 줄의 아이콘으로 배치했습니다. 마우스를 올리면 기존 기능명을 툴팁으로 표시하며, 현재 모드는 선택 배경으로 구분합니다.
- 메인창·그래프창의 청록색/짙은 파란색 스타일에 맞춘 유저 패널 창 아이콘과 기능별 SVG 아이콘을 추가했습니다. 배포 빌드의 아이콘 리소스 폴더와 Qt SVG 의존성에 포함됩니다.

## 패널 전용 실행 · 통신 설정 · Force RUN · 시스템 로그 (소스)

- 실행 파일 옆 `PCAN_Viewer_JJ.pk`에 `"user_panel_only": true`를 설정하면 메인창을 표시하지 않고 유저 패널만 엽니다. 기본값은 false이며, true일 때 기존 `viewer_mode_only`보다 우선합니다. 창 안에서는 이 플래그를 변경할 수 없습니다. 패널 전용 모드에서 패널을 닫으면 송신·수신 처리와 버스 연결을 정리하고 프로그램을 종료합니다.
- 패널은 STANDARD로 시작합니다. 패널을 여는 것만으로 기존 버스를 연결/해제하지 않으며, 메인창의 실제 연결 상태를 공유합니다. 이전 세션의 통신·DBC·패널 설정 복원과 자동 저장은 유지됩니다. 복원된 메인 TX 패킷은 정지 상태입니다.
- **통신 설정 / DBC**에서 BUS 1·2·3의 장치 검색, 연결/해제, Nominal/Data bitrate, FD ISO 설정과 DBC/SYM 등록/해제를 수행합니다. 연결 중 설정은 잠기고, 실제 변경 전에 패널 송신을 STANDARD로 정지합니다. 창을 여는 것만으로 정지하지 않습니다.
- EDIT 비밀번호와 통신 비밀번호는 `main.py`의 `USER_PANEL_EDIT_PASSWORD`, `USER_PANEL_COMMUNICATION_PASSWORD`로 독립 관리하며 초기값은 각각 `1234`입니다. 통신 설정창을 열 때마다 인증하며, Force RUN도 매번 통신 비밀번호가 필요합니다. 속성창에서 통신 속도 변경 및 DBC 포함 패키지 불러오기에도 통신 인증을 적용합니다.
- 일반 RUN은 INIT의 CMD/RCV 및 START 대상 버스의 연결/Classic·FD 타입을 사전 검사하고 INIT을 처음부터 실행합니다. INIT에서 RCV 등록/응답 비교를 지원하며, 실패·시간 초과·실행 중 연결 해제 시 STANDARD로 복귀하고 일반 송신을 시작하지 않습니다.
- **Force RUN**은 INIT의 CMD·RCV·DEL을 생략하고 START·STOP만 원래 순서대로 적용합니다. 지정되지 않은 패킷은 기존 RUN의 기본 송신 정책을 따릅니다. INIT 성공으로 기록하지 않으며 미연결·Classic/FD 불일치 송신 차단을 유지합니다. Force는 현재 실행에만 적용되고, 이후 일반 RUN에서는 INIT을 다시 실행합니다.
- 패널 하단 **시스템 로그**에 모드/송신 정지, INIT 진행·결과, Force/인증, 통신 상태·오류 및 설정 파일 처리 결과를 표시합니다. 최대 2,000줄, 연속 반복 이벤트 묶음, 높이 조절, 전체 복사·파일 저장·지우기를 지원합니다. 비밀번호는 기록하지 않습니다.
- STANDARD는 유저 패널 송신 정지이며, 일반 모드에서 메인창이 별도로 실행한 TX까지 정지시키지는 않습니다. 실행 파일 배포에는 소스 재빌드가 필요합니다.

## 슬라이더·버튼·토글 멀티 명령 (소스)

- 등록 패킷의 실제 송신 단계에서 버스 연결 및 연결 모드와 패킷의 Classic/FD 일치를 검사합니다. 미연결·타입 불일치·드라이버 송신 오류는 해당 패킷을 차단하며, 멀티 명령의 나머지 정상 채널은 등록 순서와 관계없이 계속 전송합니다. 차단 사유는 패널 상태 영역에 표시합니다.
- 즉시 송신 실패 시 이전 패킷 값을 유지하므로, 연결/설정 복구 후 같은 값을 다시 조작하여 재시도할 수 있습니다. 실패 명령을 자동 재전송하지 않습니다. 주기 송신 오류는 해당 패킷 타이머만 정지합니다.
- EDIT에서 도구를 선택하고 Tool Properties의 **멀티 명령 → 추가**로 명령을 등록합니다. 각 명령에 별도 CAN BUS, 등록 TX 패킷, 신호/비트 및 동작 값을 지정할 수 있습니다.
- 기본 명령과 추가 명령의 체크박스로 활성 여부를 선택한 뒤 **Apply Properties**를 누릅니다. 추가 명령은 수정·삭제할 수 있으며, 설정과 체크 상태는 저장·불러오기·복사·실행 취소에 포함됩니다.
- 슬라이더는 현재 표시 값을 모든 활성 명령에 적용합니다. 범위·Resolution·Home 초기값은 기본 도구 설정을 사용합니다. 버튼은 명령별 누름/해제 값, 토글은 명령별 ON/OFF 값을 적용합니다.
- 같은 패킷의 여러 신호는 모두 반영한 뒤 전송합니다. 기존과 같이 주기 0 패킷은 값 변경 시 전송하고, 주기 패킷은 설정된 송신 주기를 따릅니다. 체크 해제는 도구의 값 적용을 제외하는 설정이며, 등록 패킷의 주기 송신을 정지하거나 기존 값을 초기화하지 않습니다.
- 기존 단일 명령 도구는 기본 명령 활성 상태로 호환됩니다.

## 속성창 편집 업데이트 (소스)

- 생성창은 유지하고 기존 도구 편집은 오른쪽 Tool Properties로 변경. 위치·크기·CAN/DBC·신호·동작 속성 및 시퀀스 상세 편집 지원.
- Ctrl/Shift 다중 선택, 혼합값 표시, 체크한 공통 속성만 일괄 적용. CAN BUS 속도는 연결 해제 상태에서 메인창 설정과 함께 변경.
- DBC 없는 패널 재로드 및 비트 직접 편집 지원. 저장된 Start Bit/Length 보존, DBC 신호 자동 매칭과 Unknown 표시.
- 일반 도구의 Motorola(big-endian) 정수 비트 인코딩/디코딩 추가. 오버랩 검사도 실제 비트 위치를 사용.
- 아래 R008 배포 당시의 little-endian 제한은 이번 소스 업데이트에서 해제됨. 실행 파일은 별도 재빌드 필요.

## R008 업데이트

버전 기준: `build_exe.py`의 `APP_VERSION = "R008"`. 소스 폴더명 `PCAN_Viewer_JJ_R007`은 유지합니다.

### 새 기능

- CMD/RCV/DEL 시퀀스 도구: 단계 삽입·삭제·순서 변경, 명령어/응답 이름, 실행/정지/처음부터 재실행.
- 메인 패킷 생성창을 재사용한 Classic/FD·BRS·CRC·주기 설정 및 반복 횟수 지원.
- DBC 없이 CMD HEX/비트 수정, RCV `X/0/1` 마스크 수정. 같은 ID의 여러 신호를 하나의 응답 조건으로 비교.
- 시퀀스를 패널 Save에 포함. 상태별 버튼 색상, 진행/오류/전체 완료 색상 로그와 로그 지우기.
- 로그 형식: `[시간] 현재순번/전체개수 종류 BUS CAN_ID 이름 · 상태`. 패킷 바이트는 출력하지 않음. Standard ID는 3자리, Extended는 8자리.
- 일반 패널 도구에도 Classic/FD 및 BRS 설정 추가. 8바이트 이하 FD 송신 지원.
- 슬라이더 Resolution에 따른 표시 자릿수, Initial Value 및 왼쪽 Home 버튼.
- 로그 뷰어 Extended 필터(최대 0x1FFFFFFF), 필터 결과만 재전송, LOG 탭 Save로 원본 행 추출 및 메시지 번호 재부여.
- 전체 UI 기본 글꼴 Consolas 적용.

### 수정 및 동작 변경

- 버튼/토글의 DBC 선택값이 설정창 종료 후 기본 1/0으로 저장되던 오류 수정.
- 같은 신호의 토글은 상호 해제하며 자동 해제 시 OFF 값은 송신하지 않음.
- 격자 최소 32×32 유지, 작은 창에서는 스크롤, 큰 창에서는 격자·도구 확대.
- 일반 도구 최소 Row Span 3 → 2. Home 버튼은 왼쪽에서 슬라이더와 값 표시의 두 행을 차지.
- 패널 닫기·모드 변경 시 패널의 모든 송신 및 시퀀스 대기를 중지. 자동 재개하지 않음.

### 검증 및 배포

- Python 3.13 환경의 자동 테스트 16개 통과. 실제 CAN 장비 검증 및 R008 바이너리 검증은 별도 수행 필요.
- `python build_exe.py` 실행 후 Windows는 `dist/PCAN_Viewer_JJ_R008_win/PCAN_Viewer_JJ_R008_win.exe` 사용.
- Linux는 `dist/PCAN_Viewer_JJ_R008_linux/PCAN_Viewer_JJ_R008_linux` 사용.
- 일반 패널 fallback 인코딩의 little-endian 제한은 유지. 시퀀스는 저장된 바이트/마스크로 실행.

---

## R007 이전 기록

## Version Scope
- Project: PCAN_Viewer_JJ_R007
- Area: User Panel (v2 modular architecture)
- Date: 2026-07-30

## What Was Added
- Mode state machine: EDIT / STANDBY / RUN.
- Edit mode password gate integration from main configuration.
- TX/RX tool split creation flow.
- Group/Tab parent-child composition.
- Shape tools and draw mode (rect/line).
- Drag move + bottom-right drag resize.
- Numeric geometry editor (row/col/row_span/col_span).
- Z-order controls (front/back/forward/backward).
- TX overlap diagnostics with list highlighting and conflict focus navigation.
- Per-frame TX staging/flush policy and cycle mode support.
- Package persistence: .pjjupkg with panel JSON + DB bundle.
- CAN-free RX simulator (manual apply + auto simulation).

## Behavior Changes
- TX emission path now stages values by frame and flushes with policy-aware timers.
- Widgets using same frame can operate together without immediate overwrite.
- Edit operations are restricted by mode and optional password policy.

## Compatibility
- Existing import path compatibility preserved through wrapper module.
- Existing main window DB handling remains compatible with package load replacement flow.

## Operational Notes
- Use STANDBY for validation without TX output.
- Use RUN for full TX/RX runtime.
- Run overlap check after adding/editing any TX mapping.

## Known Limitations
- Fallback bit-packing path currently supports little-endian only.
- Big-endian fallback encoding is not yet implemented.
- Final acceptance with real CAN hardware is still required.

## Recommended Validation Path
1. Execute USER_PANEL_ACCEPTANCE_CHECKLIST.md.
2. Record results in USER_PANEL_TEST_REPORT_TEMPLATE.md.
3. Confirm no critical FAIL/BLOCKED before release.
