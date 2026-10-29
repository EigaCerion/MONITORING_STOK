"""Halaman PR (Purchase Request) pemesanan barang ke pusat."""
import datetime as dt
import re
import uuid
from pathlib import Path

from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, send_file, url_for

from . import pr as prmod
from .auth import login_required
from .db import audit, get_db, tx
from .excel_io import ExcelError, angka, baca_progress_pr
from .views_main import alasan_material_dikunci, filter_kebun_user, hari_ini, material_boleh, material_input_locked

bp = Blueprint("pr", __name__, url_prefix="/pr")
EPS = 0.0005


def _nomor_pr(db):
    """Buat nomor PR internal berurutan per bulan tanpa penggunaan ulang."""
    awal = f"PR-{dt.date.today():%y%m}-"
    r = db.execute("SELECT nilai FROM penomoran WHERE kunci = ?", (awal,)).fetchone()
    n = (r["nilai"] if r else 0) + 1
    db.execute("INSERT INTO penomoran (kunci, nilai) VALUES (?, ?) ON CONFLICT(kunci) DO UPDATE SET nilai = ?",
               (awal, n, n))
    return f"{awal}{n:03d}"


def _pr(pid):
    """Ambil PR yang berada dalam lingkup kebun pengguna atau hentikan 404."""
    p = get_db().execute("SELECT p.*, u.nama AS oleh FROM pr p LEFT JOIN users u ON u.id = p.created_by "
                         "WHERE p.id = ?", (pid,)).fetchone()
    if not p or (g.user["kebun_kode"] and p["plant"] != g.user["kebun_kode"]):
        abort(404)
    return p


def _items(pid):
    """Siapkan item PR lengkap dengan stok, penerimaan, kelompok, bulan, dan crosscheck."""
    rows = get_db().execute(
        """SELECT i.*, COALESCE(v.material, i.kode) AS x_material, COALESCE(v.deskripsi, i.deskripsi) AS x_deskripsi,
                  COALESCE(v.satuan, i.satuan) AS x_satuan, v.stok AS stok_kini, v.stok_minimum,
                  v.material_group, k.nama AS kategori,
                  COALESCE((SELECT SUM(ri.qty) FROM pr_penerimaan_item ri WHERE ri.pr_item_id = i.id), 0)
                    AS qty_diterima,
                  (SELECT r.grn FROM pr_penerimaan_item ri JOIN pr_penerimaan r ON r.id = ri.penerimaan_id
                   WHERE ri.pr_item_id = i.id ORDER BY r.tanggal DESC, r.id DESC LIMIT 1) AS grn_terakhir,
                  (SELECT r.tanggal FROM pr_penerimaan_item ri JOIN pr_penerimaan r ON r.id = ri.penerimaan_id
                   WHERE ri.pr_item_id = i.id ORDER BY r.tanggal DESC, r.id DESC LIMIT 1) AS tanggal_terakhir
           FROM pr_item i LEFT JOIN v_stok v ON v.id = i.material_id LEFT JOIN kategori k ON k.id = v.kategori_id
           WHERE i.pr_id = ? ORDER BY i.urutan, i.id""",
        (pid,)).fetchall()
    nomor = {r["kelompok"]: r["nomor_sap"] for r in get_db().execute(
        "SELECT kelompok, nomor_sap FROM pr_sap WHERE pr_id = ?", (pid,))}
    hasil = []
    for r in rows:
        d = dict(r)
        for k in ("material", "deskripsi", "satuan"):  # data stok, atau isian manual bila tidak ada di stok
            d[k] = d.pop("x_" + k)
        d["manual"] = d["material_id"] is None
        d["kelompok"] = d["kelompok"] or prmod.kelompok_default(d["kategori"], d["material_group"], d["material"])
        d["nomor_sap"] = nomor.get(d["kelompok"])
        d["bulan"] = prmod.bulan_item(d)
        d["total"] = (d["qty"] or 0) * (d["harga"] or 0)
        d["crosscek"] = round((d["qty"] or 0) + max(d["stok"] or 0, 0) - sum(b or 0 for b in d["bulan"]), 3)
        d["pakai"] = prmod.pakai_per_bulan(get_db(), d["material_id"]) if d["material_id"] else 0
        d["qty_diterima"] = round(d["qty_diterima"] or 0, 3)
        d["qty_sisa"] = round(max((d["qty"] or 0) - d["qty_diterima"], 0), 3)
        if d["qty"] and d["qty_diterima"] >= d["qty"] - EPS:
            d["status_penerimaan"] = "lengkap"
        elif d["qty_diterima"] > EPS:
            d["status_penerimaan"] = "parsial"
        else:
            d["status_penerimaan"] = "belum"
        hasil.append(d)
    hasil.sort(key=lambda d: (d["kelompok"], d["urutan"], d["id"]))  # barang sekelompok berdampingan
    return hasil


