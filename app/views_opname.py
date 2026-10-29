"""Stock opname bersama, riwayat, Berita Acara, dan pengaturan laporan."""
import datetime as dt

from flask import Blueprint, abort, flash, g, redirect, render_template, request, send_file, url_for

from .auth import login_required
from .berita_acara import buat_excel, selisih
from .db import DEFAULT_PENGATURAN, audit, delegasi_opname_aktif, get_db, opname_aktif, tx
from .db import pengaturan as ambil_pengaturan
from .excel_io import angka
from .stock import ALASAN_PENYESUAIAN, EPS, bulat, sesuaikan_stok

bp = Blueprint("opname", __name__, url_prefix="/opname")

SQL_ITEM = """SELECT i.*, s.tanggal, s.jenis_barang, s.lokasi, s.keterangan AS ket_sesi, m.material, m.deskripsi,
       m.satuan, m.plant, m.special_stock, t.sap_posted AS trx_sap_posted, t.sap_doc AS trx_sap_doc,
       t.batal AS trx_batal, uc.nama AS dihitung_oleh
FROM opname_item i JOIN opname_sesi s ON s.id = i.sesi_id JOIN materials m ON m.id = i.material_id
LEFT JOIN transactions t ON t.id = i.trx_id LEFT JOIN users uc ON uc.id = i.counted_by"""


def _filter_kebun():
    """Kembalikan filter SQL opname sesuai kebun pengguna yang login."""
    if g.user["kebun_kode"]:
        return " AND m.plant = ?", [g.user["kebun_kode"]]
    return "", []


def _items(where, args):
    """Ambil material opname berikut stok buku dan status penghitungannya."""
    fk, fa = _filter_kebun()
    rows = get_db().execute(f"{SQL_ITEM} WHERE {where}{fk} ORDER BY s.tanggal, s.jenis_barang, i.id",
                            list(args) + fa).fetchall()
    return [dict(r) for r in rows]


def _sesi_boleh(sid, harus_terbuka=False):
    """Ambil sesi opname yang dapat diakses dan opsional masih terbuka."""
    sesi = get_db().execute(
        """SELECT s.*, u.nama AS oleh, uc.nama AS ditutup_oleh, COALESCE(k.nama, s.plant) AS kebun_nama
           FROM opname_sesi s LEFT JOIN users u ON u.id = s.created_by LEFT JOIN users uc ON uc.id = s.closed_by
           LEFT JOIN kebun k ON k.kode = s.plant WHERE s.id = ?""", (sid,)).fetchone() or abort(404)
    if g.user["kebun_kode"] and sesi["plant"] != g.user["kebun_kode"]:
        abort(403)
    if g.user["role"] == "petugas" and not g.user["kebun_kode"]:
        abort(403)
    if harus_terbuka and sesi["status"] != "terbuka":
        flash("Sesi opname ini sudah ditutup.", "warn")
        return None
    return sesi


def _boleh_menutup(sesi):
    """Admin selalu boleh; petugas hanya sesi buatannya saat delegasi masih aktif."""
    if g.user["role"] == "admin":
        return True
    return bool(
        g.user["role"] == "petugas"
        and sesi["created_by"] == g.user["id"]
        and delegasi_opname_aktif(get_db(), g.user["id"], sesi["plant"])
    )


