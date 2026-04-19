# Job Email Agent Setup

## Step 1: Google Cloud Project

1. Go to https://console.cloud.google.com and create a new project (e.g. "job-email-agent")
2. Enable these two APIs:
   - Gmail API: https://console.cloud.google.com/apis/library/gmail.googleapis.com
   - Google Sheets API: https://console.cloud.google.com/apis/library/sheets.googleapis.com
   - Google Drive API: https://console.cloud.google.com/apis/library/drive.googleapis.com

## Step 2: Create OAuth2 Credentials

1. Go to APIs & Services > Credentials > Create Credentials > OAuth client ID
2. Application type: Desktop app
3. Name it anything (e.g. "job-agent")
4. Download the JSON file and rename it to `credentials.json`
5. Place `credentials.json` in this folder (/Users/rishi/email-agent/)

## Step 3: Configure OAuth Consent Screen

1. Go to APIs & Services > OAuth consent screen
2. User type: External
3. Add your Gmail address as a test user
4. Add these scopes:
   - .../auth/gmail.modify
   - .../auth/gmail.compose
   - .../auth/spreadsheets
   - .../auth/drive.file

## Step 4: Google Sheets

1. Create a new Google Sheets spreadsheet
2. Copy the spreadsheet ID from the URL:
   https://docs.google.com/spreadsheets/d/SPREADSHEET_ID/edit
3. The agent will auto-create a "Job Applications" sheet with headers

## Step 5: Environment Variables

Copy .env.example to .env and fill in:

```
cp .env.example .env
```

Edit .env:
- ANTHROPIC_API_KEY: Get from https://console.anthropic.com
- GOOGLE_SHEETS_ID: The spreadsheet ID from Step 4

## Step 6: Install Dependencies

```bash
cd /Users/rishi/email-agent
pip install -r requirements.txt
```

## Step 7: Run the Agent

```bash
python agent.py
```

On first run, a browser window will open asking you to authorize the app with your Google account. After authorizing, a `token.json` file is saved — future runs won't need browser auth.

## Sheet Columns

The agent creates these columns in "Job Applications":

| Column | Description |
|--------|-------------|
| Date Received | When the email arrived |
| Company | Company name |
| Role | Job title |
| Status | Applied / Interview / Offer / Rejected / Follow-up |
| Pay | Salary or range if mentioned |
| Remote/Onsite | Remote / Hybrid / Onsite |
| Location | City/country |
| LinkedIn/Website | Any URL found in email |
| Team Info | Team or department |
| Funding | Funding stage/amount |
| Email Subject | Original subject line |
| Sender | Sender email address |
| Summary | One-line AI summary |
| Draft Created | Yes/No |
| Notes | Manual notes column |

## Running on a Schedule (optional)

Add to crontab to run every hour:

```
crontab -e
```

Add this line (adjust path as needed):
```
0 * * * * cd /Users/rishi/email-agent && python agent.py >> agent.log 2>&1
```