def _ringkasan_penerimaan(items):
    """Ringkas progres berdasarkan jumlah baris agar satuan berbeda tidak dijumlahkan."""
    terukur = [item for item in items if (item["qty"] or 0) > EPS]
    jumlah = {status: sum(item["status_penerimaan"] == status for item in terukur)
              for status in ("lengkap", "parsial", "belum")}
    persen = (
        round(sum(min(item["qty_diterima"] / item["qty"], 1) for item in terukur) / len(terukur) * 100)
        if terukur else 0
    )
    return {**jumlah, "total": len(terukur), "persen": persen}


def _riwayat_penerimaan(pid):
    """Kelompokkan detail barang per GRN untuk riwayat di halaman PR."""
    db = get_db()
    headers = db.execute(
        """SELECT r.*, u.nama AS oleh FROM pr_penerimaan r
           LEFT JOIN users u ON u.id = r.created_by WHERE r.pr_id = ?
           ORDER BY r.tanggal DESC, r.id DESC""", (pid,)).fetchall()
    hasil = []
    for header in headers:
        baris = db.execute(
            """SELECT ri.qty, COALESCE(m.material, i.kode) AS material,
                      COALESCE(m.deskripsi, i.deskripsi) AS deskripsi,
                      COALESCE(m.satuan, i.satuan) AS satuan
               FROM pr_penerimaan_item ri JOIN pr_item i ON i.id = ri.pr_item_id
               LEFT JOIN materials m ON m.id = i.material_id
               WHERE ri.penerimaan_id = ? ORDER BY i.urutan, i.id""", (header["id"],)).fetchall()
        hasil.append({**dict(header), "baris": [dict(row) for row in baris]})
    return hasil


def _kelompok(p, items):
    """Ringkasan per kelompok: jumlah barang, estimasi, nomor PR SAP."""
    nomor = {r["kelompok"]: r["nomor_sap"] for r in get_db().execute(
        "SELECT kelompok, nomor_sap FROM pr_sap WHERE pr_id = ?", (p["id"],))}
    hasil = {}
    for it in items:
        g_ = hasil.setdefault(it["kelompok"], {"kelompok": it["kelompok"], "n": 0, "total": 0.0,
                                               "nomor_sap": nomor.get(it["kelompok"]) or p["nomor_sap"]})
        g_["n"] += 1
        g_["total"] += it["total"]
    return list(hasil.values())


def _tambah(db, p, m, qty=None):
    """Tambahkan satu material stok ke PR dengan nilai awal yang dihitung."""
    stok = db.execute("SELECT stok FROM v_stok WHERE id = ?", (m["id"],)).fetchone()["stok"]
    urutan = db.execute("SELECT COALESCE(MAX(urutan), 0) + 1 FROM pr_item WHERE pr_id = ?", (p["id"],)).fetchone()[0]
    db.execute("""INSERT INTO pr_item (pr_id, material_id, qty, harga, stok, terakhir_purchase, periode, bulan_n,
                      bulan_auto, urutan) VALUES (?,?,?,?,?,?,?,?,1,?)""",
               (p["id"], m["id"], qty if qty is not None else prmod.saran_qty(stok, m["stok_minimum"]),
                prmod.harga_default(m), round(stok, 3), prmod.terakhir_masuk(db, m["id"]),
                f"{p['bulan_default']} Bulan", p["bulan_default"], urutan))


