@echo off
:: 터미널 인코딩을 UTF-8로 변경하여 한글 깨짐 방지
chcp 65001 >nul

echo === 윈도우 환경 자동 설치 스크립트 ===

echo.
echo === 1. 파이썬 가상환경(venv) 생성 ===
if exist "venv_win" (
    echo 기존 가상환경 폴더를 발견하여 삭제합니다...
    rmdir /s /q "venv_win"
)
python -m venv venv_win
if errorlevel 1 (
    echo 파이썬 가상환경 생성 실패! 파이썬이 설치되어 있고 환경 변수(PATH)에 등록되어 있는지 확인하세요.
    pause
    exit /b
)

echo.
echo === 2. 가상환경 활성화 및 패키지 설치 ===
call venv_win\Scripts\activate.bat

echo pip 업데이트 중...
python -m pip install --upgrade pip

echo 필수 패키지 설치 중 (requirements.txt)...
pip install -r requirements.txt

echo.
echo =========================================================
echo 설치가 완료되었습니다! 계속하려면 아무 키나 누르세요.
echo =========================================================
pause