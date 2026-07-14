"""
License Server — Flask Application
Developer: Muhammad Saim - Software Engineer

Endpoints (all client API endpoints use HMAC + AES-256 encryption):
  GET  /api/ping              — Health check
  POST /api/verify            — Verify license key
  POST /api/report_milestone  — Report 50%/100% milestone
  POST /api/request_creation  — Ask if N accounts may be created

Admin Dashboard (session-protected):
  GET/POST /admin/login       — Login
  GET      /admin             — Dashboard (license list + stats)
  POST     /admin/create      — Create new license
  POST     /admin/revoke      — Revoke/reactivate license
  POST     /admin/delete      — Delete license
  POST     /admin/edit        — Edit license details
  GET      /admin/audit       — Audit log
  POST     /admin/change_pw   — Change admin password
  GET      /admin/logout      — Logout
"""

import hashlib
import hmac
import json
import logging
import os
import time
import shutil
import sqlite3
import tempfile
from datetime import datetime
from functools import wraps
from io import BytesIO

from flask import (Flask, render_template, request, jsonify,
                   session, redirect, url_for, flash, send_file)

import base64
from models import (
    AdminUser, LicenseKey, DailyUsage, AuditLog, ScriptStorage,
    get_dashboard_stats, DAILY_ACCOUNT_LIMIT, _ts, db, DB_FILE
)

# ─── App Setup ────────────────────────────────────────────────────────────────
app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "pixverse_license_server_secret_2024_change_me")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s"
)
logger = logging.getLogger(__name__)

# Shared secret between sell_script and license_server
SHARED_SECRET = os.environ.get(
    "LICENSE_SHARED_SECRET",
    "PIXVERSE_SELL_SECRET_KEY_CHANGE_IN_PRODUCTION_2024"
)


# ─── HMAC + AES Helpers (server side) ────────────────────────────────────────
# We import the same crypto primitives as sell_license_client.py
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives import padding as sym_padding
from cryptography.hazmat.backends import default_backend
import base64


def _derive_aes_key(secret: str) -> bytes:
    return hashlib.sha256(secret.encode()).digest()


def _derive_hmac_key(secret: str) -> bytes:
    return hashlib.sha256((secret + ":hmac").encode()).digest()


AES_KEY  = _derive_aes_key(SHARED_SECRET)
HMAC_KEY = _derive_hmac_key(SHARED_SECRET)


def _decrypt_payload(ct_b64: str, iv_b64: str) -> str:
    ct  = base64.b64decode(ct_b64)
    iv  = base64.b64decode(iv_b64)
    cipher    = Cipher(algorithms.AES(AES_KEY), modes.CBC(iv), backend=default_backend())
    decryptor = cipher.decryptor()
    padded    = decryptor.update(ct) + decryptor.finalize()
    unpadder  = sym_padding.PKCS7(128).unpadder()
    return (unpadder.update(padded) + unpadder.finalize()).decode()


def _encrypt_payload(plaintext: str):
    iv      = os.urandom(16)
    padder  = sym_padding.PKCS7(128).padder()
    padded  = padder.update(plaintext.encode()) + padder.finalize()
    cipher  = Cipher(algorithms.AES(AES_KEY), modes.CBC(iv), backend=default_backend())
    enc     = cipher.encryptor()
    ct      = enc.update(padded) + enc.finalize()
    return base64.b64encode(ct).decode(), base64.b64encode(iv).decode()


def _sign(data: str) -> str:
    h = hmac.new(HMAC_KEY, data.encode(), hashlib.sha256)
    return h.hexdigest()


def _verify_sig(data: str, sig: str) -> bool:
    return hmac.compare_digest(_sign(data), sig)