@bp.route("/")
@login_required("admin", "pimpinan")
def daftar():
    """Tampilkan daftar PR dan ringkasan nomor SAP per kelompok."""
    fk, fa = filter_kebun_user("p")
    rows = get_db().execute(
        f"""SELECT p.*, u.nama AS oleh, COUNT(i.id) AS n,
                   SUM(CASE WHEN i.qty > {EPS} THEN 1 ELSE 0 END) AS n_dipesan,
                   COALESCE(SUM(i.qty * COALESCE(i.harga, 0)), 0) AS total,
                   SUM(CASE WHEN i.qty > 0 AND COALESCE(ri.diterima, 0) >= i.qty - {EPS} THEN 1 ELSE 0 END) AS n_lengkap,
                   SUM(CASE WHEN COALESCE(ri.diterima, 0) > {EPS}
                                  AND COALESCE(ri.diterima, 0) < i.qty - {EPS} THEN 1 ELSE 0 END) AS n_parsial,
                   (SELECT COUNT(*) FROM pr_penerimaan r WHERE r.pr_id = p.id) AS n_grn,
                   (SELECT r.grn FROM pr_penerimaan r WHERE r.pr_id = p.id
                    ORDER BY r.tanggal DESC, r.id DESC LIMIT 1) AS grn_terakhir,
                   (SELECT GROUP_CONCAT(s.nomor_sap, ', ') FROM pr_sap s WHERE s.pr_id = p.id AND s.nomor_sap IS NOT NULL)
                       AS daftar_sap
            FROM pr p LEFT JOIN pr_item i ON i.pr_id = p.id LEFT JOIN users u ON u.id = p.created_by
            LEFT JOIN (SELECT pr_item_id, SUM(qty) AS diterima FROM pr_penerimaan_item GROUP BY pr_item_id) ri
              ON ri.pr_item_id = i.id
            WHERE 1=1 {fk} GROUP BY p.id ORDER BY p.id DESC LIMIT 200""", fa).fetchall()
    kebun = get_db().execute("SELECT * FROM kebun WHERE kode IN (SELECT DISTINCT plant FROM materials) "
                             "ORDER BY nama").fetchall()
    return render_template("pr_daftar.html", rows=rows, kebun=kebun, STATUS=prmod.STATUS, hari_ini=hari_ini())


