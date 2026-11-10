# User Panel Acceptance Checklist

## 1) Panel Open / Mode
- [ ] Open User Panel from Tx toolbar.
- [ ] Mode transitions work: EDIT -> STANDBY -> RUN.
- [ ] If password lock is enabled, STANDBY/RUN -> EDIT asks password.
- [ ] Wrong password blocks EDIT mode.

## 2) Tool Create / Edit / Delete
- [ ] Add TX Tool creates a TX widget.
- [ ] Add RX Tool creates an RX widget.
- [ ] Add Group/Shape creates visual tools.
- [ ] Edit updates title/binding/layout.
- [ ] Delete removes selected tool from canvas and list.

## 3) TX Tool Behavior
- [ ] Button: press repeats push value TX, release sends pull value once.
- [ ] Toggle: ON/OFF values send correctly.
- [ ] Slider/Spinbox: Min/Max/Resolution reflected.
- [ ] TX cycle mode works:
  - [ ] immediate
  - [ ] fixed
  - [ ] dbc fallback
  - [ ] fastest

## 4) Same CAN ID Merge / Frame Policy
- [ ] Multiple TX tools on same (Bus, CAN ID, DLC) are staged into same frame cache.
- [ ] Frame flush sends merged payload.
- [ ] Overlap check reports conflicts for overlapping bit ranges.

## 5) RX Tool Behavior
- [ ] Label updates value.
- [ ] Progress maps value into bar range.
- [ ] Status lamp follows ON/OFF condition operators.

## 6) No-CAN Simulator
- [ ] Apply Selected RX updates selected RX tool.
- [ ] Apply All RX updates all RX tools.
- [ ] Auto Sim runs only in STANDBY/RUN.
- [ ] Auto Sim stops in EDIT mode.

## 7) Layout / Grouping
- [ ] Tool drag move works in EDIT.
- [ ] Bottom-right drag resizes span.
- [ ] W+/W-/H+/H- controls span.
- [ ] Group/Tab parent assignment works.
- [ ] Drag-drop onto Group/Tab auto re-parenting works.
- [ ] Selected Tool Geometry edits row/col/spans directly.

## 8) Z-Order / Navigation
- [ ] Bring Front / Send Back / Forward / Backward work.
- [ ] Tool list context menu actions work.
- [ ] Focus Conflict cycles between overlap targets.
- [ ] Overlap tools are highlighted in red in list.

## 9) Save / Load
- [ ] Save/Load panel JSON (*.upp.json) restores tools and layout.
- [ ] Save/Load package (*.pjjupkg) restores panel + DB files.
- [ ] Loading package replaces existing DB list.

## 10) Known Limits
- [ ] Big-endian fallback encoding is not yet supported.
- [ ] Validate behavior with real CAN hardware before release.
