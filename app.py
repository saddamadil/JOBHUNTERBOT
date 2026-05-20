"""
JobHunter — Multi-user AI Job Application Agent
Backend: Flask + SQLite + IMAP/SMTP + Claude API proxy
"""
import os, json, sqlite3, smtplib, imaplib, email, ssl, csv, io, re
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import parsedate_to_datetime
from functools import wraps
from datetime import datetime, timedelta
import requests
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, send_file
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-in-prod-please")
app.permanent_session_lifetime = timedelta(days=14)

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip().strip('"').strip("'")
DB_PATH = os.environ.get("DB_PATH", "jobhunter.db")

# ─────────────────────────────────────────────────────────────────
# DATABASE
# ─────────────────────────────────────────────────────────────────
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def init_db():
    conn = db()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        full_name TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS resumes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        full_name TEXT, title TEXT, location TEXT, phone TEXT,
        portfolio TEXT, linkedin TEXT, summary TEXT,
        skills TEXT,           -- JSON array
        experience TEXT,       -- JSON array of jobs
        education TEXT,        -- JSON array
        certifications TEXT,   -- JSON array
        languages TEXT,        -- JSON array
        achievements TEXT,     -- JSON array
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS gmail_creds (
        user_id INTEGER PRIMARY KEY,
        gmail_address TEXT NOT NULL,
        app_password TEXT NOT NULL,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS jobs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        title TEXT, company TEXT, location TEXT, job_type TEXT,
        site TEXT, url TEXT, salary TEXT, posted TEXT,
        match_score INTEGER, description TEXT,
        requirements TEXT,     -- JSON array
        hiring_email TEXT, company_size TEXT, industry TEXT,
        cover_letter TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS applications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        job_id INTEGER,
        company TEXT, role TEXT,
        to_email TEXT, subject TEXT, body TEXT,
        status TEXT DEFAULT 'sent', -- sent, draft, replied, rejected, interview, offer
        reply_summary TEXT,
        sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS email_replies (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        application_id INTEGER,
        from_email TEXT, subject TEXT, body TEXT,
        classification TEXT, -- rejected, accepted, interview, generic, other
        ai_summary TEXT,
        received_at TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    );
    """)
    conn.commit()
    conn.close()

init_db()

# ─────────────────────────────────────────────────────────────────
# AUTH HELPERS
# ─────────────────────────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def wrap(*a, **kw):
        if not session.get("user_id"):
            return redirect(url_for("login"))
        return f(*a, **kw)
    return wrap

def current_user():
    return session.get("user_id")

# ─────────────────────────────────────────────────────────────────
# ROUTES — AUTH
# ─────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return redirect(url_for("dashboard") if current_user() else url_for("login"))

@app.route("/login", methods=["GET", "POST"])
def login():
    err = None
    if request.method == "POST":
        email_ = request.form.get("email", "").strip().lower()
        pw = request.form.get("password", "")
        conn = db()
        u = conn.execute("SELECT * FROM users WHERE email=?", (email_,)).fetchone()
        conn.close()
        if u and check_password_hash(u["password_hash"], pw):
            session.permanent = True
            session["user_id"] = u["id"]
            session["user_email"] = u["email"]
            session["user_name"] = u["full_name"] or u["email"].split("@")[0]
            return redirect(url_for("dashboard"))
        err = "Invalid email or password."
    return render_template("login.html", error=err)

@app.route("/signup", methods=["GET", "POST"])
def signup():
    err = None
    if request.method == "POST":
        email_ = request.form.get("email", "").strip().lower()
        pw = request.form.get("password", "")
        name = request.form.get("full_name", "").strip()
        if not email_ or not pw or len(pw) < 6:
            err = "Email and password (min 6 chars) required."
        else:
            conn = db()
            try:
                cur = conn.execute(
                    "INSERT INTO users (email, password_hash, full_name) VALUES (?,?,?)",
                    (email_, generate_password_hash(pw), name)
                )
                user_id = cur.lastrowid
                # Create empty resume
                conn.execute(
                    "INSERT INTO resumes (user_id, full_name, skills, experience, education, certifications, languages, achievements) VALUES (?,?,?,?,?,?,?,?)",
                    (user_id, name, "[]", "[]", "[]", "[]", "[]", "[]")
                )
                conn.commit()
                session.permanent = True
                session["user_id"] = user_id
                session["user_email"] = email_
                session["user_name"] = name or email_.split("@")[0]
                conn.close()
                return redirect(url_for("dashboard"))
            except sqlite3.IntegrityError:
                err = "Email already registered."
            conn.close()
    return render_template("signup.html", error=err)

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/dashboard")
@login_required
def dashboard():
    return render_template("dashboard.html",
        user_name=session.get("user_name", ""),
        user_email=session.get("user_email", ""))

# ─────────────────────────────────────────────────────────────────
# API — RESUME
# ─────────────────────────────────────────────────────────────────
@app.route("/api/resume", methods=["GET"])
@login_required
def get_resume():
    conn = db()
    r = conn.execute("SELECT * FROM resumes WHERE user_id=?", (current_user(),)).fetchone()
    conn.close()
    if not r:
        return jsonify({})
    out = dict(r)
    for k in ("skills", "experience", "education", "certifications", "languages", "achievements"):
        try: out[k] = json.loads(out.get(k) or "[]")
        except: out[k] = []
    return jsonify(out)

@app.route("/api/resume", methods=["POST"])
@login_required
def save_resume():
    data = request.get_json()
    conn = db()
    conn.execute("""
        UPDATE resumes SET
        full_name=?, title=?, location=?, phone=?, portfolio=?, linkedin=?, summary=?,
        skills=?, experience=?, education=?, certifications=?, languages=?, achievements=?,
        updated_at=CURRENT_TIMESTAMP
        WHERE user_id=?
    """, (
        data.get("full_name",""), data.get("title",""), data.get("location",""),
        data.get("phone",""), data.get("portfolio",""), data.get("linkedin",""),
        data.get("summary",""),
        json.dumps(data.get("skills", [])),
        json.dumps(data.get("experience", [])),
        json.dumps(data.get("education", [])),
        json.dumps(data.get("certifications", [])),
        json.dumps(data.get("languages", [])),
        json.dumps(data.get("achievements", [])),
        current_user()
    ))
    # Update user full_name as well
    if data.get("full_name"):
        conn.execute("UPDATE users SET full_name=? WHERE id=?",
                     (data.get("full_name"), current_user()))
        session["user_name"] = data.get("full_name")
    conn.commit()
    conn.close()
    return jsonify({"ok": True})

# ─────────────────────────────────────────────────────────────────
# API — JOBS
# ─────────────────────────────────────────────────────────────────
@app.route("/api/jobs", methods=["GET"])
@login_required
def list_jobs():
    conn = db()
    rows = conn.execute(
        "SELECT * FROM jobs WHERE user_id=? ORDER BY match_score DESC, created_at DESC",
        (current_user(),)
    ).fetchall()
    conn.close()
    out = []
    for r in rows:
        d = dict(r)
        try: d["requirements"] = json.loads(d.get("requirements") or "[]")
        except: d["requirements"] = []
        out.append(d)
    return jsonify(out)

@app.route("/api/jobs", methods=["POST"])
@login_required
def add_jobs():
    """Add one or more jobs. Skips duplicates (same title + company)."""
    data = request.get_json()
    jobs = data if isinstance(data, list) else [data]
    conn = db()

    # Get existing (title, company) pairs to dedup against
    existing = conn.execute(
        "SELECT LOWER(title) t, LOWER(company) c FROM jobs WHERE user_id=?",
        (current_user(),)
    ).fetchall()
    seen = {(r["t"], r["c"]) for r in existing}

    ids = []
    skipped = 0
    for j in jobs:
        title = (j.get("title") or "").strip()
        company = (j.get("company") or "").strip()
        key = (title.lower(), company.lower())
        if not title or not company:
            skipped += 1
            continue
        if key in seen:
            skipped += 1
            continue
        seen.add(key)
        cur = conn.execute("""
            INSERT INTO jobs (user_id, title, company, location, job_type, site, url, salary, posted,
                              match_score, description, requirements, hiring_email, company_size, industry)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            current_user(),
            title, company, j.get("location",""),
            j.get("type") or j.get("job_type",""), j.get("site",""), j.get("url",""), j.get("salary",""),
            j.get("posted",""), int(j.get("matchScore") or j.get("match_score") or 70),
            j.get("description",""),
            json.dumps(j.get("requirements", [])),
            j.get("hiringEmail") or j.get("hiring_email", ""),
            j.get("companySize") or j.get("company_size", ""),
            j.get("industry", "")
        ))
        ids.append(cur.lastrowid)
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "ids": ids, "added": len(ids), "skipped": skipped})