@bp.route("/upload-sap", methods=["POST"])
@login_required("admin")
def upload_sap():
    """Isi nomor PR SAP per kelompok dari file Pendukung PR yang kembali dari SAP."""
    unggahan = request.files.get("file")
    nama = (unggahan.filename if unggahan else "") or ""
    cocok_nomor = re.search(r"PR-\d{4}-\d{3}", nama.upper())
    if not unggahan or not nama.lower().endswith((".xlsx", ".xlsm")):
        flash("Pilih file Pendukung PR berformat .xlsx", "error")
        return redirect(url_for("pr.daftar"))
    if not cocok_nomor:
        flash("Nomor PR aplikasi tidak ditemukan pada nama file. Gunakan file hasil export aplikasi.", "error")
        return redirect(url_for("pr.daftar"))

    nomor_internal = cocok_nomor.group(0)
    db = get_db()
    p = db.execute("SELECT * FROM pr WHERE nomor = ?", (nomor_internal,)).fetchone()
    if not p:
        flash(f"{nomor_internal} tidak ditemukan di Progress PR", "error")
        return redirect(url_for("pr.daftar"))
    if g.user["kebun_kode"] and p["plant"] != g.user["kebun_kode"]:
        abort(403)
    if p["status"] == "batal":
        flash(f"{nomor_internal} sudah dibatalkan dan tidak dapat diperbarui dari SAP", "error")
        return redirect(url_for("pr.daftar"))

    path = Path(current_app.config["TMP_FOLDER"]) / f"pr_sap_{uuid.uuid4().hex}.xlsx"
    try:
        unggahan.save(path)
        rows = baca_progress_pr(path)
    except ExcelError as e:
        flash(str(e), "error")
        return redirect(url_for("pr.daftar"))
    finally:
        path.unlink(missing_ok=True)

    items = _items(p["id"])
    tersedia = {}
    for it in items:
        tersedia.setdefault((it["material"] or "").upper(), []).append(it)
    terpakai, pemetaan, error = set(), {}, []
    for r in rows:
        if r["plant"] != p["plant"]:
            error.append(f"baris {r['_baris']}: Plant {r['plant']} bukan {p['plant']}")
            continue
        kandidat = [it for it in tersedia.get(r["material"], []) if it["id"] not in terpakai]
        tepat = [it for it in kandidat if abs(float(it["qty"] or 0) - float(r["qty"] or 0)) < 0.0005]
        if len(tepat) != 1:
            alasan = "tidak ada di PR aplikasi" if not kandidat else "Qty tidak sama atau material ganda"
            error.append(f"baris {r['_baris']}: {r['material']} {alasan}")
            continue
        it = tepat[0]
        if r["satuan"] and it["satuan"] and r["satuan"] != str(it["satuan"]).upper():
            error.append(f"baris {r['_baris']}: UoM {r['material']} berbeda ({r['satuan']} ≠ {it['satuan']})")
            continue
        terpakai.add(it["id"])
        pemetaan.setdefault(it["kelompok"], set()).add(r["sap_pr"])

    hilang = [it["material"] for it in items if it["id"] not in terpakai]
    if hilang:
        error.append("material belum tercantum: " + ", ".join(hilang[:6]) + ("…" if len(hilang) > 6 else ""))
    bentrok = [f"{kel} memiliki beberapa nomor SAP" for kel, nomor in pemetaan.items() if len(nomor) != 1]
    error.extend(bentrok)
    if error:
        flash("Upload PR SAP ditolak: " + " · ".join(error[:8]), "error")
        return redirect(url_for("pr.daftar"))

    hasil = {kel: next(iter(nomor)) for kel, nomor in pemetaan.items()}
    nomor_unik = sorted(set(hasil.values()))
    with tx(db):
        for kel, nomor_sap in hasil.items():
            db.execute(
                """INSERT INTO pr_sap (pr_id, kelompok, nomor_sap) VALUES (?, ?, ?)
                   ON CONFLICT(pr_id, kelompok) DO UPDATE SET nomor_sap = excluded.nomor_sap""",
                (p["id"], kel, nomor_sap))
        db.execute(
            """UPDATE pr SET nomor_sap = ?, status = CASE WHEN status = 'selesai' THEN status ELSE 'diajukan' END,
                   updated_at = datetime('now','localtime') WHERE id = ?""",
            (nomor_unik[0] if len(nomor_unik) == 1 else None, p["id"]))
        audit(db, g.user["id"], "upload_progress_pr_sap",
              f"{p['nomor']}: {len(rows)} barang, {len(hasil)} kelompok, SAP {', '.join(nomor_unik)}")
    flash(f"{p['nomor']} diperbarui otomatis: {len(rows)} barang, {len(hasil)} kelompok, "
          f"{len(nomor_unik)} nomor PR SAP.", "ok")
    return redirect(url_for("pr.detail", pid=p["id"]))


@bp.route("/baru", methods=["POST"])
@login_required("admin")
def baru():
    """Buat draft PR baru dan opsional isi material yang stoknya menipis."""
    db = get_db()
    f = request.form
    plant = g.user["kebun_kode"] or f.get("plant", "")
    if not db.execute("SELECT 1 FROM kebun WHERE kode = ?", (plant,)).fetchone():
        flash("Pilih kebun (plant)", "error")
        return redirect(url_for("pr.daftar"))
    with tx(db):
        nomor = _nomor_pr(db)
        pid = db.execute("""INSERT INTO pr (nomor, tanggal, plant, judul, bulan_default, created_by)
                            VALUES (?,?,?,?,?,?)""",
                         (nomor, f.get("tanggal") or hari_ini(), plant, f.get("judul", "").strip() or None,
                          max(1, min(6, f.get("bulan_default", type=int) or 3)), g.user["id"])).lastrowid
        p = db.execute("SELECT * FROM pr WHERE id = ?", (pid,)).fetchone()
        n = 0
        if f.get("isi") == "menipis":
            n = _tambah_menipis(db, p, f.get("awalan", "").strip())
        audit(db, g.user["id"], "buat_pr", f"{nomor} {p['judul'] or ''} ({n} barang menipis)")
    flash(f"PR {nomor} dibuat" + (f" dengan {n} barang stok menipis" if n else "") + ".", "ok")
    return redirect(url_for("pr.detail", pid=pid))


