import os
import secrets
import string
from datetime import datetime, timezone
from decimal import Decimal
from functools import wraps

from flask import Flask, request, redirect, url_for, flash, jsonify, render_template, abort
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, current_user, login_required
from werkzeug.security import generate_password_hash, check_password_hash

APP_NAME = "Remote Print Network"
ROLES = ["pending", "device_node", "author_node", "moderator_node", "audit_only"]
ACCOUNT_STATES = ["pending", "active", "paused", "suspended", "deleted"]
CONNECTION_STATES = ["offline", "active", "suspended", "killed"]
JOB_STATES = [
    "registered", "offered", "accepted_by_device", "start_authorized", "in_progress",
    "completed_pending_author_ack", "paid", "aborted", "duplicate_rejected", "expired", "failed"
]


def utcnow():
    return datetime.now(timezone.utc)


def make_code(prefix="CODE", groups=3, size=4):
    alphabet = string.ascii_uppercase + string.digits
    parts = ["".join(secrets.choice(alphabet) for _ in range(size)) for _ in range(groups)]
    return prefix + "-" + "-".join(parts)


def normalize_database_url(url):
    if url and url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql://", 1)
    return url


app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("FLASK_SECRET_KEY", "dev-only-change-me")
app.config["SQLALCHEMY_DATABASE_URI"] = normalize_database_url(os.environ.get("DATABASE_URL")) or "sqlite:///print_network_demo.sqlite3"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
if os.environ.get("RENDER"):
    app.config["SESSION_COOKIE_SECURE"] = True

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"


class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(160), nullable=False)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    requested_role = db.Column(db.String(64), default="pending")
    approved_role = db.Column(db.String(64), default="pending")
    account_status = db.Column(db.String(64), default="pending")
    tier_level = db.Column(db.Integer, default=0)
    facility_name = db.Column(db.String(255), default="")
    facility_id = db.Column(db.String(120), default="")
    notes = db.Column(db.Text, default="")
    is_admin_superuser = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)
    approved_by_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)

    devices = db.relationship("Device", backref="owner", lazy=True, foreign_keys="Device.owner_user_id")

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def can_moderate(self):
        return self.is_admin_superuser or self.approved_role == "moderator_node"

    @property
    def is_active_account(self):
        return self.account_status == "active"


class Wallet(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), unique=True, nullable=False)
    simulated_balance_cents = db.Column(db.Integer, default=100000)
    escrow_held_cents = db.Column(db.Integer, default=0)
    total_received_cents = db.Column(db.Integer, default=0)
    user = db.relationship("User", backref=db.backref("wallet", uselist=False))


class Device(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    owner_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    device_name = db.Column(db.String(200), nullable=False)
    printer_model = db.Column(db.String(200), default="")
    device_identifier = db.Column(db.String(120), unique=True, nullable=False, index=True)
    declared_capabilities = db.Column(db.Text, default="")
    installed_nozzle_mm = db.Column(db.String(20), default="0.4")
    loaded_material = db.Column(db.String(80), default="PLA")
    approval_status = db.Column(db.String(64), default="pending")
    allowed_to_receive_jobs = db.Column(db.Boolean, default=False)
    tier_level = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)


class SessionCode(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(100), unique=True, nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    device_id = db.Column(db.Integer, db.ForeignKey("device.id"), nullable=True)
    purpose = db.Column(db.String(80), default="node_activation")
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)
    last_used_at = db.Column(db.DateTime(timezone=True), nullable=True)
    use_count = db.Column(db.Integer, default=0)
    deactivated_at = db.Column(db.DateTime(timezone=True), nullable=True)
    active = db.Column(db.Boolean, default=True)
    user = db.relationship("User", backref="session_codes")
    device = db.relationship("Device", backref="session_codes")

    @property
    def is_valid(self):
        return self.active and self.deactivated_at is None and self.user.account_status == "active"


