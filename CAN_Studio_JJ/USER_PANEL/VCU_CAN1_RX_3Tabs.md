# VCU CAN1 수신 패널 — 전체 페이지 3개

원본: `dbc/BUS1/HL_Mando_V2_VCU_CAN1_Monitoring.sym`의 현재 내용.

| 전체 페이지 | CAN ID | 수신 신호 수 |
|---|---|---:|
| Tab1 | 0x130, 0x131 | 29 |
| Tab2 | 0x132, 0x133, 0x134, 0x135, 0x136, 0x137, 0x138 | 32 |
| Tab3 | 0x13A, 0x13B, 0x13C, 0x13D | 14 |

총 13개 CAN ID, 75개 RX 표시 도구입니다. 0x139는 원본에 정의되어 있지 않습니다.

## 불러오기

유저 패널의 **File → Load Panel**에서 [VCU_CAN1_RX_3Tabs.pjjupkg](VCU_CAN1_RX_3Tabs.pjjupkg)를 선택합니다. 패키지에는 BUS1의 원본 SYM과 패널 구성이 들어 있으며, 패키지 로드는 현재 패널 및 DB 목록을 패키지 내용으로 교체합니다. 통신 속도와 장치 연결은 사용 환경에 맞게 설정합니다.

이 문서는 최초 3탭 생성본에 관한 설명입니다. 이후 SYSTEM / DRIVE 탭과 배치를 수정해 저장한 최신 구성은 [VCU_CAN1_Current.pjjupkg](VCU_CAN1_Current.pjjupkg)에 별도로 보존했습니다.

## 배치와 표시

- 전체 페이지 탭 이름은 `Tab1`, `Tab2`, `Tab3`입니다.
- CAN ID별 사각 패널로 구역을 구분했습니다. 위에서 아래로, 왼쪽에서 오른쪽으로 CAN ID가 증가합니다.
- RX 표시 도구는 기본 **3행 × 4열**, 실제 **64 × 48px**입니다. 사각 패널 안의 위치에 페이지 도구로 직접 배치해, 그룹 내부 격자에서 도구가 작아지지 않도록 했습니다.
- 제목은 `DRV1`~`DRV4`, `STR1`~`STR4`, `X/Y/Z`처럼 짧게 표시합니다. 원래 신호 이름과 비트 위치는 각 도구 속성의 바인딩에 보존되어 있습니다.
- 상태 신호도 원본 숫자로 표시합니다. ENUM 의미는 원본 SYM을 기준으로 해석합니다. 예: 통신 상태 `0=OFF`, `1=ON`, `2=Error_Func`; 조향 잠금 `0=Unlock`, `1=Lock`.
- 스케일·부호·오프셋·범위·단위는 원본을 유지했습니다. 특히 0x135의 CURR 신호는 SYM에 단위가 `km/h`로 지정되어 있어 그대로 사용했습니다. `°/s`, `℃`는 원본의 유니코드 단위를 보존했습니다.
- 저장 모드는 STANDARD/대기이며 TX 도구·송신 등록 패킷은 없습니다.

## 미리보기와 검증

미리보기에는 검증용 모의 수신값을 표시했습니다.

- [Tab1 미리보기](VCU_CAN1_RX_3Tabs_Tab1.png)
- [Tab2 미리보기](VCU_CAN1_RX_3Tabs_Tab2.png)
- [Tab3 미리보기](VCU_CAN1_RX_3Tabs_Tab3.png)

실제 패널 로딩, 75개 신호 바인딩, 기본 크기, 겹침 없는 배치, 탭별 표시 전환, 13개 프레임의 모의 수신 디코딩, 저장 필드 유지 및 패키지 내 SYM 일치를 확인했습니다.

재생성 및 검증:

```powershell
.venv/Scripts/python.exe USER_PANEL/build_vcu_can1_rx_3tabs.py
```
