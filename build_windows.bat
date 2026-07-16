@echo off
setlocal

py -3.11 -m pip install --upgrade pip
py -3.11 -m pip install -r requirements-build.txt
py -3.11 -m PyInstaller --noconfirm --clean SAPB1Proxy.spec

echo.
echo Build complete: dist\SAPB1Proxy.exe
