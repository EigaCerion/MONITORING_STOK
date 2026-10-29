"""Halaman operasional: dashboard, input HP, stok, kartu stok, transaksi, penyesuaian & opname."""
import datetime as dt
import hashlib
import hmac
import json
import re

from flask import (Blueprint, abort, current_app, flash, g, jsonify, redirect, render_template, request,
                   send_from_directory, url_for)

from .auth import login_required
from . import pengembalian
from .db import audit, delegasi_opname_aktif, get_db, opname_aktif, pengaturan, tx
from .excel_io import angka
from .photos import save_photo
from .report import excel_tabel
from .stock import (ALASAN_PENYESUAIAN, ALASAN_SAP, JENIS_LABEL, target_sap, EPS, StockError, batalkan_transaksi, bulat, cari_kebun, efek, fmt,
                    nomor_bon, sesuaikan_stok, simpan_transaksi, stok_material, ubah_qty_transaksi, validasi)

bp = Blueprint("main", __name__)
PER_HAL = 100
SQL_BELUM_POSTING = "t.batal = 0 AND t.sap_posted = 0 AND t.sumber <> 'sinkron_sap'"

TUJUAN_OTOMATIS = {
    "workshop": ("CM3AZGO996", "GOH-WORKSHOP"),
    "office": ("CM3AZGO992", "GOH-OFFICE LABOUR"),
    "staff": ("CM3AZGO991", "GOH-STAFF"),
}
TUJUAN_LABEL = {
    "blok": "Blok", "unit": "Unit / kendaraan", "workshop": "Workshop",
    "office": "Kantor", "staff": "Staff", "damkar": "Damkar / kebakaran",
}


def hari_ini():
    """Kembalikan tanggal lokal server dalam format ISO untuk form dan query."""
    return dt.date.today().isoformat()


def filter_kebun_user(alias="m"):
    """Petugas yang ditugaskan ke satu kebun hanya melihat material kebun tsb."""
    if g.user and g.user["kebun_kode"]:
        return f" AND {alias}.plant = ?", [g.user["kebun_kode"]]
    return "", []


def material_boleh(material_id):
    """Ambil material bila ada dan berada dalam kebun yang boleh diakses pengguna."""
    m = get_db().execute("SELECT * FROM materials WHERE id = ?", (material_id,)).fetchone()
    if not m:
        return None
    if g.user["kebun_kode"] and m["plant"] != g.user["kebun_kode"]:
        return None
    return m


def material_expired(m):
    """Flag manual atau tanggal kedaluwarsa yang sudah terlewati/sama dengan hari ini."""
    return bool(m and (m["expired"] or (m["expired_at"] and m["expired_at"] <= hari_ini())))


def material_input_locked(m):
    """Material yang sengaja diblokir admin agar kode tidak dipakai untuk input baru."""
    return bool(m and m["input_locked"])


def alasan_material_dikunci(m):
    """Pesan konsisten untuk material yang dikunci dari input."""
    return ((m["input_lock_reason"] or "Kode material dikunci oleh admin; gunakan kode pengganti yang benar.").strip()
            if m else "Kode material dikunci oleh admin.")


def wants_json():
    """Periksa apakah request fetch mengharapkan response JSON ringkas."""
    return request.headers.get("X-Requested-With") == "fetch"


def uploaded_photo(field_name):
    """Ambil foto dari kamera langsung bila ada, lalu fallback ke pilihan galeri."""
    camera_file = request.files.get(f"{field_name}_camera")
    if camera_file and camera_file.filename:
        return camera_file
    return request.files.get(field_name)


def kembali(default):
    """URL kembali dari form, hanya path lokal (cegah open redirect)."""
    k = request.form.get("kembali", "")
    return k if k.startswith("/") and not k.startswith("//") and "\\" not in k else default


def halaman():
    """Baca nomor halaman positif dari query string dengan fallback halaman pertama."""
    try:
        return max(1, int(request.args.get("hal", 1)))
    except ValueError:
        return 1


# ------------------------------------------------------------------ dashboard
@bp.route("/")
@login_required()
def dashboard():
    """Tampilkan ringkasan stok, masalah, dan transaksi terbaru sesuai akses."""
    if g.user["role"] == "petugas":
        return redirect(url_for("main.input_transaksi"))
    db = get_db()
    fk, fa = filter_kebun_user("v")
    s = db.execute(
        f"""SELECT COUNT(*) AS material,
                   SUM(CASE WHEN v.stok_minimum > 0 AND v.stok <= v.stok_minimum THEN 1 ELSE 0 END) AS menipis,
                   SUM(CASE WHEN v.stok <= 0 THEN 1 ELSE 0 END) AS habis,
                   SUM(CASE WHEN v.stok < -0.0005 THEN 1 ELSE 0 END) AS minus,
                   SUM(CASE WHEN v.expired = 1 OR (v.expired_at IS NOT NULL AND v.expired_at <= ?) THEN 1 ELSE 0 END) AS expired,
                   SUM(CASE WHEN v.input_locked = 1 THEN 1 ELSE 0 END) AS locked,
                   SUM(CASE WHEN ABS(v.selisih_nyata) > 0.0005 OR v.n_selisih_fisik > 0 THEN 1 ELSE 0 END)
                       AS selisih,
                   SUM(CASE WHEN v.kategori_id IS NULL THEN 1 ELSE 0 END) AS tanpa_kategori
            FROM v_stok v WHERE 1=1 {fk}""", [hari_ini(), *fa]).fetchone()
    hari = db.execute(
        f"""SELECT t.jenis, COUNT(*) AS n FROM transactions t JOIN materials m ON m.id = t.material_id
            WHERE t.batal = 0 AND t.tanggal = ? {fk.replace('v.', 'm.')} GROUP BY t.jenis""",
        [hari_ini(), *fa]).fetchall()
    tanpa_foto = db.execute(
        f"""SELECT COUNT(*) FROM transactions t JOIN materials m ON m.id = t.material_id
            WHERE t.batal = 0 AND t.jenis IN ('keluar','transfer_keluar') AND t.foto IS NULL
            {fk.replace('v.', 'm.')}""", fa).fetchone()[0]
    belum_posting = db.execute(
        f"""SELECT COUNT(*) FROM transactions t JOIN materials m ON m.id = t.material_id
            WHERE {SQL_BELUM_POSTING} {fk.replace('v.', 'm.')}""", fa).fetchone()[0]
    belum_kembali = db.execute(
        f"""SELECT COUNT(*) FROM transactions t JOIN materials m ON m.id = t.material_id
            WHERE {pengembalian.SQL_BELUM} {fk.replace('v.', 'm.')}""", fa).fetchone()[0]
    terakhir = db.execute(
        f"""SELECT t.*, m.material, m.deskripsi, m.satuan, u.nama AS oleh FROM transactions t
            JOIN materials m ON m.id = t.material_id LEFT JOIN users u ON u.id = t.created_by
            WHERE 1=1 {fk.replace('v.', 'm.')} ORDER BY t.id DESC LIMIT 12""", fa).fetchall()
    return render_template("dashboard.html", s=s, hari={r["jenis"]: r["n"] for r in hari},
                           tanpa_foto=tanpa_foto, terakhir=terakhir, belum_posting=belum_posting,
                           belum_kembali=belum_kembali)


# ------------------------------------------------------------------ input HP
def _item_tampil(m, qty_raw="", kembali_qty="", kembali_kondisi="", tujuan=None):
    """Siapkan dictionary material dan nilai form untuk baris input transaksi."""
    d = dict(m)
    d["stok"] = stok_material(get_db(), m["id"])
    d["alokasi_unit_workshop"] = _wajib_unit_workshop(get_db(), m)
    d["qty_raw"] = qty_raw
    d["kembali_qty"], d["kembali_kondisi"] = kembali_qty, kembali_kondisi
    tujuan = tujuan or {}
    d["tujuan_tipe"] = tujuan.get("tipe", "")
    d["tujuan_kode"] = tujuan.get("kode", "")
    d["aktivitas_kode"] = tujuan.get("aktivitas", "")
    d["tujuan_label"] = tujuan.get("label", "")
    return d


def _norm_duplikat(value):
    """Normalisasi teks agar pemeriksaan duplikat tahan perbedaan spasi/huruf."""
    return re.sub(r"\s+", " ", (value or "").strip().upper())


def _wajib_unit_workshop(db, material):
    """FAME, BBM, dan sparepart hanya boleh dialokasikan ke unit atau workshop."""
    kategori = ""
    if material["kategori_id"]:
        r = db.execute("SELECT nama FROM kategori WHERE id = ?", (material["kategori_id"],)).fetchone()
        kategori = _norm_duplikat(r["nama"] if r else "")
    deskripsi = _norm_duplikat(material["deskripsi"])
    return kategori in {"BBM", "SPAREPART"} or "FAME" in deskripsi


def _normalisasi_tujuan_nilai(tipe_raw, kode_raw, aktivitas_raw, jenis):
    """Normalisasi satu pilihan tujuan dari form umum maupun baris barang."""
    if jenis != "keluar":
        return {"tipe": "", "kode": "", "aktivitas": "", "label": ""}, []
    tipe = (tipe_raw or "").strip().lower()
    kode = _norm_duplikat(kode_raw)
    aktivitas = (aktivitas_raw or "").strip()
    err = []
    if tipe not in TUJUAN_LABEL:
        err.append("Tujuan BON wajib dipilih")
    elif tipe in TUJUAN_OTOMATIS:
        kode = TUJUAN_OTOMATIS[tipe][0]
        aktivitas = ""
    elif tipe == "blok":
        if not kode:
            err.append("Kode blok wajib diisi")
        aktivitas = ""
    elif tipe == "unit":
        if not kode:
            err.append("Kode unit / kendaraan wajib diisi")
        aktivitas = ""
    elif tipe == "damkar":
        if not kode:
            err.append("Kode blok lokasi Damkar wajib diisi")
        if aktivitas not in {"330", "331"}:
            err.append("Pilih aktivitas Damkar: 330 Fire Patrol atau 331 Fire Fighter Control")
    label = TUJUAN_LABEL.get(tipe, "")
    if tipe in TUJUAN_OTOMATIS:
        label += f" · {TUJUAN_OTOMATIS[tipe][1]} ({kode})"
    elif kode:
        label += f" · {kode}"
    if aktivitas:
        label += f" · aktivitas {aktivitas}"
    return {"tipe": tipe, "kode": kode, "aktivitas": aktivitas, "label": label}, err


