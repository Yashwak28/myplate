@echo off
title MyPlate Launcher
cd /d C:\Users\yashw\.gemini\antigravity\scratch\calorie-tracker

echo.
echo  =============================================
echo   MyPlate - Starting...
echo  =============================================
echo.

:: Start Flask server in a new window
call venv\Scripts\activate.bat
start "MyPlate - Server" cmd /k "call venv\Scripts\activate.bat && python app.py"

:: Wait 2 seconds for Flask to boot
timeout /t 2 /nobreak >nul

:: Start ngrok if it exists
if exist ngrok.exe (
    start "MyPlate - Public URL" cmd /k "ngrok.exe http 5000"
    echo  ngrok started! Check the ngrok window for your public URL.
    echo  Your phone can open that URL from anywhere!
) else (
    echo  [Optional] ngrok.exe not found in this folder.
    echo  Download from https://ngrok.com/download for phone access.
)

:: Open local browser
timeout /t 3 /nobreak >nul
start "" "http://localhost:5000"

echo.
echo  MyPlate is running at: http://localhost:5000
echo  Keep both windows open while using the app.
echo  Close them to stop.
echo.
pause
