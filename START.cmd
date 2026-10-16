@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
where pyw >nul 2>&1
if not errorlevel 1 (
  start "" pyw -3 "%~dp0START.pyw"
  exit /b
)
where pythonw >nul 2>&1
if not errorlevel 1 (
  start "" pythonw "%~dp0START.pyw"
  exit /b
)
py -3 "%~dp0START.pyw"
if not errorlevel 1 exit /b
python "%~dp0START.pyw"
if not errorlevel 1 exit /b
echo.
echo Python 3.10 oder neuer mit Tkinter wird benoetigt.
echo Installiere Python von https://www.python.org/downloads/windows/
echo Aktiviere bei der Installation Add python.exe to PATH sowie Tcl/Tk.
echo Danach START.cmd erneut doppelklicken.
pause
endlocal
