# User Panel Release Notes

## R013

- **Modular CAN devices**: PCAN-Basic and SocketCAN connection code was moved out of the main window into backends under `src/can_devices/`. A per-BUS **CAN Type (CAN 방식)** column was added to the connection settings grid, and the selected type is saved per BUS in the unified settings. Older settings files determine the type from the saved device kind. The type cannot be changed while connected.
- **Kvaser support (Windows)**: Uses python-can's CANlib binding with no extra Python package. Supports Classic CAN and CAN FD (ISO/Non-ISO); if a channel does not support the selected FD mode, you are notified before connecting. For 20k/800k bitrates, which have no CANlib preset, bit timing is specified directly. The receive thread does not receive TX echoes.
- **Channel identification**: Kvaser channels are identified by S/N and the device-internal channel number instead of the CANlib channel number. The same criterion is used for Refresh, session restore, and duplicate-device checks across BUSes. If a channel's configuration changed after Refresh, the connection is rejected and you are prompted to reselect.
- **Driver not installed / no device**: Other CAN types work normally even without the Kvaser driver. The channel list shows an install hint or a device-check hint.
- **Program renamed**: PCAN Viewer JJ → **CAN Studio JJ**. The title bar, program info, log file headers, and executable name (`CAN_Studio_JJ_R013_win.exe`) were changed. The run flag file becomes `CAN_Studio_JJ.pk`; if it does not exist, the existing `PCAN_Viewer_JJ.pk` is copied and used. File extensions are unchanged.
- **Bus load and error display**: The State cell shows load (estimated), fps, and error frame count every second. For Kvaser, `canReadStatus` detects Bus-Off (disconnect) and Error Warning/Passive (warning). The USBcan Light 4xHS reports error counters as 0 even in Error Passive, so TEC/REC are not shown.
- **Vector ASC/BLF**: The log viewer supports opening these files and saving filter results (in the same format), and Record supports choosing the save format (TRC/ASC/BLF). python-can readers/writers are used, with channels 1–4 mapped to BUS 1–4.
- **Trigger recording**: A Trigger column was added to the receive section. When a DBC signal comparison condition goes from false to true, all BUS frames from 1–2 seconds before and after are saved to `Log/Trigger_*.trc`. Only one trigger can be registered; a re-trigger during saving restarts the post-trigger timer (max 60 seconds per file).
- **BUS gateway**: Forwards, blocks, or ID-translates received frames to another BUS. Frames are forwarded directly from the receive thread using the existing per-BUS TX lock. Only received frames are forwarded to prevent loops, and the gateway is always off at program start.
- **Signal statistics and RX timeout**: The Stats window shows min, max, average, and sample count of RX signals. If nothing is received for the DBC cycle (or measured cycle) × multiplier, this is reported via red text in the receive section, the status bar, and the BUS log; no other action is taken.
- **Error frame diagnostics**: Error frames are only counted in the receive thread and are not sent to display processing, so program load does not increase even when thousands per second occur (e.g. no ACK). Every second the cause (PCAN: error type, direction, ECC, TEC/REC interpretation) is written to the BUS log, and main TX cycle delays are recorded only in the BUS log instead of State.
- **Device removal handling**: If a PCAN is unplugged while connected, the status check (hardware/handle error, channel state UNAVAILABLE) is confirmed twice in a row before automatically disconnecting. Device removal, receive errors, and disconnect errors are recorded only in State and the BUS detail log (…) without an error dialog.
- **Record ID filter**: In the Record window you can specify an ID range to either exclude IDs in the range or record only IDs in the range. Filtering at record time reduces file size, and the change history is kept in the TRC header.
- **CSV export**: The log viewer saves decoded signals as a time-axis CSV (sample points only / fill with previous value).
- **Stability**: When a BUS is disconnected, the stopped receive thread object is kept briefly, preventing a crash that could occur if the object was deleted before a just-emitted signal was processed.
- Gateway, trigger, and timeout settings are saved in the `tools` entry of the unified settings. Older versions ignore this entry, so file compatibility is preserved.
- Actual Kvaser hardware TX/RX must be verified separately in an environment with devices connected. Automated tests mock CANlib or use the python-can virtual BUS.

## Main TX and User Panel Cyclic TX Protection (source)