class Connection(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    connection_id = db.Column(db.String(100), unique=True, nullable=False, index=True)
    session_code_id = db.Column(db.Integer, db.ForeignKey("session_code.id"), nullable=True)
    node_type = db.Column(db.String(80), nullable=False)
    display_name = db.Column(db.String(200), default="")
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    device_id = db.Column(db.Integer, db.ForeignKey("device.id"), nullable=True)
    status = db.Column(db.String(64), default="active")
    capabilities_json = db.Column(db.Text, default="{}")
    last_seen_at = db.Column(db.DateTime(timezone=True), default=utcnow)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)
    killed_at = db.Column(db.DateTime(timezone=True), nullable=True)
    session_code = db.relationship("SessionCode")
    user = db.relationship("User")
    device = db.relationship("Device")


class JobBatch(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    batch_code = db.Column(db.String(100), unique=True, nullable=False, index=True)
    author_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    author_connection_id = db.Column(db.String(100), nullable=True)
    device_connection_id = db.Column(db.String(100), nullable=True)
    title = db.Column(db.String(200), default="Untitled print batch")
    source_gcode_filename = db.Column(db.String(255), default="")
    authorized_count = db.Column(db.Integer, default=1)
    price_per_print_cents = db.Column(db.Integer, default=0)
    max_total_payout_cents = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)
    author = db.relationship("User")


class JobInstance(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    job_id = db.Column(db.String(100), unique=True, nullable=False, index=True)
    batch_id = db.Column(db.Integer, db.ForeignKey("job_batch.id"), nullable=False)
    author_job_code = db.Column(db.String(100), unique=True, nullable=False, index=True)
    instance_index = db.Column(db.Integer, default=1)
    gcode_filename = db.Column(db.String(255), default="")
    gcode_text = db.Column(db.Text, default="")
    metadata_json = db.Column(db.Text, default="{}")
    status = db.Column(db.String(80), default="registered")
    assigned_device_connection_id = db.Column(db.String(100), nullable=True)
    started_at = db.Column(db.DateTime(timezone=True), nullable=True)
    completed_at = db.Column(db.DateTime(timezone=True), nullable=True)
    paid_at = db.Column(db.DateTime(timezone=True), nullable=True)
    payment_cents = db.Column(db.Integer, default=0)
    duplicate_completion_count = db.Column(db.Integer, default=0)
    batch = db.relationship("JobBatch", backref="instances")


class LedgerEntry(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    job_instance_id = db.Column(db.Integer, db.ForeignKey("job_instance.id"), nullable=True)
    from_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    to_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    amount_cents = db.Column(db.Integer, default=0)
    reason = db.Column(db.String(255), default="")
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)
    job_instance = db.relationship("JobInstance")
    from_user = db.relationship("User", foreign_keys=[from_user_id])
    to_user = db.relationship("User", foreign_keys=[to_user_id])


class AuditRecord(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(255), nullable=False)
    related_code = db.Column(db.String(120), default="")
    notes = db.Column(db.Text, default="")
    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)
    created_by = db.relationship("User")


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


def money(cents):
    return f"${Decimal(cents or 0) / Decimal(100):,.2f}"


@app.template_filter("money")
def money_filter(cents):
    return money(cents)


@app.template_filter("dt")
def dt_filter(value):
    if not value:
        return "—"
    return value.strftime("%Y-%m-%d %H:%M:%S UTC")


def admin_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin_superuser:
            abort(403)
        return fn(*args, **kwargs)
    return wrapper


def moderator_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.can_moderate:
            abort(403)
        return fn(*args, **kwargs)
    return wrapper


def ensure_wallet(user):
    if not user.wallet:
        db.session.add(Wallet(user=user))
        db.session.commit()


def bootstrap():
    db.create_all()
    admin_email = os.environ.get("ADMIN_EMAIL", "admin@example.com")
    admin_password = os.environ.get("ADMIN_INITIAL_PASSWORD", "admin123")
    admin = User.query.filter_by(email=admin_email).first()
    if not admin:
        admin = User(
            name="Admin Superuser",
            email=admin_email,
            requested_role="admin_superuser",
            approved_role="admin_superuser",
            account_status="active",
            tier_level=5,
            facility_name="Network Administration",
            facility_id="ADMIN",
            is_admin_superuser=True,
        )
        admin.set_password(admin_password)
        db.session.add(admin)
        db.session.commit()
        ensure_wallet(admin)


with app.app_context():
    bootstrap()


