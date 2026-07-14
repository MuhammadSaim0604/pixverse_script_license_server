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
from datetime import datetime
from functools import wraps

from flask import (Flask, render_template, request, jsonify,
                   session, redirect, url_for, flash)

import base64
from models import (
    AdminUser, LicenseKey, DailyUsage, AuditLog, ScriptStorage,
    get_dashboard_stats, DAILY_ACCOUNT_LIMIT, _ts
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