- Main TX's GUI-timer transmission was converted to per-BUS background transmission, sharing a common scheduler with the user panel. CRC/counter calculation and actual transmission are separated from display/record handling. The Windows timer resolution request is held only while work is in progress.
- After a delay, transmission returns to integer cycles of the original target time to reduce accumulated error, and backlogged frames are not sent in a burst. Main TX misses/max delay are also shown in the BUS log. A failed Hyundai CRC transmission does not advance the Alive counter.
- Removed the behavior that stopped main TX when log playback started. BUS writes are serialized, and if cyclic TX is in progress or close to its scheduled time, log selected/continuous send retries while keeping the packet. Stop All, disconnect, and closing the log window also cancel pending selected sends.
- When an edit to a running main packet is confirmed, the previous job is stopped and restarted with the new BUS/cycle. Cycle values from existing settings files are applied as-is. This does not eliminate delays from actual CAN arbitration, drivers, or the OS, nor guarantee real-time performance.

## Log Retransmission and TX/RX Panel Display (source)

- Selected/continuous send from the log viewer LOG tab and normal TX are reflected in user panel receive tools even without driver TX echo. Regardless of the original TX/RX direction, values, Enums, condition colors, and grids matching the actual BUS and CAN ID are updated in STANDARD/RUN.
- Panel display updates are separated from the receive thread and main message tree, showing the latest frame per message at about 50 ms intervals. Error frames and failed transmissions are not displayed, and pending frames are cleared on disconnect/reset. Sequence RCV responses are judged only from actual RX.
- Fixed an error where, if the log viewer was closed right after a selected send, a delayed row-highlight-clear callback accessed the deleted table.

## Settings Table Colors and Pre-Apply Preview (source)

- Removed alternating row background colors in the settings table and color rule table, unifying them with a white background and faint grid lines. Recommended colors are shown with a swatch and name; other colors are shown as HEX values.
- Color/style and RX label rule changes in an existing tool's right-side or separate settings window are previewed immediately. They are committed only on Apply/OK, and the original colors are restored on selecting another tool, actions outside the edit area, mode change, tool refresh, or close. Preview does not change saved settings, auto-save, or undo history.

## Conditional Colors for RX Labels and Settings Table (source)

- Added background/text color rules for RX labels based on Enum RAW values and physical value comparisons/ranges. Supports less than, less or equal, equal, not equal, greater than, greater or equal, in range, and out of range; the table lets you edit each rule's enabled state, priority, and colors. Recommended colors, the color palette, and custom colors are shared.
- The settings screen was split into tabs, and general items are shown in an Item/Value table. Color rules are edited in a separate table so the panel does not grow taller.
- Added **Open in Window… (창으로 보기…)** to the right-side properties area. It carries the values being entered into a separate settings window; OK applies them, and Cancel keeps the original settings and the right-side input.
- Rules apply to receive/display tests and support saving to the panel and unified settings, plus undo/redo. Enum RAW and Scale/Offset physical values are distinguished, and values from a different CAN ID with the same signal name do not overwrite each other.

## Group/Tab Colors and Click-to-Pick Colors (source)

- Added background fill and border color, width, style, and corner settings to `group_box` and `tab_container`. These apply to all inner pages of a tab, while individual colors of nested groups and child tools are kept.
- Color input for groups, tabs, rectangles, and lines now uses clicking in 10 recommended colors, a 48-color palette, and a custom color dialog. Choosing a background color automatically enables fill.
- Applied colors are saved in the panel and unified settings and can be restored with undo/redo. A rebuild is required to include this in the existing EXE.

## R009

- **Cyclic TX and diagnostics**: Cyclic transmission of registered packets was moved to per-BUS workers. If TX deadlines are late or cycles are skipped, the system log warns with BUS/ID, miss count, and max delay. Backlogged cycles are not retransmitted consecutively. Real-time performance on actual CAN hardware is not guaranteed.
- **RX-synchronized counter**: Added up/down/ping-pong modes, shared by main TX and the user panel, that take a DBC signal's raw value from the latest RX payload of the same BUS/CAN ID. It synchronizes when the RX value changes, and advances by the configured min/max/step when the value is unchanged or not received. CRC is calculated after the counter is applied.
- **Factor/Offset precision**: Decimal precision is preserved when selecting/saving signals, and the slider and main tree view were adjusted to show values representable in the DBC.
- **Graph controls**: Reset Zoom fits the last 30 seconds, Auto Y fits the visible X range, and Fit All Data fits the retained range. The combined graph synchronizes the X axis using data coordinates unaffected by plot viewport size.
- **Panel editing / sequence UI**: Tool List, the properties panel, and tab add/rename are shown only in EDIT. The INIT area can be resized/collapsed with a splitter and is hidden when RUN ends. Improved slider minimum height and sequence log spacing/font size.
- **Sequence control**: Removed global Force RUN. Each sequence's Force skips only RCV after communication authentication, and Packet Control runs only START/STOP/DEL without CMD/RCV.
- **System log line breaks**: Fixed an issue where, when aggregating repeated errors, the previous log line was erased or merged with the next log line.
- **Linux / connection restore**: Linux sessions start with BUSes Closed regardless of the previously saved connection state. A sudo password dialog appears only when SocketCAN setup gets permission denied, and the password is never stored on disk. Windows PCAN echo duplicate recording was turned off, so app transmissions are recorded once.
- **Verification scope**: Python automated tests were run. Bus cycles and driver behavior on real Linux/Windows CAN devices require separate field verification.

