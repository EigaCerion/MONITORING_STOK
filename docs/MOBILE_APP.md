# Aplikasi Android flowStok

Versi aktif: **1.0.2 (stabil)**.

## Cakupan versi awal

- Login memakai akun Admin/Petugas Gudang yang sudah ada.
- Server tetap laptop flowStok melalui alamat Tailscale.
- Sinkron pagi mengunduh master material, saldo, material terkunci/expired, dan
  Supplementary blok/unit.
- Transaksi keluar dapat disimpan tanpa jaringan, termasuk banyak barang, satu
  material yang dibagi ke beberapa tujuan, pengembalian barang lama, dan dua
  foto transaksi.
- Transaksi masuk dapat disimpan tanpa jaringan dengan banyak barang, tanggal,
  nomor dokumen/GRN, keterangan, dan dua foto opsional. Upload tetap idempoten,
  memiliki penjaga transaksi identik pada hari yang sama, dan langsung
  memperbarui saldo server.
- Transfer antar-kebun dapat dicatat offline untuk dua arah. **Kirim** wajib
  memilih kebun tujuan, nomor dokumen, penerima, Foto 1, dan stok yang cukup.
  **Terima** wajib memilih kebun asal dan nomor dokumen; dua foto bersifat
  opsional. Daftar kebun aktif ikut disimpan saat Sinkron pagi.
- Layar **Stok** menampilkan saldo hasil Sinkron pagi tanpa jaringan, dengan
  pencarian kode/nama, filter tersedia/menipis/habis/dikunci, urutan saldo,
  lokasi penyimpanan, batas minimum, serta penanda expired dan kunci Admin.
- Layar **Stock Opname** membuka sesi untuk Admin atau petugas yang menerima
  delegasi, menampilkan sesi aktif dari Sinkron pagi, dan menyimpan hitungan ke
  antrean HP. Pencarian memprioritaskan kode material yang sama persis sehingga
  barang yang dicari muncul paling atas tanpa menggulir daftar panjang.
- Hitungan material tidak boleh ditimpa petugas lain. Penutupan sesi menampilkan
  anggota yang sudah memposting, menolak bila antrean opname di HP belum habis,
  dan meminta konfirmasi bahwa seluruh anggota sudah selesai sebelum stok
  disesuaikan oleh server.
- Upload akhir hari mengirim antrean satu per satu. `client_uuid` memastikan
  retry tidak membuat BON kedua walaupun jawaban server terputus.
- Transaksi identik pada tanggal yang sama ditahan untuk konfirmasi operator.
- Pengembalian barang lama dapat dicatat untuk material wajib tukar maupun
  material biasa. Foto diwajibkan hanya bila barang fisik benar-benar
  diserahkan; kondisi hilang/pekerja baru tidak memerlukan foto barang lama.
- Sinkron pagi mengunduh daftar barang wajib tukar yang belum kembali.
  Pengembalian yang menyusul dapat dicatat offline dari APK dan diunggah
  idempoten melalui antrean yang sama.
- Sinkron pagi juga menyimpan 300 baris transaksi keluar, masuk, dan transfer
  terbaru. Riwayat dapat dicari offline berdasarkan BON, material, penerima,
  kebun lawan, tujuan, atau dokumen SAP, serta difilter menurut jenis transaksi,
  status posting SAP, dan pembatalan.
- Foto pertama/kedua yang masih kosong dapat ditambahkan dari Riwayat. Foto
  disimpan di antrean offline, lalu saat upload diterapkan ke seluruh item dalam
  BON yang sama dengan pengaman retry idempoten. Foto yang sudah tersimpan dapat
  dibuka dari Riwayat ketika perangkat terhubung ke server.
- Admin dapat membatalkan satu transaksi dari Riwayat ketika server terhubung.
  Stok cache APK langsung mengikuti stok server dan peringatan reverse SAP
  ditampilkan bila transaksi sudah berstatus posting SAP.
- Admin dapat memperbaiki penerima, keterangan, dan nomor dokumen untuk satu
  transaksi atau seluruh item BON. Admin/Petugas juga dapat menandai seluruh
  BON sudah atau belum posting SAP beserta nomor dokumennya.
- Admin dapat mengoreksi qty satu transaksi ketika server terhubung. Saldo cache
  APK mengikuti hasil server dan warning koreksi SAP muncul untuk transaksi yang
  sudah berstatus posting.
- Admin dapat memilih beberapa item riwayat untuk disatukan menjadi satu BON
  atau dipindahkan ke BON baru. Qty dan stok tidak berubah; transaksi yang sudah
  posting SAP memerlukan konfirmasi tambahan.
- Admin dapat membatalkan catatan pengembalian barang lama dari Riwayat dengan
  alasan wajib. Transaksi kembali muncul sebagai “belum kembali”, jejak audit
  tetap tersimpan, dan saldo stok tidak berubah.
- Satu material dapat dibagi ke beberapa tujuan dengan tombol **Bagi ke tujuan
  lain**, dan aplikasi menampilkan total pembagian serta sisa stok.
- Tujuan BON dicari seperti versi web: kode blok/unit, alias EX ke EXA,
  Kantor/Staff/Workshop, dan AFD. Hanya tujuan dari master Supplementary yang
  dapat dikonfirmasi; AFD tetap meminta operator memilih blok yang benar.
- Panel **Kelola → Aplikasi Mobile** mengatur GitHub Releases dan dapat
  menonaktifkan perangkat.
- Pemeriksa pembaruan membaca link GitHub dari panel Admin. APK utama bernama
  `flowStok-x.y.z.apk`; nama lama `MonitoringStok-x.y.z.apk` tetap dikenali
  selama masa migrasi.

Cakupan APK ditetapkan pada barang keluar, barang masuk, transfer antar-kebun,
riwayat hasil sinkron, daftar stok, dan Stock Opname. PR/GRN lengkap serta
rekonsiliasi SAP tetap ditangani melalui aplikasi web agar APK gudang tetap
sederhana dan cepat dipakai.

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

APK stabil:

```powershell
cd D:\CODE\monitoring_stok\mobile
powershell -ExecutionPolicy Bypass -File scripts\build-android.ps1
```

Hasil rilis utama: `mobile/dist-apk/flowStok-1.0.2.apk`. Build juga menghasilkan
`MonitoringStok-1.0.2.apk` sebagai aset kompatibilitas bagi APK lama. Unggah
keduanya pada GitHub Release 1.0.2. Halaman **Unduh App** pada web selalu memilih
nama flowStok untuk Admin dan Petugas Gudang.

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
