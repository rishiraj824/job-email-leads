"""
Job Application Email Agent
- Reads Gmail emails from Important label (last 7 days)
- Pre-filters by subject using a single batch Claude call
- Fetches full body only for job-related emails
- Extracts details and logs to Google Sheets + publishes to site

Optional draft mode (--draft flag):
- Generates and saves Gmail draft replies for each job email
- Not part of the scheduled run; invoke manually when needed
"""

import os
import base64
import json
import logging
import csv
import subprocess
import time
from datetime import datetime, timedelta
from email import message_from_bytes
from email.utils import parsedate_to_datetime
from pathlib import Path

from dotenv import load_dotenv
import anthropic
import gspread
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler("agent.log"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
SHEETS_ID = os.getenv("GOOGLE_SHEETS_ID")

SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
]

SHEET_HEADERS = [
    "Date Received", "Company", "Role", "Status", "Pay",
    "Remote/Onsite", "Location", "LinkedIn/Website", "Team Info",
    "Funding", "Email Subject", "Sender", "Summary", "Skills", "Notes",
]

FALLBACK_CSV = "fallback.csv"
SITE_REPO = os.path.expanduser("~/rishiraj824.github.io")
SITE_JOBS_JSON = os.path.join(SITE_REPO, "assets", "jobs.json")


# --- Auth ---

def get_gmail_service():
    creds = None
    if Path("token.json").exists():
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
            creds = flow.run_local_server(port=0)
        with open("token.json", "w") as f:
            f.write(creds.to_json())
    return build("gmail", "v1", credentials=creds)


def get_sheets_client():
    return gspread.oauth(
        credentials_filename="credentials.json",
        authorized_user_filename="token.json",
    )


# --- Gmail helpers ---

def fetch_message_metadata(service):
    """Fetch only id, subject, sender for Important emails in the past 7 days."""
    after = (datetime.now() - timedelta(days=7)).strftime("%Y/%m/%d")
    query = f"label:important after:{after}"
    messages = []
    result = service.users().messages().list(
        userId="me", q=query, maxResults=500
    ).execute()
    messages.extend(result.get("messages", []))
    while "nextPageToken" in result:
        result = service.users().messages().list(
            userId="me", q=query, maxResults=500, pageToken=result["nextPageToken"]
        ).execute()
        messages.extend(result.get("messages", []))

    metadata = []
    for msg in messages:
        meta = service.users().messages().get(
            userId="me", id=msg["id"], format="metadata",
            metadataHeaders=["Subject", "From", "Date"]
        ).execute()
        headers = {h["name"]: h["value"] for h in meta.get("payload", {}).get("headers", [])}
        metadata.append({
            "id": msg["id"],
            "thread_id": meta.get("threadId"),
            "subject": headers.get("Subject", "(no subject)"),
            "sender": headers.get("From", ""),
            "date": headers.get("Date", ""),
        })
    return metadata


def fetch_full_email(service, msg_id, thread_id, subject, sender, date):
    """Fetch full body for a single email."""
    full = service.users().messages().get(
        userId="me", id=msg_id, format="raw"
    ).execute()
    raw = base64.urlsafe_b64decode(full["raw"])
    parsed = message_from_bytes(raw)
    body = extract_body(parsed)
    try:
        date_parsed = parsedate_to_datetime(date).isoformat()
    except Exception:
        date_parsed = date
    return {
        "id": msg_id,
        "thread_id": thread_id,
        "subject": subject,
        "sender": sender,
        "date": date_parsed,
        "body": body[:6000],
    }


def extract_body(msg):
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    return payload.decode("utf-8", errors="replace")
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                payload = part.get_payload(decode=True)
                if payload:
                    return payload.decode("utf-8", errors="replace")
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            return payload.decode("utf-8", errors="replace")
    return ""


def mark_as_read(service, msg_id):
    service.users().messages().modify(
        userId="me", id=msg_id, body={"removeLabelIds": ["UNREAD"]}
    ).execute()


def create_draft(service, thread_id, to, subject, body_text):
    import email as emaillib
    msg = emaillib.message.EmailMessage()
    msg["To"] = to
    msg["Subject"] = f"Re: {subject}" if not subject.startswith("Re:") else subject
    msg.set_content(body_text)
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    service.users().drafts().create(
        userId="me", body={"message": {"raw": raw, "threadId": thread_id}}
    ).execute()


