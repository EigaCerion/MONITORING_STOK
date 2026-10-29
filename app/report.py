"""Data laporan pimpinan + ekspor Excel & PDF."""
import datetime as dt
import io

from fpdf import FPDF
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from .stock import JENIS_LABEL, bulat, fmt

MUTASI = ["masuk", "keluar", "transfer_masuk", "transfer_keluar", "penyesuaian"]
EFEK_SQL = """CASE jenis WHEN 'masuk' THEN qty WHEN 'transfer_masuk' THEN qty
              WHEN 'keluar' THEN -qty WHEN 'transfer_keluar' THEN -qty ELSE qty END"""


def harga_satuan(m):
    """Harga rata-rata dari export SAP (Value Unrestricted / Unrestricted)."""
    basis = m["sap_unrestricted"] if m["sap_unrestricted"] else m["stok_awal"]
    if basis and basis > 0 and m["value_unrestricted"]:
        return m["value_unrestricted"] / basis
    return 0.0


def data_laporan(db, dari, sampai, kebun="", kategori_id=""):
    """Kumpulkan seluruh dataset ringkasan dan rincian untuk laporan pimpinan."""
    where, args = ["1=1"], []
    if kebun:
        where.append("m.plant = ?")
        args.append(kebun)
    if kategori_id == "none":
        where.append("m.kategori_id IS NULL")
    elif kategori_id:
        where.append("m.kategori_id = ?")
        args.append(int(kategori_id))
    materials = db.execute(
        f"""SELECT m.*, COALESCE(k.nama, 'Belum dikategorikan') AS kategori,
                   COALESCE(kb.nama, m.name1, m.plant) AS kebun_nama
            FROM materials m LEFT JOIN kategori k ON k.id = m.kategori_id
            LEFT JOIN kebun kb ON kb.kode = m.plant
            WHERE {' AND '.join(where)} ORDER BY m.plant, k.urutan, m.material""",
        args,
    ).fetchall()

    cols = ", ".join(
        f"SUM(CASE WHEN tanggal BETWEEN :dari AND :sampai AND jenis = '{j}' THEN qty ELSE 0 END) AS {j}"
        for j in MUTASI
    )
    agg = {
        r["material_id"]: r
        for r in db.execute(
            f"""SELECT material_id, SUM(CASE WHEN tanggal < :dari THEN {EFEK_SQL} ELSE 0 END) AS sebelum,
                       SUM(CASE WHEN tanggal BETWEEN :dari AND :sampai THEN 1 ELSE 0 END) AS jml, {cols}
                FROM transactions WHERE batal = 0 AND tanggal <= :sampai GROUP BY material_id""",
            {"dari": dari, "sampai": sampai},
        )
    }

    rincian, per_kebun, per_kat, menipis = [], {}, {}, []
    for m in materials:
        a = agg.get(m["id"])
        harga = harga_satuan(m)
        r = {
            "material": m["material"], "deskripsi": m["deskripsi"], "satuan": m["satuan"],
            "special_stock": m["special_stock"], "kategori": m["kategori"],
            "kebun": m["plant"], "kebun_nama": m["kebun_nama"], "minimum": m["stok_minimum"],
            "saldo_awal": bulat(m["stok_awal"] + (a["sebelum"] if a else 0)), "harga": harga,
        }
        for j in MUTASI:
            r[j] = bulat(a[j]) if a else 0.0
        r["saldo_akhir"] = bulat(r["saldo_awal"] + r["masuk"] - r["keluar"] + r["transfer_masuk"]
                                 - r["transfer_keluar"] + r["penyesuaian"])
        r["nilai"] = r["saldo_akhir"] * harga
        rincian.append(r)

        kb = per_kebun.setdefault(m["plant"], {"kebun": m["plant"], "nama": m["kebun_nama"], "material": 0,
                                               "transaksi": 0, "saldo_nilai": 0.0,
                                               **{j: 0.0 for j in MUTASI}})
        kb["material"] += 1
        kb["transaksi"] += a["jml"] if a else 0
        kb["saldo_nilai"] += r["nilai"]
        for j in MUTASI:
            kb[j] += r[j] * harga

        kt = per_kat.setdefault(r["kategori"], {"kategori": r["kategori"], "material": 0, "nilai": 0.0,
                                                "menipis": 0, "habis": 0})
        kt["material"] += 1
        kt["nilai"] += r["nilai"]
        if r["saldo_akhir"] <= 0:
            kt["habis"] += 1
        if r["minimum"] > 0 and r["saldo_akhir"] <= r["minimum"]:
            kt["menipis"] += 1
            menipis.append(r)

    tw, targs = ["t.batal = 0", "t.jenis IN ('transfer_masuk','transfer_keluar')",
                 "t.tanggal BETWEEN ? AND ?"], [dari, sampai]
    if kebun:
        tw.append("m.plant = ?")
        targs.append(kebun)
    if kategori_id == "none":
        tw.append("m.kategori_id IS NULL")
    elif kategori_id:
        tw.append("m.kategori_id = ?")
        targs.append(int(kategori_id))
    transfer = db.execute(
        f"""SELECT t.*, m.material, m.deskripsi, m.satuan,
                   COALESCE(ka.nama, t.kebun_asal) AS asal_nama, COALESCE(kt.nama, t.kebun_tujuan) AS tujuan_nama
            FROM transactions t JOIN materials m ON m.id = t.material_id
            LEFT JOIN kebun ka ON ka.kode = t.kebun_asal LEFT JOIN kebun kt ON kt.kode = t.kebun_tujuan
            WHERE {' AND '.join(tw)} ORDER BY t.tanggal, t.id""",
        targs,
    ).fetchall()

    menipis.sort(key=lambda r: (r["saldo_akhir"] / r["minimum"]) if r["minimum"] else 0)
    total = {j: sum(k[j] for k in per_kebun.values()) for j in MUTASI}
    total["saldo_nilai"] = sum(k["saldo_nilai"] for k in per_kebun.values())
    total["transaksi"] = sum(k["transaksi"] for k in per_kebun.values())
    total["material"] = len(rincian)
    return {
        "dari": dari, "sampai": sampai, "per_kebun": list(per_kebun.values()), "total": total,
        "per_kategori": sorted(per_kat.values(), key=lambda k: k["kategori"] == "Belum dikategorikan"),
        "menipis": menipis, "transfer": [dict(t) for t in transfer], "rincian": rincian,
    }