The entries below are detailed records of past releases. Where they differ from R009 behavior or descriptions, the R009 entry at the top of this document takes precedence.

## User Panel Cyclic TX Thread Separation

- Cyclic transmission of registered packets was moved from the GUI QTimer to per-BUS TX threads. TX times are calculated from a monotonic clock, and delayed cycles are not retransmitted in a burst. Slider changes and CMD/cyclic TX are synchronized with a lock to protect the latest value → AliveCount → CRC → transmit order.
- On STOP, STANDARD, panel hide/close, and BUS disconnect, the program waits for in-progress cyclic TX to finish before stopping. A packet that errors stays stopped until restarted. Display records are passed through a separate queue, and the record time is the PC time right after transmission, not the time shown on screen. Therefore, for Tx logs delivered late, file row order may differ from timestamp order. If the record queue limit is exceeded, transmission continues and the number of dropped records is reported in the log.
- With mock CAN and GUI event processing paused for 1 second, BUS1/2 each sent 100 frames, with median intervals of about 10.02/9.95 ms and maximums of about 12.11/11.91 ms. GUI QTimer callbacks under the same conditions: 0. This is not a guarantee of real-device or hard real-time performance; PC/driver/GIL delays still exist. For actual ECU testing, re-check cycles and behavior with an external CAN log.

## RUN INIT Command Type Restriction

- RUN INIT registers and executes only CMD, START, STOP, and DEL. RCV in regular sequence tools is kept.
- RCV steps saved in existing INITs are preserved on restore, but regular RUN is blocked and a log message is shown. Delete the step or change it to another type in the INIT editor. Force RUN's existing START/STOP-only behavior is kept.

## TX Packet Duplication and Unassigned BUS

- In the TX packet table, after drag/Shift range selection and Ctrl individual selection, you can duplicate multiple packets at once with Ctrl+C/V. Copies are appended at the end in table order and selected together, each with a new registration ID and no BUS assigned. A drag starting on a new row is range selection; a drag starting on an already-selected row reorders the selected rows as a group.

- In the user panel packet registration window, Ctrl+V adds duplicate rows without a separate dialog. A new registration ID is assigned and BUS is reset to unassigned, while CAN ID, format, BRS, length, data, cycle, and note are copied. Pasting in the main window TX also duplicates with BUS unassigned.
- Unassigned packets can be saved and restored, and single-shot and cyclic transmission are blocked for them. Even with multiple unassigned copies, RUN for other valid packets continues. Assigning a BUS in packet edit makes the packet usable for transmission. The user panel still blocks duplicate registration of the same BUS/CAN ID.
- When editing an existing packet, changing the BUS keeps CAN format, BRS, length, and data. If INIT explicitly references an unassigned packet, INIT is blocked by the existing pre-check, and unassigned packets are excluded from Start All targets.

## RX Tool Display Test Tab

- The always-visible RX Simulator area and menu were moved to the **Display Test (표시 테스트)** tab in the right-side edit area. The Properties/Display Test tabs appear only when an RX tool is selected in EDIT.
- Provides test value input, applying to one selected RX, and automatic display test. The feature to apply test values to all RX tools at once was removed. This does not verify actual CAN reception or DBC decoding. Automatic testing ends when the RX tool is deselected or you leave EDIT.

## Panel Clear and Per-DBC History Check

