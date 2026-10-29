"""Laporan pimpinan: tampilan web + unduh Excel/PDF."""
import datetime as dt

from flask import Blueprint, g, render_template, request, send_file

from .auth import login_required
from .db import get_db
from .report import data_laporan, excel_laporan, pdf_laporan, tgl_id

bp = Blueprint("laporan", __name__)


def _filter():
    """Normalisasi filter periode, kebun, dan kategori sesuai akses pengguna."""
    a = request.args
    today = dt.date.today()
    dari = a.get("dari") or today.replace(day=1).isoformat()
    sampai = a.get("sampai") or today.isoformat()
    if dari > sampai:
        dari, sampai = sampai, dari
    kebun = a.get("kebun", "")
    if g.user["kebun_kode"]:
        kebun = g.user["kebun_kode"]
    return dari, sampai, kebun, a.get("kategori", "")


def _judul(db, kebun, kategori):
    """Susun subjudul laporan dari nama kebun dan kategori yang dipilih."""
    kb = db.execute("SELECT nama FROM kebun WHERE kode = ?", (kebun,)).fetchone() if kebun else None
    if kategori == "none":
        kt = "Belum dikategorikan"
    else:
        r = db.execute("SELECT nama FROM kategori WHERE id = ?", (kategori,)).fetchone() if kategori else None
        kt = r["nama"] if r else "Semua kategori"
    return f"Kebun: {kb['nama'] if kb else 'Semua kebun'}  |  Kategori: {kt}"


@bp.route("/laporan")
@login_required("admin", "pimpinan")
def laporan():
    """Tampilkan laporan pimpinan atau kirim hasil export Excel/PDF."""
    db = get_db()
    dari, sampai, kebun, kategori = _filter()
    d = data_laporan(db, dari, sampai, kebun, kategori)
    fmt = request.args.get("format")
    judul = _judul(db, kebun, kategori)
    nama = f"Laporan_Stok_{dari}_sd_{sampai}"
    if fmt == "xlsx":
        return send_file(excel_laporan(d, judul), as_attachment=True, download_name=f"{nama}.xlsx",
                         mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    if fmt == "pdf":
        return send_file(pdf_laporan(d, judul), as_attachment=True, download_name=f"{nama}.pdf",
                         mimetype="application/pdf")
    return render_template("laporan.html", d=d, judul=judul, dari=dari, sampai=sampai, kebun_sel=kebun,
                           kategori_sel=kategori, periode=f"{tgl_id(dari)} s/d {tgl_id(sampai)}",
                           kebun=db.execute("SELECT * FROM kebun ORDER BY nama").fetchall(),
                           kategori=db.execute("SELECT * FROM kategori ORDER BY urutan").fetchall())