def rp(x):
    """Format nilai Rupiah tanpa angka desimal."""
    return "Rp " + fmt(round(x or 0))


def tgl_id(iso):
    """Format tanggal ISO menjadi tampilan tanggal Indonesia."""
    try:
        return dt.date.fromisoformat(iso).strftime("%d/%m/%Y")
    except (TypeError, ValueError):
        return iso or ""


# ---------------------------------------------------------------- Excel
HEAD_FILL = PatternFill("solid", fgColor="1F4E78")
HEAD_FONT = Font(bold=True, color="FFFFFF")
THIN = Side(style="thin", color="BFBFBF")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
NUM = "#,##0.###"
RP = '"Rp" #,##0'


def format_angka(fmt, v):
    """'#,##0.###' membuat angka bulat tampil '36,154.' di Excel -> pakai '#,##0' untuk bilangan bulat."""
    if fmt == NUM and isinstance(v, (int, float)) and float(v).is_integer():
        return "#,##0"
    return fmt


def _tabel(ws, baris, header, data, formats=None):
    """Tulis tabel Excel dengan header, border, dan format kolom konsisten."""
    for j, h in enumerate(header, 1):
        c = ws.cell(baris, j, h)
        c.fill, c.font, c.border = HEAD_FILL, HEAD_FONT, BORDER
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    for i, row in enumerate(data, baris + 1):
        for j, v in enumerate(row, 1):
            c = ws.cell(i, j, v)
            c.border = BORDER
            if formats and formats.get(j):
                c.number_format = format_angka(formats[j], v)
    return baris + len(data) + 2


def _lebar(ws, widths):
    """Terapkan daftar lebar kolom pada worksheet."""
    for j, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(j)].width = w


