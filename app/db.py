"""Koneksi SQLite, skema, dan inisialisasi data awal."""
import sqlite3
from flask import current_app, g
from werkzeug.security import generate_password_hash

SCHEMA = """
CREATE TABLE IF NOT EXISTS kebun (
    kode   TEXT PRIMARY KEY,
    nama   TEXT NOT NULL,
    aktif  INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS kategori (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    nama   TEXT NOT NULL UNIQUE,
    urutan INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS users (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    username   TEXT NOT NULL UNIQUE,
    nama       TEXT NOT NULL,
    password   TEXT NOT NULL,
    role       TEXT NOT NULL CHECK (role IN ('admin','petugas','pimpinan')),
    kebun_kode TEXT REFERENCES kebun(kode),
    aktif      INTEGER NOT NULL DEFAULT 1
);

-- Master material mengikuti export SAP (14 kolom) + kolom aplikasi.
-- Kunci unik: Plant + Material + Special Stock + Special stock number.
-- (Plant ditambahkan ke kunci agar master beberapa kebun tidak saling menimpa.)
CREATE TABLE IF NOT EXISTS materials (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    name1              TEXT NOT NULL DEFAULT '',
    material_group     TEXT NOT NULL DEFAULT '',
    material           TEXT NOT NULL,
    deskripsi          TEXT NOT NULL DEFAULT '',
    plant              TEXT NOT NULL DEFAULT '',
    storage_location   TEXT NOT NULL DEFAULT '',
    batch              TEXT NOT NULL DEFAULT '',
    satuan             TEXT NOT NULL DEFAULT '',
    value_unrestricted REAL NOT NULL DEFAULT 0,
    special_stock      TEXT NOT NULL DEFAULT '',
    special_stock_no   TEXT NOT NULL DEFAULT '',
    stock_in_transit   REAL NOT NULL DEFAULT 0,
    material_type      TEXT NOT NULL DEFAULT '',
    stok_awal          REAL NOT NULL DEFAULT 0,
    tgl_stok_awal      TEXT,
    sap_unrestricted   REAL,
    sap_updated_at     TEXT,
    ada_di_sap         INTEGER NOT NULL DEFAULT 1,
    kategori_id        INTEGER REFERENCES kategori(id),
    stok_minimum       REAL NOT NULL DEFAULT 0,
    expired            INTEGER NOT NULL DEFAULT 0,
    expired_at         TEXT,
    input_locked       INTEGER NOT NULL DEFAULT 0,
    input_lock_reason  TEXT,
    created_at         TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at         TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE (plant, material, special_stock, special_stock_no)
);
CREATE INDEX IF NOT EXISTS ix_materials_material ON materials(material);

-- Referensi Supplementary SAP untuk pencarian tujuan pemakaian.
-- code = kode yang dikenal operator (mis. D24/DT05), sap_code = kode SAP (mis. B-034/order VRA).
CREATE TABLE IF NOT EXISTS supplementary_targets (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    plant       TEXT NOT NULL,
    target_type TEXT NOT NULL CHECK (target_type IN ('blok','unit')),
    code        TEXT NOT NULL,
    sap_code    TEXT NOT NULL DEFAULT '',
    name        TEXT NOT NULL DEFAULT '',
    division    TEXT,
    phase       TEXT,
    source      TEXT,
    active      INTEGER NOT NULL DEFAULT 1,
    updated_at  TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE (plant, target_type, code)
);
CREATE INDEX IF NOT EXISTS ix_supplementary_target_search
    ON supplementary_targets(plant, target_type, code, name);

-- jenis: masuk, keluar, transfer_masuk, transfer_keluar, penyesuaian.
-- qty selalu positif kecuali 'penyesuaian' (bertanda: + menambah, - mengurangi).
CREATE TABLE IF NOT EXISTS transactions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    tanggal       TEXT NOT NULL,
    jenis         TEXT NOT NULL CHECK (jenis IN
                    ('masuk','keluar','transfer_masuk','transfer_keluar','penyesuaian')),
    material_id   INTEGER NOT NULL REFERENCES materials(id),
    qty           REAL NOT NULL,
    nomor_dokumen TEXT,
    penerima      TEXT,
    kebun_asal    TEXT,
    kebun_tujuan  TEXT,
    keterangan    TEXT,
    alasan        TEXT,
    foto          TEXT,
    foto_2        TEXT,
    sumber        TEXT NOT NULL DEFAULT 'manual',
    import_ref    TEXT,
    batal         INTEGER NOT NULL DEFAULT 0,
    batal_alasan  TEXT,
    created_by    INTEGER REFERENCES users(id),
    created_at    TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_by    INTEGER REFERENCES users(id),
    updated_at    TEXT
);
CREATE INDEX IF NOT EXISTS ix_trx_material ON transactions(material_id);
CREATE INDEX IF NOT EXISTS ix_trx_tanggal ON transactions(tanggal);
CREATE INDEX IF NOT EXISTS ix_trx_dok ON transactions(nomor_dokumen);

-- Perangkat APK menggunakan token terpisah dari sesi browser. Token mentah
-- hanya dikirim sekali saat login; database menyimpan sidik SHA-256-nya.
CREATE TABLE IF NOT EXISTS mobile_devices (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    device_uuid  TEXT NOT NULL UNIQUE,
    user_id      INTEGER NOT NULL REFERENCES users(id),
    name         TEXT NOT NULL DEFAULT '',
    token_hash   TEXT NOT NULL UNIQUE,
    active       INTEGER NOT NULL DEFAULT 1,
    app_version  TEXT,
    last_seen_at TEXT,
    last_sync_at TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at   TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS ix_mobile_devices_user ON mobile_devices(user_id, active);

-- Satu client_uuid mewakili satu BON offline. Tabel ini adalah pengaman
-- idempotensi: retry akibat sinyal putus mengembalikan BON lama, bukan membuat
-- transaksi baru untuk kedua kalinya.
CREATE TABLE IF NOT EXISTS mobile_submissions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    client_uuid     TEXT NOT NULL UNIQUE,
    device_id       INTEGER NOT NULL REFERENCES mobile_devices(id),
    user_id         INTEGER NOT NULL REFERENCES users(id),
    payload_hash    TEXT NOT NULL,
    bon             TEXT NOT NULL,
    transaction_ids TEXT NOT NULL,
    received_at     TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS ix_mobile_submissions_device
    ON mobile_submissions(device_id, received_at);

CREATE TABLE IF NOT EXISTS mobile_sync_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id  INTEGER NOT NULL REFERENCES mobile_devices(id),
    user_id    INTEGER NOT NULL REFERENCES users(id),
    action     TEXT NOT NULL,
    detail     TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS ix_mobile_sync_log_device
    ON mobile_sync_log(device_id, created_at);

-- Satu transaksi aplikasi dapat terpecah menjadi beberapa dokumen material SAP.
-- Tabel ini juga menyimpan rincian asli MB51 tanpa memaksa kolom sap_doc menjadi satu nomor saja.
CREATE TABLE IF NOT EXISTS transaction_sap_docs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id INTEGER NOT NULL REFERENCES transactions(id),
    sap_doc        TEXT NOT NULL,
    movement_type  TEXT,
    posting_date   TEXT,
    qty            REAL NOT NULL,
    teks_sap       TEXT,
    created_at     TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE (transaction_id, sap_doc, movement_type, posting_date, qty)
);
CREATE INDEX IF NOT EXISTS ix_trx_sap_docs_trx ON transaction_sap_docs(transaction_id);
CREATE INDEX IF NOT EXISTS ix_trx_sap_docs_doc ON transaction_sap_docs(sap_doc);

-- Keputusan operator untuk pergerakan SAP yang berpotensi transfer/transit.
-- Baris transit dicatat di sini tanpa membuat transaksi stok, agar tidak muncul lagi
-- pada rekonsiliasi berikutnya dan tetap memiliki jejak pemeriksa.
CREATE TABLE IF NOT EXISTS sap_movement_reviews (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    review_key       TEXT NOT NULL UNIQUE,
    sap_doc          TEXT NOT NULL,
    posting_date     TEXT NOT NULL,
    material         TEXT NOT NULL,
    plant            TEXT NOT NULL,
    storage_location TEXT NOT NULL DEFAULT '',
    movement_type    TEXT NOT NULL,
    qty              REAL NOT NULL,
    special_stock    TEXT NOT NULL DEFAULT '',
    special_stock_no TEXT NOT NULL DEFAULT '',
    decision         TEXT NOT NULL CHECK (decision IN
                       ('transit','masuk_store','keluar_store','transfer_masuk','transfer_keluar')),
    pihak            TEXT,
    transaction_id   INTEGER REFERENCES transactions(id),
    source_file      TEXT,
    reviewed_by      INTEGER REFERENCES users(id),
    reviewed_at      TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS ix_sap_reviews_doc ON sap_movement_reviews(sap_doc);
CREATE INDEX IF NOT EXISTS ix_sap_reviews_trx ON sap_movement_reviews(transaction_id);

-- Stock opname: satu sesi = satu kali hitung fisik untuk satu jenis barang (Berita Acara).
CREATE TABLE IF NOT EXISTS opname_sesi (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    tanggal      TEXT NOT NULL,
    jenis_barang TEXT NOT NULL,
    lokasi       TEXT,
    plant        TEXT,
    status       TEXT NOT NULL DEFAULT 'selesai', -- terbuka = hitung bersama; selesai = stok sudah diposting
    keterangan   TEXT,
    created_by   INTEGER REFERENCES users(id),
    closed_by    INTEGER REFERENCES users(id),
    closed_at    TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
-- Semua material yang dihitung tercatat, termasuk yang fisiknya sama dengan kartu stok.
CREATE TABLE IF NOT EXISTS opname_item (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    sesi_id      INTEGER NOT NULL REFERENCES opname_sesi(id),
    material_id  INTEGER NOT NULL REFERENCES materials(id),
    stok_buku    REAL NOT NULL,          -- stok aplikasi (kartu stok) sebelum opname
    stok_sap     REAL,                   -- stok SAP saat opname (informasi)
    fisik        REAL NOT NULL,
    keterangan   TEXT,
    trx_id       INTEGER REFERENCES transactions(id),  -- transaksi PENYESUAIAN bila ada selisih
    counted_by   INTEGER REFERENCES users(id),
    counted_at   TEXT
);
CREATE INDEX IF NOT EXISTS ix_opname_item_sesi ON opname_item(sesi_id);

-- Hak sementara bagi petugas untuk membuka sesi opname pada gudangnya sendiri.
-- PRIMARY KEY user_id memastikan satu petugas hanya memiliki satu delegasi aktif/terbaru.
CREATE TABLE IF NOT EXISTS opname_delegasi (
    user_id     INTEGER PRIMARY KEY REFERENCES users(id),
    plant       TEXT NOT NULL REFERENCES kebun(kode),
    valid_until TEXT NOT NULL,
    granted_by  INTEGER NOT NULL REFERENCES users(id),
    granted_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS ix_opname_delegasi_masa ON opname_delegasi(valid_until, plant);

-- Pengembalian barang lama saat tukar baru (egrek, gas, sepatu, ...). Tidak mengubah stok:
-- barang bekas/rusak hanya dicatat sebagai bukti, dilampirkan ke transaksi KELUAR (bon) yang menggantinya.
CREATE TABLE IF NOT EXISTS pengembalian (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    transaksi_id      INTEGER NOT NULL REFERENCES transactions(id),
    material_id       INTEGER NOT NULL REFERENCES materials(id),
    tanggal           TEXT NOT NULL,
    qty               REAL NOT NULL DEFAULT 0,
    kondisi           TEXT NOT NULL,
    keterangan        TEXT,
    foto              TEXT,
    dikembalikan_oleh TEXT,
    batal             INTEGER NOT NULL DEFAULT 0,
    created_by        INTEGER REFERENCES users(id),
    created_at        TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS ix_kembali_trx ON pengembalian(transaksi_id);

-- Penghitung nomor dokumen (mis. bon per hari). Nomor tidak pernah dipakai ulang walau bon digabung.
CREATE TABLE IF NOT EXISTS penomoran (
    kunci TEXT PRIMARY KEY,
    nilai INTEGER NOT NULL
);

-- PR (Purchase Request) pemesanan barang ke pusat
CREATE TABLE IF NOT EXISTS pr (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    nomor         TEXT NOT NULL UNIQUE,         -- nomor internal PR-yymm-NNN
    tanggal       TEXT NOT NULL,
    plant         TEXT NOT NULL,
    nomor_sap     TEXT,                         -- nomor PR di SAP (diisi setelah dibuat di SAP)
    judul         TEXT,
    keterangan    TEXT,
    bulan_default INTEGER NOT NULL DEFAULT 3,   -- dibagi ke berapa bulan (Periode Pengeluaran)
    status        TEXT NOT NULL DEFAULT 'draft',
    created_by    INTEGER REFERENCES users(id),
    created_at    TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at    TEXT
);
CREATE TABLE IF NOT EXISTS pr_item (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    pr_id             INTEGER NOT NULL REFERENCES pr(id),
    material_id       INTEGER REFERENCES materials(id),   -- kosong = barang manual (belum ada di stok)
    kode              TEXT,                               -- isian manual bila material_id kosong
    deskripsi         TEXT,
    satuan            TEXT,
    qty               REAL NOT NULL DEFAULT 0,
    harga             REAL,                     -- harga satuan estimasi (default rata-rata SAP)
    stok              REAL NOT NULL DEFAULT 0,  -- stock on hand saat PR dibuat / disegarkan
    terakhir_purchase TEXT,
    periode           TEXT,
    bulan_n           INTEGER NOT NULL DEFAULT 3,
    bulan_auto        INTEGER NOT NULL DEFAULT 1,
    b1 REAL, b2 REAL, b3 REAL, b4 REAL, b5 REAL, b6 REAL,
    urutan            INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_pr_item_pr ON pr_item(pr_id);
-- Satu PR aplikasi bisa menjadi beberapa PR di SAP (dipisah per kelompok/kategori barang)
CREATE TABLE IF NOT EXISTS pr_sap (
    pr_id     INTEGER NOT NULL REFERENCES pr(id),
    kelompok  TEXT NOT NULL,
    nomor_sap TEXT,
    PRIMARY KEY (pr_id, kelompok)
);

-- Riwayat kedatangan barang PR. Satu GRN dapat memuat beberapa item dan satu
-- item PR dapat diterima bertahap melalui beberapa GRN.
CREATE TABLE IF NOT EXISTS pr_penerimaan (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    pr_id      INTEGER NOT NULL REFERENCES pr(id),
    tanggal    TEXT NOT NULL,
    grn        TEXT NOT NULL,
    keterangan TEXT,
    created_by INTEGER REFERENCES users(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE (pr_id, grn)
);
CREATE INDEX IF NOT EXISTS ix_pr_penerimaan_pr ON pr_penerimaan(pr_id, tanggal);

CREATE TABLE IF NOT EXISTS pr_penerimaan_item (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    penerimaan_id  INTEGER NOT NULL REFERENCES pr_penerimaan(id),
    pr_item_id     INTEGER NOT NULL REFERENCES pr_item(id),
    qty            REAL NOT NULL CHECK (qty > 0),
    UNIQUE (penerimaan_id, pr_item_id)
);
CREATE INDEX IF NOT EXISTS ix_pr_penerimaan_item_pr_item ON pr_penerimaan_item(pr_item_id);

CREATE TABLE IF NOT EXISTS pengaturan (
    kunci TEXT PRIMARY KEY,
    nilai TEXT
);

CREATE TABLE IF NOT EXISTS audit_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    waktu      TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    user_id    INTEGER REFERENCES users(id),
    aksi       TEXT NOT NULL,
    detail     TEXT
);

"""

