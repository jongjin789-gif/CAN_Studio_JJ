<#
candleLight (gs_usb) USB-CAN 어댑터를 WinUSB 드라이버로 전환합니다.

장치(VID 1D50 / PID 606F)의 CAN 인터페이스(Interface 0)는 스스로 WinUSB 사용을 요청합니다
(MS_COMP_WINUSB). 그런데 Zadig 등으로 시리얼(usbser) 드라이버가 설치되면 COM 포트로 잡혀
CAN 통신을 할 수 없습니다. 이 스크립트는 그 잘못된 드라이버 패키지만 제거하고 장치를 다시 검색해
Windows 기본 WinUSB 드라이버가 자동으로 붙게 합니다. 외부 드라이버는 설치하지 않습니다.

사용: 같은 폴더의 Install_candleLight_WinUSB.bat 을 더블클릭하세요(관리자 권한 자동 요청).
점검만 하려면: powershell -ExecutionPolicy Bypass -File install_candleLight_winusb.ps1 -DryRun
#>
param([switch]$DryRun)

$ErrorActionPreference = 'Stop'
$VidPid = 'VID_1D50&PID_606F'
$CanInterface = "USB\$VidPid&MI_00\*"

function Write-Step($text) { Write-Host "`n== $text" -ForegroundColor Cyan }
function Write-Ok($text) { Write-Host "[완료] $text" -ForegroundColor Green }
function Write-Warn2($text) { Write-Host "[주의] $text" -ForegroundColor Yellow }
function Write-Err($text) { Write-Host "[오류] $text" -ForegroundColor Red }

function Get-Service-Of($instanceId) {
    try { return (Get-PnpDeviceProperty -InstanceId $instanceId -KeyName 'DEVPKEY_Device_Service').Data }
    catch { return $null }
}

function Get-Inf-Of($instanceId) {
    try { return (Get-PnpDeviceProperty -InstanceId $instanceId -KeyName 'DEVPKEY_Device_DriverInfPath').Data }
    catch { return $null }
}

Write-Host '============================================================'
Write-Host ' candleLight USB-CAN 어댑터 WinUSB 드라이버 전환'
if ($DryRun) { Write-Host ' (점검 모드: 아무것도 변경하지 않습니다)' -ForegroundColor Yellow }
Write-Host '============================================================'

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
           ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin -and -not $DryRun) {
    Write-Err '관리자 권한이 필요합니다. Install_candleLight_WinUSB.bat 으로 실행하세요.'
    exit 1
}

Write-Step '1. 장치 찾기'
$devices = @(Get-PnpDevice -PresentOnly -ErrorAction SilentlyContinue |
             Where-Object { $_.InstanceId -like $CanInterface })
if ($devices.Count -eq 0) {
    Write-Err 'candleLight 어댑터(VID 1D50 / PID 606F)를 찾지 못했습니다. USB에 꽂은 뒤 다시 실행하세요.'
    exit 2
}

$failed = $false
$pending = $false
foreach ($dev in $devices) {
    $id = $dev.InstanceId
    $service = Get-Service-Of $id
    $inf = Get-Inf-Of $id
    Write-Host "장치: $($dev.FriendlyName)"
    Write-Host "  ID: $id"
    Write-Host "  현재 드라이버: $service ($inf)"

    if ($service -eq 'WinUSB') {
        Write-Ok '이미 WinUSB 드라이버를 사용 중입니다. 변경할 것이 없습니다.'
        continue
    }

    Write-Step '2. 잘못 설치된 드라이버 확인'
    if (-not $inf -or $inf -notmatch '^oem\d+\.inf$') {
        Write-Err "드라이버 패키지($inf)가 Windows 기본 드라이버라 자동으로 바꿀 수 없습니다."
        Write-Host '       Zadig에서 Interface 0 을 WinUSB 로 직접 바꿔 주세요(README.txt 참고).'
        $failed = $true
        continue
    }

    # 같은 드라이버 패키지를 다른 장치가 쓰고 있으면 지우지 않습니다(안전 장치).
    $users = @(Get-CimInstance Win32_PnPSignedDriver -ErrorAction SilentlyContinue |
               Where-Object { $_.InfName -eq $inf })
    $others = @($users | Where-Object { $_.DeviceID -notlike "USB\$VidPid*" })
    if ($others.Count -gt 0) {
        Write-Err "$inf 드라이버를 다른 장치도 사용 중이라 제거하지 않습니다:"
        $others | ForEach-Object { Write-Host "       - $($_.DeviceName) [$($_.DeviceID)]" }
        $failed = $true
        continue
    }
    Write-Ok "$inf 는 이 어댑터 전용 드라이버입니다. 제거해도 다른 장치에 영향이 없습니다."

    if ($DryRun) {
        Write-Warn2 "점검 모드: 실제 실행 시 'pnputil /delete-driver $inf /uninstall /force' 후 장치를 다시 검색합니다."
        $pending = $true
        continue
    }

    Write-Step "3. 시리얼 드라이버($inf) 제거"
    & pnputil.exe /delete-driver $inf /uninstall /force
    if ($LASTEXITCODE -ne 0) {
        Write-Err "드라이버 제거에 실패했습니다(pnputil 종료 코드 $LASTEXITCODE)."
        $failed = $true
        continue
    }

    Write-Step '4. 장치 다시 검색 (WinUSB 자동 적용)'
    & pnputil.exe /scan-devices | Out-Null
    Start-Sleep -Seconds 3
    $service = Get-Service-Of $id
    if ($service -ne 'WinUSB') {
        # 장치를 목록에서 지우고 다시 검색하면 새 드라이버를 고릅니다.
        & pnputil.exe /remove-device $id | Out-Null
        & pnputil.exe /scan-devices | Out-Null
        Start-Sleep -Seconds 3
        $service = Get-Service-Of $id
    }
    if ($service -eq 'WinUSB') {
        Write-Ok 'WinUSB 드라이버로 전환되었습니다. COM 포트는 더 이상 표시되지 않습니다.'
    } else {
        Write-Warn2 "아직 WinUSB 로 확인되지 않습니다(현재: $service). USB를 뽑았다가 다시 꽂은 뒤 이 파일을 한 번 더 실행하세요."
        $failed = $true
    }
}

Write-Host ''
if ($failed) {
    Write-Warn2 '일부 단계가 완료되지 않았습니다. 위 메시지를 확인하세요.'
    exit 3
}
if ($pending) {
    Write-Warn2 '점검 모드로 실행해 변경하지 않았습니다. 전환하려면 Install_candleLight_WinUSB.bat 을 더블클릭하세요.'
    exit 0
}
Write-Ok '모든 candleLight 어댑터가 WinUSB 를 사용합니다. 장치 관리자의 "범용 직렬 버스 장치"에서 확인할 수 있습니다.'
exit 0