def parse_secure_request(envelope: dict):
    """Decrypt and verify an incoming secure request envelope. Returns payload dict or None."""
    try:
        ct  = envelope.get("ct")
        iv  = envelope.get("iv")
        ts  = envelope.get("ts")
        sig = envelope.get("sig")
        if not all([ct, iv, ts, sig]):
            return None, "Missing fields"
        if abs(int(time.time()) - int(ts)) > 300:
            return None, "Timestamp too old (replay attack guard)"
        sig_data = f"{ct}:{iv}:{ts}"
        if not _verify_sig(sig_data, sig):
            return None, "HMAC verification failed"
        plaintext = _decrypt_payload(ct, iv)
        return json.loads(plaintext), None
    except Exception as e:
        return None, str(e)


def build_secure_response(payload: dict) -> dict:
    """Encrypt a response payload into a secure envelope."""
    payload["ts"] = int(time.time())
    pt  = json.dumps(payload, separators=(",", ":"))
    ct, iv = _encrypt_payload(pt)
    sig_data = f"{ct}:{iv}:{payload['ts']}"
    sig      = _sign(sig_data)
    return {"ct": ct, "iv": iv, "ts": payload["ts"], "sig": sig}


def secure_response(payload: dict, status: int = 200):
    return jsonify(build_secure_response(payload)), status


# ─── Auth Decorator ────────────────────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("admin_logged_in"):
            return redirect(url_for("admin_login"))
        return f(*args, **kwargs)
    return decorated


# ─── Client API Endpoints ─────────────────────────────────────────────────────

@app.route("/api/ping", methods=["GET"])
def api_ping():
    return jsonify({"status": "ok", "ts": int(time.time())}), 200


@app.route("/api/verify", methods=["POST"])
def api_verify():
    """Verify a license key. Body: secure envelope containing {license_key, machine_id}."""
    envelope = request.get_json(silent=True)
    if not envelope:
        return jsonify({"error": "No body"}), 400

    payload, err = parse_secure_request(envelope)
    if not payload:
        logger.warning(f"verify: bad envelope from {request.remote_addr}: {err}")
        AuditLog.log("", "verify_tampered", details=err, ip_address=request.remote_addr)
        return secure_response({"valid": False, "message": "Invalid request signature"})

    license_key = payload.get("license_key", "").strip()
    machine_id  = payload.get("machine_id", "").strip()

    lic = LicenseKey.get(license_key)

    if not lic:
        AuditLog.log(license_key, "verify_not_found", machine_id, ip_address=request.remote_addr)
        return secure_response({"valid": False, "message": "License key not found"})

    if not lic["is_active"]:
        AuditLog.log(license_key, "verify_inactive", machine_id, ip_address=request.remote_addr)
        return secure_response({"valid": False, "message": "License key has been revoked"})

    if LicenseKey.is_expired(lic):
        AuditLog.log(license_key, "verify_expired", machine_id, ip_address=request.remote_addr)
        return secure_response({"valid": False, "message": "License key has expired"})

    # Machine binding: once activated, only this machine can use the key
    if lic.get("machine_id") and lic["machine_id"] != machine_id:
        AuditLog.log(license_key, "verify_machine_mismatch", machine_id,
                     details=f"Expected: {lic['machine_id']}", ip_address=request.remote_addr)
        return secure_response({"valid": False, "message": "License bound to a different machine"})

    # Activate if first use
    if not lic.get("machine_id"):
        LicenseKey.activate(license_key, machine_id)
    else:
        LicenseKey.touch(license_key)

    usage = DailyUsage.get_today(license_key)
    created   = usage.get("accounts_created", 0)
    limit     = lic.get("daily_limit", DAILY_ACCOUNT_LIMIT)
    remaining = max(0, limit - created)

    AuditLog.log(license_key, "verify_ok", machine_id,
                 details=f"daily: {created}/{limit}", ip_address=request.remote_addr)

    return secure_response({
        "valid":             True,
        "message":           f"License valid. Daily: {created}/{limit}",
        "remaining_accounts": remaining,
        "daily_limit":       limit,
        "customer_name":     lic.get("customer_name", ""),
    })