def _tambah_menipis(db, p, awalan=""):
    """Tambahkan material menipis yang belum ada ke dalam PR."""
    where, args = "v.plant = ? AND v.input_locked = 0 AND v.stok_minimum > 0 AND v.stok <= v.stok_minimum", [p["plant"]]
    if awalan:
        where += " AND v.material LIKE ?"
        args.append(awalan + "%")
    ada = {r[0] for r in db.execute("SELECT material_id FROM pr_item WHERE pr_id = ?", (p["id"],))}
    n = 0
    for m in db.execute(f"SELECT v.* FROM v_stok v WHERE {where} ORDER BY v.material", args).fetchall():
        if m["id"] not in ada:
            _tambah(db, p, m)
            n += 1
    return n


@bp.route("/<int:pid>", methods=["GET", "POST"])
@login_required("admin", "pimpinan")
def detail(pid):
    """Tampilkan atau simpan header, item, pembagian bulan, dan status PR."""
    p = _pr(pid)
    db = get_db()
    if request.method == "POST":
        if g.user["role"] != "admin":
            abort(403)
        f = request.form
        aksi = f.get("aksi", "simpan")
        # Qty PR tidak boleh diturunkan melewati jumlah yang sudah memiliki GRN.
        qty_errors = []
        for item in db.execute("SELECT id, qty FROM pr_item WHERE pr_id = ?", (pid,)).fetchall():
            if f"qty_{item['id']}" not in f:
                continue
            qty_baru = max(angka(f.get(f"qty_{item['id']}")) or 0, 0)
            diterima = db.execute("SELECT COALESCE(SUM(qty), 0) FROM pr_penerimaan_item WHERE pr_item_id = ?",
                                  (item["id"],)).fetchone()[0]
            if qty_baru < diterima - EPS:
                qty_errors.append(f"Item #{item['id']}: Qty PR {qty_baru:g} lebih kecil dari diterima {diterima:g}")
        if aksi.startswith("hapus_"):
            item_id = int(aksi[6:])
            if db.execute("SELECT 1 FROM pr_penerimaan_item WHERE pr_item_id = ?", (item_id,)).fetchone():
                qty_errors.append("Barang yang sudah memiliki penerimaan GRN tidak dapat dihapus")
        if qty_errors:
            flash("PR tidak disimpan: " + " · ".join(qty_errors), "error")
            return redirect(url_for("pr.detail", pid=pid) + "#penerimaan")
        with tx(db):
            if "tanggal" in f:  # kepala PR hanya diubah bila formulirnya ikut terkirim
                db.execute("""UPDATE pr SET tanggal = ?, judul = ?, status = ?, keterangan = ?,
                                  bulan_default = ?, updated_at = datetime('now','localtime') WHERE id = ?""",
                           (f.get("tanggal") or p["tanggal"], f.get("judul", "").strip() or None,
                            f.get("status") if f.get("status") in prmod.STATUS else p["status"],
                            f.get("keterangan", "").strip() or None,
                            max(1, min(6, f.get("bulan_default", type=int) or p["bulan_default"])), pid))
            for it in db.execute("SELECT * FROM pr_item WHERE pr_id = ?", (pid,)).fetchall():
                i = it["id"]
                if f"qty_{i}" not in f:
                    continue
                bulan = [angka(f.get(f"b{k}_{i}")) for k in range(1, 7)]
                if f"kel_{i}" in f:
                    db.execute("UPDATE pr_item SET kelompok = ? WHERE id = ?",
                               (f.get(f"kel_{i}", "").strip() or None, i))
                if it["material_id"] is None:
                    db.execute("UPDATE pr_item SET kode = ?, deskripsi = ?, satuan = ? WHERE id = ?",
                               (f.get(f"kode_{i}", "").strip().upper() or None,
                                f.get(f"nama_{i}", "").strip().upper() or None,
                                f.get(f"uom_{i}", "").strip().upper() or None, i))
                db.execute("""UPDATE pr_item SET qty = ?, harga = ?, terakhir_purchase = ?, periode = ?, bulan_n = ?,
                                  bulan_auto = ?, b1 = ?, b2 = ?, b3 = ?, b4 = ?, b5 = ?, b6 = ? WHERE id = ?""",
                           [max(angka(f.get(f"qty_{i}")) or 0, 0), angka(f.get(f"harga_{i}")),
                            f.get(f"tp_{i}", "").strip() or None, f.get(f"periode_{i}", "").strip() or None,
                            max(1, min(6, f.get(f"n_{i}", type=int) or 3)), 1 if f.get(f"auto_{i}") else 0]
                           + bulan + [i])
            # nomor PR SAP per kelompok
            for kunci in [k for k in f if k.startswith("sapk_")]:
                kel = f.get(kunci, "").strip()
                if kel:
                    db.execute("""INSERT INTO pr_sap (pr_id, kelompok, nomor_sap) VALUES (?, ?, ?)
                                  ON CONFLICT(pr_id, kelompok) DO UPDATE SET nomor_sap = excluded.nomor_sap""",
                               (pid, kel, f.get("sapn_" + kunci[5:], "").strip() or None))
            if aksi.startswith("hapus_"):
                db.execute("DELETE FROM pr_item WHERE id = ? AND pr_id = ?", (int(aksi[6:]), pid))
            elif aksi == "segarkan":  # perbarui stok on hand & terakhir GRN dari data terkini
                for it in db.execute("SELECT * FROM pr_item WHERE pr_id = ? AND material_id IS NOT NULL",
                                     (pid,)).fetchall():
                    stok = db.execute("SELECT stok FROM v_stok WHERE id = ?", (it["material_id"],)).fetchone()["stok"]
                    db.execute("UPDATE pr_item SET stok = ?, terakhir_purchase = ? WHERE id = ?",
                               (round(stok, 3), prmod.terakhir_masuk(db, it["material_id"]), it["id"]))
            elif aksi == "menipis":
                n = _tambah_menipis(db, db.execute("SELECT * FROM pr WHERE id = ?", (pid,)).fetchone(),
                                    f.get("awalan", "").strip())
                flash(f"{n} barang stok menipis ditambahkan.", "ok")
            elif aksi == "manual":
                kode = f.get("m_kode", "").strip().upper()
                nama = f.get("m_nama", "").strip().upper()
                if not nama:
                    flash("Isi minimal nama barang untuk barang manual", "error")
                else:
                    ada = db.execute("SELECT * FROM v_stok WHERE material = ? AND plant = ?", (kode, p["plant"])).fetchone() \
                        if kode else None
                    if ada and material_input_locked(ada):
                        flash(f"Kode {kode} dikunci: {alasan_material_dikunci(ada)}", "error")
                    elif ada:  # ternyata ada di stok -> pakai data stok
                        _tambah(db, p, ada, angka(f.get("m_qty")))
                        flash(f"Kode {kode} ternyata ada di stok ({ada['deskripsi']}); data stok yang dipakai.", "warn")
                    else:
                        n = p["bulan_default"]
                        urutan = db.execute("SELECT COALESCE(MAX(urutan), 0) + 1 FROM pr_item WHERE pr_id = ?",
                                            (pid,)).fetchone()[0]
                        db.execute("""INSERT INTO pr_item (pr_id, material_id, kode, deskripsi, satuan, qty, harga, stok,
                                          terakhir_purchase, periode, bulan_n, bulan_auto, urutan)
                                      VALUES (?, NULL, ?, ?, ?, ?, ?, 0, 'Belum Pernah PR', ?, ?, 1, ?)""",
                                   (pid, kode or None, nama, f.get("m_uom", "").strip().upper() or None,
                                    max(angka(f.get("m_qty")) or 0, 0), angka(f.get("m_harga")), f"{n} Bulan", n,
                                    urutan))
                        flash(f"{nama} (manual) ditambahkan.", "ok")
            elif aksi == "tambah":
                m = material_boleh(f.get("material_id", type=int) or 0)
                if m and material_input_locked(m):
                    flash(f"Kode {m['material']} dikunci: {alasan_material_dikunci(m)}", "error")
                elif m and m["plant"] == p["plant"]:
                    _tambah(db, p, m)
                    flash(f"{m['deskripsi']} ditambahkan.", "ok")
        if aksi == "export":
            return redirect(url_for("pr.export", pid=pid))
        if aksi.startswith("export:"):
            return redirect(url_for("pr.export", pid=pid, kelompok=aksi[7:]))
        if aksi == "simpan":
            flash("PR disimpan.", "ok")
        return redirect(url_for("pr.detail", pid=pid) + ("#barang" if aksi != "simpan" else ""))
    items = _items(pid)
    kategori = [r[0] for r in db.execute("SELECT nama FROM kategori ORDER BY urutan")]
    return render_template("pr_detail.html", p=p, items=items, STATUS=prmod.STATUS,
                           total=sum(i["total"] for i in items), kelompok=_kelompok(p, items),
                           pilihan_kelompok=sorted({i["kelompok"] for i in items} | set(kategori)),
                           ringkasan_terima=_ringkasan_penerimaan(items),
                           riwayat_terima=_riwayat_penerimaan(pid), hari_ini=hari_ini())


