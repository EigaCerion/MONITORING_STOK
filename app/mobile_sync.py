"""Aturan domain untuk antrean transaksi APK yang dikirim setelah bekerja offline."""
import datetime as dt
import hashlib
import json
import re
import uuid

from . import pengembalian
from .db import audit, opname_aktif, tx
from .photos import save_photo
from .stock import EPS, bulat, cari_kebun, nomor_bon, simpan_transaksi, stok_material
from .supplementary import target_plants


TARGET_LABELS = {
    "blok": "Blok",
    "unit": "Unit / kendaraan",
    "workshop": "Workshop",
    "office": "Kantor",
    "staff": "Staff",
    "damkar": "Damkar / kebakaran",
}
TARGET_AUTO = {
    "workshop": "CM3AZGO996",
    "office": "CM3AZGO992",
    "staff": "CM3AZGO991",
}
MAX_ITEMS = 30
TRANSFER_TYPES = {"transfer_masuk", "transfer_keluar"}
OUTFLOW_TYPES = {"keluar", "transfer_keluar"}


class MobileSyncError(ValueError):
    """Kesalahan payload APK yang aman ditampilkan kepada operator."""

    def __init__(self, message, status=400, **extra):
        super().__init__(message)
        self.status = status
        self.extra = extra


def _norm(value):
    """Normalisasi teks untuk pencocokan duplikat dan target."""
    return re.sub(r"\s+", " ", str(value or "").strip().upper())