def _normalisasi_tujuan(form, jenis):
    """Kembalikan tujuan umum bon dan pesan validasi untuk transaksi keluar."""
    return _normalisasi_tujuan_nilai(form.get("tujuan_tipe"), form.get("tujuan_kode"),
                                     form.get("aktivitas_kode"), jenis)


def _cocokkan_tujuan_master(db, plant, tujuan):
    """Pastikan blok/unit berasal dari Supplementary dan kembalikan kode operator baku.

    Instalasi lama yang belum pernah mengunggah jenis master tersebut tetap dapat
    bertransaksi. Begitu master tersedia untuk plant, teks bebas di luar master ditolak.
    """
    tipe = tujuan.get("tipe", "")
    master_type = "blok" if tipe == "damkar" else tipe
    if master_type not in {"blok", "unit"}:
        return tujuan, []
    ada_master = db.execute(
        "SELECT 1 FROM supplementary_targets WHERE plant = ? AND target_type = ? AND active = 1 LIMIT 1",
        (plant, master_type),
    ).fetchone()
    if not ada_master:
        return tujuan, []
    kode = _norm_duplikat(tujuan.get("kode"))
    if master_type == "unit" and re.fullmatch(r"EX\d+", re.sub(r"[\s-]+", "", kode)):
        kode = "EXA" + re.sub(r"[\s-]+", "", kode)[2:]
    row = db.execute(
        """SELECT code, sap_code FROM supplementary_targets
           WHERE plant = ? AND target_type = ? AND active = 1
             AND (UPPER(code) = ? OR UPPER(sap_code) = ?) LIMIT 1""",
        (plant, master_type, kode, kode),
    ).fetchone()
    if not row:
        return tujuan, [f"{TUJUAN_LABEL.get(tipe, 'Tujuan')} '{tujuan.get('kode', '')}' tidak terdaftar di Master Supplementary"]
    hasil = dict(tujuan)
    hasil["kode"] = row["code"]
    hasil["label"] = f"{TUJUAN_LABEL.get(tipe, tipe.title())} · {row['code']}"
    if hasil.get("aktivitas"):
        hasil["label"] += f" · aktivitas {hasil['aktivitas']}"
    return hasil, []


def _payload_duplikat(form):
    """Bentuk representasi transaksi stabil yang dapat dibandingkan dan ditandatangani."""
    tombol = form.get("tombol", "keluar")
    jenis = {"keluar": "keluar", "masuk": "masuk"}.get(tombol)
    if tombol == "transfer":
        jenis = "transfer_masuk" if form.get("arah") == "terima" else "transfer_keluar"
    mids, qtys = form.getlist("material_id"), form.getlist("qty")
    kembali_qtys, kembali_kondisi = form.getlist("kembali_qty"), form.getlist("kembali_kondisi")
    item_tipes = form.getlist("item_tujuan_tipe")
    item_kodes = form.getlist("item_tujuan_kode")
    item_aktivitas = form.getlist("item_aktivitas_kode")
    tujuan_umum, _ = _normalisasi_tujuan(form, jenis)
    barang, kembali = [], []
    for i, mid in enumerate(mids):
        qty = angka(qtys[i]) if i < len(qtys) else None
        if mid.isdigit() and qty is not None and qty > 0:
            tipe_item = item_tipes[i] if i < len(item_tipes) else ""
            if tipe_item:
                tujuan_item, _ = _normalisasi_tujuan_nilai(
                    tipe_item, item_kodes[i] if i < len(item_kodes) else "",
                    item_aktivitas[i] if i < len(item_aktivitas) else "", jenis)
            else:
                tujuan_item = tujuan_umum
            barang.append((int(mid), bulat(qty), tujuan_item["tipe"], tujuan_item["kode"],
                           tujuan_item["aktivitas"]))
            kqty = angka(kembali_qtys[i]) if i < len(kembali_qtys) else None
            kondisi = _norm_duplikat(kembali_kondisi[i] if i < len(kembali_kondisi) else "")
            kembali.append((int(mid), bulat(kqty or 0), kondisi))
    tujuan = tujuan_umum
    return {
        "tanggal": form.get("tanggal") or hari_ini(), "jenis": jenis or "",
        "penerima": _norm_duplikat(form.get("penerima")),
        "keterangan": _norm_duplikat(form.get("keterangan")),
        "kebun": _norm_duplikat(form.get("kebun")),
        "tujuan_tipe": tujuan["tipe"], "tujuan_kode": tujuan["kode"],
        "aktivitas_kode": tujuan["aktivitas"], "barang": sorted(barang), "kembali": sorted(kembali),
    }


def _token_duplikat(payload):
    """Tandatangani payload duplikat agar konfirmasi pengguna tidak dapat dipalsukan."""
    isi = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    kunci = str(current_app.config["SECRET_KEY"]).encode()
    return hmac.new(kunci, isi, hashlib.sha256).hexdigest()


def _cari_duplikat(db, payload):
    """Cari bon HP dengan tanggal, jenis, penerima, kebun, dan seluruh item+qty yang sama."""
    if not payload["jenis"] or not payload["barang"]:
        return []
    rows = db.execute(
        """SELECT t.*, m.deskripsi, m.satuan, u.nama AS dibuat_oleh,
                  COALESCE((SELECT p.qty FROM pengembalian p WHERE p.transaksi_id=t.id ORDER BY p.id LIMIT 1), 0)
                    AS kembali_qty,
                  COALESCE((SELECT p.kondisi FROM pengembalian p WHERE p.transaksi_id=t.id ORDER BY p.id LIMIT 1), '')
                    AS kembali_kondisi
           FROM transactions t JOIN materials m ON m.id = t.material_id
           LEFT JOIN users u ON u.id = t.created_by
           WHERE t.tanggal = ? AND t.jenis = ? AND t.sumber = 'hp' AND t.batal = 0
           ORDER BY t.id DESC""", (payload["tanggal"], payload["jenis"])).fetchall()
    grup = {}
    for r in rows:
        key = r["bon"] or f"TRX-{r['id']}"
        grup.setdefault(key, []).append(dict(r))
    hasil = []
    for bon, anggota in grup.items():
        pertama = anggota[0]
        barang = sorted((int(r["material_id"]), bulat(r["qty"]), r.get("tujuan_tipe") or "",
                         _norm_duplikat(r.get("tujuan_kode")), r.get("aktivitas_kode") or "")
                        for r in anggota)
        kembali = sorted((int(r["material_id"]), bulat(r["kembali_qty"]),
                           _norm_duplikat(r["kembali_kondisi"])) for r in anggota)
        if (barang != payload["barang"] or kembali != payload["kembali"]
                or _norm_duplikat(pertama.get("penerima")) != payload["penerima"]
                or _norm_duplikat(pertama.get("keterangan")) != payload["keterangan"]):
            continue
        kebun = pertama.get("kebun_asal") if payload["jenis"] == "transfer_masuk" else pertama.get("kebun_tujuan")
        if payload["jenis"].startswith("transfer") and _norm_duplikat(kebun) != payload["kebun"]:
            continue
        tujuan_set = {(r.get("tujuan_tipe") or "", _norm_duplikat(r.get("tujuan_kode"))) for r in anggota}
        tujuan_ringkas = "Beberapa tujuan" if len(tujuan_set) > 1 else (
            TUJUAN_LABEL.get(pertama.get("tujuan_tipe"), "") +
            (f" · {pertama.get('tujuan_kode')}" if pertama.get("tujuan_kode") else ""))
        hasil.append({
            "bon": bon, "tanggal": pertama["tanggal"], "penerima": pertama.get("penerima") or "-",
            "dibuat_oleh": pertama.get("dibuat_oleh") or "-", "created_at": pertama.get("created_at") or "",
            "tujuan": tujuan_ringkas or "belum tercatat",
            "items": [{"id": r["id"], "deskripsi": r["deskripsi"], "qty": fmt(r["qty"]),
                       "satuan": r["satuan"]} for r in reversed(anggota)],
        })
    return hasil[:5]


def _nomor_bon(db):
    """Alias kompatibilitas untuk pemanggil lama; implementasi ada di modul stok."""
    return nomor_bon(db)


def _saran(kolom):
    """Isian yang pernah dipakai (terbaru dulu) untuk pilihan cepat di form HP."""
    fk, fa = filter_kebun_user("m")
    return [r[0] for r in get_db().execute(
        f"""SELECT t.{kolom} FROM transactions t JOIN materials m ON m.id = t.material_id
            WHERE t.{kolom} IS NOT NULL AND TRIM(t.{kolom}) <> '' AND t.sumber = 'hp' {fk}
            GROUP BY t.{kolom} ORDER BY MAX(t.id) DESC LIMIT 40""", fa)]