@bp.route("/mulai", methods=["POST"])
@login_required("admin", "petugas")
def mulai():
    """Buka sesi bersama oleh admin atau petugas dengan delegasi sementara."""
    db = get_db()
    if g.user["role"] == "petugas":
        plant = (g.user["kebun_kode"] or "").strip().upper()
        delegasi = delegasi_opname_aktif(db, g.user["id"], plant)
        if not delegasi:
            flash("Delegasi membuka sesi opname tidak tersedia atau sudah kedaluwarsa.", "error")
            return redirect(url_for("main.opname"))
    else:
        plant = request.form.get("plant", "").strip().upper()
    kebun = db.execute("SELECT * FROM kebun WHERE kode = ? AND aktif = 1", (plant,)).fetchone()
    if not kebun:
        flash("Pilih gudang/kebun yang akan diopname.", "error")
        return redirect(url_for("main.opname"))
    tgl = request.form.get("tanggal") or dt.date.today().isoformat()
    if tgl > dt.date.today().isoformat():
        flash("Tanggal opname tidak boleh di masa depan.", "error")
        return redirect(url_for("main.opname"))
    with tx(db):
        if g.user["role"] == "petugas" and not delegasi_opname_aktif(db, g.user["id"], plant):
            flash("Delegasi berakhir atau dicabut sebelum sesi sempat dibuka.", "error")
            return redirect(url_for("main.opname"))
        aktif = opname_aktif(db, plant)
        if aktif:
            sid = aktif["id"]
        else:
            sid = db.execute(
                """INSERT INTO opname_sesi
                   (tanggal, jenis_barang, lokasi, plant, status, keterangan, created_by)
                   VALUES (?,?,?,?, 'terbuka', ?,?)""",
                (tgl, request.form.get("jenis_barang", "").strip().upper() or "UMUM",
                 request.form.get("lokasi", "").strip().upper() or kebun["nama"],
                 plant, request.form.get("keterangan", "").strip(), g.user["id"])).lastrowid
            sumber = " · memakai delegasi sementara" if g.user["role"] == "petugas" else ""
            audit(db, g.user["id"], "mulai_opname_bersama", f"sesi #{sid} gudang {plant}{sumber}")
    if aktif:
        flash(f"Gudang {plant} sudah memiliki sesi opname terbuka #{sid}.", "warn")
    else:
        flash(f"Sesi opname bersama #{sid} dibuka untuk {kebun['nama']}. Transaksi gudang dikunci sementara.", "ok")
    return redirect(url_for("opname.hitung", sid=sid))


@bp.route("/delegasi", methods=["POST"])
@login_required("admin")
def delegasi():
    """Berikan atau cabut hak sementara petugas untuk membuka sesi opname."""
    db = get_db()
    uid = request.form.get("user_id", type=int) or 0
    petugas = db.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()

    if request.form.get("aksi") == "cabut":
        if not petugas:
            flash("Petugas tidak ditemukan.", "error")
            return redirect(url_for("main.opname") + "#delegasi")
        with tx(db):
            db.execute("DELETE FROM opname_delegasi WHERE user_id = ?", (uid,))
            audit(db, g.user["id"], "cabut_delegasi_opname",
                  f"{petugas['username']} · gudang {petugas['kebun_kode']}")
        flash(f"Delegasi opname untuk {petugas['nama']} dicabut.", "ok")
        return redirect(url_for("main.opname") + "#delegasi")

    if not petugas or petugas["role"] != "petugas" or not petugas["aktif"] or not petugas["kebun_kode"]:
        flash("Pilih petugas aktif yang sudah memiliki gudang/kebun.", "error")
        return redirect(url_for("main.opname") + "#delegasi")

    raw_sampai = request.form.get("valid_until", "").strip()
    try:
        sampai = dt.datetime.fromisoformat(raw_sampai)
        if sampai.tzinfo is not None:
            raise ValueError
    except (TypeError, ValueError):
        flash("Isi batas waktu delegasi yang valid.", "error")
        return redirect(url_for("main.opname") + "#delegasi")
    sekarang = dt.datetime.now()
    if sampai <= sekarang:
        flash("Batas waktu delegasi harus setelah waktu sekarang.", "error")
        return redirect(url_for("main.opname") + "#delegasi")
    if sampai > sekarang + dt.timedelta(days=7):
        flash("Delegasi opname maksimal tujuh hari.", "error")
        return redirect(url_for("main.opname") + "#delegasi")
    sampai_db = sampai.replace(second=0, microsecond=0).strftime("%Y-%m-%d %H:%M:%S")
    with tx(db):
        db.execute(
            """INSERT INTO opname_delegasi (user_id, plant, valid_until, granted_by)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET plant = excluded.plant,
                 valid_until = excluded.valid_until, granted_by = excluded.granted_by,
                 granted_at = datetime('now','localtime')""",
            (uid, petugas["kebun_kode"], sampai_db, g.user["id"]),
        )
        audit(db, g.user["id"], "beri_delegasi_opname",
              f"{petugas['username']} · gudang {petugas['kebun_kode']} · sampai {sampai_db}")
    flash(f"{petugas['nama']} dapat membuka sesi opname sampai {sampai:%d/%m/%Y %H:%M}.", "ok")
    return redirect(url_for("main.opname") + "#delegasi")


