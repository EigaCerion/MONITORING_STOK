"""Logika inti stok: validasi & posting transaksi, penyesuaian qty, edit/batal transaksi."""
import datetime as dt
import re

from .db import audit

JENIS = ["masuk", "keluar", "transfer_masuk", "transfer_keluar", "penyesuaian"]
JENIS_LABEL = {
    "masuk": "MASUK",
    "keluar": "KELUAR",
    "transfer_masuk": "TRANSFER MASUK",
    "transfer_keluar": "TRANSFER KELUAR",
    "penyesuaian": "PENYESUAIAN",
}
JENIS_KELUAR = ("keluar", "transfer_keluar")

ALASAN_SAP = "Samakan dengan SAP"
ALASAN_PENYESUAIAN = [
    "Stock opname (hitung fisik)",
    ALASAN_SAP,
    "Koreksi salah input",
    "Barang rusak / hilang",
    "Lainnya",
]

EPS = 1e-9


class StockError(ValueError):
    pass


def efek(jenis, qty):
    """Efek bertanda transaksi terhadap stok."""
    if jenis in ("masuk", "transfer_masuk"):
        return qty
    if jenis in JENIS_KELUAR:
        return -qty
    return qty  # penyesuaian sudah bertanda


def bulat(x):
    """Bulatkan qty ke presisi stok aplikasi untuk menghindari noise floating point."""
    return round(float(x), 3)


def target_sap(db, material_id):
    """Stok target aplikasi menurut SAP + transaksi belum posting, paling rendah nol."""
    r = db.execute("SELECT sap_perkiraan, pending_sap FROM v_stok WHERE id = ?", (material_id,)).fetchone()
    if not r or r["sap_perkiraan"] is None:
        return None
    return max(bulat(r["sap_perkiraan"] + r["pending_sap"]), 0.0)


def stok_material(db, material_id):
    """Baca stok berjalan satu material dari view stok dasar."""
    row = db.execute("SELECT stok FROM v_stok WHERE id = ?", (material_id,)).fetchone()
    return bulat(row["stok"]) if row else 0.0


def _norm_kebun(s):
    """Normalisasi nama/kode kebun agar pencocokan teks tidak sensitif format."""
    s = re.sub(r"\s+", " ", (s or "").strip().upper())
    return re.sub(r"^KEBUN\s+", "", s)


def cari_kebun(db, teks):
    """Cocokkan teks dengan kode atau nama kebun (abaikan huruf besar & awalan 'KEBUN')."""
    if not teks or not str(teks).strip():
        return None
    target = _norm_kebun(str(teks))
    for k in db.execute("SELECT kode, nama FROM kebun"):
        if _norm_kebun(k["kode"]) == target or _norm_kebun(k["nama"]) == target:
            return k["kode"]
    return None


def validasi(jenis, qty, stok, nomor_dokumen="", kebun_asal=None, kebun_tujuan=None,
             plant="", penerima="", ada_foto=True, wajib_foto=True, wajib_penerima=False):
    """Kembalikan daftar pesan error (kosong = valid). kebun_* sudah berupa kode kebun."""
    err = []
    if jenis not in JENIS or jenis == "penyesuaian":
        err.append("Jenis transaksi tidak dikenal")
        return err
    if qty is None or qty <= 0:
        err.append("Qty harus lebih dari 0")
    if jenis in ("transfer_masuk", "transfer_keluar") and not (nomor_dokumen or "").strip():
        err.append("Nomor dokumen transfer wajib diisi")
    if jenis == "transfer_keluar":
        if not kebun_tujuan:
            err.append("Kebun tujuan wajib diisi")
        elif kebun_tujuan == plant:
            err.append("Kebun tujuan tidak boleh sama dengan kebun asal")
    if jenis == "transfer_masuk":
        if not kebun_asal:
            err.append("Kebun asal wajib diisi")
        elif kebun_asal == plant:
            err.append("Kebun asal tidak boleh sama dengan kebun penerima")
    if jenis in JENIS_KELUAR:
        if wajib_foto and not ada_foto:
            err.append("Foto bukti wajib untuk KELUAR / TRANSFER KELUAR")
        if wajib_penerima and not (penerima or "").strip():
            err.append("Nama penerima wajib diisi")
        if qty is not None and qty > 0 and qty > stok + EPS:
            err.append(f"Qty {fmt(qty)} melebihi stok tersedia {fmt(stok)}")
    return err