@bp.route("/input", methods=["GET", "POST"])
@login_required("admin", "petugas")
def input_transaksi():
    """Satu kali simpan = satu bon: banyak barang, maksimal dua foto/penerima/nomor dokumen."""
    db = get_db()
    kebun = db.execute("SELECT * FROM kebun WHERE aktif = 1 ORDER BY nama").fetchall()
    form = request.form
    if request.method == "GET":
        m = material_boleh(request.args.get("material_id", type=int) or 0)
        return render_template("input.html", kebun=kebun, f={}, tombol=request.args.get("tombol", "keluar"),
                               items=[_item_tampil(m)] if m else [], hari_ini=hari_ini(),
                               kondisi=pengembalian.KONDISI, saran_penerima=_saran("penerima"),
                               saran_ket=_saran("keterangan"))

    tombol = form.get("tombol", "keluar")
    jenis = {"keluar": "keluar", "masuk": "masuk"}.get(tombol)
    if tombol == "transfer":
        jenis = "transfer_masuk" if form.get("arah") == "terima" else "transfer_keluar"
    errors, tampil, items, dipakai = [], [], [], set()

    def tambah_error(e):
        """Tambahkan pesan validasi sekali saja agar hasil form tetap ringkas."""
        if e not in errors:
            errors.append(e)

    tujuan, tujuan_errors = _normalisasi_tujuan(form, jenis)
    for error in tujuan_errors:
        tambah_error(error)
    qtys, k_qtys, k_kond = form.getlist("qty"), form.getlist("kembali_qty"), form.getlist("kembali_kondisi")
    item_tipes = form.getlist("item_tujuan_tipe")
    item_kodes = form.getlist("item_tujuan_kode")
    item_aktivitas = form.getlist("item_aktivitas_kode")
    ambil = lambda lst, i: lst[i] if i < len(lst) else ""  # noqa: E731
    for i, mid in enumerate(form.getlist("material_id")):
        m = material_boleh(int(mid)) if mid.isdigit() else None
        if not m:
            continue
        raw = ambil(qtys, i)
        tipe_item = ambil(item_tipes, i)
        if tipe_item:
            tujuan_item, item_errors = _normalisasi_tujuan_nilai(
                tipe_item, ambil(item_kodes, i), ambil(item_aktivitas, i), jenis)
            for error in item_errors:
                tambah_error(f"{m['deskripsi']}: {error}")
            tujuan_tampil = tujuan_item
        else:
            tujuan_item, tujuan_tampil = tujuan, None
        tujuan_item, master_errors = _cocokkan_tujuan_master(db, m["plant"], tujuan_item)
        for error in master_errors:
            tambah_error(f"{m['deskripsi']}: {error}")
        if tipe_item:
            tujuan_tampil = tujuan_item
        tampil.append(_item_tampil(m, raw, ambil(k_qtys, i), ambil(k_kond, i), tujuan_tampil))
        item_key = (m["id"], tujuan_item["tipe"], tujuan_item["kode"], tujuan_item["aktivitas"])
        if item_key in dipakai:
            tambah_error(f"{m['deskripsi']} tercantum dua kali untuk tujuan yang sama — gabungkan qty-nya")
            continue
        dipakai.add(item_key)
        if material_expired(m):
            tambah_error(f"{m['deskripsi']}: barang EXPIRED dan diblokir dari transaksi baru")
        if material_input_locked(m):
            tambah_error(f"{m['material']} — {m['deskripsi']}: {alasan_material_dikunci(m)}")
        if jenis == "keluar" and _wajib_unit_workshop(db, m) and tujuan_item["tipe"] not in {"unit", "workshop"}:
            tambah_error(f"{m['deskripsi']}: FAME, BBM, dan sparepart wajib diarahkan ke Unit / kendaraan atau Workshop")
        kembali_item = None
        if jenis == "keluar":
            kembali_item, err = pengembalian.baca(ambil(k_qtys, i), ambil(k_kond, i), angka(raw),
                                                  m["wajib_kembali"], m["deskripsi"])
            if err:
                tambah_error(err)
        items.append((m, angka(raw), kembali_item, tujuan_item))
    if not items:
        tambah_error("Tambahkan minimal satu barang")
    if len({it[0]["plant"] for it in items}) > 1:
        tambah_error("Semua barang dalam satu bon harus dari kebun yang sama")
    for plant in {it[0]["plant"] for it in items}:
        sesi_aktif = opname_aktif(db, plant)
        if sesi_aktif:
            tambah_error(f"Gudang {plant} sedang Stock Opname bersama (sesi #{sesi_aktif['id']}). "
                         "Transaksi baru dikunci sampai opname selesai.")
    tgl = form.get("tanggal") or hari_ini()
    if tgl > hari_ini():
        tambah_error("Tanggal tidak boleh di masa depan")
    kebun_lain = cari_kebun(db, form.get("kebun")) if jenis and jenis.startswith("transfer") else None
    foto = uploaded_photo("foto")
    foto_2 = uploaded_photo("foto_2")
    ada_foto = bool(foto and foto.filename)
    if not jenis:
        tambah_error("Jenis transaksi tidak dikenal")
    foto_kembali = uploaded_photo("foto_kembali")
    if any(pengembalian.ada_barang(item[2]) for item in items) and not (foto_kembali and foto_kembali.filename):
        tambah_error("Foto barang lama wajib karena ada barang lama yang dikembalikan")
    for m, qty, _, _ in items if jenis else []:
        for e in validasi(jenis, qty, stok_material(db, m["id"]), form.get("nomor_dokumen", ""),
                          kebun_asal=kebun_lain if jenis == "transfer_masuk" else None,
                          kebun_tujuan=kebun_lain if jenis == "transfer_keluar" else None,
                          plant=m["plant"], penerima=form.get("penerima", ""), ada_foto=ada_foto,
                          wajib_penerima=True):
            tambah_error(f"{m['deskripsi']}: {e}" if e.startswith("Qty") else e)

    # Satu material boleh dipecah ke beberapa tujuan dalam satu bon. Karena setiap
    # baris melihat stok yang sama, jumlah seluruh pecahan juga harus divalidasi
    # sebelum foto disimpan dan transaksi database dimulai.
    if jenis in {"keluar", "transfer_keluar"}:
        total_per_material = {}
        for material, qty, _, _ in items:
            if qty is not None and qty > 0:
                total_per_material[material["id"]] = total_per_material.get(material["id"], 0) + qty
        for material_id, total_qty in total_per_material.items():
            material = next(item[0] for item in items if item[0]["id"] == material_id)
            stok = stok_material(db, material_id)
            if total_qty > stok + EPS:
                tambah_error(
                    f"{material['deskripsi']}: total pembagian {fmt(total_qty)} melebihi stok tersedia {fmt(stok)}"
                )

    if not errors:
        payload_duplikat = _payload_duplikat(form)
        duplikat = _cari_duplikat(db, payload_duplikat)
        if duplikat and not hmac.compare_digest(form.get("duplikat_token", ""),
                                                 _token_duplikat(payload_duplikat)):
            tambah_error("Kemungkinan double input ditemukan. Periksa transaksi sebelumnya lalu tekan "
                         "‘Tetap simpan’ bila transaksi ini memang berbeda.")

    if not errors:
        try:
            nama_foto = save_photo(foto, metadata_time=form.get("foto_waktu_metadata"))
            nama_foto_2 = save_photo(foto_2, metadata_time=form.get("foto_2_waktu_metadata"))
            foto_lama = (save_photo(foto_kembali, metadata_time=form.get("foto_kembali_waktu_metadata"))
                         if any(item[2] for item in items) else None)
            hasil = []
            with tx(db):
                aktif = opname_aktif(db, items[0][0]["plant"] if items else None)
                if aktif:
                    raise StockError(f"Gudang sedang Stock Opname bersama (sesi #{aktif['id']}); transaksi dikunci")
                bon = _nomor_bon(db)
                for m, qty, kembali_item, tujuan_item in items:
                    status_material = db.execute(
                        "SELECT input_locked, input_lock_reason, expired, expired_at FROM materials WHERE id = ?",
                        (m["id"],),
                    ).fetchone()
                    if material_input_locked(status_material):
                        raise StockError(f"{m['material']} — {m['deskripsi']}: "
                                         f"{alasan_material_dikunci(status_material)}")
                    if material_expired(status_material):
                        raise StockError(f"{m['deskripsi']}: barang EXPIRED dan diblokir dari transaksi baru")
                    stok = stok_material(db, m["id"])  # cek ulang di dalam lock tulis
                    if jenis in ("keluar", "transfer_keluar") and qty > stok + EPS:
                        raise StockError(f"{m['deskripsi']}: qty {fmt(qty)} melebihi stok tersedia {fmt(stok)}")
                    tid = simpan_transaksi(db, {
                        "tanggal": tgl, "jenis": jenis, "material_id": m["id"], "qty": qty,
                        "nomor_dokumen": form.get("nomor_dokumen", "").strip(),
                        "penerima": form.get("penerima", "").strip(),
                        "kebun_asal": kebun_lain if jenis == "transfer_masuk" else None,
                        "kebun_tujuan": kebun_lain if jenis == "transfer_keluar" else None,
                        "keterangan": form.get("keterangan", "").strip(), "foto": nama_foto,
                        "foto_2": nama_foto_2, "sumber": "hp",
                        "tujuan_tipe": tujuan_item["tipe"], "tujuan_kode": tujuan_item["kode"],
                        "aktivitas_kode": tujuan_item["aktivitas"],
                        "bon": bon,
                    }, g.user["id"])
                    if kembali_item:
                        pengembalian.simpan(db, tid, m["id"], tgl, kembali_item[0], kembali_item[1], None, foto_lama,
                                            form.get("penerima", "").strip(), g.user["id"])
                    hasil.append(f"{fmt(qty)} {m['satuan']} {m['deskripsi']} → {tujuan_item['label']} "
                                 f"(sisa {fmt(stok_material(db, m['id']))})")
            flash(f"Bon {bon} tersimpan, {len(hasil)} barang: " + "; ".join(hasil), "ok")
            return redirect(url_for("main.input_transaksi", tombol=tombol))
        except StockError as e:
            tambah_error(str(e))
    for e in errors:
        flash(e, "error")
    return render_template("input.html", kebun=kebun, f=form, tombol=tombol, items=tampil, hari_ini=hari_ini(),
                           kondisi=pengembalian.KONDISI, saran_penerima=_saran("penerima"),
                           saran_ket=_saran("keterangan"))