@app.route("/api/report_milestone", methods=["POST"])
def api_report_milestone():
    """Client reports a 50% or 100% daily usage milestone."""
    envelope = request.get_json(silent=True)
    if not envelope:
        return jsonify({"error": "No body"}), 400

    payload, err = parse_secure_request(envelope)
    if not payload:
        AuditLog.log("", "milestone_tampered", details=err, ip_address=request.remote_addr)
        return secure_response({"success": False, "message": "Invalid request"})

    license_key      = payload.get("license_key", "").strip()
    machine_id       = payload.get("machine_id", "")
    accounts_created = payload.get("accounts_created", 0)
    milestone        = payload.get("milestone", "")

    lic = LicenseKey.get(license_key)
    if not lic or not lic["is_active"]:
        return secure_response({"success": False, "message": "Invalid license"})

    DailyUsage.mark_milestone(license_key, milestone)
    AuditLog.log(license_key, f"milestone_{milestone}", machine_id,
                 details=f"accounts_created={accounts_created}",
                 ip_address=request.remote_addr)

    msg = f"{'50%' if milestone == '50_percent' else '100%'} milestone recorded."
    if milestone == "100_percent":
        msg += " Account creation blocked for today."

    return secure_response({"success": True, "message": msg})


@app.route("/api/request_creation", methods=["POST"])
def api_request_creation():
    """
    Client asks: may I create `count` more accounts?
    Server is the single source of truth — the client cannot bypass this.
    """
    envelope = request.get_json(silent=True)
    if not envelope:
        return jsonify({"error": "No body"}), 400

    payload, err = parse_secure_request(envelope)
    if not payload:
        AuditLog.log("", "request_creation_tampered", details=err, ip_address=request.remote_addr)
        return secure_response({"allowed": False, "allowed_count": 0, "message": "Invalid request"})

    license_key = payload.get("license_key", "").strip()
    machine_id  = payload.get("machine_id", "")
    count       = int(payload.get("count", 1))

    lic = LicenseKey.get(license_key)
    if not lic or not lic["is_active"]:
        return secure_response({"allowed": False, "allowed_count": 0, "message": "Invalid license"})

    if LicenseKey.is_expired(lic):
        return secure_response({"allowed": False, "allowed_count": 0, "message": "License expired"})

    usage     = DailyUsage.get_today(license_key)
    created   = usage.get("accounts_created", 0)
    limit     = lic.get("daily_limit", DAILY_ACCOUNT_LIMIT)
    remaining = max(0, limit - created)

    if remaining == 0:
        return secure_response({
            "allowed":       False,
            "allowed_count": 0,
            "message":       f"Daily limit {limit} reached. Remaining: 0"
        })

    allowed_count = min(count, remaining)
    allowed       = allowed_count > 0

    # Increment usage
    try:
        DailyUsage.increment(license_key, allowed_count)
    except ValueError as e:
        return secure_response({"allowed": False, "allowed_count": 0, "message": str(e)})

    AuditLog.log(license_key, "request_creation", machine_id,
                 details=f"requested={count} allowed={allowed_count} remaining={remaining-allowed_count}",
                 ip_address=request.remote_addr)

    return secure_response({
        "allowed":       allowed,
        "allowed_count": allowed_count,
        "message":       f"Allowed {allowed_count}/{count}. Remaining after: {remaining - allowed_count}",
        "remaining":     remaining - allowed_count,
    })


# ─── Usage Reporting Endpoint ─────────────────────────────────────────────────

