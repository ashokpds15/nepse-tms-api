"""
Minimal Telegram bot (polling) that accepts commands and dispatches GitHub workflows.

Usage:
  - Set environment variables: TELEGRAM_BOT_TOKEN, GITHUB_TOKEN, GITHUB_REPO (owner/repo), BRANCH (optional, default main)
  - Run: python scripts/telegram_bot.py

Commands handled (minimal):
  /run_ci     -> dispatches the CI workflow (workflow file name: ci.yml)
  /deploy     -> dispatches the deploy workflow (deploy.yml)
  /status     -> replies with basic info

Notes:
  - This is a simple polling bot using getUpdates. For production, use webhooks or a proper framework.
  - GITHUB_TOKEN must be a Personal Access Token with repo/workflow scope to allow workflow_dispatch.
"""

"""
Telegram bot: dispatch GitHub workflows and perform basic NEPSE TMS actions.

Configuration via environment variables or .env (see .env.example):
  TELEGRAM_BOT_TOKEN  - required
  GITHUB_TOKEN        - required (PAT with repo+workflow scopes)
  GITHUB_REPO         - required (owner/repo)
  BRANCH              - optional (default: main)
  POLL_INTERVAL       - optional (seconds, default 5)
  TELEGRAM_ALLOWED_USERS - optional comma-separated Telegram usernames or numeric IDs allowed to use bot

Optional TMS settings (for /holdings):
  TMS_BASE_URL        - e.g. https://tms35.nepsetms.com.np
  TMS_USERNAME
  TMS_PASSWORD

Commands:
  /run_ci    - dispatch CI workflow (ci.yml)
  /deploy    - dispatch deploy workflow (deploy.yml)
  /status    - show basic status
  /holdings  - (if TMS creds provided) fetch holdings via nepse-tms-api

Security notes: Do NOT put credentials into public places. Use environment variables or Docker secrets. Restrict TELEGRAM_ALLOWED_USERS to avoid unauthorized use.
"""

from dotenv import load_dotenv
import os
import time
import requests
import sys

load_dotenv()

TELEGRAM_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
GITHUB_TOKEN = os.getenv('GITHUB_TOKEN')
REPO = os.getenv('GITHUB_REPO')  # owner/repo
BRANCH = os.getenv('BRANCH', 'main')
POLL_INTERVAL = int(os.getenv('POLL_INTERVAL', '5'))
ALLOWED = os.getenv('TELEGRAM_ALLOWED_USERS')  # comma-separated usernames or ids

TMS_BASE = os.getenv('TMS_BASE_URL')
TMS_USERNAME = os.getenv('TMS_USERNAME')
TMS_PASSWORD = os.getenv('TMS_PASSWORD')

if not TELEGRAM_TOKEN or not GITHUB_TOKEN or not REPO:
    print('Please set TELEGRAM_BOT_TOKEN, GITHUB_TOKEN, and GITHUB_REPO environment variables')
    sys.exit(1)

API = f'https://api.telegram.org/bot{TELEGRAM_TOKEN}'
LAST_UPDATE_ID = None

WORKFLOWS = {
    'ci': 'ci.yml',
    'deploy': 'deploy.yml',
}

allowed_set = set()
if ALLOWED:
    for v in ALLOWED.split(','):
        v = v.strip()
        if v:
            allowed_set.add(v)


def is_allowed(msg):
    if not allowed_set:
        return True
    user = msg.get('from', {})
    uid = str(user.get('id'))
    uname = user.get('username') or user.get('first_name')
    if uid in allowed_set or (uname and uname in allowed_set):
        return True
    return False


def send_message(chat_id, text):
    try:
        requests.post(f"{API}/sendMessage", data={"chat_id": chat_id, "text": text})
    except Exception as e:
        print('Failed to send message:', e)


def dispatch_workflow(workflow_file, ref=BRANCH):
    url = f'https://api.github.com/repos/{REPO}/actions/workflows/{workflow_file}/dispatches'
    payload = {"ref": ref}
    headers = {
        'Authorization': f'token {GITHUB_TOKEN}',
        'Accept': 'application/vnd.github.v3+json'
    }
    resp = requests.post(url, json=payload, headers=headers)
    return resp.status_code, resp.text


def fetch_holdings():
    try:
        from nepse_tms import TmsClient
    except Exception as e:
        return False, f'nepse_tms not installed: {e}'
    if not (TMS_BASE and TMS_USERNAME and TMS_PASSWORD):
        return False, 'TMS_BASE_URL, TMS_USERNAME and TMS_PASSWORD must be set as env vars to use /holdings'
    try:
        client = TmsClient(TMS_BASE)
        account = client.login(TMS_USERNAME, TMS_PASSWORD)
        rows = client.holdings()
        lines = []
        for h in rows:
            lines.append(f"{h.symbol}: free={h.free_quantity}, total={h.total_quantity}")
        return True, '\n'.join(lines) if lines else 'No holdings returned.'
    except Exception as e:
        return False, f'Error fetching holdings: {e}'

print('Starting Telegram -> GitHub dispatcher (polling)...')
while True:
    try:
        resp = requests.get(f'{API}/getUpdates', params={"offset": LAST_UPDATE_ID, "timeout": 20})
        data = resp.json()
        if not data.get('ok'):
            time.sleep(POLL_INTERVAL)
            continue
        for item in data.get('result', []):
            LAST_UPDATE_ID = item['update_id'] + 1
            msg = item.get('message') or item.get('edited_message')
            if not msg:
                continue
            if not is_allowed(msg):
                print('Blocked user:', msg.get('from'))
                continue
            chat_id = msg['chat']['id']
            text = msg.get('text', '').strip()
            from_user = msg['from'].get('username') or msg['from'].get('first_name')
            print(f'Received from {from_user}: {text}')

            if text.startswith('/run_ci'):
                send_message(chat_id, 'Dispatching CI workflow...')
                code, body = dispatch_workflow(WORKFLOWS['ci'])
                send_message(chat_id, f'GitHub API returned {code}.')
            elif text.startswith('/deploy'):
                send_message(chat_id, 'Dispatching deploy workflow...')
                code, body = dispatch_workflow(WORKFLOWS['deploy'])
                send_message(chat_id, f'GitHub API returned {code}.')
            elif text.startswith('/status'):
                info = f'Repo: {REPO}\nBranch: {BRANCH}\nBot: dispatcher\nAllowed users: {ALLOWED or "(any)"}'
                send_message(chat_id, info)
            elif text.startswith('/holdings'):
                send_message(chat_id, 'Fetching holdings (this may take a few seconds)...')
                ok, result = fetch_holdings()
                send_message(chat_id, result)
            else:
                send_message(chat_id, 'Unknown command. Use /run_ci, /deploy, /status, or /holdings')
    except Exception as e:
        print('Error in polling loop:', e)
        time.sleep(POLL_INTERVAL)