@bp.route("/api/cek-duplikat", methods=["POST"])
@login_required("admin", "petugas")
def api_cek_duplikat():
    """Cari transaksi identik pada tanggal pilihan sebelum foto benar-benar diunggah."""
    payload = _payload_duplikat(request.form)
    hasil = _cari_duplikat(get_db(), payload)
    return jsonify({"ok": True, "duplikat": hasil, "token": _token_duplikat(payload)})


@bp.route("/api/material")
@login_required()
def api_material():
    """Cari material aktif untuk autocomplete input transaksi."""
    q = request.args.get("q", "").strip()
    fk, fa = filter_kebun_user("v")
    args = list(fa)
    cond = ""
    for kata in q.split()[:5]:
        cond += " AND (v.material LIKE ? OR v.deskripsi LIKE ?)"
        args += [f"%{kata}%", f"%{kata}%"]
    rows = get_db().execute(
        f"""SELECT v.id, v.material, v.deskripsi, v.satuan, v.stok, v.special_stock, v.special_stock_no,
                   v.plant, v.stok_minimum, v.wajib_kembali, v.expired, v.expired_at,
                   v.input_locked, v.input_lock_reason, k.nama AS kategori
            FROM v_stok v LEFT JOIN kategori k ON k.id = v.kategori_id WHERE 1=1 {fk} {cond}
            ORDER BY CASE WHEN v.material = ? THEN 0 ELSE 1 END, v.deskripsi LIMIT 30""",
        args + [q]).fetchall()
    return jsonify([{**dict(r), "stok": bulat(r["stok"]), "stok_txt": fmt(r["stok"]),
                     "expired_effective": material_expired(r),
                     "input_locked": material_input_locked(r),
                     "input_lock_reason": alasan_material_dikunci(r) if material_input_locked(r) else "",
                     "alokasi_unit_workshop": (_norm_duplikat(r["kategori"]) in {"BBM", "SPAREPART"}
                                                or "FAME" in _norm_duplikat(r["deskripsi"]))} for r in rows])


@bp.route("/api/tujuan")
@login_required()
def api_tujuan():
    """Cari blok, unit, dan cost center GOH untuk pemilih tujuan yang ramah HP."""
    input_query = _norm_duplikat(request.args.get("q"))
    query = input_query
    target_type = (request.args.get("tipe") or "").strip().lower()
    plant = _norm_duplikat(g.user["kebun_kode"] or request.args.get("plant"))
    damkar = False
    division_filter = ""

    # AFD/Afdeling pada bon bukan satu objek SAP, melainkan kelompok blok.
    # Pencarian AFD 3 menampilkan semua blok Division 3 agar operator memilih
    # blok yang benar; API tidak pernah menebak satu blok dari divisi saja.
    division_input = re.match(r"^(?:AFD|AFDELING|DIVISI|DIV)\s*[-:]?\s*(\d+)(?:\s+(.+))?$", query)
    if division_input:
        division_filter = division_input.group(1)
        query = (division_input.group(2) or "").strip()
        target_type = "blok"
    elif re.fullmatch(r"(?:AFD|AFDELING|DIVISI|DIV)\s*[-:]?\s*", query):
        return jsonify([])

    # Pengguna boleh mengetik bahasa sehari-hari. Awalan ini hanya membantu
    # pencarian; nilai yang disimpan tetap berasal dari master, bukan teks bebas.
    prefix = re.match(r"^(BLOK|BLOCK|UNIT|DAMKAR|KEBAKARAN)\s*[-:]?\s*(.*)$", query)
    if prefix:
        jenis_input, query = prefix.groups()
        if jenis_input in {"BLOK", "BLOCK"}:
            target_type = target_type or "blok"
        elif jenis_input == "UNIT":
            target_type = target_type or "unit"
        else:
            target_type, damkar = "blok", True

    # Alias unit excavator yang umum disebut tanpa huruf A: EX17 -> EXA17.
    query_compact = re.sub(r"[\s-]+", "", query)
    if re.fullmatch(r"EX\d+", query_compact):
        query = "EXA" + query_compact[2:]
        query_compact = query

    args, where = [], ["active = 1"]
    if plant:
        where.append("plant = ?")
        args.append(plant)
    if target_type in {"blok", "unit"}:
        where.append("target_type = ?")
        args.append(target_type)
    if division_filter:
        where.append("division = ?")
        args.append(division_filter)
    for word in query.split()[:5]:
        where.append("(code LIKE ? OR sap_code LIKE ? OR name LIKE ? OR division LIKE ? OR phase LIKE ?)")
        args.extend([f"%{word}%"] * 5)
    limit = 100 if division_filter else 30
    rows = get_db().execute(
        f"""SELECT plant, target_type, code, sap_code, name, division, phase
            FROM supplementary_targets WHERE {' AND '.join(where)}
            ORDER BY target_type, code LIMIT {limit}""", args).fetchall()
    result = []
    for row in rows:
        tipe_hasil = "damkar" if damkar else row["target_type"]
        kode_compact = re.sub(r"[\s-]+", "", _norm_duplikat(row["code"]))
        sap_compact = re.sub(r"[\s-]+", "", _norm_duplikat(row["sap_code"]))
        exact = bool(query_compact and query_compact in {kode_compact, sap_compact})
        detail_parts = [x for x in (
            row["sap_code"], f"Divisi {row['division']}" if row["division"] else "", row["phase"] or "") if x]
        if division_filter and not query:
            detail_parts.insert(0, f"AFD {division_filter} — pilih blok yang tertulis/terkonfirmasi")
        detail = " · ".join(detail_parts)
        result.append({
            "tipe": tipe_hasil, "kode": row["code"], "sap_kode": row["sap_code"],
            "nama": (f"Damkar · {row['name']}" if damkar else row["name"]), "plant": row["plant"],
            "detail": detail, "exact_match": exact,
        })
    automatic = []
    aliases = {
        "workshop": {"WORKSHOP", "BENGKEL", "GOH WORKSHOP", "GOH-WORKSHOP"},
        "office": {"KANTOR", "OFFICE", "GOH OFFICE", "GOH-OFFICE", "GOH OFFICE LABOUR"},
        "staff": {"STAFF", "STAF", "GOH STAFF", "GOH-STAFF"},
    }
    for tipe, (kode, nama) in TUJUAN_OTOMATIS.items():
        haystack = _norm_duplikat(f"{tipe} {kode} {nama} {TUJUAN_LABEL[tipe]}")
        if target_type and target_type != tipe:
            continue
        alias_match = input_query in aliases.get(tipe, set())
        if query and not alias_match and not all(word in haystack for word in query.split()):
            continue
        automatic.append({"tipe": tipe, "kode": kode, "sap_kode": kode, "nama": nama,
                          "plant": plant, "detail": f"Cost center {kode}",
                          "exact_match": bool(alias_match or input_query == _norm_duplikat(kode))})
    return jsonify((automatic + result)[:limit])


# ------------------------------------------------------------------ stok
def _query_stok(args, limit=True):
    """Bangun dan jalankan query stok berdasarkan filter halaman atau export."""
    fk, fa = filter_kebun_user("v")
    where, prm = "1=1" + fk, list(fa)
    q = args.get("q", "").strip()
    for kata in q.split()[:5]:
        where += " AND (v.material LIKE ? OR v.deskripsi LIKE ? OR v.material_group LIKE ?)"
        prm += [f"%{kata}%"] * 3
    if args.get("kebun"):
        where += " AND v.plant = ?"
        prm.append(args["kebun"])
    kat = args.get("kategori", "")
    if kat == "none":
        where += " AND v.kategori_id IS NULL"
    elif kat:
        where += " AND v.kategori_id = ?"
        prm.append(int(kat))
    st = args.get("status", "")
    if st == "menipis":
        where += " AND v.stok_minimum > 0 AND v.stok <= v.stok_minimum"
    elif st == "habis":
        where += " AND v.stok <= 0"
    elif st == "selisih":
        where += " AND (ABS(v.selisih_nyata) > 0.0005 OR v.n_selisih_fisik > 0)"
    elif st == "expired":
        where += " AND (v.expired = 1 OR (v.expired_at IS NOT NULL AND v.expired_at <= ?))"
        prm.append(hari_ini())
    elif st == "locked":
        where += " AND v.input_locked = 1"
    db = get_db()
    total = db.execute(f"SELECT COUNT(*) FROM v_stok v WHERE {where}", prm).fetchone()[0]
    sql = f"""SELECT v.*, k.nama AS kategori, COALESCE(kb.nama, v.plant) AS kebun_nama
              FROM v_stok v LEFT JOIN kategori k ON k.id = v.kategori_id LEFT JOIN kebun kb ON kb.kode = v.plant
              WHERE {where} ORDER BY v.plant, COALESCE(k.urutan, 999), v.deskripsi"""
    if limit:
        sql += f" LIMIT {PER_HAL} OFFSET {(halaman() - 1) * PER_HAL}"
    return db.execute(sql, prm).fetchall(), total


@bp.route("/stok")
@login_required()
def stok():
    """Tampilkan daftar stok berjalan dengan status SAP, minimum, dan expired."""
    rows, total = _query_stok(request.args)
    db = get_db()
    return render_template("stok.html", rows=rows, total=total, hal=halaman(), per_hal=PER_HAL,
                           kategori=db.execute("SELECT * FROM kategori ORDER BY urutan").fetchall(),
                           kebun=db.execute("SELECT * FROM kebun ORDER BY nama").fetchall(),
                           alasan=ALASAN_PENYESUAIAN, hari_ini=hari_ini())