@app.route("/api/report_usage", methods=["POST"])
def api_report_usage():
    """Client reports post-task usage snapshot (after autonomous mode / bulk temp creator)."""
    envelope = request.get_json(silent=True)
    if not envelope:
        return jsonify({"error": "No body"}), 400

    payload, err = parse_secure_request(envelope)
    if not payload:
        AuditLog.log("", "report_usage_tampered", details=err, ip_address=request.remote_addr)
        return secure_response({"success": False, "message": "Invalid request"})

    license_key       = payload.get("license_key", "").strip()
    machine_id        = payload.get("machine_id", "")
    accounts_created  = int(payload.get("accounts_created", 0))
    accounts_remaining = int(payload.get("accounts_remaining", 0))
    task_type         = payload.get("task_type", "unknown")

    lic = LicenseKey.get(license_key)
    if not lic or not lic["is_active"]:
        return secure_response({"success": False, "message": "Invalid license"})

    # Sync server-side counter — use max(current, reported) so it never
    # double-counts if request_creation was also called during the session.
    DailyUsage.sync_count(license_key, accounts_created)

    AuditLog.log(license_key, f"usage_report_{task_type}", machine_id,
                 details=f"created={accounts_created} remaining={accounts_remaining}",
                 ip_address=request.remote_addr)

    return secure_response({
        "success": True,
        "message": f"Usage snapshot recorded: {accounts_created} created, {accounts_remaining} remaining"
    })


# ─── Script Download Endpoint ─────────────────────────────────────────────────

@app.route("/api/download_script", methods=["POST"])
def api_download_script():
    """
    Authenticated endpoint: valid license holders download the active .pyc script.
    Returns base64-encoded .pyc bytes (not encrypted — connection uses HMAC envelope).
    """
    envelope = request.get_json(silent=True)
    if not envelope:
        return jsonify({"error": "No body"}), 400

    payload, err = parse_secure_request(envelope)
    if not payload:
        AuditLog.log("", "download_script_tampered", details=err, ip_address=request.remote_addr)
        return jsonify({"success": False, "message": "Invalid request"}), 403

    license_key = payload.get("license_key", "").strip()
    machine_id  = payload.get("machine_id", "")

    lic = LicenseKey.get(license_key)
    if not lic:
        AuditLog.log(license_key, "download_script_not_found", machine_id,
                     ip_address=request.remote_addr)
        return jsonify({"success": False, "message": "License key not found"}), 403

    if not lic["is_active"]:
        AuditLog.log(license_key, "download_script_inactive", machine_id,
                     ip_address=request.remote_addr)
        return jsonify({"success": False, "message": "License revoked"}), 403

    if LicenseKey.is_expired(lic):
        return jsonify({"success": False, "message": "License expired"}), 403

    if lic.get("machine_id") and lic["machine_id"] != machine_id:
        return jsonify({"success": False, "message": "Machine mismatch"}), 403

    script = ScriptStorage.get_active()
    if not script:
        return jsonify({"success": False, "message": "No script available — contact admin"}), 404

    AuditLog.log(license_key, "download_script_ok", machine_id,
                 details=f"file={script['filename']} sha256={script['sha256'][:16]}...",
                 ip_address=request.remote_addr)

    return jsonify({
        "success":   True,
        "filename":  script["filename"],
        "sha256":    script["sha256"],
        "file_size": script["file_size"],
        "script_b64": base64.b64encode(script["file_data"]).decode()
    })


# ─── Admin Dashboard ──────────────────────────────────────────────────────────

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if session.get("admin_logged_in"):
        return redirect(url_for("admin_dashboard"))

    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if AdminUser.check_password(username, password):
            session["admin_logged_in"] = True
            session["admin_user"]      = username
            AdminUser.update_last_login(username)
            AuditLog.log("", "admin_login", details=username, ip_address=request.remote_addr)
            return redirect(url_for("admin_dashboard"))
        else:
            error = "Invalid username or password."
            AuditLog.log("", "admin_login_fail", details=username, ip_address=request.remote_addr)

    return render_template("login.html", error=error)


@app.route("/admin/logout")
@login_required
def admin_logout():
    session.clear()
    return redirect(url_for("admin_login"))


@app.route("/admin")
@login_required
def admin_dashboard():
    stats   = get_dashboard_stats()
    scripts = ScriptStorage.get_all()
    return render_template("dashboard.html", stats=stats, scripts=scripts,
                           admin_user=session.get("admin_user", "admin"))


