# VCU CAN1 RX 가독성 색상 설정

현재 패널의 구역 13개와 RX 라벨 75개에 적용했습니다. 배치, 도구 ID, 신호/비트 연결, DBC/SYM 원문, CAN/TX 설정은 유지합니다. 색상과 도형의 표시 종류만 변경했습니다.

- 메인창 **통합 불러오기**: `VCU_CAN1_Readable.pjjsettings`
- 유저 패널 **불러오기**: `VCU_CAN1_Readable.pjjupkg`
- 현재 자동 복원 파일 `../session.pjjsettings`와 `VCU_CAN1_Current.pjjupkg`에도 같은 구성을 저장했습니다.
- 변경 전 원본은 `backups/VCU_CAN1_before_colors_20261023.*`에 보관했습니다.

| 구역 | 배경 |
|---|---|
| Communication | 회색 |
| Control / Mode | 연한 파랑 |
| Drive Torque / RPM / Speed / Current | 하늘색 |
| Steer Angle / Steer Lock | 청록색 |
| EPB / Brake | 연한 주황 |
| Battery | 연한 초록 |
| IMU Acceleration / Angular Velocity / Angle | 연한 보라 |

| 상태 | 표시 |
|---|---|
| 통신 ON | 연한 초록 / 짙은 초록 글자 |
| 통신 OFF, 제어 None, EPB Disabled | 회색 / 짙은 회색 글자 |
| Error_Func, EMS 활성 | 연한 빨강 / 짙은 빨강 글자 |
| Sync | 연한 초록 |
| Unlock / Lock | 하늘색 / 노란색 |
| EPB Enabled / Enabling·Disabling | 파란색 / 노란색 |
| 제어 주체·모드 | 파랑·청록·보라로 구별 |
| Gear P / R / N / D | 파랑 / 주황 / 회색 / 초록 |
| 정의되지 않은 Enum RAW | 노란색 / 짙은 황갈색 글자 |
| 일반 수치 라벨 | 흰색 / 짙은 남색 글자 |

Enum 라벨 36개, 일반 수치 라벨 39개에 배경·글자색 규칙 368개를 등록했습니다. Enum은 SYM에 정의된 RAW 값으로 비교합니다. 수치 라벨의 범위는 인코딩 가능한 물리값 전체이며 운전/경보 임계값을 추가한 것이 아닙니다.

실제 화면에서 Enum 값 109개, 미정의 값 25개, 수치 라벨 39개를 모의 입력하여 색상/텍스트를 확인했습니다. 설정/패키지 재읽기와 포함된 DB 원문 일치도 확인했습니다.

`VCU_CAN1_Readable_preview.png`는 **모의 값으로 표시한 색상 예시**입니다. 실제 수신값은 저장하지 않았습니다.
