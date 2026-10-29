# Aturan Bisnis

Dokumen ini merangkum keputusan yang harus dipertahankan saat kode diubah.

## Identitas material

Material unik berdasarkan gabungan `plant`, kode material, special stock, dan
special stock number. Baris SAP berkunci sama dari storage location atau batch
berbeda dijumlahkan, bukan saling menimpa.

## Transaksi

- Satu kali simpan dapat berisi banyak barang dan menghasilkan satu nomor BON.
- Semua item satu BON menggunakan penerima, tanggal, keterangan, dan foto yang sama.
- Transaksi keluar dan transfer kirim dari HP wajib memiliki penerima dan Foto 1.
- Foto 2 untuk bon/dokumen bersifat opsional.
- Material expired tidak boleh dimasukkan ke transaksi baru.
- Material yang diberi **kunci input** tetap terlihat pada master, stok, kartu stok,
  opname, dan rekonsiliasi untuk menjaga histori, tetapi tidak dapat dipakai pada
  input HP, impor transaksi, atau penambahan item PR baru.
- Khusus FAME CM3A, kode `929.001.007` dikunci sementara agar operator
  menghabiskan stok `929.001.Z18` terlebih dahulu. Setelah stok kode Z18 habis,
  admin membuka kembali `929.001.007` dari Master SAP. Kedua kode tetap ikut
  stok, opname, histori, dan rekonsiliasi selama masa penguncian.
- Stok tidak boleh menjadi minus karena input atau koreksi transaksi.
- Koreksi dan pembatalan tidak menghapus riwayat; keduanya tercatat di audit log.

## Penjaga duplikat

Peringatan hanya membandingkan transaksi pada **tanggal yang dipilih pengguna**.
Tanggal berbeda tidak dianggap duplikat. Kandidat dianggap sama bila jenis,
penerima, keterangan, tujuan, seluruh material+qty, serta data pengembalian cocok.
Operator tetap dapat melanjutkan setelah melihat transaksi sebelumnya.

## Tujuan BON dan pembebanan SAP otomatis

- Operator cukup memilih **Tujuan BON**. Kode pembebanan SAP disimpan otomatis
  berdasarkan tujuan tersebut dan tidak menjadi isian terpisah.
- Tujuan BON menggunakan satu pencarian pintar: kata `kantor` dikenali sebagai
  GOH-OFFICE, kode blok/unit dikenali dari Supplementary, dan alias excavator
  seperti `EX17` diarahkan ke kode baku `EXA17`.
- Setelah master Supplementary tersedia untuk plant, blok atau unit di luar master
  ditolak. Hasil pengenalan selalu ditampilkan kepada operator untuk dikonfirmasi.
- `AFD 3`, `Afdeling 3`, dan `Divisi 3` mencari kolom Division pada Supplementary
  lalu menampilkan seluruh blok dalam divisi tersebut. AFD tidak otomatis menjadi
  satu blok SAP; operator tetap wajib memilih blok yang benar. Kata `AFD` tanpa
  nomor ditolak agar sistem tidak menebak tujuan.
- Pemakaian lapangan wajib memiliki blok sebagai Tujuan BON.
- Master Supplementary memetakan nama blok yang dikenal operator (contoh `D24`)
  ke kode blok SAP (contoh `B-034`) dan menyediakan pencarian unit/VRA.
- Satu BON boleh berisi beberapa tujuan. Material yang sama boleh dibagi ke blok
  atau unit berbeda, tetapi tidak boleh diduplikasi pada tujuan yang sama.
- Stok divalidasi terhadap jumlah seluruh pembagian material dalam satu BON.
- FAME, BBM, dan sparepart hanya boleh diarahkan ke unit atau workshop.
- Workshop memakai cost center `CM3AZGO996` (GOH-WORKSHOP).
- Kantor memakai cost center `CM3AZGO992` (GOH-OFFICE LABOUR).
- Staff memakai cost center `CM3AZGO991` (GOH-STAFF).
- Damkar wajib memiliki blok serta aktivitas `330` (patroli kebakaran) atau `331`
  (pengendalian kebakaran).

## Foto