@bp.route("/<int:sid>/hitung", methods=["GET", "POST"])
@login_required("admin", "petugas")
def hitung(sid):
    """Beberapa petugas mengisi material berbeda di sesi gudang yang sama."""
    db = get_db()
    sesi = _sesi_boleh(sid, harus_terbuka=True)
    if not sesi:
        return redirect(url_for("opname.detail", sid=sid))
    if request.method == "POST":
        isi, gagal = [], []
        for key, val in request.form.items():
            if not key.startswith("fisik_") or not val.strip():
                continue
            mid = int(key[6:]) if key[6:].isdigit() else 0
            qty = angka(val)
            if qty is None or qty < 0:
                gagal.append(f"#{mid}: '{val}' bukan qty fisik yang valid")
            else:
                isi.append((mid, qty, request.form.get(f"ket_{mid}", "").strip()))
        if not isi:
            flash("Belum ada qty fisik yang diisi.", "error")
            return redirect(url_for("opname.hitung", sid=sid))
        tersimpan = 0
        with tx(db):
            if db.execute("SELECT status FROM opname_sesi WHERE id = ?", (sid,)).fetchone()["status"] != "terbuka":
                flash("Sesi sudah ditutup oleh admin; hitungan tidak disimpan.", "error")
                return redirect(url_for("opname.detail", sid=sid))
            for mid, qty, ket in isi:
                m = db.execute("SELECT * FROM v_stok WHERE id = ? AND plant = ?", (mid, sesi["plant"])).fetchone()
                if not m:
                    gagal.append(f"Material #{mid} bukan bagian gudang {sesi['plant']}")
                    continue
                lama = db.execute("SELECT * FROM opname_item WHERE sesi_id = ? AND material_id = ?",
                                  (sid, mid)).fetchone()
                if lama and lama["counted_by"] not in (None, g.user["id"]):
                    nama = db.execute("SELECT nama FROM users WHERE id = ?", (lama["counted_by"],)).fetchone()
                    gagal.append(f"{m['deskripsi']} sudah dihitung oleh {nama['nama'] if nama else 'petugas lain'}")
                    continue
                if lama:
                    db.execute("""UPDATE opname_item SET fisik = ?, keterangan = ?, counted_by = ?,
                                  counted_at = datetime('now','localtime') WHERE id = ?""",
                               (bulat(qty), ket or None, g.user["id"], lama["id"]))
                else:
                    db.execute("""INSERT INTO opname_item
                                  (sesi_id, material_id, stok_buku, stok_sap, fisik, keterangan, counted_by, counted_at)
                                  VALUES (?,?,?,?,?,?,?,datetime('now','localtime'))""",
                               (sid, mid, bulat(m["stok"]), m["sap_perkiraan"], bulat(qty), ket or None, g.user["id"]))
                tersimpan += 1
            if tersimpan:
                audit(db, g.user["id"], "hitung_opname_bersama", f"sesi #{sid}: {tersimpan} material")
        if tersimpan:
            flash(f"{tersimpan} hitungan Anda tersimpan di sesi #{sid}.", "ok")
        for pesan in gagal:
            flash(pesan, "error")
        return redirect(url_for("opname.hitung", sid=sid, saved=1))

    q = request.args.get("q", "").strip()
    kategori = request.args.get("kategori", "")
    where, args = "v.plant = ?", [sesi["plant"]]
    for kata in q.split()[:5]:
        where += " AND (v.material LIKE ? OR v.deskripsi LIKE ?)"
        args += [f"%{kata}%", f"%{kata}%"]
    if kategori == "none":
        where += " AND v.kategori_id IS NULL"
    elif kategori.isdigit():
        where += " AND v.kategori_id = ?"
        args.append(int(kategori))
    total = db.execute("SELECT COUNT(*) FROM v_stok v WHERE plant = ?", (sesi["plant"],)).fetchone()[0]
    rows = db.execute(
        f"""SELECT v.*, k.nama AS kategori FROM v_stok v LEFT JOIN kategori k ON k.id = v.kategori_id
            WHERE {where} ORDER BY COALESCE(k.urutan,999), v.deskripsi LIMIT 500""", args).fetchall()
    items = db.execute(
        """SELECT i.*, u.nama AS dihitung_oleh FROM opname_item i LEFT JOIN users u ON u.id = i.counted_by
           WHERE i.sesi_id = ? ORDER BY i.id""", (sid,)).fetchall()
    anggota = db.execute(
        """SELECT i.counted_by, COALESCE(u.nama, 'Tidak diketahui') AS nama, COUNT(*) AS n,
                  MAX(i.counted_at) AS terakhir_simpan
           FROM opname_item i LEFT JOIN users u ON u.id = i.counted_by
           WHERE i.sesi_id = ? GROUP BY i.counted_by, u.nama ORDER BY u.nama""", (sid,)
    ).fetchall()
    terhitung = {r["material_id"]: r for r in items}
    kelompok = db.execute("SELECT * FROM kategori ORDER BY urutan").fetchall()
    return render_template("opname_hitung.html", sesi=sesi, rows=rows, total=total, terhitung=terhitung,
                           n_hitung=len(items), n_petugas=len({r["counted_by"] for r in items if r["counted_by"]}),
                           kategori=kelompok, anggota=anggota, bisa_menutup=_boleh_menutup(sesi))