@app.route("/api/jobs/bulk-delete", methods=["POST"])
@login_required
def bulk_delete_jobs():
    """Delete multiple jobs by ID array."""
    data = request.get_json()
    ids = data.get("ids", [])
    if not ids:
        return jsonify({"deleted": 0})
    conn = db()
    placeholders = ",".join("?" for _ in ids)
    conn.execute(
        f"DELETE FROM jobs WHERE id IN ({placeholders}) AND user_id=?",
        (*ids, current_user())
    )
    deleted = conn.total_changes
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "deleted": deleted})


@app.route("/api/jobs/dedup", methods=["POST"])
@login_required
def dedup_jobs():
    """Remove duplicate jobs (same title + company), keep the highest match_score."""
    conn = db()
    rows = conn.execute(
        "SELECT id, LOWER(title) t, LOWER(company) c, match_score FROM jobs WHERE user_id=? ORDER BY match_score DESC, id ASC",
        (current_user(),)
    ).fetchall()
    seen = {}
    to_delete = []
    for r in rows:
        key = (r["t"], r["c"])
        if key in seen:
            to_delete.append(r["id"])
        else:
            seen[key] = r["id"]
    if to_delete:
        placeholders = ",".join("?" for _ in to_delete)
        conn.execute(
            f"DELETE FROM jobs WHERE id IN ({placeholders}) AND user_id=?",
            (*to_delete, current_user())
        )
        conn.commit()
    conn.close()
    return jsonify({"ok": True, "removed": len(to_delete)})

