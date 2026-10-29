# Monitoring Stok

Aplikasi web untuk memantau stok gudang kebun: input transaksi lewat HP (dengan foto bukti), import Excel,
master dari export SAP, edit qty manual untuk selisih fisik/SAP, dan laporan pimpinan (Excel & PDF).
Implementasi mengikuti `UPDATE_v1.1.md` dan template Excel di folder ini.

## Dokumentasi

| Pembaca | Mulai dari |
|---|---|
| Operator / admin gudang | README ini: cara menjalankan dan alur kerja harian. |
| Pengembang baru | [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md): peta modul dan aliran data. |
| Pengembang fitur | [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md): standar kode, UI, pengujian, dan checklist. |
| Pemeriksa aturan | [`docs/BUSINESS_RULES.md`](docs/BUSINESS_RULES.md): keputusan stok, SAP, foto, opname, dan duplikat. |
| Perencana aplikasi offline | [`docs/OFFLINE_SYNC_ROADMAP.md`](docs/OFFLINE_SYNC_ROADMAP.md): alur sinkron pagi, upload akhir hari, konflik, keamanan, dan tahapan APK. |
| Pengguna/pengelola APK | [`docs/MOBILE_APP.md`](docs/MOBILE_APP.md): instalasi uji, operasi offline, panel perangkat, pembaruan, dan build Android. |

## Menjalankan

Kebutuhan: Python 3.11+ (sudah dites di 3.14).

```bash
start.bat
```

Atau secara manual:

```bash
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python run.py
```

- Buka `http://localhost:8000`. Dari HP di jaringan (WiFi) yang sama, buka `http://<IP-komputer>:8000` (IP ditampilkan saat start).
  Izinkan port 8000 di Windows Firewall bila HP tidak bisa membuka.
- Login awal: **admin / admin123**. Segera ganti password lewat menu nama pengguna (kanan atas).
  Password awal bisa diatur dengan environment `STOK_ADMIN_PASSWORD` sebelum start pertama.
- Data tersimpan di folder `data/` (database `stok.db` + folder `foto/`). **Backup folder ini secara berkala.**
  Lokasi bisa diganti dengan environment `STOK_DATA_DIR`. Port bisa diganti dengan `PORT`.

## Urutan pemakaian awal

1. **Master SAP** → upload file export SAP 13 Okt 2026 (format `TEMPLATE_MASTER_SAP.xlsx`). Unrestricted menjadi stok awal.
2. **Master SAP → Master Supplementary** → upload workbook Supplementary PM agar blok dan unit/VRA dapat dicari saat input.
3. **Pengaturan → Kebun** → lengkapi daftar kebun tujuan/asal transfer. Plant dari master SAP otomatis ditambahkan.
4. **Master SAP** → atur kategori (pakai "Atur kategori cepat per Material Group") & stok minimum.
5. **Pengaturan → Pengguna** → buat akun petugas (bisa dikunci ke satu kebun) & pimpinan.

## Fitur

| Menu | Isi |
|---|---|
| Input Transaksi | Form HP: tombol **KELUAR** (default) / **TRANSFER** / **MASUK**. Satu simpan = satu **bon** (BON-yymmdd-NNN) berisi banyak barang, penerima & nomor dokumen; tiap barang tetap tercatat sendiri. Tujuan BON cukup diketik (`kantor`, kode blok, atau kode unit), lalu dikenali dan dikonfirmasi dari Master Supplementary; alias seperti `EX17` diarahkan ke `EXA17`. Tombol **Bagi ke tujuan lain** memungkinkan material yang sama dialokasikan ke beberapa blok dalam satu bon. Pilihan **GOH-STAFF** tersedia otomatis. Mendukung **Foto 1** orang/penerima (wajib untuk KELUAR & transfer kirim) dan **Foto 2** bon/dokumen (opsional); foto dari galeri dikompres dan diberi watermark waktu asli dari metadata. Material berflag/tanggal **EXPIRED** diblokir dari transaksi baru. |
| Stok | Daftar stok berjalan + stok SAP terakhir. Admin: tombol **Ubah Qty** untuk mengisi qty fisik yang benar. |
| Kartu stok (klik material) | Riwayat mutasi dengan saldo berjalan, foto bukti, tombol Koreksi / Batal per transaksi. |
| Stock Opname | Isi qty fisik massal/sesi bersama, termasuk delegasi sementara pembukaan sesi kepada petugas gudang. |
| Selisih SAP | Stok aplikasi − Stok SAP − transaksi belum posting = **selisih nyata**. Upload **MB51 + MB52** untuk mencocokkan transaksi, mengisi nomor dokumen SAP, menemukan transaksi yang terlewat, dan menjaga saldo akhir tetap sama dengan SAP. |
| Transaksi | Filter periode/jenis/kebun/status. Centang → **Tandai sudah posting SAP** (+ no. dokumen SAP). **Export Excel** sesuai filter. Lampirkan foto belakangan. |
| Import Excel | Upload `TEMPLATE_TRANSAKSI_MASUK_TRANSFER.xlsx` → pratinjau & validasi → simpan. Semua baris harus valid. |
| Progress PR | Pantau barang PR yang sudah datang, datang sebagian, dan belum datang. Satu GRN dapat berisi beberapa barang; penerimaan bertahap tersimpan sebagai riwayat tanpa menambah stok dua kali. |
| Laporan | Ringkasan per kebun, transfer antar kebun, stok per kategori, stok menipis. Filter periode/kebun/kategori. Unduh Excel & PDF (A4). |
| Pengaturan → Log | Jejak semua perubahan manual, koreksi, pembatalan, upload & import. |

