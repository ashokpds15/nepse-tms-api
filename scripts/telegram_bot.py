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

This update adds an inline keyboard (buttons) so users can tap options instead of typing commands.

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

Buttons provided:
  - Run CI
  - Deploy (asks for confirmation)
  - Status
  - Holdings

Security notes: Do NOT put credentials into public places. Use environment variables or Docker secrets. Restrict TELEGRAM_ALLOWED_USERS to avoid unauthorized use.
"""

from dotenv import load_dotenv
import os
import time
import json
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


def is_allowed(msg_or_user):
    # Accept either a message dict or a user dict
    if not allowed_set:
        return True
    if isinstance(msg_or_user, dict) and 'from' in msg_or_user:
        user = msg_or_user.get('from', {})
    else:
        user = msg_or_user
    uid = str(user.get('id'))
    uname = user.get('username') or user.get('first_name')
    if uid in allowed_set or (uname and uname in allowed_set):
        return True
    return False


def send_message_raw(payload):
    try:
        requests.post(f"{API}/sendMessage", json=payload, timeout=10)
    except Exception as e:
        print('Failed to send message:', e)


def send_text(chat_id, text, reply_markup=None):
    payload = {'chat_id': chat_id, 'text': text}
    if reply_markup is not None:
        payload['reply_markup'] = reply_markup
    send_message_raw(payload)


def send_keyboard(chat_id, text='Choose an action:'):
    kb = {
        'inline_keyboard': [
            [
                {'text': 'Run CI', 'callback_data': 'run_ci'},
                {'text': 'Deploy', 'callback_data': 'deploy'}
            ],
            [
                {'text': 'Status', 'callback_data': 'status'},
                {'text': 'Holdings', 'callback_data': 'holdings'}
            ]
        ]
    }
    send_text(chat_id, text, reply_markup=kb)


def answer_callback(callback_query_id, text=None):
    payload = {'callback_query_id': callback_query_id}
    if text:
        payload['text'] = text
    try:
        requests.post(f"{API}/answerCallbackQuery", json=payload, timeout=5)
    except Exception as e:
        print('Failed to answer callback:', e)


def dispatch_workflow(workflow_file, ref=BRANCH):
    url = f'https://api.github.com/repos/{REPO}/actions/workflows/{workflow_file}/dispatches'
    payload = {"ref": ref}
    headers = {
        'Authorization': f'token {GITHUB_TOKEN}',
        'Accept': 'application/vnd.github.v3+json'
    }
    resp = requests.post(url, json=payload, headers=headers, timeout=10)
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

print('Starting Telegram -> GitHub dispatcher (polling with inline keyboard support)...')
while True:
    try:
        resp = requests.get(f'{API}/getUpdates', params={"offset": LAST_UPDATE_ID, "timeout": 20})
        data = resp.json()
        if not data.get('ok'):
            time.sleep(POLL_INTERVAL)
            continue
        for item in data.get('result', []):
            LAST_UPDATE_ID = item['update_id'] + 1

            # Handle callback queries (button presses)
            if 'callback_query' in item:
                cq = item['callback_query']
                cq_id = cq.get('id')
                user = cq.get('from', {})
                if not is_allowed(user):
                    answer_callback(cq_id, text='Unauthorized')
                    continue
                data_payload = cq.get('data')
                chat = cq.get('message', {}).get('chat', {})
                chat_id = chat.get('id')

                # Acknowledge the button press quickly
                answer_callback(cq_id)

                if data_payload == 'run_ci':
                    send_text(chat_id, 'Dispatching CI workflow...')
                    code, body = dispatch_workflow(WORKFLOWS['ci'])
                    send_text(chat_id, f'GitHub API returned {code}.')
                elif data_payload == 'deploy':
                    # Ask for confirmation
                    confirm_kb = {'inline_keyboard': [[
                        {'text': 'Confirm Deploy', 'callback_data': 'confirm_deploy'},
                        {'text': 'Cancel', 'callback_data': 'cancel'}
                    ]]}
                    send_text(chat_id, 'Are you sure you want to deploy?', reply_markup=confirm_kb)
                elif data_payload == 'confirm_deploy':
                    send_text(chat_id, 'Confirmed: dispatching deploy workflow...')
                    code, body = dispatch_workflow(WORKFLOWS['deploy'])
                    send_text(chat_id, f'GitHub API returned {code}.')
                elif data_payload == 'cancel':
                    send_text(chat_id, 'Action cancelled.')
                elif data_payload == 'status':
                    info = f'Repo: {REPO}\nBranch: {BRANCH}\nBot: dispatcher\nAllowed users: {ALLOWED or "(any)"}'
                    send_text(chat_id, info)
                elif data_payload == 'holdings':
                    send_text(chat_id, 'Fetching holdings (this may take a few seconds)...')
                    ok, result = fetch_holdings()
                    send_text(chat_id, result)
                else:
                    send_text(chat_id, f'Unknown action: {data_payload}')

                continue

            # Handle normal messages
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

            if text.startswith('/'):
                # Recognized slash commands show keyboard or act
                if text.startswith('/start'):
                    send_keyboard(chat_id, 'Welcome — choose an action:')
                elif text.startswith('/run_ci'):
                    send_text(chat_id, 'Dispatching CI workflow...')
                    code, body = dispatch_workflow(WORKFLOWS['ci'])
                    send_text(chat_id, f'GitHub API returned {code}.')
                elif text.startswith('/deploy'):
                    send_keyboard(chat_id, 'Use the Deploy button to start a deployment (confirmation will be requested).')
                elif text.startswith('/status'):
                    info = f'Repo: {REPO}\nBranch: {BRANCH}\nBot: dispatcher\nAllowed users: {ALLOWED or "(any)"}'
                    send_text(chat_id, info)
                elif text.startswith('/holdings'):
                    send_text(chat_id, 'Fetching holdings (this may take a few seconds)...')
                    ok, result = fetch_holdings()
                    send_text(chat_id, result)
                else:
                    send_keyboard(chat_id)
            else:
                # For plain text, show the keyboard to guide users
                send_keyboard(chat_id)
    except Exception as e:
        print('Error in polling loop:', e)
        time.sleep(POLL_INTERVAL)
