# JobHunter — AI Job Application Agent

Multi-user Flask app that helps you find jobs, write cover letters, send applications via Gmail, and auto-classify replies (interview / rejection / offer) using Claude AI.

## Features

- **Multi-user accounts** — anyone can sign up, each user has their own private data
- **Bento-grid dashboard** — clean white cards with stats, sparklines, and AI agent panel
- **Dynamic resume editor** — skills (tag chips), experience, education, certifications, languages, achievements — all addable/removable on the fly
- **AI agent** — generates matched job listings based on your resume
- **CSV import** — bulk upload job listings (with downloadable template)
- **Gmail integration** — send emails via SMTP, read inbox via IMAP using app password
- **AI email classification** — Claude reads each reply and labels it as interview / rejected / offer / generic / other
- **Cover letter generation** — AI writes a 300-340 word cover letter tailored to each job and your resume
- **Application tracking** — full history with status updates from inbox scan

## Deploy on Render (Free)

### 1. Push to GitHub

```bash
git init
git add .
git commit -m "first commit"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/jobhunter.git
git push -u origin main
```

### 2. Create a Render Web Service

1. Go to [render.com](https://render.com) → sign in with GitHub
2. Click **New +** → **Web Service** → connect your `jobhunter` repo
3. Settings:
   - **Runtime:** Python 3
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `gunicorn app:app --bind 0.0.0.0:$PORT --workers 2 --timeout 120`
   - **Instance Type:** Free
4. Under **Environment Variables**, add:
   - `ANTHROPIC_API_KEY` — from [console.anthropic.com/api-keys](https://console.anthropic.com/api-keys)
   - `SECRET_KEY` — run `python -c "import secrets; print(secrets.token_hex(32))"` and paste the output

5. Click **Create Web Service**. Build takes ~3 minutes.
6. Your app is live at `https://jobhunter.onrender.com`

### 3. Connect Your Domain (jobhunter.saddamadil.in)

1. In Render → your service → **Settings** → **Custom Domains** → add `jobhunter.saddamadil.in`
2. In Hostinger → DNS Management → add CNAME record:
   - **Name:** `jobhunter`
   - **Target:** `jobhunter.onrender.com`
   - **TTL:** 14400
3. Wait 5–30 minutes. SSL is auto-issued by Render.

## Local Development

```bash
pip install -r requirements.txt
cp .env.example .env  # then edit .env with your keys
python app.py
```

App runs at http://localhost:5000

## Gmail App Password Setup

Each user connects their own Gmail through the in-app "Gmail Settings" modal. They need to:

1. Enable 2-Step Verification at [myaccount.google.com/security](https://myaccount.google.com/security)
2. Generate an App Password at [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords)
3. Paste the 16-character password in the JobHunter Gmail Settings modal

The app verifies the credentials via SMTP login before saving.

## File Structure

```
jobhunter/
├── app.py                   # Flask backend (380 lines)
├── requirements.txt
├── Procfile                 # for Render
├── .env.example
├── .gitignore
├── README.md
└── templates/
    ├── login.html           # Sign-in page
    ├── signup.html          # Account creation
    ├── dashboard.html       # Main UI (Bento grid + 5 tabs)
    └── app.js.html          # All frontend JS (included by dashboard)
```

## Tech Stack

- **Backend:** Flask 3 + SQLite + Werkzeug password hashing
- **Email:** Python `smtplib` (SMTP_SSL gmail.com:465) + `imaplib` (IMAP4_SSL imap.gmail.com:993)
- **AI:** Claude API (Sonnet 4) via server-side proxy — API key never leaves the server
- **Frontend:** Vanilla JS, Plus Jakarta Sans + Inter fonts, no build step

## Security Notes

- Passwords are hashed with Werkzeug's `pbkdf2:sha256`
- Sessions use Flask's signed cookies with `SECRET_KEY`
- Gmail app passwords are stored encrypted at rest in SQLite (column `gmail_creds.app_password`)
- The Claude API key stays on the server — frontend calls `/api/claude` proxy
- Each user's jobs/resumes/applications are isolated via `user_id` foreign keys

## Limitations

- The AI agent **simulates** job listings (it doesn't scrape real job boards). For real scraping you'd need APIs from LinkedIn / Indeed / etc.
- IMAP scan only reads the last 30 days, last 50 messages, and matches replies by sender domain
- Render's free tier sleeps after 15 minutes of inactivity (first request takes ~30 seconds to wake up)