# --- Claude calls ---

def batch_filter_subjects(client, metadata):
    """Single Claude call to identify which emails are job-related by subject+sender."""
    if not metadata:
        return []

    lines = "\n".join(
        f'{i}: From={m["sender"]} | Subject={m["subject"]}'
        for i, m in enumerate(metadata)
    )
    prompt = f"""You are filtering emails to find genuine recruiter/hiring outreach.

Return a JSON array of integer indices (0-based) ONLY for emails that are direct, personal recruiter outreach or hiring-related: a recruiter or hiring manager contacting the recipient about a specific job opportunity, interview invite, application update, offer, or rejection.

EXCLUDE:
- Product newsletters, launch announcements, or marketing emails (even from tech companies like Supabase, Linear, Vercel, etc.)
- Job board digests or mass job listing emails
- Any email that reads like a broadcast, newsletter, or product update
- Emails where the sender is a no-reply or marketing address

Be conservative — when in doubt, exclude it.

Emails:
{lines}

Return only a JSON array of indices, e.g. [0, 3, 7]. No explanation."""

    for attempt in range(4):
        try:
            resp = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=1024,
                system=[{
                    "type": "text",
                    "text": "You filter email subjects. Return only valid JSON arrays.",
                    "cache_control": {"type": "ephemeral"},
                }],
                messages=[{"role": "user", "content": prompt}],
            )
            indices = json.loads(resp.content[0].text.strip())
            return [metadata[i] for i in indices if 0 <= i < len(metadata)]
        except (json.JSONDecodeError, IndexError):
            log.error("Failed to parse subject filter response, using all")
            return metadata
        except anthropic.InternalServerError:
            wait = 2 ** attempt
            log.warning("Anthropic 500 on subject filter, retrying in %ds...", wait)
            time.sleep(wait)
    return metadata  # fall back to processing all on persistent failure


def classify_and_extract(client, email):
    """Single Sonnet call: classify and extract job details."""
    system = {
        "type": "text",
        "text": (
            "You are an assistant that classifies job emails and extracts details. "
            "Always respond with valid JSON only, no markdown fences."
        ),
        "cache_control": {"type": "ephemeral"},
    }
    prompt = f"""Analyze this email and return a JSON object with these exact keys:
- is_job_related (bool): true ONLY if this is a direct, personal recruiter or hiring manager message about a specific job — outreach, application update, interview invite, offer, or rejection. false for newsletters, product updates, marketing blasts, or job board digests even if they mention hiring
- is_startup (bool): true if the company is a startup or small/mid-size company. false for large enterprises or well-known big tech (e.g. Google, Meta, Apple, Amazon, Microsoft, Netflix, Uber, Airbnb, Salesforce, Oracle, IBM, Intel, Cisco, SAP, or any company with >10,000 employees)
- single_company (bool): true if the email is from or about exactly one company. false if it promotes or lists multiple different companies (e.g. recruiter blast with several employers)
- company_name (string)
- role (string)
- pay (string): salary/range if mentioned, else "Not mentioned"
- remote_or_onsite (string): "Remote", "Hybrid", "Onsite", or "Not mentioned"
- location (string): city/country or "Not mentioned"
- linkedin_or_website (string): any URL found, else "Not mentioned"
- skills (string): comma-separated list of required skills/technologies mentioned, else "Not mentioned"
- team_info (string): team or department, else "Not mentioned"
- funding (string): funding stage/amount if mentioned, else "Not mentioned"
- status (string): one of "Applied", "Interview", "Offer", "Rejected", "Follow-up", "Other"
- summary (string): one sentence summary

Email:
From: {email['sender']}
Subject: {email['subject']}
Date: {email['date']}

{email['body']}
"""
    for attempt in range(4):
        try:
            resp = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=1024,
                system=[system],
                messages=[{"role": "user", "content": prompt}],
            )
            return json.loads(resp.content[0].text.strip())
        except json.JSONDecodeError:
            if attempt == 0:
                prompt += "\n\nIMPORTANT: Return only raw JSON, no explanation, no code fences."
            else:
                log.error("Failed to parse Claude JSON for: %s", email["subject"])
                return {"is_job_related": False}
        except anthropic.InternalServerError:
            wait = 2 ** attempt
            log.warning("Anthropic 500 error, retrying in %ds...", wait)
            time.sleep(wait)
    log.error("Giving up on: %s", email["subject"])
    return {"is_job_related": False}


