"""API ber-token untuk APK Monitoring Stok dan sinkronisasi melalui Tailscale."""
import datetime as dt
import hashlib
import json
import re
import secrets
from functools import wraps

from flask import Blueprint, g, jsonify, request
from werkzeug.security import check_password_hash

from .db import audit, get_db, mobile_pengaturan, tx
from .mobile_sync import MobileSyncError, save_submission
from .stock import StockError, bulat


bp = Blueprint("mobile_api", __name__, url_prefix="/api/mobile/v1")


def _json_error(message, status=400, **extra):
    """Buat bentuk galat API yang konsisten untuk antrean APK."""
    return jsonify({"ok": False, "error": message, **extra}), status


def _token_hash(token):
    """Sidik token Bearer sebelum dibandingkan dengan database."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def mobile_auth_required(view):
    """Batasi endpoint pada perangkat aktif dengan token Bearer yang sah."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        header = request.headers.get("Authorization", "")
        token = header[7:].strip() if header.startswith("Bearer ") else ""
        if not token:
            return _json_error("Perangkat belum login", 401)
        row = get_db().execute(
            """SELECT d.*, u.username, u.nama AS user_name, u.role, u.kebun_kode, u.aktif AS user_active
               FROM mobile_devices d JOIN users u ON u.id=d.user_id
               WHERE d.token_hash=?""",
            (_token_hash(token),),
        ).fetchone()
        if not row or not row["active"] or not row["user_active"]:
            return _json_error("Akses perangkat sudah tidak aktif; login kembali atau hubungi admin", 401)
        g.mobile_device = row
        g.user = {
            "id": row["user_id"],
            "username": row["username"],
            "nama": row["user_name"],
            "role": row["role"],
            "kebun_kode": row["kebun_kode"],
        }
        get_db().execute(
            "UPDATE mobile_devices SET last_seen_at=datetime('now','localtime'), updated_at=datetime('now','localtime') WHERE id=?",
            (row["id"],),
        )
        return view(*args, **kwargs)
    return wrapped


@bp.after_request
def mobile_cors(response):
    """Izinkan hanya origin lokal Capacitor memakai API Bearer tanpa cookie."""
    origin = request.headers.get("Origin", "")
    if origin in {"http://localhost", "https://localhost", "capacitor://localhost"}:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Vary"] = "Origin"
    response.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type, X-App-Version"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return response


@bp.route("/<path:_path>", methods=["OPTIONS"])
def options(_path):
    """Jawab preflight WebView untuk server Tailscale lintas origin."""
    return "", 204


@bp.get("/status")
def status():
    """Health check ringan sebelum login atau sinkron."""
    return jsonify({
        "ok": True,
        "service": "monitoring-stok-mobile",
        "server_time": dt.datetime.now().isoformat(timespec="seconds"),
    })


@bp.post("/login")
def login():
    """Daftarkan perangkat setelah memeriksa akun aplikasi yang aktif."""
    data = request.get_json(silent=True) or {}
    username = str(data.get("username") or "").strip().lower()
    password = str(data.get("password") or "")
    device_uuid = str(data.get("device_uuid") or "").strip().lower()
    device_name = re.sub(r"\s+", " ", str(data.get("device_name") or "HP Gudang").strip())[:80]
    app_version = str(data.get("app_version") or "")[:24]
    if not re.fullmatch(r"[a-z0-9-]{8,80}", device_uuid):
        return _json_error("Identitas perangkat tidak valid")
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE username=? AND aktif=1", (username,)).fetchone()
    if not user or user["role"] not in {"admin", "petugas"} or not check_password_hash(user["password"], password):
        return _json_error("Username atau password salah", 401)
    token = secrets.token_urlsafe(40)
    existing = db.execute("SELECT id, active FROM mobile_devices WHERE device_uuid=?", (device_uuid,)).fetchone()
    if existing and not existing["active"]:
        return _json_error("Perangkat ini dinonaktifkan Admin dan tidak dapat login kembali", 403)
    with tx(db):
        if existing:
            db.execute(
                """UPDATE mobile_devices SET user_id=?, name=?, token_hash=?, app_version=?,
                          last_seen_at=datetime('now','localtime'), updated_at=datetime('now','localtime')
                   WHERE id=?""",
                (user["id"], device_name, _token_hash(token), app_version, existing["id"]),
            )
            device_id = existing["id"]
        else:
            device_id = db.execute(
                """INSERT INTO mobile_devices
                   (device_uuid,user_id,name,token_hash,app_version,last_seen_at)
                   VALUES (?,?,?,?,?,datetime('now','localtime'))""",
                (device_uuid, user["id"], device_name, _token_hash(token), app_version),
            ).lastrowid
        db.execute(
            "INSERT INTO mobile_sync_log (device_id,user_id,action,detail) VALUES (?,?,?,?)",
            (device_id, user["id"], "login", f"{device_name}; versi {app_version or '-'}"),
        )
        audit(db, user["id"], "login_apk", f"device#{device_id} {device_name}")
    return jsonify({
        "ok": True,
        "token": token,
        "user": {"id": user["id"], "name": user["nama"], "role": user["role"], "plant": user["kebun_kode"]},
        "server_time": dt.datetime.now().isoformat(timespec="seconds"),
    })