# View dibuat ulang setiap start agar perubahan definisi ikut terpasang di database lama.
VIEWS = """
DROP VIEW IF EXISTS v_stok;
DROP VIEW IF EXISTS v_stok_dasar;
DROP VIEW IF EXISTS v_sap_pending;

-- Stok berjalan = stok awal + seluruh transaksi yang tidak dibatalkan.
CREATE VIEW v_stok_dasar AS
SELECT m.*,
       m.stok_awal + COALESCE((
           SELECT SUM(CASE t.jenis
                        WHEN 'masuk' THEN t.qty
                        WHEN 'transfer_masuk' THEN t.qty
                        WHEN 'keluar' THEN -t.qty
                        WHEN 'transfer_keluar' THEN -t.qty
                        ELSE t.qty END)
           FROM transactions t
           WHERE t.material_id = m.id AND t.batal = 0), 0) AS stok
FROM materials m;

-- Transaksi yang belum tercermin di file SAP terakhir yang diupload (sinkron_sap tidak dihitung):
--   belum  = belum diinput ke SAP (belum ditandai posting)
--   tunggu = sudah diinput ke SAP (✓) tetapi SETELAH jam data file SAP -> file SAP belum memuatnya
CREATE VIEW v_sap_pending AS
SELECT t.material_id,
       SUM(CASE WHEN t.sap_posted = 0 THEN CASE t.jenis WHEN 'masuk' THEN t.qty WHEN 'transfer_masuk' THEN t.qty
                        WHEN 'keluar' THEN -t.qty WHEN 'transfer_keluar' THEN -t.qty ELSE t.qty END ELSE 0 END) AS belum,
       SUM(t.sap_posted = 0) AS n_belum,
       SUM(CASE WHEN t.sap_posted = 0 AND t.jenis = 'penyesuaian' THEN t.qty ELSE 0 END) AS fisik,
       SUM(t.sap_posted = 0 AND t.jenis = 'penyesuaian') AS n_fisik,
       SUM(CASE WHEN t.sap_posted = 1 THEN CASE t.jenis WHEN 'masuk' THEN t.qty WHEN 'transfer_masuk' THEN t.qty
                        WHEN 'keluar' THEN -t.qty WHEN 'transfer_keluar' THEN -t.qty ELSE t.qty END ELSE 0 END) AS tunggu,
       SUM(t.sap_posted = 1) AS n_tunggu
FROM transactions t JOIN materials m ON m.id = t.material_id
WHERE t.batal = 0 AND t.sumber <> 'sinkron_sap'
  AND (t.sap_posted = 0 OR t.sap_posted_at > COALESCE(m.sap_updated_at, ''))
GROUP BY t.material_id;

-- sap_perkiraan = stok di file SAP + transaksi yang sudah diinput ke SAP setelah file itu dibuat
--                 (perkiraan stok SAP saat ini, terverifikasi saat export SAP terbaru diupload).
-- pending_sap   = transaksi yang BELUM diinput ke SAP.
-- selisih_nyata = stok aplikasi - SAP perkiraan - belum diinput. 0 = cocok.
CREATE VIEW v_stok AS
SELECT d.*,
       COALESCE(p.belum, 0) AS pending_sap, COALESCE(p.n_belum, 0) AS n_pending,
       COALESCE(p.n_belum, 0) AS n_belum_posting,
       -- selisih_fisik: Ubah Qty / opname yang belum disesuaikan di SAP (perlu tindakan di SAP)
       COALESCE(p.fisik, 0) AS selisih_fisik, COALESCE(p.n_fisik, 0) AS n_selisih_fisik,
       -- transaksi harian (masuk/keluar/transfer) yang belum diinput SAP (wajar)
       COALESCE(p.belum, 0) - COALESCE(p.fisik, 0) AS pending_rutin,
       COALESCE(p.n_belum, 0) - COALESCE(p.n_fisik, 0) AS n_pending_rutin,
       COALESCE(p.tunggu, 0) AS tunggu_sap, COALESCE(p.n_tunggu, 0) AS n_tunggu_upload,
       CASE WHEN d.sap_unrestricted IS NULL THEN NULL
            ELSE ROUND(d.sap_unrestricted + COALESCE(p.tunggu, 0), 6) END AS sap_perkiraan,
       CASE WHEN d.sap_unrestricted IS NULL THEN NULL
            ELSE ROUND(d.stok - d.sap_unrestricted - COALESCE(p.belum, 0) - COALESCE(p.tunggu, 0), 6)
       END AS selisih_nyata
FROM v_stok_dasar d LEFT JOIN v_sap_pending p ON p.material_id = d.id;
"""