- Added Clear Packets, Clear Tools, and Clear All menus and icons in EDIT mode. They run after confirmation and can be undone. Clear Packets removes registered TX packets, INIT, tool packet links, and sequence commands. Clear Tools removes only placed tools, and Clear All resets the panel's packets, tools, INIT, and pages. The main DBC and BUS connections are kept.
- When the panel is reopened, per-BUS DBC/SYM content and registration order are compared. If identical, the previous panel opens in STANDARD; if different, you choose Use Previous Panel, New Panel, or Cancel. If only the file location or name changed, the content is considered the same. Older history without comparison info is supplemented based on the DBC saved with it.
- When New Panel is chosen, the previous panel is backed up to `panel_history/*.upp.json` in the session save folder. In panel-only startup, canceling the open dialog exits the program.
- When restoring unified settings, if the saved CAN device is not present, only that cause is reported. FD support and bitrate that cannot be checked because the device was not found are not shown again as separate errors. Saved settings are kept and the affected BUS is left Closed.

## Panel Title and Bottom Status Display

- Edit the window title via **View → Rename User Panel… (보기 → 유저 패널 제목 변경…)** in the main window. The title is included in the panel file, package, and auto-save/unified settings; empty input restores `User Panel`.
- The full-width status bar (QStatusBar) at the very bottom of the window is divided into sections, with `BUS 1 | BUS 2 | BUS 3` connection status on the left and Mode | TX/RX status (`모드 | 송수신 상태`) on the right. Check detailed errors in the system log. The list of shortcut descriptions at the top was removed; shortcut functions remain.

## Restoring Auto-Saved Settings with Unregistered TX Tools

- Fixed an issue where restoring the whole panel at program start failed because of tools without a TX packet link yet. Such a panel is restored in STANDARD, and the system log shows a notice that registration/linking is needed. RUN is blocked until the TX packet links are completed, and validation of invalid registered packet data is kept.

## User Panel Icon Toolbar (source)

- Mode (EDIT/STANDARD/RUN/Force RUN), communication settings, TX packet management, INIT settings, and TX/RX/group/shape creation buttons are arranged as icons in a single row. Hovering shows the existing function name as a tooltip, and the current mode is indicated by a selected background.
- Added a user panel window icon and per-function SVG icons matching the teal/dark blue style of the main and graph windows. They are included in the icon resource folder and the Qt SVG dependency of the release build.

## Panel-Only Mode / Communication Settings / Force RUN / System Log (source)

- Setting `"user_panel_only": true` in `PCAN_Viewer_JJ.pk` (`CAN_Studio_JJ.pk` from R013) next to the executable opens only the user panel without showing the main window. The default is false, and when true it takes precedence over the existing `viewer_mode_only`. This flag cannot be changed from within the window. In panel-only mode, closing the panel cleans up TX/RX processing and bus connections and exits the program.
- The panel starts in STANDARD. Opening the panel by itself does not connect/disconnect existing buses; it shares the main window's actual connection state. Restoring the previous session's communication, DBC, and panel settings and auto-save are kept. Restored main TX packets are stopped.
- In **Communication Settings / DBC (통신 설정 / DBC)**, you can search devices, connect/disconnect, set Nominal/Data bitrate and FD ISO, and register/unregister DBC/SYM for BUS 1, 2, and 3. Settings are locked while connected, and panel TX is stopped to STANDARD before an actual change. Merely opening the window does not stop it.
- The EDIT password and communication password are managed independently in `main.py` as `USER_PANEL_EDIT_PASSWORD` and `USER_PANEL_COMMUNICATION_PASSWORD`, both initially `1234`. Authentication is required every time the communication settings window is opened, and Force RUN also requires the communication password every time. Communication authentication also applies to changing bitrate in the properties window and loading packages that include a DBC.
- Regular RUN pre-checks INIT's CMD/RCV and the connection/Classic-FD type of START target buses, then runs INIT from the beginning. INIT supports RCV registration/response comparison; on failure, timeout, or disconnect during execution, it returns to STANDARD and does not start normal transmission.
- **Force RUN** skips INIT's CMD, RCV, and DEL and applies only START and STOP in their original order. Packets not specified follow RUN's existing default transmission policy. It is not recorded as INIT success, and blocking of transmission for unconnected buses or Classic/FD mismatch is kept. Force applies only to the current run; subsequent regular RUNs execute INIT again.
- The **System Log (시스템 로그)** at the bottom of the panel shows mode/TX stop, INIT progress and results, Force/authentication, communication status/errors, and settings file processing results. Supports up to 2,000 lines, grouping of consecutive repeated events, height adjustment, copy all, save to file, and clear. Passwords are never logged.
- STANDARD stops user panel transmission; in normal mode it does not stop TX started separately from the main window. Distributing the executable requires rebuilding from source.

