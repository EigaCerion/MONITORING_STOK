"""API ber-token untuk APK Monitoring Stok dan sinkronisasi melalui Tailscale."""
import datetime as dt
import hashlib
import json
import re
import secrets
import uuid
from functools import wraps

from flask import Blueprint, current_app, g, jsonify, request, send_from_directory
from werkzeug.security import check_password_hash

from .db import audit, delegasi_opname_aktif, get_db, mobile_pengaturan, opname_aktif, tx
from .mobile_sync import (
    MobileSyncError, payload_hash, save_photo_submission, save_return_submission, save_submission,
)
from .stock import (
    ALASAN_PENYESUAIAN, EPS, StockError, batalkan_transaksi, bulat, nomor_bon,
    sesuaikan_stok, stok_material, ubah_qty_transaksi,
)
from .supplementary import target_plants


bp = Blueprint("mobile_api", __name__, url_prefix="/api/mobile/v1")


def _version_key(value):
    """Ubah versi semver sederhana menjadi tuple agar payload tetap kompatibel dengan APK lama."""
    numbers = re.findall(r"\d+", str(value or ""))[:3]
    return tuple(int(number) for number in numbers + ["0"] * (3 - len(numbers)))


def _json_error(message, status=400, **extra):
    """Buat bentuk galat API yang konsisten untuk antrean APK."""
    return jsonify({"ok": False, "error": message, **extra}), status


