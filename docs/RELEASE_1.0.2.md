# flowStok 1.0.2 — Rekonsiliasi Aman

Rilis ini memperbaiki dua masalah kritis pada rekonsiliasi SAP dan riwayat mobile.

## Perbaikan

- Rantai plasma yang arahnya sudah dikenali otomatis tidak lagi meminta pilihan arah manual.
- Centang, pilihan arah, dan kebun tujuan tetap tersimpan bila validasi gagal.
- Tersedia pencarian, filter status, **Pilih semua yang aman**, dan penghitung pilihan.
- Baris yang gagal ditandai serta langsung ditampilkan kepada operator.
- Transaksi yang sudah SAP menjadi hanya-baca di mobile.
- Foto tidak dapat ditambahkan lewat HP setelah transaksi direalisasikan SAP.
- Status **Sudah SAP** tidak dapat dibuka kembali lewat HP.
- Foto yang sudah tersimpan di web dapat dilihat dari riwayat setelah sinkron.
- Riwayat menampilkan tanggal, sumber transaksi, petugas pencatat, dan tanggal posting SAP dengan jelas.
- Transaksi impor MB51 ditandai **Impor SAP · hanya baca** agar tidak disalahartikan sebagai input aplikasi.

## Aset GitHub Release

Unggah kedua aset berikut pada Release `v1.0.2`:

1. `flowStok-1.0.2.apk` — aset utama.
2. `MonitoringStok-1.0.2.apk` — nama kompatibilitas untuk pembaru versi lama.

ID aplikasi, penyimpanan offline, dan kunci tanda tangan tidak berubah.
