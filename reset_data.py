"""Kosongkan data uji coba sebelum aplikasi dipakai sungguhan.

Mode biasa (kosong total):
    .venv\\Scripts\\python reset_data.py
    Dihapus : material, transaksi, pengembalian, opname, log, foto.

Mode simpan pengeluaran tanggal tertentu:
    .venv\\Scripts\\python reset_data.py --simpan-keluar 2026-10-15
    Disimpan: transaksi KELUAR / TRANSFER KELUAR aktif di tanggal itu (+ foto & pengembalian-nya),
              master material beserta kategori, stok minimum & penanda wajib tukar.
    Dihapus : transaksi lain, opname, log, foto yang tidak dipakai.
    Setelah itu upload tarikan SAP baru di Master SAP dengan opsi "Mulai baru" (otomatis tercentang).

Selalu disimpan: akun pengguna, kebun, kategori, pengaturan laporan.
Sebelum menghapus, database & foto dibackup ke data/backup-YYYYmmdd-HHMMSS/.
"""
import datetime as dt
import os
import shutil
import sqlite3
import sys
from pathlib import Path

DATA = Path(os.environ.get("STOK_DATA_DIR", Path(__file__).resolve().parent / "data"))
KELUAR = "('keluar', 'transfer_keluar')"


def hitung(db, tabel):
    try:
        return db.execute(f"SELECT COUNT(*) FROM {tabel}").fetchone()[0]
    except sqlite3.OperationalError:
        return 0


def main():
    db_path = DATA / "stok.db"
    if not db_path.exists():
        print(f"Database tidak ditemukan: {db_path}")
        return 1
    simpan_tgl = None
    if "--simpan-keluar" in sys.argv:
        simpan_tgl = sys.argv[sys.argv.index("--simpan-keluar") + 1]
        dt.date.fromisoformat(simpan_tgl)  # validasi format YYYY-MM-DD
    pesan = (f"Hapus semua data kecuali pengeluaran tanggal {simpan_tgl} di {DATA}?" if simpan_tgl
             else f"Kosongkan semua material, transaksi, log & foto di {DATA}?")
    if "--ya" not in sys.argv and input(pesan + " Ketik YA untuk lanjut: ").strip().upper() != "YA":
        print("Dibatalkan.")
        return 1

    backup = DATA / f"backup-{dt.datetime.now():%Y%m%d-%H%M%S}"
    backup.mkdir(parents=True)
    db = sqlite3.connect(db_path, timeout=30)
    tujuan = sqlite3.connect(backup / "stok.db")
    db.backup(tujuan)
    tujuan.close()
    if (DATA / "foto").exists():
        shutil.copytree(DATA / "foto", backup / "foto")
    print(f"Backup dibuat: {backup}")

    sebelum = {t: hitung(db, t) for t in ("materials", "transactions", "pengembalian", "opname_sesi", "audit_log")}
    db.execute("PRAGMA foreign_keys = OFF")
    db.execute("BEGIN IMMEDIATE")
    for t in ("opname_item", "opname_sesi", "audit_log"):
        db.execute(f"DELETE FROM {t}")
    if simpan_tgl:
        db.execute(f"DELETE FROM transactions WHERE NOT (tanggal = ? AND jenis IN {KELUAR} AND batal = 0)",
                   (simpan_tgl,))
        db.execute("DELETE FROM pengembalian WHERE batal = 1 OR transaksi_id NOT IN (SELECT id FROM transactions)")
        db.execute("INSERT INTO pengaturan (kunci, nilai) VALUES ('reset_stok_awal', '1') "
                   "ON CONFLICT(kunci) DO UPDATE SET nilai = '1'")
    else:
        for t in ("pengembalian", "transactions", "materials", "penomoran"):
            db.execute(f"DELETE FROM {t}")
        db.execute("DELETE FROM pengaturan WHERE kunci = 'reset_stok_awal'")
    db.execute("COMMIT")
    sesudah = {t: hitung(db, t) for t in sebelum}

    # foto: hapus yang tidak lagi dipakai transaksi / pengembalian
    dipakai = {r[0] for r in db.execute("SELECT foto FROM transactions WHERE foto IS NOT NULL "
                                        "UNION SELECT foto FROM pengembalian WHERE foto IS NOT NULL")}
    try:
        db.execute("VACUUM")
    except sqlite3.OperationalError:
        pass  # aplikasi sedang berjalan; tidak masalah
    db.close()
    folder = DATA / "foto"
    terhapus = 0
    if folder.exists():
        for f in folder.rglob("*"):
            if f.is_file() and f.relative_to(folder).as_posix() not in dipakai:
                f.unlink()
                terhapus += 1
    for f in (DATA / "tmp").glob("*") if (DATA / "tmp").exists() else []:
        if f.is_file():
            f.unlink()

    for t in sebelum:
        print(f"  {t:<14}: {sebelum[t]:>6} -> {sesudah[t]}")
    print(f"  foto dihapus  : {terhapus} (tersisa {len(dipakai)})")
    if simpan_tgl:
        print("Selesai. Restart start.bat, lalu Master SAP -> upload tarikan SAP baru dengan opsi 'Mulai baru'.")
    else:
        print("Selesai. Pengguna, kebun & kategori tetap ada. Restart start.bat lalu upload master SAP terbaru.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
