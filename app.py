import os
import requests
from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from functools import wraps
from datetime import timedelta

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-in-production")
app.permanent_session_lifetime = timedelta(hours=12)

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ADMIN_USERNAME    = os.environ.get("ADMIN_USERNAME", "saddam")
ADMIN_PASSWORD    = os.environ.get("ADMIN_PASSWORD", "Hunter2026!")


# ---------- auth guard ----------
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


# ---------- routes ----------
@app.route("/")
def index():
    return redirect(url_for("dashboard") if session.get("logged_in") else url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        u = request.form.get("username", "").strip()
        p = request.form.get("password", "").strip()
        if u == ADMIN_USERNAME and p == ADMIN_PASSWORD:
            session.permanent = True
            session["logged_in"] = True
            session["username"] = u
            return redirect(url_for("dashboard"))
        error = "Wrong username or password."
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/dashboard")
@login_required
def dashboard():
    return render_template("dashboard.html", username=session.get("username", ""))


# ---------- Claude API proxy (key lives server-side only) ----------
@app.route("/api/claude", methods=["POST"])
@login_required
def claude_proxy():
    if not ANTHROPIC_API_KEY:
        return jsonify({"error": "ANTHROPIC_API_KEY not set on server."}), 500
    try:
        body = request.get_json(force=True)
        body.setdefault("model", "claude-sonnet-4-20250514")
        body.setdefault("max_tokens", 1200)
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json=body,
            timeout=90,
        )
        return jsonify(resp.json()), resp.status_code
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
