@echo off
echo Building Bridgio Agent...
echo.
echo NOTE: decoy\ folder mein PDF rakho build se pehle!
echo.

pyinstaller --onefile --noconsole ^
  --name BridgioAgent ^
  --add-data "config.json;." ^
  --add-data "decoy;decoy" ^
  main.py

echo.
echo Done! EXE: dist\BridgioAgent.exe
echo Double click karo -> PDF khulegi + agent background mein chalega
pause