@bp.route("/<int:pid>/penerimaan", methods=["POST"])
@login_required("admin")
def penerimaan(pid):
    """Catat satu GRN yang dapat memuat beberapa item dan penerimaan parsial."""
    p = _pr(pid)
    db = get_db()
    if p["status"] == "batal":
        flash("PR yang dibatalkan tidak dapat menerima barang", "error")
        return redirect(url_for("pr.detail", pid=pid) + "#penerimaan")
    grn = request.form.get("grn", "").strip().upper()
    tanggal = request.form.get("tanggal_terima") or hari_ini()
    keterangan = request.form.get("keterangan_terima", "").strip() or None
    errors, diterima = [], []
    if not grn:
        errors.append("Nomor GRN wajib diisi")
    try:
        tanggal_obj = dt.date.fromisoformat(tanggal)
        if tanggal_obj > dt.date.today():
            errors.append("Tanggal penerimaan tidak boleh di masa depan")
    except ValueError:
        errors.append("Tanggal penerimaan tidak valid")
    if db.execute("SELECT 1 FROM pr_penerimaan WHERE pr_id = ? AND UPPER(grn) = ?", (pid, grn)).fetchone():
        errors.append(f"GRN {grn} sudah tercatat pada PR ini")

    items = {row["id"]: row for row in _items(pid)}
    for key, raw in request.form.items():
        if not key.startswith("terima_") or not raw.strip():
            continue
        try:
            item_id = int(key[7:])
        except ValueError:
            continue
        item = items.get(item_id)
        qty = angka(raw)
        if not item:
            errors.append(f"Item #{item_id} tidak ditemukan pada PR")
        elif qty is None or qty <= 0:
            errors.append(f"{item['deskripsi']}: qty datang harus lebih dari 0")
        elif qty > item["qty_sisa"] + EPS:
            errors.append(
                f"{item['deskripsi']}: qty datang {qty:g} melebihi sisa PR {item['qty_sisa']:g} {item['satuan'] or ''}"
            )
        else:
            diterima.append((item, round(qty, 3)))
    if not diterima:
        errors.append("Isi qty datang minimal untuk satu barang")
    if errors:
        for error in dict.fromkeys(errors):
            flash(error, "error")
        return redirect(url_for("pr.detail", pid=pid) + "#penerimaan")

    try:
        with tx(db):
            # Ulangi pemeriksaan setelah write lock diperoleh agar dua operator
            # tidak dapat menyimpan nomor GRN yang sama secara bersamaan.
            if db.execute(
                "SELECT 1 FROM pr_penerimaan WHERE pr_id = ? AND UPPER(grn) = ?", (pid, grn)
            ).fetchone():
                raise ValueError(f"GRN {grn} sudah tercatat pada PR ini")
            rid = db.execute(
                """INSERT INTO pr_penerimaan (pr_id, tanggal, grn, keterangan, created_by)
                   VALUES (?, ?, ?, ?, ?)""", (pid, tanggal, grn, keterangan, g.user["id"])).lastrowid
            for item, qty in diterima:
                # Cek ulang di dalam transaksi untuk menjaga batas bila ada dua input bersamaan.
                total_lama = db.execute(
                    "SELECT COALESCE(SUM(qty), 0) FROM pr_penerimaan_item WHERE pr_item_id = ?",
                    (item["id"],)).fetchone()[0]
                if total_lama + qty > item["qty"] + EPS:
                    raise ValueError(f"Penerimaan {item['deskripsi']} melebihi Qty PR")
                db.execute("INSERT INTO pr_penerimaan_item (penerimaan_id, pr_item_id, qty) VALUES (?, ?, ?)",
                           (rid, item["id"], qty))
            audit(db, g.user["id"], "terima_pr",
                  f"{p['nomor']} · GRN {grn} · {len(diterima)} barang · " +
                  ", ".join(f"{item['material']} {qty:g}" for item, qty in diterima[:12]))
    except ValueError as error:
        flash(str(error), "error")
        return redirect(url_for("pr.detail", pid=pid) + "#penerimaan")
    flash(f"GRN {grn} tersimpan: {len(diterima)} barang diterima.", "ok")
    return redirect(url_for("pr.detail", pid=pid) + "#penerimaan")


