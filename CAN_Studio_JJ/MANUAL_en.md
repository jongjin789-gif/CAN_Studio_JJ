# CAN Studio JJ - User Manual (R013)

This document describes the main features and usage of CAN Studio JJ (formerly PCAN Viewer JJ) in detail.

## Kvaser Connection (Windows)

1. Install **Kvaser Drivers for Windows** from [Kvaser Downloads](https://kvaser.com/download/), reboot the PC if the installer asks, and then connect the device. If this is the first time installing the driver, restart the program. Because the program uses the existing `python-can`, you do not need to install the Python `canlib` package or the CANlib SDK separately.
2. In **Communication Settings / DBC (통신 설정 / DBC)** in the main window or User Panel, select **CAN Type (CAN 방식) → Kvaser** for each BUS and press **Refresh**. PCAN and Kvaser can be used together on different BUSes.
3. Check the device name, serial number (S/N), and device-internal channel number (#) in **Channel**, and set the **Baudrate**. `[Virtual]` denotes a virtual channel provided by the driver.
4. For Classic CAN, set **Data Bitrate → Off**; for CAN FD, set **Data Bitrate** and **FD ISO** on an `[FD]` channel, then press **Open**. If the FD mode is not supported, you are notified when connecting. Check the device specifications for the actual maximum speed.

If the driver is not installed, installation guidance is shown in the channel list. Installing the driver alone does not add the Kvaser option to an existing EXE; you must rebuild from the updated source. Without hardware, actual CAN transmission/reception cannot be verified. On Linux, select the `canX` interface provided by the Kvaser kernel driver as **SocketCAN**.

### CAN Type ALL and Driver Installation Guide (Windows)

- **CAN Type (CAN 방식) → ALL**: Shows PCAN, Kvaser, and candleLight devices together in one list. When you select a channel, it connects using that device type, and the speed list changes to match the device. Kvaser virtual channels provided by the driver are hidden in ALL and shown only in Kvaser mode. The default type is PCAN, and the selected type (including ALL) is saved in the session.
- **Devices with a mismatched driver**: If a device is connected but its driver is missing or incorrect, it is shown in the channel list as `… · 드라이버 설치 필요` ("driver installation required"; for candleLight, `… · WinUSB 드라이버 필요`, "WinUSB driver required") and cannot be selected.
- **Installation guide dialog**: Shown only when you press **Refresh**, use **Device Scan (장치 검색)** in the menu, or select a CAN type directly. It is not shown during automatic scans such as program startup or session restore. Only one guide dialog is shown per action (other devices are handled on the next Refresh), and a device that has already been prompted is not asked about again until the program is restarted.
  - **PCAN**: Installs the PCAN-USB driver from `Driver\pcan_drivers` (administrator privileges). When finished, unplug and replug the USB. If the file is missing or installation fails, the PEAK download page is suggested.
  - **Kvaser**: Runs the Kvaser installer from `Driver\kvaser_drivers`. After installation, you must **restart CAN Studio JJ** to use Kvaser devices.
  - **candleLight**: Removes an incorrect driver such as a COM port driver and switches to WinUSB (administrator privileges). When finished, unplug and replug the USB.
  - For PCAN and candleLight, the program automatically rescans once the device reconnects with the correct driver after the operation (waits up to 2 minutes).
- Pressing "No" in the administrator privilege prompt changes nothing. You can also run the installer files in the Driver folder directly.

### candleLight (gs_usb) USB-CAN Adapter (Windows)

1. Connect the adapter. If it appears as a COM port under **Ports (COM & LPT)** in Device Manager, double-click `Driver\candleLight_WinUSB\Install_candleLight_WinUSB.bat` to switch it to the WinUSB driver (administrator confirmation required; no external driver installation). It is correct if it appears under **Universal Serial Bus devices**.
2. Select **Communication Settings / DBC (통신 설정 / DBC) → CAN Type (CAN 방식) → candleLight**, and channels are detected automatically. The channel name shows the device number (#) and serial number (S/N).
3. Set the **Baudrate** and press **Open**. It is **Classic CAN only**, so Data Bitrate is N/A and CAN FD frames are not transmitted.
4. Even if the device order changes after re-plugging the USB, the same adapter is found by its serial number. If the USB is unplugged while connected, it is detected as device removal and the connection is closed.
5. You cannot connect if another program is using the adapter (WinUSB can be used by only one program).
6. Transmit characteristics: This adapter silently drops the next frame if it arrives before the previous frame has gone out on the bus, so the program confirms transmit completion before sending the next frame (up to about 1,700 frames/s at 500 kBit/s). If the adapter drops a frame during bidirectional traffic (a few % in testing), it is automatically resent after about 20 ms, so nothing is lost, but that frame's cycle may be delayed accordingly.
7. If there is no node on the bus to ACK, or the Baudrate differs, the transmission is stopped after about 1 second with a "transmit buffer full" (송신 버퍼 가득 참) error.

On Linux, the kernel gs_usb driver creates `can0` etc., so select **SocketCAN**.

**If only two virtual channels are shown**: `Kvaser Virtual CAN Driver [Virtual]` is a test channel, not a port of a real device. Check the status of the real Kvaser device in Windows Device Manager. If there is no Kvaser device in Device Manager at all, Windows has not recognized the USB device. Check that you rebooted after installing the driver, and try connecting directly to a PC USB port without a hub, or try a different cable/port. Error code 28 means that even though the driver package is installed, the driver has not been applied to the physical device. Close the program, run the Kvaser Drivers for Windows installer as administrator to repair/reinstall, and then reconnect the USB. After confirming that the device status is normal, press Refresh in the program. Refresh rescans CANlib devices and keeps the selection by serial number and device-internal channel number even if the numbering changes.

**USBcan Light 4xHS** is a 4-channel Classic CAN product (50 kbit/s–1 Mbit/s). When recognized correctly, internal channels `#1`–`#4` are shown under this device name and CAN FD settings are disabled. The two virtual channels may also be shown, making six entries in total.

## Unified Settings and Menu Bar (added 2026-09-29)

- **File → Save Unified Settings… (파일 → 통합 설정 저장…)** (`Ctrl+Shift+S`): Saves the original DBC/SYM content for BUS 1·2·3, the TX list, CAN settings, real-time graphs, and the entire UserPanel into a single `.pjjsettings` file. Settings can be restored from this file alone even if the original DB files are moved or deleted.
- **File → Load Unified Settings… (파일 → 통합 설정 불러오기…)** (`Ctrl+Shift+O`): Validates the entire file first and then replaces the existing settings. If validation fails, the existing settings and windows are kept. Empty items are applied as-is, so no previous items remain.
- Graphs preserve per-window title, legend names/order, colors, line styles, visibility, formulas, and window position/size. Graphs included in a combined view are also restored as **individual windows with Sync OFF**. Saving does not change the current on-screen Sync state. Previous waveforms, measurement tags, tree received values, and counts are not restored.
- UserPanel includes pages, tools, layout, registered packets, Init/sequences, and signal links. The configuration of closed panels is also preserved, and the restore mode is **STANDARD (transmission stopped)**. RUN/sequence execution is not resumed automatically.
- When loading, TX, UserPanel, and log retransmission are all stopped, and **the existing log view main window and all individual/combined log graphs are closed.** Original log files are not deleted. Any recording in progress is saved under `Log` in the user data folder.
- On Windows, BUSes that were Open at save time are reconnected. On Linux, all BUSes start Closed regardless of the previous connection state, and only device selection and speed settings are restored. Even if a device is not found, the BUS is Closed and the saved settings are preserved. Manual loading reports the restore result; automatic restore at startup omits the device-not-found notification.
- **Database → Export per BUS / Export All DBs… (데이터베이스 → BUS별 내보내기 / 전체 DB 내보내기…)**: Extracts the original content as registered into per-BUS subfolders. If a file with the same name exists, a number is appended to preserve the existing file. `등록 해제` (Unregister) does not delete the original file.
- **Transmit → Stop All Transmission (송신 → 전체 송신 정지)** (`Ctrl+Shift+X`): Stops all TX, UserPanel, and log retransmission while keeping the CAN connection.
- The `CAN`, `송신` (Transmit), and `보기` (View) menus also provide the functions of the existing on-screen buttons. `도움말` (Help) lets you view the manual and program information. In viewer-only mode, the CAN/TX/unified-settings change menus are not shown.

### Auto-Restore and Storage Location

The auto-restore file is saved as `session.pjjsettings` next to the executable (the project folder when running from source). Changes are saved about every 5 seconds and on normal exit. Building a new version changes the distribution folder name, so to keep your previous settings, copy `session.pjjsettings` from the previous distribution folder to sit next to the new executable.

The run flags / TX packet file is `CAN_Studio_JJ.pk` next to the executable (`PCAN_Viewer_JJ.pk` up to R012_1). If `CAN_Studio_JJ.pk` does not exist and `PCAN_Viewer_JJ.pk` does, it is copied automatically and used. This `.pk` file is read to import previous settings only when there is no new auto-restore file. Existing files are not deleted. If the auto-restore file is corrupted, an error is shown and automatic overwriting stops. Auto-save is re-enabled after you load a valid unified settings file or successfully save unified settings.

Unified save is for settings; it is not a backup of received logs themselves. Formulas support numbers, signal variables such as `X1`, `Y1`–`Y3`, and arithmetic/comparison operators. External function calls and Python code execution are not supported.

## Table of Contents
1. [Screen Layout](#1-screen-layout)
2. [Basic Usage](#2-basic-usage)
   - [Channel Connection](#channel-connection)
   - [Applying a Database (DBC/SYM)](#applying-a-database-dbcsym)
   - [Data Reception and Analysis](#data-reception-and-analysis)
3. [Transmit Functions (Tx Panel)](#3-transmit-functions-tx-panel)
   - [Creating and Editing Packets](#creating-and-editing-packets)
   - [Packet Type (CRC/Alive Count)](#packet-type-hyundai_crc)
   - [Sending and Managing Packets](#sending-and-managing-packets)
4. [Real-time Graph](#4-real-time-graph)
   - [Opening a Graph](#opening-a-graph)
   - [Key Features](#key-features)
5. [Log Recording and Viewer](#5-log-recording-and-viewer)
   - [Data Recording](#data-recording-record)
   - [Log Viewer](#log-viewer)
6. [User Panel](#6-user-panel)
    - [Control Modes](#control-modes)
    - [TX Behavior](#tx-behavior)
    - [Command Sequence](#command-sequence)
7. [Run, Build, and Version](#7-run-build-and-version)
8. [Diagnostic and Analysis Tools (R013)](#8-diagnostic-and-analysis-tools-r013)

---

## 1. Screen Layout

The program's main screen consists of four main areas.

- **① Connection Control**: Area for selecting CAN hardware channels and starting/stopping communication.
- **File → New Screen (파일 → 새 화면)** in the main window resets the communication connections, DBCs, TX/RX packets, graphs, and User Panels. Unsaved recordings are preserved first, and the reset state is reflected in the automatic settings save.
- **File → New Panel** in the User Panel resets the panel's tools, registered TX packets, INIT, pages, and edit history. The main window's connections and DBCs are kept.
- The User Panel title is changed via **File → Rename Panel…** in that window. The title is saved together in the panel file, package, and unified settings.
- **② Database Files**: Area for loading and managing `.dbc` or `.sym` files used to interpret communication data.
- **③ Monitoring Tree (RX section)**: Area that shows messages and signal values received from the CAN bus in real time.
- **④ Tx Panel (TX section)**: Panel for creating CAN messages and transmitting them to the bus.

---

## 2. Basic Usage

### Channel Connection
1.  In the **Connection Control** panel, select the **Bus** to connect. (Up to 4 channels simultaneously)
2.  Choose the **CAN Type (CAN 방식)** for each BUS, then select a CAN hardware channel detected for that type and the communication speed (Baudrate).
    - Windows: **PCAN** (PCAN-Basic driver), **Kvaser** (Kvaser Drivers for Windows / CANlib). For Kvaser details, see [Kvaser Connection (Windows)](#kvaser-connection-windows) at the beginning of this document.
    - Linux: **SocketCAN** (`can0`, `can1` …, `vcan0` for testing). Uses interfaces registered by kernel drivers regardless of the device manufacturer.
    - The CAN type cannot be changed while connected and is saved per BUS in the unified settings. For older settings files, the type is determined from the saved device kind.
3.  For **CAN FD** communication, enable the FD option and set the data communication speed (Data Baudrate).
4.  Press the **Open** button to start communication. On a successful connection, the button is disabled and the **Close** button is enabled.
5. Linux SocketCAN can be run as a regular user. If interface configuration requires privileges, the app shows a sudo password dialog. If the sudo authentication cache is still valid, it does not ask again; the entered password is kept in memory for at most 60 seconds for consecutive commands and then discarded. It is never saved to a file or log. Windows has no such dialog.

- The connection settings are a 4-row grid where the row number is the BUS number. Row sorting and moving are not supported. Bus settings use compact rows and a small font matching the TX packet table, and the speed notation keeps the existing settings.
- **Open / Close** is a single button that changes according to the actual connection result. While connected, device scanning and settings editing for that BUS are locked. There is no Reset button.
- **State** shows the current status or the main error on one line. The **…** button on the right lets you view, copy, and save the per-BUS chronological log and error diagnostics. PCAN CAN warnings and Bus-Off states are also checked; Bus-Off disconnects that BUS.
- If a USB device is unplugged while connected, that BUS is disconnected automatically (PCAN detects it after about 2 seconds). No error dialog is shown; the cause is displayed in the **State** field, and details are available in the BUS log via the **…** button. Receive errors and disconnect errors are recorded the same way.
- Disconnecting stops the main TX, panel periodic transmission, and related sequences for that BUS. Log playback is stopped (logs are sent only to the recorded BUS). Transmission tasks are not resumed automatically after reconnecting. That BUS's received values and statistics are reset on reconnect, while DBCs, packets, and panel settings are kept.
- The arrows on the **CAN Settings** and **Database Files (.dbc, .sym)** titles collapse and expand the settings areas. The TX section height is kept and the RX section height adjusts. The collapsed state is saved in the unified settings, and DBC decoding and log opening remain available.
- The User Panel's **Communication Settings / DBC (통신 설정 / DBC)** window shares the same 4-channel grid and connection state. Changing communication settings switches the panel to STANDARD.
- The unified settings save format is v2. When reading an existing v1 3-channel setting, BUS 1–3 and panel references are kept and an empty BUS 4 is added. On the first conversion, the original is kept as a `.v1.bak` file. Older executables cannot read new v2 files, so use the backup when reverting to a previous version.

### Applying a Database (DBC/SYM)
1.  In the **Database Files** panel, click the **Load DBC/SYM** button for the corresponding bus and select a file.
2.  When the file is loaded successfully, its file name is shown in the list.
3.  The RX section (Monitoring Tree) now shows message names instead of CAN IDs, with signals listed beneath them.

> **💡 .sym File Compatibility**
> PEAK-System `.sym` files in both format version 5.0 and 6.0 are supported. The program analyzes the file content to detect the version automatically and parse it, so you can load any file as long as the extension is correct.

### Data Reception and Analysis
-   In the **Monitoring Tree**, received messages are grouped and displayed by CAN ID.
-   When a DBC file is applied, message names and their signal lists are shown in a tree.
-   The **Value** column shows the message's raw data (hex) or each signal's physical value in real time.
-   Even if signals with the same name exist in different CAN IDs, values are displayed separately by BUS, CAN ID, and signal name. Enums, decimal precision, and units also use that message's definition.
-   The **Cycle (ms)** and **Count** columns show the message reception cycle and count.
-   **Batch selection**: Checking the checkbox of a message name (parent item) selects/deselects all of its signals at once.
-   Double-clicking the **RX** label next to the RX section or the **TX** label next to the TX section toggles that area's tree between collapse all / expand all. If any message is expanded, everything collapses; if all are collapsed, everything including child items expands. The two areas work independently.
-   **Sorting**: The main window RX section is sorted by **Bus number → CAN ID** by default. Clicking the **Bus** header sorts by CAN ID numerically within the same bus, and new packets are inserted at the matching position. Clicking another column header sorts by that column's values; clicking the same header again toggles ascending/descending. Cycle, Count, and numeric signal values are sorted by numeric magnitude.

---

## 3. Transmit Functions (Tx Panel)

In the TX panel at the bottom of the main window, you can create CAN messages and send them to the bus.

### Creating and Editing Packets
1.  Press the **Create Packet** button to open the packet creation window.
2.  In the **CAN ID / Symbol** field, select a symbol (message name) defined in the DBC file, or enter a CAN ID directly in hex.
    -   Selecting a symbol automatically fills in ID, Length, and Type, and a signal table appears below.
    -   Entering a physical value in a **Value** cell of the signal table automatically calculates the **Data (HEX)** at the top.
3.  You can also enter hex data directly in the **Data (HEX)** field. In this case, the values in the signal table are calculated in reverse and displayed automatically.
4.  To edit a created packet, **double-click** the item in the list. The expanded/collapsed state of the child signal tree is kept after confirming or canceling the edit. You can change the state directly with the expand arrow.

### DBC Signal CRC-8 / CRC-16
- In **Auto Inc/Dec / CRC (자동 증감 / CRC)** of DBC Signals, select `CRC-8` or `CRC-16` and press the **CRC Settings (CRC 설정)** button in the adjacent cell. CRC-8 applies to 8-bit integer signals and CRC-16 to 16-bit integer signals.
- The calculation range is specified by a **0-based byte index and size**. For example, bytes 2–31 are start `2`, size `30`.
- **Additional calculation data** is fixed HEX bytes appended after the range. `0x0000` or `00 00` appends two zero bytes. `12 34` is appended in the entered order.
- Enter `poly`, `init`, and `xorout` as HEX values. Poly is the normal-form polynomial excluding the highest term; RefIn/RefOut specify bit reflection of the input bytes and the final CRC, respectively.
- The CRC is calculated on every transmission after the counter value is applied. Factor/Offset are not applied to the result. For CRC-16, choose **LSB first (Intel)** or **MSB first (Motorola)** in the settings window. Example: the result `0x1234` becomes `34 12` or `12 34`, respectively. The DBC signal's bit position is kept, and the input data order and RefIn/RefOut settings are not affected. Previously saved settings keep the existing DBC order.
- A registration error is shown if the calculation range includes even one byte containing bits of the CRC signal itself. Exceeding the length, invalid parameters, and circular references between CRCs are also not allowed.
- Settings are kept in packet editing, the auto-save file, and XMT save/load. When using DBC signal CRC instead of the legacy `Hyundai_CRC` packet type, set the packet type to **None (적용 안함)**.

### Packet Type (Hyundai_CRC)
-   You can select `Hyundai_CRC` in the **Packet Type (패킷 타입)** combo box.
-   When this type is selected, regardless of what you enter in the **Data (HEX)** field, the program internally overwrites the first 3 bytes of data at the moment the packet is actually sent, according to the rules below.
    -   `Data[0]`, `Data[1]`: CRC-16 CCITT-FALSE value (Little Endian)
    -   `Data[2]`: Alive Counter value cycling from 0 to 255
-   You only need to enter the actual user data (from byte 3 onward), excluding the CRC and Alive Count.

### Sending and Managing Packets
-   **Single send**: Select the packet to send in the list and press the **Spacebar** to send it once.
-   **Periodic send**:
    -   When creating the packet, set **Cycle Time** to a value greater than 0 (ms).
    -   Press the **Start** button in the list's **Action** column to start automatic transmission at the set cycle.
    -   During transmission, the button changes to **Stop**; click it to stop transmission.
    -   Periodic transmission from the main TX and the User Panel is handled by per-BUS background threads using a common scheduler. Screen updates and log recording are separated from the transmit path, and each cycle is added to the previous target time to reduce cumulative error. Missed cycles are not sent in a burst; the scheduler skips to the next time based on the original cycle.
    -   Starting log playback keeps main/panel periodic transmission running. If the same BUS is transmitting or is close to a scheduled periodic transmission time, log selected/continuous sending waits. It does not preempt frames already passed to the driver or CAN arbitration. Delays and drops can be checked via the `TX 주기 지연` (TX cycle delay) warning in the BUS log.
    -   On Windows, 1 ms timer resolution is requested while periodic transmission tasks exist and released when they end. This does not guarantee 1 ms accuracy; errors due to OS scheduling, drivers, and bus load must be verified on real hardware.
-   **Management shortcuts**:
    -   **Delete**: `Delete` key
    -   **Copy/Paste**: `Ctrl+C`, `Ctrl+V` (multiple items can be selected)
-   **Save/Load list**: Use the **Load/Save Tx List** buttons to save the current packet list to, or load it from, an `.xmt` file.

---

## 4. Real-time Graph

Select signals in the RX tree to view their changing values on a graph in real time.

### Opening a Graph
1.  In the RX section (Monitoring Tree), **check the checkbox** in front of the signal names you want to graph. (Multiple selections allowed)
2.  Click the **[View Real-time Graph for Selection]** button to show all selected signals in a single graph window.
3.  When the graph opens, all message/signal checks in the RX tree are cleared. Select new signals for the next graph.

### Key Features
- **Reset Zoom**: Shows the most recent 30 seconds based on the latest data and auto-adjusts the Y-axis range for that interval. If **Auto Y** is on, the range expands automatically when Y values in the data currently visible on the X-axis exceed it. Turn off Auto Y to fix the Y-axis manually.
- Reset Zoom in the **real-time combined graph** also aligns all graphs to the same most recent 30-second interval. **Fit All Data** shows the entire data interval kept in memory. In the combined graph, panning/zooming one plot's X-axis makes the other plots use the same time range.
-   **Legend**: **Double-click** a legend item on the right of the graph to temporarily hide or show that signal's plot.
-   **Tags**:
    -   **Click** a point on the graph to pin a tag showing the time and value at that position.
    -   If you click the graph while multiple signals are selected in the legend (click or Ctrl+click), tags are created for all selected signals at once.
    -   Click a created tag again to delete it.
-   **Sync X-Axis**: Newly opened real-time and log graphs have sync enabled by default. When you zoom or pan in one window, the X-axes of all other synced windows move together. Resizing a window is applied the same way, and sync can be turned off with each window's Sync button.
-   At the **far right** of the combined view toolbar there are two close buttons. **Exit Combined View (통합보기 종료)** switches to individual graph windows, and **Close Graphs (그래프 종료)**, the power icon, closes the combined window and all included graphs. The **X** in the window title bar also closes all graphs without opening individual windows.
-   **Edit Labels (labels/style)**:
    -   You can change the graph title and each legend's display name to any text you want.
    -   **Drag items with the mouse** in the legend list to reorder them.
    -   You can easily change each signal's **line color** and **line style** (solid, dashed, etc.).
-   **User-defined Formula**:
    -   Press the **Formula** button to create new virtual signals (Y1, Y2, Y3) using the signals shown in the graph as variables (X1, X2...).
    -   Example: arithmetic such as `X1 + X2` or `(X1 - 5) * 10` is supported.
-   **Screenshot**: Saves the current graph view as an image file (.png). If X-axis sync is on, all synced graphs are combined and saved as one long image.
-   **Close Graphs**: On the right of the main window RX toolbar, buttons are arranged in the order **Close Graphs → Clear Data → Record**. In the log view, **Close Graphs** is at the far right of the graph toolbar in the DBC tab. Each button closes all individual and combined graphs opened from that window.

---

## 5. Log Recording and Viewer

### Data Recording (Record)
-   With at least one CAN channel open, press the **Record** button in the main window to open the recording window.
-   From then on, all received CAN messages are recorded in PEAK-System `.trc` format.
-   The log being recorded is written to a temporary file in real time rather than memory, so memory does not grow even during long recordings.
-   **Save**: Choose a save location, save, and close the window. Canceling the save dialog continues recording.
-   **Close** button: Closes without saving and deletes the temporary file.
-   Window X button or program exit: If there is a recording, it is saved automatically to `[program folder]/Log/` and the temporary file is deleted.
-   `$STARTTIME` is based on the time of the first frame, so absolute time is correct when reloaded. 29-bit IDs are recorded as 8 digits according to the DBC definition.
-   **ID filter** (top of the window): Choose **Exclude range (범위 제외)** (record excluding IDs in the range) or **Range only (범위만 기록)** (record only IDs in the range), enter an ID range (hex, e.g. `100-1FF, 321`), and press Enter or **Apply (적용)**. The same filter applies to all BUSes.
    -   Filtering applies to frames after the time it is applied; already recorded frames remain. The number of filtered frames is shown as `제외 N` (excluded N).
    -   Selecting **Record all (전체 기록)** clears the filter immediately. Filter change history is left in the saved file header as `; ID filter from <time>: exclude/only <range>` (the filter is also reflected in ASC/BLF saves).

### Log Viewer
-   Click the **Open CAN Log Viewer** button to open a saved `.trc` file.
-   Supported formats: PEAK TRC 1.0 / 1.1 / 1.2 / 1.3 / 2.x (including CAN FD), CL2000 logger TXT. IDs recorded as 8 digits are retransmitted as 29-bit (Extended).
-   When opening a file, the log viewer interprets the log based on **the DBC/SYM files currently loaded in the main window** and shows the analysis results.

#### Log Viewer Features
-   **DBC tab**:
    -   Shows the list of messages and signals found in the log file.
    -   Check signals here and press the **View Graph** button to see a **static graph** over the entire log period. When the graph opens, all message/signal checks are cleared. (Same features as the real-time graph)
    -   In the log graph's **Combined View**, **Reset Zoom** and **Fit All Data** set the X-axis to the full log time span of all participating graphs, and the Y-axis is auto-adjusted to each graph's data. Empty log data or a single time point is also supported.
-   **LOG tab**:
    -   Shows the raw contents of the `.trc` file in a table.
    -   The first row has **Send (전송)**, **Send Selected (선택 전송)**, and **Repeat (반복)**, with **Save** at the far right. The second row has CAN bus selection, the CAN ID range, and the **Filter (필터)** button.
    -   **Filter**: Select a CAN bus (`ALL`, `BUS1`–`BUS4`), enter the start and end CAN IDs in hex, and turn the filter on. Logs that satisfy both the bus and ID range are shown. The default ID input is `0x000~0x7FF`, and the range `0x000~0x1FFFFFFF` supports both Standard and Extended. To view a single ID only, enter the same start and end.
    -   **Cancel Filter (필터 취소)**: Stops an in-progress filter or clears an applied filter and shows the logs for all buses and IDs again. The entered bus/ID conditions are kept. It does not wait for the previous job to finish or re-check all IDs; it only restores hidden rows. Restoring many hidden rows takes some time, and progress is shown in the status bar. Turning the filter button off again clears it the same way.
    -   You can still operate the window while filtering/unfiltering large logs, and the status bar at the bottom shows processing progress and result counts. If you change the range or clear the filter during processing, it is reapplied with the new conditions. Save, send, and copy are available after the filter completes.
    -   **Save**: Saves the current filter results to a separate `.trc` file. The original header, time, type, direction, data, and line endings are kept; only message numbers are renumbered from 1. You must use a file name different from the original. If the filter is off, the entire displayed log is saved.
    -   **Log data retransmission**:
        -   **Send (전송)** button: Retransmits only the current filter results at the original time intervals. If a displayed row is selected, it starts from that position; otherwise it starts from the first result. Once sending starts, the target list is fixed.
        -   **Send Selected (선택 전송)** (or Spacebar): Sends only the data of the one selected row in the table, once.
        -   **Repeat (반복)** checkbox: When sending finishes, repeats from the beginning of the filter results.
        -   **BUS check**: Each frame in the log is sent **only to its recorded BUS number**. If the log to be sent contains a BUS with no device connected, or CAN FD frames for a BUS connected as Classic CAN, sending does not start and the affected BUS is indicated. Frames are never sent to a different BUS instead. To send only some BUSes, keep only those BUSes with the CAN bus filter and then send.
        -   The Send buttons are enabled/disabled immediately as BUSes connect/disconnect. If you open a log while all BUSes are closed and then connect a BUS, you do not need to reopen it.
        -   Successfully sent frames are also shown in User Panel RX tools linked to the **BUS and CAN ID** actually used, **regardless of the TX/RX direction in the original log**. With the panel in **STANDARD or RUN**, values, Enums, colors, and grids are updated. They are displayed without a driver TX echo and do not depend on the receive thread or main tree updates. Fast playback shows the latest value per message in about 50 ms steps. EDIT keeps the existing edit/display test behavior.
        -   This screen update is separate from the sequence's response reception check. RCV waits are evaluated only against actually received frames.

---

## 6. User Panel

A user-defined control/display screen opened with the **User Panel** button on the Tx Panel toolbar.

### Control Modes
- **EDIT**: Mode for creating/deleting/placing tools and changing settings
- **STANDARD**: All User Panel TX stopped; RX display widgets are updated
- **RUN**: Both TX and RX active
- The Tool List, property editing area, and Add/Rename/Delete Page buttons are visible only in EDIT. Page tabs and the work canvas are available in other modes as well.
- If edit security is enabled, a password is required when returning to EDIT (from STANDARD/RUN).
- Closing the panel or changing modes stops all of the panel's periodic transmissions, sequences, response waits, and delays. On entering RUN, periodic transmission of registered packets starts without any initial operation, and sequences start with their run button. Separate transmissions from the main TX panel are not included in this stop scope.
- The RUN Init sequence area is shown above the canvas. You can adjust its height or collapse it with the vertical splitter handle between the canvas and Init; it is hidden when leaving RUN.
- There is no global Force RUN mode. The **Force Run (강제 실행)** button of a regular sequence tool skips only RCV and performs CMD/DEL/START/STOP (no communication verification required). The **Packet Control (패킷 제어)** button excludes CMD/RCV and runs only START/STOP/DEL.

### TX Packet Registration
- In EDIT mode, **Register / Manage TX Packets (TX 패킷 등록 / 관리)** lets you register, edit, and delete packets using the same packet creation window as the main window. Count up/down/cycle, CRC-8/CRC-16, and Hyundai CRC settings are supported.
- For per-DBC-signal counters, in addition to the existing **None / Up / Down / Round-trip (None / 업 / 다운 / 왕복)**, you can select **Sync to RX value + Up/Down/Round-trip (수신값 동기화 + 업/다운/왕복)**. It reads the raw value of that DBC signal from the most recent RX frame with the same BUS/CAN ID as the TX packet. When the RX value changes, that value takes priority in the next TX; if the same value repeats or no value has been received yet, it counts in the selected direction within the min/max range. If CRC is configured, the CRC is calculated after the counter is applied. Specifying a signal from a different CAN ID as the source is not supported.
- **Delay input is required.** `0` sends only when a tool value changes; a positive value sends the packet every that many ms. Periodic packets are sent in RUN even if no tool is linked.
- Registered packets with a positive cycle are sent by per-BUS transmit threads using the same scheduler as the main TX. If transmission falls behind and misses a cycle, the backlog is not sent all at once; it skips to the next cycle based on the original target time. Missed cycles and maximum delay can be checked via the `TX 주기 지연` (TX cycle delay) warning in the system log. Real-time performance of the OS/driver is not guaranteed, so verify cycles on real hardware.
- The same BUS/ID can be registered only once. To delete a packet that has linked tools, first delete the tools or link them to another registered packet.
- Transmit data application order: **registered original → overwrite the specified bits of tool signals → active count → active CRC**. Bits not specified by tools keep the original.
- The registered list is saved in the panel JSON and package. Loading a file starts in EDIT. Unregistered TX tools in existing files must complete packet registration and linking before RUN can be used. RX tools keep the existing full DBC selection method.
- Packets using count/signal CRC require the corresponding DBC/SYM.

### Creating Tools
- **Add TX Tool**: Create a transmit widget with the ID/symbol of a registered packet
- **Add RX Tool**: Create a receive widget
- **Add Group/Shape**: Create group/tab/shape tools
- Quick shape creation: **Draw Rect / Draw Line**

### Copying and Cutting Tools
- In EDIT, select one tool or multiple tools with Ctrl+click, then duplicate with **Ctrl+C → Ctrl+V**. This works in both the canvas and the tool list, and the duplicates are selected at a slightly offset position.
- **Holding Ctrl while dragging the frame/title of a selected tool** copies the selected tools. Ctrl+click without moving selects/deselects as before.
- Copying a group also copies its child tools. Layout spacing, signals, styles, and sequence settings are kept, and duplicates get new IDs and independent settings. Duplication can be undone/redone with Ctrl+Z/Y.
- **Ctrl+X → Ctrl+V** cuts and pastes the selected tools. Even when selecting a single group, all child tools of nested groups/tabs move together, keeping names, settings, and internal layout. You can switch to the target page tab before pasting. A cut item is cleared from the clipboard after being pasted once, and the cut and the paste can each be undone/redone with Ctrl+Z/Y.
- **Cut / Copy / Paste** are also available from the **Edit** menu and the tool list's right-click menu. Ctrl+X/C/V in property input fields are used for text editing.
- After copying, you can change to a similar signal in the properties. To create the same TX tool on another BUS, first register that BUS's packet, then select the BUS/registered packet in the duplicate's properties. Multiple duplicates can be changed at once via **TX Registered Packet (TX 등록 패킷)** in the common properties. RX uses the existing BUS/DBC signal selection.

### TX Behavior
- **button**: Applies `Button Push Value` to the packet's specified bits when pressed and `Button Pull Value` when released. The transmit interval follows the registered packet's delay.
- **toggle**: Sends `Toggle ON/OFF Value` according to ON/OFF
- For toggles linked to the same bus, CAN ID, and bit area, turning one on turns the others off. The OFF value of an automatically released toggle is not sent. Clicking an ON toggle again sends its own OFF value.
- **slider/spinbox**: Sends values based on `Min/Max/Resolution`
- **slider**: Integer signals (integer scale/offset) or an integer Resolution are displayed as integers. Others are displayed according to the Resolution's decimal places, e.g. `0.1 → 1.0`, `0.01 → 1.00`. `Initial Value (Home)` is the initial position, and the Home button on the left spans the two rows of the slider and value display. Pressing Home in RUN also sends that value. The initial position is applied according to the range and step. When the slider has focus via Tab or click, the Left/Right arrow keys decrease/increase by one step. Entering a number in the input field below and pressing **Set** or Enter moves to that position. Out-of-range numbers are clamped to the minimum/maximum and snapped to the step. Invalid values such as empty input, text, NaN, or infinity return to Home. Tx sliders in RUN apply the existing transmit behavior to these operations as well.
- **Frame format, length, BRS, and delay** are set in the registered packet. The per-tool TX Cycle Mode / TX Cycle inputs have been removed.
- Single-tool properties select the registered packet and the signal/bit field. Links can be changed in bulk via **TX Registered Packet (TX 등록 패킷)** in the multi-selection common properties. RX BUS and common layout, size, and BUS speed changes are kept.

### Command Sequence

In **RUN Init Sequence Settings (RUN Init 시퀀스 설정)**, register the steps to run automatically on entering RUN. Init supports only `CMD` send, `DEL` wait, `START` periodic start, and `STOP` periodic stop; there is no receive comparison. Leave the list empty to not use Init.

- The order is: enter RUN → run Init → on success, start periodic transmission of registered packets. Without Init, registered periodic transmission starts immediately.
- During Init, regular TX tool/sequence operations and automatic periodic transmission are held. Init CMDs are executed, and the periodic transmission states set by START/STOP are applied after Init completes.
- Packets STOPped in Init are not restarted automatically. They can be restarted with START in a regular sequence. If Init fails or is aborted, the panel returns to STANDARD.
- Re-entering RUN runs Init from the beginning. Init and failure-handling settings are saved together in the panel JSON/package.

Create it via `Add TX Tool → sequence → 명령 시퀀스 설정` (Command Sequence Settings). To see the buttons and log, an area of about Row Span 8, Col Span 12 is recommended.

- Grid columns: type (CMD/RCV/DEL/START/STOP), packet create/edit or target selection, command/response name, and a read-only summary or bit edit button.
- New steps are inserted below the selected row. Manage steps with the Delete and Up/Down buttons.
- CMD: Select a registered TX packet and specify the repeat count. It uses the common transmit processing that applies tool values and count/CRC, and the repeat interval follows the registered packet delay.
- Checking **Packet Create / Edit → Use CMD-specific Value (패킷 생성 / 편집 → CMD 전용 값 사용)** in a CMD lets you edit that step's transmit value in `Data (HEX)` or `DBC Signals → Value`. This is supported in Init, regular sequences, and failure handling alike. The data successfully sent after count and CRC are applied becomes the current packet value and persists in subsequent periodic transmissions. If sending fails, the previous value is kept.
- Ending a sequence or switching STANDARD→RUN does not reset the current value. Sending an initial value with the next sequence CMD updates it to that value. Tool operations change only their bits, and active count/CRC continue to be calculated on every transmission. The registered original is not changed.
- When `CMD 전용 값 사용` (Use CMD-specific Value) is unchecked, the current packet value is used. BUS/ID/length/cycle/count/CRC settings are changed in the registered packet, and if the registered length changes, the CMD-specific data length must also be adjusted.
- RCV: Check one or more signals of the same ID to set expected values, or edit bits without a DBC as `[0]: XXXX XX01`. `X` excludes a bit from comparison; all compared bits plus the bus, ID, format, and length must match in the same frame. It aborts when the timeout (ms) is exceeded.
- DEL: Moves to the next step after the set time (ms).
- START/STOP: Starts/stops **automatic periodic transmission** of an individual registered packet or all of them. CMD sends of the same packet and value-change sends from delay-0 tools are kept. Repeating START does not create duplicate timers.
- A regular sequence shows OK when the receive condition matches, and NG on receive timeout, transmit error, etc. Non-matching frames keep being waited on until the timeout.
- In **Commands to Run on Failure (실패 시 실행할 명령 설정)** you can enter a list of CMD/DEL/START/STOP. On failure, normal step execution stops and that list runs once. Even if failure handling succeeds, the original sequence result is NG; if failure handling itself fails, it ends immediately.
- A user stop, switching to STANDARD/EDIT, or closing the panel cancels all sequence activity without running the failure-handling list.
- CMD references a registered packet. RCV is saved as expected bytes and a comparison mask, keeping the existing `X/0/1` editing.
- The normal run and force run buttons cycle through run → stop → rerun from the beginning. Force run ignores RCV and proceeds with the remaining CMD/DEL/START/STOP. The Packet Control button skips CMD/RCV and performs only START/STOP/DEL. Normal log lines are black, errors red, and overall completion green. The sequence log font is shown reduced to save screen space.
- The log shows names instead of packet bytes. IDs are 3 digits for Standard and 8 digits for Extended.

```text
[10:30:00.001] 1/3 CMD BUS_1 0x001 Init 명령 · 송신 완료
[10:30:00.125] 2/3 RCV BUS_1 0x00000001 Init 응답 · 응답 조건 일치
[10:30:00.126] 3/3 DEL - - 안정화 대기 · 200 ms 대기
```

For detailed execution rules, see [Sequence Usage](USER_PANEL_SEQUENCE_en.md).

### RX Behavior
- Supports **progress / label / status_lamp / grid_view** widgets
- **grid_view**: Create via **Add RX Tool → grid_view → Select Signals from DBC Tree… (DBC 트리에서 신호 선택…)**. Checking a symbol selects all its child signals; checking an individual signal selects only that item. Rows are added in the order checked, and unchecking and re-checking moves the item to the last row. Selecting a symbol at once appends the not-yet-selected signals in DBC signal order.
- In the grid properties, check which columns to display among **Bus / Symbol Name / Signal Name / User Name (버스 / 심볼명 / 신호명 / 사용자이름)**. **Value is always shown.** The user name is entered by double-clicking that column in the DBC tree selection window; if empty, the signal name is shown. Existing panels show bus, symbol name, and signal name.
- On creation/editing, column widths are calculated to fit the text length, and the remaining width is assigned to the value column. The minimum tool size is calculated from the number of selected rows, displayed columns, and text length; enlarging the tool scales the text to its width and height. If the content is larger than the grid, scrolling is used.
- Received values, units, and enum values are updated in STANDARD/RUN, and signal selection, row order, displayed columns, and user names are saved in `.pjjupkg`. Saved items not present in the DBC can be reviewed and removed in the selection list.
- **Enums in grid_view** are looked up by the **RAW value** before factor/offset are applied. If matched, `Enum name (physical value unit)` is shown; otherwise the physical value calculated as `RAW × Factor + Offset` and its unit are shown.
- **label** shows the Enum name if the RAW value matches a DBC Enum; otherwise it shows the number with factor/offset applied. If there is a unit, it is shown together, e.g. `Ready V`, `11.5 V`. The unit specified on the tool takes priority; otherwise the DBC unit is used.
- The **graph icon** to the right of the grid title opens all displayed signals, in row order, in the same real-time graph window as the main window. The graph's sync, Combined View, and session save features work the same way.
- **progress**: In the **Progress Display (Progress 표시)** property, choose `퍼센트` (Percent), `실제 값` (Actual value), or `퍼센트 + 실제 값` (Percent + actual value). Percent is 0–100% relative to Min–Max; actual value is the received value with scale/offset and unit applied. The display choice is saved in the panel package, and existing panels keep the percent display.
- **status_lamp**: ON/OFF conditions are configured independently
- Condition operators: `gt`, `ge`, `lt`, `le`, `eq`, `ne`, `between`, `outside`

### Testing Without CAN (RX Simulator)
- **Apply Selected RX**: Injects a manual value into the selected RX tools
- **Apply All RX**: Injects the same value into all RX tools
- **Auto Sim**: Injects automatic waveform values in STANDBY/RUN mode

### Layout/Group Editing
- Select/right-click in the **Tool List** (Edit/Delete/Z-order)
- **Bring Front / Send Back / Forward / Backward**: Change the front/back stacking order
- Drag the tool frame to move it; drag the bottom-right corner to change its size (span)
- **Tool Properties** on the right: Selecting a tool or pressing **Edit** lets you edit position, size, CAN, DBC, signal, and value/behavior without a separate window. Press **Apply Properties** after editing. You must apply before selecting another tool.
- Tool settings are divided into the **Basic / CAN·DBC / Value·Behavior / Color·Condition / Grid (기본 / CAN·DBC / 값·동작 / 색상·조건 / 그리드)** tabs, and only the tabs needed for that tool are shown. General settings are edited in an **Item / Value (항목 / 설정값)** table where each row uses the same background color. **View in Window… (창으로 보기…)** on the right takes the values being entered and opens a wide separate window. OK applies to the tool, and Cancel reverts any unconfirmed color preview. Other properties being entered on the right are kept. Available in EDIT mode with a single tool selected.
- Creation uses the existing settings window. Detailed editing of sequence steps, packets, and bits opens inside the properties window; after OK in the detail editor, apply to the tool with **Apply Properties**.
- Select multiple tools with **Ctrl+click / Shift+click** in the Tool List, or **Ctrl+click** on frames/titles in the canvas. Differing values are shown as `(Mixed)`.
- In multi-selection, check only the items to change and press **Apply Checked Properties**. Position, size, CAN BUS, and the TX cycle/frame format/BRS of general tools are applied in bulk; unchecked items are kept. A CAN BUS change for a sequence is applied to its included CMD/RCV packets.
- In **CAN BUS Speed** you can change the nominal bitrate and FD data bitrate. The speed is shared by all tools on the same BUS and must be applied after disconnecting that BUS. It is also reflected in the main window's connection speed settings. Speeds saved in a panel file are not applied to the device just by loading; check and apply them.
- Dropping onto a group/tab automatically makes it a child of that parent. The tool size before the move is kept, and if the internal space is insufficient, you can scroll to see it.
- Groups (`group_box`), tabs (`tab_container`), and rectangles (`shape_rect`) set background color and border color/thickness/style/corners in **Color / Style Settings (색상 / 스타일 설정)**. Lines (`shape_line`) set line color/thickness/style. Press the **Select Color (색상 선택)** button and click one of the 10 recommended colors or 48 palette colors, or choose **Custom Color… (사용자 지정 색상…)**. Selecting a background color automatically turns on fill; unchecking **Fill Background (배경색 채우기)** turns fill off. Apply with **Apply Properties** after editing. The color is also applied to the inside background of groups/tabs, and child tools keep their own colors.
- Recommended colors are shown with a swatch and a name such as **색 파란색** (Color: Blue); other colors are shown as **색 #XXXXXX**. The HEX value can also be seen in the tooltip.
- Changing an existing tool's color/style on the right or in **View in Window… (창으로 보기…)** shows an immediate preview on screen. It is confirmed only with **Apply Properties / OK**; unconfirmed colors are reverted on selecting another tool, clicking/shortcuts outside the edit area, mode switching, tool refresh, or closing. Previews are not included in file/auto-save or undo history. The palette, custom color window, and tab navigation inside the settings let you continue editing.
- The top-level canvas and the inside of groups/tabs use the same grid cell size. Widening the window increases the number of placeable cells, and the size of existing tools is kept.
- The default and minimum size of general tools is **Row Span 3 × Col Span 6**. Smaller tools in existing panels are also adjusted to this size on load. Larger minimum sizes of sliders and sequences are kept, and line shapes allow 1 to preserve thickness.

### Conditional Colors for RX Labels

- Select an RX `label` tool and open **Color / Condition → Edit Color Rule Table… (색상 / 조건 → 색상 규칙 표 편집…)**. Each row of the table is one rule, configured in the **Use / Basis / Condition / Value·min·Enum / max / Target / Color (사용 / 기준 / 조건 / 값·min·Enum / max / 적용 대상 / 색상)** columns. When there are many rows, the table scrolls internally so the settings window does not grow longer.
- If the signal has Enums, select **Enum** or **Physical value (물리값)**. **Add All Enums (Enum 일괄 추가)** adds each Enum of the current signal as its own row, after which you can change the color per row. Signals without Enums use physical value conditions.
- Enum conditions are compared against the DBC **RAW value**. Physical value conditions are compared against the **value with Scale/Offset applied**. Even if the DBC is not found, saved Enum RAW rules are preserved and applied according to the saved bit/Scale/Offset settings.
- The comparison conditions are **less than / less than or equal / equal / not equal / greater than / greater than or equal / within range / outside range**. Single comparisons use only the first value. Range comparisons take the first value (min) and max, and min ≤ max is required. Within range is **min ≤ value ≤ max**; outside range is **value < min or value > max**.
- The target is **Background color (배경색)** or **Text color (글자색)**. The color button offers the existing **10 recommended colors / 48 palette colors / custom color**. If only a background color is specified, the text color is adjusted automatically according to brightness.
- If multiple rows match, the **first match from the top is applied separately for background and text color**. Change priority with **Up / Down**, and uncheck a row to exclude it. Targets with no match revert to the default color.
- OK in the rule table applies to the tool settings being entered. Finally, press **Apply Properties** (right panel) or **OK** (settings window) to apply. Applied rules are saved in the panel and unified settings and support undo/redo. You can also check them with **Display Test (표시 테스트)** in EDIT.
- While changing colors in the rule table, the color matching the current received value or **Display Test** value is previewed immediately. Canceling the rule table reverts to the colors before the table was opened, and moving on to another action before the final Apply reverts to the last confirmed colors.

### Undo / Redo

- In EDIT mode, **Ctrl+Z** undoes and **Ctrl+Y** redoes. The Undo/Redo items in the Edit menu also show the number of steps currently available.
- Creation, deletion, position, size, stacking order, and applied tool properties (including sequence settings) are kept for up to **50 steps**. Multi-deletion and bulk property application each count as one step, and previews in the creation window are grouped into one step when creation is confirmed.
- Applying a new action after undo clears the redo history. Loading a panel file or closing the window resets the history, and history is not saved to the file.
- CAN transmission/reception, device connection state, and the actual BUS communication speed are not reverted. Communication speed is changed separately in CAN BUS Speed.
- In property input fields, Ctrl+Z reverts the text being typed. To undo panel operations, put focus on the canvas/tool list or use the Edit menu.

### Conflict Check
- **Check TX Overlap**: Checks for bit overlaps within the same CAN frame
- Overlapping tools are shown in red in the Tool List
- **Focus Conflict** or double-clicking an item navigates between conflicting tools

### Save/Load
- **File → Save Panel**: Saves the panel settings and per-BUS DBC/SYM into a single `.pjjupkg` file. If the extension is omitted, `.pjjupkg` is appended.
- **File → Load Panel**: Select a `.pjjupkg` file to restore the panel together with the included DBs. After loading, the panel is in STANDARD/idle state.
- **Save** also includes sequence order, names, packets, comparison masks, timeouts, delays, and repeat settings. Logs and execution progress are not saved.
- Panel package save: `*.pjjupkg`
  - Saves panel tool settings + per-Bus DB files (dbc/sym) as a bundle
  - On package load, the existing DB list is replaced and then the panel is restored
- The user save/load format has been unified to `.pjjupkg`. By moving a single package file, you can use the panel together with its DBC/SYM on another PC.

### Tool Layout Page Tabs

- In edit mode, press **+ Tab (+ 탭)** above the layout area and enter a name to create a new page. You can rename it with the **Rename Tab (탭 이름 변경)** button or by double-clicking the tab name.
- The **Delete Tab (탭 삭제)** button deletes the current page and all its tools after confirmation. The last page cannot be deleted, and Ctrl+Z restores the page together with its tools.
- New tools are placed on the current tab. The tool list and group parent selection are also shown based on the current tab. To copy a tool to another tab, press Ctrl+C and then Ctrl+V on the target tab.
- Switching tabs does not reset tool values or running sequences. Registered packet transmission and reception processing in RUN continue regardless of the displayed tab. Tabs can be switched in RUN/Standard, but adding, renaming, and deleting are possible only in edit mode.
- Tab names, member tools, and the selected tab are saved together in the JSON and panel package. Existing panel files without tab information are loaded with all tools placed on **Page 1 (페이지 1)**. Adding, renaming, and deleting tabs support Undo/Redo. Grid size, registered packets, and the Init sequence are shared across the whole panel.

### Tabs Inside a Tool

- Create a tab tool with **Add Group/Shape → tab_container**. Edit tab add/rename/delete in **Tab Settings (탭 설정)** in the creation window or in the selected tool's properties, then apply with **OK / Apply Properties**. You can also rename a tab by double-clicking its name in the list.
- At least one internal tab is kept. When a tab is deleted, its tools move to the next tab; deleting the last tab moves them to the previous tab. Tools on other tabs stay on their original tabs. Applied changes support Undo/Redo and save/load.
- In a child tool's **Parent Group/Tab**, select a tab tool and then specify the **Tab to Place In (배치할 탭)**. When dropping by drag, the tool is placed in the currently displayed internal tab.

### Editing Signals Without DBC

- In the CMD of Init/regular/failure-handling sequences, turning on **Use CMD-specific Value → Direct HEX / BIN / X Input (CMD 전용 값 사용 → 직접 HEX / BIN / X 입력)** lets you enter raw values without selecting signals. In RCV, comparison values are also specified with **Direct HEX / BIN / X Input**.
- HEX example: `01 02 03 04 05 06 XX 08`; BIN example: `0010 0001 1100 XXXX`. The leftmost is the first byte, and BIN is entered from the most significant bit. One X in HEX is 4 bits; one X in BIN is 1 bit. Input is byte-based, and omitted trailing bytes are also treated as X.
- In CMD, X keeps the current bits of the registered packet. After the specified bits are overwritten, active count/CRC are applied, and the final successfully sent value is kept. In RCV, X is excluded from comparison. An RCV that is entirely X cannot be registered.
- **Allow RX DLC Difference (수신 DLC 차이 허용)** is off by default. When on, with a DLC 8 setting and a comparison value of `11 22` or `11 22 XX XX`, an actual received `11 22 CC FF` is also OK. If a byte specified for comparison is not present in the actual received frame, it does not pass, and BUS, ID, Standard/Extended, FD, and BRS conditions are still checked.
- Direct input values, bit masks, input format, and the DLC difference setting are saved in the panel. Sending and comparing these raw values do not require a DBC.

- Start Bit, Bit Length, Byte Order, Scale, Offset, and Signed settings are saved in the panel. Values are sent and received using this information even without a DBC. Integer signals in Intel (little-endian) and Motorola (big-endian) are supported.
- Selecting a DBC Message/Signal fills in Start Bit, Length, etc. automatically. Both fields remain visible and editable while using a DBC.
- If there is a signal matching the saved bit information and the current BUS, CAN ID, and Byte Order, the DBC signal is linked. If the DBC is missing or has changed and does not match, it is shown as **Unknown (Manual)** and the saved bit information is kept.
- Editing Start Bit/Length directly re-matches the signal. If multiple signals share the same position and cannot be distinguished by the existing selection, it is shown as Unknown, and you can select the desired signal manually.
- Bit ranges outside the packet length, invalid CAN IDs/frame lengths, and Scale 0 are not applied.

## 7. Run, Build, and Version

The version is set by `APP_VERSION = "R013"` in `build_exe.py`.

- Running from source: after installing dependencies and activating the virtual environment, run `python main.py`. Version changes are reflected on restart.
- Build: `python build_exe.py` in the same environment.
- Windows executable: `dist/CAN_Studio_JJ_R013_win/CAN_Studio_JJ_R013_win.exe` (viewer-only build: `CAN_Studio_Viewer_JJ_R013_win`).
- Linux binary: `dist/CAN_Studio_JJ_R013_linux/CAN_Studio_JJ_R013_linux`.
- The title reads the version from the executable file name. Modifying the source does not change existing executables, so you must rebuild R013.
- The user manual, sequence usage guide, change history, and per-OS run instructions are copied to the distribution folder.
- The default font is Consolas. In environments where it is not installed, or for unsupported characters such as Korean, a system fallback font may be shown.
- Linux sessions start with BUSes Closed regardless of the previous connection state. Development tests are run on Python 3.13 with `python -B -m unittest discover -s tests`; real CAN hardware and per-OS build results must be verified separately.


## 8. Diagnostic and Analysis Tools (R013)

The new features add only one button or column each while keeping the existing screen layout. Hovering over any button or column header shows a description.

### Bus Load · Error Frames · Error Counters
- The **State** field of a connected BUS shows `부하 %` (load %), frames per second (`fps`), and the number of error frames received since connecting (`Err`), updated every second. The same values are also shown in the User Panel's communication settings window.
- `Err` is the **cumulative number of error frames** received since connecting. When error frames occur, `오류 프레임 N개/초 · 원인` (N error frames/s · cause) is logged to the **…** BUS log every second. For PCAN, the error type, direction, and TEC/REC are decoded; typically, **No ACK (ACK 없음)** occurs when there is no node to acknowledge (the other BUS not connected or the device removed) or the Baudrate differs. In that case the transmitting side may record thousands of error frames per second, which stop as soon as the other node is connected.
- **TX cycle delay (TX 주기 지연)** (a cycle skipped due to Windows/USB latency) is recorded only in the BUS log, and the State field keeps showing the connection state. Repeated messages of the same kind are merged into one line in recent records and shown as `(반복 N회)` (repeated N times).
- The bus load is an **estimate** calculated from the length and bitrate of frames reported to this program (received + TX sent by this program). Bit stuffing is approximated by an average, so it may differ slightly from the device's own load measurement.
- Controller error counters (`TEC / REC`) are prepared to be shown only when the device reports them. Currently supported devices (PCAN, Kvaser USBcan Light 4xHS) do not report the counters, so they are not shown.
- Like PCAN, Kvaser also disconnects when it detects **Bus-Off**, and shows Error Warning/Passive as warnings.

### Vector ASC / BLF Logs
- In **Log View** (File → Open CAN Log (파일 → CAN 로그 열기)), you can open Vector `.asc` and `.blf` in addition to `.trc` and CL2000 `.txt`. Channels 1–4 are shown as BUS 1–4. Error frames and remote frames are excluded.
- **SAVE** in the LOG tab saves the filter results in the same format as the opened file.
- In **Save** of the **Record** window, you can choose the file format from TRC / Vector ASC / Vector BLF. Auto-save when closing the window with X or exiting the program is TRC as before.

### Trigger Recording (RX Trigger Column)
- Click the **Trigger** cell of a signal row in the RX section (shown in gray as `＋ 설정` (+ Set)), or right-click → **Trigger Settings… (트리거 설정…)**.
- Clicking a registered trigger cell cycles through **Watching → Stopped → Settings window**. In the stopped state, pressing **OK (확인)** in the settings window resumes watching; canceling keeps it stopped. You can also use right-click → **Stop Trigger / Start Watching (트리거 정지/감시 시작)**.
- The Trigger column has a fixed width; widening the window expands the **Value** column.
- The **Trigger column header** shows the trigger state: `Trigger` (none), green dot `Trigger · 감시` (watching), red dot `Trigger · 저장 중` (saving), yellow dot `Trigger · 정지` (stopped). You can check the state in the header even after clearing the RX section.
- Hovering over the header shows the triggered signal (BUS, ID, message, signal), the condition, and the occurrence/save counts. Clicking the header does not sort; **right-clicking** opens the trigger settings/stop/start watching/clear menu.
- Set the condition (`>`, `>=`, `<`, `<=`, `==`, `!=`), the threshold value (physical value), and the **pre/post-trigger save time (1–2 seconds)**.
- The trigger fires at the moment the condition changes from **false → true**. While the condition stays true, it does not fire again.
- **Frames from all BUSes** from the pre-trigger time to the post-trigger time are saved as `Trigger_날짜_시간_신호.trc` (Trigger_date_time_signal.trc) in the `Log` folder next to the executable.
- If it triggers again while saving (waiting for the post-trigger time), the post-trigger time is counted again from that point. To prevent excessive saving, one file is limited to 60 seconds.
- Only **one** trigger can be registered. Setting a trigger on another signal asks whether to replace the existing trigger. Clear it with right-click → **Clear Trigger (트리거 해제)** or CAN menu → Clear Trigger.
- The Trigger cell shows the condition and state (`감시` (watching) · `정지` (stopped) · `저장 중` (saving) · number of saves), and save results are recorded in the status bar and that BUS's log (**…**). Trigger settings and the stopped state are saved in the unified settings.

### BUS Gateway (GW Button)
- Add rules with the **GW** button in the RX section or CAN menu → **BUS Gateway… (BUS 게이트웨이…)**.
- Rule: **From BUS → To BUS**, action (**Forward all / Forward listed IDs only / Block listed IDs (전체 전달 / 지정 ID만 전달 / 지정 ID 차단)**), ID list (hex, e.g. `100-1FF, 321`), ID translation (e.g. `100=200`).
- Forwarding starts only after you check **Enable Gateway (게이트웨이 사용)** and press **Apply (적용)**. While active, the button shows `GW ON`. Rules are saved in the unified settings, but for safety the gateway is **always off** when the program starts.
- Only **frames received from the device** are forwarded. TX sent by this program is not forwarded, so using BUS 1→2 and 2→1 rules together does not create a loop.
- Forwarded frames are shown/recorded as TX of the To BUS in the RX section, Record, and trigger. Both BUSes must be connected, and CAN FD frames can be sent only if the To BUS is also connected as FD. Forwarded/failed counts are shown in the gateway window, and failures are recorded in the BUS log.

### Signal Statistics · RX Timeout Alert (Stats Button)
- In the **Stats** window, check the current, minimum, maximum, average, and sample count of received (RX) DBC signals. **Reset** clears only the statistics. They are also reset on Clear Data and reconnect.
- **RX timeout alert**: If an RX message is not received for `cycle × multiplier` (default 3×, minimum 20 ms), the message name and Cycle are shown in red in the RX section and recorded in the status bar and BUS log. When reception resumes, they return to normal and `수신 재개` (reception resumed) is logged.
- The cycle uses the DBC `GenMsgCycleTime` first. If absent, depending on the **Use Measured Cycle (측정 주기 사용)** option, the average cycle after 5 receptions is used (when unchecked, only messages with a DBC cycle are monitored).
- The timeout is **alert only**. It does not affect transmission/reception, connections, or panel operation, and does not change the connection state in the State field.

### CSV Export (Log View)
- The **CSV** button in the log viewer's DBC tab saves decoded signals as a time-axis table (`Time`, `Offset (s)`, one column per signal). Only checked signals are saved; if none are checked, all signals are saved.
- In the format option of the save dialog, choose **Sample-time values only (샘플 시점 값만)** (recorded only at times when a value arrived) or **Fill with previous value (직전 값 채움)** (empty cells filled with the previous value). It is saved as UTF-8 (BOM) so that Korean text does not break in Excel.