# Kolom yang ditambahkan setelah versi pertama: (tabel, kolom, definisi)
MIGRASI = [
    ("transactions", "sap_posted", "INTEGER NOT NULL DEFAULT 0"),
    ("transactions", "sap_posted_at", "TEXT"),
    ("transactions", "sap_posted_by", "INTEGER REFERENCES users(id)"),
    ("transactions", "sap_doc", "TEXT"),
    ("transactions", "sap_movement_type", "TEXT"),
    ("transactions", "sap_posting_date", "TEXT"),
    ("transactions", "bon", "TEXT"),   # satu bon (input HP) bisa berisi banyak barang
    ("transactions", "foto_2", "TEXT"),  # foto transaksi kedua (opsional: bon/dokumen)
    ("transactions", "tujuan_tipe", "TEXT"),       # blok / unit / workshop / office / staff / damkar
    ("transactions", "tujuan_kode", "TEXT"),       # kode blok, unit, atau cost center SAP
    ("transactions", "aktivitas_kode", "TEXT"),    # work type SAP, khususnya Damkar 330/331
    ("materials", "wajib_kembali", "INTEGER NOT NULL DEFAULT 0"),  # keluar wajib tukar barang lama
    ("materials", "expired", "INTEGER NOT NULL DEFAULT 0"),         # blokir transaksi material kedaluwarsa
    ("materials", "expired_at", "TEXT"),                            # tanggal kedaluwarsa (opsional)
    ("materials", "input_locked", "INTEGER NOT NULL DEFAULT 0"),    # blokir pemakaian kode untuk input baru
    ("materials", "input_lock_reason", "TEXT"),                     # alasan yang ditampilkan ke operator
    ("opname_sesi", "plant", "TEXT"),                              # gudang untuk sesi hitung bersama
    ("opname_sesi", "status", "TEXT NOT NULL DEFAULT 'selesai'"),  # terbuka / selesai
    ("opname_sesi", "closed_by", "INTEGER REFERENCES users(id)"),
    ("opname_sesi", "closed_at", "TEXT"),
    ("opname_item", "counted_by", "INTEGER REFERENCES users(id)"),
    ("opname_item", "counted_at", "TEXT"),
    ("pr_item", "kelompok", "TEXT"),   # kelompok PR SAP (default: kategori / material group)
]

