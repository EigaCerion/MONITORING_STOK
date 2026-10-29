# Rencana Aplikasi Offline dan Sinkronisasi

Status dokumen: **fondasi seri 0.1 sudah dibuat untuk uji lapangan**. Transaksi
keluar, dua foto, antrean offline, sinkron pagi/akhir hari, Tailscale, idempotensi,
perangkat Admin, dan pembaruan GitHub sudah tersedia. Transaksi masuk, transfer,
PR/GRN, dan opname offline masih berada pada tahap berikutnya.

## Tujuan

Menjaga operasional gudang tetap berjalan ketika jaringan kebun putus. Aplikasi
dipasang pada HP sebagai APK, transaksi disimpan terlebih dahulu di perangkat,
kemudian dikirim ke server setelah jaringan tersedia tanpa membuat transaksi
ganda atau merusak saldo stok.

Server tetap menjadi sumber data resmi. Data di HP adalah salinan kerja dan
antrean transaksi yang belum berhasil diterima server.

## Jalur server tetap melalui Tailscale

- Laptop yang sekarang menjalankan aplikasi tetap menjadi server utama.
- APK memakai alamat Tailscale laptop yang sama dengan akses HP saat ini. Alamat
  server diatur sekali saat perangkat didaftarkan atau dipindai dari QR aplikasi;
  operator tidak perlu mengganti jalur antara mode online dan offline.
- Saat Tailscale tersambung dan server dapat dijangkau, APK dapat melakukan
  sinkron pagi, upload akhir hari, atau mencoba kembali antrean secara aman.
- Saat sinyal, Tailscale, atau laptop tidak tersedia, APK tidak berpindah ke
  server lain. Transaksi tetap berada di antrean lokal sampai jalur yang sama
  kembali aktif.
- Server tidak perlu dibuka ke internet umum. Akses tetap dibatasi pada perangkat
  yang sudah masuk jaringan Tailscale dan memiliki akun aplikasi yang sah.
- Layar status menampilkan tiga pemeriksaan terpisah: koneksi internet/jaringan,
  Tailscale aktif, dan server laptop dapat dijangkau. Dengan begitu petugas tahu
  penyebab upload tertunda tanpa mengubah alamat aplikasi.
- Sebelum jam kerja dan sebelum upload akhir hari, laptop server serta Tailscale
  harus menyala. Bila laptop mati, antrean di HP tetap aman dan sinkron dapat
  dilanjutkan setelah laptop hidup kembali.

## Alur kerja harian

### Sebelum mulai bekerja

1. Pegawai masuk menggunakan akun dan perangkat yang terdaftar.
2. Pegawai wajib menekan **Sinkron pagi** saat masih memiliki jaringan.
3. Aplikasi mengunduh master material, saldo awal, material terkunci, tujuan BON,
   Supplementary blok/unit, sesi opname, dan transaksi terbaru yang diperlukan.
4. Aplikasi menampilkan waktu sinkron terakhir dan status **Siap dipakai offline**.
5. Bila data belum pernah disinkronkan atau sudah melewati batas waktu yang
   ditentukan, input baru diblokir sampai sinkron pagi berhasil.

### Selama bekerja

- Transaksi, detail barang, tujuan, penerima, foto, waktu, dan pengguna disimpan
  di database lokal HP.
- Setiap transaksi memperoleh `client_uuid` unik sejak pertama dibuat. Nilai ini
  tidak berubah walaupun pengguna menekan unggah berulang kali.
- Foto tetap diberi watermark, dikompres, dan disimpan lokal sampai server
  mengonfirmasi bahwa file sudah diterima dengan lengkap.
- Layar selalu membedakan status **Belum diunggah**, **Sedang diunggah**,
  **Berhasil**, dan **Perlu diperiksa**.
- Transaksi yang sudah masuk antrean tidak boleh dihapus diam-diam. Pembatalan
  disimpan sebagai kejadian baru agar riwayat tetap dapat diaudit.

### Saat pulang kerja

1. Pegawai wajib membuka **Upload akhir hari** ketika jaringan tersedia.
2. Aplikasi mengunggah transaksi terlebih dahulu, lalu foto secara bertahap.
3. Server memeriksa `client_uuid`; pengiriman ulang mengembalikan hasil transaksi
   lama dan tidak membuat transaksi kedua.