@bp.get("/bootstrap")
@mobile_auth_required
def bootstrap():
    """Kirim master, saldo, tujuan, dan konfigurasi pembaruan untuk kerja offline."""
    db = get_db()
    user = g.user
    args = []
    plant_filter = ""
    if user["kebun_kode"]:
        plant_filter = " AND v.plant=?"
        args.append(user["kebun_kode"])
    materials = [dict(row) for row in db.execute(
        f"""SELECT v.id, v.material, v.deskripsi, v.satuan, v.plant, v.stok,
                   v.kategori_id, COALESCE(k.nama,'') AS kategori, v.wajib_kembali,
                   v.expired, v.expired_at, v.input_locked, COALESCE(v.input_lock_reason,'') AS input_lock_reason
            FROM v_stok v LEFT JOIN kategori k ON k.id=v.kategori_id
            WHERE v.ada_di_sap=1 {plant_filter}
            ORDER BY v.plant, v.deskripsi""",
        args,
    ).fetchall()]
    for material in materials:
        material["stok"] = bulat(material["stok"])
    plants = sorted({material["plant"] for material in materials})
    targets = []
    if plants:
        marks = ",".join("?" * len(plants))
        targets = [dict(row) for row in db.execute(
            f"""SELECT plant,target_type,code,sap_code,name,division,phase
                 FROM supplementary_targets
                 WHERE active=1 AND plant IN ({marks}) ORDER BY plant,target_type,code""",
            plants,
        ).fetchall()]
    settings = mobile_pengaturan(db)
    db.execute(
        """UPDATE mobile_devices SET last_sync_at=datetime('now','localtime'), app_version=?,
                  updated_at=datetime('now','localtime') WHERE id=?""",
        (request.headers.get("X-App-Version", "")[:24], g.mobile_device["id"]),
    )
    db.execute(
        "INSERT INTO mobile_sync_log (device_id,user_id,action,detail) VALUES (?,?,?,?)",
        (g.mobile_device["id"], user["id"], "morning_sync", f"{len(materials)} material; {len(targets)} target"),
    )
    return jsonify({
        "ok": True,
        "schema_version": 1,
        "synced_at": dt.datetime.now().isoformat(timespec="seconds"),
        "valid_for_date": dt.date.today().isoformat(),
        "user": {"id": user["id"], "name": user["nama"], "role": user["role"], "plant": user["kebun_kode"]},
        "materials": materials,
        "targets": targets,
        "fixed_targets": [
            {"type": "workshop", "code": "CM3AZGO996", "name": "GOH-WORKSHOP"},
            {"type": "office", "code": "CM3AZGO992", "name": "GOH-OFFICE LABOUR"},
            {"type": "staff", "code": "CM3AZGO991", "name": "GOH-STAFF"},
        ],
        "update": {
            "github_repo": settings.get("mobile_github_repo", ""),
            "minimum_version": settings.get("mobile_min_version", "0.1.0"),
            "asset_prefix": "MonitoringStok-",
        },
    })


@bp.post("/transactions")
@mobile_auth_required
def transactions():
    """Terima satu BON multipart beserta foto dengan idempotensi client_uuid."""
    try:
        payload = json.loads(request.form.get("payload") or "{}")
        result = save_submission(
            get_db(), g.user, g.mobile_device, payload,
            request.files.get("photo"), request.files.get("photo_2"), request.files.get("return_photo"),
        )
        return jsonify(result)
    except json.JSONDecodeError:
        return _json_error("Payload transaksi tidak dapat dibaca")
    except MobileSyncError as exc:
        return _json_error(str(exc), exc.status, **exc.extra)
    except StockError as exc:
        return _json_error(str(exc), 400)
