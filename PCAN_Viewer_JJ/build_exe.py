import PyInstaller.__main__
import os
import platform
import sys
import json
import importlib.util
import shutil
import stat
import time

try:
    from PyInstaller.utils.hooks import collect_submodules
except ImportError:
    collect_submodules = None

# =====================================================================
# 소프트웨어 버전 설정 (빌드 시 생성되는 실행 파일명에 반영됩니다)
# =====================================================================
APP_VERSION = "R012_1"

def _collect_hidden_imports():
    """
    python-can은 CAN 인터페이스 모듈을 런타임에 동적으로 import하므로, PyInstaller가
    이를 자동으로 찾지 못하는 경우가 많습니다. 이 함수는 현재 환경에 설치된 python-can
    인터페이스들을 최대한 수집하여 빌드에 포함시켜 'Module not found' 오류를 방지합니다.
    """
    # 1. 필수 및 기본 모듈 추가
    hidden_imports = {
        "PyQt5.QtSvg",
        "cantools",
        "can.interfaces",
    }

    # 2. PyInstaller의 유틸리티를 사용하여 'can.interfaces' 및 그 하위의 모든 모듈을 수집
    #    Linux에서 'socketcan' 관련 모듈 누락 오류를 해결하기 위해 'can.interfaces.socketcan'도 명시적으로 수집합니다.
    if collect_submodules:
        packages_to_scan = ['can.interfaces']
        # socketcan은 Linux 전용이므로, Windows/Mac에서 불필요한 오류 메시지가 표시되는 것을 막기 위해 Linux에서만 스캔합니다.
        if platform.system() == 'Linux':
            packages_to_scan.append('can.interfaces.socketcan')

        for package_name in packages_to_scan:
            try:
                hidden_imports.update(collect_submodules(package_name))
                print(f"[안내] '{package_name}' 하위 모듈 자동 수집 완료.")
            except Exception as exc:
                print(f"[경고] '{package_name}' 하위 모듈 자동 수집 실패: {exc}")
    
    # 3. 자동 수집이 실패하거나, 구버전 PyInstaller를 위해 주요 인터페이스를 수동으로 확인하여 추가
    potential_imports = [
        "can.interfaces.pcan",
        "can.interfaces.virtual",
        "can.interfaces.vector",
        "can.interfaces.kvaser",
        "can.interfaces.ixxat",
        "can.interfaces.slcan",
        "can.interfaces.gs_usb",
    ]
    if platform.system() == 'Linux':
        potential_imports.append("can.interfaces.socketcan")

    for module_name in potential_imports:
        # Windows에서 socketcan을 찾지 못해도 오류가 아니므로 find_spec으로 존재 여부만 확인
        if importlib.util.find_spec(module_name):
            hidden_imports.add(module_name)

    return sorted(list(hidden_imports))

def robust_rmtree(path, max_retries=10, delay=1.0):
    """
    shutil.rmtree의 Windows 권한 오류(PermissionError)를 회피하기 위해,
    재시도 후 최종적으로 폴더 이름 변경을 시도하는 강화된 버전입니다.
    """
    if not os.path.exists(path):
        return

    def _onerror(func, target, exc_info):
        exc_type, exc_value, _ = exc_info
        if not os.access(target, os.W_OK):
            os.chmod(target, stat.S_IWRITE)
            func(target)
        elif isinstance(exc_value, PermissionError):
            os.chmod(target, stat.S_IWRITE)
            func(target)
        else:
            raise exc_value

    for i in range(max_retries):
        try:
            shutil.rmtree(path, onerror=_onerror)
            return # 성공 시 즉시 종료
        except OSError as e:
            if i < max_retries - 1:
                print(f"[경고] '{os.path.basename(path)}' 폴더 삭제 실패 (오류: {e}). {delay}초 후 재시도합니다... ({i+1}/{max_retries-1})")
                time.sleep(delay)
            else:
                # 모든 재시도 실패 시, 마지막 수단으로 폴더 이름 변경 시도
                try:
                    new_name = f"{path}_todelete_{int(time.time())}"
                    os.rename(path, new_name)
                    print(f"[경고] 폴더를 삭제할 수 없어 '{os.path.basename(new_name)}'(으)로 이름을 변경했습니다. 빌드는 계속됩니다.")
                    return # 이름 변경 성공 시, 빌드 계속 진행
                except OSError as rename_error:
                    print(f"[오류] '{os.path.basename(path)}' 폴더를 삭제하거나 이름을 변경하지 못했습니다. (Rename Error: {rename_error})")
                    raise e # 이름 변경도 실패하면, 원래의 오류를 발생시켜 빌드 중단