@bp.route("/<int:sid>/selesai", methods=["POST"])
@login_required("admin", "petugas")
def selesai(sid):
    """Tutup sesi dan posting selisih setelah seluruh anggota dikonfirmasi selesai."""
    db = get_db()
    sesi = _sesi_boleh(sid, harus_terbuka=True)
    if not sesi:
        return redirect(url_for("opname.detail", sid=sid))
    if not _boleh_menutup(sesi):
        abort(403)
    if request.form.get("konfirmasi_semua") != "1":
        flash("Sesi belum ditutup. Pastikan semua anggota sudah menyimpan hitungannya lalu centang konfirmasi.",
              "error")
        return redirect(url_for("opname.hitung", sid=sid) + "#tutup-sesi")
    with tx(db):
        status = db.execute("SELECT status FROM opname_sesi WHERE id = ?", (sid,)).fetchone()
        if not status or status["status"] != "terbuka":
            flash("Sesi sudah ditutup oleh pengguna lain.", "warn")
            return redirect(url_for("opname.detail", sid=sid))
        items = db.execute(
            """SELECT i.*, m.deskripsi FROM opname_item i JOIN materials m ON m.id = i.material_id
               WHERE i.sesi_id = ? ORDER BY i.id""", (sid,)).fetchall()
        if not items:
            flash("Sesi belum memiliki hitungan barang.", "error")
            return redirect(url_for("opname.hitung", sid=sid))
        berubah = []
        for it in items:
            kini = db.execute("SELECT stok FROM v_stok WHERE id = ?", (it["material_id"],)).fetchone()
            if not kini or abs(kini["stok"] - it["stok_buku"]) >= EPS:
                berubah.append(it["deskripsi"])
        if berubah:
            flash("Sesi belum dapat ditutup karena stok aplikasi berubah setelah dihitung: "
                  + ", ".join(berubah[:5]) + (" …" if len(berubah) > 5 else ""), "error")
            return redirect(url_for("opname.hitung", sid=sid))
        n_selisih = 0
        for it in items:
            trx = None
            if abs(it["fisik"] - it["stok_buku"]) >= EPS:
                trx, _, _ = sesuaikan_stok(
                    db, it["material_id"], it["fisik"], ALASAN_PENYESUAIAN[0],
                    " · ".join(x for x in (sesi["keterangan"], it["keterangan"]) if x)
                    or f"Opname bersama #{sid}", sesi["tanggal"], g.user["id"])
                n_selisih += 1
            db.execute("UPDATE opname_item SET trx_id = ? WHERE id = ?", (trx, it["id"]))
        db.execute("""UPDATE opname_sesi SET status = 'selesai', closed_by = ?,
                      closed_at = datetime('now','localtime') WHERE id = ?""", (g.user["id"], sid))
        audit(db, g.user["id"], "selesai_opname_bersama",
              f"sesi #{sid}: {len(items)} dihitung, {n_selisih} penyesuaian")
    flash(f"Sesi opname #{sid} selesai: {len(items)} material dihitung, {n_selisih} disesuaikan.", "ok")
    return redirect(url_for("opname.detail", sid=sid))


@bp.route("/riwayat")
@login_required("admin", "petugas", "pimpinan")
def riwayat():
    """Tampilkan riwayat sesi opname sesuai filter kebun pengguna."""
    fk, fa = _filter_kebun()
    rows = get_db().execute(
        f"""SELECT s.*, u.nama AS oleh, COUNT(i.id) AS n,
                   SUM(CASE WHEN ABS(i.stok_buku - i.fisik) > 0.0005 THEN 1 ELSE 0 END) AS n_selisih,
                   SUM(CASE WHEN i.trx_id IS NOT NULL AND t.sap_posted = 0 AND t.batal = 0 THEN 1 ELSE 0 END)
                       AS n_belum_sap
            FROM opname_sesi s JOIN opname_item i ON i.sesi_id = s.id JOIN materials m ON m.id = i.material_id
            LEFT JOIN transactions t ON t.id = i.trx_id LEFT JOIN users u ON u.id = s.created_by
            WHERE s.status = 'selesai' {fk} GROUP BY s.id ORDER BY s.tanggal DESC, s.id DESC LIMIT 300""", fa).fetchall()
    return render_template("opname_riwayat.html", rows=rows)