- Browser membaca `DateTimeOriginal` sebelum kompresi menghapus EXIF.
- Server memprioritaskan EXIF asli bila masih tersedia.
- Jika metadata waktu tidak ada, watermark secara eksplisit menulis waktu unggah.
- Foto disimpan sebagai JPEG, sisi terpanjang maksimal 1600 px, kualitas 72.
- Metadata kamera tidak dipertahankan dalam file hasil untuk menghemat ruang dan
  mengurangi informasi pribadi; waktu yang relevan sudah tercetak di watermark.

## Aplikasi Android offline

- Sinkron pagi wajib berhasil pada tanggal berjalan sebelum APK membuka form
  transaksi baru. Saldo lokal hanya salinan kerja; validasi stok selalu diulang
  server saat upload.
- Seri 0.1 hanya membuat transaksi keluar. Masuk, transfer, PR/GRN, dan
  opname tetap memakai aplikasi web sampai modul offline berikutnya selesai.
- Tombol simpan memasukkan BON dan foto ke antrean HP terlebih dahulu. Upload
  akhir hari mengirim antrean melalui alamat Tailscale laptop yang sama.
- Setiap BON mempunyai `client_uuid`. Pengiriman berulang dengan UUID dan isi
  yang sama mengembalikan BON lama; UUID yang dipakai untuk isi berbeda ditolak.
- Pemeriksaan duplikat, expired, kunci material, master tujuan, stok, dan sesi
  opname diulang oleh server. Konflik tidak mengubah stok sebelum diselesaikan.
- Hanya akun pembuat antrean yang boleh mengunggahnya. Ganti akun tidak menghapus
  antrean lokal.
- Admin dapat menonaktifkan perangkat. Perangkat nonaktif tidak dapat memakai
  token lama atau login ulang sampai diaktifkan kembali dari panel Admin.
- Repositori GitHub pembaruan diatur terpusat dari panel Admin. APK hanya menerima
  aset bernama `MonitoringStok-x.y.z.apk` dari GitHub Releases tersebut.

## SAP dan rekonsiliasi

- MB52 adalah snapshot saldo; MB51 adalah riwayat mutasi.
- Saldo gudang aplikasi adalah gabungan storage location **ST01 + STTF**, untuk kebun inti maupun plasma.
- Storage location lain, termasuk TP01 dan TP07, belum masuk stok gudang dan tidak dihitung ke saldo aplikasi.
- Untuk sementara stok ST01 dan STTF digabung pada satu material; pemisahan label barang sekali pakai/habis
  belum diterapkan sesuai keputusan operasional 28 Oktober 2026.
- CM9A adalah plant plasma dengan alur langsung masuk lalu keluar sehingga tidak memiliki saldo mengendap.
  Ketiadaan material CM9A pada MB52 bukan data hilang. Baris pengeluaran CM9A boleh ditautkan ke transaksi
  aplikasi CM3A yang cocok tanpa memindahkan materialnya. Transaksi CM9A baru tidak dibuat otomatis agar
  penerimaan/transfer dan pemakaian tidak dihitung dua kali.
- Rantai plasma lengkap **641 CM3A keluar → 101 CM9A masuk → 221 CM9A keluar ke blok** digabung menjadi
  satu transaksi keluar dari stok CM3A. Sistem hanya boleh menggabungkannya bila material, qty,
  purchase order 641/101, tanggal fisik 641/221, dan blok pada catatan SAP cocok dengan Master Supplementary.
  Tanggal posting 101 boleh lebih lambat daripada pengeluaran fisik dan harus ditampilkan kepada operator.
  Baris 101 dan 221
  disimpan sebagai dokumen SAP pendukung dan tidak mengubah stok lagi. Operator tetap mencentang rantai
  tersebut sebelum transaksi yang hilang dibuat.
- Bila pasangan **641 CM3A → 101 CM9A** sudah pasti dari PO, material, dan qty tetapi MvT 221 belum ada,
  keputusan arah tetap dilakukan pada baris 641. Baris 101 menjadi dokumen pendukung transaksi yang sama,
  tidak meminta status transit terpisah, dan tidak mengubah stok untuk kedua kali.
- Pasangan movement asli dan reversal pada tanggal/material/plant/lokasi/qty yang sama—misalnya
  **221 -6.400** dan **222 +6.400**—ditandai sebagai pembatalan SAP dengan dampak bersih nol. Keduanya
  tidak ditawarkan sebagai transaksi baru selama belum pernah ditautkan ke transaksi aktif.
