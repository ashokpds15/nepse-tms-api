Telegram + GitHub Actions integration

Overview
---------
This repository now includes:

- CI workflow (.github/workflows/ci.yml) that runs on push and workflow_dispatch and sends a Telegram notification when complete.
- Deploy workflow (.github/workflows/deploy.yml) that runs on release published and workflow_dispatch. It also notifies Telegram.
- scripts/telegram_bot.py — a minimal polling-based Telegram bot that can dispatch these workflows using the GitHub Actions workflow_dispatch API.

What this implements
---------------------
- Run tests/build on push and manual dispatch (workflow_dispatch).
- On completion, CI workflow sends a Telegram message (requires secrets).
- On release published, deploy job runs (placeholder steps; update with real deploy commands) and sends Telegram message.
- A Telegram bot (polling) that accepts /run_ci, /deploy and /status and will call the GitHub REST API to start the corresponding workflow.

Required GitHub Secrets
-----------------------
Add the following repository secrets (Settings → Secrets → Actions):

- TELEGRAM_BOT_TOKEN — token of the Telegram bot (botXXXX:YYY)
- TELEGRAM_CHAT_ID — chat id to send notifications to (group or user)
- GITHUB_TOKEN — personal access token (PAT) with `repo` and `workflow` scopes for the Telegram bot to call the Actions API

Optional secrets for deployment (if using SSH):
- SSH_PRIVATE_KEY — private key used for SSH deploy steps
- DEPLOY_USER — username on the target host
- DEPLOY_HOST — hostname or IP of the deployment target

How the Telegram bot triggers workflows
---------------------------------------
The bot calls the GitHub API endpoint:

POST /repos/:owner/:repo/actions/workflows/:workflow_id/dispatches

with payload {"ref": "main"}

The minimal script uses the workflow file name (ci.yml, deploy.yml) as the workflow identifier. For more advanced usage, use the workflow id number or filename and add inputs if needed.

Hosting the Telegram bot
------------------------
Options:
- Run on a small VPS or server (systemd service)
- Host on Heroku / Railway / Fly / Render (simple deploy)
- Containerize and run in any container host

Example (systemd):
1. Create virtualenv, install requests: python -m venv venv && venv\Scripts\pip install requests
2. Create systemd service or run via screen/tmux: python scripts/telegram_bot.py
3. Set env vars TELEGRAM_BOT_TOKEN, GITHUB_TOKEN, GITHUB_REPO (owner/repo), optional BRANCH

Security notes
--------------
- GITHUB_TOKEN used by the bot should be a PAT with the minimum necessary scopes (repo, workflow). Do NOT store this token in the bot code; use environment variables or secret storage.
- Be careful which chat or users can access the bot commands — anyone who can message the bot can trigger workflows.

Next steps / Customization
--------------------------
- Replace the placeholder deploy steps in .github/workflows/deploy.yml with your real deployment commands (cloud CLI, docker push, ssh+rsync, etc.)
- Use webhooks (recommended) instead of polling for a production-grade bot (telegram webhook + HTTPS endpoint)
- Add authentication/whitelisting to the bot (check msg['from']['id'] against an allowlist) to avoid unauthorized triggers

If you'd like, proceed and:
- I can update deploy.yml with a concrete deploy method you use (SSH, rsync, Docker Hub, AWS, etc.)
- Create a systemd example unit or Dockerfile for the bot
- Create a GitHub Actions secret setup checklist and optionally commit a .github/workflows/issue-or-pr-template to remind maintainers to add secrets

Added Docker support and run instructions
----------------------------------------
Files added to make the bot runnable locally or on a server:
- Dockerfile
- docker-compose.yml
- requirements.txt
- .env.example

Quick Docker / Compose run (do these locally):

1. Copy the example file and fill in real credentials (do NOT commit this file):
   cp .env.example .env
   # edit .env and add your values

2. Build and run with Docker Compose:
   docker compose build
   docker compose up -d

3. Monitor logs:
   docker compose logs -f telegram-bot

CLI run (no container):
1. Create a virtualenv and install deps:
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1   # Windows PowerShell
   python -m pip install --upgrade pip
   pip install -r requirements.txt

2. Set environment variables in your shell (PowerShell example):
   $env:TELEGRAM_BOT_TOKEN = 'bot<your-telegram-bot-token>'
   $env:GITHUB_TOKEN = '<your-github-pat>'
   $env:GITHUB_REPO = 'ashokpds15/nepse-tms-api'
   # optional:
   $env:TMS_BASE_URL = 'https://tms35.nepsetms.com.np'
   $env:TMS_USERNAME = 'your_tms_user'
   $env:TMS_PASSWORD = 'your_tms_password'

3. Run the bot:
   python scripts\telegram_bot.py

Commands available once the bot is running
- /run_ci    -> dispatch CI workflow (ci.yml)
- /deploy    -> dispatch deploy workflow (deploy.yml)
- /status    -> show basic status
- /holdings  -> (if TMS env vars provided) fetch holdings using the nepse-tms-api library

Security reminders
- Do not paste tokens or PATs into public chat. The token that was previously pasted must be revoked immediately.
- Use .env (not committed) or Docker secrets in production.
- Restrict TELEGRAM_ALLOWED_USERS to a small allowlist to avoid unauthorized triggers.

Next steps you might want
- Add a systemd unit or Windows service wrapper to keep the bot always running on a server.
- Add more TMS actions (place_order, cancel_order) with explicit confirmation steps and dry-run safety checks.
- Replace polling with webhooks for production reliability and lower resource use.

I’m an AI assistant using Copilot CLI runtime in VS Code. If you want the systemd unit, a Docker registry CI workflow, or more TMS commands added to the bot, say which and I'll add them.