@bp.route("/stok/export")
@login_required()
def export_stok():
    """Export daftar stok sesuai filter aktif menjadi workbook Excel."""
    rows, _ = _query_stok(request.args, limit=False)
    data = [[m["kebun_nama"], m["kategori"] or "", m["material_group"], m["material"], m["deskripsi"],
             m["special_stock"], m["satuan"], m["stok"], m["sap_unrestricted"], m["tunggu_sap"],
             m["sap_perkiraan"], m["pending_rutin"], m["selisih_fisik"], m["selisih_nyata"],
             m["stok_minimum"] or None, "YA" if material_expired(m) else "", m["expired_at"],
             "YA" if material_input_locked(m) else "", m["input_lock_reason"],
             m["sap_updated_at"]] for m in rows]
    header = ["Kebun", "Kategori", "Group", "Material", "Deskripsi", "Sp.Stock", "Satuan", "Stok Aplikasi",
              "Stok File SAP", "Diinput SAP setelah file", "SAP Perkiraan", "Trx Harian Belum Diinput SAP",
              "Selisih Fisik Belum Disesuaikan SAP", "Selisih Nyata", "Stok Minimum", "Expired", "Tanggal Expired",
              "Input Dikunci", "Alasan Kunci", "File SAP per"]
    num = "#,##0.###"
    return excel_tabel("Stok", header, data, [20, 16, 8, 14, 38, 8, 7, 13, 13, 13, 13, 14, 16, 12, 11, 9, 13, 11, 36, 18],
                       f"Stok_{hari_ini()}.xlsx", {j: num for j in range(8, 16)})


@bp.route("/stok/<int:mid>/sesuaikan", methods=["POST"])
@login_required("admin")
def sesuaikan(mid):
    """Ubah stok satu material melalui transaksi penyesuaian yang diaudit."""
    db = get_db()
    m = material_boleh(mid) or abort(404)
    aktif = opname_aktif(db, m["plant"])
    if aktif:
        pesan = f"Gudang sedang Stock Opname bersama (sesi #{aktif['id']}); perubahan stok dikunci"
        if wants_json():
            return jsonify(ok=False, pesan=pesan), 409
        flash(pesan, "error")
        return redirect(kembali(url_for("main.kartu_stok", mid=mid)))
    qty = angka(request.form.get("qty_baru"))
    alasan = request.form.get("alasan", "").strip()
    try:
        with tx(db):
            tid, lama, selisih = sesuaikan_stok(db, mid, qty, alasan, request.form.get("keterangan", "").strip(),
                                                request.form.get("tanggal") or hari_ini(), g.user["id"])
        pesan = f"Stok {m['material']} diubah {fmt(lama)} → {fmt(qty)} {m['satuan']} (selisih {fmt(selisih)})"
        if wants_json():
            return jsonify(ok=True, pesan=pesan, stok=bulat(qty), stok_txt=fmt(qty), trx=tid)
        flash(pesan, "ok")
    except StockError as e:
        if wants_json():
            return jsonify(ok=False, pesan=str(e)), 400
        flash(str(e), "error")
    return redirect(kembali(url_for("main.kartu_stok", mid=mid)))


@bp.route("/stok/<int:mid>")
@login_required()
def kartu_stok(mid):
    """Tampilkan detail material dan mutasi stok dengan saldo berjalan."""
    db = get_db()
    m = material_boleh(mid) or abort(404)
    m = db.execute("""SELECT v.*, k.nama AS kategori, COALESCE(kb.nama, v.plant) AS kebun_nama FROM v_stok v
                      LEFT JOIN kategori k ON k.id = v.kategori_id LEFT JOIN kebun kb ON kb.kode = v.plant
                      WHERE v.id = ?""", (mid,)).fetchone()
    trx = db.execute(
        """SELECT t.*, u.nama AS oleh, u2.nama AS diubah_oleh, ka.nama AS asal_nama, kt.nama AS tujuan_nama
           FROM transactions t LEFT JOIN users u ON u.id = t.created_by LEFT JOIN users u2 ON u2.id = t.updated_by
           LEFT JOIN kebun ka ON ka.kode = t.kebun_asal LEFT JOIN kebun kt ON kt.kode = t.kebun_tujuan
           WHERE t.material_id = ? ORDER BY t.tanggal, t.id""", (mid,)).fetchall()
    saldo, baris = m["stok_awal"], []
    for t in trx:
        if not t["batal"]:
            saldo = bulat(saldo + efek(t["jenis"], t["qty"]))
        baris.append({**dict(t), "saldo": saldo, "efek": efek(t["jenis"], t["qty"])})
    baris.reverse()
    return render_template("kartu_stok.html", m=m, baris=baris, alasan=ALASAN_PENYESUAIAN, hari_ini=hari_ini(),
                           kembali=pengembalian.per_transaksi(db, [b["id"] for b in baris]),
                           kondisi=pengembalian.KONDISI)


# ------------------------------------------------------------------ stock opname (massal)
@bp.route("/opname", methods=["GET", "POST"])
@login_required("admin", "petugas")
def opname():
    """Layani opname langsung satu admin untuk beberapa material sekaligus."""
    db = get_db()
    if request.method == "POST":
        if g.user["role"] != "admin":
            abort(403)
        f = request.form
        alasan = f.get("alasan", "").strip() or ALASAN_PENYESUAIAN[0]
        tgl = f.get("tanggal") or hari_ini()
        ket = f.get("keterangan", "").strip()
        jenis_barang = f.get("jenis_barang", "").strip().upper() or "UMUM"
        lokasi = f.get("lokasi", "").strip().upper()
        isi, gagal = [], []
        for key, val in f.items():
            if not key.startswith("fisik_") or not val.strip():
                continue
            mid = int(key[6:])
            qty = angka(val)
            if qty is None or qty < 0:
                gagal.append(f"#{mid}: '{val}' bukan angka valid")
            elif material_boleh(mid):
                isi.append((mid, qty, f.get(f"ket_{mid}", "").strip()))
        if gagal or not isi:
            for e in gagal:
                flash(e, "error")
            if not isi:
                flash("Belum ada qty fisik yang diisi.", "error")
            return redirect(url_for("main.opname", **request.args))
        plants = {db.execute("SELECT plant FROM materials WHERE id = ?", (mid,)).fetchone()["plant"]
                  for mid, _, _ in isi}
        terkunci = [p for p in plants if opname_aktif(db, p)]
        if terkunci:
            flash(f"Gudang {terkunci[0]} sedang memiliki sesi opname bersama. Gunakan sesi tersebut.", "error")
            return redirect(url_for("main.opname"))
        ubah = 0
        with tx(db):
            plant = next(iter(plants)) if len(plants) == 1 else None
            sesi = db.execute("INSERT INTO opname_sesi (tanggal, jenis_barang, lokasi, plant, status, keterangan, "
                              "created_by, closed_by, closed_at) VALUES (?,?,?,?,?,?,?,?,datetime('now','localtime'))",
                              (tgl, jenis_barang, lokasi, plant, "selesai", ket, g.user["id"], g.user["id"])).lastrowid
            for mid, qty, ket_item in isi:
                v = db.execute("SELECT stok, sap_perkiraan FROM v_stok WHERE id = ?", (mid,)).fetchone()
                trx = None
                if abs(qty - v["stok"]) >= EPS:
                    trx, _, _ = sesuaikan_stok(db, mid, qty, alasan, " · ".join(x for x in (ket, ket_item) if x)
                                               or f"Opname {jenis_barang}", tgl, g.user["id"])
                    ubah += 1
                db.execute("INSERT INTO opname_item (sesi_id, material_id, stok_buku, stok_sap, fisik, keterangan, "
                           "trx_id, counted_by, counted_at) VALUES (?,?,?,?,?,?,?,?,datetime('now','localtime'))",
                           (sesi, mid, bulat(v["stok"]), v["sap_perkiraan"], bulat(qty), ket_item or None, trx,
                            g.user["id"]))
            audit(db, g.user["id"], "stock_opname", f"sesi #{sesi} {jenis_barang}: {len(isi)} dihitung, {ubah} selisih")
        flash(f"Stock opname {jenis_barang} disimpan: {len(isi)} material dihitung, {ubah} disesuaikan.", "ok")
        return redirect(url_for("opname.detail", sid=sesi))
    rows, total = _query_stok(request.args, limit=False)
    kategori = db.execute("SELECT * FROM kategori ORDER BY urutan").fetchall()
    jenis_default = next((k["nama"].upper() for k in kategori if str(k["id"]) == request.args.get("kategori")), "")
    jenis_lama = [r[0] for r in db.execute("SELECT DISTINCT jenis_barang FROM opname_sesi ORDER BY jenis_barang")]
    sesi_aktif = db.execute(
        f"""SELECT s.*, kb.nama AS kebun_nama, u.nama AS pembuat, COUNT(i.id) AS n_hitung,
                   COUNT(DISTINCT i.counted_by) AS n_petugas
            FROM opname_sesi s LEFT JOIN kebun kb ON kb.kode = s.plant LEFT JOIN users u ON u.id = s.created_by
            LEFT JOIN opname_item i ON i.sesi_id = s.id LEFT JOIN materials m ON m.id = i.material_id
            WHERE s.status = 'terbuka'
              {"AND s.plant = ?" if g.user["kebun_kode"] else ""}
            GROUP BY s.id ORDER BY s.id DESC""",
        [g.user["kebun_kode"]] if g.user["kebun_kode"] else []).fetchall()
    delegasi_aktif = (
        delegasi_opname_aktif(db, g.user["id"], g.user["kebun_kode"])
        if g.user["role"] == "petugas" else None
    )
    delegasi_rows = []
    petugas_delegasi = []
    if g.user["role"] == "admin":
        delegasi_rows = db.execute(
            """SELECT d.*, u.nama, u.username, k.nama AS kebun_nama, a.nama AS pemberi
               FROM opname_delegasi d JOIN users u ON u.id = d.user_id
               LEFT JOIN kebun k ON k.kode = d.plant LEFT JOIN users a ON a.id = d.granted_by
               WHERE d.valid_until >= datetime('now','localtime')
               ORDER BY d.valid_until, u.nama"""
        ).fetchall()
        petugas_delegasi = db.execute(
            """SELECT u.*, k.nama AS kebun_nama FROM users u LEFT JOIN kebun k ON k.kode = u.kebun_kode
               WHERE u.role = 'petugas' AND u.aktif = 1 AND u.kebun_kode IS NOT NULL ORDER BY u.nama"""
        ).fetchall()
    delegasi_default = (dt.datetime.now() + dt.timedelta(hours=8)).strftime("%Y-%m-%dT%H:%M")
    delegasi_maks = (dt.datetime.now() + dt.timedelta(days=7)).strftime("%Y-%m-%dT%H:%M")
    return render_template("opname.html", rows=rows[:500], total=total, kategori=kategori,
                           jenis_default=jenis_default, setelan=pengaturan(db),
                           jenis_pilihan=sorted(set(jenis_lama) | {"SPAREPART", "FERTILIZERS", "CHEMICALS"}
                                                | {k["nama"].upper() for k in kategori}),
                           kebun=db.execute("SELECT * FROM kebun ORDER BY nama").fetchall(),
                           alasan=ALASAN_PENYESUAIAN, hari_ini=hari_ini(), sesi_aktif=sesi_aktif,
                           delegasi_aktif=delegasi_aktif, delegasi_rows=delegasi_rows,
                           petugas_delegasi=petugas_delegasi, delegasi_default=delegasi_default,
                           delegasi_maks=delegasi_maks)