Peran: **Admin** (semua), **Petugas Gudang** (input HP, lihat stok/transaksi, lampir foto), **Pimpinan** (lihat stok, selisih, laporan).

## Alur harian dengan SAP

1. Pengeluaran gudang dicatat di aplikasi (HP) → stok aplikasi langsung berkurang.
2. Transaksi diinput ke SAP. Export MB51 dan MB52 pada akhir pemeriksaan, lalu buka **Selisih SAP → Rekonsiliasi
   MB51 + MB52**. Isi *Data MB52 per* sesuai jam export. Sistem mengisi status posting, nomor dokumen, movement type,
   dan tanggal posting. Satu transaksi yang pecah menjadi beberapa dokumen SAP ikut dikenali. Secara aman, dokumen
   MB51 pada tanggal yang sama dianggap terjadi setelah jam MB52; centang pilihan khusus bila MB52 memang sudah
   mencakup seluruh dokumen pada tanggal tersebut.
3. Baris MB51 tanpa pasangan menjadi **calon transaksi baru** dan tidak dicentang otomatis. Pilih hanya transaksi yang
   benar-benar belum tercatat (misalnya pengiriman langsung ke kantor). Saat dikonfirmasi, transaksi dibuat sebagai
   sudah posting SAP dan penyesuaian sinkron lama dihitung ulang agar saldo akhir tetap mengikuti MB52.
4. Buka **Selisih SAP**: (1) *Selisih nyata* = tidak ada transaksi yang menjelaskan; (2) *Selisih fisik belum disesuaikan di SAP* = hasil Ubah Qty / opname, sesuaikan di SAP lalu tandai ✓ dari halaman itu; (3) *Transaksi harian belum diinput SAP* = wajar. Yang muncul di *Selisih nyata*
   berarti ada yang tidak cocok (salah qty, transaksi terlewat, beda fisik) — cek kartu stoknya.

Tombol "Samakan / Koreksi mengikuti SAP" menjadikan stok aplikasi = Stok SAP + transaksi belum posting,
jadi pengeluaran yang belum diinput ke SAP tidak "kembali" lagi.

## Stock opname & Berita Acara

1. Admin membuka **Stock Opname → Mulai opname bersama** untuk satu gudang. Selama sesi terbuka, transaksi gudang
   dikunci agar saldo tidak berubah di tengah penghitungan.
2. Beberapa akun **Petugas Gudang** masuk ke sesi yang sama dari HP masing-masing, lalu mengisi barang yang mereka
   hitung. Di HP gunakan kotak **Cari / scan kode material**: ketik kode → Enter → isi qty → Enter untuk lanjut ke
   kode berikutnya, tanpa menggulir atau menggeser tabel. Hitungan tersimpan atas nama petugas; barang yang sudah
   dihitung orang lain terkunci agar tidak tertimpa.
3. Admin menekan **Tutup sesi & posting stok**. Baru pada tahap ini selisih otomatis dibuat sebagai PENYESUAIAN;
   barang dengan fisik sama tetap tercatat di Berita Acara. Opname langsung oleh satu admin tetap tersedia.
4. **Riwayat & Export Berita Acara** → *Excel* (satu sesi), *Excel 1 hari* (semua jenis barang di tanggal itu), atau
   centang beberapa sesi → export. Format mengikuti Berita Acara Perhitungan Fisik Persediaan: satu sheet per jenis
   barang + sheet *Selisih*, siap cetak A4 landscape.
5. Kop & tanda tangan diatur di **Pengaturan → Laporan Opname**.

Kolom selisih memakai konvensi format lama: **Lebih** = kartu stock > fisik, **Kurang** = kartu stock < fisik.

## Pengembalian barang lama (tukar baru)

- Tandai material yang **wajib tukar** (egrek, tabung gas, sepatu, …) di **Master SAP** → kolom *Tukar* (atau massal lewat
  bar bawah). Juga bisa saat *Tambah material baru*.
- Saat input KELUAR barang wajib tukar, isi **↩ Barang lama**: qty + kondisi (Rusak / Aus / Tabung kosong / Hilang /
  Tidak ada — pekerja baru) + foto barang lama (opsional). Barang lain bisa diisi lewat *+ ada barang lama dikembalikan*.
- Catatan dilampirkan ke transaksi & bon KELUAR-nya, **tidak mengubah stok**.
- Menu **Pengembalian**: daftar *belum mengembalikan* (tombol ↩ Catat untuk pengembalian menyusul), riwayat, export Excel.