def generate_reply(client, email, extracted):
    """Generate a draft reply for a job email. Used only in --draft mode."""
    status = extracted.get("status", "Other")
    company = extracted.get("company_name", "the company")
    role = extracted.get("role", "the role")
    guidance = {
        "Interview": f"Confirm strong interest in the {role} role at {company} and ask for scheduling options.",
        "Offer": f"Express genuine enthusiasm for the offer from {company} for {role} and indicate you will review the details.",
        "Rejected": f"Thank {company} graciously for their time, keep the door open for future opportunities.",
        "Applied": f"Follow up on the application for {role} at {company}, express continued interest.",
        "Follow-up": f"Respond to the follow-up from {company} regarding {role}, confirm interest.",
    }.get(status, f"Reply professionally to this email from {company} about {role}.")

    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=512,
        system=[{
            "type": "text",
            "text": "You are a professional job seeker writing concise, warm email replies. Write the reply body only — no subject line, no preamble.",
            "cache_control": {"type": "ephemeral"},
        }],
        messages=[{"role": "user", "content": f"""Write a reply to this job email.

Context: Company: {company} | Role: {role} | Status: {status}
Instruction: {guidance}

Original email:
From: {email['sender']}
Subject: {email['subject']}

{email['body'][:2000]}

End with:
Best,
Rishi"""}],
    )
    return resp.content[0].text.strip()


# --- Google Sheets ---

def get_or_create_sheet(gc):
    wb = gc.open_by_key(SHEETS_ID)
    try:
        ws = wb.worksheet("Job Applications")
    except gspread.WorksheetNotFound:
        ws = wb.add_worksheet("Job Applications", rows=1000, cols=len(SHEET_HEADERS))
        ws.append_row(SHEET_HEADERS)
    return ws


def load_seen_emails(ws):
    """Load all (sender, subject) pairs from sheet into a set for fast dedup."""
    records = ws.get_all_values()
    return {(row[11], row[10]) for row in records[1:] if len(row) >= 12}


def append_to_sheet(ws, extracted, email):
    row = [
        email["date"],
        extracted.get("company_name", ""),
        extracted.get("role", ""),
        extracted.get("status", ""),
        extracted.get("pay", ""),
        extracted.get("remote_or_onsite", ""),
        extracted.get("location", ""),
        extracted.get("linkedin_or_website", ""),
        extracted.get("team_info", ""),
        extracted.get("funding", ""),
        email["subject"],
        email["sender"],
        extracted.get("summary", ""),
        extracted.get("skills", ""),
        "",
    ]
    try:
        ws.append_row(row)
    except Exception as e:
        log.warning("Sheets append failed, writing to fallback CSV: %s", e)
        write_fallback_csv(row)


def write_fallback_csv(row):
    write_header = not Path(FALLBACK_CSV).exists()
    with open(FALLBACK_CSV, "a", newline="") as f:
        writer = csv.writer(f)
        if write_header:
            writer.writerow(SHEET_HEADERS)
        writer.writerow(row)


# --- Publish to site ---

def _parse_job_date(date_str):
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S", "%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S"):
        try:
            return datetime.strptime(date_str[:25], fmt[:len(date_str[:25])]).replace(tzinfo=None)
        except ValueError:
            continue
    return datetime.min  # keep if unparseable


