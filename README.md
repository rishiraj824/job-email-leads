# Job Email Agent

Monitors your Gmail Important label daily, classifies job-related emails using Claude, logs them to Google Sheets, and creates draft replies automatically.

## How it works

1. Fetches email subjects/metadata from Important label (last 30 days)
2. Single Haiku call batch-filters subjects for job relevance
3. For each candidate, one Sonnet call extracts details + writes a draft reply
4. Logs to Google Sheets and creates Gmail drafts

**~2 Claude API calls per run. Costs ~$0.05/run.**

## Setup

See [setup.md](setup.md) for full step-by-step instructions covering:
- Google Cloud project + OAuth credentials
- Gmail & Sheets API setup
- Environment variables

## Quick start

```bash
cp .env.example .env
# fill in ANTHROPIC_API_KEY and GOOGLE_SHEETS_ID in .env
# place credentials.json (Google OAuth) in this folder

pip3 install -r requirements.txt
python3 agent.py
```

## Environment variables

| Variable | Description |
|----------|-------------|
| `ANTHROPIC_API_KEY` | From console.anthropic.com |
| `GOOGLE_SHEETS_ID` | Spreadsheet ID from the Google Sheets URL |

## Scheduled runs

A cron job runs the agent daily at 8am via `run.sh`:

```
0 8 * * * /Users/rishi/email-agent/run.sh
```

Logs are written to `agent.log`.

## Sheet columns

Date Received · Company · Role · Status · Pay · Remote/Onsite · Location · LinkedIn/Website · Team Info · Funding · Email Subject · Sender · Summary · Draft Created · Notes
