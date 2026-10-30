@echo off
REM Jalankan HRIS lokal: aktifkan venv312, pasang requirements.txt, migrasi, lalu runserver.
cd /d "%~dp0"

if not exist venv312\Scripts\activate.bat (
    echo [INFO] venv312 belum ada, membuat...
    py -3.12 -m venv venv312 || python -m venv venv312
    if errorlevel 1 ( echo [ERROR] Gagal membuat venv312. & pause & exit /b 1 )
)

call venv312\Scripts\activate.bat

echo [INFO] Memasang requirements.txt...
python -m pip install -r requirements.txt
if errorlevel 1 ( echo [ERROR] pip install gagal. & pause & exit /b 1 )

echo [INFO] Migrasi database...
python manage.py migrate
if errorlevel 1 ( echo [ERROR] migrate gagal. & pause & exit /b 1 )

echo [INFO] Server di http://127.0.0.1:8000/
python manage.py runserver 127.0.0.1:8000
pause
