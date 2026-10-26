# Open Chat — API-key-free messaging web app

## Features
- Student ID + password registration/login
- One-to-one messages and group chats
- Online/offline status while connected
- Message history stored in SQLite
- WebSockets for live messaging
- Passwords hashed with PBKDF2; no third-party API keys

## Windows setup
1. Install Python 3.11 or 3.12 from https://www.python.org/downloads/windows/ and VS Code from https://code.visualstudio.com/.
2. Extract the ZIP, then open the extracted `college_chat_web_app` folder in VS Code.
3. Select **Terminal → New Terminal**.
4. Create a virtual environment:
   ```powershell
   py -3.11 -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```
   If `py -3.11` fails, use `python -m venv .venv`. If activation is blocked, run `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` and activate again.
5. Install packages:
   ```powershell
   python -m pip install --upgrade pip
   pip install -r requirements.txt
   ```
6. Start the server from the project folder:
   ```powershell
   python -m uvicorn main:app --reload
   ```
7. Visit http://127.0.0.1:8000.

## Test with two accounts
1. Register user A in a normal browser window.
2. Open an Incognito window or another browser and register user B.
3. Select the other user in **People** and send a message. It should appear without refreshing.
4. Create a group, select another user, then send a group message.
5. Close one browser window; their online status should change after the WebSocket disconnects.

## How it works
- `main.py`: API endpoints, password hashing, authorization, SQLite queries and WebSocket events.
- `static/index.html`: page structure.
- `static/style.css`: interface styles.
- `static/app.js`: login, chat list, group UI, and live message client.
- `chat.db`: created automatically when the app first starts.

## Security and deployment notes
This is an educational prototype, not a production-ready WhatsApp replacement. It has no end-to-end encryption; messages are stored in the SQLite database in readable form. Before exposing it to the public internet, set a strong secret, use HTTPS, add rate limiting, account recovery, abuse reporting, backups, and security review. Do not use the demo secret in production. Online status is in server memory and resets when the server restarts. Do not expose the development server directly to the internet.

For local testing, keep `CHAT_HTTPS_ONLY` unset. Before deployment behind HTTPS, set environment variables:
```powershell
$env:CHAT_SESSION_SECRET = "use-a-long-random-secret-kept-private"
$env:CHAT_HTTPS_ONLY = "1"
```
Use a proper hosting service and HTTPS reverse proxy, then run Uvicorn according to the host's deployment instructions.

## Android APK
This is a web app and does not generate an APK directly. Easiest: use it in a phone browser. For installable mobile use, deploy over HTTPS and later add PWA support, or wrap the site in an Android WebView / rebuild the frontend in Flutter. The backend still needs to be hosted and reachable.
