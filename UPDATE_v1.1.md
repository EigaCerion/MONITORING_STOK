# UPDATE v1.1 — Keputusan & Perubahan

## 1. Keputusan dari Klien
| Topik | Keputusan |
|---|---|
| Sumber kategori racun, beras, alat panen, TK panen, ATK, BBM, oli | Input manual |
| Stok awal | File export SAP 13 Okt 2026 dipakai sebagai acuan awal. Master terbaru akan diupload ulang berkala dari SAP. |
| Material Special Stock = Q | Dihitung semua. Perubahan di masa depan jika diperlukan. |
| Format import | Mengikuti format export SAP yang dikirim (kolom sama persis). |
| Transfer stok antar kebun | Wajib dicatat dengan flag tersendiri. |

## 2. Jenis Transaksi (final)
| Kode | Arti | Input | Efek stok |
|---|---|---|---|
| MASUK | Barang masuk biasa | Excel / manual | + |
| KELUAR | Barang keluar ke penerima | HP | − |
| TRANSFER_KELUAR | Kirim stok ke kebun lain | HP / Excel | − |
| TRANSFER_MASUK | Terima stok dari kebun lain | Excel / manual | + |

Aturan transfer:
- Wajib isi **Kebun Tujuan** (untuk TRANSFER_KELUAR) atau **Kebun Asal** (untuk TRANSFER_MASUK).
- Nomor dokumen transfer wajib diisi (surat jalan / nomor transfer).
- Foto bukti tetap wajib untuk KELUAR dan TRANSFER_KELUAR.
- Validasi stok sama seperti KELUAR: qty tidak boleh melebihi stok.

## 3. Perubahan pada Input HP
Field jenis transaksi menjadi pilihan 3 tombol: **KELUAR** (default) / **TRANSFER** / **MASUK**.
- Jika memilih TRANSFER, muncul field **Kebun Tujuan** (pilihan daftar kebun) dan **Nomor Dokumen**.
- Field penerima tetap diisi sebagai nama penanggung jawab penerima di kebun tujuan.

## 4. Perubahan pada Master Data
- Kolom master mengikuti export SAP (14 kolom), sesuai `TEMPLATE_MASTER_SAP.xlsx`.
- Kunci unik: Material + Special Stock + Special stock number.
- Tabel `kebun` baru: daftar kebun (kode, nama). Diisi admin.

## 5. Laporan untuk Pimpinan (sederhana)
Satu halaman, bisa diunduh sebagai Excel dan PDF:
1. **Ringkasan per kebun:** total masuk, keluar, transfer masuk, transfer keluar, saldo.
2. **Transfer antar kebun:** tabel tanggal, dari, ke, material, qty, nomor dokumen.
3. **Stok per kategori** dan material dengan stok menipis.
4. Filter: periode, kebun, kategori.

Prinsip: angka ringkasan di atas, rincian di bawah. Maksimal 1 halaman A4 untuk ringkasan.

## 6. Perubahan Skema Database
Tabel `transactions` ditambah kolom:
- `kebun_asal` (TEXT, nullable)
- `kebun_tujuan` (TEXT, nullable)
- `jenis` diperluas menjadi: `masuk`, `keluar`, `transfer_masuk`, `transfer_keluar`

Tabel baru: `kebun (kode PK, nama, aktif)`.

Tabel `materials` ditambah: `material_group`, `material_type`, `special_stock`, `special_stock_no`, `stok_awal`, `stock_in_transit`, `value_unrestricted`. Kunci unik menjadi komposit.

## 7. Menunggu Konfirmasi
- File barang masuk contoh sudah dipakai sebagai acuan format. Jika ada format khusus barang masuk yang berbeda dari template, mohon kirim.
- Daftar kebun lengkap (kode dan nama) untuk pilihan kebun asal/tujuan.
