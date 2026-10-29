# Arsitektur Monitoring Stok

Dokumen ini adalah peta teknis aplikasi. Mulai dari sini sebelum mengubah kode.

## Gambaran umum

Aplikasi menggunakan Flask, Jinja, SQLite, dan JavaScript tanpa build step. Data
operasional disimpan lokal agar aplikasi tetap sederhana untuk lingkungan kebun.

```text
Browser HP / tablet / PC
        │ HTTP + form/JSON
        ▼
Flask blueprint (views_*.py)
        │ validasi dan orkestrasi
        ▼
Modul domain (stock.py, pengembalian.py, pr.py, photos.py)
        │ transaksi SQL
        ▼
SQLite (data/stok.db) + foto (data/foto/)
```

APK Android berada di `mobile/`. Antarmuka dibundel di dalam APK dan menyimpan
antrean/foto pada IndexedDB perangkat. Sinkronisasi memakai API Bearer
`/api/mobile/v1/*` menuju server Flask yang sama melalui Tailscale.

## Struktur direktori

| Lokasi | Tanggung jawab |
|---|---|
| `app/__init__.py` | Membuat aplikasi, konfigurasi, error handler, dan registrasi blueprint. |
| `app/db.py` | Skema SQLite, migrasi kompatibel, koneksi, transaksi, dan audit. |
| `app/stock.py` | Aturan stok dan penyimpanan transaksi. Tidak boleh bergantung pada template. |
| `app/photos.py` | Validasi gambar, EXIF, watermark, kompresi, dan penyimpanan foto. |
| `app/pengembalian.py` | Aturan pengembalian barang lama. |
| `app/pr.py` | Perhitungan dan export Purchase Requisition. |
| `app/excel_io.py` | Pembacaan dan normalisasi file SAP/Excel. |
| `app/report.py`, `app/berita_acara.py` | Pembuatan laporan Excel/PDF dan berita acara. |
| `app/views_main.py` | Route operasional: dashboard, input, stok, transaksi, dan selisih. |
| `app/views_admin.py` | Route administrasi, import, master SAP, dan rekonsiliasi. |
| `app/views_opname.py` | Sesi opname bersama dan berita acara. |
| `app/views_pr.py` | Halaman dan alur PR. |
| `app/mobile_api.py` | Login perangkat, bootstrap sinkron pagi, CORS Capacitor, dan upload BON. |
| `app/mobile_sync.py` | Validasi/idempotensi transaksi offline sebelum masuk stok. |
| `mobile/` | Source antarmuka offline, proyek Capacitor Android, dan skrip build APK. |
| `app/templates/` | Tampilan Jinja. Komponen berulang ada di `_macros.html` dan `_ikon.html`. |
| `app/static/app.css` | Design system dan tata letak responsif. |
| `app/static/app.js` | Interaksi browser tanpa framework/build. |
| `tests/test_alur.py` | Pengujian integrasi alur bisnis utama. |

## Batas tanggung jawab

1. **Route** membaca request, memeriksa hak akses, memanggil modul domain, lalu
   memilih response. Hindari menaruh perhitungan bisnis baru langsung di route.
2. **Modul domain** menerima nilai biasa atau row database dan mengembalikan hasil
   yang dapat diuji. Modul domain tidak merender HTML.
3. **Template** hanya menampilkan data. Jangan melakukan perhitungan stok di Jinja.
4. **JavaScript** meningkatkan kenyamanan, tetapi validasi penting selalu diulang
   di server karena input browser dapat dimodifikasi.
5. **Database** adalah sumber kebenaran. Perubahan beberapa tabel harus berada di
   dalam `with tx(db):` agar berhasil seluruhnya atau batal seluruhnya.

## Alur transaksi keluar

1. Browser memvalidasi isian dasar, mengompres foto, dan memeriksa kemungkinan
   duplikat pada tanggal yang dipilih.
2. Server mengulang seluruh validasi: stok, penerima, expired, kunci input material,
   tujuan SAP, dan foto.
3. `stock.simpan_transaksi` menyimpan setiap item dalam satu transaksi database.
4. Semua item satu penyimpanan mendapat nomor BON yang sama.
5. Foto diproses oleh `photos.save_photo`; path relatifnya disimpan pada transaksi.
6. Pengembalian barang lama dicatat terpisah dan tidak mengubah stok.

## Migrasi database

Skema awal berada di `SCHEMA` dalam `db.py`. Penambahan kolom kompatibel dilakukan
melalui daftar migrasi di file yang sama. Jangan mengubah data produksi secara
destruktif atau menghapus kolom lama. Setiap perubahan skema harus:

1. aman saat dijalankan berulang;
2. tetap bisa membuka database versi sebelumnya;
3. memiliki pengujian alur yang menggunakan kolom baru;
4. dicatat di dokumentasi aturan bisnis bila memengaruhi operator.

## Keamanan dan audit

- Semua form perubahan data memakai CSRF.
- Hak akses diperiksa dengan `login_required` dan pembatasan kebun pengguna.
- Perubahan manual penting dicatat lewat `db.audit`.
- Nama file upload dibuat oleh server; nama asli tidak digunakan sebagai path.
- SQL dinamis hanya untuk nama kolom yang ditentukan aplikasi. Nilai pengguna
  selalu dikirim sebagai parameter query.
- Pesan error pengguna dibuat ringkas; rincian teknis disimpan di `data/error.log`.

## Design system responsif

- **HP:** sampai 600 px; satu kolom, tombol sentuh besar, dialog seperti bottom sheet.
- **Tablet:** 601–900 px; kartu dua kolom bila ruang cukup, navigasi bawah tetap aktif.
- **Desktop:** di atas 900 px; navigasi atas dan tabel penuh.
- Ikon berasal dari macro `app/templates/_ikon.html`, bukan emoji, agar bentuknya
  konsisten pada semua sistem operasi.

## Dokumen terkait

- [Aturan bisnis](BUSINESS_RULES.md)
- [Panduan pengembangan](DEVELOPMENT.md)
- [Panduan operator](../README.md)
- [Aplikasi Android](MOBILE_APP.md)