# Kop & tanda tangan Berita Acara Stock Opname (bisa diubah di Pengaturan -> Laporan Opname)
DEFAULT_PENGATURAN = {
    "nama_perusahaan": "PT CITRA MAHKOTA",
    "unit_operasional": "CMA1",
    "lokasi": "CMA1",
    "dibuat_nama": "YUSUF ARISTOKRAT",
    "dibuat_jabatan": "SUPPORT",
    "diperiksa_nama": "RAPMI ODONG",
    "diperiksa_jabatan": "KTU",
    "disetujui_nama": "M.MANSUR",
    "disetujui_jabatan": "ESTATE MANAGER",
}

# Konfigurasi APK dikelola dari panel Admin dan ikut terkirim saat sinkron pagi.
# Repositori sengaja kosong agar alamat GitHub tidak pernah ditebak aplikasi.
MOBILE_DEFAULTS = {
    "mobile_github_repo": "",
    "mobile_min_version": "0.1.0",
}

DEFAULT_KATEGORI = ["Racun", "Beras", "Alat Panen", "TK Panen", "ATK", "BBM", "Oli", "Lainnya"]


def get_db():
    """Kembalikan satu koneksi SQLite per request dengan foreign key dan WAL aktif."""
    if "db" not in g:
        conn = sqlite3.connect(
            current_app.config["DATABASE"], timeout=15, isolation_level=None
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        g.db = conn
    return g.db


def close_db(_exc=None):
    """Tutup koneksi database request saat application context berakhir."""
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


class tx:
    """Context manager transaksi tulis (BEGIN IMMEDIATE) agar validasi stok aman dari race."""

    def __init__(self, db):
        """Simpan koneksi yang akan digunakan selama transaksi tulis."""
        self.db = db

    def __enter__(self):
        """Mulai transaksi tulis eksklusif dan kembalikan koneksinya."""
        self.db.execute("BEGIN IMMEDIATE")
        return self.db

    def __exit__(self, exc_type, *_):
        """Commit bila berhasil atau rollback bila blok mengangkat exception."""
        self.db.execute("ROLLBACK" if exc_type else "COMMIT")
        return False


def init_db(app):
    """Pasang skema, migrasi kompatibel, view, dan data awal pada startup."""
    with app.app_context():
        db = get_db()
        db.executescript(SCHEMA)
        baru = set()
        for tabel, kolom, definisi in MIGRASI:
            ada = {r[1] for r in db.execute(f"PRAGMA table_info({tabel})")}
            if kolom not in ada:
                db.execute(f"ALTER TABLE {tabel} ADD COLUMN {kolom} {definisi}")
                baru.add(kolom)
        if "sap_posted" in baru:
            # penyesuaian "Samakan dengan SAP" versi lama -> tandai sebagai sinkron SAP
            db.execute("UPDATE transactions SET sumber = 'sinkron_sap', sap_posted = 1, "
                       "sap_posted_at = created_at WHERE alasan = 'Samakan dengan SAP'")
        db.execute("CREATE INDEX IF NOT EXISTS ix_trx_bon ON transactions(bon)")
        db.execute("CREATE INDEX IF NOT EXISTS ix_trx_sap_doc ON transactions(sap_doc)")
        db.execute("CREATE INDEX IF NOT EXISTS ix_opname_sesi_status_plant ON opname_sesi(status, plant)")
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_opname_sesi_aktif_plant ON opname_sesi(plant) "
                   "WHERE status = 'terbuka' AND plant IS NOT NULL")
        db.executescript(VIEWS)
        if db.execute("SELECT COUNT(*) FROM kategori").fetchone()[0] == 0:
            for i, nama in enumerate(DEFAULT_KATEGORI):
                db.execute("INSERT INTO kategori (nama, urutan) VALUES (?, ?)", (nama, i))
        if db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
            db.execute(
                "INSERT INTO users (username, nama, password, role) VALUES (?,?,?, 'admin')",
                ("admin", "Administrator", generate_password_hash(app.config["ADMIN_DEFAULT_PASSWORD"])),
            )
        for k, v in DEFAULT_PENGATURAN.items():
            db.execute("INSERT OR IGNORE INTO pengaturan (kunci, nilai) VALUES (?, ?)", (k, v))
        for k, v in MOBILE_DEFAULTS.items():
            db.execute("INSERT OR IGNORE INTO pengaturan (kunci, nilai) VALUES (?, ?)", (k, v))
        close_db()


