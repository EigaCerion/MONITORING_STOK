"""Pengembalian barang lama (tukar baru): validasi & simpan. Tidak mempengaruhi stok."""
from .excel_io import angka

KONDISI = ["Rusak", "Aus / habis pakai", "Tabung / wadah kosong", "Hilang / tidak dikembalikan",
           "Tidak ada — pekerja baru / tambahan"]
HILANG = "Hilang / tidak dikembalikan"
TANPA_BARANG = (HILANG, "Tidak ada — pekerja baru / tambahan")  # boleh qty 0

SQL_BELUM = """t.jenis = 'keluar' AND t.batal = 0 AND m.wajib_kembali = 1
    AND NOT EXISTS (SELECT 1 FROM pengembalian p WHERE p.transaksi_id = t.id AND p.batal = 0)"""


def baca(qty_raw, kondisi, qty_keluar, wajib, nama):
    """Kembalikan (data|None, error|None). data = (qty, kondisi)."""
    kondisi = (kondisi or "").strip()
    qty = angka(qty_raw)
    if not kondisi and (qty is None or qty == 0):
        return None, (f"{nama}: wajib tukar — isi pengembalian barang lama (qty & kondisi)" if wajib else None)
    if kondisi not in KONDISI:
        return None, f"{nama}: pilih kondisi barang lama yang dikembalikan"
    if kondisi in TANPA_BARANG:
        return (qty or 0.0, kondisi), None
    if qty is None or qty <= 0:
        return None, f"{nama}: qty barang lama yang dikembalikan harus lebih dari 0"
    if qty_keluar is not None and qty > qty_keluar:
        return None, f"{nama}: barang lama dikembalikan ({qty:g}) melebihi qty keluar ({qty_keluar:g})"
    return (qty, kondisi), None


def ada_barang(data):
    """True bila benar-benar ada barang lama yang diserahkan (maka foto barang lama wajib)."""
    return bool(data) and data[1] not in TANPA_BARANG and data[0] > 0


def simpan(db, transaksi_id, material_id, tanggal, qty, kondisi, keterangan, foto, oleh_nama, user_id):
    """Simpan bukti pengembalian barang lama dan kembalikan ID barisnya."""
    return db.execute(
        """INSERT INTO pengembalian (transaksi_id, material_id, tanggal, qty, kondisi, keterangan, foto,
               dikembalikan_oleh, created_by) VALUES (?,?,?,?,?,?,?,?,?)""",
        (transaksi_id, material_id, tanggal, qty, kondisi, keterangan or None, foto, oleh_nama or None,
         user_id)).lastrowid


def per_transaksi(db, ids):
    """{transaksi_id: [pengembalian aktif]} untuk ditampilkan di daftar transaksi / kartu stok."""
    ids = list(ids)
    if not ids:
        return {}
    hasil = {}
    for r in db.execute(f"SELECT * FROM pengembalian WHERE batal = 0 AND transaksi_id IN "
                        f"({','.join('?' * len(ids))}) ORDER BY id", ids):
        hasil.setdefault(r["transaksi_id"], []).append(dict(r))
    return hasil