def payload_hash(payload):
    """Sidik stabil isi BON; konfirmasi duplikat tidak mengubah identitasnya."""
    isi = dict(payload)
    isi.pop("confirm_duplicate", None)
    mentah = json.dumps(isi, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(mentah).hexdigest()


def _parse_uuid(value):
    """Pastikan identitas BON berasal dari UUID standar dan kembalikan bentuk baku."""
    try:
        return str(uuid.UUID(str(value or "")))
    except (ValueError, AttributeError, TypeError) as exc:
        raise MobileSyncError("Identitas transaksi offline tidak valid") from exc


def _parse_date(value):
    """Validasi tanggal transaksi terhadap tanggal lokal server."""
    try:
        tanggal = dt.date.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise MobileSyncError("Tanggal transaksi tidak valid") from exc
    if tanggal > dt.date.today():
        raise MobileSyncError("Tanggal transaksi tidak boleh di masa depan")
    return tanggal.isoformat()


def _material_requires_unit(db, material):
    """FAME, kategori BBM, dan sparepart hanya boleh menuju unit/workshop."""
    kategori = ""
    if material["kategori_id"]:
        row = db.execute("SELECT nama FROM kategori WHERE id = ?", (material["kategori_id"],)).fetchone()
        kategori = _norm(row["nama"] if row else "")
    return kategori in {"BBM", "SPAREPART"} or "FAME" in _norm(material["deskripsi"])


def _target(db, plant, raw):
    """Validasi target BON dengan aturan yang sama seperti input browser."""
    raw = raw if isinstance(raw, dict) else {}
    tipe = str(raw.get("type") or "").strip().lower()
    kode = _norm(raw.get("code"))
    aktivitas = str(raw.get("activity") or "").strip()
    if tipe not in TARGET_LABELS:
        raise MobileSyncError("Tujuan BON wajib dipilih")
    if tipe in TARGET_AUTO:
        kode, aktivitas = TARGET_AUTO[tipe], ""
    elif tipe in {"blok", "unit"} and not kode:
        raise MobileSyncError(f"Kode {TARGET_LABELS[tipe].lower()} wajib diisi")
    elif tipe == "damkar":
        if not kode:
            raise MobileSyncError("Kode blok lokasi Damkar wajib diisi")
        if aktivitas not in {"330", "331"}:
            raise MobileSyncError("Pilih aktivitas Damkar 330 atau 331")

    master_type = "blok" if tipe == "damkar" else tipe
    if master_type in {"blok", "unit"}:
        plants = target_plants(plant, master_type)
        marks = ",".join("?" for _ in plants)
        ada_master = db.execute(
            f"SELECT 1 FROM supplementary_targets WHERE plant IN ({marks}) "
            "AND target_type=? AND active=1 LIMIT 1",
            (*plants, master_type),
        ).fetchone()
        if master_type == "unit" and re.fullmatch(r"EX\d+", re.sub(r"[\s-]+", "", kode)):
            kode = "EXA" + re.sub(r"[\s-]+", "", kode)[2:]
        if ada_master:
            found = db.execute(
                f"""SELECT code FROM supplementary_targets
                    WHERE plant IN ({marks}) AND target_type=? AND active=1
                      AND (UPPER(code)=? OR UPPER(sap_code)=?)
                    ORDER BY CASE WHEN plant=? THEN 0 ELSE 1 END LIMIT 1""",
                (*plants, master_type, kode, kode, plant),
            ).fetchone()
            if not found:
                raise MobileSyncError(
                    f"{TARGET_LABELS[tipe]} '{kode}' tidak terdaftar di Master Supplementary"
                )
            kode = found["code"]
    return {"type": tipe, "code": kode, "activity": aktivitas}


def _prepare_items(db, user, payload, transaction_type):
    """Baca item BON sesuai aturan transaksi keluar atau masuk."""
    raw_items = payload.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        raise MobileSyncError("Tambahkan minimal satu barang")
    if len(raw_items) > MAX_ITEMS:
        raise MobileSyncError(f"Satu BON maksimal {MAX_ITEMS} baris barang")
    prepared, seen = [], set()
    plants = set()
    for raw in raw_items:
        if not isinstance(raw, dict):
            raise MobileSyncError("Format barang tidak valid")
        try:
            material_id = int(raw.get("material_id"))
            qty = bulat(float(raw.get("qty")))
        except (TypeError, ValueError) as exc:
            raise MobileSyncError("Material atau qty tidak valid") from exc
        material = db.execute("SELECT * FROM materials WHERE id=?", (material_id,)).fetchone()
        if not material:
            raise MobileSyncError("Material tidak ditemukan; lakukan Sinkron pagi kembali", 409)
        if user["kebun_kode"] and material["plant"] != user["kebun_kode"]:
            raise MobileSyncError("Material berada di kebun yang tidak boleh diakses", 403)
        if qty <= 0:
            raise MobileSyncError(f"{material['deskripsi']}: qty harus lebih dari 0")
        if material["expired"] or (material["expired_at"] and material["expired_at"] <= dt.date.today().isoformat()):
            raise MobileSyncError(f"{material['deskripsi']}: barang EXPIRED dan diblokir")
        if material["input_locked"]:
            alasan = material["input_lock_reason"] or "kode material dikunci oleh admin"
            raise MobileSyncError(f"{material['material']} — {material['deskripsi']}: {alasan}", 409)
        target = {"type": "", "code": "", "activity": ""}
        if transaction_type == "keluar":
            target = _target(db, material["plant"], raw.get("target") or payload.get("target"))
            if _material_requires_unit(db, material) and target["type"] not in {"unit", "workshop"}:
                raise MobileSyncError(
                    f"{material['deskripsi']}: FAME, BBM, dan sparepart wajib ke Unit atau Workshop"
                )
        key = (material_id, target["type"], target["code"], target["activity"])
        if key in seen:
            pesan = (" untuk tujuan yang sama" if transaction_type == "keluar" else "")
            raise MobileSyncError(f"{material['deskripsi']} tercantum dua kali{pesan}")
        seen.add(key)
        kembali = None
        if transaction_type == "keluar":
            kembali, kembali_error = pengembalian.baca(
                raw.get("return_qty"), raw.get("return_condition"), qty,
                material["wajib_kembali"], material["deskripsi"],
            )
            if kembali_error:
                raise MobileSyncError(kembali_error)
        prepared.append({"material": material, "qty": qty, "target": target, "return": kembali})
        plants.add(material["plant"])
    if len(plants) != 1:
        raise MobileSyncError("Semua barang dalam satu BON harus dari kebun yang sama")
    return prepared, next(iter(plants))


def _signature_rows(rows):
    """Bentuk tanda tangan item transaksi tersimpan untuk penjaga duplikat."""
    return sorted((
        int(row["material_id"]), bulat(row["qty"]), row["tujuan_tipe"] or "",
        _norm(row["tujuan_kode"]), row["aktivitas_kode"] or "",
    ) for row in rows)


def find_duplicates(db, transaction_type, tanggal, penerima, document, counterparty, prepared):
    """Cari BON aktif pada hari yang sama dengan detail dan seluruh item identik."""
    expected = sorted((
        int(item["material"]["id"]), item["qty"], item["target"]["type"],
        _norm(item["target"]["code"]), item["target"]["activity"],
    ) for item in prepared)
    rows = db.execute(
        """SELECT t.*, m.deskripsi, m.satuan, u.nama AS dibuat_oleh
           FROM transactions t JOIN materials m ON m.id=t.material_id
           LEFT JOIN users u ON u.id=t.created_by
           WHERE t.tanggal=? AND t.jenis=? AND t.batal=0
           ORDER BY t.id DESC""",
        (tanggal, transaction_type),
    ).fetchall()
    groups = {}
    for source in rows:
        row = dict(source)
        groups.setdefault(row["bon"] or f"TRX-{row['id']}", []).append(row)
    result = []
    for bon, members in groups.items():
        if _norm(members[0]["penerima"]) != _norm(penerima):
            continue
        if _norm(members[0]["nomor_dokumen"]) != _norm(document):
            continue
        saved_counterparty = (members[0]["kebun_asal"] if transaction_type == "transfer_masuk"
                              else members[0]["kebun_tujuan"] if transaction_type == "transfer_keluar"
                              else "")
        if _norm(saved_counterparty) != _norm(counterparty):
            continue
        if _signature_rows(members) != expected:
            continue
        result.append({
            "bon": bon,
            "tanggal": members[0]["tanggal"],
            "penerima": members[0]["penerima"] or "-",
            "nomor_dokumen": members[0]["nomor_dokumen"] or "-",
            "dibuat_oleh": members[0]["dibuat_oleh"] or "-",
            "items": [
                {"description": row["deskripsi"], "qty": row["qty"], "unit": row["satuan"]}
                for row in reversed(members)
            ],
        })
    return result[:5]


def _existing_submission(db, client_uuid, expected_hash):
    """Kembalikan hasil retry yang sama atau tolak UUID yang dipakai untuk isi berbeda."""
    row = db.execute("SELECT * FROM mobile_submissions WHERE client_uuid=?", (client_uuid,)).fetchone()
    if not row:
        return None
    if row["payload_hash"] != expected_hash:
        raise MobileSyncError("Identitas transaksi sudah dipakai untuk isi yang berbeda", 409)
    transaction_ids = json.loads(row["transaction_ids"])
    transaction_type, stocks = "", {}
    if transaction_ids:
        marks = ",".join("?" * len(transaction_ids))
        saved = db.execute(
            f"SELECT jenis,material_id FROM transactions WHERE id IN ({marks}) ORDER BY id",
            transaction_ids,
        ).fetchall()
        transaction_type = saved[0]["jenis"] if saved else ""
        stocks = {
            str(saved_row["material_id"]): bulat(stok_material(db, saved_row["material_id"]))
            for saved_row in saved
        }
    return {
        "ok": True,
        "already_received": True,
        "bon": row["bon"],
        "transaction_ids": transaction_ids,
        "transaction_type": transaction_type,
        "stocks": stocks,
    }


def save_submission(db, user, device, payload, photo_1, photo_2=None, return_photo=None):
    """Validasi dan simpan satu BON APK secara idempoten.

    Foto diproses setelah seluruh validasi awal lulus. Validasi stok dan status
    material diulang di dalam transaksi tulis untuk mencegah race antar-HP.
    """
    if not isinstance(payload, dict):
        raise MobileSyncError("Payload transaksi tidak valid")
    client_uuid = _parse_uuid(payload.get("client_uuid"))
    expected_hash = payload_hash(payload)
    existing = _existing_submission(db, client_uuid, expected_hash)
    if existing:
        return existing
    transaction_type = str(payload.get("type") or "").strip().lower()
    if transaction_type not in {"keluar", "masuk", *TRANSFER_TYPES}:
        raise MobileSyncError("Jenis transaksi APK tidak didukung")
    tanggal = _parse_date(payload.get("date"))
    penerima = str(payload.get("recipient") or "").strip()
    document = str(payload.get("document") or "").strip()
    if transaction_type in OUTFLOW_TYPES and not penerima:
        raise MobileSyncError("Nama penerima wajib diisi")
    if transaction_type in OUTFLOW_TYPES and (not photo_1 or not photo_1.filename):
        raise MobileSyncError("Foto orang/penerima wajib diambil")
    prepared, plant = _prepare_items(db, user, payload, transaction_type)
    counterparty = ""
    if transaction_type in TRANSFER_TYPES:
        if not document:
            raise MobileSyncError("Nomor dokumen transfer wajib diisi")
        counterparty = cari_kebun(db, payload.get("counterparty_plant")) or ""
        active_counterparty = (db.execute("SELECT kode FROM kebun WHERE kode=? AND aktif=1", (counterparty,)).fetchone()
                               if counterparty else None)
        if not active_counterparty:
            direction = "asal" if transaction_type == "transfer_masuk" else "tujuan"
            raise MobileSyncError(f"Kebun {direction} wajib dipilih dari daftar aktif")
        if counterparty == plant:
            raise MobileSyncError("Kebun asal dan tujuan transfer tidak boleh sama")
    if opname_aktif(db, plant):
        raise MobileSyncError("Gudang sedang Stock Opname; transaksi baru dikunci", 409)
    duplicates = find_duplicates(
        db, transaction_type, tanggal, penerima, document, counterparty, prepared,
    )
    if duplicates and not payload.get("confirm_duplicate"):
        raise MobileSyncError(
            "Kemungkinan double input ditemukan. Periksa transaksi sebelumnya.",
            409, duplicate=True, matches=duplicates,
        )
    needs_return_photo = transaction_type == "keluar" and any(
        pengembalian.ada_barang(item["return"]) for item in prepared
    )
    if needs_return_photo and not (return_photo and return_photo.filename):
        raise MobileSyncError("Foto barang lama wajib karena ada barang lama yang dikembalikan")

    image_1 = (save_photo(photo_1, metadata_time=payload.get("photo_time"))
               if photo_1 and photo_1.filename else None)
    image_2 = save_photo(photo_2, metadata_time=payload.get("photo_2_time")) if photo_2 and photo_2.filename else None
    old_image = (save_photo(return_photo, metadata_time=payload.get("return_photo_time"))
                 if needs_return_photo else None)

    with tx(db):
        existing = _existing_submission(db, client_uuid, expected_hash)
        if existing:
            return existing
        if opname_aktif(db, plant):
            raise MobileSyncError("Gudang sedang Stock Opname; transaksi baru dikunci", 409)
        prepared, _ = _prepare_items(db, user, payload, transaction_type)
        # Pemeriksaan awal memberi respons cepat, tetapi dua HP dapat mulai
        # mengunggah transaksi identik pada waktu yang hampir bersamaan. Ulangi
        # di dalam BEGIN IMMEDIATE agar hanya unggahan pertama yang tersimpan;
        # unggahan kedua kembali menjadi konfirmasi duplikat.
        duplicates = find_duplicates(
            db, transaction_type, tanggal, penerima, document, counterparty, prepared,
        )
        if duplicates and not payload.get("confirm_duplicate"):
            raise MobileSyncError(
                "Kemungkinan double input ditemukan. Periksa transaksi sebelumnya.",
                409, duplicate=True, matches=duplicates,
            )
        if transaction_type in OUTFLOW_TYPES:
            total_by_material = {}
            for item in prepared:
                material_id = item["material"]["id"]
                total_by_material[material_id] = total_by_material.get(material_id, 0) + item["qty"]
            for material_id, total in total_by_material.items():
                stock = stok_material(db, material_id)
                if total > stock + EPS:
                    material = next(i["material"] for i in prepared if i["material"]["id"] == material_id)
                    raise MobileSyncError(
                        f"{material['deskripsi']}: qty {total:g} melebihi stok server {stock:g}", 409
                    )
        bon = nomor_bon(db)
        transaction_ids = []
        for item in prepared:
            target = item["target"]
            transaction_id = simpan_transaksi(db, {
                "tanggal": tanggal,
                "jenis": transaction_type,
                "material_id": item["material"]["id"],
                "qty": item["qty"],
                "nomor_dokumen": document,
                "penerima": penerima,
                "kebun_asal": counterparty if transaction_type == "transfer_masuk" else None,
                "kebun_tujuan": counterparty if transaction_type == "transfer_keluar" else None,
                "keterangan": str(payload.get("note") or "").strip(),
                "foto": image_1,
                "foto_2": image_2,
                "sumber": "apk",
                "tujuan_tipe": target["type"],
                "tujuan_kode": target["code"],
                "aktivitas_kode": target["activity"],
                "bon": bon,
            }, user["id"])
            transaction_ids.append(transaction_id)
            if transaction_type == "keluar" and item["return"]:
                pengembalian.simpan(
                    db, transaction_id, item["material"]["id"], tanggal,
                    item["return"][0], item["return"][1], None, old_image,
                    penerima, user["id"],
                )
        db.execute(
            """INSERT INTO mobile_submissions
               (client_uuid, device_id, user_id, payload_hash, bon, transaction_ids)
               VALUES (?,?,?,?,?,?)""",
            (client_uuid, device["id"], user["id"], expected_hash, bon,
             json.dumps(transaction_ids, separators=(",", ":"))),
        )
        db.execute(
            "INSERT INTO mobile_sync_log (device_id,user_id,action,detail) VALUES (?,?,?,?)",
            (device["id"], user["id"], "upload_transaction",
             f"{bon}; {transaction_type}; {len(transaction_ids)} item"),
        )
        audit(db, user["id"], "upload_apk",
              f"{bon}; {transaction_type}; device#{device['id']}; {len(transaction_ids)} item")
        stocks = {
            str(item["material"]["id"]): bulat(stok_material(db, item["material"]["id"]))
            for item in prepared
        }
    return {
        "ok": True,
        "already_received": False,
        "bon": bon,
        "transaction_ids": transaction_ids,
        "transaction_type": transaction_type,
        "stocks": stocks,
        "message": f"{bon} berhasil diunggah ke server",
    }


def _existing_return_submission(db, client_uuid, expected_hash):
    """Kembalikan pengembalian lama untuk retry atau tolak UUID dengan isi berbeda."""
    row = db.execute(
        "SELECT * FROM mobile_return_submissions WHERE client_uuid=?", (client_uuid,),
    ).fetchone()
    if not row:
        return None
    if row["payload_hash"] != expected_hash:
        raise MobileSyncError("Identitas pengembalian sudah dipakai untuk isi yang berbeda", 409)
    return {
        "ok": True, "already_received": True, "return_id": row["return_id"],
        "transaction_id": row["transaction_id"],
    }


def save_return_submission(db, user, device, transaction_id, payload, photo=None):
    """Catat pengembalian yang menyusul dari antrean APK secara idempoten."""
    if not isinstance(payload, dict):
        raise MobileSyncError("Payload pengembalian tidak valid")
    client_uuid = _parse_uuid(payload.get("client_uuid"))
    expected_hash = payload_hash(payload)
    existing = _existing_return_submission(db, client_uuid, expected_hash)
    if existing:
        return existing

    row = db.execute(
        """SELECT t.*, m.material, m.deskripsi, m.satuan, m.plant, m.wajib_kembali
           FROM transactions t JOIN materials m ON m.id=t.material_id WHERE t.id=?""",
        (transaction_id,),
    ).fetchone()
    if not row or row["jenis"] != "keluar" or row["batal"]:
        raise MobileSyncError("Transaksi keluar tidak ditemukan atau sudah dibatalkan", 404)
    if user.get("kebun_kode") and row["plant"] != user["kebun_kode"]:
        raise MobileSyncError("Transaksi berada di kebun yang tidak boleh diakses", 403)
    if db.execute(
        "SELECT 1 FROM pengembalian WHERE transaksi_id=? AND batal=0 LIMIT 1", (transaction_id,),
    ).fetchone():
        raise MobileSyncError("Pengembalian untuk transaksi ini sudah tercatat", 409)

    data, error = pengembalian.baca(
        payload.get("qty"), payload.get("condition"), row["qty"], True, row["deskripsi"],
    )
    if error or not data:
        raise MobileSyncError(error or "Isi qty dan kondisi barang lama")
    if pengembalian.ada_barang(data) and not (photo and photo.filename):
        raise MobileSyncError("Foto barang lama wajib karena ada barang lama yang dikembalikan")
    date = _parse_date(payload.get("date"))
    image = save_photo(photo, metadata_time=payload.get("photo_time")) if photo and photo.filename else None

    with tx(db):
        existing = _existing_return_submission(db, client_uuid, expected_hash)
        if existing:
            return existing
        if db.execute(
            "SELECT 1 FROM pengembalian WHERE transaksi_id=? AND batal=0 LIMIT 1", (transaction_id,),
        ).fetchone():
            raise MobileSyncError("Pengembalian untuk transaksi ini sudah tercatat", 409)
        return_id = pengembalian.simpan(
            db, transaction_id, row["material_id"], date, data[0], data[1],
            str(payload.get("note") or "").strip(), image,
            str(payload.get("returned_by") or "").strip() or row["penerima"], user["id"],
        )
        db.execute(
            """INSERT INTO mobile_return_submissions
               (client_uuid,device_id,user_id,transaction_id,return_id,payload_hash)
               VALUES (?,?,?,?,?,?)""",
            (client_uuid, device["id"], user["id"], transaction_id, return_id, expected_hash),
        )
        db.execute(
            "INSERT INTO mobile_sync_log (device_id,user_id,action,detail) VALUES (?,?,?,?)",
            (device["id"], user["id"], "upload_return", f"trx#{transaction_id}; kembali#{return_id}"),
        )
        audit(db, user["id"], "pengembalian_apk", f"trx#{transaction_id} {row['material']}: {data[0]:g} {data[1]}")
    return {
        "ok": True, "already_received": False, "return_id": return_id,
        "transaction_id": transaction_id, "message": "Pengembalian barang lama diterima server",
    }


def _existing_photo_submission(db, client_uuid, expected_hash):
    """Kembalikan lampiran foto lama untuk retry UUID yang sama."""
    row = db.execute(
        "SELECT * FROM mobile_photo_submissions WHERE client_uuid=?", (client_uuid,),
    ).fetchone()
    if not row:
        return None
    if row["payload_hash"] != expected_hash:
        raise MobileSyncError("Identitas lampiran foto sudah dipakai untuk isi berbeda", 409)
    return {
        "ok": True, "already_received": True, "transaction_id": row["transaction_id"],
        "slot": row["slot"], "filename": row["filename"],
    }


def save_photo_submission(db, user, device, transaction_id, payload, photo=None):
    """Lampirkan foto 1/2 ke transaksi atau seluruh item BON dari antrean APK."""
    if user.get("role") not in {"admin", "petugas"}:
        raise MobileSyncError("Akun ini tidak memiliki hak melampirkan foto transaksi", 403)
    if not isinstance(payload, dict):
        raise MobileSyncError("Payload lampiran foto tidak valid")
    client_uuid = _parse_uuid(payload.get("client_uuid"))
    expected_hash = payload_hash(payload)
    existing = _existing_photo_submission(db, client_uuid, expected_hash)
    if existing:
        return existing
    slot = 2 if str(payload.get("slot")) == "2" else 1
    column = "foto_2" if slot == 2 else "foto"
    row = db.execute(
        """SELECT t.*,m.plant FROM transactions t JOIN materials m ON m.id=t.material_id
           WHERE t.id=?""", (transaction_id,),
    ).fetchone()
    if not row or row["batal"]:
        raise MobileSyncError("Transaksi tidak ditemukan atau sudah dibatalkan", 404)
    if user.get("kebun_kode") and row["plant"] != user["kebun_kode"]:
        raise MobileSyncError("Transaksi berada di kebun yang tidak boleh diakses", 403)
    if row["sap_posted"] or row["sumber"] in {"sap_mb51", "sinkron_sap"}:
        raise MobileSyncError(
            "Transaksi sudah direalisasikan SAP dan bersifat hanya-baca. Foto tidak dapat ditambahkan dari HP.",
            409,
        )
    if row[column]:
        raise MobileSyncError(f"Foto {slot} transaksi sudah terisi", 409)
    if not (photo and photo.filename):
        raise MobileSyncError("Pilih foto terlebih dahulu")
    filename = save_photo(photo, metadata_time=payload.get("photo_time"))

    with tx(db):
        existing = _existing_photo_submission(db, client_uuid, expected_hash)
        if existing:
            return existing
        current = db.execute(
            f"SELECT bon,{column} AS foto,sap_posted,sumber FROM transactions WHERE id=?",
            (transaction_id,),
        ).fetchone()
        if not current or current["foto"]:
            raise MobileSyncError(f"Foto {slot} transaksi sudah terisi", 409)
        if current["sap_posted"] or current["sumber"] in {"sap_mb51", "sinkron_sap"}:
            raise MobileSyncError(
                "Transaksi berubah menjadi sudah SAP sebelum foto diunggah. Sinkronkan ulang aplikasi.", 409
            )
        if current["bon"] and db.execute(
            """SELECT 1 FROM transactions WHERE bon=? AND batal=0
               AND (sap_posted=1 OR sumber IN ('sap_mb51','sinkron_sap')) LIMIT 1""",
            (current["bon"],),
        ).fetchone():
            raise MobileSyncError(
                "BON ini sudah mempunyai item yang direalisasikan SAP. Foto tidak dapat diubah dari HP.", 409
            )
        affected = db.execute(
            f"UPDATE transactions SET {column}=? WHERE id=?", (filename, transaction_id),
        ).rowcount
        if current["bon"]:
            affected += db.execute(
                f"UPDATE transactions SET {column}=? WHERE bon=? AND {column} IS NULL",
                (filename, current["bon"]),
            ).rowcount
        db.execute(
            """INSERT INTO mobile_photo_submissions
               (client_uuid,device_id,user_id,transaction_id,slot,filename,payload_hash)
               VALUES (?,?,?,?,?,?,?)""",
            (client_uuid, device["id"], user["id"], transaction_id, slot, filename, expected_hash),
        )
        db.execute(
            "INSERT INTO mobile_sync_log (device_id,user_id,action,detail) VALUES (?,?,?,?)",
            (device["id"], user["id"], "upload_transaction_photo", f"trx#{transaction_id}; foto {slot}"),
        )
        audit(db, user["id"], "lampir_foto_apk", f"trx#{transaction_id} foto {slot}: {filename}")
    return {
        "ok": True, "already_received": False, "transaction_id": transaction_id,
        "slot": slot, "filename": filename, "affected": affected,
    }