# ------------------------------------------------------------------ selisih SAP
@bp.route("/selisih", methods=["GET", "POST"])
@login_required("admin", "pimpinan")
def selisih():
    """Kelompokkan selisih SAP dan proses koreksi yang dipilih admin."""
    db = get_db()
    if request.method == "POST":
        if g.user["role"] != "admin":
            abort(403)
        ids = [int(i) for i in request.form.getlist("ids")]
        if request.form.get("aksi") == "posting_fisik":
            ids = [i for i in ids if material_boleh(i)]
            if not ids:
                flash("Centang material terlebih dahulu", "error")
                return redirect(url_for("main.selisih"))
            with tx(db):
                n = db.execute(
                    f"""UPDATE transactions SET sap_posted = 1, sap_posted_at = datetime('now','localtime'),
                        sap_posted_by = ?, sap_doc = COALESCE(NULLIF(?, ''), sap_doc)
                        WHERE batal = 0 AND sap_posted = 0 AND jenis = 'penyesuaian' AND sumber <> 'sinkron_sap'
                          AND material_id IN ({','.join('?' * len(ids))})""",
                    [g.user["id"], request.form.get("sap_doc", "").strip()] + ids).rowcount
                audit(db, g.user["id"], "posting_sap", f"{n} penyesuaian ditandai sudah disesuaikan di SAP: {ids[:50]}")
            flash(f"{n} penyesuaian ditandai ✓ sudah disesuaikan di SAP.", "ok")
            return redirect(url_for("main.selisih"))
        n = 0
        with tx(db):
            for mid in ids:
                m = material_boleh(mid)
                target = target_sap(db, mid) if m else None
                if target is None or abs(stok_material(db, mid) - target) < EPS:
                    continue
                sesuaikan_stok(db, mid, target, ALASAN_SAP,
                               f"Koreksi ke SAP per {m['sap_updated_at'] or '-'} + transaksi belum posting",
                               hari_ini(), g.user["id"])
                n += 1
        flash(f"{n} material dikoreksi mengikuti SAP.", "ok")
        return redirect(url_for("main.selisih"))
    fk, fa = filter_kebun_user("v")
    rows = db.execute(
        f"""SELECT v.*, k.nama AS kategori FROM v_stok v LEFT JOIN kategori k ON k.id = v.kategori_id
            WHERE v.sap_unrestricted IS NOT NULL
              AND (ABS(v.selisih_nyata) > 0.0005 OR v.n_belum_posting > 0) {fk}
            ORDER BY v.deskripsi""", fa).fetchall()
    nyata = [r for r in rows if abs(r["selisih_nyata"]) > 0.0005]
    fisik = [r for r in rows if r["n_selisih_fisik"]]
    menunggu = [r for r in rows if r["n_pending_rutin"] and abs(r["selisih_nyata"]) <= 0.0005]
    info = db.execute(f"SELECT MAX(v.sap_updated_at) AS per, SUM(v.ada_di_sap = 0) AS hilang, "
                      f"SUM(v.n_belum_posting) AS n_belum, SUM(v.n_tunggu_upload) AS n_tunggu FROM v_stok v "
                      f"WHERE 1=1 {fk}", fa).fetchone()
    return render_template("selisih.html", nyata=nyata, fisik=fisik, menunggu=menunggu, info=info,
                           alasan=ALASAN_PENYESUAIAN,
                           hari_ini=hari_ini())


# ------------------------------------------------------------------ transaksi
def _filter_transaksi(a):
    """Susun klausa filter dan parameter aman untuk daftar/export transaksi."""
    fk, fa = filter_kebun_user("m")
    where, prm = "1=1" + fk, list(fa)
    if a.get("dari"):
        where += " AND t.tanggal >= ?"
        prm.append(a["dari"])
    if a.get("sampai"):
        where += " AND t.tanggal <= ?"
        prm.append(a["sampai"])
    if a.get("jenis"):
        where += " AND t.jenis = ?"
        prm.append(a["jenis"])
    if a.get("bon"):
        where += " AND t.bon = ?"
        prm.append(a["bon"])
    if a.get("kebun"):
        where += " AND (m.plant = ? OR t.kebun_asal = ? OR t.kebun_tujuan = ?)"
        prm += [a["kebun"]] * 3
    for kata in a.get("q", "").split()[:5]:
        where += (" AND (m.material LIKE ? OR m.deskripsi LIKE ? OR t.nomor_dokumen LIKE ? OR t.penerima LIKE ?"
                  " OR t.keterangan LIKE ? OR t.sap_doc LIKE ? OR t.bon LIKE ? OR t.tujuan_kode LIKE ?)")
        prm += [f"%{kata}%"] * 8
    st = a.get("status")
    if st == "tanpa_foto":
        where += " AND t.batal = 0 AND t.jenis IN ('keluar','transfer_keluar') AND t.foto IS NULL"
    elif st == "batal":
        where += " AND t.batal = 1"
    elif st == "dikoreksi":
        where += " AND t.updated_at IS NOT NULL"
    elif st == "belum_posting":
        where += f" AND {SQL_BELUM_POSTING}"
    elif st == "sudah_posting":
        where += " AND t.batal = 0 AND t.sap_posted = 1"
    elif st == "belum_kembali":
        where += f" AND {pengembalian.SQL_BELUM}"
    return where, prm


SQL_TRANSAKSI = """SELECT t.*, m.material, m.deskripsi, m.satuan, m.plant, m.storage_location, m.special_stock,
       m.special_stock_no, m.wajib_kembali, u.nama AS oleh, up.nama AS posting_oleh, ka.nama AS asal_nama, kt.nama AS tujuan_nama
FROM transactions t JOIN materials m ON m.id = t.material_id LEFT JOIN users u ON u.id = t.created_by
LEFT JOIN users up ON up.id = t.sap_posted_by
LEFT JOIN kebun ka ON ka.kode = t.kebun_asal LEFT JOIN kebun kt ON kt.kode = t.kebun_tujuan"""


@bp.route("/transaksi")
@login_required()
def transaksi():
    """Tampilkan riwayat transaksi terfilter beserta status dan aksinya."""
    db = get_db()
    where, prm = _filter_transaksi(request.args)
    total = db.execute(f"SELECT COUNT(*) FROM transactions t JOIN materials m ON m.id = t.material_id "
                       f"WHERE {where}", prm).fetchone()[0]
    rows = db.execute(f"{SQL_TRANSAKSI} WHERE {where} ORDER BY t.tanggal DESC, t.id DESC "
                      f"LIMIT {PER_HAL} OFFSET {(halaman() - 1) * PER_HAL}", prm).fetchall()
    return render_template("transaksi.html", rows=rows, total=total, hal=halaman(), per_hal=PER_HAL,
                           kebun=db.execute("SELECT * FROM kebun ORDER BY nama").fetchall(),
                           kembali=pengembalian.per_transaksi(db, [r["id"] for r in rows]),
                           kondisi=pengembalian.KONDISI)


@bp.route("/transaksi/export")
@login_required()
def export_transaksi():
    """Export transaksi sesuai filter aktif, termasuk tujuan pembebanan SAP."""
    where, prm = _filter_transaksi(request.args)
    rows = get_db().execute(f"{SQL_TRANSAKSI} WHERE {where} ORDER BY t.tanggal, t.id", prm).fetchall()
    data = []
    for t in rows:
        ke = (t["tujuan_nama"] or t["kebun_tujuan"]) if t["jenis"] == "transfer_keluar" else ""
        dari = (t["asal_nama"] or t["kebun_asal"]) if t["jenis"] == "transfer_masuk" else ""
        if t["batal"]:
            posting = "BATAL"
        elif t["sumber"] == "sinkron_sap":
            posting = "Sinkron SAP"
        else:
            posting = "Sudah" if t["sap_posted"] else "Belum"
        data.append([
            t["id"], t["bon"], dt.date.fromisoformat(t["tanggal"]), JENIS_LABEL[t["jenis"]], t["material"], t["deskripsi"],
            t["special_stock"], t["qty"], efek(t["jenis"], t["qty"]), t["satuan"], t["plant"], t["storage_location"],
            t["nomor_dokumen"], t["penerima"], dari, ke, t["tujuan_tipe"], t["tujuan_kode"],
            t["aktivitas_kode"], t["alasan"], t["keterangan"],
            posting, t["sap_doc"], t["sap_movement_type"], t["sap_posting_date"], t["sap_posted_at"],
            t["sumber"], t["oleh"], t["created_at"],
            "Ada" if t["foto"] else "", "Ada" if t["foto_2"] else "", t["batal_alasan"],
        ])
    header = ["ID", "Bon", "Tanggal", "Jenis", "Material", "Deskripsi", "Sp.Stock", "Qty", "Efek ke Stok", "Satuan",
              "Plant", "SLoc", "No. Dokumen", "Penerima", "Kebun Asal", "Kebun Tujuan", "Tipe Tujuan",
              "Kode Tujuan / Cost Center", "Aktivitas", "Alasan", "Keterangan",
              "Posting SAP", "No. Dok SAP", "Movement Type SAP", "Tanggal Posting SAP", "Waktu Posting SAP",
              "Sumber", "Diinput Oleh", "Waktu Input", "Foto 1", "Foto 2", "Alasan Batal"]
    widths = [6, 16, 11, 16, 14, 36, 8, 10, 11, 7, 7, 7, 16, 18, 18, 18, 14, 22, 10, 22, 28, 11, 14, 12,
              15, 18, 10, 16, 18, 7, 7, 18]
    nama = f"Transaksi_{request.args.get('dari') or 'awal'}_sd_{request.args.get('sampai') or hari_ini()}.xlsx"
    return excel_tabel("Transaksi", header, data, widths, nama, {3: "dd/mm/yyyy", 8: "#,##0.###", 9: "#,##0.###"})