def fmt(x):
    """Format angka ringkas memakai aturan tampilan Indonesia tanpa nol berlebih."""
    try:
        x = bulat(x)
    except Exception:  # None / nilai tak terdefinisi di template -> kosong, jangan bikin halaman error
        return ""
    s = f"{x:,.3f}".rstrip("0").rstrip(".")
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def simpan_transaksi(db, d, user_id):
    """Insert transaksi (dipanggil di dalam blok tx). d = dict kolom transaksi."""
    m = db.execute("SELECT plant FROM materials WHERE id = ?", (d["material_id"],)).fetchone()
    jenis = d["jenis"]
    sinkron = d.get("sumber") == "sinkron_sap"  # menyamakan dengan SAP = otomatis dianggap sudah di SAP
    kebun_asal, kebun_tujuan = d.get("kebun_asal"), d.get("kebun_tujuan")
    if jenis == "transfer_keluar":
        kebun_asal = m["plant"]
    elif jenis == "transfer_masuk":
        kebun_tujuan = m["plant"]
    cur = db.execute(
        """INSERT INTO transactions (tanggal, jenis, material_id, qty, nomor_dokumen, penerima,
               kebun_asal, kebun_tujuan, keterangan, alasan, foto, foto_2, sumber, import_ref, created_by,
               sap_posted, sap_posted_at, sap_posted_by, bon, tujuan_tipe, tujuan_kode, aktivitas_kode)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,CASE WHEN ? THEN datetime('now','localtime') END,?,?,?,?,?)""",
        (d["tanggal"], jenis, d["material_id"], bulat(d["qty"]), d.get("nomor_dokumen") or None,
         d.get("penerima") or None, kebun_asal, kebun_tujuan, d.get("keterangan") or None,
         d.get("alasan"), d.get("foto"), d.get("foto_2"), d.get("sumber", "manual"), d.get("import_ref"), user_id,
         1 if sinkron else 0, sinkron, user_id if sinkron else None, d.get("bon"),
         d.get("tujuan_tipe") or None, d.get("tujuan_kode") or None, d.get("aktivitas_kode") or None),
    )
    return cur.lastrowid


def nomor_bon(db):
    """Buat nomor BON harian yang tidak dipakai ulang walau transaksi dibatalkan/digabung.

    Fungsi harus dipanggil di dalam ``with tx(db)`` agar dua perangkat yang
    sinkron bersamaan tidak memperoleh nomor yang sama.
    """
    awal = f"BON-{dt.date.today():%y%m%d}-"
    row = db.execute("SELECT nilai FROM penomoran WHERE kunci = ?", (awal,)).fetchone()
    if row:
        nilai = row["nilai"]
    else:
        terpakai = [int(r[0]) for r in db.execute(
            "SELECT CAST(SUBSTR(bon, ?) AS INTEGER) FROM transactions WHERE bon LIKE ?",
            (len(awal) + 1, awal + "%"),
        ) if r[0]]
        for (detail,) in db.execute("SELECT detail FROM audit_log WHERE detail LIKE ?", (f"%{awal}%",)):
            terpakai += [int(value) for value in re.findall(re.escape(awal) + r"(\d+)", detail or "")]
        nilai = max(terpakai, default=0)
    nilai += 1
    db.execute(
        "INSERT INTO penomoran (kunci, nilai) VALUES (?, ?) "
        "ON CONFLICT(kunci) DO UPDATE SET nilai = ?",
        (awal, nilai, nilai),
    )
    return f"{awal}{nilai:03d}"


