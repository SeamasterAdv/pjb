@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv312\Scripts\python.exe" (
    echo Ambiente virtual Python 3.12 nao encontrado em .venv312\Scripts\python.exe
    echo Recrie o ambiente com: py -3.12 -m venv --system-site-packages .venv312
    echo Depois instale as dependencias com: .venv312\Scripts\python.exe -m pip install -r requirements.txt
    pause
    exit /b 1
)
".venv312\Scripts\python.exe" -c "import sys; exit(0 if sys.version_info[:2] == (3, 12) else 1)"
if errorlevel 1 (
    echo O ambiente .venv312 nao esta usando Python 3.12.
    pause
    exit /b 1
)

start "Beatriz" /D "%~dp0" ".venv312\Scripts\python.exe" "%~dp0main.py"
exit /b 0