@bp.route("/<int:sid>")
@login_required("admin", "petugas", "pimpinan")
def detail(sid):
    """Tampilkan rincian hasil hitung dan selisih satu sesi opname."""
    sesi = _sesi_boleh(sid)
    items = _items("s.id = ?", [sid])
    for it in items:
        it["lebih"], it["kurang"] = selisih(it)
    return render_template("opname_detail.html", sesi=sesi, items=items)


@bp.route("/<int:sid>/batal", methods=["POST"])
@login_required("admin")
def batal(sid):
    """Batalkan sesi opname yang salah: penyesuaian stoknya dibatalkan, sesi dihapus dari riwayat."""
    from .stock import StockError, batalkan_transaksi
    db = get_db()
    sesi = db.execute("SELECT * FROM opname_sesi WHERE id = ?", (sid,)).fetchone() or abort(404)
    try:
        with tx(db):
            n = 0
            for it in db.execute("SELECT * FROM opname_item WHERE sesi_id = ? AND trx_id IS NOT NULL", (sid,)).fetchall():
                t = db.execute("SELECT batal FROM transactions WHERE id = ?", (it["trx_id"],)).fetchone()
                if t and not t["batal"]:
                    batalkan_transaksi(db, it["trx_id"], f"Sesi opname #{sid} dibatalkan", g.user["id"])
                    n += 1
            db.execute("DELETE FROM opname_item WHERE sesi_id = ?", (sid,))
            db.execute("DELETE FROM opname_sesi WHERE id = ?", (sid,))
            audit(db, g.user["id"], "batal_opname",
                  f"sesi #{sid} {sesi['jenis_barang']} {sesi['tanggal']}: {n} penyesuaian dibatalkan")
    except StockError as e:
        flash(f"Sesi tidak bisa dibatalkan: {e}", "error")
        return redirect(url_for("opname.detail", sid=sid))
    flash(f"Sesi opname #{sid} dibatalkan; {n} penyesuaian stok dikembalikan.", "ok")
    return redirect(url_for("main.opname") if sesi["status"] == "terbuka" else url_for("opname.riwayat"))


@bp.route("/export")
@login_required("admin", "petugas", "pimpinan")
def export():
    """?sesi=1&sesi=2 (sesi tertentu) atau ?tanggal=YYYY-MM-DD (semua sesi di tanggal tsb)."""
    sesi = [int(s) for s in request.args.getlist("sesi") if s.isdigit()]
    tanggal = request.args.get("tanggal", "")
    if sesi:
        items = _items(f"s.status = 'selesai' AND s.id IN ({','.join('?' * len(sesi))})", sesi)
    elif tanggal:
        items = _items("s.status = 'selesai' AND s.tanggal = ?", [tanggal])
    else:
        abort(400)
    if not items:
        flash("Tidak ada data opname untuk diexport", "error")
        return redirect(url_for("opname.riwayat"))
    tgl = sorted({i["tanggal"] for i in items})
    nama = f"Berita_Acara_Stock_Opname_{tgl[0]}" + (f"_sd_{tgl[-1]}" if len(tgl) > 1 else "") + ".xlsx"
    return send_file(buat_excel(items, ambil_pengaturan(get_db())), as_attachment=True, download_name=nama,
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@bp.route("/pengaturan", methods=["GET", "POST"])
@login_required("admin")
def pengaturan():
    """Kelola kop dan penandatangan Berita Acara Stock Opname."""
    db = get_db()
    if request.method == "POST":
        with tx(db):
            for k in DEFAULT_PENGATURAN:
                db.execute("INSERT INTO pengaturan (kunci, nilai) VALUES (?, ?) "
                           "ON CONFLICT(kunci) DO UPDATE SET nilai = excluded.nilai",
                           (k, request.form.get(k, "").strip()))
            audit(db, g.user["id"], "pengaturan_laporan", "kop & tanda tangan berita acara diubah")
        flash("Pengaturan laporan opname disimpan", "ok")
        return redirect(url_for("opname.pengaturan"))
    return render_template("opname_pengaturan.html", st=ambil_pengaturan(db))

