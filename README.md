# SAP B1 Desktop Proxy

Cross-platform desktop bridge between SAP Business One Service Layer and Power BI. The application runs a read-only local proxy, authenticates to SAP, and can publish the proxy through an ngrok HTTPS tunnel.

The original Render service remains available through `sap_proxy.py`. Its deployment uses `requirements-render.txt`.

## Desktop Features

- PyWebView HTML/CSS/JavaScript desktop interface
- SAP Service Layer login and automatic session renewal
- Local proxy bound to `127.0.0.1`
- API key, bearer, and Basic-password authentication
- Automatic ngrok download and assigned-domain support
- Real-time logs, manual start/stop, and system tray controls
- Per-user login startup on Windows and macOS
- Power Query M generation
- Windows DPAPI protection for stored secrets
- macOS/Linux secret file restricted to the current user

## Run On macOS

Use Python 3.11 for parity with the Windows build.

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python main.py
```

Settings are stored under `~/Library/Application Support/SAPB1Proxy`.
Runtime events are appended as redacted JSON lines to `logs/proxy.log` under the same directory.

Run tests:

```bash
python -m unittest discover -s tests -v
```

## Build Windows Executable

PyInstaller must run on Windows. From a Windows Python 3.11 environment:

```powershell
py -3.11 -m pip install -r requirements-build.txt
py -3.11 -m PyInstaller --noconfirm --clean SAPB1Proxy.spec
```

The executable is written to `dist\SAPB1Proxy.exe`. `build_windows.bat` runs the same process. The GitHub Actions workflow also builds and uploads the executable on `main`.

## Usage

1. Enter the SAP server, port, company database, username, and password.
2. Keep certificate verification enabled and select the SAP CA certificate when required.
3. Enter the ngrok authtoken and optional assigned `ngrok-free.app` domain.
4. Save settings and test the SAP connection.
5. Start the services.
6. Generate and copy the Power Query M code from the Power BI section.

The application downloads ngrok into its local application-data directory when no `ngrok` executable is available on `PATH`.

The Windows executable uses the Microsoft Edge WebView2 renderer. Windows installations without WebView2 must install that runtime before opening the GUI.

## Startup

Windows uses a per-user Task Scheduler job named `SAPB1Proxy_AutoStart`. It runs after login with a 30-second delay and opens minimized to the tray. It does not require administrator privileges.

The application supports:

```bash
python main.py --autostart --minimized
python main.py --headless --autostart
```

## Security Defaults

- Proxy listens only on localhost.
- Only SAP entity `GET` requests are exposed.
- Login and logout endpoints are never proxied.
- API keys must contain at least 32 characters.
- Logs redact passwords, API keys, authorization values, and ngrok tokens.
- SAP TLS verification is enabled by default.
- Secret values are not preloaded into the desktop form or initial WebView state.
- The API key is decrypted only for an explicit copy or Power BI code generation action.
- ngrok receives its authtoken through its child-process environment; the app does not persist an `ngrok.yml` token file.

On Windows, non-secret settings are stored in
`%LOCALAPPDATA%\SAPB1Proxy\config.json`. The SAP password, proxy API key, and
ngrok authtoken are stored in `%LOCALAPPDATA%\SAPB1Proxy\secrets.dat` using
Windows DPAPI for the current user. A legacy plaintext `ngrok.yml` created by
older builds is removed when the updated application opens.

To completely reset a Windows installation, exit the tray application and run:

```powershell
Remove-Item "$env:LOCALAPPDATA\SAPB1Proxy" -Recurse -Force
```
