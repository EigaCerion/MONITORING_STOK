"""Halaman admin: upload master SAP, import transaksi Excel, kebun, kategori, pengguna, audit."""
import datetime as dt
import hashlib
import json
import re
import uuid
from difflib import SequenceMatcher
from pathlib import Path

from flask import (Blueprint, abort, current_app, flash, g, jsonify, redirect, render_template, request,
                   send_from_directory, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

from .auth import ROLE_LABEL, login_required
from .db import MOBILE_DEFAULTS, audit, get_db, mobile_pengaturan, opname_aktif, tx
from .excel_io import (GUDANG_SLOC, ExcelError, angka, baca_master_sap, baca_mb51,
                       baca_supplementary, baca_transaksi)
from .report import EFEK_SQL
from .stock import (ALASAN_SAP, EPS, JENIS_LABEL, bulat, cari_kebun, efek, sesuaikan_stok, simpan_transaksi,
                    stok_material, target_sap, validasi)
from .views_main import PER_HAL, _query_stok, halaman, hari_ini, kembali, wants_json

bp = Blueprint("admin", __name__)
STATIC_TEMPLATE = Path(__file__).parent / "static" / "template"


def _simpan_upload(file):
    """Simpan upload Excel ke folder sementara dengan nama acak yang aman."""
    if not file or not file.filename:
        raise ExcelError("Pilih file Excel (.xlsx) terlebih dahulu")
    if not file.filename.lower().endswith((".xlsx", ".xlsm")):
        raise ExcelError("File harus berformat .xlsx (Excel). Simpan ulang file SAP sebagai .xlsx")
    path = Path(current_app.config["TMP_FOLDER"]) / f"{uuid.uuid4().hex}.xlsx"
    file.save(path)
    return path


@bp.route("/template/<nama>")
@login_required("admin")
def unduh_template(nama):
    """Kirim template Excel resmi dari daftar nama file yang diizinkan."""
    return send_from_directory(STATIC_TEMPLATE, nama, as_attachment=True)


# ------------------------------------------------------------------ master SAP
@bp.route("/master")
@login_required("admin")
def master():
    """Tampilkan master material SAP beserta filter dan pengaturan cepat."""
    rows, total = _query_stok(request.args)
    db = get_db()
    grup = db.execute(
        """SELECT m.material_group, COUNT(*) AS n, SUM(m.kategori_id IS NULL) AS tanpa,
                  GROUP_CONCAT(DISTINCT k.nama) AS kategori,
                  (SELECT deskripsi FROM materials x WHERE x.material_group = m.material_group LIMIT 1) AS contoh
           FROM materials m LEFT JOIN kategori k ON k.id = m.kategori_id
           GROUP BY m.material_group ORDER BY m.material_group""").fetchall()
    info = db.execute("SELECT MAX(sap_updated_at) AS terakhir, MIN(tgl_stok_awal) AS awal, COUNT(*) AS n "
                      "FROM materials").fetchone()
    supplementary = db.execute(
        """SELECT COUNT(*) AS n, SUM(target_type = 'blok') AS blok, SUM(target_type = 'unit') AS unit,
                  MAX(updated_at) AS terakhir FROM supplementary_targets WHERE active = 1""").fetchone()
    return render_template("master.html", rows=rows, total=total, hal=halaman(), per_hal=PER_HAL, grup=grup,
                           info=info, kategori=db.execute("SELECT * FROM kategori ORDER BY urutan").fetchall(),
                           kebun=db.execute("SELECT * FROM kebun ORDER BY nama").fetchall(), hari_ini=hari_ini(),
                           now_local=dt.datetime.now().strftime("%Y-%m-%dT%H:%M"),
                           supplementary=supplementary,
                           perlu_reset=db.execute("SELECT 1 FROM pengaturan WHERE kunci = 'reset_stok_awal'").fetchone())


@bp.route("/master/upload", methods=["POST"])
@login_required("admin")
def master_upload():
    """Validasi dan terapkan export master SAP tanpa menggandakan stok aplikasi."""
    db = get_db()
    try:
        path = _simpan_upload(request.files.get("file"))
        try:
            rows, peringatan = baca_master_sap(path)
        finally:
            path.unlink(missing_ok=True)
    except ExcelError as e:
        flash(str(e), "error")
        return redirect(url_for("admin.master"))

    tgl_awal = request.form.get("tgl_stok_awal") or hari_ini()
    sinkron = request.form.get("sinkron") == "1"
    # Mulai baru: file ini menjadi stok awal semua material. Transaksi yang tersisa & belum masuk SAP
    # tetap dihitung (stok aplikasi = SAP + transaksi belum diinput SAP).
    reset = request.form.get("reset") == "1"
    # Waktu data SAP (jam export), dipakai untuk menentukan transaksi mana yang sudah tercermin di SAP.
    try:
        sekarang = dt.datetime.fromisoformat(request.form.get("sap_per", "")).strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        sekarang = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if reset:
        tgl_awal = sekarang[:10]
        sinkron = False

    def sudah_di_sap(mid):
        """Efek transaksi yang sudah tercermin di file SAP ini (✓ SAP sebelum jam data SAP)."""
        return db.execute(
            f"""SELECT COALESCE(SUM({EFEK_SQL}), 0) FROM transactions WHERE material_id = ? AND batal = 0
                AND sap_posted = 1 AND sap_posted_at <= ?""", (mid, sekarang)).fetchone()[0]

    baru = update = disinkron = 0
    plants = set()
    attrs = ["name1", "material_group", "deskripsi", "storage_location", "batch", "satuan", "value_unrestricted",
             "stock_in_transit", "material_type"]
    with tx(db):
        for r in rows:
            plant = r["plant"] or "UMUM"
            plants.add(plant)
            if not db.execute("SELECT 1 FROM kebun WHERE kode = ?", (plant,)).fetchone():
                db.execute("INSERT INTO kebun (kode, nama) VALUES (?, ?)", (plant, r["name1"] or plant))
            m = db.execute("SELECT * FROM materials WHERE plant = ? AND material = ? AND special_stock = ? "
                           "AND special_stock_no = ?",
                           (plant, r["material"], r["special_stock"], r["special_stock_no"])).fetchone()
            if m:
                db.execute(f"""UPDATE materials SET {', '.join(f'{a} = ?' for a in attrs)}, sap_unrestricted = ?,
                               sap_updated_at = ?, ada_di_sap = 1, updated_at = datetime('now','localtime')
                               WHERE id = ?""",
                           [r[a] for a in attrs] + [r["unrestricted"], sekarang, m["id"]])
                update += 1
                if reset:
                    db.execute("UPDATE materials SET stok_awal = ?, tgl_stok_awal = ? WHERE id = ?",
                               (bulat(r["unrestricted"] - sudah_di_sap(m["id"])), tgl_awal, m["id"]))
                target = target_sap(db, m["id"]) if sinkron else None
                if target is not None and abs(stok_material(db, m["id"]) - target) > EPS:
                    sesuaikan_stok(db, m["id"], target, ALASAN_SAP,
                                   f"Upload master SAP per {sekarang}", hari_ini(), g.user["id"])
                    disinkron += 1
            else:
                db.execute(f"""INSERT INTO materials (plant, material, special_stock, special_stock_no,
                                   {', '.join(attrs)}, stok_awal, tgl_stok_awal, sap_unrestricted, sap_updated_at)
                               VALUES (?,?,?,?,{', '.join('?' * len(attrs))},?,?,?,?)""",
                           [plant, r["material"], r["special_stock"], r["special_stock_no"]]
                           + [r[a] for a in attrs] + [r["unrestricted"], tgl_awal, r["unrestricted"], sekarang])
                baru += 1
        hilang = 0
        for p in plants:
            hilang_ids = [x[0] for x in db.execute(
                """SELECT id FROM materials WHERE plant = ? AND (sap_updated_at IS NULL OR sap_updated_at <> ?)""",
                (p, sekarang))]
            hilang += len(hilang_ids)
            for mid in hilang_ids:
                db.execute("UPDATE materials SET ada_di_sap = 0 WHERE id = ?", (mid,))
                if reset:  # tidak ada di tarikan SAP baru -> stok SAP dianggap 0
                    db.execute("UPDATE materials SET stok_awal = ?, tgl_stok_awal = ?, sap_unrestricted = 0, "
                               "sap_updated_at = ? WHERE id = ?",
                               (bulat(-sudah_di_sap(mid)), tgl_awal, sekarang, mid))
        if reset:
            db.execute("DELETE FROM pengaturan WHERE kunci = 'reset_stok_awal'")
        audit(db, g.user["id"], "upload_master_sap",
              f"{request.files['file'].filename}: baru {baru}, diperbarui {update}, disinkron {disinkron}, "
              f"tidak ada di file {hilang}" + (" — MULAI BARU (stok awal direset)" if reset else ""))
    flash(f"Master SAP diproses: {baru} material baru (stok awal per {tgl_awal}), {update} diperbarui"
          + (f", {disinkron} disamakan dengan SAP" if sinkron else "")
          + (f". {hilang} material lama tidak ada di file ini (tetap disimpan)." if hilang else "."), "ok")
    for p in peringatan:
        flash(p, "warn")
    if reset:
        flash(f"Mulai baru: stok awal semua material = tarikan SAP per {sekarang}. Transaksi yang belum diinput "
              f"SAP tetap mengurangi/menambah stok aplikasi.", "ok")
    elif update and not sinkron:
        flash("Stok aplikasi tidak diubah. Cek menu 'Selisih SAP': transaksi yang belum diposting ke SAP "
              "tidak dihitung sebagai selisih.", "warn")
    return redirect(url_for("admin.master"))


@bp.route("/master/supplementary", methods=["POST"])
@login_required("admin")
def supplementary_upload():
    """Impor master blok dan unit dari workbook Supplementary Plant Maintenance."""
    upload = request.files.get("file")
    original_name = upload.filename if upload else ""
    try:
        path = _simpan_upload(upload)
        try:
            rows, warnings = baca_supplementary(path)
        finally:
            path.unlink(missing_ok=True)
    except ExcelError as error:
        flash(str(error), "error")
        return redirect(url_for("admin.master") + "#supplementary")

    blok, unit = simpan_supplementary(get_db(), rows, original_name, g.user["id"])
    flash(f"Master Supplementary diperbarui: {blok} blok dan {unit} unit.", "ok")
    for warning in warnings:
        flash(warning, "warn")
    return redirect(url_for("admin.master") + "#supplementary")


def simpan_supplementary(db, rows, original_name, user_id):
    """Ganti kelompok master Supplementary secara transaksional dan catat audit."""
    groups = {(row["plant"], row["target_type"]) for row in rows}
    with tx(db):
        for plant, target_type in groups:
            db.execute("UPDATE supplementary_targets SET active = 0 WHERE plant = ? AND target_type = ?",
                       (plant, target_type))
        for row in rows:
            source = f"{original_name} · {row['source']}"
            db.execute(
                """INSERT INTO supplementary_targets
                       (plant, target_type, code, sap_code, name, division, phase, source, active, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, datetime('now','localtime'))
                   ON CONFLICT(plant, target_type, code) DO UPDATE SET
                       sap_code=excluded.sap_code, name=excluded.name, division=excluded.division,
                       phase=excluded.phase, source=excluded.source, active=1, updated_at=excluded.updated_at""",
                (row["plant"], row["target_type"], row["code"], row["sap_code"], row["name"],
                 row["division"], row["phase"], source),
            )
            if not db.execute("SELECT 1 FROM kebun WHERE kode = ?", (row["plant"],)).fetchone():
                db.execute("INSERT INTO kebun (kode, nama) VALUES (?, ?)", (row["plant"], row["plant"]))
        audit(db, user_id, "upload_supplementary",
              f"{original_name}: {len(rows)} tujuan ({len(groups)} kelompok plant/jenis)")
    return (sum(row["target_type"] == "blok" for row in rows),
            sum(row["target_type"] == "unit" for row in rows))


@bp.route("/master/baru", methods=["GET", "POST"])
@login_required("admin")
def material_baru():
    """Material yang belum ada di export SAP (mis. barang baru pertama kali dipesan untuk kebun ini).
    Stok awal 0; stok bertambah lewat transaksi MASUK. Saat export SAP berikutnya memuat material ini
    (kode + plant + special stock sama), baris ini otomatis tersambung dengan data SAP."""
    db = get_db()
    kebun = db.execute("SELECT * FROM kebun WHERE aktif = 1 ORDER BY nama").fetchall()
    kategori = db.execute("SELECT * FROM kategori ORDER BY urutan").fetchall()
    f = request.form if request.method == "POST" else request.args
    if request.method == "POST":
        plant = f.get("plant", "")
        kode = f.get("material", "").strip().upper()
        desk = f.get("deskripsi", "").strip().upper()
        satuan = f.get("satuan", "").strip().upper()
        sp = f.get("special_stock", "").strip().upper()
        sp_no = f.get("special_stock_no", "").strip().upper()
        minimum = angka(f.get("stok_minimum")) or 0
        err = []
        if not db.execute("SELECT 1 FROM kebun WHERE kode = ?", (plant,)).fetchone():
            err.append("Pilih kebun (plant)")
        if not kode or not desk or not satuan:
            err.append("Kode material, deskripsi, dan satuan wajib diisi")
        if minimum < 0:
            err.append("Stok minimum tidak boleh negatif")
        ada = db.execute("SELECT id FROM materials WHERE plant = ? AND material = ? AND special_stock = ? "
                         "AND special_stock_no = ?", (plant, kode, sp, sp_no)).fetchone()
        if ada:
            err.append(f"Material {kode} sudah ada di kebun {plant}")
        if not err:
            nama_kebun = db.execute("SELECT nama FROM kebun WHERE kode = ?", (plant,)).fetchone()["nama"]
            with tx(db):
                cur = db.execute(
                    """INSERT INTO materials (name1, material_group, material, deskripsi, plant, satuan, special_stock,
                           special_stock_no, stok_awal, tgl_stok_awal, kategori_id, stok_minimum, ada_di_sap,
                           wajib_kembali, expired, expired_at)
                       VALUES (?,?,?,?,?,?,?,?,0,?,?,?,0,?,?,?)""",
                    (nama_kebun, f.get("material_group", "").strip(), kode, desk, plant, satuan, sp, sp_no,
                     hari_ini(), f.get("kategori_id", type=int), minimum, 1 if f.get("wajib_kembali") else 0,
                     1 if f.get("expired") else 0, f.get("expired_at") or None))
                audit(db, g.user["id"], "tambah_material", f"{plant} {kode} {desk} ({satuan})")
            flash(f"Material {kode} {desk} ditambahkan dengan stok 0. Lanjutkan input MASUK-nya.", "ok")
            return redirect(url_for("main.input_transaksi", tombol="masuk", material_id=cur.lastrowid))
        for e in err:
            flash(e, "error")
    return render_template("material_baru.html", f=f, kebun=kebun, kategori=kategori,
                           plant_default=(kebun[0]["kode"] if len(kebun) == 1 else ""))


@bp.route("/master/atur", methods=["POST"])
@login_required("admin")
def master_atur():
    """Ubah kategori, minimum, wajib tukar, expired, dan kunci input material."""
    db = get_db()
    ids = [int(i) for i in request.form.getlist("ids")]
    kat = request.form.get("kategori_id")
    mn = request.form.get("stok_minimum", "").strip()
    wk = request.form.get("wajib_kembali", "")
    expired = request.form.get("expired", "")
    expired_at = request.form.get("expired_at")
    input_locked = request.form.get("input_locked", "")
    input_lock_reason = request.form.get("input_lock_reason")
    if not ids:
        flash("Centang material yang akan diubah", "error")
        return redirect(kembali(url_for("admin.master")))
    sets, args = [], []
    if kat not in (None, "", "-"):
        sets.append("kategori_id = ?")
        args.append(None if kat == "none" else int(kat))
    if mn:
        x = angka(mn)
        if x is None or x < 0:
            msg = "Stok minimum harus angka ≥ 0"
            if wants_json():
                return jsonify(ok=False, pesan=msg), 400
            flash(msg, "error")
            return redirect(kembali(url_for("admin.master")))
        sets.append("stok_minimum = ?")
        args.append(x)
    if wk in ("0", "1"):
        sets.append("wajib_kembali = ?")
        args.append(int(wk))
    if expired in ("0", "1"):
        sets.append("expired = ?")
        args.append(int(expired))
    if expired_at is not None:
        sets.append("expired_at = ?")
        args.append(expired_at.strip() or None)
    if input_locked in ("0", "1"):
        sets.append("input_locked = ?")
        args.append(int(input_locked))
        if input_locked == "0" and input_lock_reason is None:
            sets.append("input_lock_reason = NULL")
    if input_lock_reason is not None:
        sets.append("input_lock_reason = ?")
        args.append(input_lock_reason.strip() or None)
    if sets:
        with tx(db):
            db.execute(f"UPDATE materials SET {', '.join(sets)}, updated_at = datetime('now','localtime') "
                       f"WHERE id IN ({','.join('?' * len(ids))})", args + ids)
            audit(db, g.user["id"], "atur_material", f"material {ids[:50]}: {', '.join(sets)}")
    if wants_json():
        return jsonify(ok=True)
    flash(f"{len(ids)} material diperbarui", "ok")
    return redirect(kembali(url_for("admin.master")))


@bp.route("/master/minimum", methods=["POST"])
@login_required("admin")
def master_minimum():
    """Isi / reset stok minimum massal: semua material, atau hanya kode berawalan tertentu (mis. 952)."""
    db = get_db()
    aksi = request.form.get("aksi")
    awalan = request.form.get("awalan", "").strip().rstrip("*%").strip()
    timpa = request.form.get("timpa") == "1"
    where, args = "1=1", []
    if awalan:
        where += " AND material LIKE ?"
        args.append(awalan + "%")
    if aksi == "reset":
        nilai, ket = 0.0, "direset ke 0"
    else:
        nilai = angka(request.form.get("nilai"))
        if nilai is None or nilai < 0:
            flash("Isi nilai stok minimum (angka ≥ 0)", "error")
            return redirect(url_for("admin.master") + "#minimum")
        ket = f"diisi {request.form.get('nilai').strip()}"
        if not timpa:
            where += " AND stok_minimum = 0"
    with tx(db):
        n = db.execute(f"UPDATE materials SET stok_minimum = ?, updated_at = datetime('now','localtime') "
                       f"WHERE {where}", [nilai] + args).rowcount
        sasaran = f"kode berawalan {awalan}" if awalan else "semua material"
        audit(db, g.user["id"], "stok_minimum_massal", f"{sasaran}: {n} material {ket}")
    flash(f"Stok minimum {ket} untuk {n} material ({sasaran}).", "ok")
    return redirect(url_for("admin.master", q=awalan or None) + "#minimum")


@bp.route("/master/grup", methods=["POST"])
@login_required("admin")
def master_grup():
    """Atur kategori atau minimum secara massal berdasarkan material group."""
    db = get_db()
    timpa = request.form.get("timpa") == "1"
    n = 0
    with tx(db):
        for key, val in request.form.items():
            if not key.startswith("grup_") or not val:
                continue
            grup = key[5:]
            n += db.execute(
                "UPDATE materials SET kategori_id = ? WHERE material_group = ?"
                + ("" if timpa else " AND kategori_id IS NULL"), (int(val), grup)).rowcount
    flash(f"Kategori diterapkan ke {n} material berdasarkan Material Group", "ok")
    return redirect(url_for("admin.master") + "#grup")


# ------------------------------------------------------------------ import transaksi
def periksa_import(db, rows, plant, auto_kebun):
    """Validasi seluruh baris import transaksi dan siapkan pratinjau atomik."""
    stok_jalan, kunci_file = {}, set()
    kebun_baru = {}
    sesi_opname = opname_aktif(db, plant)
    for r in rows:
        err, warn = [], []
        r["material_id"] = None
        if sesi_opname:
            err.append(f"Gudang {plant} sedang Stock Opname bersama (sesi #{sesi_opname['id']}); import dikunci")
        if not r["tanggal"]:
            err.append(f"Tanggal '{r['tanggal_raw']}' tidak valid (format dd/mm/yyyy)")
        elif r["tanggal"] > hari_ini():
            err.append("Tanggal di masa depan")
        jenis = r["jenis"]
        if not jenis:
            err.append(f"Jenis '{r['jenis_raw']}' tidak dikenal (MASUK / TRANSFER_MASUK / TRANSFER_KELUAR)")
        elif jenis == "keluar":
            err.append("KELUAR hanya lewat input HP (wajib foto bukti)")
        cand = db.execute("SELECT * FROM materials WHERE plant = ? AND material = ? "
                          "ORDER BY special_stock <> '', id", (plant, r["material"])).fetchall()
        m = None
        if not cand:
            err.append(f"Material {r['material']} tidak ada di master kebun {plant}. Jika barang baru, tambahkan "
                       f"dulu di Master SAP → Tambah material baru")
        else:
            m = cand[0]
            if len(cand) > 1:
                warn.append(f"Ada {len(cand)} varian special stock, dipakai {m['special_stock'] or 'stok biasa'}")
            r["material_id"] = m["id"]
            r["deskripsi"] = m["deskripsi"]
            r["satuan_master"] = m["satuan"]
            if m["expired"] or (m["expired_at"] and m["expired_at"] <= hari_ini()):
                err.append(f"Material {m['material']} sudah EXPIRED dan diblokir dari transaksi baru")
            if m["input_locked"]:
                alasan = m["input_lock_reason"] or "Kode material dikunci oleh admin; gunakan kode pengganti yang benar."
                err.append(f"Material {m['material']} dikunci dari input: {alasan}")
            if r["satuan"] and r["satuan"].upper() != (m["satuan"] or "").upper():
                warn.append(f"Satuan {r['satuan']} berbeda dengan master ({m['satuan']})")
        if r["qty"] is None:
            err.append(f"Quantity '{r['qty_raw']}' bukan angka")

        kebun_lain = None
        if jenis in ("transfer_masuk", "transfer_keluar"):
            raw = r["kebun_asal_raw"] if jenis == "transfer_masuk" else r["kebun_tujuan_raw"]
            kebun_lain = cari_kebun(db, raw)
            if not kebun_lain and raw:
                kode = re.sub(r"^KEBUN\s+", "", raw.strip().upper())[:30]
                if auto_kebun:
                    kebun_baru[kode] = raw.strip()
                    kebun_lain = kode
                    warn.append(f"Kebun '{raw}' akan ditambahkan ke daftar kebun")
                else:
                    err.append(f"Kebun '{raw}' belum terdaftar (tambah di menu Kebun atau centang tambah otomatis)")
            if jenis == "transfer_keluar" and r["kebun_asal_raw"]:
                asal = cari_kebun(db, r["kebun_asal_raw"])
                if asal and asal != plant:
                    warn.append(f"Kebun Asal '{r['kebun_asal_raw']}' diabaikan; dipakai kebun {plant}")
            if jenis == "transfer_keluar":
                warn.append("Foto bukti belum ada, lampirkan nanti di menu Transaksi")
        r["kebun_asal"] = kebun_lain if jenis == "transfer_masuk" else None
        r["kebun_tujuan"] = kebun_lain if jenis == "transfer_keluar" else None

        if m and jenis and r["qty"] is not None:
            if m["id"] not in stok_jalan:
                stok_jalan[m["id"]] = stok_material(db, m["id"])
            v_err = validasi(jenis, r["qty"], stok_jalan[m["id"]], r["nomor_dokumen"], kebun_asal=r["kebun_asal"],
                             kebun_tujuan=r["kebun_tujuan"], plant=plant, wajib_foto=False)
            if any("belum terdaftar" in x for x in err):  # jangan dobel pesan kebun
                v_err = [e for e in v_err if not e.startswith(("Kebun asal wajib", "Kebun tujuan wajib"))]
            err += v_err
            if r["nomor_dokumen"]:
                kunci = (jenis, m["id"], r["nomor_dokumen"].upper())
                if kunci in kunci_file:
                    err.append("Duplikat di file (jenis + material + nomor dokumen sama)")
                kunci_file.add(kunci)
                if db.execute("SELECT 1 FROM transactions WHERE batal = 0 AND jenis = ? AND material_id = ? "
                              "AND UPPER(nomor_dokumen) = ?", kunci).fetchone():
                    err.append(f"Dokumen {r['nomor_dokumen']} untuk material ini sudah pernah diinput")
            if not err:
                stok_jalan[m["id"]] = bulat(stok_jalan[m["id"]] + efek(jenis, r["qty"]))
                r["stok_sesudah"] = stok_jalan[m["id"]]
        r["errors"], r["warnings"] = err, warn
    return rows, kebun_baru


@bp.route("/import", methods=["GET", "POST"])
@login_required("admin")
def import_transaksi():
    """Terima file import dan tampilkan pratinjau sebelum disimpan."""
    db = get_db()
    kebun_master = db.execute("SELECT * FROM kebun WHERE kode IN (SELECT DISTINCT plant FROM materials) "
                              "ORDER BY nama").fetchall()
    if request.method == "GET":
        return render_template("import.html", kebun=kebun_master, rows=None)
    plant = request.form.get("plant", "")
    auto_kebun = request.form.get("auto_kebun") == "1"
    try:
        path = _simpan_upload(request.files.get("file"))
        try:
            rows = baca_transaksi(path)
        finally:
            path.unlink(missing_ok=True)
    except ExcelError as e:
        flash(str(e), "error")
        return redirect(url_for("admin.import_transaksi"))
    if not plant:
        flash("Pilih kebun pemilik stok untuk file ini", "error")
        return redirect(url_for("admin.import_transaksi"))
    rows, kebun_baru = periksa_import(db, rows, plant, auto_kebun)
    token = uuid.uuid4().hex
    nama_file = request.files["file"].filename
    (Path(current_app.config["TMP_FOLDER"]) / f"imp_{token}.json").write_text(json.dumps({
        "plant": plant, "auto_kebun": auto_kebun, "rows": rows, "file": nama_file, "uid": g.user["id"]}))
    n_err = sum(1 for r in rows if r["errors"])
    return render_template("import.html", kebun=kebun_master, rows=rows, token=token, n_err=n_err, plant=plant,
                           kebun_baru=kebun_baru, nama_file=nama_file, JENIS_LABEL=JENIS_LABEL)


@bp.route("/import/konfirmasi", methods=["POST"])
@login_required("admin")
def import_konfirmasi():
    """Simpan batch import yang sebelumnya lolos validasi pratinjau."""
    token = re.sub(r"[^0-9a-f]", "", request.form.get("token", ""))
    f = Path(current_app.config["TMP_FOLDER"]) / f"imp_{token}.json"
    if not token or not f.exists():
        flash("Data pratinjau sudah kedaluwarsa, upload ulang file", "error")
        return redirect(url_for("admin.import_transaksi"))
    data = json.loads(f.read_text())
    if data["uid"] != g.user["id"]:
        abort(403)
    db = get_db()
    ref = f"IMP-{dt.datetime.now():%Y%m%d%H%M%S}"
    try:
        with tx(db):
            for r in data["rows"]:  # reset hasil cek lama lalu periksa ulang di dalam lock
                r.pop("errors", None)
            rows, kebun_baru = periksa_import(db, data["rows"], data["plant"], data["auto_kebun"])
            if any(r["errors"] for r in rows):
                raise ValueError("Data berubah sejak pratinjau dan sekarang ada baris error. Upload ulang file.")
            for kode, nama in kebun_baru.items():
                db.execute("INSERT OR IGNORE INTO kebun (kode, nama) VALUES (?, ?)", (kode, nama))
            for r in rows:
                simpan_transaksi(db, {
                    "tanggal": r["tanggal"], "jenis": r["jenis"], "material_id": r["material_id"], "qty": r["qty"],
                    "nomor_dokumen": r["nomor_dokumen"], "penerima": r["penerima"],
                    "kebun_asal": r["kebun_asal"], "kebun_tujuan": r["kebun_tujuan"],
                    "keterangan": r["keterangan"], "sumber": "excel", "import_ref": ref,
                }, g.user["id"])
            audit(db, g.user["id"], "import_transaksi", f"{data['file']}: {len(rows)} baris, ref {ref}")
    except ValueError as e:
        flash(str(e), "error")
        return redirect(url_for("admin.import_transaksi"))
    f.unlink(missing_ok=True)
    flash(f"{len(rows)} transaksi dari {data['file']} berhasil diimport (ref {ref})", "ok")
    return redirect(url_for("main.transaksi", q=""))


# ------------------------------------------------------------------ rekonsiliasi SAP MB51
def _norm_cocok(value):
    """Normalisasi teks SAP untuk pencocokan transaksi yang toleran format."""
    return re.sub(r"[^A-Z0-9]+", "", (value or "").upper())


TRANSFER_MVT = {
    "301", "302", "303", "305", "309", "311", "312", "313", "315",
    "641", "642", "643", "644", "645", "646", "647", "648",
}

ISSUE_MVT = {"201", "202", "221", "222"}

# Movement pembatalan SAP. Pasangan dengan tanggal/material/plant/lokasi/qty yang
# sama dan tanda berlawanan tidak boleh ditawarkan sebagai transaksi baru.
REVERSAL_MVT = {"201": "202", "221": "222", "261": "262", "641": "642"}

# CM9A adalah kebun plasma yang menerima lalu langsung mengeluarkan barang.
# Karena saldo akhirnya selalu nol, material CM9A biasanya tidak muncul di MB52.
FLOW_THROUGH_PLANTS = {"CM9A"}


def _tandai_reversal(rows):
    """Tandai pasangan movement asli dan reversal yang saling meniadakan."""
    dipakai = set()
    for i, asli in enumerate(rows):
        reversal_mvt = REVERSAL_MVT.get((asli.get("movement_type") or "").strip())
        if not reversal_mvt or float(asli.get("qty") or 0) >= 0 or i in dipakai:
            continue
        kandidat = []
        for j, balik in enumerate(rows):
            if j in dipakai or j == i or balik.get("movement_type") != reversal_mvt:
                continue
            sama = (
                balik.get("tanggal") == asli.get("tanggal")
                and balik.get("plant") == asli.get("plant")
                and balik.get("material") == asli.get("material")
                and (balik.get("storage_location") or "") == (asli.get("storage_location") or "")
                and (balik.get("special_stock") or "") == (asli.get("special_stock") or "")
                and (balik.get("special_stock_no") or "") == (asli.get("special_stock_no") or "")
                and abs(float(balik.get("qty") or 0) + float(asli.get("qty") or 0)) < 0.0005
            )
            if sama:
                kandidat.append((j, balik))
        if len(kandidat) != 1:
            continue
        j, balik = kandidat[0]
        reversal_id = hashlib.sha256(
            f"{asli.get('sap_doc')}|{balik.get('sap_doc')}|{asli.get('material')}|"
            f"{asli.get('tanggal')}|{abs(float(asli.get('qty') or 0)):.3f}".encode("utf-8")
        ).hexdigest()[:20]
        asli.update(reversal_id=reversal_id, reversal_role="original",
                    reversal_pair_doc=balik.get("sap_doc"))
        balik.update(reversal_id=reversal_id, reversal_role="reversal",
                     reversal_pair_doc=asli.get("sap_doc"))
        dipakai.update((i, j))


def _kunci_material(row):
    """Bentuk identitas material SAP dari plant, material, dan special stock."""
    return (
        (row.get("plant") or "UMUM").upper(),
        (row.get("material") or "").upper(),
        (row.get("special_stock") or "").upper(),
        (row.get("special_stock_no") or "").upper(),
    )


def _blok_dari_teks_sap(db, plant, *texts):
    """Kenali tepat satu blok Supplementary dari catatan SAP suatu rantai transfer."""
    tokens = {_norm_cocok(token) for text in texts if text
              for token in re.findall(r"[A-Z0-9-]+", text.upper())}
    if not tokens:
        return None
    cocok = []
    for target in db.execute(
            """SELECT plant, code, sap_code, name, division, phase
               FROM supplementary_targets
               WHERE plant = ? AND target_type = 'blok' AND active = 1""",
            (plant,)).fetchall():
        if _norm_cocok(target["code"]) in tokens or _norm_cocok(target["sap_code"]) in tokens:
            cocok.append(dict(target))
    return cocok[0] if len(cocok) == 1 else None


def _tandai_pasangan_transfer(db, rows):
    """Hubungkan 641 keluar dengan 101 masuk berdasarkan PO, material, dan qty.

    Pasangan hanya menjadi saran. Operator tetap menentukan arah sebelum data disimpan.
    """
    keluar = [r for r in rows if r.get("movement_type") in {"641", "643", "645", "647"}
              and float(r.get("qty") or 0) < 0 and r.get("purchase_order")]
    masuk = [r for r in rows if r.get("movement_type") == "101"
             and float(r.get("qty") or 0) > 0 and r.get("purchase_order")]
    for kiri in keluar:
        kandidat = [kanan for kanan in masuk
                    if kanan.get("purchase_order") == kiri.get("purchase_order")
                    and (kanan.get("material") or "").upper() == (kiri.get("material") or "").upper()
                    and kanan.get("plant") != kiri.get("plant")
                    and abs(abs(float(kanan.get("qty") or 0)) - abs(float(kiri.get("qty") or 0))) < 0.0005]
        if len(kandidat) != 1:
            continue
        kanan = kandidat[0]
        kiri.update(transfer_pair=True, transfer_dari=kiri["plant"], transfer_ke=kanan["plant"],
                    transfer_saran="transfer_keluar", transfer_pihak_saran=kanan["plant"])
        kanan.update(transfer_pair=True, transfer_dari=kiri["plant"], transfer_ke=kanan["plant"],
                     transfer_saran="transfer_masuk", transfer_pihak_saran=kiri["plant"])

        # Plasma CM9A adalah alur langsung: 641 CM3A keluar, 101 CM9A masuk,
        # kemudian 221 CM9A habis ke blok. MvT 101 boleh diposting terlambat;
        # PO, material, dan qty mengikatnya ke 641, sedangkan 221 mengikuti tanggal fisik 641.
        # Bila bloknya dikenali dari catatan 101, jadikan satu rantai.
        # Hanya baris 641 yang memengaruhi stok aplikasi agar tidak terhitung tiga kali.
        if kanan["plant"] not in FLOW_THROUGH_PLANTS:
            continue
        pair_raw = (
            f"{kiri.get('sap_doc')}|{kanan.get('sap_doc')}|{kiri.get('purchase_order')}|"
            f"{kiri.get('material')}|{abs(float(kiri.get('qty') or 0)):.3f}"
        )
        pair_id = hashlib.sha256(pair_raw.encode("utf-8")).hexdigest()[:20]
        kiri.update(flow_pair_id=pair_id, flow_pair_role="source",
                    flow_pair_source_row=kiri.get("_baris"), flow_pair_receipt_row=kanan.get("_baris"))
        kanan.update(flow_pair_id=pair_id, flow_pair_role="receipt",
                     flow_pair_source_row=kiri.get("_baris"), flow_pair_receipt_row=kanan.get("_baris"))
        pemakaian = [r for r in rows if r.get("movement_type") in {"201", "221"}
                     and float(r.get("qty") or 0) < 0
                     and r.get("plant") == kanan.get("plant")
                     and r.get("tanggal") == kiri.get("tanggal")
                     and (r.get("material") or "").upper() == (kanan.get("material") or "").upper()
                     and abs(abs(float(r.get("qty") or 0)) - abs(float(kanan.get("qty") or 0))) < 0.0005]
        if len(pemakaian) != 1:
            continue
        akhir = pemakaian[0]
        target = _blok_dari_teks_sap(
            db, kanan["plant"], kanan.get("teks_sap"), akhir.get("teks_sap"),
            kanan.get("special_stock_no"), akhir.get("special_stock_no"),
        )
        if not target:
            continue
        chain_raw = (
            f"{kiri.get('sap_doc')}|{kanan.get('sap_doc')}|{akhir.get('sap_doc')}|"
            f"{kiri.get('material')}|{kiri.get('tanggal')}|{abs(float(kiri.get('qty') or 0)):.3f}"
        )
        chain_id = hashlib.sha256(chain_raw.encode("utf-8")).hexdigest()[:20]
        umum = {
            "flow_chain_id": chain_id,
            "flow_chain_source_row": kiri.get("_baris"),
            "flow_chain_target": target,
            "flow_chain_from": kiri["plant"],
            "flow_chain_via": kanan["plant"],
            "flow_chain_source_date": kiri.get("tanggal"),
            "flow_chain_receipt_date": kanan.get("tanggal"),
            "flow_chain_count": 3,
        }
        kiri.update(umum, flow_chain_role="source")
        kanan.update(umum, flow_chain_role="receipt")
        akhir.update(umum, flow_chain_role="issue")


def _review_key(row):
    """Identitas stabil satu baris MB51 untuk mencegah pemeriksaan transfer berulang."""
    nilai = [
        row.get("sap_doc"), row.get("tanggal"), row.get("material"), row.get("plant"),
        row.get("storage_location"), row.get("movement_type"), f"{float(row.get('qty') or 0):.3f}",
        row.get("special_stock"), row.get("special_stock_no"),
    ]
    mentah = "\x1f".join(str(x or "").strip().upper() for x in nilai)
    return hashlib.sha256(mentah.encode("utf-8")).hexdigest()


def _perlu_verifikasi_transfer(row):
    """TP/transit dan movement transfer tidak boleh diklasifikasikan otomatis."""
    sloc = (row.get("storage_location") or "").upper()
    mvt = (row.get("movement_type") or "").strip()
    return bool((sloc and sloc not in GUDANG_SLOC) or mvt in TRANSFER_MVT or row.get("transfer_pair"))


def _pilih_kandidat(candidates, row):
    """Pilih satu kandidat secara aman; teks MB51 memecahkan kandidat dengan qty sama."""
    if len(candidates) == 1:
        return candidates[0]
    sap_text = _norm_cocok(row.get("teks_sap"))
    if not sap_text:
        return None
    nilai = []
    for c in candidates:
        # Tujuan BON/unit adalah pembeda paling kuat untuk BBM/FAME ketika beberapa
        # kendaraan menerima qty sama pada tanggal yang sama (mis. DT07 dan DT32).
        fields = [_norm_cocok(c.get(k)) for k in
                  ("tujuan_kode", "keterangan", "penerima", "nomor_dokumen")]
        score = max((100 if sap_text == f else 70 if len(sap_text) >= 3 and (sap_text in f or f in sap_text)
                     else 0) for f in fields if f) if any(fields) else 0
        # Toleransi salah ketik kecil pada kode unit, mis. EX04 di SAP dan EXA04 di aplikasi.
        sap_parts = [_norm_cocok(x) for x in re.findall(r"[A-Z0-9]+", (row.get("teks_sap") or "").upper())]
        for a in sap_parts + [sap_text]:
            for f in fields:
                if len(a) >= 4 and len(f) >= 4:
                    rasio = SequenceMatcher(None, a, f).ratio()
                    if rasio >= 0.82:
                        score = max(score, int(rasio * 100))
        nilai.append((score, c))
    nilai.sort(key=lambda x: x[0], reverse=True)
    if nilai and nilai[0][0] >= 70 and (len(nilai) == 1 or nilai[0][0] > nilai[1][0]):
        return nilai[0][1]
    return None


def _jelaskan_pending(transaksi, rows):
    """Beri alasan ringkas mengapa transaksi aplikasi belum mendapat pasangan MB51."""
    material = (transaksi.get("material") or "").upper()
    desc = _norm_cocok(transaksi.get("deskripsi"))

    def material_cocok(row):
        """Periksa kecocokan kode atau deskripsi material baris SAP."""
        return ((row.get("material") or "").upper() == material or
                bool(desc) and _norm_cocok(row.get("deskripsi")) == desc)

    terkait = [r for r in rows if r.get("tanggal") == transaksi.get("tanggal")
               and r.get("plant") == transaksi.get("plant")
               and r.get("special_stock", "") == transaksi.get("special_stock", "")
               and material_cocok(r)]
    if not terkait:
        sekitar = [r for r in rows if r.get("plant") == transaksi.get("plant")
                   and r.get("special_stock", "") == transaksi.get("special_stock", "")
                   and material_cocok(r)]
        if sekitar:
            tanggal_sap = sorted({r["tanggal"] for r in sekitar})
            transaksi["alasan_tidak_cocok"] = (
                "Tidak ada baris SAP pada tanggal yang sama. Tanggal SAP untuk material ini: "
                + ", ".join(tanggal_sap[:4]) + ("…" if len(tanggal_sap) > 4 else "")
            )
        else:
            transaksi["alasan_tidak_cocok"] = (
                "Material ini tidak ditemukan di MB51 yang diunggah. Periksa rentang tanggal export SAP."
            )
        return

    efek_trx = float(transaksi.get("efek") or 0)
    qty_sama = [r for r in terkait if abs(float(r.get("qty") or 0) - efek_trx) < 0.0005]
    if qty_sama:
        status = {r.get("status") for r in qty_sama}
        if status & {"sudah", "ditinjau"}:
            alasan = "Ada qty SAP yang sama, tetapi barisnya sudah tersimpan atau sudah diputuskan untuk transaksi lain."
        else:
            alasan = "Ada qty SAP yang sama, tetapi pasangan belum aman ditentukan otomatis. Periksa penerima/keterangan."
    else:
        alasan = "Qty aplikasi tidak sama dengan baris SAP pada material dan tanggal ini."
    ringkas = []
    for r in terkait[:5]:
        ringkas.append(f"{r['qty']:g} (MvT {r.get('movement_type') or '-'}, dok. {r.get('sap_doc') or '-'})")
    transaksi["alasan_tidak_cocok"] = alasan
    transaksi["sap_terdekat"] = "; ".join(ringkas) + ("; …" if len(terkait) > 5 else "")


def _rekonsiliasi_mb51(db, rows, mulai=None):
    """Pasangkan baris MB51 dengan transaksi aplikasi dan kelompokkan hasilnya."""
    _tandai_reversal(rows)
    _tandai_pasangan_transfer(db, rows)
    trx = [dict(r) for r in db.execute(
        """SELECT t.*, m.material, m.plant, m.special_stock, m.special_stock_no, m.deskripsi, m.satuan,
                  (SELECT oi.sesi_id FROM opname_item oi WHERE oi.trx_id = t.id LIMIT 1) AS opname_sesi_id
           FROM transactions t JOIN materials m ON m.id = t.material_id
           WHERE t.batal = 0 AND t.sumber <> 'sinkron_sap'
           ORDER BY t.tanggal, t.id""").fetchall()]
    if mulai:
        trx = [t for t in trx if t["tanggal"] >= mulai]
    for t in trx:
        t["efek"] = bulat(efek(t["jenis"], t["qty"]))
        t["asal_label"] = (f"Stock Opname #{t['opname_sesi_id']}" if t["opname_sesi_id"]
                             else "Penyesuaian" if t["jenis"] == "penyesuaian" else JENIS_LABEL[t["jenis"]])

    review = {r["review_key"]: dict(r) for r in db.execute(
        "SELECT * FROM sap_movement_reviews ORDER BY id").fetchall()}
    rincian_sap = {}
    for detail in db.execute(
            """SELECT d.sap_doc, d.movement_type, d.posting_date, d.qty,
                      t.id AS transaction_id, m.material
               FROM transaction_sap_docs d
               JOIN transactions t ON t.id = d.transaction_id
               JOIN materials m ON m.id = t.material_id
               WHERE t.batal = 0""").fetchall():
        key = (detail["sap_doc"], detail["movement_type"], detail["posting_date"],
               round(float(detail["qty"]), 3), detail["material"].upper())
        rincian_sap.setdefault(key, []).append(detail["transaction_id"])
    digunakan = set()
    for row in rows:
        row["review_key"] = _review_key(row)
        row["perlu_verifikasi_transfer"] = _perlu_verifikasi_transfer(row)
        lama = review.get(row["review_key"])
        if lama:
            konflik = bool(row.get("transfer_pair") and lama.get("decision") != row.get("transfer_saran"))
            transaksi_lama = next((t for t in trx if t["id"] == lama.get("transaction_id")), None)
            row.update(status="verifikasi_ulang" if konflik else "ditinjau", review=lama,
                       transaction_id=lama.get("transaction_id"), transaksi=transaksi_lama)
            if lama.get("transaction_id"):
                digunakan.add(lama["transaction_id"])
            continue
        if row.get("reversal_id"):
            detail_key = (row.get("sap_doc"), row.get("movement_type"), row.get("tanggal"),
                          round(float(row.get("qty") or 0), 3), (row.get("material") or "").upper())
            tersimpan = list(dict.fromkeys(rincian_sap.get(detail_key, [])))
            if len(tersimpan) == 1:
                transaksi_lama = next((t for t in trx if t["id"] == tersimpan[0]), None)
                row.update(status="sudah", transaction_id=tersimpan[0], transaksi=transaksi_lama)
                digunakan.add(tersimpan[0])
            else:
                row.update(status="reversal", perlu_verifikasi_transfer=False)
            continue
        if row.get("flow_chain_role") in {"receipt", "issue"}:
            row.update(status="rantai", perlu_verifikasi_transfer=False)
            continue
        if row.get("flow_chain_role") == "source":
            row["perlu_verifikasi_transfer"] = False
        elif row.get("flow_pair_role") == "receipt":
            row.update(status="pasangan", perlu_verifikasi_transfer=False)
            continue

        detail_key = (row.get("sap_doc"), row.get("movement_type"), row.get("tanggal"),
                      round(float(row.get("qty") or 0), 3), (row.get("material") or "").upper())
        tersimpan = list(dict.fromkeys(rincian_sap.get(detail_key, [])))
        if len(tersimpan) == 1:
            transaksi_lama = next((t for t in trx if t["id"] == tersimpan[0]), None)
            row.update(status="sudah", transaction_id=tersimpan[0], transaksi=transaksi_lama)
            digunakan.add(tersimpan[0])
            continue
        dasar = [t for t in trx if t["id"] not in digunakan and t["plant"] == row["plant"]
                 and t["tanggal"] == row["tanggal"] and t["special_stock"] == row["special_stock"]
                 and (not row.get("special_stock_no") or t["special_stock_no"] == row["special_stock_no"])
                 and abs(t["efek"] - row["qty"]) < 0.0005]
        tepat = [t for t in dasar if t["material"].upper() == row["material"]]
        beda_kode = False
        if not tepat and row["deskripsi"]:
            desc = _norm_cocok(row["deskripsi"])
            tepat = [t for t in dasar if _norm_cocok(t["deskripsi"]) == desc]
            beda_kode = bool(tepat)

        # Pengeluaran untuk afdeling kadang diposting SAP langsung pada plant plasma/KKPA,
        # sementara operator HP masih memilih plant gudang inti. Pindah plant hanya ditawarkan
        # bila tanggal, material/deskripsi, qty, dan kandidatnya benar-benar tunggal.
        beda_plant = False
        if not tepat and row.get("movement_type") in ISSUE_MVT and row.get("qty", 0) < 0 \
                and not row.get("special_stock"):
            lintas = [t for t in trx if t["id"] not in digunakan and t["plant"] != row["plant"]
                      and t["tanggal"] == row["tanggal"] and not t["special_stock"]
                      and abs(t["efek"] - row["qty"]) < 0.0005
                      and (t["material"].upper() == row["material"]
                           or (row.get("deskripsi") and
                               _norm_cocok(t["deskripsi"]) == _norm_cocok(row["deskripsi"])))]
            if len(lintas) == 1:
                tepat = lintas
                beda_plant = True
                beda_kode = lintas[0]["material"].upper() != row["material"]

        sudah = [t for t in tepat if t["sap_posted"] and (t["sap_doc"] or "") == row["sap_doc"]]
        pilih = _pilih_kandidat(sudah, row)
        if pilih:
            row.update(status="sudah", transaction_id=pilih["id"], transaksi=pilih,
                       metode="deskripsi" if beda_kode else "material", beda_plant=beda_plant)
            digunakan.add(pilih["id"])
            continue

        terbuka = [t for t in tepat if
                   (not t["sap_posted"] and (not (t["sap_doc"] or "").strip() or t["sap_doc"] == row["sap_doc"]))
                   or (t["sap_posted"] and not (t["sap_doc"] or "").strip())]
        pilih = _pilih_kandidat(terbuka, row)
        if pilih:
            row.update(status="cocok", transaction_id=pilih["id"], transaksi=pilih,
                       metode="deskripsi" if beda_kode else "material", beda_plant=beda_plant)
            digunakan.add(pilih["id"])
        elif terbuka:
            row.update(status="ambigu", kandidat=len(terbuka))
        else:
            row.update(status="tidak_ada")

    # Satu transaksi aplikasi kadang dipecah menjadi beberapa dokumen SAP. Contoh nyata:
    # input 7.633 kg di aplikasi (maksud operator 7.633 = 7.633 kg) menjadi dua baris
    # MB51 2.384 + 5.249 kg. Gabungkan hanya bila kandidatnya tunggal dan totalnya pasti.
    kelompok = {}
    for row in rows:
        if row["status"] != "tidak_ada":
            continue
        k = (row["tanggal"], row["plant"], row["material"], row["special_stock"],
             row.get("special_stock_no") or "",
             _norm_cocok(row.get("deskripsi")))
        kelompok.setdefault(k, []).append(row)
    nomor_kelompok = 0
    for (_, plant, material, special_stock, special_stock_no, desc), anggota in kelompok.items():
        if len(anggota) < 2:
            continue
        total = bulat(sum(r["qty"] for r in anggota))
        kandidat = []
        for t in trx:
            if t["id"] in digunakan or t["tanggal"] != anggota[0]["tanggal"] or t["plant"] != plant \
                    or t["special_stock"] != special_stock \
                    or (special_stock_no and t["special_stock_no"] != special_stock_no):
                continue
            material_cocok = t["material"].upper() == material or (desc and _norm_cocok(t["deskripsi"]) == desc)
            if not material_cocok:
                continue
            e = t["efek"]
            if abs(e - total) < 0.0005 or (abs(e) < 100 and abs(bulat(e * 1000) - total) < 0.0005):
                kandidat.append(t)
        if len(kandidat) != 1:
            continue
        nomor_kelompok += 1
        pilih = kandidat[0]
        group_id = f"g{nomor_kelompok}"
        for pos, row in enumerate(anggota):
            row.update(status="gabung", transaction_id=pilih["id"], transaksi=pilih,
                       metode="deskripsi" if pilih["material"].upper() != material else "material",
                       group_id=group_id, group_lead=pos == 0, group_count=len(anggota),
                       group_qty=total, koreksi_qty=abs(total) if pilih["jenis"] != "penyesuaian" else total,
                       koreksi_skala=abs(pilih["efek"] - total) >= 0.0005)
        flow_sources = [r for r in anggota if r.get("flow_chain_role") == "source"
                        and r.get("flow_chain_target")]
        if len(flow_sources) == len(anggota):
            target_codes = list(dict.fromkeys(r["flow_chain_target"]["code"] for r in flow_sources))
            for row in anggota:
                row.update(flow_target_group=True, flow_split=len(target_codes) > 1,
                           flow_split_count=len(target_codes))
        digunakan.add(pilih["id"])

    pending = [t for t in trx if t["id"] not in digunakan and
               (not t["sap_posted"] or not (t["sap_doc"] or "").strip())]
    for t in pending:
        _jelaskan_pending(t, rows)
    return rows, pending


def _material_sap(db, row):
    """Pilih material SAP yang tepat; gunakan WBS/special-stock number bila tersedia."""
    sql = "SELECT * FROM materials WHERE plant = ? AND upper(material) = ? AND special_stock = ?"
    args = [row["plant"], row["material"].upper(), row["special_stock"]]
    if row.get("special_stock_no"):
        sql += " AND special_stock_no = ?"
        args.append(row["special_stock_no"])
    hasil = db.execute(sql + " ORDER BY id", args).fetchall()
    return dict(hasil[0]) if len(hasil) == 1 else None


def _ringkas_mb52(db, rows, sap_per):
    """Hubungkan baris MB52 ke material aplikasi dan siapkan kontrol saldo per material."""
    hasil, by_mid, by_key = [], {}, {}
    for r in rows:
        m = db.execute(
            """SELECT id FROM materials WHERE plant = ? AND material = ? AND special_stock = ?
               AND special_stock_no = ?""",
            (r["plant"] or "UMUM", r["material"], r["special_stock"], r["special_stock_no"])).fetchone()
        d = {k: r.get(k, "") for k in (
            "name1", "material_group", "material_type", "plant", "material", "deskripsi",
            "storage_location", "batch", "unrestricted", "satuan", "value_unrestricted",
            "special_stock", "special_stock_no", "stock_in_transit",
        )}
        d["material_id"] = m["id"] if m else None
        d["baru_master"] = not bool(m)
        hasil.append(d)
        by_key[_kunci_material(d)] = d
        if m:
            by_mid[m["id"]] = d
    return hasil, by_mid, by_key


def _kontrol_saldo(db, material_id, qty_mb52, sap_per):
    """Hitung penyesuaian pengontrol agar saldo akhir mengikuti snapshot MB52."""
    stok = stok_material(db, material_id)
    sinkron = db.execute(
        f"""SELECT COALESCE(SUM({EFEK_SQL}), 0) FROM transactions
            WHERE material_id = ? AND batal = 0 AND sumber = 'sinkron_sap'""",
        (material_id,)).fetchone()[0]
    pending = db.execute(
        f"""SELECT COALESCE(SUM({EFEK_SQL}), 0) FROM transactions
            WHERE material_id = ? AND batal = 0 AND sumber <> 'sinkron_sap'
              AND (sap_posted = 0 OR sap_posted_at > ?)""",
        (material_id, sap_per)).fetchone()[0]
    mentah = bulat(stok - sinkron)
    # Sama dengan target_sap(): stok aplikasi tidak boleh diproyeksikan negatif.
    target = max(bulat(qty_mb52 + pending), 0.0)
    perlu = bulat(target - mentah)
    return {"stok": stok, "sync": bulat(sinkron), "pending": bulat(pending),
            "raw": mentah, "target": target, "required": perlu}


def _siapkan_transaksi_baru(db, rows, mb52_by_mid, mb52_by_key, sap_per, same_day_included=False):
    """Tandai baris tanpa pasangan sebagai calon transaksi baru, lengkap dengan dampak saldonya."""
    for row in rows:
        row["tercakup_mb52"] = (row["tanggal"] < sap_per[:10] or
                                  (row["tanggal"] == sap_per[:10] and same_day_included))
        m = _material_sap(db, row)
        if m:
            row["sap_material_id"] = m["id"]
            row["sap_material"] = {k: m[k] for k in ("id", "material", "deskripsi", "satuan", "plant")}
        else:
            calon = mb52_by_key.get(_kunci_material(row))
            if calon:
                row["sap_material_baru"] = True
                row["sap_material"] = {
                    "id": None, "material": calon["material"], "deskripsi": calon["deskripsi"],
                    "satuan": calon["satuan"], "plant": calon["plant"],
                }
        if row["status"] in ("cocok", "gabung") and row.get("sap_material"):
            row["pindah_material"] = bool(row.get("sap_material_baru") or
                                           row["transaksi"]["material_id"] != row.get("sap_material_id"))
        if row.get("beda_plant") and not row.get("sap_material"):
            if (row.get("plant") or "").upper() in FLOW_THROUGH_PLANTS and row.get("transaksi"):
                row["flow_through_plant"] = True
            else:
                row["plant_tujuan_tidak_ada"] = True
        if row["status"] != "tidak_ada":
            continue
        if not m and not row.get("sap_material_baru"):
            if row.get("perlu_verifikasi_transfer"):
                row["status"] = "verifikasi"
            continue
        if not m:
            row.update(status="baru", kontrol=None, residual_after=None, aman=False)
            continue
        if m["id"] not in mb52_by_mid:
            # Tetap boleh diputuskan manual. Ini penting untuk TP07 yang belum muncul
            # pada saldo ST01; keputusan transit tidak membutuhkan transaksi stok.
            if row.get("perlu_verifikasi_transfer"):
                row.update(status="baru", kontrol=None, residual_after=None, aman=False)
            continue
        kontrol = _kontrol_saldo(db, m["id"], mb52_by_mid[m["id"]]["unrestricted"], sap_per)
        # Bila dokumen terjadi sesudah jam MB52, efek transaksi dan pending SAP bertambah bersama;
        # residual tidak berubah. Dokumen sebelum snapshot menjelaskan residual lama.
        sesudah = (bulat(kontrol["required"] - row["qty"])
                   if row["tercakup_mb52"] else kontrol["required"])
        row.update(status="baru", kontrol=kontrol, residual_after=sesudah,
                   aman=abs(sesudah) <= abs(kontrol["required"]) + EPS)
    return rows


@bp.route("/rekonsiliasi-sap", methods=["GET", "POST"])
@login_required("admin")
def rekonsiliasi_sap():
    """Terima MB51/MB52, validasi cakupan, lalu tampilkan hasil rekonsiliasi."""
    db = get_db()
    awal_trx = db.execute(
        "SELECT MIN(tanggal) AS awal FROM transactions WHERE batal = 0 AND sumber <> 'sinkron_sap'"
    ).fetchone()["awal"]
    mulai_default = awal_trx or hari_ini()
    if request.method == "GET":
        return render_template("rekonsiliasi_sap.html", rows=None,
                               mulai_default=mulai_default,
                               now_local=dt.datetime.now().strftime("%Y-%m-%dT%H:%M"))
    path51 = None
    try:
        file51 = request.files.get("mb51") or request.files.get("file")
        path51 = _simpan_upload(file51)
        try:
            rows, dilewati = baca_mb51(path51)
        finally:
            path51.unlink(missing_ok=True)
        files52 = [f for f in request.files.getlist("mb52") if f and f.filename]
        rows52, peringatan52 = ([], [])
        nama_files52 = []
        for file52 in files52:
            path52 = _simpan_upload(file52)
            try:
                bagian, warning = baca_master_sap(path52)
                rows52.extend(bagian)
                peringatan52.extend(f"{file52.filename}: {p}" for p in warning)
                nama_files52.append(file52.filename)
            finally:
                path52.unlink(missing_ok=True)
    except ExcelError as e:
        flash(str(e), "error")
        return redirect(url_for("admin.rekonsiliasi_sap"))

    mulai = request.form.get("mulai_rekonsiliasi") or awal_trx or min(r["tanggal"] for r in rows)
    try:
        mulai = dt.date.fromisoformat(mulai).isoformat()
    except ValueError:
        mulai = mulai_default
    diabaikan_awal = sum(r["tanggal"] < mulai for r in rows)
    rows = [r for r in rows if r["tanggal"] >= mulai]
    if not rows:
        flash(f"Tidak ada baris MB51 pada atau setelah {mulai}. Data sebelum tanggal mulai diabaikan.", "error")
        return redirect(url_for("admin.rekonsiliasi_sap"))
    rows, pending = _rekonsiliasi_mb51(db, rows, mulai)
    try:
        sap_per = dt.datetime.fromisoformat(request.form.get("sap_per", "")).strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        sap_per = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    mb52_data, mb52_by_mid, mb52_by_key = (_ringkas_mb52(db, rows52, sap_per)
                                            if rows52 else ([], {}, {}))
    same_day_included = request.form.get("same_day_included") == "1"
    rows = _siapkan_transaksi_baru(db, rows, mb52_by_mid, mb52_by_key, sap_per, same_day_included)
    token = uuid.uuid4().hex
    nama_file = file51.filename
    stats = {s: sum(r["status"] == s for r in rows)
             for s in ("cocok", "sudah", "ditinjau", "verifikasi", "verifikasi_ulang",
                       "ambigu", "tidak_ada", "baru", "pasangan", "reversal")}
    stats["gabung"] = sum(r["status"] == "gabung" and r.get("group_lead") for r in rows)
    stats["baru_aman"] = sum(r["status"] == "baru" and r.get("aman") for r in rows)
    preview = {
        "rows": rows, "pending": pending, "stats": stats, "dilewati": dilewati,
        "file": nama_file, "file52": ", ".join(nama_files52) if nama_files52 else None,
        "mb52": mb52_data, "sap_per": sap_per, "same_day_included": same_day_included,
        "mulai": mulai, "diabaikan_awal": diabaikan_awal, "uid": g.user["id"],
    }
    (Path(current_app.config["TMP_FOLDER"]) / f"mb51_{token}.json").write_text(
        json.dumps(preview), encoding="utf-8")
    for p in peringatan52:
        flash("MB52: " + p, "warn")
    return render_template("rekonsiliasi_sap.html", rows=rows, pending=pending, token=token, stats=stats,
                           dilewati=dilewati, nama_file=nama_file,
                           nama_file52=", ".join(nama_files52) if nama_files52 else None,
                           sap_per=sap_per, mulai=mulai, mulai_default=mulai_default,
                           diabaikan_awal=diabaikan_awal,
                           now_local=dt.datetime.now().strftime("%Y-%m-%dT%H:%M"))


@bp.route("/rekonsiliasi-sap/konfirmasi", methods=["POST"])
@login_required("admin")
def rekonsiliasi_sap_konfirmasi():
    """Terapkan keputusan operator atas hasil rekonsiliasi dalam satu transaksi."""
    token = re.sub(r"[^0-9a-f]", "", request.form.get("token", ""))
    f = Path(current_app.config["TMP_FOLDER"]) / f"mb51_{token}.json"
    if not token or not f.exists():
        flash("Data pratinjau sudah kedaluwarsa, upload ulang file MB51", "error")
        return redirect(url_for("admin.rekonsiliasi_sap"))
    data = json.loads(f.read_text(encoding="utf-8"))
    if data["uid"] != g.user["id"]:
        abort(403)
    dipilih = {int(i) for i in request.form.getlist("matches") + request.form.getlist("news")
               + request.form.getlist("reviews") if i.isdigit()}
    if not dipilih:
        flash("Tidak ada pasangan atau transaksi baru yang dipilih", "error")
        return redirect(url_for("admin.rekonsiliasi_sap"))

    keputusan = {}
    error_keputusan = []
    for i in sorted(dipilih):
        if i < 0 or i >= len(data["rows"]):
            continue
        row = data["rows"][i]
        if (row.get("beda_plant") and not row.get("sap_material_id")
                and not row.get("sap_material_baru") and not row.get("flow_through_plant")):
            error_keputusan.append(
                f"baris {row['_baris']}: upload MB52 plant {row['plant']} sebelum memindahkan transaksi"
            )
            continue
        if not row.get("perlu_verifikasi_transfer"):
            continue
        arah = request.form.get(f"arah_{i}", "").strip()
        pihak = request.form.get(f"pihak_{i}", "").strip()
        diizinkan = {"masuk_store", "keluar_store", "transfer_masuk", "transfer_keluar"}
        if row.get("status") in ("baru", "verifikasi"):
            diizinkan.add("transit")
        if row.get("status") == "verifikasi":
            diizinkan = {"transit"}
        if arah not in diizinkan:
            error_keputusan.append(f"baris {row['_baris']}: pilih arah pergerakan")
            continue
        if arah in ("masuk_store", "transfer_masuk") and row["qty"] <= 0:
            error_keputusan.append(f"baris {row['_baris']}: qty SAP negatif tidak dapat dipilih sebagai barang masuk")
        elif arah in ("keluar_store", "transfer_keluar") and row["qty"] >= 0:
            error_keputusan.append(f"baris {row['_baris']}: qty SAP positif tidak dapat dipilih sebagai barang keluar")
        elif arah in ("transfer_masuk", "transfer_keluar") and not pihak:
            error_keputusan.append(f"baris {row['_baris']}: isi kebun asal/tujuan")
        else:
            keputusan[i] = (arah, pihak)
    if error_keputusan:
        flash("Verifikasi transfer belum lengkap: " + " · ".join(error_keputusan), "error")
        return render_template(
            "rekonsiliasi_sap.html", rows=data["rows"], pending=data.get("pending", []), token=token,
            stats=data.get("stats", {}), dilewati=data.get("dilewati", 0), nama_file=data["file"],
            nama_file52=data.get("file52"), sap_per=data.get("sap_per"),
            mulai=data.get("mulai"), diabaikan_awal=data.get("diabaikan_awal", 0),
            now_local=dt.datetime.now().strftime("%Y-%m-%dT%H:%M")), 400

    db = get_db()
    berhasil = penyesuaian = baru = dikoreksi = ditinjau = master_baru = dibagi_tujuan = 0
    sap_per = data.get("sap_per") or dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    same_day_included = bool(data.get("same_day_included"))
    mb52_by_mid = {int(r["material_id"]): r for r in data.get("mb52", []) if r.get("material_id")}
    terpengaruh = set()

    def simpan_rincian(tid, rincian):
        """Simpan semua dokumen SAP yang membentuk satu transaksi aplikasi."""
        for r in rincian:
            db.execute(
                """INSERT OR IGNORE INTO transaction_sap_docs
                   (transaction_id, sap_doc, movement_type, posting_date, qty, teks_sap)
                   VALUES (?,?,?,?,?,?)""",
                (tid, r["sap_doc"], r["movement_type"], r["tanggal"], r["qty"], r.get("teks_sap")))

    def tandai_sap(tid, rincian):
        """Tandai transaksi telah diposting dan hubungkan dokumen SAP-nya."""
        docs = list(dict.fromkeys(r["sap_doc"] for r in rincian))
        mvts = list(dict.fromkeys(r["movement_type"] for r in rincian))
        tgls = list(dict.fromkeys(r["tanggal"] for r in rincian))
        if mb52_by_mid:
            tercakup = all(t < sap_per[:10] or (t == sap_per[:10] and same_day_included) for t in tgls)
            waktu_posting = (sap_per if tercakup else
                              (dt.datetime.fromisoformat(sap_per) + dt.timedelta(seconds=1)).strftime("%Y-%m-%d %H:%M:%S"))
        else:
            waktu_posting = max(tgls) + " 23:59:59"
        db.execute(
            """UPDATE transactions SET sap_posted = 1, sap_posted_at = ?,
                   sap_posted_by = COALESCE(sap_posted_by, ?), sap_doc = ?,
                   sap_movement_type = ?, sap_posting_date = ? WHERE id = ?""",
            (waktu_posting, g.user["id"], ", ".join(docs), ", ".join(mvts), max(tgls), tid))
        simpan_rincian(tid, rincian)

    def rincian_terkait(row):
        """Ambil seluruh dokumen SAP bila baris merupakan kepala rantai plasma."""
        chain_id = row.get("flow_chain_id")
        if chain_id:
            return [r for r in data["rows"] if r.get("flow_chain_id") == chain_id]
        pair_id = row.get("flow_pair_id")
        if pair_id:
            return [r for r in data["rows"] if r.get("flow_pair_id") == pair_id]
        return [row]

    def simpan_review(rincian, arah, pihak, tid=None):
        """Simpan keputusan manual arah transfer agar tidak diminta berulang."""
        for r in rincian:
            db.execute(
                """INSERT INTO sap_movement_reviews
                   (review_key, sap_doc, posting_date, material, plant, storage_location,
                    movement_type, qty, special_stock, special_stock_no, decision, pihak,
                    transaction_id, source_file, reviewed_by)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(review_key) DO UPDATE SET decision=excluded.decision, pihak=excluded.pihak,
                       transaction_id=COALESCE(excluded.transaction_id, sap_movement_reviews.transaction_id),
                       source_file=excluded.source_file, reviewed_by=excluded.reviewed_by,
                       reviewed_at=datetime('now','localtime')""",
                (r.get("review_key") or _review_key(r), r["sap_doc"], r["tanggal"], r["material"],
                 r["plant"], r.get("storage_location") or "", r["movement_type"], r["qty"],
                 r.get("special_stock") or "", r.get("special_stock_no") or "", arah, pihak or None,
                 tid, data["file"], g.user["id"]))

    def simpan_review_rincian(rincian, arah, pihak, tid=None):
        """Simpan keputusan sumber dan keputusan kebalikan pada receipt flow-through."""
        for r in rincian:
            if r.get("flow_pair_role") == "receipt":
                simpan_review([r], r.get("transfer_saran") or "transfer_masuk",
                              r.get("transfer_pihak_saran") or pihak, tid)
            else:
                simpan_review([r], arah, pihak, tid)

    def ubah_arah_transaksi(tid, row, arah, pihak):
        """Terapkan keputusan arah transfer pada transaksi yang sudah ada."""
        jenis = {
            "masuk_store": "masuk", "keluar_store": "keluar",
            "transfer_masuk": "transfer_masuk", "transfer_keluar": "transfer_keluar",
        }[arah]
        asal = pihak if arah == "transfer_masuk" else (row["plant"] if arah == "transfer_keluar" else None)
        tujuan = row["plant"] if arah == "transfer_masuk" else (pihak if arah == "transfer_keluar" else None)
        db.execute(
            """UPDATE transactions SET jenis = ?, kebun_asal = ?, kebun_tujuan = ?,
                   penerima = CASE WHEN ? = 'transfer_keluar' AND COALESCE(penerima, '') = '' THEN ? ELSE penerima END,
                   updated_by = ?, updated_at = datetime('now','localtime') WHERE id = ?""",
            (jenis, asal, tujuan, arah, pihak, g.user["id"], tid))

    with tx(db):
        # MB52 lintas plant sekaligus melengkapi master kebun/material yang belum dikenal.
        # Tanggal mulai aplikasi menjadi batas saldo awal; riwayat lebih lama tidak dibuat sebagai transaksi.
        for r in data.get("mb52", []):
            if not r.get("material_id"):
                plant = r.get("plant") or "UMUM"
                if not db.execute("SELECT 1 FROM kebun WHERE kode = ?", (plant,)).fetchone():
                    db.execute("INSERT INTO kebun (kode, nama) VALUES (?, ?)",
                               (plant, r.get("name1") or plant))
                ada = db.execute(
                    """SELECT id FROM materials WHERE plant = ? AND material = ?
                       AND special_stock = ? AND special_stock_no = ?""",
                    (plant, r["material"], r.get("special_stock") or "",
                     r.get("special_stock_no") or "")).fetchone()
                if ada:
                    r["material_id"] = ada["id"]
                else:
                    attrs = ["name1", "material_group", "deskripsi", "storage_location", "batch", "satuan",
                             "value_unrestricted", "stock_in_transit", "material_type"]
                    kategori = db.execute(
                        "SELECT kategori_id FROM materials WHERE material = ? AND kategori_id IS NOT NULL LIMIT 1",
                        (r["material"],)).fetchone()
                    cur = db.execute(
                        f"""INSERT INTO materials (plant, material, special_stock, special_stock_no,
                               {', '.join(attrs)}, stok_awal, tgl_stok_awal, sap_unrestricted, sap_updated_at,
                               kategori_id)
                           VALUES (?,?,?,?,{', '.join('?' * len(attrs))},?,?,?,?,?)""",
                        [plant, r["material"], r.get("special_stock") or "", r.get("special_stock_no") or ""]
                        + [r.get(a, "") for a in attrs]
                        + [r["unrestricted"], data.get("mulai") or sap_per[:10],
                           r["unrestricted"], sap_per, kategori["kategori_id"] if kategori else None])
                    r["material_id"] = cur.lastrowid
                    master_baru += 1
            db.execute("UPDATE materials SET sap_unrestricted = ?, sap_updated_at = ?, ada_di_sap = 1 "
                       "WHERE id = ?", (r["unrestricted"], sap_per, r["material_id"]))
            mb52_by_mid[int(r["material_id"])] = r

        material_ids = {_kunci_material(r): int(r["material_id"]) for r in data.get("mb52", [])
                        if r.get("material_id")}
        for row in data["rows"]:
            if not row.get("sap_material_id"):
                row["sap_material_id"] = material_ids.get(_kunci_material(row))

        for i, row in enumerate(data["rows"]):
            if i not in dipilih:
                continue
            if row.get("status") == "verifikasi_ulang":
                arah_pihak = keputusan.get(i)
                if not arah_pihak:
                    continue
                tid = row.get("transaction_id")
                if tid:
                    ubah_arah_transaksi(tid, row, arah_pihak[0], arah_pihak[1])
                    tandai_sap(tid, rincian_terkait(row))
                    terpengaruh.add(db.execute(
                        "SELECT material_id FROM transactions WHERE id = ?", (tid,)).fetchone()["material_id"])
                simpan_review_rincian(rincian_terkait(row), arah_pihak[0], arah_pihak[1], tid)
                ditinjau += 1
                continue
            if row.get("status") == "verifikasi":
                arah_pihak = keputusan.get(i)
                if arah_pihak and arah_pihak[0] == "transit":
                    simpan_review([row], arah_pihak[0], arah_pihak[1])
                    ditinjau += 1
                continue
            if row.get("status") == "baru":
                arah_pihak = keputusan.get(i)
                if arah_pihak and arah_pihak[0] == "transit":
                    simpan_review([row], arah_pihak[0], arah_pihak[1])
                    ditinjau += 1
                    continue
                mid = row.get("sap_material_id")
                if not mid or (mid not in mb52_by_mid and not row.get("perlu_verifikasi_transfer")):
                    continue
                duplikat = db.execute(
                    """SELECT 1 FROM transactions t JOIN transaction_sap_docs d ON d.transaction_id = t.id
                       WHERE t.batal = 0 AND d.sap_doc = ? AND t.material_id = ?
                         AND d.movement_type = ? AND abs(d.qty - ?) < 0.0005""",
                    (row["sap_doc"], mid, row["movement_type"], row["qty"])).fetchone()
                if duplikat:
                    continue
                if arah_pihak:
                    jenis = {"masuk_store": "masuk", "keluar_store": "keluar",
                             "transfer_masuk": "transfer_masuk",
                             "transfer_keluar": "transfer_keluar"}[arah_pihak[0]]
                else:
                    jenis = ("penyesuaian" if row["movement_type"] in ("701", "702")
                             else ("masuk" if row["qty"] > 0 else "keluar"))
                qty = row["qty"] if jenis == "penyesuaian" else abs(row["qty"])
                pihak = arah_pihak[1] if arah_pihak else ""
                target_flow = row.get("flow_chain_target") or {}
                if target_flow:
                    keterangan = (
                        f"Import MB51 rantai plasma {row.get('flow_chain_from')} → "
                        f"{row.get('flow_chain_via')} → Blok {target_flow.get('code')}"
                    )
                else:
                    keterangan = "Import MB51: " + (
                        row.get("teks_sap") or row.get("deskripsi") or row["movement_type"]
                    )
                tid = simpan_transaksi(db, {
                    "tanggal": row["tanggal"], "jenis": jenis, "material_id": mid, "qty": qty,
                    "nomor_dokumen": row["sap_doc"], "keterangan": keterangan,
                    "penerima": pihak if jenis == "transfer_keluar" else None,
                    "kebun_asal": pihak if jenis == "transfer_masuk" else None,
                    "kebun_tujuan": pihak if jenis == "transfer_keluar" else None,
                    "tujuan_tipe": "blok" if target_flow else None,
                    "tujuan_kode": target_flow.get("code") if target_flow else None,
                    "sumber": "sap_mb51", "import_ref": data["file"],
                }, g.user["id"])
                tandai_sap(tid, rincian_terkait(row))
                if arah_pihak:
                    simpan_review_rincian(rincian_terkait(row), arah_pihak[0], pihak, tid)
                    ditinjau += 1
                terpengaruh.add(mid)
                baru += 1
                continue
            if row.get("status") == "gabung":
                if not row.get("group_lead"):
                    continue
                rincian = [x for x in data["rows"] if x.get("group_id") == row.get("group_id")]
                t = db.execute("SELECT * FROM transactions WHERE id = ?", (row["transaction_id"],)).fetchone()
                if not t or t["batal"] or t["sumber"] == "sinkron_sap":
                    continue
                old_mid = t["material_id"]
                new_mid = row.get("sap_material_id") or old_mid
                if row.get("flow_target_group"):
                    kelompok_tujuan = {}
                    urutan_tujuan = []
                    for sumber in rincian:
                        target = sumber.get("flow_chain_target") or {}
                        kode = target.get("code")
                        if not kode:
                            continue
                        if kode not in kelompok_tujuan:
                            kelompok_tujuan[kode] = {"target": target, "sources": []}
                            urutan_tujuan.append(kode)
                        kelompok_tujuan[kode]["sources"].append(sumber)
                    if not kelompok_tujuan or t["jenis"] != "keluar":
                        continue
                    for posisi, kode in enumerate(urutan_tujuan):
                        bagian = kelompok_tujuan[kode]
                        qty_bagian = bulat(sum(abs(float(s["qty"])) for s in bagian["sources"]))
                        dokumen_bagian = []
                        sudah_detail = set()
                        for sumber in bagian["sources"]:
                            for detail in rincian_terkait(sumber):
                                key = (detail["sap_doc"], detail["movement_type"], detail["qty"])
                                if key not in sudah_detail:
                                    dokumen_bagian.append(detail)
                                    sudah_detail.add(key)
                        if posisi == 0:
                            tid = t["id"]
                            db.execute(
                                """UPDATE transactions
                                   SET qty = ?, material_id = ?, tujuan_tipe = 'blok', tujuan_kode = ?,
                                       aktivitas_kode = NULL, updated_by = ?,
                                       updated_at = datetime('now','localtime')
                                   WHERE id = ?""",
                                (qty_bagian, new_mid, kode, g.user["id"], tid),
                            )
                        else:
                            tid = simpan_transaksi(db, {
                                "tanggal": t["tanggal"], "jenis": t["jenis"],
                                "material_id": new_mid, "qty": qty_bagian,
                                "nomor_dokumen": bagian["sources"][0]["sap_doc"],
                                "penerima": t["penerima"], "kebun_asal": t["kebun_asal"],
                                "kebun_tujuan": t["kebun_tujuan"], "keterangan": t["keterangan"],
                                "alasan": t["alasan"], "foto": t["foto"], "foto_2": t["foto_2"],
                                "sumber": t["sumber"], "import_ref": t["import_ref"], "bon": t["bon"],
                                "tujuan_tipe": "blok", "tujuan_kode": kode,
                            }, g.user["id"])
                            db.execute(
                                """UPDATE transactions SET created_by = ?, created_at = ?, updated_by = ?,
                                       updated_at = datetime('now','localtime') WHERE id = ?""",
                                (t["created_by"], t["created_at"], g.user["id"], tid),
                            )
                        tandai_sap(tid, dokumen_bagian)
                        berhasil += 1
                    terpengaruh.update((old_mid, new_mid))
                    dibagi_tujuan += bool(len(urutan_tujuan) > 1)
                    continue
                db.execute("UPDATE transactions SET qty = ?, material_id = ?, updated_by = ?, "
                           "updated_at = datetime('now','localtime') WHERE id = ?",
                           (row["koreksi_qty"], new_mid, g.user["id"], t["id"]))
                arah_pihak = keputusan.get(i)
                if arah_pihak:
                    ubah_arah_transaksi(t["id"], row, arah_pihak[0], arah_pihak[1])
                    simpan_review_rincian(rincian, arah_pihak[0], arah_pihak[1], t["id"])
                    ditinjau += len(rincian)
                tandai_sap(t["id"], rincian)
                terpengaruh.update((old_mid, new_mid))
                berhasil += 1
                dikoreksi += bool(row.get("koreksi_skala"))
                continue
            if row.get("status") != "cocok":
                continue
            t = db.execute(
                """SELECT t.*, m.material, m.plant, m.special_stock, m.deskripsi
                   FROM transactions t JOIN materials m ON m.id = t.material_id WHERE t.id = ?""",
                (row["transaction_id"],)).fetchone()
            if not t or t["batal"] or t["sumber"] == "sinkron_sap":
                continue
            cocok_material = (t["material"].upper() == row["material"] or
                              bool(row["deskripsi"]) and
                              _norm_cocok(t["deskripsi"]) == _norm_cocok(row["deskripsi"]))
            if (t["tanggal"] != row["tanggal"] or
                    (t["plant"] != row["plant"] and not row.get("beda_plant")) or
                    t["special_stock"] != row["special_stock"] or not cocok_material or
                    abs(bulat(efek(t["jenis"], t["qty"])) - row["qty"]) >= 0.0005 or
                    (t["sap_doc"] and t["sap_doc"] != row["sap_doc"])):
                continue
            old_mid = t["material_id"]
            new_mid = row.get("sap_material_id") or old_mid
            if new_mid != old_mid:
                db.execute("UPDATE transactions SET material_id = ?, updated_by = ?, "
                           "updated_at = datetime('now','localtime') WHERE id = ?",
                           (new_mid, g.user["id"], t["id"]))
            arah_pihak = keputusan.get(i)
            if arah_pihak:
                ubah_arah_transaksi(t["id"], row, arah_pihak[0], arah_pihak[1])
                simpan_review_rincian(rincian_terkait(row), arah_pihak[0], arah_pihak[1], t["id"])
                ditinjau += 1
            tandai_sap(t["id"], rincian_terkait(row))
            terpengaruh.update((old_mid, new_mid))
            berhasil += 1
            penyesuaian += t["jenis"] == "penyesuaian"
        # Ganti penyesuaian sinkron lama dengan residual baru. Transaksi MB51 tetap terlihat,
        # sedangkan saldo akhir selalu kembali ke MB52 + transaksi yang memang belum tercermin di MB52.
        if mb52_by_mid:
            for mid in sorted(terpengaruh):
                if mid not in mb52_by_mid:
                    continue
                db.execute(
                    """UPDATE transactions SET batal = 1,
                           batal_alasan = 'Direklasifikasi oleh rekonsiliasi MB51 + MB52',
                           updated_by = ?, updated_at = datetime('now','localtime')
                       WHERE material_id = ? AND batal = 0 AND sumber = 'sinkron_sap'""",
                    (g.user["id"], mid))
                target = target_sap(db, mid)
                if target is not None and abs(stok_material(db, mid) - target) > EPS:
                    sesuaikan_stok(db, mid, target, ALASAN_SAP,
                                   f"Residual rekonsiliasi {data['file']} + {data.get('file52')}",
                                   sap_per[:10], g.user["id"])
        audit(db, g.user["id"], "rekonsiliasi_mb51",
              f"{data['file']} + {data.get('file52')}: {berhasil} transaksi cocok, {baru} transaksi baru, "
              f"{dikoreksi} qty dikoreksi; {dibagi_tujuan} transaksi dibagi per tujuan; "
              f"{penyesuaian} penyesuaian/opname; "
              f"{ditinjau} pergerakan transfer ditinjau; {master_baru} material master baru; "
              f"mulai {data.get('mulai')}, {data.get('diabaikan_awal', 0)} baris lama diabaikan")
    f.unlink(missing_ok=True)
    flash(f"Rekonsiliasi MB51 selesai: {berhasil} transaksi diberi nomor dokumen SAP, {baru} transaksi baru ditambahkan"
          + (f", {dikoreksi} quantity salah format dikoreksi" if dikoreksi else "")
          + (f", {dibagi_tujuan} transaksi dibagi mengikuti blok plasma" if dibagi_tujuan else "")
          + (f", {ditinjau} pergerakan transfer diverifikasi" if ditinjau else "")
          + (f", {master_baru} material plant baru dibuat dari MB52" if master_baru else "")
          + (f", termasuk {penyesuaian} penyesuaian/Stock Opname." if penyesuaian else "."), "ok")
    return redirect(url_for("main.transaksi", status="sudah_posting"))


# ------------------------------------------------------------------ kebun & kategori
@bp.route("/kebun", methods=["GET", "POST"])
@login_required("admin")
def kebun():
    """Kelola daftar kebun untuk pembatasan akses dan arah transfer."""
    db = get_db()
    if request.method == "POST":
        kode = request.form.get("kode", "").strip().upper()
        nama = request.form.get("nama", "").strip()
        aktif = 1 if request.form.get("aktif", "1") == "1" else 0
        if not kode or not nama:
            flash("Kode dan nama kebun wajib diisi", "error")
        else:
            with tx(db):
                if request.form.get("edit"):
                    db.execute("UPDATE kebun SET nama = ?, aktif = ? WHERE kode = ?", (nama, aktif, kode))
                elif db.execute("SELECT 1 FROM kebun WHERE kode = ?", (kode,)).fetchone():
                    flash(f"Kode {kode} sudah ada", "error")
                    return redirect(url_for("admin.kebun"))
                else:
                    db.execute("INSERT INTO kebun (kode, nama, aktif) VALUES (?,?,1)", (kode, nama))
            flash(f"Kebun {kode} disimpan", "ok")
        return redirect(url_for("admin.kebun"))
    rows = db.execute("""SELECT kb.*, (SELECT COUNT(*) FROM materials m WHERE m.plant = kb.kode) AS material
                         FROM kebun kb ORDER BY kb.aktif DESC, kb.nama""").fetchall()
    return render_template("kebun.html", rows=rows)


@bp.route("/kategori", methods=["GET", "POST"])
@login_required("admin")
def kategori():
    """Kelola kategori material dan urutan tampilnya."""
    db = get_db()
    if request.method == "POST":
        aksi = request.form.get("aksi")
        nama = request.form.get("nama", "").strip()
        kid = request.form.get("id", type=int)
        with tx(db):
            if aksi == "tambah" and nama:
                if db.execute("SELECT 1 FROM kategori WHERE nama = ?", (nama,)).fetchone():
                    flash("Kategori sudah ada", "error")
                else:
                    db.execute("INSERT INTO kategori (nama, urutan) VALUES (?, (SELECT COALESCE(MAX(urutan),0)+1 "
                               "FROM kategori))", (nama,))
            elif aksi == "ubah" and kid and nama:
                db.execute("UPDATE kategori SET nama = ?, urutan = ? WHERE id = ?",
                           (nama, request.form.get("urutan", type=int) or 0, kid))
            elif aksi == "hapus" and kid:
                n = db.execute("SELECT COUNT(*) FROM materials WHERE kategori_id = ?", (kid,)).fetchone()[0]
                if n:
                    flash(f"Kategori masih dipakai {n} material", "error")
                else:
                    db.execute("DELETE FROM kategori WHERE id = ?", (kid,))
        return redirect(url_for("admin.kategori"))
    rows = db.execute("""SELECT k.*, (SELECT COUNT(*) FROM materials m WHERE m.kategori_id = k.id) AS material
                         FROM kategori k ORDER BY k.urutan, k.nama""").fetchall()
    return render_template("kategori.html", rows=rows)


# ------------------------------------------------------------------ pengguna
@bp.route("/pengguna", methods=["GET", "POST"])
@login_required("admin")
def pengguna():
    """Kelola akun, peran, status aktif, dan pembatasan kebun pengguna."""
    db = get_db()
    if request.method == "POST":
        f = request.form
        uid = f.get("id", type=int)
        username = f.get("username", "").strip().lower()
        role = f.get("role")
        kebun = f.get("kebun_kode") or None
        pw = f.get("password", "")
        if role not in ROLE_LABEL:
            abort(400)
        with tx(db):
            if uid:
                if uid == g.user["id"] and (role != "admin" or f.get("aktif") != "1"):
                    flash("Tidak bisa menurunkan peran / menonaktifkan akun sendiri", "error")
                    return redirect(url_for("admin.pengguna"))
                db.execute("UPDATE users SET nama = ?, role = ?, kebun_kode = ?, aktif = ? WHERE id = ?",
                           (f.get("nama", "").strip(), role, kebun, 1 if f.get("aktif") == "1" else 0, uid))
                if pw:
                    if len(pw) < 6:
                        flash("Password minimal 6 karakter", "error")
                        return redirect(url_for("admin.pengguna"))
                    db.execute("UPDATE users SET password = ? WHERE id = ?", (generate_password_hash(pw), uid))
                audit(db, g.user["id"], "ubah_pengguna", f"user#{uid} role={role} kebun={kebun}")
                flash("Pengguna diperbarui", "ok")
            else:
                if not re.fullmatch(r"[a-z0-9._-]{3,30}", username) or len(pw) < 6 or not f.get("nama"):
                    flash("Username 3-30 huruf/angka, nama wajib, password minimal 6 karakter", "error")
                elif db.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
                    flash("Username sudah dipakai", "error")
                else:
                    db.execute("INSERT INTO users (username, nama, password, role, kebun_kode) VALUES (?,?,?,?,?)",
                               (username, f.get("nama").strip(), generate_password_hash(pw), role, kebun))
                    audit(db, g.user["id"], "tambah_pengguna", f"{username} role={role}")
                    flash(f"Pengguna {username} ditambahkan", "ok")
        return redirect(url_for("admin.pengguna"))
    return render_template("pengguna.html", rows=db.execute("SELECT * FROM users ORDER BY aktif DESC, nama").fetchall(),
                           kebun=db.execute("SELECT * FROM kebun WHERE aktif = 1 ORDER BY nama").fetchall())


@bp.route("/akun", methods=["GET", "POST"])
@login_required()
def akun():
    """Izinkan pengguna aktif mengganti password akunnya sendiri."""
    if request.method == "POST":
        lama, baru = request.form.get("lama", ""), request.form.get("baru", "")
        if not check_password_hash(g.user["password"], lama):
            flash("Password lama salah", "error")
        elif len(baru) < 6 or baru != request.form.get("ulang"):
            flash("Password baru minimal 6 karakter dan harus sama dengan ulangan", "error")
        else:
            db = get_db()
            with tx(db):
                db.execute("UPDATE users SET password = ? WHERE id = ?", (generate_password_hash(baru), g.user["id"]))
            flash("Password berhasil diganti", "ok")
            return redirect(url_for("main.dashboard"))
    return render_template("akun.html")


def _nama_wifi():
    """Nama WiFi yang sedang dipakai laptop (Windows), untuk ditampilkan ke anggota."""
    import subprocess
    try:
        out = subprocess.run(["netsh", "wlan", "show", "interfaces"], capture_output=True, text=True, timeout=5,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    except (OSError, subprocess.SubprocessError):
        return ""
    for baris in out.splitlines():
        kunci, _, nilai = baris.partition(":")
        if kunci.strip() == "SSID":
            return nilai.strip()
    return ""


@bp.route("/akses")
@login_required("admin")
def akses():
    """Tampilkan alamat jaringan dan QR code untuk akses dari perangkat lain."""
    from .jaringan import alamat_lokal, qr_svg
    port = request.host.rsplit(":", 1)[1] if ":" in request.host else ("443" if request.scheme == "https" else "80")
    alamat = []
    for a in alamat_lokal():
        url = f"http://{a['ip']}" + ("" if port == "80" else f":{port}")
        svg = qr_svg(url)
        alamat.append({**a, "url": url, "qr": svg[svg.find("<svg"):]})
    return render_template("akses.html", alamat=alamat, port=port, wifi=_nama_wifi())


def _github_repo(value):
    """Normalisasi link/repositori GitHub menjadi bentuk owner/repository."""
    value = (value or "").strip()
    value = re.sub(r"^https?://github\.com/", "", value, flags=re.I).strip("/")
    value = re.sub(r"\.git$", "", value, flags=re.I)
    if not value:
        return ""
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}", value):
        raise ValueError("Link GitHub harus berbentuk https://github.com/pemilik/repositori")
    return value


@bp.route("/aplikasi-mobile", methods=["GET", "POST"])
@login_required("admin")
def aplikasi_mobile():
    """Kelola pembaruan APK dan perangkat yang dapat menyinkronkan data."""
    db = get_db()
    if request.method == "POST":
        action = request.form.get("action", "save_settings")
        try:
            with tx(db):
                if action == "save_settings":
                    repo = _github_repo(request.form.get("github_repo"))
                    minimum = request.form.get("minimum_version", "").strip()
                    if not re.fullmatch(r"\d{1,6}\.\d{1,2}\.\d{1,2}", minimum):
                        raise ValueError("Versi minimum harus berbentuk angka seperti 0.1.0")
                    values = {"mobile_github_repo": repo, "mobile_min_version": minimum}
                    for key in MOBILE_DEFAULTS:
                        db.execute(
                            "INSERT INTO pengaturan (kunci,nilai) VALUES (?,?) "
                            "ON CONFLICT(kunci) DO UPDATE SET nilai=excluded.nilai",
                            (key, values[key]),
                        )
                    audit(db, g.user["id"], "pengaturan_apk", f"github={repo or '-'}; minimum={minimum}")
                    flash("Pengaturan pembaruan APK disimpan", "ok")
                elif action in {"disable_device", "enable_device"}:
                    device_id = request.form.get("device_id", type=int)
                    device = db.execute("SELECT * FROM mobile_devices WHERE id=?", (device_id,)).fetchone()
                    if not device:
                        raise ValueError("Perangkat tidak ditemukan")
                    active = 1 if action == "enable_device" else 0
                    db.execute(
                        "UPDATE mobile_devices SET active=?, updated_at=datetime('now','localtime') WHERE id=?",
                        (active, device_id),
                    )
                    audit(db, g.user["id"], "akses_perangkat_apk",
                          f"device#{device_id} {'diaktifkan' if active else 'dinonaktifkan'}")
                    flash("Akses perangkat diperbarui", "ok")
                else:
                    raise ValueError("Tindakan tidak dikenal")
        except ValueError as exc:
            flash(str(exc), "error")
        return redirect(url_for("admin.aplikasi_mobile"))
    devices = db.execute(
        """SELECT d.*, u.nama AS user_name, u.username,
                  (SELECT COUNT(*) FROM mobile_submissions s WHERE s.device_id=d.id) AS submissions,
                  (SELECT MAX(received_at) FROM mobile_submissions s WHERE s.device_id=d.id) AS last_upload_at
           FROM mobile_devices d JOIN users u ON u.id=d.user_id
           ORDER BY d.active DESC, COALESCE(d.last_seen_at,d.created_at) DESC"""
    ).fetchall()
    dist_apk = Path(__file__).resolve().parent.parent / "mobile" / "dist-apk"
    apk_debug = max(dist_apk.glob("MonitoringStok-*-debug.apk"), key=lambda path: path.stat().st_mtime,
                    default=None) if dist_apk.exists() else None
    return render_template(
        "aplikasi_mobile.html",
        settings=mobile_pengaturan(db),
        devices=devices,
        apk_debug={"name": apk_debug.name, "size_mb": round(apk_debug.stat().st_size / 1024 / 1024, 1)}
                  if apk_debug else None,
    )


@bp.get("/aplikasi-mobile/apk-uji")
@login_required("admin")
def unduh_apk_uji():
    """Unduh APK debug terbaru untuk pemasangan terbatas pada HP penguji."""
    dist_apk = Path(__file__).resolve().parent.parent / "mobile" / "dist-apk"
    apk = max(dist_apk.glob("MonitoringStok-*-debug.apk"), key=lambda path: path.stat().st_mtime,
              default=None) if dist_apk.exists() else None
    if not apk:
        abort(404)
    return send_from_directory(dist_apk, apk.name, as_attachment=True,
                               mimetype="application/vnd.android.package-archive")


@bp.route("/audit")
@login_required("admin")
def audit_log():
    """Tampilkan jejak audit terbaru untuk pemeriksaan perubahan manual."""
    rows = get_db().execute("""SELECT a.*, u.nama FROM audit_log a LEFT JOIN users u ON u.id = a.user_id
                               ORDER BY a.id DESC LIMIT ? OFFSET ?""",
                            (PER_HAL, (halaman() - 1) * PER_HAL)).fetchall()
    return render_template("audit.html", rows=rows, hal=halaman())