## Slider/Button/Toggle Multi-Command (source)

- At the actual transmission step of a registered packet, bus connection and connection mode are checked against the packet's Classic/FD type. Unconnected, type-mismatched, or driver TX errors block that packet, while the remaining valid channels of a multi-command continue transmitting regardless of registration order. The block reason is shown in the panel status area.
- On immediate TX failure the previous packet value is kept, so after restoring the connection/settings you can retry by operating the same value again. Failed commands are not retransmitted automatically. A cyclic TX error stops only that packet's timer.
- In EDIT, select a tool and register commands via **Multi-Command → Add (멀티 명령 → 추가)** in Tool Properties. Each command can have its own CAN BUS, registered TX packet, signal/bits, and action values.
- Select whether the base command and additional commands are active using their checkboxes, then press **Apply Properties**. Additional commands can be edited and deleted, and their settings and check states are included in save, load, copy, and undo.
- A slider applies its current displayed value to all active commands. Range, Resolution, and Home initial value use the base tool settings. A button applies each command's press/release values, and a toggle applies each command's ON/OFF values.
- Multiple signals of the same packet are all applied before transmission. As before, packets with cycle 0 are sent on value change, and cyclic packets follow the configured TX cycle. Unchecking only excludes the tool's value application; it does not stop cyclic transmission of the registered packet or reset existing values.
- Existing single-command tools remain compatible with the base command active.

## Properties Window Editing Update (source)

- Creation dialog kept; editing existing tools moved to the right-side Tool Properties. Supports editing position, size, CAN/DBC, signal, and action properties, plus detailed sequence editing.
- Ctrl/Shift multi-select, mixed-value display, and batch apply of only checked common properties. CAN BUS bitrate is changed together with the main window settings while disconnected.
- Supports reloading panels without a DBC and editing bits directly. Saved Start Bit/Length are preserved, with automatic DBC signal matching and Unknown display.
- Added Motorola (big-endian) integer bit encoding/decoding for general tools. Overlap checks also use actual bit positions.
- The little-endian limitation at the time of the R008 release below is lifted in this source update. The executable must be rebuilt separately.

## R008 Update

Version reference: `APP_VERSION = "R008"` in `build_exe.py`. The source folder name `PCAN_Viewer_JJ_R007` is kept.

### New Features

- CMD/RCV/DEL sequence tool: insert/delete/reorder steps, command/response names, run/stop/restart from the beginning.
- Classic/FD, BRS, CRC, and cycle settings reusing the main packet creation dialog, plus repeat count support.
- Edit CMD HEX/bits and RCV `X/0/1` masks without a DBC. Multiple signals of the same ID are compared as a single response condition.
- Sequences included in panel Save. Per-state button colors, color-coded progress/error/all-complete log, and log clear.
- Log format: `[시간] 현재순번/전체개수 종류 BUS CAN_ID 이름 · 상태` ([time] current step/total type BUS CAN_ID name · status). Packet bytes are not printed. Standard IDs use 3 digits, Extended IDs 8 digits.
- Classic/FD and BRS settings added to general panel tools as well. Supports FD transmission of 8 bytes or less.
- Slider display digits based on Resolution, Initial Value, and a Home button on the left.
- Log viewer Extended filter (up to 0x1FFFFFFF), retransmit filter results only, and LOG tab Save to extract original rows and renumber messages.
- Default font Consolas applied to the whole UI.

### Fixes and Behavior Changes

- Fixed an error where button/toggle DBC selected values were saved as the default 1/0 after closing the settings dialog.
- Toggles on the same signal mutually release each other, and the OFF value is not sent on automatic release.
- Grid kept at a minimum of 32×32; small windows scroll, large windows enlarge the grid and tools.
- Minimum Row Span for general tools changed from 3 → 2. The Home button occupies two rows on the left, spanning the slider and value display.
- Closing the panel or changing mode stops all panel transmission and sequence waits. They are not resumed automatically.

### Verification and Distribution

- 16 automated tests passed in a Python 3.13 environment. Verification with real CAN devices and of the R008 binaries must be done separately.
- After running `python build_exe.py`, use `dist/PCAN_Viewer_JJ_R008_win/PCAN_Viewer_JJ_R008_win.exe` on Windows.
- On Linux, use `dist/PCAN_Viewer_JJ_R008_linux/PCAN_Viewer_JJ_R008_linux`.
- The little-endian limitation of general panel fallback encoding remains. Sequences run with saved bytes/masks.

---

## Records Before R007

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