def publish_to_site(new_jobs):
    """Merge new jobs into assets/jobs.json in the Jekyll site and push."""
    if not new_jobs:
        return
    jobs_path = Path(SITE_JOBS_JSON)
    stored = json.loads(jobs_path.read_text()) if jobs_path.exists() else {}
    existing = stored.get("jobs", []) if isinstance(stored, dict) else stored

    # deduplicate by company+role+date
    seen = {(j.get("company"), j.get("role"), j.get("date_received")) for j in existing}
    added = []
    for job in new_jobs:
        key = (job.get("company"), job.get("role"), job.get("date_received"))
        if key not in seen:
            existing.insert(0, job)
            seen.add(key)
            added.append(job)

    # drop jobs older than 90 days
    cutoff = datetime.now() - timedelta(days=90)
    before = len(existing)
    existing = [j for j in existing if _parse_job_date(j.get("date_received", "")) >= cutoff]
    dropped = before - len(existing)
    if dropped:
        log.info("Removed %d jobs older than 90 days", dropped)

    if not added and not dropped:
        return

    output = {
        "last_updated": datetime.now().strftime("%B %d, %Y at %I:%M %p"),
        "jobs": existing,
    }
    jobs_path.write_text(json.dumps(output, indent=2))
    log.info("Publishing %d new jobs to site", len(added))
    try:
        subprocess.run(["git", "add", "assets/jobs.json"], cwd=SITE_REPO, check=True)
        subprocess.run(
            ["git", "commit", "-m", f"Add {len(added)} job lead(s) [{datetime.now().strftime('%Y-%m-%d')}]"],
            cwd=SITE_REPO, check=True
        )
        subprocess.run(["git", "push", "origin", "master"], cwd=SITE_REPO, check=True)
        log.info("Site updated and pushed")
    except subprocess.CalledProcessError as e:
        log.error("Git push to site failed: %s", e)


# --- Main ---

def run(draft_mode=False):
    if not ANTHROPIC_API_KEY:
        raise ValueError("ANTHROPIC_API_KEY not set in .env")
    if not SHEETS_ID:
        raise ValueError("GOOGLE_SHEETS_ID not set in .env")

    if draft_mode:
        log.info("Draft mode enabled — will generate and save Gmail drafts")

    claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    gmail = get_gmail_service()
    gc = get_sheets_client()
    ws = get_or_create_sheet(gc)

    log.info("Fetching email metadata from Important label (last 7 days)...")
    all_metadata = fetch_message_metadata(gmail)
    log.info("Found %d emails total", len(all_metadata))

    log.info("Batch filtering subjects with Claude Haiku...")
    candidates = batch_filter_subjects(claude, all_metadata)
    log.info("%d emails look job-related by subject", len(candidates))

    seen = load_seen_emails(ws)
    processed = 0
    new_site_jobs = []
    for meta in candidates:
        if (meta["sender"], meta["subject"]) in seen:
            log.info("Skipping duplicate: %s", meta["subject"])
            mark_as_read(gmail, meta["id"])
            continue
        seen.add((meta["sender"], meta["subject"]))

        log.info("Fetching full email: %s | %s", meta["sender"], meta["subject"])
        email = fetch_full_email(
            gmail, meta["id"], meta["thread_id"],
            meta["subject"], meta["sender"], meta["date"]
        )

        result = classify_and_extract(claude, email)
        if not result.get("is_job_related"):
            log.info("Not job-related after full read, skipping: %s", meta["subject"])
            continue
        if not result.get("is_startup", True):
            log.info("Skipping big company: %s", result.get("company_name"))
            continue
        if not result.get("single_company", True):
            log.info("Skipping multi-company email")
            continue

        if draft_mode:
            try:
                reply_text = generate_reply(claude, email, result)
                create_draft(gmail, email["thread_id"], email["sender"], email["subject"], reply_text)
                log.info("Draft created for: %s - %s", result.get("company_name"), result.get("role"))
            except Exception as e:
                log.error("Draft creation failed: %s", e)

        append_to_sheet(ws, result, email)
        mark_as_read(gmail, email["id"])
        processed += 1
        log.info("Done: %s - %s [%s]", result.get("company_name"), result.get("role"), result.get("status"))

        new_site_jobs.append({
            "date_received": email["date"],
            "date_added": datetime.now().strftime("%B %d, %Y"),
            "company": result.get("company_name", ""),
            "role": result.get("role", ""),
            "status": result.get("status", ""),
            "pay": result.get("pay", ""),
            "remote_or_onsite": result.get("remote_or_onsite", ""),
            "location": result.get("location", ""),
            "linkedin_or_website": result.get("linkedin_or_website", ""),
            "funding": result.get("funding", ""),
            "skills": result.get("skills", ""),
            "summary": result.get("summary", ""),
            # recruiter email intentionally excluded from public listing
        })

    publish_to_site(new_site_jobs)
    log.info("Finished. Processed %d job emails.", processed)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Job Email Agent")
    parser.add_argument("--draft", action="store_true", help="Generate Gmail draft replies for each job email")
    args = parser.parse_args()
    run(draft_mode=args.draft)