@app.route("/api/jobs/<int:job_id>", methods=["PATCH"])
@login_required
def update_job(job_id):
    data = request.get_json()
    conn = db()
    if "cover_letter" in data:
        conn.execute("UPDATE jobs SET cover_letter=? WHERE id=? AND user_id=?",
                     (data["cover_letter"], job_id, current_user()))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})

@app.route("/api/jobs/<int:job_id>", methods=["DELETE"])
@login_required
def delete_job(job_id):
    conn = db()
    conn.execute("DELETE FROM jobs WHERE id=? AND user_id=?", (job_id, current_user()))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})

# ─────────────────────────────────────────────────────────────────
# API — CSV UPLOAD
# ─────────────────────────────────────────────────────────────────
@app.route("/api/jobs/import-csv", methods=["POST"])
@login_required
def import_csv():
    """Import jobs from uploaded CSV.
    Expected columns: title, company, location, type, site, url, salary, hiring_email, description, requirements (semicolon-separated)
    """
    f = request.files.get("file")
    if not f:
        return jsonify({"error": "No file uploaded"}), 400
    try:
        text = f.read().decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text))
        conn = db()
        n = 0
        for row in reader:
            reqs = [r.strip() for r in (row.get("requirements","")).split(";") if r.strip()]
            conn.execute("""
                INSERT INTO jobs (user_id, title, company, location, job_type, site, url, salary, posted,
                                  match_score, description, requirements, hiring_email, company_size, industry)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                current_user(),
                row.get("title",""), row.get("company",""), row.get("location",""),
                row.get("type","Remote"), row.get("site","CSV Import"),
                row.get("url",""), row.get("salary",""), row.get("posted","Recent"),
                int(row.get("match_score") or row.get("matchScore") or 70),
                row.get("description",""), json.dumps(reqs),
                row.get("hiring_email") or row.get("email") or "",
                row.get("company_size",""), row.get("industry","")
            ))
            n += 1
        conn.commit()
        conn.close()
        return jsonify({"ok": True, "imported": n})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/jobs/csv-template")
@login_required
def csv_template():
    sample = "title,company,location,type,site,url,salary,hiring_email,match_score,description,requirements\n"
    sample += "Digital Marketing Manager,Example GmbH,Berlin Germany,Remote,LinkedIn,https://example.com/job,45000-60000 EUR,hr@example.com,82,Lead digital campaigns and SEO strategy,SEO;Google Ads;Meta Ads;3+ years experience;English fluent\n"
    return send_file(
        io.BytesIO(sample.encode()),
        mimetype="text/csv",
        as_attachment=True,
        download_name="jobs_template.csv"
    )

# ─────────────────────────────────────────────────────────────────
# API — GMAIL CREDS
# ─────────────────────────────────────────────────────────────────
@app.route("/api/gmail", methods=["GET", "POST", "DELETE"])
@login_required
def gmail_creds():
    conn = db()
    if request.method == "GET":
        c = conn.execute("SELECT gmail_address FROM gmail_creds WHERE user_id=?",
                         (current_user(),)).fetchone()
        conn.close()
        return jsonify({"connected": bool(c), "gmail_address": c["gmail_address"] if c else ""})
    if request.method == "DELETE":
        conn.execute("DELETE FROM gmail_creds WHERE user_id=?", (current_user(),))
        conn.commit()
        conn.close()
        return jsonify({"ok": True})
    # POST
    data = request.get_json()
    addr = data.get("gmail_address","").strip()
    pw = data.get("app_password","").replace(" ", "")
    if not addr or not pw:
        return jsonify({"error": "Email and app password required"}), 400
    # Test the credentials
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context(), timeout=15) as s:
            s.login(addr, pw)
    except Exception as e:
        return jsonify({"error": f"Login failed: {str(e)}"}), 400
    conn.execute("""
        INSERT INTO gmail_creds (user_id, gmail_address, app_password) VALUES (?,?,?)
        ON CONFLICT(user_id) DO UPDATE SET gmail_address=excluded.gmail_address, app_password=excluded.app_password
    """, (current_user(), addr, pw))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})

# ─────────────────────────────────────────────────────────────────
# API — SEND EMAIL via SMTP
# ─────────────────────────────────────────────────────────────────
@app.route("/api/email/send", methods=["POST"])
@login_required
def send_email():
    data = request.get_json()
    to = data.get("to","").strip()
    subject = data.get("subject","")
    body = data.get("body","")
    company = data.get("company","")
    role = data.get("role","")
    job_id = data.get("job_id")
    if not to or not subject:
        return jsonify({"error": "To and subject required"}), 400

    conn = db()
    creds = conn.execute("SELECT * FROM gmail_creds WHERE user_id=?",
                        (current_user(),)).fetchone()
    if not creds:
        conn.close()
        return jsonify({"error": "Gmail not connected. Add app password in settings."}), 400

    msg = MIMEMultipart()
    msg["From"] = creds["gmail_address"]
    msg["To"] = to
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context(), timeout=20) as s:
            s.login(creds["gmail_address"], creds["app_password"])
            s.send_message(msg)
    except Exception as e:
        conn.close()
        return jsonify({"error": f"Send failed: {str(e)}"}), 500

    cur = conn.execute("""
        INSERT INTO applications (user_id, job_id, company, role, to_email, subject, body, status)
        VALUES (?,?,?,?,?,?,?,?)
    """, (current_user(), job_id, company, role, to, subject, body, "sent"))
    conn.commit()
    app_id = cur.lastrowid
    conn.close()
    return jsonify({"ok": True, "application_id": app_id})

# ─────────────────────────────────────────────────────────────────
# API — READ + CLASSIFY EMAILS via IMAP
# ─────────────────────────────────────────────────────────────────
def classify_with_claude(subject, body):
    """Ask Claude to classify an email reply."""
    if not ANTHROPIC_API_KEY:
        return {"classification": "other", "summary": "(no API key)"}
    prompt = f"""Classify this email reply to a job application.
Subject: {subject}
Body (truncated): {body[:1500]}

Return ONLY a JSON object: {{"classification": "rejected" | "interview" | "accepted" | "generic" | "other", "summary": "one short sentence"}}.
- rejected: any clear rejection (e.g. "we decided to move forward with other candidates")
- interview: invitation to interview, screening call, assessment
- accepted: job offer or "we'd like to make you an offer"
- generic: auto-reply, "we received your application", confirmation
- other: anything else"""
    try:
        r = requests.post("https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            },
            json={
                "model": "claude-sonnet-4-20250514",
                "max_tokens": 200,
                "messages": [{"role": "user", "content": prompt}]
            },
            timeout=30
        )
        text = r.json()["content"][0]["text"]
        # strip markdown
        text = re.sub(r"```json|```", "", text).strip()
        m = re.search(r"\{[\s\S]*\}", text)
        if m:
            return json.loads(m.group(0))
    except Exception as e:
        return {"classification": "other", "summary": f"(parse error: {e})"}
    return {"classification": "other", "summary": ""}

@app.route("/api/email/scan", methods=["POST"])
@login_required
def scan_inbox():
    """Read inbox via IMAP, classify ALL job-related emails (not just replies to apps).

    Strategy:
    - Pulls last 30 days, last 100 messages
    - For each email, asks Claude: is this job-related? if yes, classify it
    - Matches to a sent application if possible, otherwise stands alone
    """
    conn = db()
    creds = conn.execute("SELECT * FROM gmail_creds WHERE user_id=?",
                        (current_user(),)).fetchone()
    if not creds:
        conn.close()
        return jsonify({"error": "Gmail not connected. Add app password in settings."}), 400

    # Get list of recipients we've emailed (to match replies when possible)
    sent_to = conn.execute(
        "SELECT id, to_email, company, role, sent_at FROM applications WHERE user_id=? ORDER BY sent_at DESC LIMIT 200",
        (current_user(),)
    ).fetchall()
    sent_map = {row["to_email"].lower(): row for row in sent_to if row["to_email"]}

    # Job-related keyword patterns - if subject contains any of these, it's likely job-related
    JOB_KEYWORDS = [
        "application", "applied", "thank you for applying", "position", "role", "interview",
        "candidate", "career", "opportunity", "hiring", "recruitment", "recruiter",
        "shortlist", "screening", "we received", "your candidacy", "next steps",
        "job", "vacancy", "stelle", "bewerbung", "vorstellungsgespräch",
        "rejected", "decided", "moving forward", "not selected", "unsuccessful",
        "offer", "congratulations", "welcome aboard", "linkedin", "indeed", "glassdoor",
        "stepstone", "xing", "monster"
    ]

    def looks_job_related(subj, from_):
        s = (subj or "").lower()
        f = (from_ or "").lower()
        return any(kw in s for kw in JOB_KEYWORDS) or \
               any(kw in f for kw in ["recruit", "talent", "hr@", "careers@", "hiring@", "jobs@"])

    classified = []
    debug_info = {"total_scanned": 0, "job_related": 0, "skipped_existing": 0, "errors": []}

    try:
        with imaplib.IMAP4_SSL("imap.gmail.com", 993) as m:
            m.login(creds["gmail_address"], creds["app_password"])
            m.select("inbox")
            # Fetch last 30 days
            since = (datetime.now() - timedelta(days=30)).strftime("%d-%b-%Y")
            typ, msg_nums = m.search(None, f'(SINCE "{since}")')
            if typ != "OK":
                conn.close()
                return jsonify({"error": "IMAP search failed"}), 500
            ids = msg_nums[0].split()
            debug_info["total_scanned"] = len(ids)

            # Scan last 100 messages
            for num in ids[-100:]:
                try:
                    typ, msg_data = m.fetch(num, "(RFC822)")
                    if not msg_data or not msg_data[0]:
                        continue
                    raw = msg_data[0][1]
                    msg = email.message_from_bytes(raw)
                    from_ = email.utils.parseaddr(msg.get("From",""))[1].lower()
                    subj = str(msg.get("Subject","")).strip()

                    # Skip if not job-related (keyword check before expensive AI call)
                    if not looks_job_related(subj, from_):
                        continue
                    debug_info["job_related"] += 1

                    date_hdr = msg.get("Date","")
                    try:
                        received_at = parsedate_to_datetime(date_hdr).isoformat() if date_hdr else None
                    except:
                        received_at = None

                    # Try to match to a sent application
                    matching_app = None
                    if "@" in from_:
                        sender_domain = from_.split("@")[-1]
                        for sent_email, row in sent_map.items():
                            if "@" not in sent_email:
                                continue
                            recipient_domain = sent_email.split("@")[-1]
                            if from_ == sent_email or sender_domain == recipient_domain:
                                matching_app = row
                                break

                    # Check duplicate
                    existing = conn.execute(
                        "SELECT id FROM email_replies WHERE user_id=? AND from_email=? AND subject=?",
                        (current_user(), from_, subj)
                    ).fetchone()
                    if existing:
                        debug_info["skipped_existing"] += 1
                        continue

                    # Get body text
                    body_text = ""
                    if msg.is_multipart():
                        for part in msg.walk():
                            ct = part.get_content_type()
                            if ct == "text/plain":
                                try:
                                    body_text = part.get_payload(decode=True).decode(
                                        part.get_content_charset() or "utf-8", errors="replace"
                                    )
                                    break
                                except: pass
                        if not body_text:
                            for part in msg.walk():
                                if part.get_content_type() == "text/html":
                                    try:
                                        html = part.get_payload(decode=True).decode(
                                            part.get_content_charset() or "utf-8", errors="replace"
                                        )
                                        body_text = re.sub(r"<[^>]+>", " ", html)
                                        break
                                    except: pass
                    else:
                        try:
                            body_text = msg.get_payload(decode=True).decode(
                                msg.get_content_charset() or "utf-8", errors="replace"
                            )
                        except:
                            body_text = str(msg.get_payload() or "")
                    body_text = (body_text or "")[:3000].strip()

                    cls = classify_with_claude(subj, body_text)
                    classification = cls.get("classification", "other")
                    summary = cls.get("summary", "")

                    conn.execute("""
                        INSERT INTO email_replies (user_id, application_id, from_email, subject, body, classification, ai_summary, received_at)
                        VALUES (?,?,?,?,?,?,?,?)
                    """, (
                        current_user(),
                        matching_app["id"] if matching_app else None,
                        from_, subj, body_text, classification, summary, received_at
                    ))

                    if matching_app:
                        status_map = {"interview": "interview", "accepted": "offer", "rejected": "rejected"}
                        new_status = status_map.get(classification, "replied")
                        conn.execute("UPDATE applications SET status=?, reply_summary=? WHERE id=?",
                                     (new_status, summary, matching_app["id"]))

                    classified.append({
                        "from": from_, "subject": subj,
                        "classification": classification, "summary": summary,
                        "matched_app": bool(matching_app)
                    })
                except Exception as inner_e:
                    debug_info["errors"].append(str(inner_e)[:200])
                    continue

        conn.commit()
    except imaplib.IMAP4.error as e:
        conn.close()
        return jsonify({
            "error": f"Gmail login failed: {str(e)}. Verify your app password.",
            "hint": "Generate a fresh app password at myaccount.google.com/apppasswords"
        }), 401
    except Exception as e:
        conn.close()
        return jsonify({"error": f"Scan failed: {str(e)}", "debug": debug_info}), 500
    conn.close()
    return jsonify({
        "ok": True,
        "new_replies": len(classified),
        "replies": classified,
        "debug": debug_info
    })

@app.route("/api/applications")
@login_required
def list_applications():
    conn = db()
    rows = conn.execute("""
        SELECT a.*, COUNT(r.id) as reply_count
        FROM applications a
        LEFT JOIN email_replies r ON r.application_id = a.id
        WHERE a.user_id=?
        GROUP BY a.id
        ORDER BY a.sent_at DESC
    """, (current_user(),)).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

@app.route("/api/replies")
@login_required
def list_replies():
    conn = db()
    rows = conn.execute("""
        SELECT r.*, a.company, a.role
        FROM email_replies r
        LEFT JOIN applications a ON a.id = r.application_id
        WHERE r.user_id=?
        ORDER BY r.received_at DESC
    """, (current_user(),)).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

@app.route("/api/stats")
@login_required
def stats():
    conn = db()
    cu = current_user()
    n_jobs = conn.execute("SELECT COUNT(*) c FROM jobs WHERE user_id=?", (cu,)).fetchone()["c"]
    n_apps = conn.execute("SELECT COUNT(*) c FROM applications WHERE user_id=?", (cu,)).fetchone()["c"]
    n_replies = conn.execute("SELECT COUNT(*) c FROM email_replies WHERE user_id=?", (cu,)).fetchone()["c"]
    n_interview = conn.execute("SELECT COUNT(*) c FROM email_replies WHERE user_id=? AND classification='interview'", (cu,)).fetchone()["c"]
    n_rejected = conn.execute("SELECT COUNT(*) c FROM email_replies WHERE user_id=? AND classification='rejected'", (cu,)).fetchone()["c"]
    n_offer = conn.execute("SELECT COUNT(*) c FROM email_replies WHERE user_id=? AND classification='accepted'", (cu,)).fetchone()["c"]
    avg_match = conn.execute("SELECT AVG(match_score) a FROM jobs WHERE user_id=?", (cu,)).fetchone()["a"] or 0
    conn.close()
    response_rate = round((n_replies / n_apps * 100), 1) if n_apps else 0
    return jsonify({
        "jobs": n_jobs, "applications": n_apps, "replies": n_replies,
        "interviews": n_interview, "rejected": n_rejected, "offers": n_offer,
        "avg_match": round(avg_match, 1), "response_rate": response_rate
    })

# ─────────────────────────────────────────────────────────────────
# API — CLAUDE PROXY
# ─────────────────────────────────────────────────────────────────
@app.route("/api/claude", methods=["POST"])
@login_required
def claude_proxy():
    if not ANTHROPIC_API_KEY:
        return jsonify({"error": "ANTHROPIC_API_KEY is not set on the server. Edit your .env file and rebuild Docker."}), 500
    if not ANTHROPIC_API_KEY.startswith("sk-ant-"):
        return jsonify({"error": f"ANTHROPIC_API_KEY looks invalid (should start with 'sk-ant-', got '{ANTHROPIC_API_KEY[:12]}...'). Check your .env file."}), 500
    try:
        body = request.get_json(force=True)
        body.setdefault("model", "claude-sonnet-4-20250514")
        body.setdefault("max_tokens", 1500)
        r = requests.post("https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            },
            json=body, timeout=90)
        data = r.json()
        # If Anthropic returns an error, surface it clearly
        if r.status_code != 200 or data.get("type") == "error":
            err_msg = data.get("error", {}).get("message") if isinstance(data.get("error"), dict) else data.get("error", "Unknown")
            return jsonify({"error": f"Anthropic API error ({r.status_code}): {err_msg}"}), r.status_code or 500
        return jsonify(data), 200
    except requests.exceptions.Timeout:
        return jsonify({"error": "Request to Anthropic timed out after 90s"}), 504
    except requests.exceptions.ConnectionError:
        return jsonify({"error": "Cannot reach api.anthropic.com — check Docker container has internet access"}), 502
    except Exception as e:
        return jsonify({"error": f"Proxy error: {type(e).__name__}: {str(e)}"}), 500

@app.route("/health")
def health():
    return jsonify({"status": "ok"})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
