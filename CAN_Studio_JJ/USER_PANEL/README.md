# 유저 패널 파일

프로그램의 **File → Save Panel / Load Panel**은 `.pjjupkg` 형식만 사용합니다.
패키지 하나에 패널 설정과 BUS별 DBC/SYM이 함께 들어 있으므로, 다른 PC에는 이 파일 하나를 옮기면 됩니다.

- [VCU_CAN1_Current.pjjupkg](VCU_CAN1_Current.pjjupkg): 최근 저장한 패널 설정을 변환한 파일입니다. **SYSTEM / DRIVE** 탭과 수정된 도구 배치를 그대로 포함합니다.
- [VCU_CAN1_RX_3Tabs.pjjupkg](VCU_CAN1_RX_3Tabs.pjjupkg): 최초 생성한 전체 페이지 Tab1~3, 13개 CAN ID, 75개 RX 도구 구성입니다.

패키지에는 `dbc/BUS1/HL_Mando_V2_VCU_CAN1_Monitoring.sym`이 포함되어 있습니다.
불러올 때 현재 패널과 DB 목록이 패키지 내용으로 교체됩니다. CAN 장치 연결과 속도는 사용 환경에 맞게 설정합니다.

## 조향모터 (AQMD6008NS-TBE, CANopen)

- [Steering_AQMD_CANopen.pjjupkg](Steering_AQMD_CANopen.pjjupkg): BUS 1, 500 kbps, DBC 없음(raw 직접 입력). 노드 ID FL=1, FR=2, RL=3, RR=4.
  - **1회 설정** 페이지: 출하 ID 1 → 노드별 ID 설정·저장, 저장 재시도, 스트로크 측정
  - **FL/FR/RL/RR** 페이지: 부팅 초기화 + PDO 설정 → Operational, 목표 위치/속도/가감속 입력, 이동·정지·비상정지, 원점, 스트로크·위치완료 읽기, TPDO 상태 표시
- [build_steering_panel.py](build_steering_panel.py): 위 패키지 생성 스크립트입니다. 노드 ID·BUS는 파일 상단 `NODES`, `BUS`를 바꾸고 다시 실행합니다.

`build_vcu_can1_rx_3tabs.py`, `build_mando_panel.py`는 패널 생성·검증용 개발 스크립트입니다. 프로그램 실행이나 완성된 패키지 불러오기에는 필요하지 않으므로 삭제해도 됩니다. 스크립트를 다시 실행하면 최초 생성 배치를 만들기 때문에 현재 편집본을 유지하려면 위의 Current 패키지를 사용합니다.
