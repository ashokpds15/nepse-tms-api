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

import os
import time
import requests
import sys

TELEGRAM_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
GITHUB_TOKEN = os.getenv('GITHUB_TOKEN')
REPO = os.getenv('GITHUB_REPO')  # owner/repo
BRANCH = os.getenv('BRANCH', 'main')
POLL_INTERVAL = int(os.getenv('POLL_INTERVAL', '5'))

if not TELEGRAM_TOKEN or not GITHUB_TOKEN or not REPO:
    print('Please set TELEGRAM_BOT_TOKEN, GITHUB_TOKEN, and GITHUB_REPO environment variables')
    sys.exit(1)

API = f'https://api.telegram.org/bot{TELEGRAM_TOKEN}'
LAST_UPDATE_ID = None

WORKFLOWS = {
    'ci': 'ci.yml',
    'deploy': 'deploy.yml',
}


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


print('Starting Telegram -> GitHub dispatcher (polling)...')
while True:
    try:
        resp = requests.get(f'{API}/getUpdates', params={"offset": LAST_UPDATE_ID, "timeout": 10})
        data = resp.json()
        if not data.get('ok'):
            time.sleep(POLL_INTERVAL)
            continue
        for item in data.get('result', []):
            LAST_UPDATE_ID = item['update_id'] + 1
            msg = item.get('message') or item.get('edited_message')
            if not msg:
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
                send_message(chat_id, f'Repo: {REPO}\nBranch: {BRANCH}\nBot: minimal dispatcher')
            else:
                send_message(chat_id, 'Unknown command. Use /run_ci, /deploy, or /status')
    except Exception as e:
        print('Error in polling loop:', e)
        time.sleep(POLL_INTERVAL)