def audit(db, user_id, aksi, detail=""):
    """Tambahkan satu jejak audit untuk perubahan penting oleh pengguna."""
    db.execute("INSERT INTO audit_log (user_id, aksi, detail) VALUES (?,?,?)", (user_id, aksi, detail))


def opname_aktif(db, plant):
    """Sesi opname bersama yang sedang mengunci satu gudang, bila ada."""
    if not plant:
        return None
    return db.execute("SELECT * FROM opname_sesi WHERE status = 'terbuka' AND plant = ? ORDER BY id DESC LIMIT 1",
                      (plant,)).fetchone()


def delegasi_opname_aktif(db, user_id, plant=None):
    """Delegasi buka sesi yang belum kedaluwarsa untuk petugas dan gudang terkait."""
    sql = """SELECT d.*, u.nama AS petugas_nama, a.nama AS pemberi_nama, k.nama AS kebun_nama
             FROM opname_delegasi d JOIN users u ON u.id = d.user_id
             LEFT JOIN users a ON a.id = d.granted_by LEFT JOIN kebun k ON k.kode = d.plant
             WHERE d.user_id = ? AND u.aktif = 1 AND u.role = 'petugas'
               AND d.valid_until >= datetime('now','localtime')"""
    args = [user_id]
    if plant:
        sql += " AND d.plant = ?"
        args.append(plant)
    return db.execute(sql, args).fetchone()


def pengaturan(db):
    """Kembalikan seluruh pengaturan laporan sebagai dictionary key-value."""
    hasil = dict(DEFAULT_PENGATURAN)
    hasil.update({r["kunci"]: r["nilai"] for r in db.execute("SELECT kunci, nilai FROM pengaturan")})
    return hasil


def mobile_pengaturan(db):
    """Kembalikan konfigurasi APK yang aman dibagikan kepada perangkat."""
    hasil = dict(MOBILE_DEFAULTS)
    hasil.update({r["kunci"]: r["nilai"] for r in db.execute(
        "SELECT kunci, nilai FROM pengaturan WHERE kunci LIKE 'mobile_%'"
    )})
    return hasil
