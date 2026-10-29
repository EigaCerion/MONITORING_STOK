/* Monitoring Stok — JS ringan tanpa build step */
(function () {
  "use strict";
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
  const CSRF = $('meta[name="csrf"]')?.content || "";
  const TANPA_BARANG = /^(Hilang|Tidak ada)/;  // kondisi tanpa barang lama -> foto tidak wajib
  const TUJUAN_TEKS = { blok: "Blok", unit: "Unit", workshop: "Workshop", office: "Kantor",
    staff: "Staff", damkar: "Damkar" };
  const ICON_PATHS = {
    kamera: '<path d="M4 8h3l2-3h6l2 3h3v11H4z"/><circle cx="12" cy="13" r="3.5"/>',
    galeri: '<rect x="3" y="5" width="18" height="14" rx="2"/><circle cx="9" cy="10" r="2"/><path d="M21 15l-5-5L5 19"/>',
    centang: '<path d="M5 12l4 4L19 6"/>',
    filter: '<path d="M4 5h16l-6 8v6l-4-2v-4z"/>',
  };

  /** Buat ikon SVG dekoratif yang konsisten dengan ikon Jinja di server. */
  function iconNode(name, size = 20) {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("class", "ikon");
    svg.setAttribute("width", String(size));
    svg.setAttribute("height", String(size));
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("fill", "none");
    svg.setAttribute("stroke", "currentColor");
    svg.setAttribute("stroke-width", "2");
    svg.setAttribute("stroke-linecap", "round");
    svg.setAttribute("stroke-linejoin", "round");
    svg.setAttribute("aria-hidden", "true");
    svg.innerHTML = ICON_PATHS[name] || "";
    return svg;
  }

  /** Ganti isi kontrol dengan ikon dan label teks tanpa menyisipkan HTML pengguna. */
  function setIconLabel(element, icon, label) {
    element.replaceChildren(iconNode(icon), document.createTextNode(label));
  }

  /* ---------- angka: terima "1.234,5" / "1234.5" / "12,5" ---------- */
  function parseNum(v) {
    let s = String(v ?? "").trim().replace(/\s/g, "");
    if (!s) return null;
    if (s.includes(",") && s.includes(".")) {
      s = s.lastIndexOf(",") > s.lastIndexOf(".") ? s.replace(/\./g, "").replace(",", ".") : s.replace(/,/g, "");
    } else if (s.includes(",")) s = s.replace(",", ".");
    const x = Number(s);
    return Number.isFinite(x) ? x : null;
  }
  const fmtNum = (x) => (x == null ? "–" : Number(x).toLocaleString("id-ID", { maximumFractionDigits: 3 }));
  window.parseNum = parseNum;

  function toast(msg, cls = "ok") {
    const t = $("#toast");
    t.innerHTML = "";
    const d = document.createElement("div");
    d.className = "flash " + cls;
    d.textContent = msg;
    t.appendChild(d);
    clearTimeout(toast._t);
    toast._t = setTimeout(() => (t.innerHTML = ""), 4500);
  }

  async function postForm(url, fd) {
    fd.set("_csrf", CSRF);
    const r = await fetch(url, { method: "POST", body: fd, headers: { "X-Requested-With": "fetch" } });
    let data = {};
    try { data = await r.json(); } catch (e) { data = { ok: false, pesan: "Gagal (" + r.status + ")" }; }
    return data;
  }

  /* ---------- umum ---------- */
  document.addEventListener("click", (e) => {
    if (e.target.closest("[data-menu]")) {
      const tb = $("#topbar");
      tb.classList.toggle("open");
      if (tb.classList.contains("open")) window.scrollTo({ top: 0, behavior: "smooth" });
    }
    $$("details.navmore[open]").forEach((d) => { if (!d.contains(e.target)) d.open = false; });
    const close = e.target.closest("[data-close]");
    if (close) close.closest("dialog")?.close();
    const btnConfirm = e.target.closest("button[data-confirm]");
    if (btnConfirm && !confirm(btnConfirm.dataset.confirm)) e.preventDefault();
  });
  document.addEventListener("submit", (e) => {
    const f = e.target;
    if (f.dataset.confirm && !confirm(f.dataset.confirm)) { e.preventDefault(); return; }
    // cegah dobel submit
    const btn = e.submitter;
    if (btn && !f.dataset.noLock) setTimeout(() => (btn.disabled = true), 0);
  });
  document.addEventListener("change", (e) => {
    const all = e.target.closest("[data-check-all]");
    if (all) {
      // dukung checkbox yang terhubung lewat atribut form="..."
      const f = all.form || all.closest("form");
      const boxes = f ? Array.from(f.elements) : $$("input");
      boxes.filter((c) => c.name === all.dataset.checkAll && c.type === "checkbox").forEach((c) => (c.checked = all.checked));
    }
  });

  /* ---------- filter ringkas di HP: tombol "Filter (n)" ---------- */
  $$("form[data-filter]").forEach((f) => {
    const lain = $$("input, select", f).filter((el) => el.name && el.name !== "q" && el.type !== "hidden");
    const aktif = lain.filter((el) => el.value).length;
    const b = document.createElement("button");
    b.type = "button";
    b.className = "btn toggle-filter";
    setIconLabel(b, "filter", aktif ? `Filter (${aktif})` : "Filter");
    b.addEventListener("click", () => f.classList.toggle("buka"));
    f.querySelector(".grow")?.after(b);
  });

  /* ---------- bar aksi muncul hanya saat ada yang dicentang ---------- */
  $$("[data-pilih-bar]").forEach((bar) => {
    const formId = bar.dataset.pilihBar;
    const hitung = () => {
      const n = $$(`input[name="ids"][form="${formId}"]:checked`).length;
      bar.hidden = n === 0;
      const el = bar.querySelector("[data-pilih-n]");
      if (el) el.textContent = n;
    };
    document.addEventListener("change", (e) => { if (e.target.name === "ids" || e.target.dataset.checkAll) hitung(); });
    hitung();
  });

  /** True bila salah satu sumber kamera/galeri untuk bidang foto sudah terisi. */
  function fotoTerpilih(inputUtama) {
    if (!inputUtama) return false;
    const bidang = inputUtama.dataset.photoField || inputUtama.name;
    return $$('input[type=file][data-photo-field]', inputUtama.parentElement)
      .some((input) => input.dataset.photoField === bidang && input.files.length > 0);
  }

  /* ---------- tombol kamera langsung dan galeri untuk semua input foto ---------- */
  function pasangTombolFoto(root) {
    $$('input[type=file][data-compress]', root).forEach((inp) => {
      if (inp.dataset.tombolOk) return;
      inp.dataset.tombolOk = "1";
      if (!inp.id) inp.id = "foto_" + Math.random().toString(36).slice(2, 8);
      inp.classList.add("foto-input");
      const bidang = inp.name;
      inp.dataset.photoField = bidang;

      // Input kamera dipisahkan dari input galeri agar Android/iOS menampilkan
      // pilihan yang pasti, sementara nama field server tetap tidak ambigu.
      const kamera = document.createElement("input");
      kamera.type = "file";
      kamera.id = `${inp.id}_kamera`;
      kamera.name = `${bidang}_camera`;
      kamera.accept = inp.accept || "image/*";
      kamera.setAttribute("capture", "environment");
      kamera.className = "foto-input";
      kamera.dataset.compress = "";
      kamera.dataset.photoField = bidang;
      kamera.setAttribute("aria-label", inp.dataset.kamera || "Ambil foto langsung");

      const aksi = document.createElement("div");
      aksi.className = "foto-actions";
      const lblKamera = document.createElement("label");
      lblKamera.className = "foto-btn foto-btn-camera";
      lblKamera.htmlFor = kamera.id;
      const teksKamera = inp.dataset.kamera || "Ambil foto langsung";
      setIconLabel(lblKamera, "kamera", teksKamera);
      const lblGaleri = document.createElement("label");
      lblGaleri.className = "foto-btn";
      lblGaleri.htmlFor = inp.id;
      const teksGaleri = inp.dataset.tombol || "Pilih dari galeri";
      setIconLabel(lblGaleri, "galeri", teksGaleri);
      aksi.append(lblKamera, lblGaleri);
      inp.after(kamera, aksi);

      // Validasi wajib dilakukan per pasangan, bukan hanya input galeri.
      if (inp.required) {
        inp.required = false;
        inp.dataset.photoRequired = "1";
      }
      const perbarui = (aktif) => {
        const dariKamera = aktif === kamera && kamera.files.length > 0;
        const dariGaleri = aktif === inp && inp.files.length > 0;
        lblKamera.classList.toggle("ada", dariKamera);
        lblGaleri.classList.toggle("ada", dariGaleri);
        setIconLabel(lblKamera, dariKamera ? "centang" : "kamera",
          dariKamera ? "Foto kamera siap — ketuk untuk ganti" : teksKamera);
        setIconLabel(lblGaleri, dariGaleri ? "centang" : "galeri",
          dariGaleri ? "Foto galeri siap — ketuk untuk ganti" : teksGaleri);
      };
      inp.addEventListener("change", () => {
        if (inp.files.length) kamera.value = "";
        perbarui(inp);
      });
      kamera.addEventListener("change", () => {
        if (kamera.files.length) inp.value = "";
        perbarui(kamera);
      });
      inp.closest("form")?.addEventListener("reset", () => {
        kamera.value = "";
        perbarui(null);
        const prev = inp.parentElement.querySelector(".foto-prev");
        if (prev) prev.hidden = true;
        const waktu = inp.parentElement.querySelector(".foto-waktu-metadata");
        const info = inp.parentElement.querySelector(".foto-waktu-info");
        if (waktu) waktu.value = "";
        if (info) info.remove();
      });
    });
  }
  pasangTombolFoto(document);

  /* Baca DateTimeOriginal dari JPEG sebelum kompresi membuang metadata kamera. */
  function waktuExifJpeg(buffer) {
    const v = new DataView(buffer), batas = v.byteLength;
    if (batas < 4 || v.getUint16(0, false) !== 0xffd8) return "";
    const ascii = (pos, n) => {
      if (pos < 0 || pos + n > batas) return "";
      let hasil = "";
      for (let i = 0; i < n; i++) hasil += String.fromCharCode(v.getUint8(pos + i));
      return hasil.split("\0", 1)[0].trim();
    };
    let pos = 2;
    while (pos + 4 <= batas) {
      if (v.getUint8(pos) !== 0xff) { pos++; continue; }
      const marker = v.getUint8(pos + 1);
      pos += 2;
      if (marker === 0xda || marker === 0xd9) break;
      if (pos + 2 > batas) break;
      const panjang = v.getUint16(pos, false);
      if (panjang < 2 || pos + panjang > batas) break;
      const awal = pos + 2, akhir = pos + panjang;
      if (marker === 0xe1 && ascii(awal, 6) === "Exif") {
        const tiff = awal + 6;
        if (tiff + 8 > akhir) return "";
        const tanda = v.getUint16(tiff, false);
        const little = tanda === 0x4949;
        if (!little && tanda !== 0x4d4d) return "";
        const u16 = (p) => (p + 2 <= akhir ? v.getUint16(p, little) : 0);
        const u32 = (p) => (p + 4 <= akhir ? v.getUint32(p, little) : 0);
        if (u16(tiff + 2) !== 42) return "";
        const bacaIfd = (alamat) => {
          if (alamat < tiff || alamat + 2 > akhir) return { tanggal: "", exif: 0 };
          const n = u16(alamat), hasil = { tanggal: "", exif: 0 };
          for (let i = 0; i < n; i++) {
            const e = alamat + 2 + i * 12;
            if (e + 12 > akhir) break;
            const tag = u16(e), tipe = u16(e + 2), jumlah = u32(e + 4);
            if (tag === 0x8769) hasil.exif = tiff + u32(e + 8);
            if ((tag === 0x9003 || tag === 0x9004 || tag === 0x0132) && tipe === 2 && jumlah >= 19) {
              const data = jumlah <= 4 ? e + 8 : tiff + u32(e + 8);
              const nilai = ascii(data, Math.min(jumlah, 32));
              if (/^\d{4}:\d{2}:\d{2} \d{2}:\d{2}:\d{2}$/.test(nilai)) hasil.tanggal = nilai;
            }
          }
          return hasil;
        };
        const utama = bacaIfd(tiff + u32(tiff + 4));
        const rinci = utama.exif ? bacaIfd(utama.exif) : { tanggal: "" };
        const nilai = rinci.tanggal || utama.tanggal;
        return nilai ? nilai.replace(/^(\d{4}):(\d{2}):(\d{2}) /, "$1-$2-$3 ") : "";
      }
      pos += panjang;
    }
    return "";
  }

  async function waktuMetadataFoto(file) {
    if (!file || !(/jpe?g/i.test(file.type) || /\.jpe?g$/i.test(file.name))) return "";
    try {
      return waktuExifJpeg(await file.slice(0, 512 * 1024).arrayBuffer());
    } catch (err) {
      return "";
    }
  }

  function tampilkanWaktuFoto(inp, nilai) {
    const bidang = inp.dataset.photoField || inp.name;
    let hidden = inp.parentElement.querySelector(".foto-waktu-metadata");
    if (!hidden) {
      hidden = document.createElement("input");
      hidden.type = "hidden";
      hidden.className = "foto-waktu-metadata";
      hidden.name = `${bidang}_waktu_metadata`;
      inp.after(hidden);
    }
    hidden.value = nilai;
    let info = inp.parentElement.querySelector(".foto-waktu-info");
    if (!info) {
      info = document.createElement("div");
      info.className = "hint foto-waktu-info";
      inp.parentElement.append(info);
    }
    info.textContent = nilai
      ? `Watermark: ${nilai.slice(8, 10)}-${nilai.slice(5, 7)}-${nilai.slice(0, 4)} ${nilai.slice(11)} (metadata foto)`
      : "Metadata tanggal tidak ditemukan; watermark akan memakai waktu saat foto diunggah.";
  }

  /* ---------- kompres foto sebelum upload (HP sering 4-8 MB) ---------- */
  async function compress(file, maxSide = 1600, quality = 0.72) {
    const heic = /heic|heif/i.test(file.type) || /\.hei[cf]$/i.test(file.name);
    if (!file || !file.type.startsWith("image/") || (file.size < 400 * 1024 && !heic)) return file;
    try {
      const bmp = await createImageBitmap(file);
      const scale = Math.min(1, maxSide / Math.max(bmp.width, bmp.height));
      const c = document.createElement("canvas");
      c.width = Math.round(bmp.width * scale);
      c.height = Math.round(bmp.height * scale);
      c.getContext("2d").drawImage(bmp, 0, 0, c.width, c.height);
      const blob = await new Promise((res) => c.toBlob(res, "image/jpeg", quality));
      if (!blob || blob.size >= file.size) return file;
      return new File([blob], file.name.replace(/\.\w+$/, "") + ".jpg", { type: "image/jpeg" });
    } catch (err) {
      return file;
    }
  }
  document.addEventListener("change", async (e) => {
    const inp = e.target;
    if (!inp.matches("input[type=file][data-compress]") || !inp.files[0]) return;
    const asli = inp.files[0];
    inp.dataset.processing = "1";
    try {
      tampilkanWaktuFoto(inp, await waktuMetadataFoto(asli));
      const out = await compress(asli);
      if (out !== asli && window.DataTransfer) {
        const dt = new DataTransfer();
        dt.items.add(out);
        inp.files = dt.files;
      }
      const prev = inp.parentElement.querySelector(".foto-prev");
      if (prev) { prev.src = URL.createObjectURL(inp.files[0]); prev.hidden = false; }
    } finally {
      delete inp.dataset.processing;
    }
  });

  document.addEventListener("submit", (e) => {
    if (e.target.querySelector('input[type=file][data-processing="1"]')) {
      e.preventDefault();
      e.stopImmediatePropagation();
      toast("Foto masih diproses. Tunggu sebentar lalu simpan kembali.", "warn");
      return;
    }
    const fotoWajibKosong = $$('input[type=file][data-photo-required="1"]', e.target)
      .find((input) => !fotoTerpilih(input));
    if (fotoWajibKosong) {
      e.preventDefault();
      e.stopImmediatePropagation();
      toast("Ambil foto atau pilih foto dari galeri terlebih dahulu.", "error");
      fotoWajibKosong.parentElement.scrollIntoView({ behavior: "smooth", block: "center" });
    }
  }, true);

  /* ---------- dialog ubah qty (penyesuaian stok) ---------- */
  const dlg = $("#dlgAdjust");
  if (dlg) {
    const form = $("#formAdjust"), qty = $("#adjQty");
    let cur = null, srcBtn = null;
    const upd = () => {
      const v = parseNum(qty.value);
      const d = v == null ? null : Math.round((v - cur.stok) * 1000) / 1000;
      $("#adjDiff").textContent = d == null ? "–" : (d > 0 ? "+" : "") + fmtNum(d);
      $("#adjDiff").className = d > 0 ? "plus" : d < 0 ? "minus" : "";
    };
    document.addEventListener("click", (e) => {
      const b = e.target.closest("[data-adjust]");
      if (!b) return;
      srcBtn = b;
      cur = JSON.parse(b.dataset.adjust);
      $("#adjName").textContent = `${cur.material} · ${cur.deskripsi}`;
      $("#adjNow").textContent = fmtNum(cur.stok);
      $("#adjSap").textContent = fmtNum(cur.sap);
      $("#adjUnit").textContent = cur.satuan ? `(${cur.satuan})` : "";
      $("#adjUseSap").hidden = cur.sap == null;
      cur.target = cur.sap == null ? null : Math.round((cur.sap + (cur.pending || 0)) * 1000) / 1000;
      $("#adjSapHint").textContent = cur.sap == null ? "" : cur.pending
        ? `Stok SAP ${fmtNum(cur.sap)} + belum posting ${cur.pending > 0 ? "+" : ""}${fmtNum(cur.pending)} = ${fmtNum(cur.target)}`
        : "";
      qty.value = "";
      $("#adjKet").value = "";
      form.action = cur.url;
      upd();
      dlg.showModal();
      qty.focus();
    });
    qty.addEventListener("input", upd);
    $("#adjUseSap").addEventListener("click", () => {
      qty.value = String(cur.target);
      $("#adjAlasan").value = "Samakan dengan SAP";
      upd();
    });
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      e.stopImmediatePropagation();
      const v = parseNum(qty.value);
      if (v == null || v < 0) { toast("Qty harus angka ≥ 0", "error"); return; }
      const fd = new FormData(form);
      fd.set("qty_baru", String(v));
      const btn = form.querySelector("button[type=submit]");
      btn.disabled = true;
      const r = await postForm(cur.url, fd);
      btn.disabled = false;
      if (!r.ok) { toast(r.pesan, "error"); return; }
      dlg.close();
      if (location.pathname === "/stok") {
        const low = r.stok <= 0 || (cur.min > 0 && r.stok <= cur.min);
        $$(`[data-stok-for="${cur.id}"]`).forEach((el) => { el.textContent = r.stok_txt; el.classList.toggle("low", low); });
        cur.stok = r.stok;
        srcBtn.dataset.adjust = JSON.stringify(cur);
        toast(r.pesan);
      } else {
        sessionStorage.setItem("flash", r.pesan);
        location.reload();
      }
    }, true);
  }
  try {
    const f = sessionStorage.getItem("flash");
    if (f) { sessionStorage.removeItem("flash"); toast(f); }
  } catch (e) { /* storage tidak tersedia */ }

  /* ---------- dialog koreksi / batal / foto transaksi ---------- */
  document.addEventListener("click", (e) => {
    const b = e.target.closest("[data-trx-dialog]");
    if (!b) return;
    const d = document.getElementById(b.dataset.trxDialog);
    const f = d.querySelector("form");
    f.action = b.dataset.url;
    f.reset();
    $$("button", f).forEach((x) => (x.disabled = false));
    d.querySelector("[data-info]").textContent = b.dataset.info || "";
    const q = f.querySelector('[name="qty"]');
    if (q && b.dataset.qty) q.value = b.dataset.qty;
    if (b.dataset.isi) {
      Object.entries(JSON.parse(b.dataset.isi)).forEach(([k, v]) => { if (f.elements[k]) f.elements[k].value = v; });
    }
    if (f.elements.kondisi) f.elements.kondisi.dispatchEvent(new Event("change", { bubbles: true }));
    const opsiBon = d.querySelector("[data-hanya-bon]");
    if (opsiBon) {
      opsiBon.hidden = !b.dataset.bon;
      d.querySelector("[data-bon]").textContent = b.dataset.bon || "";
    }
    d.showModal();
  });

  // Dialog pengembalian: foto wajib bila barang lama benar-benar diserahkan.
  document.addEventListener("change", (e) => {
    const f = e.target.closest("#dlgKembali form");
    if (!f || !["kondisi", "qty"].includes(e.target.name)) return;
    const wajib = !!f.elements.kondisi.value && !TANPA_BARANG.test(f.elements.kondisi.value) && parseNum(f.elements.qty.value) > 0;
    f.elements.foto.required = wajib;
    f.querySelector("[data-lbl-foto-lama]").classList.toggle("req", wajib);
  });

  /* ---------- halaman input HP: satu bon, banyak barang ---------- */
  window.initInputForm = function () {
    const form = $("#formInput");
    const daftar = $("#itemList");
    const mode = () => {
      const tombol = $('input[name="tombol"]:checked').value;
      const arah = $('input[name="arah"]:checked')?.value || "kirim";
      return { tombol, arah, kirim: tombol === "keluar" || (tombol === "transfer" && arah === "kirim") };
    };
    const rows = () => $$(".item-row", daftar);
    const tujuanTipe = $("#tujuan_tipe"), tujuanKode = $("#tujuan_kode");
    const tujuanInput = $("#tujuan_bon_input"), tujuanKonfirmasi = $("#tujuanBonKonfirmasi");
    const tujuanList = $("#tujuanPickList");
    const dlgTujuanItem = $("#dlgTujuanItem"), cariTujuanItem = $("#cariTujuanItem");
    const pickTujuanItem = $("#pickTujuanItem");
    let timerTujuan = null, timerTujuanItem = null, rowTujuanAktif = null;
    let hasilTujuanBon = [], permintaanTujuan = 0;

    function plantBon(row = null) {
      return row?.dataset.plant || rows()[0]?.dataset.plant || "";
    }

    function labelTujuan(target) {
      const kodeSap = target.sap_kode && target.sap_kode !== target.kode ? ` · SAP ${target.sap_kode}` : "";
      return `${TUJUAN_TEKS[target.tipe] || target.tipe} · ${target.kode}${kodeSap}`;
    }

    async function ambilTujuan(query, tipe = "", plant = "") {
      const params = new URLSearchParams({ q: query || "", tipe: tipe || "", plant: plant || "" });
      const response = await fetch("/api/tujuan?" + params.toString());
      if (!response.ok) throw new Error("Gagal memuat tujuan");
      return response.json();
    }

    function renderTujuan(list, hasil, pilih) {
      list.innerHTML = "";
      hasil.forEach((target) => {
        const li = document.createElement("li");
        li.innerHTML = "<div></div><div class='code'></div>";
        li.firstChild.textContent = `${TUJUAN_TEKS[target.tipe] || target.tipe} · ${target.kode} · ${target.nama}`;
        li.lastChild.textContent = target.detail || target.sap_kode || "";
        li.addEventListener("click", () => pilih(target));
        list.appendChild(li);
      });
      if (!hasil.length) {
        const li = document.createElement("li");
        li.className = "muted";
        li.textContent = "Tujuan tidak ditemukan. Admin dapat memperbarui Master Supplementary.";
        list.appendChild(li);
      }
      list.hidden = false;
    }

    function kosongkanTujuanBon(pesan = "", invalid = false, detail = "") {
      tujuanTipe.value = "";
      tujuanKode.value = "";
      $("#aktivitas_kode").value = "";
      tujuanKonfirmasi.hidden = !pesan;
      tujuanKonfirmasi.classList.toggle("invalid", invalid);
      $("#tujuanBonHasil").textContent = pesan;
      $("#tujuanBonDetail").textContent = detail || (invalid
        ? "Gunakan tujuan yang muncul dari Master Supplementary."
        : "");
      aturTujuan();
    }

    function pilihTujuanBon(target, inputAsli = "") {
      tujuanTipe.value = target.tipe || "";
      tujuanKode.value = target.kode || "";
      $("#aktivitas_kode").value = target.aktivitas || "";
      tujuanKonfirmasi.hidden = false;
      tujuanKonfirmasi.classList.remove("invalid");
      $("#tujuanBonHasil").textContent = labelTujuan(target);
      const inputInfo = inputAsli && inputAsli.toLocaleUpperCase("id-ID") !== String(target.kode).toLocaleUpperCase("id-ID")
        ? `Input “${inputAsli}” dikenali sebagai ${target.kode}. ` : "";
      $("#tujuanBonDetail").textContent = inputInfo + (target.detail || target.nama || "Terdaftar di master tujuan");
      tujuanList.hidden = true;
      aturTujuan();
    }

    function setTujuanItem(row, target = null) {
      $('input[name="item_tujuan_tipe"]', row).value = target?.tipe || "";
      $('input[name="item_tujuan_kode"]', row).value = target?.kode || "";
      $('input[name="item_aktivitas_kode"]', row).value = target?.aktivitas || "";
      $(".item-tujuan-label", row).textContent = target ? labelTujuan(target) : "Ikuti tujuan utama";
    }

    function bukaTujuanItem(row) {
      rowTujuanAktif = row;
      cariTujuanItem.value = "";
      pickTujuanItem.hidden = true;
      dlgTujuanItem.querySelector("[data-item-nama]").textContent =
        `${$(".nm", row).textContent} · pilih blok, unit, atau GOH khusus.`;
      dlgTujuanItem.showModal();
      setTimeout(() => cariTujuanItem.focus(), 0);
      ambilTujuan("", "", plantBon(row)).then((hasil) => renderTujuan(pickTujuanItem, hasil, (target) => {
        setTujuanItem(rowTujuanAktif, target);
        dlgTujuanItem.close();
      })).catch(() => toast("Master tujuan tidak dapat dimuat.", "error"));
    }

    function aturTujuan() {
      const tipe = tujuanTipe?.value || "";
      const keluar = mode().tombol === "keluar";
      $("#aktivitasDamkarBox").hidden = !(keluar && tipe === "damkar");
      $("#aktivitas_kode").required = keluar && tipe === "damkar";
      const khusus = rows().some((r) => r.dataset.alokasiUnit === "1" &&
        !$('input[name="item_tujuan_tipe"]', r).value);
      const hintTujuan = $("#tujuanHint");
      if (khusus && keluar) {
        hintTujuan.textContent = "FAME, BBM, atau sparepart terpilih: tujuan wajib Unit / kendaraan atau Workshop.";
        hintTujuan.className = ["unit", "workshop"].includes(tipe) ? "hint" : "hint minus";
      } else if (tipe === "workshop") {
        hintTujuan.textContent = "Referensi SAP: CM3AZGO996 · GOH-WORKSHOP.";
        hintTujuan.className = "hint";
      } else if (tipe === "office") {
        hintTujuan.textContent = "Referensi SAP: CM3AZGO992 · GOH-OFFICE LABOUR.";
        hintTujuan.className = "hint";
      } else if (tipe === "staff") {
        hintTujuan.textContent = "Referensi SAP: CM3AZGO991 · GOH-STAFF.";
        hintTujuan.className = "hint";
      } else if (tipe === "damkar") {
        hintTujuan.textContent = "Damkar dibebankan ke WBS blok dengan aktivitas 330 atau 331 sesuai pekerjaan.";
        hintTujuan.className = "hint";
      } else {
        hintTujuan.textContent = tipe
          ? "Tujuan ini berlaku untuk semua barang. Gunakan “Bagi ke tujuan lain” hanya untuk barang yang berbeda tujuan."
          : "Pilih tujuan utama BON. Data pembebanan SAP akan ditentukan otomatis.";
        hintTujuan.className = "hint";
      }
    }

    const show = () => {
      const { tombol, arah, kirim } = mode();
      const keys = [tombol];
      if (tombol === "transfer") keys.push(arah);
      if (kirim) keys.push("kirim");
      $$("[data-show]", form).forEach((el) => {
        el.hidden = !el.dataset.show.split(" ").some((k) => keys.includes(k));
      });
      $("#lblFoto").classList.toggle("req", kirim);
      $("#lblFoto").textContent = kirim ? "Foto 1 — Orang / penerima" : "Foto 1 — Orang / penerima (opsional)";
      $("#lblKebun").textContent = arah === "terima" ? "Kebun Asal" : "Kebun Tujuan";
      $("#lblDok").classList.toggle("req", tombol === "transfer");
      $("#lblPenerima").textContent = tombol === "transfer" ? "Nama Penerima (PJ di kebun tujuan)" : "Nama Penerima";
      rows().forEach(hint);
      $$(".item-kembali, .item-tambah-kembali", daftar).forEach((el) => el.closest(".item-row").classList.toggle("no-kembali", tombol !== "keluar"));
      fotoKembali();
      label();
      aturTujuan();
    };
    function adaBarangLama(row) {
      if (mode().tombol !== "keluar" || $(".item-kembali", row).hidden) return false;
      const kond = $('select[name="kembali_kondisi"]', row).value;
      return !!kond && !TANPA_BARANG.test(kond) && parseNum($('input[name="kembali_qty"]', row).value) > 0;
    }
    function fotoKembali() {
      const ada = mode().tombol === "keluar" && rows().some((r) => !$(".item-kembali", r).hidden);
      const wajib = rows().some(adaBarangLama);
      $("#fotoKembaliBox").hidden = !ada;
      $("#lblFotoKembali").classList.toggle("req", wajib);
    }
    daftar.addEventListener("input", (e) => { if (e.target.name === "kembali_qty") fotoKembali(); });
    daftar.addEventListener("change", (e) => { if (e.target.name === "kembali_kondisi") fotoKembali(); });
    function label() {
      const { tombol, arah } = mode();
      const n = rows().length;
      $("#itemCount").textContent = n;
      $("#itemKosong").hidden = n > 0;
      const nama = tombol === "transfer" ? (arah === "terima" ? "Terima Transfer" : "Transfer Keluar") : tombol.toUpperCase();
      $("#btnSimpan").textContent = `Simpan ${nama}` + (n ? ` (${n} barang)` : "");
    }
    function hint(row) {
      const h = $(".item-hint", row);
      const v = parseNum($('input[name="qty"]', row).value);
      const stok = Number(row.dataset.stok);
      h.textContent = "";
      h.className = "item-hint hint";
      row.classList.remove("over");
      if (v == null || row.dataset.stok === "") return;
      const { kirim } = mode();
      const pecahan = rows().filter((r) => r.dataset.id === row.dataset.id);
      const total = pecahan.reduce((jumlah, r) => jumlah + (parseNum($('input[name="qty"]', r).value) || 0), 0);
      if (kirim && total > stok + 1e-9) {
        h.textContent = `Total pembagian ${fmtNum(total)} melebihi stok (${fmtNum(stok)})`;
        h.className = "item-hint hint minus";
        row.classList.add("over");
      } else {
        const saldo = kirim ? stok - total : stok + total;
        h.textContent = `${pecahan.length > 1 ? `Total dibagi: ${fmtNum(total)} · ` : ""}Sisa: ${fmtNum(Math.round(saldo * 1000) / 1000)} ${row.dataset.satuan}`;
      }
    }
    function pasang(row) {
      $('input[name="qty"]', row).addEventListener("input", () => {
        rows().filter((r) => r.dataset.id === row.dataset.id).forEach(hint);
      });
      $(".item-tambah-kembali", row).addEventListener("click", (e) => {
        e.target.hidden = true;
        $(".item-kembali", row).hidden = false;
        $('input[name="kembali_qty"]', row).value = $('input[name="qty"]', row).value;
        fotoKembali();
        $('select[name="kembali_kondisi"]', row).focus();
      });
      $(".item-del", row).addEventListener("click", () => {
        const materialId = row.dataset.id;
        row.remove();
        rows().filter((r) => r.dataset.id === materialId).forEach(hint);
        label();
        fotoKembali();
        aturTujuan();
        cari.focus();
      });
      $(".item-tujuan-ubah", row).addEventListener("click", () => bukaTujuanItem(row));
      $(".item-bagi", row).addEventListener("click", () => {
        const salinan = row.cloneNode(true);
        $('input[name="qty"]', salinan).value = "";
        $('input[name="kembali_qty"]', salinan).value = "";
        $('select[name="kembali_kondisi"]', salinan).value = "";
        setTujuanItem(salinan, null);
        row.after(salinan);
        pasang(salinan);
        hint(row);
        label();
        fotoKembali();
        bukaTujuanItem(salinan);
      });
      $('input[name="qty"]', row).addEventListener("change", (e) => {
        const kq = $('input[name="kembali_qty"]', row);
        if (!kq.value && row.dataset.wajib === "1") kq.value = e.target.value;
      });
      hint(row);
    }
    rows().forEach(pasang);

    // pencarian & penambahan barang
    const cari = $("#cari"), list = $("#pickList");
    let timer = null, hasil = [], act = -1;
    function render() {
      list.innerHTML = "";
      hasil.forEach((m, i) => {
        const li = document.createElement("li");
        li.className = i === act ? "act" : "";
        li.innerHTML = `<div></div><div class="code"></div>`;
        li.firstChild.textContent = m.deskripsi;
        const sudah = rows().some((r) => r.dataset.id === String(m.id));
        li.lastChild.textContent = `${m.material}${m.special_stock ? " · Sp " + m.special_stock : ""} · stok ${m.stok_txt} ${m.satuan}` +
          (m.input_locked ? " · DIKUNCI — jangan dipakai" : m.expired_effective ? " · EXPIRED — diblokir" : sudah ? " · sudah di daftar" : "");
        if (m.expired_effective || m.input_locked) li.classList.add("minus");
        li.addEventListener("click", () => tambah(m));
        list.appendChild(li);
      });
      if (!hasil.length && cari.value.trim()) {
        const li = document.createElement("li");
        li.className = "muted";
        li.textContent = "Tidak ditemukan. ";
        if (form.dataset.tambahUrl) {
          const a = document.createElement("a");
          a.href = form.dataset.tambahUrl + "?material=" + encodeURIComponent(cari.value.trim());
          a.textContent = "+ Tambah material baru";
          li.appendChild(a);
        } else {
          li.append("Barang baru? Minta admin menambahkannya di Master SAP.");
        }
        list.appendChild(li);
      }
      list.hidden = !cari.value.trim();
    }
    async function load() {
      const q = cari.value.trim();
      if (!q) { hasil = []; render(); return; }
      const r = await fetch("/api/material?q=" + encodeURIComponent(q));
      hasil = await r.json();
      act = hasil.length ? 0 : -1;
      render();
    }
    function tambah(m) {
      cari.value = "";
      list.hidden = true;
      if (m.expired_effective) {
        toast(`${m.deskripsi}: barang EXPIRED dan tidak dapat diinput`, "error");
        return;
      }
      if (m.input_locked) {
        toast(`${m.material}: ${m.input_lock_reason || "kode material dikunci oleh admin"}`, "error");
        return;
      }
      const ada = rows().find((r) => r.dataset.id === String(m.id));
      if (ada) {
        toast(`${m.deskripsi} sudah ada di daftar — ubah qty-nya`, "warn");
        $('input[name="qty"]', ada).focus();
        return;
      }
      const row = $("#itemTpl").content.firstElementChild.cloneNode(true);
      row.dataset.id = m.id;
      row.dataset.stok = m.stok;
      row.dataset.satuan = m.satuan;
      row.dataset.plant = m.plant;
      row.dataset.expired = m.expired_effective ? "1" : "0";
      row.dataset.inputLocked = m.input_locked ? "1" : "0";
      row.dataset.alokasiUnit = m.alokasi_unit_workshop ? "1" : "0";
      $('input[name="material_id"]', row).value = m.id;
      $(".nm", row).textContent = m.deskripsi;
      $(".cd", row).textContent = m.material + (m.special_stock ? ` · Sp ${m.special_stock} ${m.special_stock_no || ""}` : "");
      $(".st", row).textContent = `stok ${m.stok_txt} ${m.satuan}`;
      $(".sat", row).textContent = m.satuan;
      row.dataset.wajib = m.wajib_kembali ? "1" : "0";
      $(".item-kembali", row).hidden = !m.wajib_kembali;
      $(".item-tambah-kembali", row).hidden = !!m.wajib_kembali;
      daftar.appendChild(row);
      fotoKembali();
      pasang(row);
      label();
      aturTujuan();
      $('input[name="qty"]', row).focus();
    }
    cari.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(load, 200); });
    cari.addEventListener("keydown", (e) => {
      if (e.key === "ArrowDown") { act = Math.min(hasil.length - 1, act + 1); render(); e.preventDefault(); }
      else if (e.key === "ArrowUp") { act = Math.max(0, act - 1); render(); e.preventDefault(); }
      else if (e.key === "Enter") { e.preventDefault(); if (hasil[act]) tambah(hasil[act]); }
    });
    // Enter di kolom qty -> kembali ke pencarian (bukan submit)
    daftar.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && e.target.name === "qty") { e.preventDefault(); cari.focus(); }
    });
    $$('input[name="tombol"], input[name="arah"]', form).forEach((r) => r.addEventListener("change", show));
    tujuanInput.addEventListener("input", () => {
      clearTimeout(timerTujuan);
      const nomorPermintaan = ++permintaanTujuan;
      const inputAsli = tujuanInput.value.trim();
      hasilTujuanBon = [];
      kosongkanTujuanBon();
      if (!inputAsli) { tujuanList.hidden = true; return; }
      if (/^(AFD|AFDELING|DIVISI|DIV)\s*[-:]?\s*$/i.test(inputAsli)) {
        tujuanList.hidden = true;
        kosongkanTujuanBon("Nomor AFD belum ditulis", true, "Tuliskan misalnya AFD 3, lalu pilih blok yang benar.");
        return;
      }
      timerTujuan = setTimeout(async () => {
        try {
          const hasilTujuan = await ambilTujuan(inputAsli, "", plantBon());
          if (nomorPermintaan !== permintaanTujuan) return;
          hasilTujuanBon = hasilTujuan;
          const tepat = hasilTujuan.filter((target) => target.exact_match);
          if (tepat.length === 1) {
            pilihTujuanBon(tepat[0], inputAsli);
            return;
          }
          renderTujuan(tujuanList, hasilTujuan, (target) => pilihTujuanBon(target, inputAsli));
          if (!hasilTujuan.length) kosongkanTujuanBon(`“${inputAsli}” tidak terdaftar`, true);
        } catch (error) {
          tujuanList.hidden = true;
          kosongkanTujuanBon("Master tujuan tidak dapat diperiksa", true);
        }
      }, 180);
    });
    tujuanInput.addEventListener("keydown", (event) => {
      if (event.key !== "Enter") return;
      event.preventDefault();
      if (hasilTujuanBon.length) pilihTujuanBon(hasilTujuanBon[0], tujuanInput.value.trim());
    });
    cariTujuanItem.addEventListener("input", () => {
      clearTimeout(timerTujuanItem);
      timerTujuanItem = setTimeout(async () => {
        try {
          const hasilTujuan = await ambilTujuan(cariTujuanItem.value.trim(), "", plantBon(rowTujuanAktif));
          renderTujuan(pickTujuanItem, hasilTujuan, (target) => {
            setTujuanItem(rowTujuanAktif, target);
            dlgTujuanItem.close();
          });
        } catch (error) {
          pickTujuanItem.hidden = true;
        }
      }, 180);
    });
    $("#btnTujuanUmum").addEventListener("click", () => {
      if (rowTujuanAktif) setTujuanItem(rowTujuanAktif, null);
      dlgTujuanItem.close();
    });

    // Saat form ditampilkan ulang setelah validasi server, periksa lagi teksnya
    // ke API. Jangan pernah menampilkan teks tersembunyi sebagai tujuan sah tanpa
    // mencocokkannya kembali dengan master.
    if (tujuanInput.value.trim()) {
      setTimeout(() => tujuanInput.dispatchEvent(new Event("input", { bubbles: true })), 0);
    }

    const dlgDuplikat = $("#dlgDuplikat"), daftarDuplikat = $("#duplikatList");
    let lewatiCekDuplikat = false, sedangCekDuplikat = false, hasilCekDuplikat = null;
    function tampilkanDuplikat(data) {
      daftarDuplikat.innerHTML = "";
      data.duplikat.forEach((d) => {
        const kartu = document.createElement("div");
        kartu.className = "duplikat-item";
        const judul = document.createElement("div");
        const link = document.createElement("a");
        link.href = "/transaksi?q=" + encodeURIComponent(d.bon);
        link.textContent = d.bon;
        link.target = "_blank";
        judul.append(link, ` · ${d.created_at} · ${d.dibuat_oleh}`);
        const meta = document.createElement("small");
        meta.textContent = `Penerima: ${d.penerima} · Tujuan: ${d.tujuan}`;
        const ul = document.createElement("ul");
        d.items.forEach((it) => {
          const li = document.createElement("li");
          li.textContent = `${it.qty} ${it.satuan} · ${it.deskripsi}`;
          ul.appendChild(li);
        });
        kartu.append(judul, meta, ul);
        daftarDuplikat.appendChild(kartu);
      });
      dlgDuplikat.showModal();
    }
    $("#btnTetapSimpan").addEventListener("click", () => {
      if (!hasilCekDuplikat) return;
      $("#duplikatToken").value = hasilCekDuplikat.token;
      lewatiCekDuplikat = true;
      dlgDuplikat.close();
      form.requestSubmit($("#btnSimpan"));
    });
    form.addEventListener("input", (e) => {
      if (e.target.name && e.target.name !== "duplikat_token") {
        $("#duplikatToken").value = "";
        lewatiCekDuplikat = false;
      }
    });

    form.addEventListener("submit", async (e) => {
      if (lewatiCekDuplikat) return;
      const err = [];
      const { tombol, arah, kirim } = mode();
      if (!rows().length) err.push("Tambahkan minimal satu barang");
      rows().forEach((row) => {
        const inp = $('input[name="qty"]', row);
        const v = parseNum(inp.value);
        const nama = $(".nm", row).textContent;
        if (row.dataset.expired === "1") err.push(`${nama}: barang EXPIRED dan diblokir`);
        if (v == null || v <= 0) err.push(`${nama}: qty belum diisi`);
        if (tombol === "keluar" && !$(".item-kembali", row).hidden) {
          const kond = $('select[name="kembali_kondisi"]', row).value;
          const kq = parseNum($('input[name="kembali_qty"]', row).value);
          if (row.dataset.wajib === "1" && !kond) err.push(`${nama}: wajib tukar — isi barang lama yang dikembalikan`);
          else if (kond && !TANPA_BARANG.test(kond) && !(kq > 0)) err.push(`${nama}: qty barang lama belum diisi`);
        }
        const tipeBaris = $('input[name="item_tujuan_tipe"]', row).value || tujuanTipe.value;
        if (row.dataset.alokasiUnit === "1" && !["unit", "workshop"].includes(tipeBaris)) {
          err.push(`${nama}: FAME, BBM, dan sparepart wajib diarahkan ke Unit / kendaraan atau Workshop`);
        }
      });
      if (kirim) {
        const totals = new Map();
        rows().forEach((row) => {
          const qty = parseNum($('input[name="qty"]', row).value);
          if (!(qty > 0)) return;
          const current = totals.get(row.dataset.id) || { total: 0, row };
          current.total += qty;
          totals.set(row.dataset.id, current);
        });
        totals.forEach(({ total, row }) => {
          const stok = Number(row.dataset.stok);
          if (total > stok + 1e-9) {
            err.push(`${$(".nm", row).textContent}: total pembagian ${fmtNum(total)} melebihi stok ${fmtNum(stok)}`);
          }
        });
      }
      if (tombol === "transfer" && !$("#kebun").value) err.push("Pilih kebun " + (arah === "terima" ? "asal" : "tujuan"));
      if (tombol === "transfer" && !$("#nomor_dokumen").value.trim()) err.push("Nomor dokumen wajib diisi");
      if (kirim && !$("#penerima").value.trim()) err.push("Nama penerima wajib diisi");
      if (tombol === "keluar") {
        const tipe = tujuanTipe.value;
        if (!tipe) err.push("Pilih Tujuan BON");
        if (["blok", "unit", "damkar"].includes(tipe) && !tujuanKode.value.trim()) {
          err.push(tipe === "unit" ? "Kode unit wajib diisi" : "Kode blok wajib diisi");
        }
        if (tipe === "damkar" && !$("#aktivitas_kode").value) err.push("Pilih aktivitas Damkar 330 atau 331");
      }
      if (kirim && !fotoTerpilih($("#foto"))) err.push("Foto bukti wajib");
      if (rows().some(adaBarangLama) && !fotoTerpilih($("#foto_kembali"))) err.push("Foto barang lama wajib");
      if (err.length) {
        e.preventDefault();
        e.stopImmediatePropagation();
        toast(err.join(" · "), "error");
        return;
      }
      rows().forEach((row) => {
        const inp = $('input[name="qty"]', row);
        inp.value = String(parseNum(inp.value));
      });
      e.preventDefault();
      e.stopImmediatePropagation();
      if (sedangCekDuplikat) return;
      sedangCekDuplikat = true;
      const fd = new FormData();
      ["tombol", "arah", "tanggal", "penerima", "keterangan", "kebun", "tujuan_tipe", "tujuan_kode", "aktivitas_kode"].forEach((n) => {
        const el = form.elements[n];
        if (el) fd.append(n, el.value || "");
      });
      rows().forEach((row) => {
        fd.append("material_id", $('input[name="material_id"]', row).value);
        fd.append("qty", $('input[name="qty"]', row).value);
        fd.append("kembali_qty", $('input[name="kembali_qty"]', row).value);
        fd.append("kembali_kondisi", $('select[name="kembali_kondisi"]', row).value);
        fd.append("item_tujuan_tipe", $('input[name="item_tujuan_tipe"]', row).value);
        fd.append("item_tujuan_kode", $('input[name="item_tujuan_kode"]', row).value);
        fd.append("item_aktivitas_kode", $('input[name="item_aktivitas_kode"]', row).value);
      });
      try {
        hasilCekDuplikat = await postForm("/api/cek-duplikat", fd);
        if (!hasilCekDuplikat.ok) throw new Error(hasilCekDuplikat.pesan || "Gagal memeriksa");
        if (hasilCekDuplikat.duplikat.length) tampilkanDuplikat(hasilCekDuplikat);
        else {
          $("#duplikatToken").value = hasilCekDuplikat.token;
          lewatiCekDuplikat = true;
          form.requestSubmit($("#btnSimpan"));
        }
      } catch (errCek) {
        toast("Pemeriksaan double input gagal. Coba simpan lagi setelah koneksi stabil.", "error");
      } finally {
        sedangCekDuplikat = false;
      }
    }, true);
    show();
    if (!rows().length) cari.focus();
  };

  /* ---------- PR: cari & tambah barang, hitung estimasi & pembagian bulan ---------- */
  window.initPR = function () {
    const form = $("#formPR");
    if (!form) return;
    const cari = $("#cariPR"), list = $("#pickPR");
    if (cari) {
      let timer = null;
      cari.addEventListener("input", () => {
        clearTimeout(timer);
        timer = setTimeout(async () => {
          const q = cari.value.trim();
          list.innerHTML = "";
          if (!q) { list.hidden = true; return; }
          const hasil = await (await fetch("/api/material?q=" + encodeURIComponent(q))).json();
          hasil.forEach((m) => {
            const li = document.createElement("li");
            li.innerHTML = "<div></div><div class='code'></div>";
            li.firstChild.textContent = m.deskripsi;
            li.lastChild.textContent = `${m.material} · stok ${m.stok_txt} ${m.satuan}` +
              (m.input_locked ? " · DIKUNCI" : "");
            if (m.input_locked) li.classList.add("minus");
            li.addEventListener("click", () => {
              if (m.input_locked) {
                toast(`${m.material}: ${m.input_lock_reason || "kode material dikunci oleh admin"}`, "error");
                return;
              }
              $("#prMaterial").value = m.id;
              form.requestSubmit($("#btnTambahPR"));
            });
            list.appendChild(li);
          });
          if (!hasil.length) list.innerHTML = "<li class='muted'>Tidak ada di stok — pakai “+ Barang manual” di bawah.</li>";
          list.hidden = false;
        }, 200);
      });
      cari.addEventListener("keydown", (e) => { if (e.key === "Enter") e.preventDefault(); });
    }
    const fmt = (x) => Number(x).toLocaleString("id-ID", { maximumFractionDigits: 3 });
    function bagi(total, n) {
      n = Math.max(1, Math.min(6, n));
      if (Number.isInteger(total)) {
        const dasar = Math.floor(total / n), sisa = total - dasar * n;
        return Array.from({ length: n }, (_, i) => dasar + (i < sisa ? 1 : 0));
      }
      const dasar = Math.floor((total / n) * 1000) / 1000;
      const v = Array(n).fill(dasar);
      v[0] = Math.round((total - dasar * (n - 1)) * 1000) / 1000;
      return v;
    }
    function hitung(row, dariBulan) {
      const qty = parseNum($("[data-qty]", row).value) || 0;
      const harga = parseNum($("[data-harga]", row).value) || 0;
      const stok = Number(row.dataset.stok) || 0;
      $("[data-estimasi]", row).textContent = fmt(qty * harga);
      const auto = $("[data-auto]", row);
      const bulan = $$("[data-bulan]", row);
      if (dariBulan) auto.checked = false;
      if (auto.checked) {
        const v = bagi(qty + stok, Number($("[data-n]", row).value));
        bulan.forEach((b, i) => { b.value = i < v.length ? String(v[i]) : ""; });
      }
      const jml = bulan.reduce((a, b) => a + (parseNum(b.value) || 0), 0);
      const cek = Math.round((qty + stok - jml) * 1000) / 1000;
      const sel = $("[data-cek]", row);
      sel.textContent = fmt(cek);
      sel.classList.toggle("minus", Math.abs(cek) > 0.0005);
      const total = $$("[data-pr-row]").reduce((a, r) => a + (parseNum($("[data-qty]", r).value) || 0) * (parseNum($("[data-harga]", r).value) || 0), 0);
      if ($("#prTotal")) $("#prTotal").textContent = fmt(total);
    }
    $$("[data-pr-row]").forEach((row) => {
      row.addEventListener("input", (e) => {
        if (e.target.matches("[data-bulan]")) hitung(row, true);
        else if (e.target.matches("[data-qty], [data-harga]")) hitung(row, false);
      });
      row.addEventListener("change", (e) => { if (e.target.matches("[data-n], [data-auto]")) hitung(row, false); });
    });
  };

  /* ---------- stock opname ---------- */
  // dipanggil di halaman hasil opname: data sudah tersimpan di server, draft boleh dibuang
  window.opnameTersimpan = function (bagian) {
    try {
      localStorage.removeItem("opnameDraft:" + location.host); // format draft versi lama
      localStorage.removeItem("opnameDraft:" + location.host + ":" + (bagian || "langsung"));
    } catch (e) { /* abaikan */ }
  };

  window.initOpname = function () {
    // Isian qty fisik disimpan sebagai draft di browser, sehingga TIDAK hilang saat mencari / memfilter barang lain.
    const form = $("#formOpname");
    if (!form) return;
    // Tombol penutupan baru aktif setelah penutup mengonfirmasi semua anggota sudah menyimpan hitungan.
    const konfirmasiTutup = $("[data-close-check]");
    const tombolTutup = $("[data-close-button]");
    if (konfirmasiTutup && tombolTutup) {
      const aturTutup = () => {
        tombolTutup.disabled = !konfirmasiTutup.checked || tombolTutup.dataset.ready !== "1";
      };
      konfirmasiTutup.addEventListener("change", aturTutup);
      aturTutup();
    }
    const cepat = form.dataset.quick === "1";
    const KUNCI = "opnameDraft:" + location.host + ":" + (form.dataset.draftKey || "langsung");
    const baca = () => { try { return JSON.parse(localStorage.getItem(KUNCI) || "{}"); } catch (e) { return {}; } };
    const tulis = (d) => { try { localStorage.setItem(KUNCI, JSON.stringify(d)); } catch (e) { /* abaikan */ } };
    let draft = baca();
    const tampil = new Set($$("[data-opname-row]").map((r) => r.dataset.mid));

    function barisLain(mid, d) {
      const tr = document.createElement("tr");
      tr.dataset.opnameRow = "";
      Object.assign(tr.dataset, { mid, material: d.material, deskripsi: d.deskripsi, satuan: d.satuan, stok: d.stok,
                                  search: `${d.material} ${d.deskripsi}` });
      tr.className = "draft-lain";
      tr.innerHTML = `<td class="nowrap c-kode"></td><td class="desc c-mat"></td><td class="c-sat"></td>
        <td class="num c-stok"></td><td class="num c-sap"><small>dari pencarian lain</small></td>
        <td class="num c-fisik"><label class="mobile-only">Qty fisik</label><input class="inline opname-qty" inputmode="decimal" autocomplete="off" enterkeyhint="next"></td>
        <td class="num c-selisih" data-label="Selisih" data-diff></td>
        <td class="c-ket"><input class="inline opname-ket" placeholder="Keterangan (opsional)"></td>`;
      const td = tr.children;
      td[0].textContent = d.material; td[1].textContent = d.deskripsi; td[2].textContent = d.satuan; td[3].textContent = fmtNum(d.stok);
      const [fisik, ket] = $$("input", tr);
      fisik.name = "fisik_" + mid; fisik.dataset.stok = d.stok; fisik.value = d.fisik || "";
      ket.name = "ket_" + mid; ket.value = d.ket || "";
      return tr;
    }
    // pulihkan isian: baris yang tampil diisi ulang, sisanya ditampilkan di atas tabel
    $$("[data-opname-row]").forEach((r) => {
      const d = draft[r.dataset.mid];
      if (!d) return;
      const fisik = $(`input[name="fisik_${r.dataset.mid}"]`, r);
      if (fisik.disabled) { delete draft[r.dataset.mid]; tulis(draft); return; }
      fisik.value = d.fisik || "";
      $(`input[name="ket_${r.dataset.mid}"]`, r).value = d.ket || "";
    });
    Object.entries(draft).forEach(([mid, d]) => { if (!tampil.has(mid)) $("#draftLain").appendChild(barisLain(mid, d)); });

    // Pencarian lokal: hasil langsung menjadi kartu teratas, tanpa reload atau geser tabel.
    const cariCepat = $("#opnameCariCepat");
    const norm = (s) => String(s || "").toLocaleLowerCase("id-ID").replace(/\s+/g, " ").trim();
    const prioritaskan = (baris) => {
      if (!baris) return;
      const wadah = $("#opnamePrioritas", form);
      const daftar = $("#opnameBaris", form);
      if (!wadah || !daftar) return;
      // Hanya satu barang menjadi prioritas. Pilihan lama tetap ada, tetapi kembali ke daftar biasa.
      Array.from(wadah.children).forEach((r) => {
        r.classList.remove("opname-priority");
        daftar.prepend(r);
      });
      baris.classList.add("opname-priority");
      wadah.appendChild(baris);
    };
    const filterCepat = () => {
      if (!cariCepat) return [];
      const kata = norm(cariCepat.value).split(" ").filter(Boolean);
      const cocok = [];
      $$("[data-opname-row]", form).forEach((r) => {
        const teks = norm(r.dataset.search || `${r.dataset.material} ${r.dataset.deskripsi}`);
        const tampilkan = kata.every((k) => teks.includes(k));
        r.hidden = !tampilkan;
        r.classList.toggle("opname-row-match", tampilkan && kata.length > 0);
        if (tampilkan) cocok.push(r);
      });
      const n = $("#opnameTerlihat");
      if (n) n.textContent = cocok.length;
      const kosong = $("#opnameKosongCari");
      if (kosong) kosong.hidden = cocok.length > 0;
      if (kata.length && cocok.length) {
        const persis = cocok.find((r) => norm(r.dataset.material) === norm(cariCepat.value));
        if (persis || cocok.length === 1) prioritaskan(persis || cocok[0]);
      }
      return cocok;
    };
    if (cariCepat) {
      cariCepat.addEventListener("input", filterCepat);
      cariCepat.addEventListener("keydown", (e) => {
        if (e.key !== "Enter") return;
        e.preventDefault();
        const cocok = filterCepat();
        const persis = cocok.find((r) => norm(r.dataset.material) === norm(cariCepat.value));
        const baris = persis || cocok[0];
        if (!baris) {
          const filter = $(".opname-filter");
          const qServer = $('input[name="q"]', filter);
          if (qServer && cariCepat.value.trim()) {
            qServer.value = cariCepat.value.trim();
            toast("Mencari di seluruh gudang…", "ok");
            filter.requestSubmit();
          } else toast("Material tidak ditemukan", "error");
          return;
        }
        prioritaskan(baris);
        if (baris.dataset.locked === "1") {
          toast(`Sudah dihitung oleh ${baris.dataset.owner || "petugas lain"}`, "error");
          baris.scrollIntoView({ block: "center", behavior: "smooth" });
          return;
        }
        setTimeout(() => {
          const i = $("input[name^=fisik_]", baris);
          i?.focus({ preventScroll: true }); i?.select(); i?.scrollIntoView({ block: "center", behavior: "smooth" });
        }, 80);
      });
      $("#opnameCariHapus")?.addEventListener("click", () => {
        cariCepat.value = ""; filterCepat(); cariCepat.focus();
      });
      const awal = filterCepat();
      if (cariCepat.value.trim() && awal.length === 1 && awal[0].dataset.locked !== "1") {
        setTimeout(() => {
          const i = $('input[name^="fisik_"]', awal[0]);
          i?.focus({ preventScroll: true }); i?.select(); i?.scrollIntoView({ block: "center" });
        }, 80);
      }
    }

    const count = () => {
      let berubah = 0;
      $$("input[name^=fisik_]:not(:disabled)").forEach((i) => {
        const v = parseNum(i.value), cell = i.closest("tr").querySelector("[data-diff]");
        if (v == null) { cell.textContent = ""; return; }
        const d = Math.round((v - Number(i.dataset.stok)) * 1000) / 1000;
        cell.textContent = d === 0 ? "0" : (d > 0 ? "+" : "") + fmtNum(d);
        cell.className = "num " + (d > 0 ? "plus" : d < 0 ? "minus" : "");
        if (d !== 0 && (!cepat || draft[i.closest("tr").dataset.mid])) berubah++;
      });
      const n = Object.keys(draft).length;
      $("#opnameCount").textContent = `${n} barang diisi · ${berubah} berubah`;
      $("#draftInfo").hidden = n === 0;
      $("[data-draft-n]").textContent = n;
    };
    document.addEventListener("input", (e) => {
      const nm = e.target.name || "";
      if (!nm.startsWith("fisik_") && !nm.startsWith("ket_")) return;
      const tr = e.target.closest("[data-opname-row]");
      const mid = tr.dataset.mid;
      const fisik = $(`input[name="fisik_${mid}"]`, tr).value.trim();
      const ket = $(`input[name="ket_${mid}"]`, tr).value.trim();
      if (fisik || ket) {
        draft[mid] = { fisik, ket, material: tr.dataset.material, deskripsi: tr.dataset.deskripsi,
                       satuan: tr.dataset.satuan, stok: Number(tr.dataset.stok) };
      } else delete draft[mid];
      tulis(draft);
      count();
    });
    // Enter pindah ke baris berikutnya, bukan submit
    document.addEventListener("keydown", (e) => {
      if (e.key !== "Enter" || !e.target.name?.startsWith("fisik_")) return;
      e.preventDefault();
      if (cepat && cariCepat) {
        cariCepat.value = "";
        filterCepat();
        cariCepat.focus();
        return;
      }
      const all = $$("input[name^=fisik_]:not(:disabled)");
      all[all.indexOf(e.target) + 1]?.focus();
    });
    $("#draftHapus").addEventListener("click", () => {
      if (!confirm("Kosongkan semua isian qty fisik yang belum disimpan?")) return;
      const mids = Object.keys(draft);
      draft = {}; tulis(draft);
      $("#draftLain").innerHTML = "";
      if (cepat) mids.forEach((mid) => {
        const r = $(`[data-opname-row][data-mid="${mid}"]`, form);
        if (r) $$("input[name^=fisik_], input[name^=ket_]", r).forEach((i) => (i.value = ""));
      });
      else $$("input[name^=fisik_]:not(:disabled), input[name^=ket_]:not(:disabled)").forEach((i) => (i.value = ""));
      count();
    });
    // ringkasan sebelum simpan: semua barang yang akan disesuaikan
    form.addEventListener("submit", (e) => {
      const isi = cepat
        ? Object.keys(draft).map((mid) => $(`input[name="fisik_${mid}"]`, form)).filter((i) => i && i.value.trim() !== "")
        : $$("input[name^=fisik_]:not(:disabled)").filter((i) => i.value.trim() !== "");
      if (!isi.length) { e.preventDefault(); toast("Belum ada qty fisik yang diisi", "error"); return; }
      const baris = isi.map((i) => {
        const tr = i.closest("[data-opname-row]");
        const v = parseNum(i.value), stok = Number(i.dataset.stok);
        const tanda = v === stok ? "sama" : `${fmtNum(stok)} → ${fmtNum(v)}`;
        return `• ${tr.dataset.deskripsi}: ${tanda}${v === 0 && stok > 0 ? "  PERHATIAN: jadi NOL" : ""}`;
      });
      const pesan = `Simpan opname ${isi.length} barang?\n\n${baris.slice(0, 25).join("\n")}` +
        (baris.length > 25 ? `\n… dan ${baris.length - 25} lainnya` : "");
      if (!confirm(pesan)) { e.preventDefault(); e.stopImmediatePropagation(); return; }
      if (cepat) $$("[data-opname-row]", form).forEach((r) => {
        if (draft[r.dataset.mid]) return;
        $$("input[name^=fisik_], input[name^=ket_]", r).forEach((i) => (i.disabled = true));
      });
    }, true);
    count();
  };

  /* ---------- rekonsiliasi SAP: arah transfer wajib dipilih operator ---------- */
  window.initRekonsiliasi = function () {
    const form = $("#formRekon");
    if (!form) return;
    $$('[data-rekon-arah]').forEach((blok) => {
      const arah = $('[data-arah]', blok), pihak = $('[data-pihak]', blok);
      const indeks = arah.name.replace("arah_", "");
      const cek = $(`input[value="${indeks}"][form="formRekon"]`);
      const atur = () => {
        const transfer = arah.value === "transfer_masuk" || arah.value === "transfer_keluar";
        pihak.hidden = !transfer;
        pihak.required = transfer;
        if (arah.value && cek) cek.checked = true;
      };
      arah.addEventListener("change", atur);
      atur();
    });
    form.addEventListener("submit", (e) => {
      const salah = [];
      Array.from(form.elements).filter((el) =>
        (el.name === "matches" || el.name === "news" || el.name === "reviews") && el.checked).forEach((cek) => {
        const arah = $(`[name="arah_${cek.value}"]`);
        if (!arah) return;
        const pihak = $(`[name="pihak_${cek.value}"]`);
        if (!arah.value) salah.push(`baris pilihan ${cek.value}: arah belum dipilih`);
        if ((arah.value === "transfer_masuk" || arah.value === "transfer_keluar") && !pihak.value.trim()) {
          salah.push(`baris pilihan ${cek.value}: kebun asal/tujuan belum diisi`);
        }
      });
      if (salah.length) {
        e.preventDefault();
        e.stopImmediatePropagation();
        toast("Verifikasi transfer belum lengkap · " + salah.join(" · "), "error");
      }
    }, true);
  };

  /* ---------- master: ubah kategori / minimum langsung di tabel ---------- */
  window.initMaster = function () {
    document.addEventListener("change", async (e) => {
      const el = e.target.closest("[data-inline]");
      if (!el) return;
      const fd = new FormData();
      fd.append("ids", el.dataset.id);
      if (el.dataset.inline === "kategori_id") fd.append("kategori_id", el.value);
      else if (el.dataset.inline === "wajib_kembali") fd.append("wajib_kembali", el.checked ? "1" : "0");
      else if (el.dataset.inline === "expired") fd.append("expired", el.checked ? "1" : "0");
      else if (el.dataset.inline === "input_locked") fd.append("input_locked", el.checked ? "1" : "0");
      else if (el.dataset.inline === "expired_at") fd.append("expired_at", el.value);
      else {
        const v = parseNum(el.value);
        if (v == null || v < 0) { toast("Stok minimum harus angka ≥ 0", "error"); return; }
        fd.append("stok_minimum", String(v));
      }
      const r = await postForm("/master/atur", fd);
      toast(r.ok ? "Tersimpan" : r.pesan || "Gagal menyimpan", r.ok ? "ok" : "error");
    });
  };
})();