@bp.route("/<int:pid>/penerimaan/<int:rid>/hapus", methods=["POST"])
@login_required("admin")
def hapus_penerimaan(pid, rid):
    """Hapus satu pencatatan GRN yang keliru tanpa mengubah transaksi stok."""
    p = _pr(pid)
    db = get_db()
    receipt = db.execute("SELECT * FROM pr_penerimaan WHERE id = ? AND pr_id = ?", (rid, pid)).fetchone()
    if not receipt:
        abort(404)
    with tx(db):
        db.execute("DELETE FROM pr_penerimaan_item WHERE penerimaan_id = ?", (rid,))
        db.execute("DELETE FROM pr_penerimaan WHERE id = ?", (rid,))
        audit(db, g.user["id"], "hapus_penerimaan_pr", f"{p['nomor']} · GRN {receipt['grn']}")
    flash(f"Penerimaan GRN {receipt['grn']} dihapus. Qty kembali menjadi belum datang.", "ok")
    return redirect(url_for("pr.detail", pid=pid) + "#penerimaan")


@bp.route("/<int:pid>/export")
@login_required("admin", "pimpinan")
def export(pid):
    """Semua barang (satu file) atau ?kelompok=... untuk attachment satu PR SAP."""
    p = _pr(pid)
    items = _items(pid)
    kel = request.args.get("kelompok")
    if kel:
        items = [i for i in items if i["kelompok"] == kel]
        if not items:
            abort(404)
    return send_file(prmod.buat_excel(p, items), as_attachment=True, download_name=prmod.nama_file(p, kel),
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@bp.route("/<int:pid>/hapus", methods=["POST"])
@login_required("admin")
def hapus(pid):
    """Hapus draft PR yang belum diajukan beserta seluruh itemnya."""
    p = _pr(pid)
    db = get_db()
    with tx(db):
        db.execute("DELETE FROM pr_penerimaan_item WHERE penerimaan_id IN "
                   "(SELECT id FROM pr_penerimaan WHERE pr_id = ?)", (pid,))
        db.execute("DELETE FROM pr_penerimaan WHERE pr_id = ?", (pid,))
        db.execute("DELETE FROM pr_item WHERE pr_id = ?", (pid,))
        db.execute("DELETE FROM pr_sap WHERE pr_id = ?", (pid,))
        db.execute("DELETE FROM pr WHERE id = ?", (pid,))
        audit(db, g.user["id"], "hapus_pr", p["nomor"])
    flash(f"PR {p['nomor']} dihapus.", "ok")
    return redirect(url_for("pr.daftar"))