- Bila satu transaksi aplikasi dijabarkan SAP ke beberapa blok plasma, transaksi aplikasi dibagi per blok
  dengan BON, penerima, foto, dan keterangan yang sama. Total qty tidak berubah; pembagian ini tidak boleh
  dicentang otomatis dan baru diterapkan setelah konfirmasi operator.
- Transfer masuk kebun dan antar kebun membutuhkan verifikasi arah secara manual.
- Baris MB51 tanpa pasangan hanya menjadi calon transaksi; tidak otomatis disimpan.
- Rekonsiliasi operasional setelah peluncuran memakai batas mulai **15 Oktober 2026**.
  MB51 harus mencakup CM3A dan CM9A; MB52 menjadi kontrol saldo CM3A dengan
  ST01+STTF, sedangkan CM9A tetap mengikuti aturan flow-through di atas.
- Transaksi tanggal 14 Oktober 2026 dapat diabaikan karena terjadi sebelum aplikasi
  diluncurkan, sesuai keputusan operasional saat rekonsiliasi awal.
- Transaksi aplikasi yang belum diposting SAP harus tetap dipisahkan dari selisih nyata.
- Gangguan jaringan akibat tower tersambar petir membuat aplikasi tidak dapat dipakai pada
  **26–28 Oktober 2026**. Berdasarkan konfirmasi operasional, baris FAME `929.001.Z18`
  MvT 261 tanggal 26–27 Oktober pada MB51 boleh dicatat sebagai transaksi `sap_mb51`
  yang terlewat, dengan catatan gangguan pada setiap transaksi. Tarikan MB51 yang dipakai
  belum memuat pengeluaran FAME tanggal 28 Oktober; tanggal tersebut baru boleh diproses
  dari tarikan baru setelah seluruh posting sore masuk.

## Stock opname

- Satu sesi bersama hanya untuk satu gudang.
- Material yang sedang dihitung satu petugas dikunci agar tidak ditimpa petugas lain.
- Transaksi gudang dikunci selama sesi berlangsung agar saldo dasar tidak berubah.
- Penyesuaian stok baru dibuat saat sesi ditutup oleh admin atau petugas penerima delegasi.
- Admin dapat mendelegasikan hak membuka sesi kepada Petugas Gudang maksimal tujuh hari.
- Delegasi hanya berlaku untuk gudang yang terpasang pada akun petugas. Nilai plant dari HP
  diabaikan dan server selalu memakai gudang akun tersebut.
- Petugas penerima delegasi boleh menutup dan mem-posting selisih hanya untuk sesi yang dibuatnya sendiri,
  selama delegasi masih aktif. Pembatalan sesi tetap khusus admin.
- Sebelum sesi ditutup, aplikasi menampilkan anggota, jumlah barang, dan waktu simpan terakhir. Penutup wajib
  mengonfirmasi semua anggota sudah menekan **Simpan hitungan saya** dan tidak menyisakan draft di HP.
- Hak berhenti otomatis setelah kedaluwarsa dan dapat dicabut admin lebih awal. Pemberian,
  pemakaian, dan pencabutan delegasi masuk ke audit log.

## Pengembalian barang lama

- Pencatatan pengembalian tidak menambah atau mengurangi stok material baru.
- Foto wajib bila barang lama benar-benar diserahkan.
- Foto tidak wajib untuk kondisi “Hilang” atau “Tidak ada — pekerja baru”.

## Penerimaan Purchase Request

- Satu nomor GRN pada satu PR dapat memuat beberapa barang.
- Satu item PR dapat diterima bertahap melalui beberapa GRN.
- Qty penerimaan tidak boleh melebihi sisa Qty PR, dan nomor GRN yang sama tidak
  boleh dicatat dua kali dalam PR yang sama.
- Qty PR tidak boleh diturunkan hingga lebih kecil daripada qty yang sudah diterima.
- Pencatatan GRN adalah monitoring kedatangan dan tidak otomatis menambah stok.
  Transaksi MASUK tetap dicatat melalui Input Transaksi agar stok tidak terhitung dua kali.
- GRN yang salah dapat dihapus dengan jejak audit; item yang sudah memiliki GRN
  tidak dapat dihapus dari PR sebelum riwayat penerimaannya dikoreksi.