@bp.route("/transaksi/posting", methods=["POST"])
@login_required("admin", "petugas")
def posting_sap():
    """Tandai transaksi sudah / belum diinput ke SAP."""
    db = get_db()
    ids = [int(i) for i in request.form.getlist("ids") if i.isdigit()]
    if not ids:
        flash("Centang transaksi terlebih dahulu", "error")
        return redirect(kembali(url_for("main.transaksi")))
    fk, fa = filter_kebun_user("m")
    batas = f"id IN ({','.join('?' * len(ids))}) AND material_id IN (SELECT m.id FROM materials m WHERE 1=1 {fk})"
    with tx(db):
        if request.form.get("aksi") == "batal":
            n = db.execute(f"UPDATE transactions SET sap_posted = 0, sap_posted_at = NULL, sap_posted_by = NULL "
                           f"WHERE sumber <> 'sinkron_sap' AND {batas}", ids + fa).rowcount
            pesan = f"{n} transaksi ditandai BELUM posting SAP"
        else:
            n = db.execute(f"""UPDATE transactions SET sap_posted = 1, sap_posted_at = datetime('now','localtime'),
                               sap_posted_by = ?, sap_doc = COALESCE(NULLIF(?, ''), sap_doc)
                               WHERE batal = 0 AND sap_posted = 0 AND {batas}""",
                           [g.user["id"], request.form.get("sap_doc", "").strip()] + ids + fa).rowcount
            pesan = f"{n} transaksi ditandai SUDAH posting SAP"
        audit(db, g.user["id"], "posting_sap", f"{pesan}: {ids[:50]}")
    flash(pesan, "ok")
    return redirect(kembali(url_for("main.transaksi")))


def _trx_boleh(tid):
    """Ambil transaksi bila materialnya berada dalam lingkup akses pengguna."""
    t = get_db().execute("SELECT * FROM transactions WHERE id = ?", (tid,)).fetchone()
    if not t or not material_boleh(t["material_id"]):
        abort(404)
    return t


@bp.route("/transaksi/<int:tid>/koreksi", methods=["POST"])
@login_required("admin")
def koreksi_transaksi(tid):
    """Koreksi qty transaksi aktif dengan alasan dan jejak audit."""
    t = _trx_boleh(tid)
    db = get_db()
    try:
        with tx(db):
            lama, baru = ubah_qty_transaksi(db, tid, angka(request.form.get("qty")),
                                            request.form.get("alasan", ""), g.user["id"])
        flash(f"Qty transaksi #{tid} dikoreksi. Stok {fmt(lama)} → {fmt(baru)}", "ok")
        if t["sap_posted"] and t["sumber"] != "sinkron_sap":
            flash(f"Transaksi #{tid} sudah diinput ke SAP ({t['sap_doc'] or 'tanpa no. dok'}). Koreksi juga qty-nya di "
                  f"SAP; sampai itu dilakukan, selisihnya akan tampil sebagai Selisih nyata setelah upload SAP.", "warn")
    except StockError as e:
        flash(str(e), "error")
    return redirect(kembali(url_for("main.transaksi")))


@bp.route("/transaksi/<int:tid>/kembali", methods=["POST"])
@login_required("admin", "petugas")
def catat_kembali(tid):
    """Catat barang lama yang dikembalikan untuk transaksi KELUAR (bisa menyusul setelah bon dibuat)."""
    t = _trx_boleh(tid)
    db = get_db()
    back = kembali(url_for("main.transaksi"))
    if t["jenis"] != "keluar" or t["batal"]:
        flash("Pengembalian barang lama hanya untuk transaksi KELUAR yang aktif", "error")
        return redirect(back)
    m = db.execute("SELECT * FROM materials WHERE id = ?", (t["material_id"],)).fetchone()
    data, err = pengembalian.baca(request.form.get("qty"), request.form.get("kondisi"), t["qty"], True,
                                  m["deskripsi"])
    if err or not data:
        flash(err or "Isi qty & kondisi barang lama", "error")
        return redirect(back)
    foto_lama = uploaded_photo("foto")
    if pengembalian.ada_barang(data) and not (foto_lama and foto_lama.filename):
        flash("Foto barang lama wajib karena ada barang lama yang dikembalikan", "error")
        return redirect(back)
    try:
        nama_foto = save_photo(foto_lama, metadata_time=request.form.get("foto_waktu_metadata"))
    except StockError as e:
        flash(str(e), "error")
        return redirect(back)
    with tx(db):
        pengembalian.simpan(db, tid, t["material_id"], request.form.get("tanggal") or hari_ini(), data[0], data[1],
                            request.form.get("keterangan", "").strip(), nama_foto,
                            request.form.get("oleh", "").strip() or t["penerima"], g.user["id"])
        audit(db, g.user["id"], "pengembalian", f"trx#{tid} {m['material']}: {fmt(data[0])} {data[1]}")
    flash(f"Pengembalian barang lama {m['deskripsi']} ({fmt(data[0])} {m['satuan']}, {data[1]}) tercatat "
          f"di {t['bon'] or 'transaksi #' + str(tid)}", "ok")
    return redirect(back)


@bp.route("/pengembalian/<int:pid>/batal", methods=["POST"])
@login_required("admin")
def batal_kembali(pid):
    """Batalkan catatan pengembalian tanpa menghapus bukti riwayatnya."""
    db = get_db()
    p = db.execute("SELECT * FROM pengembalian WHERE id = ? AND batal = 0", (pid,)).fetchone()
    if not p or not material_boleh(p["material_id"]):
        abort(404)
    with tx(db):
        db.execute("UPDATE pengembalian SET batal = 1 WHERE id = ?", (pid,))
        audit(db, g.user["id"], "batal_pengembalian", f"pengembalian#{pid} trx#{p['transaksi_id']}")
    flash("Catatan pengembalian dibatalkan", "ok")
    return redirect(kembali(url_for("main.daftar_kembali")))


def _filter_kembali(a):
    """Susun filter daftar pengembalian barang lama dan status tertundanya."""
    fk, fa = filter_kebun_user("m")
    where, prm = "p.batal = 0" + fk, list(fa)
    if a.get("dari"):
        where += " AND p.tanggal >= ?"
        prm.append(a["dari"])
    if a.get("sampai"):
        where += " AND p.tanggal <= ?"
        prm.append(a["sampai"])
    for kata in a.get("q", "").split()[:5]:
        where += " AND (m.material LIKE ? OR m.deskripsi LIKE ? OR p.dikembalikan_oleh LIKE ? OR t.bon LIKE ?)"
        prm += [f"%{kata}%"] * 4
    if a.get("kondisi"):
        where += " AND p.kondisi = ?"
        prm.append(a["kondisi"])
    return where, prm


SQL_KEMBALI = """SELECT p.*, t.bon, t.qty AS qty_keluar, t.tanggal AS tgl_keluar, t.penerima, t.keterangan AS ket_keluar,
       t.foto AS foto_bon, t.foto_2 AS foto_bon_2, m.material, m.deskripsi, m.satuan, u.nama AS dicatat_oleh
FROM pengembalian p JOIN transactions t ON t.id = p.transaksi_id JOIN materials m ON m.id = p.material_id
LEFT JOIN users u ON u.id = p.created_by"""


@bp.route("/pengembalian")
@login_required()
def daftar_kembali():
    """Tampilkan kewajiban dan riwayat pengembalian barang lama."""
    db = get_db()
    fk, fa = filter_kebun_user("m")
    belum = db.execute(f"""{SQL_TRANSAKSI} WHERE {pengembalian.SQL_BELUM} {fk}
                           ORDER BY t.tanggal DESC, t.id DESC LIMIT 300""", fa).fetchall()
    where, prm = _filter_kembali(request.args)
    rows = db.execute(f"{SQL_KEMBALI} WHERE {where} ORDER BY p.tanggal DESC, p.id DESC LIMIT 500", prm).fetchall()
    wajib = db.execute(f"SELECT COUNT(*) FROM materials m WHERE m.wajib_kembali = 1 {fk}", fa).fetchone()[0]
    return render_template("pengembalian.html", belum=belum, rows=rows, wajib=wajib, kondisi=pengembalian.KONDISI,
                           hari_ini=hari_ini())


