# Aplikasi Android Monitoring Stok

Versi aktif: **0.1.2 (uji lapangan)**.

## Cakupan versi awal

- Login memakai akun Admin/Petugas Gudang yang sudah ada.
- Server tetap laptop Monitoring Stok melalui alamat Tailscale.
- Sinkron pagi mengunduh master material, saldo, material terkunci/expired, dan
  Supplementary blok/unit.
- Transaksi keluar dapat disimpan tanpa jaringan, termasuk banyak barang, satu
  material yang dibagi ke beberapa tujuan, pengembalian barang lama, dan dua
  foto transaksi.
- Upload akhir hari mengirim antrean satu per satu. `client_uuid` memastikan
  retry tidak membuat BON kedua walaupun jawaban server terputus.
- Transaksi identik pada tanggal yang sama ditahan untuk konfirmasi operator.
- Pengembalian barang lama dapat dicatat untuk material wajib tukar maupun
  material biasa. Foto diwajibkan hanya bila barang fisik benar-benar
  diserahkan; kondisi hilang/pekerja baru tidak memerlukan foto barang lama.
- Sinkron pagi mengunduh daftar barang wajib tukar yang belum kembali.
  Pengembalian yang menyusul dapat dicatat offline dari APK dan diunggah
  idempoten melalui antrean yang sama.
- Satu material dapat dibagi ke beberapa tujuan dengan tombol **Bagi ke tujuan
  lain**, dan aplikasi menampilkan total pembagian serta sisa stok.
- Tujuan BON dicari seperti versi web: kode blok/unit, alias EX ke EXA,
  Kantor/Staff/Workshop, dan AFD. Hanya tujuan dari master Supplementary yang
  dapat dikonfirmasi; AFD tetap meminta operator memilih blok yang benar.
- Panel **Kelola → Aplikasi Mobile** mengatur GitHub Releases dan dapat
  menonaktifkan perangkat.
- Pemeriksa pembaruan membaca link GitHub dari panel Admin. APK yang dikenali
  harus bernama `MonitoringStok-x.y.z.apk`.

Transaksi masuk, transfer, PR/GRN, dan Stock Opname offline belum ada pada versi
seri 0.1. Fitur tersebut tetap dapat digunakan melalui aplikasi web dan akan masuk
setelah transaksi keluar lolos uji lapangan.

## Alur operator

1. Pastikan Tailscale di HP dan laptop menyala.
2. Buka APK, masukkan alamat Tailscale laptop sekali, lalu login.
3. Tekan **Sinkron sekarang** sebelum mulai bekerja.
4. Catat transaksi. Tombol simpan selalu memasukkan data ke antrean HP terlebih
   dahulu, sehingga sinyal putus tidak menghilangkan input.
5. Saat pulang, buka **Upload**, aktifkan Tailscale, lalu tekan **Upload semua**.
6. Pastikan angka antrean aktif menjadi nol. Riwayat yang sudah terkirim tetap
   terlihat dengan nomor BON server.

## Panel Admin

Halaman **Kelola → Aplikasi Mobile** menyediakan:

- link repositori GitHub, boleh dikosongkan sampai repositori siap;
- versi minimum yang diizinkan;
- perangkat, pengguna, versi APK, waktu sinkron terakhir, dan jumlah upload;
- tombol menonaktifkan perangkat hilang atau tidak lagi digunakan.

Perangkat yang dinonaktifkan tidak dapat memakai token lama maupun login ulang.
Admin harus mengaktifkannya kembali dari panel.

## Build APK

APK uji:

```powershell
cd D:\CODE\monitoring_stok\mobile
powershell -ExecutionPolicy Bypass -File scripts\build-android.ps1 -ModeDebug
```

Hasil uji langsung: `mobile/dist-apk/MonitoringStok-0.1.2-debug.apk`. Untuk
perangkat uji yang sudah memakai tanda tangan debug yang sama, aset GitHub
Releases disalin dengan nama `MonitoringStok-0.1.2.apk` agar dikenali pemeriksa
pembaruan aplikasi.

APK produksi membutuhkan keystore khusus dan permanen di luar repositori:

```text
D:\CODE\key\monitoring-stok\key.properties
D:\CODE\key\monitoring-stok\monitoring-stok-rilis.keystore
```

`key.properties` berisi `storeFile`, `storePassword`, `keyAlias`, dan
`keyPassword`. Berkas tersebut tidak boleh masuk repositori atau hilang. Semua
pembaruan harus ditandatangani kunci yang sama; mengganti kunci memaksa uninstall
dan menghapus database antrean HP.

Setelah kunci siap:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build-android.ps1
```

Naikkan `version` di `mobile/package.json` sebelum setiap rilis baru. Unggah APK
rilis ke GitHub Releases tanpa mengubah nama berkasnya.

## Pemulihan kegagalan

- Status **Mengunggah** yang tertinggal karena aplikasi ditutup dikembalikan ke
  **Menunggu upload** saat APK dibuka lagi.
- Upload ulang memakai `client_uuid` yang sama. Bila server sudah menerima data,
  server mengembalikan BON lama tanpa mengurangi stok lagi.
- Foto baru dibuang dari antrean lokal setelah server menjawab berhasil.
- Ganti akun tidak menghapus antrean; hanya akun pembuat yang boleh mengirimnya.
