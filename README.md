# Job Email Agent

Monitors your Gmail Important label, classifies job-related emails from startups using Claude, logs them to Google Sheets, and publishes a curated job board to [rishiraj.github.io/jobs](https://rishiraj.github.io/jobs) — updated daily.

## How it works

1. Fetches email metadata (subject + sender) from Important label — last 7 days
2. Single **Haiku** call batch-filters all subjects for job relevance
3. For each candidate, one **Sonnet** call extracts structured details + filters out big companies
4. Logs to Google Sheets and publishes to the site

**~2 Claude API calls per run. ~$0.05/run.**

## Setup

See [setup.md](setup.md) for full step-by-step instructions:
- Google Cloud project + OAuth credentials
- Gmail & Sheets API setup
- Environment variables

## Quick start

```bash
cp .env.example .env
# fill in ANTHROPIC_API_KEY and GOOGLE_SHEETS_ID
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

Runs daily at 8am via cron:

```
0 8 * * * /Users/rishi/email-agent/run.sh
```

Logs are written to `agent.log`.

## Draft mode (optional, manual)

By default the agent only extracts and logs job leads — it does **not** create Gmail drafts on scheduled runs.

To generate draft replies for all job emails found, run manually with the `--draft` flag:

```bash
python3 agent.py --draft
```

This adds a third Claude call per job email (Sonnet) to write a tailored reply based on the email status (Interview, Offer, Rejection, etc.) and saves it as a Gmail draft. Review and send from your Drafts folder.

## Sheet columns

Date Received · Company · Role · Status · Pay · Remote/Onsite · Location · LinkedIn/Website · Team Info · Funding · Email Subject · Sender · Summary · Notes
