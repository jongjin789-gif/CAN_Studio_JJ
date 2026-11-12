# User Panel Command Sequence (R009)

Enter step names in the **Command / Response Name** column located between the Create/Edit buttons and the command viewer. Names are also saved with the panel Save.

In EDIT mode, select **Add TX Tool → Widget Type: sequence → Command Sequence Settings (명령 시퀀스 설정)**.
Because the button and the log are shown together, a larger area than a normal button is recommended (e.g. Row Span 8, Col Span 12).

The settings window starts with a single CMD row. Create inserts below the selected row; use Delete and Up/Down buttons to edit the order.

To the right of the Run button are the **Force Run (강제 실행)** and **Packet Control (패킷 제어)** buttons. Force Run skips only RCV and executes CMD/DEL/START/STOP. Packet Control skips CMD/RCV and executes only START/STOP/DEL. Both buttons work in RUN mode and do not require communication authentication (password). Communication authentication is required only for changing communication settings and loading packages. There is no Force RUN option in the global mode selection.

- **CMD:** Uses the same DBC/HEX input, Classic/FD, BRS, length, CRC, cycle, and Note settings as the main packet creation window. Extended IDs can also be specified directly. Repeat defaults to 1; when sending multiple times, Cycle Time is used as the send interval. After the last transmission, it moves to the next step. Hyundai_CRC calculates AliveCount and CRC at run time, and the count restarts from the beginning on each new run.
- **RCV:** In the same editor window, check the box to the left of each signal name to compare and set the expected value and timeout (ms). All selected bits must match in a single frame for success. Bus, ID, Standard/Extended, Classic/FD, BRS, and data length must also match. RCV does not recalculate CRC; it compares the stored expected bits. Changing CRC/AliveCount fields can be excluded from comparison.
- **DEL:** Waits 0–600000 ms.
- If you choose **Sync with RX value + Up/Down/Round-trip** in a TX packet's counter settings, the latest RX DBC raw value of the same BUS/CAN ID is used. When the value changes, that value is sent; when the value stays the same or has not been received yet, it increments/decrements in the selected direction according to min/max/step. CRC is recalculated after the counter is applied.

Run data is stored as a byte array and an RCV comparison mask, so it works even without the main DBC when the panel file is reloaded. CMD can be edited as HEX or `0/1` bits. RCV is edited with the **View / Edit Bit Conditions (비트 조건 보기 / 편집)** button in the `[0]: XXXX XX01` format. The leftmost character is bit 7, and `X` means excluded from comparison. Other packet settings and timeouts are edited in **Create / Edit Packet (패킷 생성 / 편집)**.

In RUN mode, press the button to start and press it again to stop. A rerun always starts from the first step. On send failure, disconnection, or response timeout, an error is logged and the sequence stops. A response received right after a CMD is checked by the immediately following RCV; responses from a previous run or already-processed responses are not reused for the next RCV.

Button colors: blue before run, teal for CMD, orange for RCV, purple for DEL, gray when stopped, green when completed, red on error. Log colors: black for normal progress, red for errors, green for overall completion. The log can be cleared from the right-click menu, and the most recent 2000 lines are kept.

Instead of packet bytes, the log shows `[time] current step/total type BUS CAN_ID name · status`. If the name is left empty, it is shown as `이름 없음` (No name). Standard IDs are shown with 3 digits like `0x001` and Extended IDs with 8 digits like `0x00000001`; the command viewer uses the same rule.

```text
[10:30:00.001] 1/3 CMD BUS_1 0x001 Init 명령 · 송신 완료
[10:30:00.125] 2/3 RCV BUS_1 0x00000001 Init 응답 · 응답 조건 일치
[10:30:00.126] 3/3 DEL - - 안정화 대기 · 200 ms 대기
```

(Example meaning: `Init command · Sent`, `Init response · Response condition matched`, `Stabilization wait · Waiting 200 ms`.)

Closing the panel or changing the mode stops all sequences, waits, press-and-hold repeated transmissions, and periodic transmissions of the panel. They do not resume automatically when returning to RUN. Commands already sent are not reverted. Separate transmissions from the main TX panel are not included in this stop scope. The RUN Init log panel can be resized or collapsed with a splitter and is hidden when leaving RUN.

If a periodic transmission worker misses a deadline, a `TX 주기 지연` (TX cycle delay) warning is written to the system log with the BUS/CAN ID, the number of misses, and the maximum delay. It does not send a burst of frames to catch up on delayed cycles. In the sequence and system logs, repeated errors occurring within a short time are merged into fewer lines, and the sequence tool log font is reduced to save space.

Multiple sequences and existing panel transmissions run independently. Configurations that manipulate the same signal at the same time must be coordinated by the user.
