# Monitoring Stok Mobile 1.0.0 — Stable

Versi stabil pertama aplikasi gudang untuk transaksi dan Stock Opname melalui
HP, termasuk pekerjaan offline dan sinkronisasi aman melalui Tailscale.

## Fitur utama

- Barang keluar dengan tujuan otomatis, pembagian tujuan, dua foto, dan
  pengembalian barang lama.
- Barang masuk dan transfer antar-kebun.
- Daftar stok yang mudah dicari serta histori hasil sinkron.
- Stock Opname bersama dari beberapa HP dengan penguncian hitungan antarpetugas.
- Antrean offline, Sinkron pagi, dan Upload akhir hari.
- Pemeriksaan material expired, material dikunci, stok server, dan transaksi
  identik pada tanggal yang sama.
- Retry idempoten: upload yang terputus dapat dicoba kembali tanpa membuat BON
  atau hitungan opname ganda.
- Pembaruan aplikasi melalui GitHub Releases yang diatur Admin.

## Perbaikan stabilitas

- Penjaga duplikasi diulang di dalam transaksi database sehingga dua HP yang
  mengunggah data identik bersamaan tidak dapat membuat dua transaksi tanpa
  konfirmasi operator.
- Upload yang terhenti ketika aplikasi ditutup dikembalikan ke antrean aman saat
  aplikasi dibuka lagi.
- Konflik stok, opname, foto, serta pengembalian tidak mengubah data sebelum
  validasi server selesai.

## Persyaratan

- Android dan Tailscale.
- Akses private Tailscale yang diberikan Admin.
- Alamat laptop server, username, dan password Monitoring Stok.

## Instalasi

Unggah berkas **`MonitoringStok-1.0.0.apk`** sebagai aset GitHub Release tanpa
mengubah nama. Petugas juga dapat mengunduhnya dari menu **Unduh App** pada
aplikasi web.

Sebelum instalasi pertama versi stabil, simpan/upload seluruh antrean dari APK
uji. Bila tanda tangan APK uji berbeda, Android mungkin meminta APK lama
dihapus terlebih dahulu.