@app.route("/admin/create", methods=["POST"])
@login_required
def admin_create_license():
    customer_name  = request.form.get("customer_name", "").strip()
    customer_email = request.form.get("customer_email", "").strip()
    daily_limit    = int(request.form.get("daily_limit", DAILY_ACCOUNT_LIMIT))
    expires_at     = request.form.get("expires_at", "").strip() or None
    notes          = request.form.get("notes", "").strip()

    # Server enforces max 6600
    daily_limit = min(daily_limit, DAILY_ACCOUNT_LIMIT)

    key = LicenseKey.create(
        customer_name=customer_name,
        customer_email=customer_email,
        daily_limit=daily_limit,
        expires_at=expires_at,
        notes=notes
    )
    AuditLog.log(key, "license_created",
                 details=f"customer={customer_name}, limit={daily_limit}",
                 ip_address=request.remote_addr)
    flash(f"License created: {key}", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/revoke", methods=["POST"])
@login_required
def admin_revoke_license():
    key    = request.form.get("license_key", "").strip()
    action = request.form.get("action", "revoke")
    if action == "revoke":
        LicenseKey.deactivate(key)
        AuditLog.log(key, "license_revoked", ip_address=request.remote_addr)
        flash(f"License revoked: {key}", "warning")
    else:
        LicenseKey.reactivate(key)
        AuditLog.log(key, "license_reactivated", ip_address=request.remote_addr)
        flash(f"License reactivated: {key}", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/delete", methods=["POST"])
@login_required
def admin_delete_license():
    key = request.form.get("license_key", "").strip()
    LicenseKey.delete(key)
    AuditLog.log(key, "license_deleted", ip_address=request.remote_addr)
    flash(f"License deleted: {key}", "danger")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/edit", methods=["POST"])
@login_required
def admin_edit_license():
    key            = request.form.get("license_key", "").strip()
    customer_name  = request.form.get("customer_name", "").strip()
    customer_email = request.form.get("customer_email", "").strip()
    daily_limit    = int(request.form.get("daily_limit", DAILY_ACCOUNT_LIMIT))
    expires_at     = request.form.get("expires_at", "").strip() or None
    notes          = request.form.get("notes", "").strip()

    daily_limit = min(daily_limit, DAILY_ACCOUNT_LIMIT)

    LicenseKey.update(key,
        customer_name=customer_name,
        customer_email=customer_email,
        daily_limit=daily_limit,
        expires_at=expires_at,
        notes=notes
    )
    AuditLog.log(key, "license_edited", ip_address=request.remote_addr)
    flash(f"License updated: {key}", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/audit")
@login_required
def admin_audit():
    logs = AuditLog.get_recent(200)
    return render_template("audit.html", logs=logs,
                           admin_user=session.get("admin_user", "admin"))


@app.route("/admin/license/<key>")
@login_required
def admin_license_detail(key):
    lic     = LicenseKey.get(key)
    usage   = DailyUsage.get_history(key, 30)
    logs    = AuditLog.get_for_license(key, 50)
    return render_template("license_detail.html", lic=lic, usage=usage,
                           logs=logs, admin_user=session.get("admin_user", "admin"),
                           daily_limit=DAILY_ACCOUNT_LIMIT)


@app.route("/admin/change_pw", methods=["POST"])
@login_required
def admin_change_password():
    current  = request.form.get("current_password", "")
    new_pw   = request.form.get("new_password", "")
    confirm  = request.form.get("confirm_password", "")
    username = session.get("admin_user", "admin")

    if not AdminUser.check_password(username, current):
        flash("Current password is incorrect.", "danger")
        return redirect(url_for("admin_dashboard"))

    if new_pw != confirm:
        flash("New passwords do not match.", "danger")
        return redirect(url_for("admin_dashboard"))

    if len(new_pw) < 8:
        flash("Password must be at least 8 characters.", "danger")
        return redirect(url_for("admin_dashboard"))

    AdminUser.change_password(username, new_pw)
    AuditLog.log("", "admin_password_changed", details=username, ip_address=request.remote_addr)
    flash("Password changed successfully.", "success")
    return redirect(url_for("admin_dashboard"))


# ─── Script Upload / Management ───────────────────────────────────────────────

@app.route("/admin/upload_script", methods=["POST"])
@login_required
def admin_upload_script():
    """Admin uploads the compiled .pyc script that licensed clients will download."""
    if "script_file" not in request.files:
        flash("No file selected.", "danger")
        return redirect(url_for("admin_dashboard"))

    f      = request.files["script_file"]
    if not f.filename:
        flash("No file selected.", "danger")
        return redirect(url_for("admin_dashboard"))

    data           = f.read()
    version_label  = request.form.get("version_label", "").strip()
    username       = session.get("admin_user", "admin")

    script_id = ScriptStorage.save(f.filename, data, username, version_label)
    AuditLog.log("", "script_uploaded",
                 details=f"file={f.filename} size={len(data)} version={version_label} id={script_id}",
                 ip_address=request.remote_addr)
    flash(f"Script '{f.filename}' uploaded and set as active ({len(data):,} bytes).", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/script/activate/<int:script_id>", methods=["POST"])
@login_required
def admin_activate_script(script_id):
    ScriptStorage.set_active(script_id)
    AuditLog.log("", "script_activated", details=f"id={script_id}",
                 ip_address=request.remote_addr)
    flash(f"Script #{script_id} set as active.", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/script/delete/<int:script_id>", methods=["POST"])
@login_required
def admin_delete_script(script_id):
    ScriptStorage.delete(script_id)
    AuditLog.log("", "script_deleted", details=f"id={script_id}",
                 ip_address=request.remote_addr)
    flash(f"Script #{script_id} deleted.", "warning")
    return redirect(url_for("admin_dashboard"))


# ─── Database Export / Import ─────────────────────────────────────────────────

@app.route("/admin/export_db", methods=["GET"])
@login_required
def admin_export_db():
    """
    Export the entire SQLite database as a downloadable file.
    Database is exported with timestamp to prevent accidental overwrites.
    """
    try:
        # Read the database file directly (SQLite databases are just files)
        # First close any existing connections to prevent locking
        import tempfile
        
        # Create a temporary copy of the database
        temp_fd, temp_path = tempfile.mkstemp(suffix=".db")
        try:
            # Use backup to create a clean copy
            with sqlite3.connect(str(DB_FILE)) as source_conn:
                with sqlite3.connect(temp_path) as temp_conn:
                    source_conn.backup(temp_conn)
            
            # Read the temporary file
            with open(temp_path, "rb") as f:
                db_data = f.read()
        finally:
            os.close(temp_fd)
            os.unlink(temp_path)
        
        export_buffer = BytesIO(db_data)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"license_server_backup_{timestamp}.db"
        
        AuditLog.log("", "database_exported", 
                    details=f"file={filename}", 
                    ip_address=request.remote_addr)
        
        return send_file(
            export_buffer,
            mimetype="application/octet-stream",
            as_attachment=True,
            download_name=filename
        )
    except Exception as e:
        logger.error(f"Database export failed: {e}")
        AuditLog.log("", "database_export_failed", details=str(e),
                    ip_address=request.remote_addr)
        flash(f"Export failed: {str(e)}", "danger")
        return redirect(url_for("admin_dashboard"))


@app.route("/admin/export_db_json", methods=["GET"])
@login_required
def admin_export_db_json():
    """
    Export database as JSON format for easier inspection/import.
    Includes all tables and data.
    """
    try:
        export_data = {
            "export_timestamp": datetime.now().isoformat(),
            "server_version": "1.0",
            "tables": {},
            "schema": {}
        }
        
        with db.get_connection() as conn:
            # Get all user table names (exclude ALL SQLite internal tables including sqlite_sequence)
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name != 'sqlite_sequence' ORDER BY name"
            ).fetchall()
            
            for table_row in tables:
                table_name = table_row[0]
                
                # Store the CREATE TABLE statement for accurate schema restoration
                schema_stmt = conn.execute(
                    "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                    (table_name,)
                ).fetchone()
                export_data["schema"][table_name] = schema_stmt[0] if schema_stmt else None
                
                rows = conn.execute(f"SELECT * FROM [{table_name}]").fetchall()
                
                # Convert rows to list of dicts
                table_data = []
                if rows:
                    columns = [description[0] for description in conn.execute(
                        f"PRAGMA table_info({table_name})"
                    ).fetchall()]
                    
                    for row in rows:
                        row_dict = dict(row)
                        table_data.append(row_dict)
                
                export_data["tables"][table_name] = table_data
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"license_server_backup_{timestamp}.json"
        
        json_buffer = BytesIO(json.dumps(export_data, indent=2, default=str).encode())
        
        AuditLog.log("", "database_exported_json", 
                    details=f"file={filename}", 
                    ip_address=request.remote_addr)
        
        return send_file(
            json_buffer,
            mimetype="application/json",
            as_attachment=True,
            download_name=filename
        )
    except Exception as e:
        logger.error(f"JSON export failed: {e}")
        AuditLog.log("", "database_export_json_failed", details=str(e),
                    ip_address=request.remote_addr)
        flash(f"JSON export failed: {str(e)}", "danger")
        return redirect(url_for("admin_dashboard"))


@app.route("/admin/import_db", methods=["GET", "POST"])
@login_required
def admin_import_db():
    """
    Import database from uploaded SQLite or JSON backup file.
    Creates backup of current DB before import.
    Supports both .db (SQLite) and .json (JSON export) formats.
    """
    if request.method == "GET":
        return render_template("import_db.html", 
                             admin_user=session.get("admin_user", "admin"))
    
    if "backup_file" not in request.files:
        flash("No file selected for import", "danger")
        return redirect(url_for("admin_import_db"))
    
    file = request.files["backup_file"]
    if not file.filename:
        flash("No file selected for import", "danger")
        return redirect(url_for("admin_import_db"))
    
    try:
        file_data = file.read()
        filename_lower = file.filename.lower()
        
        # Create backup of current database
        backup_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = DB_FILE.parent / f"license_server_backup_pre_import_{backup_timestamp}.db"
        shutil.copy2(DB_FILE, backup_path)
        
        # Determine file type and import accordingly
        if filename_lower.endswith(".json"):
            # Import from JSON format
            try:
                import_data = json.loads(file_data.decode())
                if "tables" not in import_data:
                    flash("Invalid JSON format. Missing 'tables' key.", "danger")
                    return redirect(url_for("admin_import_db"))
                
                # Recreate database from JSON
                import_from_json(import_data)
                import_format = "JSON"
                
            except json.JSONDecodeError as e:
                flash(f"Invalid JSON format: {str(e)}", "danger")
                return redirect(url_for("admin_import_db"))
        
        elif filename_lower.endswith((".db", ".sqlite", ".sqlite3")):
            # Import SQLite database file
            if not file_data.startswith(b"SQLite format 3"):
                flash("Invalid database file. Must be SQLite format.", "danger")
                return redirect(url_for("admin_import_db"))
            
            # Write imported database
            with open(DB_FILE, "wb") as f:
                f.write(file_data)
            import_format = "SQLite"
        
        else:
            flash("Unsupported file format. Use .db, .sqlite, .sqlite3, or .json", "danger")
            return redirect(url_for("admin_import_db"))
        
        AuditLog.log("", "database_imported", 
                    details=f"filename={file.filename} format={import_format} backup_created={backup_path.name}",
                    ip_address=request.remote_addr)
        
        flash(f"Database imported successfully ({import_format})! Backup saved: {backup_path.name}", "success")
        return redirect(url_for("admin_dashboard"))
        
    except Exception as e:
        logger.error(f"Database import failed: {e}")
        AuditLog.log("", "database_import_failed", details=str(e),
                    ip_address=request.remote_addr)
        flash(f"Import failed: {str(e)}", "danger")
        return redirect(url_for("admin_import_db"))


def infer_column_types(rows: list) -> dict:
    """
    Infer column types from data rows.
    Returns {column_name: 'INTEGER'|'REAL'|'TEXT'}
    """
    if not rows:
        return {}
    
    col_types = {}
    for col in rows[0].keys():
        # Default to TEXT
        col_type = "TEXT"
        
        # Check if column contains numeric data
        is_integer = True
        is_real = True
        
        for row in rows:
            val = row.get(col)
            if val is None:
                continue
            
            # Try to parse as integer
            if is_integer:
                try:
                    int(val)
                except (ValueError, TypeError):
                    is_integer = False
            
            # Try to parse as float
            if is_real and not is_integer:
                try:
                    float(val)
                except (ValueError, TypeError):
                    is_real = False
        
        if is_integer:
            col_type = "INTEGER"
        elif is_real:
            col_type = "REAL"
        
        col_types[col] = col_type
    
    return col_types


def import_from_json(import_data: dict) -> None:
    """
    Restore database from JSON export format.
    Clears current database and repopulates from JSON data.
    Uses original schema from export if available, otherwise infers types.
    """
    with db.get_connection() as conn:
        c = conn.cursor()
        
        # Get list of user tables (exclude sqlite_* system tables)
        existing_tables = c.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        
        # Drop existing user tables
        for (table_name,) in existing_tables:
            c.execute(f"DROP TABLE IF EXISTS [{table_name}]")
        
        # Disable foreign key constraints temporarily
        c.execute("PRAGMA foreign_keys=OFF")
        
        # Recreate tables from JSON using original schema if available
        schema_dict = import_data.get("schema", {})
        tables_dict = import_data.get("tables", {})
        
        for table_name in sorted(tables_dict.keys()):
            # Skip SQLite internal tables
            if table_name.startswith('sqlite_'):
                continue
            
            rows = tables_dict[table_name]
            if not rows:
                continue
            
            # Recreate table using original schema if available
            if table_name in schema_dict and schema_dict[table_name]:
                # Use original CREATE TABLE statement
                try:
                    c.execute(schema_dict[table_name])
                except sqlite3.OperationalError:
                    # Schema creation failed, fall back to type inference
                    first_row = rows[0]
                    col_types = infer_column_types(rows)
                    col_defs = ", ".join([f"[{col}] {col_types.get(col, 'TEXT')}" for col in first_row.keys()])
                    c.execute(f"CREATE TABLE IF NOT EXISTS [{table_name}] ({col_defs})")
            else:
                # Infer types from data
                first_row = rows[0]
                col_types = infer_column_types(rows)
                col_defs = ", ".join([f"[{col}] {col_types.get(col, 'TEXT')}" for col in first_row.keys()])
                c.execute(f"CREATE TABLE IF NOT EXISTS [{table_name}] ({col_defs})")
            
            # Insert rows with proper type conversion
            if rows:
                first_row = rows[0]
                columns = list(first_row.keys())
                placeholders = ", ".join(["?" for _ in columns])
                col_names = ", ".join([f"[{c}]" for c in columns])
                
                for row in rows:
                    values = []
                    for col in columns:
                        val = row.get(col)
                        # Keep None as is, SQLite will store as NULL
                        values.append(val)
                    
                    c.execute(
                        f"INSERT INTO [{table_name}] ({col_names}) VALUES ({placeholders})",
                        values
                    )
        
        # Re-enable foreign key constraints
        c.execute("PRAGMA foreign_keys=ON")
        conn.commit()


# ─── Entry Point ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port  = int(os.environ.get("LICENSE_SERVER_PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    print(f"\n{'='*60}")
    print(f"  License Server — Muhammad Saim (Software Engineer)")
    print(f"  Port   : {port}")
    print(f"  Debug  : {debug}")
    print(f"  Admin  : http://localhost:{port}/admin")
    print(f"  Default: admin / admin123  ← CHANGE IMMEDIATELY")
    print(f"{'='*60}\n")
    app.run(host="0.0.0.0", port=port, debug=debug)