def excel_laporan(d, judul_filter):
    """Buat workbook laporan stok lengkap dari dataset yang telah dihitung."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Ringkasan"
    ws["A1"] = "LAPORAN MONITORING STOK"
    ws["A1"].font = Font(bold=True, size=14)
    ws["A2"] = f"Periode {tgl_id(d['dari'])} s/d {tgl_id(d['sampai'])}  |  {judul_filter}"
    ws["A3"] = "Nilai (Rp) dihitung dari harga rata-rata SAP (Value Unrestricted / Unrestricted)."
    ws["A3"].font = Font(italic=True, size=9, color="666666")
    ws["A5"] = "1. Ringkasan per Kebun (nilai Rp)"
    ws["A5"].font = Font(bold=True)
    rows = [[k["nama"], k["material"], k["transaksi"], k["masuk"], k["keluar"], k["transfer_masuk"],
             k["transfer_keluar"], k["penyesuaian"], k["saldo_nilai"]] for k in d["per_kebun"]]
    t = d["total"]
    rows.append(["TOTAL", t["material"], t["transaksi"], t["masuk"], t["keluar"], t["transfer_masuk"],
                 t["transfer_keluar"], t["penyesuaian"], t["saldo_nilai"]])
    fm = {j: RP for j in range(4, 10)}
    nxt = _tabel(ws, 6, ["Kebun", "Jml Material", "Jml Transaksi", "Masuk", "Keluar", "Transfer Masuk",
                         "Transfer Keluar", "Penyesuaian", "Saldo Akhir"], rows, fm)
    for j in range(1, 10):
        ws.cell(nxt - 2, j).font = Font(bold=True)
    ws.cell(nxt, 1, "2. Stok per Kategori").font = Font(bold=True)
    nxt = _tabel(ws, nxt + 1, ["Kategori", "Jml Material", "Nilai Stok", "Stok Menipis", "Stok Habis"],
                 [[k["kategori"], k["material"], k["nilai"], k["menipis"], k["habis"]] for k in d["per_kategori"]],
                 {3: RP})
    ws.cell(nxt, 1, f"Transfer antar kebun: {len(d['transfer'])} dokumen  |  "
                    f"Material stok menipis: {len(d['menipis'])}  (rincian di sheet berikutnya)")
    _lebar(ws, [30, 12, 12, 16, 16, 16, 16, 16, 18])

    ws = wb.create_sheet("Transfer Antar Kebun")
    _tabel(ws, 1, ["Tanggal", "Jenis", "Dari", "Ke", "Material", "Deskripsi", "Qty", "Satuan", "Nomor Dokumen",
                   "Penerima"],
           [[tgl_id(x["tanggal"]), JENIS_LABEL[x["jenis"]], x["asal_nama"], x["tujuan_nama"], x["material"],
             x["deskripsi"], x["qty"], x["satuan"], x["nomor_dokumen"], x["penerima"]] for x in d["transfer"]],
           {7: NUM})
    _lebar(ws, [12, 17, 22, 22, 15, 40, 10, 8, 18, 20])

    ws = wb.create_sheet("Stok Menipis")
    _tabel(ws, 1, ["Kebun", "Kategori", "Material", "Deskripsi", "Stok", "Minimum", "Satuan"],
           [[x["kebun_nama"], x["kategori"], x["material"], x["deskripsi"], x["saldo_akhir"], x["minimum"],
             x["satuan"]] for x in d["menipis"]], {5: NUM, 6: NUM})
    _lebar(ws, [22, 18, 15, 40, 10, 10, 8])

    ws = wb.create_sheet("Rincian Mutasi")
    _tabel(ws, 1, ["Kebun", "Kategori", "Material", "Deskripsi", "Sp.Stock", "Satuan", "Saldo Awal", "Masuk",
                   "Keluar", "Transfer Masuk", "Transfer Keluar", "Penyesuaian", "Saldo Akhir", "Minimum",
                   "Nilai Saldo"],
           [[x["kebun_nama"], x["kategori"], x["material"], x["deskripsi"], x["special_stock"], x["satuan"],
             x["saldo_awal"], x["masuk"], x["keluar"], x["transfer_masuk"], x["transfer_keluar"],
             x["penyesuaian"], x["saldo_akhir"], x["minimum"], x["nilai"]] for x in d["rincian"]],
           {**{j: NUM for j in range(7, 15)}, 15: RP})
    _lebar(ws, [22, 18, 15, 40, 8, 8] + [12] * 8 + [16])
    ws.freeze_panes = "A2"

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


# ---------------------------------------------------------------- PDF
def _aman(s):
    """Ubah nilai menjadi teks aman untuk font PDF bawaan."""
    return str(s if s is not None else "").encode("latin-1", "replace").decode("latin-1")


class _PDF(FPDF):
    judul = ""

    def footer(self):
        """Cetak identitas laporan, waktu cetak, dan nomor halaman."""
        self.set_y(-12)
        self.set_font("Helvetica", "I", 7)
        self.set_text_color(120)
        self.cell(0, 5, _aman(f"{self.judul}  -  dicetak {dt.datetime.now():%d/%m/%Y %H:%M}  -  hal {self.page_no()}"),
                  align="R")
        self.set_text_color(0)


def _pdf_tabel(pdf, header, widths, rows, align=None, bold_last=False):
    """Render tabel PDF dengan pembungkusan teks dan pergantian halaman otomatis."""
    align = align or ["L"] * len(header)
    pdf.set_font("Helvetica", "B", 7.5)
    pdf.set_fill_color(31, 78, 120)
    pdf.set_text_color(255)

    def kepala():
        """Cetak ulang header tabel setelah pergantian halaman PDF."""
        pdf.set_font("Helvetica", "B", 7.5)
        pdf.set_fill_color(31, 78, 120)
        pdf.set_text_color(255)
        for h, w in zip(header, widths):
            pdf.cell(w, 6, _aman(h), border=1, align="C", fill=True)
        pdf.ln()
        pdf.set_text_color(0)

    kepala()
    for i, row in enumerate(rows):
        if pdf.get_y() > pdf.h - 20:
            pdf.add_page()
            kepala()
        tebal = bold_last and i == len(rows) - 1
        pdf.set_font("Helvetica", "B" if tebal else "", 7.5)
        pdf.set_fill_color(242, 246, 250)
        for v, w, a in zip(row, widths, align):
            s = _aman(v)
            while s and pdf.get_string_width(s) > w - 1.5:
                s = s[:-1]
            pdf.cell(w, 5.5, s, border=1, align=a, fill=(i % 2 == 1))
        pdf.ln()
    pdf.ln(3)


def _sub(pdf, teks):
    """Render subjudul bagian pada laporan PDF."""
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(0, 7, _aman(teks), new_x="LMARGIN", new_y="NEXT")


def pdf_laporan(d, judul_filter):
    """Buat laporan PDF A4 dari dataset laporan pimpinan."""
    pdf = _PDF(orientation="P", unit="mm", format="A4")
    pdf.judul = "Laporan Monitoring Stok"
    pdf.set_auto_page_break(True, 15)
    pdf.set_margins(10, 10, 10)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 8, "LAPORAN MONITORING STOK", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 8.5)
    pdf.cell(0, 5, _aman(f"Periode {tgl_id(d['dari'])} s/d {tgl_id(d['sampai'])}   |   {judul_filter}"),
             new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)

    t = d["total"]
    kartu = [("Nilai Saldo Stok", rp(t["saldo_nilai"])), ("Jumlah Transaksi", fmt(t["transaksi"])),
             ("Transfer Antar Kebun", fmt(len(d["transfer"]))), ("Material Menipis", fmt(len(d["menipis"])))]
    w = 190 / 4
    y = pdf.get_y()
    for i, (lbl, val) in enumerate(kartu):
        x = 10 + i * w
        pdf.set_fill_color(242, 246, 250)
        pdf.rect(x + 1, y, w - 2, 15, style="F")
        pdf.set_xy(x + 3, y + 2)
        pdf.set_font("Helvetica", "", 7.5)
        pdf.cell(w - 6, 4, _aman(lbl))
        pdf.set_xy(x + 3, y + 7)
        pdf.set_font("Helvetica", "B", 11)
        pdf.cell(w - 6, 6, _aman(val))
    pdf.set_xy(10, y + 19)

    _sub(pdf, "1. Ringkasan per Kebun (nilai Rp)")
    rows = [[k["nama"], fmt(k["transaksi"]), rp(k["masuk"]), rp(k["keluar"]), rp(k["transfer_masuk"]),
             rp(k["transfer_keluar"]), rp(k["penyesuaian"]), rp(k["saldo_nilai"])] for k in d["per_kebun"]]
    rows.append(["TOTAL", fmt(t["transaksi"]), rp(t["masuk"]), rp(t["keluar"]), rp(t["transfer_masuk"]),
                 rp(t["transfer_keluar"]), rp(t["penyesuaian"]), rp(t["saldo_nilai"])])
    _pdf_tabel(pdf, ["Kebun", "Trx", "Masuk", "Keluar", "Trf Masuk", "Trf Keluar", "Penyesuaian", "Saldo"],
               [38, 10, 24, 24, 24, 24, 22, 24], rows, ["L", "R"] + ["R"] * 6, bold_last=True)

    _sub(pdf, "2. Stok per Kategori")
    _pdf_tabel(pdf, ["Kategori", "Jml Material", "Nilai Stok", "Menipis", "Habis"], [60, 30, 50, 25, 25],
               [[k["kategori"], fmt(k["material"]), rp(k["nilai"]), fmt(k["menipis"]), fmt(k["habis"])]
                for k in d["per_kategori"]], ["L", "R", "R", "R", "R"])

    _sub(pdf, f"3. Material Stok Menipis ({len(d['menipis'])})")
    tampil = d["menipis"][:12]
    _pdf_tabel(pdf, ["Material", "Deskripsi", "Kategori", "Stok", "Min", "Sat"], [24, 82, 30, 20, 20, 14],
               [[x["material"], x["deskripsi"], x["kategori"], fmt(x["saldo_akhir"]), fmt(x["minimum"]),
                 x["satuan"]] for x in tampil], ["L", "L", "L", "R", "R", "L"])
    if len(d["menipis"]) > len(tampil):
        pdf.set_font("Helvetica", "I", 7.5)
        pdf.cell(0, 4, _aman(f"... {len(d['menipis']) - len(tampil)} material lainnya pada lampiran."),
                 new_x="LMARGIN", new_y="NEXT")

    # ---- Rincian (halaman berikutnya)
    pdf.add_page()
    _sub(pdf, f"Lampiran A. Transfer Antar Kebun ({len(d['transfer'])})")
    _pdf_tabel(pdf, ["Tanggal", "Dari", "Ke", "Material", "Deskripsi", "Qty", "Sat", "No. Dokumen"],
               [17, 26, 26, 21, 50, 14, 10, 26],
               [[tgl_id(x["tanggal"]), x["asal_nama"], x["tujuan_nama"], x["material"], x["deskripsi"],
                 fmt(x["qty"]), x["satuan"], x["nomor_dokumen"]] for x in d["transfer"]],
               ["L", "L", "L", "L", "L", "R", "L", "L"])
    if len(d["menipis"]) > len(tampil):
        _sub(pdf, f"Lampiran B. Seluruh Material Stok Menipis ({len(d['menipis'])})")
        _pdf_tabel(pdf, ["Kebun", "Material", "Deskripsi", "Kategori", "Stok", "Min", "Sat"],
                   [30, 22, 70, 26, 14, 14, 14],
                   [[x["kebun_nama"], x["material"], x["deskripsi"], x["kategori"], fmt(x["saldo_akhir"]),
                     fmt(x["minimum"]), x["satuan"]] for x in d["menipis"]],
                   ["L", "L", "L", "L", "R", "R", "L"])
    return io.BytesIO(bytes(pdf.output()))


def excel_tabel(judul, header, rows, widths, nama_file, formats=None):
    """Satu sheet tabel sederhana (header biru, filter, baris beku) sebagai unduhan."""
    from flask import send_file

    wb = Workbook()
    ws = wb.active
    ws.title = judul[:31]
    _tabel(ws, 1, header, rows, formats)
    _lebar(ws, widths)
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(header))}{max(1, len(rows) + 1)}"
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return send_file(buf, as_attachment=True, download_name=nama_file,
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