def sesuaikan_stok(db, material_id, qty_baru, alasan, keterangan, tanggal, user_id, sumber=None):
    """Ubah stok material menjadi qty_baru dengan mencatat transaksi PENYESUAIAN sebesar selisihnya."""
    sumber = sumber or ("sinkron_sap" if alasan == ALASAN_SAP else "penyesuaian")
    if qty_baru is None or qty_baru < 0:
        raise StockError("Qty baru tidak boleh negatif")
    if not (alasan or "").strip():
        raise StockError("Alasan penyesuaian wajib diisi")
    stok = stok_material(db, material_id)
    selisih = bulat(qty_baru - stok)
    if abs(selisih) < EPS:
        raise StockError("Qty baru sama dengan stok sekarang, tidak ada perubahan")
    tid = simpan_transaksi(db, {
        "tanggal": tanggal, "jenis": "penyesuaian", "material_id": material_id, "qty": selisih,
        "alasan": alasan, "keterangan": keterangan, "sumber": sumber,
    }, user_id)
    audit(db, user_id, "penyesuaian_stok",
          f"material_id={material_id} stok {fmt(stok)} -> {fmt(qty_baru)} (selisih {fmt(selisih)}) alasan: {alasan}")
    return tid, stok, selisih


def ubah_qty_transaksi(db, trx_id, qty_baru, alasan, user_id):
    """Koreksi qty transaksi yang sudah tercatat; stok tidak boleh menjadi minus."""
    t = db.execute("SELECT * FROM transactions WHERE id = ?", (trx_id,)).fetchone()
    if not t or t["batal"]:
        raise StockError("Transaksi tidak ditemukan atau sudah dibatalkan")
    if not (alasan or "").strip():
        raise StockError("Alasan koreksi wajib diisi")
    if t["jenis"] == "penyesuaian":
        if qty_baru is None or abs(qty_baru) < EPS:
            raise StockError("Qty penyesuaian tidak boleh 0")
    elif qty_baru is None or qty_baru <= 0:
        raise StockError("Qty harus lebih dari 0")
    stok = stok_material(db, t["material_id"])
    stok_baru = bulat(stok - efek(t["jenis"], t["qty"]) + efek(t["jenis"], qty_baru))
    if stok_baru < -EPS:
        raise StockError(f"Koreksi ditolak: stok akan menjadi minus ({fmt(stok_baru)})")
    db.execute("UPDATE transactions SET qty = ?, updated_by = ?, updated_at = datetime('now','localtime') "
               "WHERE id = ?", (bulat(qty_baru), user_id, trx_id))
    audit(db, user_id, "koreksi_qty_transaksi",
          f"trx#{trx_id} qty {fmt(t['qty'])} -> {fmt(qty_baru)}; stok {fmt(stok)} -> {fmt(stok_baru)}; alasan: {alasan}")
    return stok, stok_baru


def batalkan_transaksi(db, trx_id, alasan, user_id):
    """Batalkan transaksi aktif secara auditabel tanpa menghapus riwayatnya."""
    t = db.execute("SELECT * FROM transactions WHERE id = ?", (trx_id,)).fetchone()
    if not t or t["batal"]:
        raise StockError("Transaksi tidak ditemukan atau sudah dibatalkan")
    if not (alasan or "").strip():
        raise StockError("Alasan pembatalan wajib diisi")
    stok = stok_material(db, t["material_id"])
    stok_baru = bulat(stok - efek(t["jenis"], t["qty"]))
    if stok_baru < -EPS:
        raise StockError(f"Pembatalan ditolak: stok akan menjadi minus ({fmt(stok_baru)})")
    db.execute("UPDATE transactions SET batal = 1, batal_alasan = ?, updated_by = ?, "
               "updated_at = datetime('now','localtime') WHERE id = ?", (alasan, user_id, trx_id))
    audit(db, user_id, "batal_transaksi", f"trx#{trx_id} ({t['jenis']} {fmt(t['qty'])}) alasan: {alasan}")
    return stok, stok_baru
