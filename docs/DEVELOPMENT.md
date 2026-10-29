# Panduan Pengembangan

Panduan ini ditujukan agar pengembang baru dapat bekerja tanpa menebak pola proyek.

## Menyiapkan lingkungan

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run.py
```

Jalankan pengujian sebelum dan sesudah perubahan:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
node --check app\static\app.js
```

Seluruh source pengujian berada di `tests/test_alur.py`. Data sementara dan cache
hasil pengujian otomatis disimpan di `.test-artifacts/`, sehingga folder utama
proyek tetap rapi. Folder `.test-artifacts/` bukan data aplikasi dan boleh
dibersihkan saat tidak ada pengujian yang sedang berjalan.

## Standar fungsi dan komentar

- Gunakan nama fungsi yang menjelaskan hasil atau tindakannya.
- Fungsi publik dan fungsi dengan aturan bisnis wajib memiliki docstring.
- Docstring menjelaskan **tujuan, input penting, hasil, dan error**, bukan mengulang
  setiap baris implementasi.
- Komentar dipakai untuk alasan yang tidak terlihat dari kode, misalnya keputusan
  SAP, kompatibilitas versi, atau urutan fallback. Hindari komentar seperti
  “tambah satu” tepat di atas `nilai += 1`.
- Satu fungsi sebaiknya mengerjakan satu tanggung jawab. Ekstrak ke modul domain
  bila route perlu memahami detail gambar, Excel, perhitungan, atau format laporan.
- Gunakan istilah domain Indonesia secara konsisten pada UI dan aturan bisnis;
  gunakan nama teknis Inggris hanya untuk konsep standar seperti `metadata_time`.

## Menambah route

1. Pilih blueprint sesuai domain.
2. Tambahkan `login_required` dengan peran paling sempit yang diperlukan.
3. Validasi input di server meskipun sudah divalidasi JavaScript.
4. Gunakan parameter query, bukan menggabungkan input ke SQL.
5. Bungkus perubahan terkait dalam `with tx(db):`.
6. Catat perubahan penting dengan `audit`.
7. Tambahkan pengujian berhasil, gagal, dan batas hak akses.

## Menambah atau mengubah aturan stok

Aturan stok ditempatkan di `stock.py` atau modul domain terkait, bukan di template.
Pastikan minimal menguji:

- stok cukup dan stok tidak cukup;
- transaksi batal;
- pembatasan kebun pengguna;
- efek ke stok berjalan;
- rekonsiliasi SAP bila jenis transaksi berkaitan dengan posting.

## Template dan ikon

- Gunakan komponen `_macros.html` untuk pola form/dialog/tabel yang berulang.
- Gunakan `ikon()` dari `_ikon.html`; jangan memakai emoji sebagai ikon antarmuka.
- Ikon hanya dekoratif. Link/tombol tetap harus memiliki teks atau `aria-label`.
- Hindari `style="..."` baru. Tambahkan class bermakna atau utility kecil di `app.css`.
- Area sentuh mobile minimal 42–46 px.
- Uji HP (≤600 px), tablet (601–900 px), dan desktop (>900 px).

Contoh:

```jinja2
{% from "_ikon.html" import ikon %}
<button class="btn primary">{{ ikon('simpan', 18) }}<span>Simpan</span></button>
```

## JavaScript

- Aplikasi sengaja tidak membutuhkan build step.
- Inisialisasi fitur halaman diekspos sebagai `window.initNamaFitur` dan dipanggil
  dari block `scripts` template terkait.
- Jangan memasukkan teks pengguna melalui `innerHTML`; gunakan `textContent` atau
  node DOM. `innerHTML` hanya boleh memakai konstanta internal seperti path SVG.
- Operasi fetch memakai `postForm` agar CSRF dan format error konsisten.
- Validasi JavaScript adalah bantuan UX; server tetap menjadi pengaman utama.

## Database dan migrasi

- Jangan mengedit database produksi secara manual.
- Tambahkan migrasi kompatibel di `db.py`.
- Jangan memakai `DROP TABLE`, menghapus kolom, atau mengganti riwayat transaksi.
- Backup folder `data/` sebelum perubahan besar.

## Checklist sebelum menyerahkan perubahan

- [ ] Aturan bisnis tidak berubah tanpa persetujuan pengguna.
- [ ] Fungsi baru memiliki nama dan docstring yang jelas.
- [ ] Tidak ada duplikasi logika yang seharusnya menjadi helper.
- [ ] Tampilan dapat digunakan dengan keyboard dan layar sentuh.
- [ ] Ikon berasal dari pustaka ikon proyek.
- [ ] `node --check` lulus.
- [ ] Seluruh pytest lulus.
- [ ] README atau dokumen aturan bisnis diperbarui bila perilaku berubah.