4. Pegawai baru boleh menyelesaikan tugas setelah antrean menunjukkan nol atau
   seluruh kegagalan sudah diberi alasan dan diserahkan kepada penanggung jawab.
5. Aplikasi menyimpan bukti ringkas: jumlah berhasil, jumlah gagal, waktu
   sinkron, pengguna, dan perangkat.

## Aturan sinkronisasi penting

- `client_uuid` menjadi kunci idempotensi wajib pada server.
- Nomor BON sementara dibuat di HP, sedangkan nomor resmi dikembalikan server
  saat sinkron berhasil. Hubungan keduanya disimpan permanen.
- Server mengulangi seluruh validasi: material aktif/tidak terkunci, stok cukup,
  tujuan valid, duplikat pada tanggal transaksi, dan hak akses pengguna.
- Urutan kejadian mengikuti waktu transaksi di perangkat, bukan waktu unggah.
  Waktu perangkat, waktu diterima server, dan zona waktu disimpan terpisah.
- Saldo lokal hanya perkiraan kerja. Saldo resmi selalu saldo hasil server.
- Data master memakai versi. Bila versi master berubah selama HP offline, server
  dapat menolak transaksi terkait dan meminta verifikasi, bukan mengubah tujuan
  atau material secara otomatis.
- Upload dilakukan per paket kecil dan dapat dilanjutkan setelah putus koneksi.
  Transaksi tidak ditandai selesai sebelum detail dan semua foto dikonfirmasi.

## Konflik yang harus masuk pemeriksaan

- Material dikunci setelah sinkron pagi tetapi sudah dipilih saat offline.
- Stok server tidak lagi cukup karena transaksi dari perangkat lain lebih dahulu
  diterima.
- Transaksi sangat mirip sudah ada pada tanggal yang sama.
- Tujuan blok/unit sudah berubah atau tidak lagi ada di master Supplementary.
- Satu sesi opname dihitung beberapa HP pada material yang sama.
- Jam HP berubah tidak wajar atau tanggal transaksi berada di luar hari kerja.

Konflik tidak boleh langsung dibuang atau dipaksakan. Data masuk ke kotak
**Perlu verifikasi**, menampilkan transaksi lokal dan data server berdampingan,
kemudian admin memilih simpan, koreksi, gabungkan, atau batalkan dengan audit log.

## Pengamanan perangkat

- Perangkat harus didaftarkan dan dapat dinonaktifkan dari server.
- Database lokal dan token akses disimpan terenkripsi.
- Data hanya boleh dibuka oleh akun dan perangkat yang sesuai.
- Token offline memiliki masa berlaku terbatas; perubahan hak akses diperiksa
  kembali saat sinkron berikutnya.
- Foto baru dihapus dari HP setelah server mengonfirmasi checksum file.
- Log sinkron menyimpan pengguna, perangkat, versi aplikasi, waktu, hasil, dan
  alasan kegagalan tanpa menyimpan kata sandi.

## Tahapan realisasi

1. Tambahkan identitas transaksi global, API sinkron idempoten, versi master,
   status antrean, dan layar pemeriksaan konflik pada server yang sekarang.
2. Buat aplikasi Android dari antarmuka yang sudah dikenal operator, memakai
   database lokal, kamera langsung, dan tujuan sinkron Tailscale yang sama.
   Mulai dari transaksi keluar dan foto.
3. Tambahkan transaksi masuk, retur, PR/GRN, lalu opname offline. Opname dibuat
   tahap terakhir karena membutuhkan penguncian dan konflik antaranggota.
4. Uji terbatas pada satu gudang dan beberapa HP dengan simulasi sinyal putus,
   unggah berulang, jam HP salah, stok habis, dan foto gagal.
5. Setelah hasil audit sesuai, terapkan aturan wajib sinkron pagi dan upload akhir
   hari pada seluruh petugas gudang.

## Kriteria siap produksi

- Mengirim paket yang sama berkali-kali hanya menghasilkan satu transaksi.
- Aplikasi pulih setelah ditutup paksa atau HP mati tanpa kehilangan antrean.
- Foto tidak hilang dan tidak terduplikasi setelah koneksi terputus.
- Konflik stok dan master tidak mengubah saldo sebelum diverifikasi.
- Admin dapat melihat perangkat yang belum upload pada akhir hari.
- Laporan audit dapat menghubungkan transaksi HP, nomor BON resmi, foto, dan
  hasil sinkron server.