@app.route("/")
def index():
    stats = {
        "users": User.query.count(),
        "devices": Device.query.count(),
        "connections": Connection.query.count(),
        "jobs": JobInstance.query.count(),
    }
    return render_template("index.html", app_name=APP_NAME, stats=stats)


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        if User.query.filter_by(email=email).first():
            flash("That email is already registered.", "error")
            return redirect(url_for("register"))
        user = User(
            name=request.form.get("name", "").strip() or "Unnamed user",
            email=email,
            requested_role=request.form.get("requested_role", "pending"),
            approved_role="pending",
            account_status="pending",
            tier_level=0,
            facility_name=request.form.get("facility_name", "").strip(),
            facility_id=request.form.get("facility_id", "").strip(),
        )
        user.set_password(request.form.get("password", ""))
        db.session.add(user)
        db.session.commit()
        ensure_wallet(user)
        flash("Registration submitted. An admin or moderator must approve the account before node activation.", "ok")
        return redirect(url_for("login"))
    return render_template("register.html", roles=[r for r in ROLES if r != "pending"])


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        user = User.query.filter_by(email=request.form.get("email", "").strip().lower()).first()
        if not user or not user.check_password(request.form.get("password", "")):
            flash("Invalid login.", "error")
            return redirect(url_for("login"))
        if user.account_status == "deleted":
            flash("This account has been deleted.", "error")
            return redirect(url_for("login"))
        login_user(user)
        return redirect(url_for("registration_dashboard"))
    return render_template("login.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    flash("Logged out.", "ok")
    return redirect(url_for("index"))


@app.route("/registration")
@login_required
def registration_dashboard():
    ensure_wallet(current_user)
    devices = Device.query.filter_by(owner_user_id=current_user.id).order_by(Device.created_at.desc()).all()
    codes = SessionCode.query.filter_by(user_id=current_user.id).order_by(SessionCode.created_at.desc()).limit(12).all()
    return render_template("registration_dashboard.html", devices=devices, codes=codes)


@app.route("/registration/device/new", methods=["GET", "POST"])
@login_required
def new_device():
    if request.method == "POST":
        ident = request.form.get("device_identifier", "").strip() or make_code("DEV", 2, 4)
        if Device.query.filter_by(device_identifier=ident).first():
            flash("That device identifier already exists.", "error")
            return redirect(url_for("new_device"))
        device = Device(
            owner_user_id=current_user.id,
            device_name=request.form.get("device_name", "").strip() or "Unnamed printer",
            printer_model=request.form.get("printer_model", "").strip(),
            device_identifier=ident,
            declared_capabilities=request.form.get("declared_capabilities", "").strip(),
            installed_nozzle_mm=request.form.get("installed_nozzle_mm", "0.4").strip(),
            loaded_material=request.form.get("loaded_material", "PLA").strip(),
        )
        db.session.add(device)
        db.session.commit()
        flash("Device registered. It must be approved before it can receive jobs.", "ok")
        return redirect(url_for("registration_dashboard"))
    return render_template("device_form.html")


@app.route("/registration/session-code", methods=["POST"])
@login_required
def generate_session_code():
    device_id = request.form.get("device_id", type=int)
    device = db.session.get(Device, device_id) if device_id else None
    if device and device.owner_user_id != current_user.id and not current_user.can_moderate:
        abort(403)
    code = SessionCode(
        code=make_code("REG", 3, 4),
        user_id=current_user.id,
        device_id=device.id if device else None,
        purpose=request.form.get("purpose", "node_activation"),
        active=True,
    )
    db.session.add(code)
    db.session.commit()
    flash(f"Generated persistent registration code: {code.code}", "ok")
    return redirect(url_for("registration_dashboard"))


@app.route("/registration/session-code/<int:code_id>/deactivate", methods=["POST"])
@login_required
def deactivate_session_code(code_id):
    code = db.session.get(SessionCode, code_id) or abort(404)
    if code.user_id != current_user.id and not current_user.can_moderate:
        abort(403)
    code.active = False
    code.deactivated_at = utcnow()
    db.session.commit()
    flash("Registration code deactivated.", "ok")
    return redirect(request.referrer or url_for("registration_dashboard"))


@app.route("/registration/delete-account", methods=["POST"])
@login_required
def delete_own_account():
    current_user.account_status = "deleted"
    db.session.commit()
    logout_user()
    flash("Account marked deleted.", "ok")
    return redirect(url_for("index"))


@app.route("/registration/admin/users")
@login_required
@moderator_required
def admin_users():
    users = User.query.order_by(User.created_at.desc()).all()
    return render_template("admin_users.html", users=users, roles=ROLES, states=ACCOUNT_STATES)


@app.route("/registration/admin/user/<int:user_id>", methods=["GET", "POST"])
@login_required
@moderator_required
def admin_user_detail(user_id):
    user = db.session.get(User, user_id) or abort(404)
    if request.method == "POST":
        action = request.form.get("action")
        if action == "save":
            user.approved_role = request.form.get("approved_role", user.approved_role)
            user.account_status = request.form.get("account_status", user.account_status)
            user.tier_level = request.form.get("tier_level", type=int) or 0
            user.notes = request.form.get("notes", "")
            user.approved_by_id = current_user.id
            if user.approved_role == "moderator_node" and not current_user.is_admin_superuser:
                flash("Only the admin superuser can promote users to moderator.", "error")
                return redirect(url_for("admin_user_detail", user_id=user.id))
            ensure_wallet(user)
            db.session.commit()
            flash("User updated.", "ok")
        elif action == "delete":
            if not current_user.is_admin_superuser:
                abort(403)
            user.account_status = "deleted"
            db.session.commit()
            flash("User marked deleted.", "ok")
        return redirect(url_for("admin_user_detail", user_id=user.id))
    return render_template("admin_user_detail.html", user=user, roles=ROLES, states=ACCOUNT_STATES)


@app.route("/registration/admin/devices")
@login_required
@moderator_required
def admin_devices():
    devices = Device.query.order_by(Device.created_at.desc()).all()
    return render_template("admin_devices.html", devices=devices)


@app.route("/registration/admin/device/<int:device_id>", methods=["GET", "POST"])
@login_required
@moderator_required
def admin_device_detail(device_id):
    device = db.session.get(Device, device_id) or abort(404)
    if request.method == "POST":
        device.approval_status = request.form.get("approval_status", device.approval_status)
        device.allowed_to_receive_jobs = bool(request.form.get("allowed_to_receive_jobs"))
        device.tier_level = request.form.get("tier_level", type=int) or 0
        device.installed_nozzle_mm = request.form.get("installed_nozzle_mm", device.installed_nozzle_mm)
        device.loaded_material = request.form.get("loaded_material", device.loaded_material)
        device.declared_capabilities = request.form.get("declared_capabilities", device.declared_capabilities)
        db.session.commit()
        flash("Device updated.", "ok")
        return redirect(url_for("admin_device_detail", device_id=device.id))
    return render_template("admin_device_detail.html", device=device)


@app.route("/routing")
@login_required
@moderator_required
def routing_dashboard():
    connections = Connection.query.order_by(Connection.last_seen_at.desc()).all()
    jobs = JobInstance.query.order_by(JobInstance.id.desc()).limit(50).all()
    ledger = LedgerEntry.query.order_by(LedgerEntry.created_at.desc()).limit(25).all()
    return render_template("routing_dashboard.html", connections=connections, jobs=jobs, ledger=ledger)


@app.route("/routing/connection/<int:conn_id>/<action>", methods=["POST"])
@login_required
@moderator_required
def manage_connection(conn_id, action):
    conn = db.session.get(Connection, conn_id) or abort(404)
    if action == "activate":
        conn.status = "active"
        conn.killed_at = None
    elif action == "suspend":
        conn.status = "suspended"
    elif action == "reset":
        conn.status = "offline"
    elif action == "kill":
        conn.status = "killed"
        conn.killed_at = utcnow()
    else:
        abort(404)
    db.session.commit()
    flash(f"Connection {action} applied.", "ok")
    return redirect(url_for("routing_dashboard"))


@app.route("/audit", methods=["GET", "POST"])
@login_required
def audit_records():
    if request.method == "POST":
        rec = AuditRecord(
            title=request.form.get("title", "Untitled audit record"),
            related_code=request.form.get("related_code", ""),
            notes=request.form.get("notes", ""),
            created_by_id=current_user.id,
        )
        db.session.add(rec)
        db.session.commit()
        flash("Audit record added.", "ok")
        return redirect(url_for("audit_records"))
    records = AuditRecord.query.order_by(AuditRecord.created_at.desc()).all()
    return render_template("audit_records.html", records=records)


# ---------------- API: routing hub / node clients ----------------


def api_error(message, status=400, **extra):
    payload = {"ok": False, "error": message}
    payload.update(extra)
    return jsonify(payload), status


@app.post("/api/registration/activate")
def api_activate_registration_code():
    data = request.get_json(silent=True) or request.form
    code_text = (data.get("registration_code") or data.get("session_code") or "").strip()
    code = SessionCode.query.filter_by(code=code_text).first()
    if not code or not code.is_valid:
        return api_error("Registration code is invalid, inactive, deactivated, or account is not active.", 403)
    code.last_used_at = utcnow()
    code.use_count = (code.use_count or 0) + 1
    db.session.commit()
    user = code.user
    device = code.device
    return jsonify({
        "ok": True,
        "user": {
            "id": user.id,
            "name": user.name,
            "email": user.email,
            "approved_role": user.approved_role,
            "account_status": user.account_status,
            "tier_level": user.tier_level,
            "facility_name": user.facility_name,
            "facility_id": user.facility_id,
        },
        "device": {
            "id": device.id,
            "device_name": device.device_name,
            "device_identifier": device.device_identifier,
            "allowed_to_receive_jobs": device.allowed_to_receive_jobs,
            "approval_status": device.approval_status,
            "tier_level": device.tier_level,
            "installed_nozzle_mm": device.installed_nozzle_mm,
            "loaded_material": device.loaded_material,
        } if device else None,
        "wallet_visible_to_self": {
            "simulated_balance_cents": user.wallet.simulated_balance_cents if user.wallet else 0,
            "escrow_held_cents": user.wallet.escrow_held_cents if user.wallet else 0,
        }
    })


@app.post("/api/routing/connect")
def api_connect_node():
    data = request.get_json(silent=True) or request.form
    code_text = (data.get("registration_code") or data.get("session_code") or "").strip()
    node_type = (data.get("node_type") or "unknown_node").strip()
    display_name = (data.get("display_name") or data.get("node_name") or node_type).strip()
    capabilities_json = data.get("capabilities_json") or data.get("capabilities") or "{}"
    code = SessionCode.query.filter_by(code=code_text).first()
    if not code or not code.is_valid:
        return api_error("Registration code is invalid or inactive.", 403)
    if node_type == "device_node" and code.device and not code.device.allowed_to_receive_jobs:
        return api_error("Device is registered but not currently allowed to receive jobs.", 403, status="suspended")
    code.last_used_at = utcnow()
    code.use_count = (code.use_count or 0) + 1
    existing = Connection.query.filter_by(session_code_id=code.id, node_type=node_type).first()
    if existing and existing.status != "killed":
        existing.status = "active"
        existing.display_name = display_name
        existing.last_seen_at = utcnow()
        existing.capabilities_json = str(capabilities_json)
        conn = existing
    else:
        conn = Connection(
            connection_id=make_code("CONN", 3, 4),
            session_code_id=code.id,
            node_type=node_type,
            display_name=display_name,
            user_id=code.user_id,
            device_id=code.device_id,
            capabilities_json=str(capabilities_json),
            status="active",
        )
        db.session.add(conn)
    db.session.commit()
    return jsonify({"ok": True, "connection_id": conn.connection_id, "status": conn.status, "node_type": conn.node_type})


@app.post("/api/routing/heartbeat")
def api_heartbeat():
    data = request.get_json(silent=True) or request.form
    conn = Connection.query.filter_by(connection_id=data.get("connection_id", "")).first()
    if not conn:
        return api_error("Unknown connection.", 404)
    if conn.status == "killed":
        return api_error("Connection was killed by routing hub admin.", 403, status="killed")
    conn.last_seen_at = utcnow()
    db.session.commit()
    return jsonify({"ok": True, "status": conn.status})


@app.get("/api/routing/status/<connection_id>")
def api_status(connection_id):
    conn = Connection.query.filter_by(connection_id=connection_id).first()
    if not conn:
        return api_error("Unknown connection.", 404)
    author_active = Connection.query.filter(Connection.node_type == "author_node", Connection.status == "active").count() > 0
    return jsonify({"ok": True, "status": conn.status, "node_type": conn.node_type, "author_node_active": author_active, "last_seen_at": dt_filter(conn.last_seen_at)})



@app.get("/api/routing/active-devices")
def api_active_devices():
    devices = Connection.query.filter_by(node_type="device_node", status="active").order_by(Connection.last_seen_at.desc()).all()
    rows = []
    for c in devices:
        d = c.device
        u = c.user
        rows.append({
            "connection_id": c.connection_id,
            "display_name": c.display_name,
            "status": c.status,
            "last_seen_at": dt_filter(c.last_seen_at),
            "tier_level": d.tier_level if d else (u.tier_level if u else 0),
            "capabilities_json": c.capabilities_json,
            "device": {
                "id": d.id,
                "device_name": d.device_name,
                "device_identifier": d.device_identifier,
                "installed_nozzle_mm": d.installed_nozzle_mm,
                "loaded_material": d.loaded_material,
                "allowed_to_receive_jobs": d.allowed_to_receive_jobs,
                "approval_status": d.approval_status,
            } if d else None,
            "user": {
                "id": u.id,
                "name": u.name,
                "facility_id": u.facility_id,
            } if u else None,
        })
    return jsonify({"ok": True, "devices": rows})

@app.post("/api/routing/disconnect")
def api_disconnect():
    data = request.get_json(silent=True) or request.form
    conn = Connection.query.filter_by(connection_id=data.get("connection_id", "")).first()
    if not conn:
        return api_error("Unknown connection.", 404)
    conn.status = "offline"
    conn.last_seen_at = utcnow()
    db.session.commit()
    return jsonify({"ok": True, "status": conn.status})


@app.post("/api/jobs/register-bundle")
def api_register_bundle():
    data = request.get_json(silent=True) or {}
    author_conn = Connection.query.filter_by(connection_id=data.get("author_connection_id", "")).first()
    if not author_conn or author_conn.status != "active" or author_conn.node_type != "author_node":
        return api_error("Active author node connection required.", 403)
    count = int(data.get("authorized_count", 1))
    if count < 1 or count > 5:
        return api_error("authorized_count must be between 1 and 5.")
    price_cents = int(data.get("price_per_print_cents", 0))
    batch = JobBatch(
        batch_code=make_code("BATCH", 3, 4),
        author_user_id=author_conn.user_id,
        author_connection_id=author_conn.connection_id,
        device_connection_id=data.get("device_connection_id"),
        title=data.get("title", "Untitled print batch"),
        source_gcode_filename=data.get("source_gcode_filename", ""),
        authorized_count=count,
        price_per_print_cents=price_cents,
        max_total_payout_cents=count * price_cents,
    )
    db.session.add(batch)
    db.session.flush()
    provided_instances = data.get("instances") or []
    created = []
    for i in range(count):
        src = provided_instances[i] if i < len(provided_instances) else {}
        code = src.get("author_job_code") or make_code("JOB", 3, 4)
        if JobInstance.query.filter_by(author_job_code=code).first():
            return api_error(f"Duplicate author_job_code: {code}")
        inst = JobInstance(
            job_id=src.get("job_id") or make_code("PRINT", 3, 4),
            batch_id=batch.id,
            author_job_code=code,
            instance_index=i + 1,
            gcode_filename=src.get("gcode_filename") or f"instance_{i+1:03d}.gcode",
            gcode_text=src.get("gcode_text", ""),
            metadata_json=str(src.get("metadata", {})),
            status="offered",
            assigned_device_connection_id=data.get("device_connection_id"),
            payment_cents=price_cents,
        )
        db.session.add(inst)
        created.append(inst)
    db.session.commit()
    return jsonify({
        "ok": True,
        "batch_code": batch.batch_code,
        "authorized_count": count,
        "price_per_print_cents": price_cents,
        "max_total_payout_cents": batch.max_total_payout_cents,
        "instances": [{"job_id": j.job_id, "author_job_code": j.author_job_code, "status": j.status} for j in created]
    })


@app.get("/api/jobs/available")
def api_available_jobs():
    device_conn_id = request.args.get("device_connection_id") or request.args.get("connection_id")
    conn = Connection.query.filter_by(connection_id=device_conn_id).first()
    if not conn or conn.status != "active" or conn.node_type != "device_node":
        return api_error("Active device node connection required.", 403)
    jobs = JobInstance.query.filter(
        JobInstance.status == "offered",
        (JobInstance.assigned_device_connection_id == None) | (JobInstance.assigned_device_connection_id == device_conn_id)
    ).order_by(JobInstance.id.asc()).limit(25).all()
    return jsonify({"ok": True, "jobs": [serialize_job(j, include_payload=False) for j in jobs]})


def serialize_job(j, include_payload=False):
    payload = {
        "job_id": j.job_id,
        "batch_code": j.batch.batch_code,
        "author_job_code": j.author_job_code,
        "instance_index": j.instance_index,
        "gcode_filename": j.gcode_filename,
        "status": j.status,
        "price_cents": j.payment_cents,
        "paid": bool(j.paid_at),
    }
    if include_payload:
        payload["gcode_text"] = j.gcode_text
        payload["metadata_json"] = j.metadata_json
    return payload


@app.post("/api/jobs/claim")
def api_claim_job():
    data = request.get_json(silent=True) or request.form
    conn = Connection.query.filter_by(connection_id=data.get("device_connection_id", "")).first()
    job = JobInstance.query.filter_by(job_id=data.get("job_id", "")).first()
    if not conn or conn.status != "active" or conn.node_type != "device_node":
        return api_error("Active device node connection required.", 403)
    if not job or job.status != "offered":
        return api_error("Job is not available.")
    if job.assigned_device_connection_id and job.assigned_device_connection_id != conn.connection_id:
        return api_error("Job is assigned to a different device.", 403)
    job.assigned_device_connection_id = conn.connection_id
    job.status = "accepted_by_device"
    db.session.commit()
    return jsonify({"ok": True, "job": serialize_job(job, include_payload=True)})


@app.post("/api/jobs/start")
def api_start_job():
    data = request.get_json(silent=True) or request.form
    conn = Connection.query.filter_by(connection_id=data.get("device_connection_id", "")).first()
    job = JobInstance.query.filter_by(job_id=data.get("job_id", "")).first()
    if not conn or conn.status != "active" or conn.node_type != "device_node":
        return api_error("Active device node connection required.", 403)
    if not job:
        return api_error("Unknown job.", 404)
    if job.assigned_device_connection_id != conn.connection_id:
        return api_error("Job is not assigned to this device.", 403)
    if job.paid_at or job.status in ["paid", "completed_pending_author_ack"]:
        job.duplicate_completion_count += 1
        db.session.commit()
        return api_error("Job instance has already completed or paid; duplicate start rejected.", 409)
    if job.status not in ["accepted_by_device", "start_authorized"]:
        return api_error(f"Job is not startable from current state: {job.status}", 409)
    job.status = "in_progress"
    job.started_at = utcnow()
    db.session.commit()
    return jsonify({"ok": True, "status": job.status, "job": serialize_job(job, include_payload=True)})


@app.post("/api/jobs/complete")
def api_complete_job():
    data = request.get_json(silent=True) or request.form
    conn = Connection.query.filter_by(connection_id=data.get("device_connection_id", "")).first()
    job = JobInstance.query.filter_by(job_id=data.get("job_id", "")).first()
    completion_status = data.get("completion_status", "completed")
    if completion_status not in ["completed", "aborted", "failed"]:
        return api_error("completion_status must be completed, aborted, or failed.")
    if not conn or conn.status != "active" or conn.node_type != "device_node":
        return api_error("Active device node connection required.", 403)
    if not job:
        return api_error("Unknown job.", 404)
    if job.assigned_device_connection_id != conn.connection_id:
        return api_error("Job is not assigned to this device.", 403)
    if job.paid_at or job.status == "paid":
        job.duplicate_completion_count += 1
        db.session.commit()
        return api_error("Duplicate completion rejected; this job instance has already paid.", 409)
    if completion_status == "aborted":
        job.status = "aborted"
        job.completed_at = utcnow()
        db.session.commit()
        return jsonify({"ok": True, "status": job.status, "payment_cents": 0})
    if completion_status == "failed":
        job.status = "failed"
        job.completed_at = utcnow()
        db.session.commit()
        return jsonify({"ok": True, "status": job.status, "payment_cents": 0})
    if job.status != "in_progress":
        job.duplicate_completion_count += 1
        db.session.commit()
        return api_error("Completed jobs must have one-time start authorization and be in_progress before payment can be considered.", 409)
    job.status = "completed_pending_author_ack"
    job.completed_at = utcnow()
    db.session.commit()
    return jsonify({"ok": True, "status": job.status, "payment_cents": 0, "requires_author_ack": True})


@app.post("/api/jobs/acknowledge")
def api_acknowledge_job():
    data = request.get_json(silent=True) or request.form
    author_conn = Connection.query.filter_by(connection_id=data.get("author_connection_id", "")).first()
    job = JobInstance.query.filter_by(job_id=data.get("job_id", "")).first()
    if not author_conn or author_conn.status != "active" or author_conn.node_type != "author_node":
        return api_error("Active author node connection required.", 403)
    if not job:
        return api_error("Unknown job.", 404)
    if job.batch.author_user_id != author_conn.user_id:
        return api_error("Author node does not own this job.", 403)
    if job.paid_at or job.status == "paid":
        return api_error("Job already paid; duplicate acknowledgement rejected.", 409)
    if job.status in ["aborted", "failed"]:
        job.status = job.status + "_acknowledged"
        db.session.commit()
        return jsonify({"ok": True, "status": job.status, "payment_cents": 0})
    if job.status != "completed_pending_author_ack":
        return api_error(f"Job is not ready for acknowledgement: {job.status}", 409)
    device_conn = Connection.query.filter_by(connection_id=job.assigned_device_connection_id).first()
    if not device_conn or not device_conn.user_id:
        return api_error("Assigned device owner not found.", 409)
    amount = job.payment_cents
    ensure_wallet(author_conn.user)
    ensure_wallet(device_conn.user)
    author_conn.user.wallet.simulated_balance_cents -= amount
    device_conn.user.wallet.simulated_balance_cents += amount
    device_conn.user.wallet.total_received_cents += amount
    job.status = "paid"
    job.paid_at = utcnow()
    entry = LedgerEntry(job_instance_id=job.id, from_user_id=author_conn.user_id, to_user_id=device_conn.user_id, amount_cents=amount, reason="Print instance completed and acknowledged")
    db.session.add(entry)
    db.session.commit()
    return jsonify({"ok": True, "status": job.status, "payment_cents": amount})



@app.get("/api/jobs/device-status")
def api_device_job_status():
    conn = Connection.query.filter_by(connection_id=request.args.get("device_connection_id", "")).first()
    if not conn or conn.node_type != "device_node":
        return api_error("Active device node connection required.", 403)
    job_id = request.args.get("job_id", "")
    job = JobInstance.query.filter_by(job_id=job_id).first() if job_id else None
    if job and job.assigned_device_connection_id != conn.connection_id:
        return api_error("Job is not assigned to this device.", 403)
    ensure_wallet(conn.user)
    last_entry = LedgerEntry.query.filter_by(to_user_id=conn.user_id).order_by(LedgerEntry.created_at.desc()).first()
    return jsonify({
        "ok": True,
        "job": serialize_job(job, include_payload=False) if job else None,
        "last_received_cents": last_entry.amount_cents if last_entry else 0,
        "total_received_cents": conn.user.wallet.total_received_cents if conn.user and conn.user.wallet else 0,
    })

@app.get("/api/jobs/author-completions")
def api_author_completions():
    author_conn = Connection.query.filter_by(connection_id=request.args.get("author_connection_id", "")).first()
    if not author_conn or author_conn.status != "active" or author_conn.node_type != "author_node":
        return api_error("Active author node connection required.", 403)
    jobs = JobInstance.query.join(JobBatch).filter(
        JobBatch.author_user_id == author_conn.user_id,
        JobInstance.status.in_(["completed_pending_author_ack", "aborted", "failed", "paid"])
    ).order_by(JobInstance.completed_at.desc().nullslast()).limit(50).all()
    return jsonify({"ok": True, "jobs": [serialize_job(j, include_payload=False) for j in jobs]})


@app.get("/healthz")
def healthz():
    return jsonify({"ok": True, "app": APP_NAME, "time": utcnow().isoformat()})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5055"))
    app.run(host="0.0.0.0", port=port, debug=os.environ.get("FLASK_DEBUG") == "1")