## Stok minimum cepat & PR pemesanan

- **Master SAP → Stok minimum cepat**: isi minimum untuk semua material atau hanya kode berawalan tertentu
  (mis. `952`). Tanpa centang *Timpa* hanya material yang minimumnya masih 0. Tombol **Reset minimum** mengembalikan
  ke 0 (sesuai awalan; kosong = semua).
- **Menu PR**: buat PR baru (bisa langsung berisi semua barang stok menipis, opsional per awalan kode), tambah barang
  dari stok atau **barang manual** (kode belum ada di stok: isi nama, UoM, qty, harga sendiri).
  Qty awal = 2× minimum − stok; harga = rata-rata SAP; Stock on Hand = stok aplikasi; Periode Pengeluaran dibagi rata
  ke 1–6 bulan sehingga crosscek 0 (bisa diatur manual).
- **Export Excel** mengikuti format "Pendukung PR (Stock, Terakhir GRN, Periode Pemakaian dan Estimasi GI)".

## Barang baru (belum ada di master)

- **Penerimaan sudah diposting di SAP (GR):** upload export SAP terbaru di Master SAP. Material baru otomatis
  ditambahkan dengan stok dari SAP. Jangan input MASUK lagi, karena stoknya akan dobel.
- **Barang datang tapi belum ada di export SAP:** Master SAP → **+ Tambah material baru** (kode harus sama dengan
  kode SAP) → stok 0 → input MASUK. Saat export SAP berikutnya memuat kode tsb, baris yang sama otomatis tersambung
  ke data SAP. Admin juga bisa langsung menambah dari pencarian material di Input Transaksi.

## Edit qty manual (selisih fisik / sistem)

Tiga cara, semuanya hanya untuk admin dan tercatat sebagai transaksi **PENYESUAIAN** (riwayat tidak pernah dihapus):

1. **Ubah Qty** di halaman Stok / Kartu Stok / Selisih SAP. Isi qty sebenarnya; selisih dihitung otomatis.
   Tombol "Samakan dengan SAP" mengisi qty SAP. Wajib pilih alasan.
2. **Stock Opname**: isi qty fisik banyak material sekaligus.
3. **Koreksi / Batal** transaksi yang salah input (mis. salah ketik qty) di Kartu Stok atau Transaksi.
   Ditolak jika membuat stok menjadi minus.

## Perubahan / keputusan terhadap spesifikasi v1.1

| # | Perubahan | Alasan |
|---|---|---|
| 1 | Kunci unik material = **Plant** + Material + Special Stock + Special stock number (spec: tanpa Plant). | Agar master beberapa kebun tidak saling menimpa. Untuk satu kebun hasilnya sama. |
| 2 | Baris SAP dengan kunci sama (beda Storage location/Batch) **dijumlahkan**. | Mencegah stok hilang tertimpa. Muncul peringatan saat upload. |
| 3 | Jenis transaksi ke-5: **`penyesuaian`** (qty bertanda). | Untuk edit qty manual / opname / sinkron SAP dengan jejak audit. |
| 4 | Kolom tambahan `transactions`: `alasan`, `sumber`, `import_ref`, `batal`, `batal_alasan`, `updated_by/at`. Kolom tambahan `materials`: `sap_unrestricted`, `sap_updated_at`, `ada_di_sap`, `tgl_stok_awal`, `kategori_id`, `stok_minimum`. Tabel `kategori`, `users`, `audit_log`. | Pendukung koreksi, perbandingan SAP, kategori manual, login, audit. |
| 5 | Upload master ulang **tidak** mengubah stok aplikasi. Status *posting SAP* per transaksi + jam data SAP dipakai untuk memisahkan selisih nyata dari transaksi yang belum diposting. | Transaksi aplikasi biasanya belum diposting di SAP saat export diambil. |
| 6 | TRANSFER_KELUAR lewat Excel boleh tanpa foto, ditandai "tanpa foto" dan foto dilampirkan belakangan. KELUAR lewat Excel ditolak. | Excel tidak bisa membawa foto, sedangkan foto tetap wajib. |
| 7 | TRANSFER di HP punya pilihan Kirim/Terima. Terima = TRANSFER_MASUK manual. | Spec menyebut TRANSFER_MASUK bisa manual. |
| 8 | Ringkasan per kebun di laporan dalam **nilai Rupiah** (harga rata-rata SAP = Value Unrestricted ÷ Unrestricted). Qty per material ada di sheet "Rincian Mutasi". | Qty beda satuan (L, PC, SAK) tidak bisa dijumlahkan. |
| 9 | Import Excel mencegah dobel: jenis + material + nomor dokumen yang sama ditolak. Kebun dicocokkan dengan kode/nama (awalan "KEBUN" diabaikan). | Mencegah import ulang file yang sama. |

## Masih menunggu dari klien (lihat v1.1 §7)

- Daftar kebun lengkap: bisa langsung diisi di menu Pengaturan → Kebun.
- Format khusus barang masuk jika berbeda dari template.

## Tes

```bash
.venv\Scripts\python -m pytest -q
```