@bp.route("/pengembalian/export")
@login_required()
def export_kembali():
    """Export daftar pengembalian barang lama sesuai filter aktif."""
    where, prm = _filter_kembali(request.args)
    rows = get_db().execute(f"{SQL_KEMBALI} WHERE {where} ORDER BY p.tanggal, p.id", prm).fetchall()
    data = [[dt.date.fromisoformat(r["tanggal"]), r["bon"], dt.date.fromisoformat(r["tgl_keluar"]), r["material"],
             r["deskripsi"], r["qty_keluar"], r["qty"], r["satuan"], r["kondisi"], r["dikembalikan_oleh"],
             r["keterangan"], "Ada" if r["foto"] else "", r["dicatat_oleh"]] for r in rows]
    header = ["Tgl Kembali", "Bon", "Tgl Keluar", "Material", "Deskripsi", "Qty Baru Keluar", "Qty Lama Kembali",
              "Satuan", "Kondisi", "Dikembalikan Oleh", "Keterangan", "Foto", "Dicatat Oleh"]
    return excel_tabel("Pengembalian", header, data, [11, 16, 11, 14, 36, 10, 10, 7, 22, 20, 28, 6, 16],
                       f"Pengembalian_Barang_{hari_ini()}.xlsx",
                       {1: "dd/mm/yyyy", 3: "dd/mm/yyyy", 6: "#,##0.###", 7: "#,##0.###"})


def _baris_bon(ids, aksi):
    """Ambil transaksi terpilih & periksa bisa dijadikan satu bon. Kembalikan (rows, pesan_error)."""
    fk, fa = filter_kebun_user("m")
    rows = get_db().execute(f"""SELECT t.*, m.plant FROM transactions t JOIN materials m ON m.id = t.material_id
                                WHERE t.id IN ({','.join('?' * len(ids))}) {fk} ORDER BY t.id""", ids + fa).fetchall()
    masalah = []
    if len(rows) != len(ids) or any(r["batal"] for r in rows):
        masalah.append("ada transaksi yang dibatalkan / tidak ditemukan")
    for kolom, nama in (("jenis", "jenis transaksi"), ("tanggal", "tanggal"), ("plant", "kebun"),
                        ("penerima", "nama penerima")):
        if len({(r[kolom] or "").strip().lower() for r in rows}) > 1:
            masalah.append(f"{nama} berbeda")
    if any(r["jenis"] == "penyesuaian" for r in rows):
        masalah.append("penyesuaian (Ubah Qty / opname) tidak memakai bon")
    return rows, (f"Tidak bisa {aksi}: " + "; ".join(masalah) + "." if masalah else None)


@bp.route("/transaksi/gabung-bon", methods=["POST"])
@login_required("admin")
def gabung_bon():
    """Satukan transaksi yang terlanjur terinput di bon berbeda ke satu bon (bon paling awal)."""
    db = get_db()
    ids = sorted({int(i) for i in request.form.getlist("ids") if i.isdigit()})
    back = kembali(url_for("main.transaksi"))
    if len(ids) < 2:
        flash("Centang minimal 2 transaksi yang mau disatukan", "error")
        return redirect(back)
    rows, err = _baris_bon(ids, "disatukan")
    if err:
        flash(err + " Samakan dulu lewat Edit bila memang satu bon.", "error")
        return redirect(back)
    with tx(db):
        target = next((r["bon"] for r in rows if r["bon"]), None) or _nomor_bon(db)
        foto = next((r["foto"] for r in rows if r["bon"] == target and r["foto"]), None) or \
            next((r["foto"] for r in rows if r["foto"]), None)
        foto_2 = next((r["foto_2"] for r in rows if r["bon"] == target and r["foto_2"]), None) or \
            next((r["foto_2"] for r in rows if r["foto_2"]), None)
        bon_lama = sorted({r["bon"] for r in rows if r["bon"] and r["bon"] != target})
        db.execute(f"UPDATE transactions SET bon = ? WHERE id IN ({','.join('?' * len(ids))})", [target] + ids)
        if foto:  # baris tanpa foto ikut memakai foto bon
            db.execute(f"UPDATE transactions SET foto = ? WHERE foto IS NULL AND id IN ({','.join('?' * len(ids))})",
                       [foto] + ids)
        if foto_2:
            db.execute(f"UPDATE transactions SET foto_2 = ? WHERE foto_2 IS NULL AND id IN ({','.join('?' * len(ids))})",
                       [foto_2] + ids)
        audit(db, g.user["id"], "gabung_bon",
              f"trx {ids} disatukan ke {target}" + (f" (dari {', '.join(bon_lama)})" if bon_lama else ""))
    flash(f"{len(ids)} transaksi disatukan ke bon {target}.", "ok")
    return redirect(url_for("main.transaksi", bon=target))


@bp.route("/transaksi/pisah-bon", methods=["POST"])
@login_required("admin")
def pisah_bon():
    """Keluarkan transaksi terpilih dari bonnya menjadi bon baru (mis. salah tergabung dengan bon orang lain)."""
    db = get_db()
    ids = sorted({int(i) for i in request.form.getlist("ids") if i.isdigit()})
    back = kembali(url_for("main.transaksi"))
    if not ids:
        flash("Centang transaksi yang mau dipisahkan menjadi bon baru", "error")
        return redirect(back)
    rows, err = _baris_bon(ids, "dipisahkan menjadi satu bon baru")
    if err:
        flash(err + " Pisahkan per penerima / per jenis.", "error")
        return redirect(back)
    with tx(db):
        baru = _nomor_bon(db)
        asal = sorted({r["bon"] for r in rows if r["bon"]})
        db.execute(f"UPDATE transactions SET bon = ? WHERE id IN ({','.join('?' * len(ids))})", [baru] + ids)
        audit(db, g.user["id"], "pisah_bon",
              f"trx {ids} dipisahkan ke {baru}" + (f" (dari {', '.join(asal)})" if asal else ""))
    flash(f"{len(ids)} transaksi dipisahkan menjadi bon baru {baru}.", "ok")
    return redirect(url_for("main.transaksi", bon=baru))


@bp.route("/transaksi/<int:tid>/edit", methods=["POST"])
@login_required("admin")
def edit_transaksi(tid):
    """Perbaiki salah tulis keterangan / penerima / nomor dokumen (qty lewat Koreksi)."""
    t = _trx_boleh(tid)
    db = get_db()
    baru = {k: request.form.get(k, "").strip() or None for k in ("keterangan", "penerima", "nomor_dokumen")}
    if t["jenis"] in ("transfer_masuk", "transfer_keluar") and not baru["nomor_dokumen"]:
        flash("Nomor dokumen transfer wajib diisi", "error")
        return redirect(kembali(url_for("main.transaksi")))
    if t["jenis"] in ("keluar", "transfer_keluar") and t["sumber"] == "hp" and not baru["penerima"]:
        flash("Nama penerima wajib diisi untuk transaksi keluar", "error")
        return redirect(kembali(url_for("main.transaksi")))
    semua_bon = bool(t["bon"]) and request.form.get("semua_bon") == "1"
    with tx(db):
        if semua_bon:
            target = db.execute("SELECT * FROM transactions WHERE bon = ? AND batal = 0", (t["bon"],)).fetchall()
        else:
            target = [t]
        for r in target:
            beda = [f"{k}: '{r[k] or ''}' → '{baru[k] or ''}'" for k in baru if (r[k] or None) != baru[k]]
            if not beda:
                continue
            db.execute("UPDATE transactions SET keterangan = ?, penerima = ?, nomor_dokumen = ? WHERE id = ?",
                       (baru["keterangan"], baru["penerima"], baru["nomor_dokumen"], r["id"]))
            audit(db, g.user["id"], "edit_transaksi", f"trx#{r['id']} " + "; ".join(beda))
    flash(f"Keterangan {'bon ' + t['bon'] if semua_bon else 'transaksi #' + str(tid)} diperbarui", "ok")
    return redirect(kembali(url_for("main.transaksi")))


@bp.route("/transaksi/<int:tid>/batal", methods=["POST"])
@login_required("admin")
def batal_transaksi(tid):
    """Batalkan transaksi dan, bila dipilih, seluruh item dalam BON yang sama."""
    t = _trx_boleh(tid)
    db = get_db()
    try:
        with tx(db):
            lama, baru = batalkan_transaksi(db, tid, request.form.get("alasan", ""), g.user["id"])
        flash(f"Transaksi #{tid} dibatalkan. Stok {fmt(lama)} → {fmt(baru)}", "ok")
        if t["sap_posted"] and t["sumber"] != "sinkron_sap":
            flash(f"Transaksi #{tid} sudah diinput ke SAP ({t['sap_doc'] or 'tanpa no. dok'}). Batalkan/reverse juga "
                  f"di SAP; sampai itu dilakukan, selisihnya akan tampil sebagai Selisih nyata setelah upload SAP.", "warn")
    except StockError as e:
        flash(str(e), "error")
    return redirect(kembali(url_for("main.transaksi")))


@bp.route("/transaksi/<int:tid>/foto", methods=["POST"])
@login_required("admin", "petugas")
def foto_transaksi(tid):
    """Lampirkan foto pertama/kedua ke transaksi dan item BON terkait."""
    _trx_boleh(tid)
    try:
        nama = save_photo(uploaded_photo("foto"), metadata_time=request.form.get("foto_waktu_metadata"))
        if not nama:
            raise StockError("Pilih foto terlebih dahulu")
        slot = 2 if request.form.get("slot") == "2" else 1
        kolom = "foto_2" if slot == 2 else "foto"
        db = get_db()
        with tx(db):
            t = db.execute("SELECT bon FROM transactions WHERE id = ?", (tid,)).fetchone()
            db.execute(f"UPDATE transactions SET {kolom} = ? WHERE id = ?", (nama, tid))
            if t["bon"]:  # satu pasang foto bon berlaku untuk semua barang dalam bon yang sama
                db.execute(f"UPDATE transactions SET {kolom} = ? WHERE bon = ? AND {kolom} IS NULL", (nama, t["bon"]))
            audit(db, g.user["id"], "lampir_foto", f"trx#{tid} foto {slot}: {nama}")
        flash(f"Foto {slot} transaksi #{tid} tersimpan", "ok")
    except StockError as e:
        flash(str(e), "error")
    return redirect(kembali(url_for("main.transaksi")))


@bp.route("/foto/<path:nama>")
@login_required()
def foto(nama):
    """Kirim file foto bukti kepada pengguna yang sudah login."""
    return send_from_directory(current_app.config["UPLOAD_FOLDER"], nama)