def _token_hash(token):
    """Sidik token Bearer sebelum dibandingkan dengan database."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _mobile_uuid(value, label="Identitas antrean opname"):
    """Validasi UUID antrean tanpa mempercayai format dari perangkat."""
    try:
        return str(uuid.UUID(str(value or "")))
    except (ValueError, AttributeError, TypeError) as exc:
        raise MobileSyncError(f"{label} tidak valid") from exc


def _opname_session_payload(db, session, user):
    """Bentuk snapshot sesi opname untuk disimpan dan dibuka offline di APK."""
    session = dict(session)
    items = [dict(row) for row in db.execute(
        """SELECT i.id,i.material_id,i.stok_buku,i.stok_sap,i.fisik,i.keterangan,
                  i.counted_by,i.counted_at,COALESCE(u.nama,'Tidak diketahui') AS counted_by_name
           FROM opname_item i LEFT JOIN users u ON u.id=i.counted_by
           WHERE i.sesi_id=? ORDER BY i.id""",
        (session["id"],),
    ).fetchall()]
    members = [dict(row) for row in db.execute(
        """SELECT i.counted_by,COALESCE(u.nama,'Tidak diketahui') AS name,COUNT(*) AS count,
                  MAX(i.counted_at) AS last_saved
           FROM opname_item i LEFT JOIN users u ON u.id=i.counted_by
           WHERE i.sesi_id=? GROUP BY i.counted_by,u.nama ORDER BY u.nama""",
        (session["id"],),
    ).fetchall()]
    can_close = user["role"] == "admin" or bool(
        user["role"] == "petugas" and session["created_by"] == user["id"]
        and delegasi_opname_aktif(db, user["id"], session["plant"])
    )
    total = db.execute("SELECT COUNT(*) FROM v_stok WHERE plant=?", (session["plant"],)).fetchone()[0]
    return {
        **session, "items": items, "members": members, "counted": len(items),
        "total_materials": total, "can_close": can_close,
    }


def _opname_session_for_user(db, session_id, user, open_only=True):
    """Ambil sesi yang boleh diakses akun APK dan opsional masih terbuka."""
    session = db.execute(
        """SELECT s.*,COALESCE(k.nama,s.plant) AS garden_name,
                  COALESCE(u.nama,'Tidak diketahui') AS created_by_name
           FROM opname_sesi s LEFT JOIN kebun k ON k.kode=s.plant
           LEFT JOIN users u ON u.id=s.created_by WHERE s.id=?""",
        (session_id,),
    ).fetchone()
    if not session:
        raise MobileSyncError("Sesi opname tidak ditemukan", 404)
    if user.get("kebun_kode") and session["plant"] != user["kebun_kode"]:
        raise MobileSyncError("Sesi opname berada di gudang yang tidak boleh diakses", 403)
    if user["role"] == "petugas" and not user.get("kebun_kode"):
        raise MobileSyncError("Akun petugas belum memiliki gudang", 403)
    if open_only and session["status"] != "terbuka":
        raise MobileSyncError("Sesi opname sudah ditutup; hitungan tidak dapat diubah", 409)
    return session


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
    local_preview = re.fullmatch(r"http://(?:127\.0\.0\.1|localhost):5173", origin)
    if origin in {"http://localhost", "https://localhost", "capacitor://localhost"} or local_preview:
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
    app_version = request.headers.get("X-App-Version", "")[:24]
    if _version_key(app_version) >= (0, 1, 5):
        recent_type_filter = "IN ('keluar','masuk','transfer_masuk','transfer_keluar')"
    elif _version_key(app_version) >= (0, 1, 4):
        recent_type_filter = "IN ('keluar','masuk')"
    else:
        recent_type_filter = "='keluar'"
    args = []
    plant_filter = ""
    if user["kebun_kode"]:
        plant_filter = " AND v.plant=?"
        args.append(user["kebun_kode"])
    materials = [dict(row) for row in db.execute(
        f"""SELECT v.id, v.material, v.deskripsi, v.satuan, v.plant, v.stok,
                   v.kategori_id, COALESCE(k.nama,'') AS kategori, v.wajib_kembali,
                   v.stok_minimum, COALESCE(v.lokasi_blok,'') AS lokasi_blok,
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
        block_plants = sorted({candidate for plant in plants for candidate in target_plants(plant, "blok")})
        block_marks = ",".join("?" * len(block_plants))
        unit_marks = ",".join("?" * len(plants))
        master_targets = [dict(row) for row in db.execute(
            f"""SELECT plant,target_type,code,sap_code,name,division,phase
                  FROM supplementary_targets
                  WHERE active=1 AND (
                    (target_type='blok' AND plant IN ({block_marks})) OR
                    (target_type='unit' AND plant IN ({unit_marks}))
                  ) ORDER BY plant,target_type,code""",
            (*block_plants, *plants),
        ).fetchall()]
        # APK lama menyaring target berdasarkan plant material. Duplikasi logis ini
        # membuat blok plasma dapat langsung dipakai tanpa menunggu pembaruan APK,
        # sambil tetap menyimpan plant master asli untuk audit/debugging.
        expanded = {}
        for visible_plant in plants:
            for target in master_targets:
                if target["plant"] not in target_plants(visible_plant, target["target_type"]):
                    continue
                item = dict(target)
                item["master_plant"] = target["plant"]
                item["plant"] = visible_plant
                key = (visible_plant, item["target_type"], item["code"])
                if key not in expanded or target["plant"] == visible_plant:
                    expanded[key] = item
        targets = sorted(expanded.values(), key=lambda row: (row["plant"], row["target_type"], row["code"]))
    pending_args = []
    pending_plant = ""
    if user["kebun_kode"]:
        pending_plant = " AND m.plant=?"
        pending_args.append(user["kebun_kode"])
    pending_returns = [dict(row) for row in db.execute(
        f"""SELECT t.id AS transaction_id,t.bon,t.tanggal,t.qty AS qty_out,t.penerima,
                   m.id AS material_id,m.material,m.deskripsi,m.satuan,m.plant
            FROM transactions t JOIN materials m ON m.id=t.material_id
            WHERE t.jenis='keluar' AND t.batal=0 AND m.wajib_kembali=1
              AND NOT EXISTS (SELECT 1 FROM pengembalian p
                              WHERE p.transaksi_id=t.id AND p.batal=0)
              {pending_plant}
            ORDER BY t.tanggal,t.id""",
        pending_args,
    ).fetchall()]
    recent_transactions = [dict(row) for row in db.execute(
        f"""SELECT t.id,t.jenis,t.material_id,t.bon,t.tanggal,t.qty,t.penerima,t.keterangan,t.nomor_dokumen,
                   t.kebun_asal,t.kebun_tujuan,t.tujuan_tipe,
                   t.tujuan_kode,t.aktivitas_kode,t.sap_posted,t.sap_doc,t.sap_posting_date,
                   t.sap_posted_at,t.sumber,t.batal,t.batal_alasan,t.created_at,t.updated_at,
                   COALESCE(u.nama,'Sistem') AS created_by_name,
                   CASE WHEN t.sap_posted=1 OR t.sumber IN ('sap_mb51','sinkron_sap') THEN 1 ELSE 0 END AS mobile_read_only,
                   m.material,m.deskripsi,m.satuan,m.plant,
                   m.wajib_kembali,CASE WHEN t.foto IS NULL THEN 0 ELSE 1 END AS has_photo,
                   CASE WHEN t.foto_2 IS NULL THEN 0 ELSE 1 END AS has_photo_2,
                   CASE WHEN pr.id IS NULL THEN 0 ELSE 1 END AS has_return,
                   pr.id AS return_id,pr.qty AS return_qty,pr.kondisi AS return_condition,
                   pr.tanggal AS return_date,pr.dikembalikan_oleh AS return_by
            FROM transactions t JOIN materials m ON m.id=t.material_id
            LEFT JOIN users u ON u.id=t.created_by
            LEFT JOIN pengembalian pr ON pr.id=(
                SELECT MAX(p.id) FROM pengembalian p
                WHERE p.transaksi_id=t.id AND p.batal=0
            )
            WHERE t.jenis {recent_type_filter} {pending_plant}
            ORDER BY t.tanggal DESC,t.id DESC LIMIT 300""",
        pending_args,
    ).fetchall()]
    settings = mobile_pengaturan(db)
    gardens = [dict(row) for row in db.execute(
        "SELECT kode,nama FROM kebun WHERE aktif=1 ORDER BY nama,kode"
    ).fetchall()]
    session_where, session_args = "s.status='terbuka'", []
    if user["kebun_kode"]:
        session_where += " AND s.plant=?"
        session_args.append(user["kebun_kode"])
    open_sessions = db.execute(
        f"""SELECT s.*,COALESCE(k.nama,s.plant) AS garden_name,
                   COALESCE(u.nama,'Tidak diketahui') AS created_by_name
            FROM opname_sesi s LEFT JOIN kebun k ON k.kode=s.plant
            LEFT JOIN users u ON u.id=s.created_by
            WHERE {session_where} ORDER BY s.id""",
        session_args,
    ).fetchall()
    delegation = delegasi_opname_aktif(db, user["id"], user["kebun_kode"]) if user["role"] == "petugas" else None
    opname = {
        "sessions": [_opname_session_payload(db, session, user) for session in open_sessions],
        "can_start": user["role"] == "admin" or bool(delegation),
        "delegated_plant": delegation["plant"] if delegation else None,
    }
    db.execute(
        """UPDATE mobile_devices SET last_sync_at=datetime('now','localtime'), app_version=?,
                  updated_at=datetime('now','localtime') WHERE id=?""",
        (app_version, g.mobile_device["id"]),
    )
    db.execute(
        "INSERT INTO mobile_sync_log (device_id,user_id,action,detail) VALUES (?,?,?,?)",
        (g.mobile_device["id"], user["id"], "morning_sync", f"{len(materials)} material; {len(targets)} target"),
    )
    return jsonify({
        "ok": True,
        "schema_version": 5,
        "features": ["keluar", "masuk", "transfer", "stock", "opname"],
        "synced_at": dt.datetime.now().isoformat(timespec="seconds"),
        "valid_for_date": dt.date.today().isoformat(),
        "user": {"id": user["id"], "name": user["nama"], "role": user["role"], "plant": user["kebun_kode"]},
        "materials": materials,
        "targets": targets,
        "gardens": gardens,
        "pending_returns": pending_returns,
        "recent_transactions": recent_transactions,
        "opname": opname,
        "fixed_targets": [
            {"type": "workshop", "code": "CM3AZGO996", "name": "GOH-WORKSHOP"},
            {"type": "office", "code": "CM3AZGO992", "name": "GOH-OFFICE LABOUR"},
            {"type": "staff", "code": "CM3AZGO991", "name": "GOH-STAFF"},
        ],
        "update": {
            "github_repo": settings.get("mobile_github_repo", ""),
            "minimum_version": settings.get("mobile_min_version", "0.1.0"),
            "asset_prefix": "flowStok-",
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


@bp.post("/opname/start")
@mobile_auth_required
def start_opname():
    """Buka sesi opname dari APK bila admin atau delegasi petugas mengizinkan."""
    body = request.get_json(silent=True) or {}
    db, user = get_db(), g.user
    plant = (str(body.get("plant") or "").strip().upper() if user["role"] == "admin"
             else str(user.get("kebun_kode") or "").strip().upper())
    if user["role"] == "petugas" and not delegasi_opname_aktif(db, user["id"], plant):
        return _json_error("Delegasi membuka sesi opname tidak tersedia atau sudah kedaluwarsa", 403)
    garden = db.execute("SELECT kode,nama FROM kebun WHERE kode=? AND aktif=1", (plant,)).fetchone()
    if not garden:
        return _json_error("Pilih gudang/kebun aktif yang akan diopname")
    try:
        date = dt.date.fromisoformat(str(body.get("date") or dt.date.today().isoformat()))
    except ValueError:
        return _json_error("Tanggal opname tidak valid")
    if date > dt.date.today():
        return _json_error("Tanggal opname tidak boleh di masa depan")
    with tx(db):
        if user["role"] == "petugas" and not delegasi_opname_aktif(db, user["id"], plant):
            return _json_error("Delegasi berakhir sebelum sesi dibuka", 403)
        active = opname_aktif(db, plant)
        if active:
            session_id = active["id"]
            created = False
        else:
            session_id = db.execute(
                """INSERT INTO opname_sesi
                   (tanggal,jenis_barang,lokasi,plant,status,keterangan,created_by)
                   VALUES (?,?,?,?, 'terbuka', ?,?)""",
                (date.isoformat(), str(body.get("item_type") or "UMUM").strip().upper() or "UMUM",
                 str(body.get("location") or "").strip().upper() or garden["nama"], plant,
                 str(body.get("note") or "").strip(), user["id"]),
            ).lastrowid
            created = True
            audit(db, user["id"], "mulai_opname_apk", f"sesi #{session_id} gudang {plant}")
    session = _opname_session_for_user(db, session_id, user)
    return jsonify({"ok": True, "created": created, "session": _opname_session_payload(db, session, user)})


@bp.post("/opname/<int:session_id>/counts")
@mobile_auth_required
def save_opname_counts(session_id):
    """Simpan hitungan fisik offline secara idempoten dan cegah tabrakan antarpetugas."""
    payload = request.get_json(silent=True) or {}
    try:
        client_uuid = _mobile_uuid(payload.get("client_uuid"))
        expected_hash = payload_hash(payload)
        raw_items = payload.get("items")
        if not isinstance(raw_items, list) or not raw_items or len(raw_items) > 30:
            raise MobileSyncError("Hitungan opname harus berisi 1 sampai 30 material")
        parsed, seen = [], set()
        for item in raw_items:
            material_id = int(item.get("material_id"))
            quantity = bulat(float(item.get("quantity")))
            if material_id in seen:
                raise MobileSyncError("Satu material opname tidak boleh dikirim dua kali")
            if quantity < 0:
                raise MobileSyncError("Qty fisik tidak boleh negatif")
            seen.add(material_id)
            parsed.append((material_id, quantity, str(item.get("note") or "").strip()))
    except (TypeError, ValueError) as exc:
        return _json_error("Material atau qty fisik tidak valid")
    except MobileSyncError as exc:
        return _json_error(str(exc), exc.status, **exc.extra)

    db, user = get_db(), g.user
    try:
        existing = db.execute(
            "SELECT * FROM mobile_opname_submissions WHERE client_uuid=?", (client_uuid,),
        ).fetchone()
        if existing:
            if existing["payload_hash"] != expected_hash or existing["session_id"] != session_id:
                raise MobileSyncError("Identitas hitungan sudah dipakai untuk isi berbeda", 409)
            session = _opname_session_for_user(db, session_id, user, open_only=False)
            return jsonify({
                "ok": True, "already_received": True,
                "material_ids": json.loads(existing["material_ids"]),
                "session": _opname_session_payload(db, session, user),
            })
        session = _opname_session_for_user(db, session_id, user)
        with tx(db):
            if db.execute("SELECT status FROM opname_sesi WHERE id=?", (session_id,)).fetchone()["status"] != "terbuka":
                raise MobileSyncError("Sesi opname sudah ditutup", 409)
            validated = []
            conflicts = []
            for material_id, quantity, note in parsed:
                material = db.execute(
                    "SELECT * FROM v_stok WHERE id=? AND plant=?", (material_id, session["plant"]),
                ).fetchone()
                if not material:
                    raise MobileSyncError(f"Material #{material_id} bukan bagian gudang sesi", 409)
                old = db.execute(
                    "SELECT * FROM opname_item WHERE sesi_id=? AND material_id=?", (session_id, material_id),
                ).fetchone()
                if old and old["counted_by"] not in (None, user["id"]):
                    counter = db.execute("SELECT nama FROM users WHERE id=?", (old["counted_by"],)).fetchone()
                    conflicts.append(f"{material['deskripsi']} sudah dihitung oleh {counter['nama'] if counter else 'petugas lain'}")
                validated.append((material, old, quantity, note))
            if conflicts:
                raise MobileSyncError(" · ".join(conflicts), 409, conflict=True)
            for material, old, quantity, note in validated:
                if old:
                    db.execute(
                        """UPDATE opname_item SET fisik=?,keterangan=?,counted_by=?,
                                  counted_at=datetime('now','localtime') WHERE id=?""",
                        (quantity, note or None, user["id"], old["id"]),
                    )
                else:
                    db.execute(
                        """INSERT INTO opname_item
                           (sesi_id,material_id,stok_buku,stok_sap,fisik,keterangan,counted_by,counted_at)
                           VALUES (?,?,?,?,?,?,?,datetime('now','localtime'))""",
                        (session_id, material["id"], bulat(material["stok"]), material["sap_perkiraan"],
                         quantity, note or None, user["id"]),
                    )
            material_ids = [material_id for material_id, _quantity, _note in parsed]
            db.execute(
                """INSERT INTO mobile_opname_submissions
                   (client_uuid,device_id,user_id,session_id,payload_hash,material_ids)
                   VALUES (?,?,?,?,?,?)""",
                (client_uuid, g.mobile_device["id"], user["id"], session_id, expected_hash,
                 json.dumps(material_ids, separators=(",", ":"))),
            )
            db.execute(
                "INSERT INTO mobile_sync_log (device_id,user_id,action,detail) VALUES (?,?,?,?)",
                (g.mobile_device["id"], user["id"], "upload_opname", f"sesi #{session_id}; {len(parsed)} material"),
            )
            audit(db, user["id"], "hitung_opname_apk", f"sesi #{session_id}: {len(parsed)} material")
        refreshed = _opname_session_for_user(db, session_id, user)
        return jsonify({
            "ok": True, "already_received": False,
            "material_ids": [material_id for material_id, _quantity, _note in parsed],
            "session": _opname_session_payload(db, refreshed, user),
        })
    except MobileSyncError as exc:
        return _json_error(str(exc), exc.status, **exc.extra)


@bp.post("/opname/<int:session_id>/close")
@mobile_auth_required
def close_opname(session_id):
    """Tutup sesi online setelah penanggung jawab mengonfirmasi semua anggota selesai."""
    body = request.get_json(silent=True) or {}
    db, user = get_db(), g.user
    try:
        session = _opname_session_for_user(db, session_id, user)
        can_close = user["role"] == "admin" or bool(
            user["role"] == "petugas" and session["created_by"] == user["id"]
            and delegasi_opname_aktif(db, user["id"], session["plant"])
        )
        if not can_close:
            raise MobileSyncError("Akun ini tidak memiliki hak menutup sesi opname", 403)
        if body.get("confirm_all") is not True:
            raise MobileSyncError("Pastikan semua anggota sudah memposting hitungannya lalu beri konfirmasi")
        with tx(db):
            current = db.execute("SELECT status FROM opname_sesi WHERE id=?", (session_id,)).fetchone()
            if not current or current["status"] != "terbuka":
                raise MobileSyncError("Sesi sudah ditutup oleh pengguna lain", 409)
            items = db.execute(
                """SELECT i.*,m.deskripsi FROM opname_item i JOIN materials m ON m.id=i.material_id
                   WHERE i.sesi_id=? ORDER BY i.id""", (session_id,),
            ).fetchall()
            if not items:
                raise MobileSyncError("Sesi belum memiliki hitungan barang")
            changed = []
            for item in items:
                current_stock = db.execute("SELECT stok FROM v_stok WHERE id=?", (item["material_id"],)).fetchone()
                if not current_stock or abs(current_stock["stok"] - item["stok_buku"]) >= EPS:
                    changed.append(item["deskripsi"])
            if changed:
                raise MobileSyncError(
                    "Stok aplikasi berubah setelah dihitung: " + ", ".join(changed[:5]), 409,
                )
            stocks, adjusted = {}, 0
            for item in items:
                transaction_id = None
                if abs(item["fisik"] - item["stok_buku"]) >= EPS:
                    transaction_id, _, _ = sesuaikan_stok(
                        db, item["material_id"], item["fisik"], ALASAN_PENYESUAIAN[0],
                        " · ".join(value for value in (session["keterangan"], item["keterangan"]) if value)
                        or f"Opname bersama #{session_id}", session["tanggal"], user["id"],
                    )
                    adjusted += 1
                db.execute("UPDATE opname_item SET trx_id=? WHERE id=?", (transaction_id, item["id"]))
                stocks[str(item["material_id"])] = bulat(stok_material(db, item["material_id"]))
            db.execute(
                """UPDATE opname_sesi SET status='selesai',closed_by=?,
                          closed_at=datetime('now','localtime') WHERE id=?""", (user["id"], session_id),
            )
            audit(db, user["id"], "selesai_opname_apk",
                  f"sesi #{session_id}: {len(items)} dihitung, {adjusted} penyesuaian")
        return jsonify({
            "ok": True, "session_id": session_id, "counted": len(items),
            "adjusted": adjusted, "stocks": stocks,
            "message": f"Sesi opname #{session_id} selesai",
        })
    except (MobileSyncError, StockError) as exc:
        status = exc.status if isinstance(exc, MobileSyncError) else 400
        extra = exc.extra if isinstance(exc, MobileSyncError) else {}
        return _json_error(str(exc), status, **extra)


@bp.post("/returns/<int:transaction_id>")
@mobile_auth_required
def returns(transaction_id):
    """Terima pengembalian menyusul dari antrean APK secara idempoten."""
    try:
        payload = json.loads(request.form.get("payload") or "{}")
        result = save_return_submission(
            get_db(), g.user, g.mobile_device, transaction_id, payload, request.files.get("photo"),
        )
        return jsonify(result)
    except json.JSONDecodeError:
        return _json_error("Payload pengembalian tidak dapat dibaca")
    except MobileSyncError as exc:
        return _json_error(str(exc), exc.status, **exc.extra)
    except StockError as exc:
        return _json_error(str(exc), 400)


@bp.post("/returns/<int:return_id>/cancel")
@mobile_auth_required
def cancel_return(return_id):
    """Batalkan bukti pengembalian dari APK dan buka kembali status tertunda."""
    if g.user["role"] != "admin":
        return _json_error("Hanya Admin yang dapat membatalkan catatan pengembalian", 403)
    body = request.get_json(silent=True) or {}
    reason = str(body.get("reason") or "").strip()
    if not reason:
        return _json_error("Alasan pembatalan wajib diisi")
    db = get_db()
    row = db.execute(
        """SELECT p.*,t.batal AS transaction_cancelled,m.plant,m.wajib_kembali,m.deskripsi
           FROM pengembalian p JOIN transactions t ON t.id=p.transaksi_id
           JOIN materials m ON m.id=p.material_id WHERE p.id=?""",
        (return_id,),
    ).fetchone()
    if not row or row["batal"]:
        return _json_error("Catatan pengembalian tidak ditemukan atau sudah dibatalkan", 404)
    if g.user.get("kebun_kode") and row["plant"] != g.user["kebun_kode"]:
        return _json_error("Catatan berada di kebun yang tidak boleh diakses", 403)
    with tx(db):
        db.execute(
            """UPDATE pengembalian SET batal=1,batal_alasan=?,batal_by=?,
                      batal_at=datetime('now','localtime') WHERE id=? AND batal=0""",
            (reason, g.user["id"], return_id),
        )
        audit(db, g.user["id"], "batal_pengembalian_apk",
              f"pengembalian#{return_id} trx#{row['transaksi_id']}; {reason}")
    return jsonify({
        "ok": True, "return_id": return_id, "transaction_id": row["transaksi_id"],
        "pending_again": bool(row["wajib_kembali"] and not row["transaction_cancelled"]),
        "message": f"Catatan pengembalian {row['deskripsi']} dibatalkan",
    })


@bp.post("/transactions/<int:transaction_id>/photo")
@mobile_auth_required
def transaction_photo(transaction_id):
    """Terima foto transaksi tambahan dari antrean APK secara idempoten."""
    try:
        payload = json.loads(request.form.get("payload") or "{}")
        result = save_photo_submission(
            get_db(), g.user, g.mobile_device, transaction_id, payload, request.files.get("photo"),
        )
        return jsonify(result)
    except json.JSONDecodeError:
        return _json_error("Payload lampiran foto tidak dapat dibaca")
    except MobileSyncError as exc:
        return _json_error(str(exc), exc.status, **exc.extra)
    except StockError as exc:
        return _json_error(str(exc), 400)


@bp.get("/transactions/<int:transaction_id>/photo/<int:slot>")
@mobile_auth_required
def get_transaction_photo(transaction_id, slot):
    """Kirim foto transaksi kepada perangkat yang berhak melihat materialnya."""
    if slot not in {1, 2}:
        return _json_error("Slot foto tidak valid", 404)
    column = "foto_2" if slot == 2 else "foto"
    row = get_db().execute(
        f"""SELECT t.{column} AS filename,m.plant FROM transactions t
            JOIN materials m ON m.id=t.material_id WHERE t.id=?""", (transaction_id,),
    ).fetchone()
    if not row or not row["filename"]:
        return _json_error(f"Foto {slot} tidak tersedia", 404)
    if g.user.get("kebun_kode") and row["plant"] != g.user["kebun_kode"]:
        return _json_error("Foto berada di kebun yang tidak boleh diakses", 403)
    return send_from_directory(current_app.config["UPLOAD_FOLDER"], row["filename"])


@bp.post("/transactions/<int:transaction_id>/cancel")
@mobile_auth_required
def cancel_transaction(transaction_id):
    """Batalkan satu transaksi dari APK; hanya Admin dan aman untuk retry."""
    if g.user["role"] != "admin":
        return _json_error("Hanya Admin yang dapat membatalkan transaksi", 403)
    reason = str((request.get_json(silent=True) or {}).get("reason") or "").strip()
    if not reason:
        return _json_error("Alasan pembatalan wajib diisi")
    db = get_db()
    row = db.execute(
        """SELECT t.*,m.plant,m.deskripsi FROM transactions t
           JOIN materials m ON m.id=t.material_id WHERE t.id=?""", (transaction_id,),
    ).fetchone()
    if not row:
        return _json_error("Transaksi tidak ditemukan", 404)
    if g.user.get("kebun_kode") and row["plant"] != g.user["kebun_kode"]:
        return _json_error("Transaksi berada di kebun yang tidak boleh diakses", 403)
    if row["sap_posted"] or row["sumber"] in {"sap_mb51", "sinkron_sap"}:
        return _json_error(
            "Transaksi yang sudah SAP hanya dapat dibatalkan melalui pemeriksaan Admin di web", 409
        )
    if row["batal"]:
        return jsonify({
            "ok": True, "already_cancelled": True, "transaction_id": transaction_id,
            "material_id": row["material_id"], "stock": bulat(stok_material(db, row["material_id"])),
            "sap_warning": bool(row["sap_posted"] and row["sumber"] != "sinkron_sap"),
        })
    try:
        with tx(db):
            before, after = batalkan_transaksi(db, transaction_id, reason, g.user["id"])
        return jsonify({
            "ok": True, "already_cancelled": False, "transaction_id": transaction_id,
            "material_id": row["material_id"], "before": bulat(before), "stock": bulat(after),
            "sap_warning": bool(row["sap_posted"] and row["sumber"] != "sinkron_sap"),
            "message": f"{row['deskripsi']} dibatalkan; stok dipulihkan",
        })
    except StockError as exc:
        return _json_error(str(exc), 400)


@bp.post("/transactions/<int:transaction_id>/edit")
@mobile_auth_required
def edit_transaction(transaction_id):
    """Perbaiki penerima/keterangan/nomor dokumen dari APK; hanya Admin."""
    if g.user["role"] != "admin":
        return _json_error("Hanya Admin yang dapat mengubah detail transaksi", 403)
    body = request.get_json(silent=True) or {}
    recipient = str(body.get("recipient") or "").strip()
    note = str(body.get("note") or "").strip()
    document = str(body.get("document") or "").strip()
    db = get_db()
    row = db.execute(
        """SELECT t.*,m.plant FROM transactions t JOIN materials m ON m.id=t.material_id
           WHERE t.id=?""", (transaction_id,),
    ).fetchone()
    if not row or row["batal"]:
        return _json_error("Transaksi tidak ditemukan atau sudah dibatalkan", 404)
    if row["jenis"] in {"keluar", "transfer_keluar"} and not recipient:
        return _json_error("Nama penerima wajib diisi")
    if g.user.get("kebun_kode") and row["plant"] != g.user["kebun_kode"]:
        return _json_error("Transaksi berada di kebun yang tidak boleh diakses", 403)
    if row["sap_posted"] or row["sumber"] in {"sap_mb51", "sinkron_sap"}:
        return _json_error(
            "Detail transaksi yang sudah SAP hanya dapat dikoreksi melalui Admin web", 409
        )
    all_bon = bool(body.get("all_bon") and row["bon"])
    with tx(db):
        targets = db.execute(
            "SELECT id,penerima,keterangan,nomor_dokumen,sap_posted,sumber FROM transactions WHERE bon=? AND batal=0"
            if all_bon else "SELECT id,penerima,keterangan,nomor_dokumen,sap_posted,sumber FROM transactions WHERE id=? AND batal=0",
            (row["bon"] if all_bon else transaction_id,),
        ).fetchall()
        if any(target["sap_posted"] or target["sumber"] in {"sap_mb51", "sinkron_sap"}
               for target in targets):
            return _json_error(
                "BON mempunyai item yang sudah SAP. Detail hanya dapat dikoreksi melalui Admin web", 409
            )
        for target in targets:
            db.execute(
                """UPDATE transactions SET penerima=?,keterangan=?,nomor_dokumen=?,
                           updated_by=?,updated_at=datetime('now','localtime') WHERE id=?""",
                (recipient, note or None, document or None, g.user["id"], target["id"]),
            )
            audit(db, g.user["id"], "edit_transaksi_apk", f"trx#{target['id']} detail BON diperbarui")
    return jsonify({
        "ok": True, "transaction_ids": [target["id"] for target in targets],
        "recipient": recipient, "note": note, "document": document,
    })


@bp.post("/transactions/<int:transaction_id>/posting")
@mobile_auth_required
def transaction_posting(transaction_id):
    """Tandai seluruh item BON sudah/belum posting SAP dari APK."""
    if g.user["role"] not in {"admin", "petugas"}:
        return _json_error("Akun ini tidak memiliki hak mengubah status SAP", 403)
    body = request.get_json(silent=True) or {}
    posted = bool(body.get("posted"))
    document = str(body.get("document") or "").strip()
    db = get_db()
    row = db.execute(
        """SELECT t.*,m.plant FROM transactions t JOIN materials m ON m.id=t.material_id
           WHERE t.id=?""", (transaction_id,),
    ).fetchone()
    if not row or row["batal"]:
        return _json_error("Transaksi tidak ditemukan atau sudah dibatalkan", 404)
    if row["sumber"] in {"sap_mb51", "sinkron_sap"}:
        return _json_error("Transaksi hasil sinkron SAP tidak dapat diubah statusnya", 409)
    if g.user.get("kebun_kode") and row["plant"] != g.user["kebun_kode"]:
        return _json_error("Transaksi berada di kebun yang tidak boleh diakses", 403)
    if not posted:
        return _json_error(
            "Status sudah SAP tidak dapat dibuka kembali dari HP. Gunakan Admin web setelah pemeriksaan.", 409
        )
    where, value = ("bon=?", row["bon"]) if row["bon"] else ("id=?", transaction_id)
    with tx(db):
        targets = db.execute(
            f"SELECT id FROM transactions WHERE {where} AND batal=0 AND sumber<>'sinkron_sap'", (value,),
        ).fetchall()
        ids = [target["id"] for target in targets]
        if posted:
            db.execute(
                f"""UPDATE transactions SET sap_posted=1,sap_posted_at=datetime('now','localtime'),
                           sap_posted_by=?,sap_doc=COALESCE(NULLIF(?,''),sap_doc)
                    WHERE id IN ({','.join('?' * len(ids))})""",
                [g.user["id"], document] + ids,
            )
        else:
            db.execute(
                f"""UPDATE transactions SET sap_posted=0,sap_posted_at=NULL,sap_posted_by=NULL
                    WHERE id IN ({','.join('?' * len(ids))})""", ids,
            )
        audit(db, g.user["id"], "posting_sap_apk", f"posted={int(posted)}; trx={ids[:50]}; dok={document}")
    return jsonify({"ok": True, "transaction_ids": ids, "posted": posted, "document": document})


@bp.post("/transactions/<int:transaction_id>/correct")
@mobile_auth_required
def correct_transaction(transaction_id):
    """Koreksi qty satu transaksi dari APK; hanya Admin dan harus online."""
    if g.user["role"] != "admin":
        return _json_error("Hanya Admin yang dapat mengoreksi qty transaksi", 403)
    body = request.get_json(silent=True) or {}
    try:
        quantity = float(str(body.get("quantity") or "").replace(",", "."))
    except ValueError:
        return _json_error("Qty koreksi tidak valid")
    reason = str(body.get("reason") or "").strip()
    if not reason:
        return _json_error("Alasan koreksi wajib diisi")
    db = get_db()
    row = db.execute(
        """SELECT t.*,m.plant,m.deskripsi FROM transactions t
           JOIN materials m ON m.id=t.material_id WHERE t.id=?""", (transaction_id,),
    ).fetchone()
    if not row or row["batal"]:
        return _json_error("Transaksi tidak ditemukan atau sudah dibatalkan", 404)
    if g.user.get("kebun_kode") and row["plant"] != g.user["kebun_kode"]:
        return _json_error("Transaksi berada di kebun yang tidak boleh diakses", 403)
    if row["sap_posted"] or row["sumber"] in {"sap_mb51", "sinkron_sap"}:
        return _json_error(
            "Qty transaksi yang sudah SAP hanya dapat dikoreksi melalui Admin web", 409
        )
    try:
        with tx(db):
            before, after = ubah_qty_transaksi(db, transaction_id, quantity, reason, g.user["id"])
        return jsonify({
            "ok": True, "transaction_id": transaction_id, "material_id": row["material_id"],
            "quantity": bulat(quantity), "before": bulat(before), "stock": bulat(after),
            "sap_warning": bool(row["sap_posted"] and row["sumber"] != "sinkron_sap"),
            "message": f"Qty {row['deskripsi']} berhasil dikoreksi",
        })
    except StockError as exc:
        return _json_error(str(exc), 400)


def _mobile_bon_rows(db, raw_ids):
    """Validasi pilihan transaksi APK untuk operasi gabung/pisah BON."""
    try:
        ids = sorted({int(value) for value in raw_ids})
    except (TypeError, ValueError):
        return [], "Pilihan transaksi tidak valid"
    if not ids or len(ids) > 100:
        return [], "Pilih antara 1 sampai 100 transaksi"
    marks = ",".join("?" * len(ids))
    rows = db.execute(
        f"""SELECT t.*,m.plant FROM transactions t JOIN materials m ON m.id=t.material_id
            WHERE t.id IN ({marks}) ORDER BY t.id""", ids,
    ).fetchall()
    problems = []
    if len(rows) != len(ids) or any(row["batal"] for row in rows):
        problems.append("ada transaksi dibatalkan atau tidak ditemukan")
    if g.user.get("kebun_kode") and any(row["plant"] != g.user["kebun_kode"] for row in rows):
        problems.append("ada transaksi di luar kebun akun")
    if any(row["jenis"] != "keluar" for row in rows):
        problems.append("hanya transaksi barang keluar yang dapat dipilih")
    for column, label in (("jenis", "jenis transaksi"), ("tanggal", "tanggal"),
                          ("plant", "kebun"), ("penerima", "nama penerima")):
        if len({str(row[column] or "").strip().lower() for row in rows}) > 1:
            problems.append(f"{label} berbeda")
    return rows, ("; ".join(problems) if problems else "")


@bp.post("/transactions/bons/merge")
@mobile_auth_required
def merge_bons():
    """Gabungkan transaksi terpilih menjadi satu BON tanpa mengubah stok."""
    if g.user["role"] != "admin":
        return _json_error("Hanya Admin yang dapat menggabungkan BON", 403)
    body = request.get_json(silent=True) or {}
    db = get_db()
    rows, error = _mobile_bon_rows(db, body.get("transaction_ids") or [])
    if len(rows) < 2:
        return _json_error(error or "Pilih minimal dua transaksi")
    if error:
        return _json_error(f"BON tidak dapat digabung: {error}")
    if any(row["sap_posted"] for row in rows) and not body.get("confirm_posted"):
        return _json_error("Ada transaksi yang sudah posting SAP; konfirmasi diperlukan", 409,
                           needs_posted_confirmation=True)
    ids = [row["id"] for row in rows]
    marks = ",".join("?" * len(ids))
    with tx(db):
        target = next((row["bon"] for row in rows if row["bon"]), None) or nomor_bon(db)
        photo1 = next((row["foto"] for row in rows if row["bon"] == target and row["foto"]), None) \
            or next((row["foto"] for row in rows if row["foto"]), None)
        photo2 = next((row["foto_2"] for row in rows if row["bon"] == target and row["foto_2"]), None) \
            or next((row["foto_2"] for row in rows if row["foto_2"]), None)
        db.execute(f"UPDATE transactions SET bon=? WHERE id IN ({marks})", [target] + ids)
        if photo1:
            db.execute(f"UPDATE transactions SET foto=? WHERE foto IS NULL AND id IN ({marks})", [photo1] + ids)
        if photo2:
            db.execute(f"UPDATE transactions SET foto_2=? WHERE foto_2 IS NULL AND id IN ({marks})", [photo2] + ids)
        audit(db, g.user["id"], "gabung_bon_apk", f"trx {ids} disatukan ke {target}")
    return jsonify({"ok": True, "transaction_ids": ids, "bon": target,
                    "has_photo": bool(photo1), "has_photo_2": bool(photo2)})


@bp.post("/transactions/bons/split")
@mobile_auth_required
def split_bon():
    """Pindahkan transaksi terpilih ke satu nomor BON baru tanpa mengubah stok."""
    if g.user["role"] != "admin":
        return _json_error("Hanya Admin yang dapat memisahkan BON", 403)
    body = request.get_json(silent=True) or {}
    db = get_db()
    rows, error = _mobile_bon_rows(db, body.get("transaction_ids") or [])
    if error:
        return _json_error(f"BON tidak dapat dipisahkan: {error}")
    if any(row["sap_posted"] for row in rows) and not body.get("confirm_posted"):
        return _json_error("Ada transaksi yang sudah posting SAP; konfirmasi diperlukan", 409,
                           needs_posted_confirmation=True)
    ids = [row["id"] for row in rows]
    marks = ",".join("?" * len(ids))
    with tx(db):
        new_bon = nomor_bon(db)
        db.execute(f"UPDATE transactions SET bon=? WHERE id IN ({marks})", [new_bon] + ids)
        audit(db, g.user["id"], "pisah_bon_apk", f"trx {ids} dipisahkan ke {new_bon}")
    return jsonify({"ok": True, "transaction_ids": ids, "bon": new_bon})
