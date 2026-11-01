# Tutorial flowStok Mobile 1.0.2

## Persiapan

1. Gunakan HP Android dengan ruang kosong untuk aplikasi dan foto transaksi.
2. Pasang dan aktifkan Tailscale.
3. Jika sudah mempunyai Tailscale, minta **akses private kepada Admin**. Status
   Tailscale aktif saja belum membuat HP dapat melihat laptop server.
4. Minta alamat Tailscale laptop server, username, dan password flowStok
   kepada Admin.

## Instalasi

1. Login ke aplikasi web sebagai Petugas Gudang.
2. Buka menu **Unduh App**.
3. Tekan **Unduh APK stabil terbaru**.
4. Buka hasil unduhan dan pilih **Instal**. Jika Android meminta izin, izinkan
   pemasangan dari browser yang digunakan untuk mengunduh.
5. Jangan memasang APK dari link atau pengirim lain.

## Login pertama

1. Buka Tailscale dan pastikan sudah tersambung ke jaringan private perusahaan.
2. Buka flowStok.
3. Masukkan alamat server Tailscale yang diberikan Admin, misalnya
   `http://nama-laptop.tailxxxx.ts.net:8000`.
4. Isi nama perangkat, username, dan password, lalu tekan **Hubungkan dan login**.
5. Alamat server disimpan di HP dan tidak perlu diketik setiap hari.

## Alur kerja harian

### Sebelum bekerja

1. Aktifkan Tailscale.
2. Tekan **Sinkron sekarang**.
3. Pastikan aplikasi menampilkan data sinkron hari ini. Material, stok, tujuan,
   histori, dan sesi opname kemudian dapat digunakan tanpa sinyal.

### Saat bekerja

- **Barang Keluar:** pilih barang, qty, penerima, dan tujuan. Foto penerima wajib.
- **Barang Masuk:** isi nomor dokumen/GRN dan barang yang diterima.
- **Transfer:** pilih arah, kebun lawan, dokumen, dan penerima bila mengirim.
- **Stok:** cari kode atau nama material dan lihat saldo hasil sinkron terakhir.
- **Histori:** lihat transaksi server dari sinkron terakhir.
- **Stock Opname:** buka sesi aktif, cari kode material, isi qty fisik, lalu simpan
  ke antrean. Material yang sudah dihitung petugas lain tidak boleh ditimpa.

Semua input pertama-tama disimpan di HP. Jangan menghapus antrean saat jaringan
putus.

### Sebelum pulang

1. Aktifkan Tailscale dan pastikan laptop server dapat dihubungi.
2. Buka menu **Upload**.
3. Tekan **Upload semua**.
4. Tinjau peringatan kemungkinan duplikat sebelum memilih **Ya, tetap upload**.
5. Pastikan antrean aktif menjadi **0** sebelum selesai bekerja.

## Jika sinkron atau upload gagal

1. Periksa data internet/Wi-Fi.
2. Pastikan Tailscale aktif dan akses private sudah disetujui Admin.
3. Periksa alamat laptop pada Pengaturan aplikasi.
4. Pastikan laptop menyala dan server flowStok sedang berjalan.
5. Coba upload kembali. Retry memakai identitas transaksi yang sama sehingga
   tidak membuat BON kedua.
6. Jika tetap gagal, jangan hapus antrean; tunjukkan pesan error kepada Admin.
