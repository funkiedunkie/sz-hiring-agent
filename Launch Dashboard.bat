@echo off
cd /d C:\Users\Buste\sz-hiring-agent

:: Start Streamlit (binds to 0.0.0.0 so phone on same WiFi can reach it)
start "" "C:\Users\Buste\AppData\Roaming\Python\Python313\Scripts\streamlit.exe" run dashboard.py

:: Give it 3 seconds to start, then open the browser on this PC
timeout /t 3 /nobreak >nul
start "" "http://localhost:8501"

echo.
echo Dashboard is running.
echo On your phone (same WiFi): http://192.168.4.81:8501
echo.
pause