def build_executable():
    current_dir = os.path.dirname(os.path.abspath(__file__))
    
    # 타겟 스크립트를 분리된 구조에 맞게 main.py로 변경
    target_script = os.path.join(current_dir, 'main.py')
    icon_path = os.path.join(current_dir, 'icon', 'viewer.ico')
    
    # PCAN_Viewer_JJ.pk에서 뷰어 모드 여부 파악
    is_viewer_mode = False
    pk_path = os.path.join(current_dir, "PCAN_Viewer_JJ.pk")
    if os.path.exists(pk_path):
        try:
            with open(pk_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if isinstance(data, dict) and data.get("viewer_mode_only"):
                    is_viewer_mode = True
        except Exception:
            pass
                
    app_prefix = "PCAN_Log_Viewer_JJ" if is_viewer_mode else "PCAN_Viewer_JJ"
    
    icon_dir = os.path.join(current_dir, 'icon')
    current_os = platform.system()
    if current_os == 'Windows':
        os_suffix = "_win"
        pcan_lib = os.path.join(current_dir, 'src', 'PCANBasic.dll')
        binary_option = f'--add-binary={pcan_lib};.' 
        data_option = f'--add-data={icon_dir};icon'
    elif current_os == 'Linux':
        os_suffix = "_linux"
        pcan_lib = os.path.join(current_dir, 'src', 'libpcanbasic.so')
        binary_option = f'--add-binary={pcan_lib}:.' 
        data_option = f'--add-data={icon_dir}:icon'
    else:
        os_suffix = "_mac"
        pcan_lib = os.path.join(current_dir, 'src', 'libPCBUSB.dylib')
        binary_option = f'--add-binary={pcan_lib}:.'
        data_option = f'--add-data={icon_dir}:icon'

    print("=== 실행 파일 빌드를 시작합니다 ===")
    
    dist_path = os.path.join(current_dir, 'dist', f'{app_prefix}_{APP_VERSION}{os_suffix}')
    work_path = os.path.join(current_dir, 'build', f'{app_prefix}_{APP_VERSION}{os_suffix}')
    
    # PyInstaller의 --clean 옵션은 Windows에서 파일 잠금으로 인한 권한 오류를 자주 일으킵니다.
    # 이를 방지하기 위해, 빌드 시작 전에 수동으로 build 및 dist 폴더를 삭제합니다.
    print("[안내] 이전 빌드/배포 폴더를 정리합니다...")
    try:
        robust_rmtree(work_path)
        robust_rmtree(dist_path)
    except Exception as e:
        print(f"[오류] 폴더 정리 중 심각한 오류 발생: {e}")
        if platform.system() == 'Linux' and isinstance(e, PermissionError):
            error_msg = "\n[Linux 사용자 안내]\n이전에 'sudo'를 사용하여 빌드를 실행한 경우, 생성된 폴더/파일의 소유자가 root로 되어있을 수 있습니다."
            error_msg += "\n이 경우, 일반 사용자 권한으로는 삭제할 수 없습니다."
            error_msg += "\n터미널에서 아래 명령어를 실행하여 폴더를 직접 삭제한 후 다시 시도해 보세요:"
            error_msg += f"\n<code>sudo rm -rf \"{work_path}\" \"{dist_path}\"</code>"
        else:
            error_msg = "다른 프로그램(예: 파일 탐색기, 이전 실행 파일, 바이러스 백신)이 해당 폴더/파일을 사용하고 있는지 확인해주세요."
            if platform.system() == 'Windows':
                error_msg += "\n- VS Code 등 IDE의 터미널에서 실행 중이라면, IDE를 완전히 종료하고 외부 터미널(PowerShell, CMD)에서 다시 시도해보세요."
                error_msg += "\n- 또는, 현재 터미널을 '관리자 권한으로 실행'한 후 다시 시도해보세요."
        print(error_msg)
        sys.exit(1)

    build_args = [
        target_script,
        f'--name={app_prefix}_{APP_VERSION}{os_suffix}',
        '--onefile',       
        '--noconsole',
        f'--paths={current_dir}', # 패키지 인식을 위해 루트 디렉토리 추가
        f'--distpath={dist_path}',
        f'--workpath={work_path}',
        f'--specpath={work_path}',
        # '--clean',  # Windows에서 PermissionError를 유발할 수 있어 수동 처리로 대체함
    ]
    
    # python-can 동적 인터페이스 모듈 포함
    for module_name in _collect_hidden_imports():
        build_args.append(f"--hidden-import={module_name}")

    # 아이콘 폴더/파일이 있을 때만 포함 (빌드 오류 방지)
    if os.path.isdir(icon_dir):
        build_args.append(data_option)
    else:
        print(f"[안내] icon 폴더가 없어 add-data에서 제외합니다: {icon_dir}")

    if os.path.isfile(icon_path):
        build_args.append(f'--icon={icon_path}')
    else:
        print(f"[안내] 아이콘 파일이 없어 icon 옵션에서 제외합니다: {icon_path}")

    # OS별 라이브러리(dll, so) 필수 포함 여부 분기 처리
    if current_os == 'Windows':
        if not os.path.exists(pcan_lib):
            print(f"\n[에러] 윈도우 빌드에는 {os.path.basename(pcan_lib)} 파일이 필수입니다!")
            print("빌드를 강제 중단합니다. src 폴더에 DLL 파일을 추가한 후 다시 시도해주세요.\n")
            sys.exit(1)
        build_args.append(binary_option)
    else:
        if os.path.exists(pcan_lib):
            build_args.append(binary_option)
        else:
            print(f"\n[안내] {os.path.basename(pcan_lib)} 라이브러리가 없어 빌드에서 제외합니다.")
            print(f"({current_os} 환경은 SocketCAN을 기본으로 사용하므로 해당 파일 없이도 정상 동작합니다.)\n")
            
    PyInstaller.__main__.run(build_args)
    
    # 배포 모드(viewer_mode_only/user_panel_only) 전환용 설정 파일을 exe 옆에 배치
    # (세션 매니저가 있으면 앱이 자동 생성하지 않으므로 빌드 시 직접 복사/생성한다)
    dist_pk_path = os.path.join(dist_path, 'PCAN_Viewer_JJ.pk')
    if os.path.exists(pk_path):
        shutil.copy2(pk_path, dist_pk_path)
    else:
        with open(dist_pk_path, 'w', encoding='utf-8') as f:
            json.dump({"viewer_mode_only": False, "user_panel_only": False}, f, indent=4)
    print(f"[안내] PCAN_Viewer_JJ.pk 파일이 배포 폴더에 생성되었습니다.")

    # 배포 파일과 함께 최신 사용법 및 변경 내역 제공
    for document in ('MANUAL.md', 'USER_PANEL_SEQUENCE.md', 'USER_PANEL_RELEASE_NOTES.md'):
        document_path = os.path.join(current_dir, document)
        if os.path.exists(document_path):
            shutil.copy2(document_path, os.path.join(dist_path, document))
            print(f"[안내] {document} 파일이 배포 폴더에 복사되었습니다.")

    
    # OS별 배포용 README 파일(실행 가이드) 자동 생성
    if current_os == 'Linux':
        readme_path = os.path.join(dist_path, 'README_Linux_실행_가이드.md')
        readme_content = f"""# {app_prefix.replace('_', ' ')} - 리눅스 환경 실행 가이드

이 프로그램은 파이썬 가상환경(venv)이나 추가 라이브러리 설치 없이 바로 실행 가능한 독립 바이너리 파일입니다.
단, 리눅스의 디스플레이(GUI) 출력 및 SocketCAN 제어를 위해 타겟 PC에 최소한의 OS 필수 패키지가 설치되어 있어야 합니다.

## 🛠️ 1. 필수 시스템 패키지 설치 (최초 1회)
프로그램을 실행할 리눅스 PC(Ubuntu/Debian 기준)의 터미널을 열고 아래 명령어를 복사하여 실행해 주세요.

```bash
sudo apt-get update
sudo apt-get install -y can-utils iproute2 \
    libxcb-cursor0 libxcb-xinerama0 libxcb-icccm4 libxcb-image0 \
    libxcb-keysyms1 libxcb-randr0 libxcb-render-util0 libxcb-xfixes0 \
    libxkbcommon-x11-0 libqt5x11extras5 fonts-nanum libopengl0
```

## 🚀 2. 실행 방법
```bash
chmod +x {app_prefix}_{APP_VERSION}_linux
./{app_prefix}_{APP_VERSION}_linux
```

## ⚠️ 3. (중요) 실제 CAN 하드웨어 사용 시
일반 사용자로 프로그램을 실행하세요. 인터페이스 설정에 권한이 필요한 경우에만 앱에서 sudo 비밀번호를 묻습니다.
비밀번호는 현재 실행 중에만 사용하며 파일이나 로그에 저장하지 않습니다. CAN 세션은 이전 저장 상태와 관계없이 Close로 시작합니다.

## {APP_VERSION} 기능 및 사용 안내
- User Panel: Add TX Tool → sequence → 명령 시퀀스 설정에서 CMD/RCV/DEL을 등록합니다.
- DBC 없이 저장된 CMD 데이터와 RCV 비트 조건을 수정·실행할 수 있습니다.
- sequence Force는 RCV를 건너뛰고, Packet Control은 CMD/RCV를 제외해 START/STOP/DEL만 실행합니다. 전역 Force RUN 모드는 없습니다.
- TX counter에서 최신 RX DBC raw값 동기화와 업/다운/왕복 fallback을 선택할 수 있습니다.
- 실시간 그래프: Reset Zoom(최근 30초), Auto Y(표시 중인 구간), Fit All Data(보관 구간).
- 패널 닫기·모드 변경 시 패널 송신과 대기를 모두 중지합니다.
- 양수 주기의 등록 패킷은 BUS별 스레드에서 전송하며, deadline 누락은 시스템 로그에 표시합니다. 실제 주기 성능은 하드웨어/드라이버에서 확인해야 합니다.
- 패널 FD/BRS, 슬라이더 Home, 최소 격자와 창 확대 동작을 지원합니다.
- LOG 탭: Extended ID 필터, 필터 결과 재전송, Save로 원본 형식 추출 저장을 지원합니다.
- 상세 사용법: MANUAL.md / USER_PANEL_SEQUENCE.md / USER_PANEL_RELEASE_NOTES.md.
- Consolas가 없거나 지원하지 않는 문자는 시스템 대체 글꼴로 표시됩니다.
"""
        with open(readme_path, 'w', encoding='utf-8') as f:
            f.write(readme_content)
    elif current_os == 'Windows':
        readme_path = os.path.join(dist_path, 'README_Windows_Run_Guide.txt')
        readme_content = f"""{app_prefix.replace('_', ' ')} - 윈도우 환경 실행 가이드

이 프로그램은 단일 실행 파일(.exe)로 제작되어 파이썬 설치 없이 바로 실행 가능합니다.

[실행 방법]
{app_prefix}_{APP_VERSION}_win.exe 파일을 더블클릭하여 실행하세요.

[주의 사항]
- PEAK-System PCAN 드라이버가 PC에 설치되어 있어야 CAN 통신이 정상적으로 동작합니다.
- 가상환경 폴더나 소스코드(src)를 같이 들고 다닐 필요 없이, 이 폴더 내용물만 복사해서 배포/사용하시면 됩니다!

[{APP_VERSION} 기능 및 사용 안내]
- User Panel: Add TX Tool → sequence → 명령 시퀀스 설정에서 CMD/RCV/DEL을 등록합니다.
- DBC 없이 CMD 데이터와 RCV 비트 조건을 수정·실행하며, 패널 Save에 함께 저장됩니다.
- sequence Force/Packet Control은 각 sequence 도구에서 실행하며, 전역 Force RUN 모드는 없습니다.
- TX counter의 RX 동기화 모드, 그래프 Reset Zoom/Auto Y/Fit All Data, BUS별 주기 송신 진단을 포함합니다.
- 패널 닫기·모드 변경 시 패널의 모든 송신과 대기를 중지합니다.
- 패널 FD/BRS, 슬라이더 Home, 최소 격자와 창 확대 동작을 지원합니다.
- LOG 탭: Extended ID 필터, 필터 결과 재전송, Save로 원본 형식 추출 저장을 지원합니다.
- UI 기본 글꼴은 Consolas입니다.
- 상세 사용법: MANUAL.md / USER_PANEL_SEQUENCE.md / USER_PANEL_RELEASE_NOTES.md.
- 소스 수정은 기존 EXE에 자동 반영되지 않습니다. 새로 빌드된 {APP_VERSION} 실행 파일을 사용하세요.
"""
        with open(readme_path, 'w', encoding='utf-8') as f:
            f.write(readme_content)

    print(f"=== 빌드가 완료되었습니다! 'dist/PCAN_Viewer_JJ_{APP_VERSION}{os_suffix}' 폴더를 확인해주세요. ===")

if __name__ == '__main__':
    build_executable()
