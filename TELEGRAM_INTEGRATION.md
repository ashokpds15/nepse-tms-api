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
