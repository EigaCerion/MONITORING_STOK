import './styles.css';
import { App } from '@capacitor/app';
import {
  clearAuth, deleteQueue, getCache, getOrCreateDeviceId, getQueue, getSetting,
  listQueue, putQueue, setCache, setSetting,
} from './db.js';
import {
  bootstrap as fetchBootstrap, cancelReturn, cancelTransaction, closeOpname, correctTransaction, editTransaction, fetchTransactionPhoto,
  health, login, mergeBons, normalizeServerUrl, splitBon,
  setTransactionPosting, startOpname, uploadOpnameCount, uploadReturn, uploadTransaction, uploadTransactionPhoto,
} from './api.js';
import { compressPhoto } from './photos.js';
import { checkUpdate, compareVersions, downloadUpdate } from './update.js';

const APP_VERSION = __APP_VERSION__;
const RETURN_CONDITIONS = [
  'Rusak', 'Aus / habis pakai', 'Tabung / wadah kosong',
  'Hilang / tidak dikembalikan', 'Tidak ada — pekerja baru / tambahan',
];
const NO_PHYSICAL_RETURN = new Set(['Hilang / tidak dikembalikan', 'Tidak ada — pekerja baru / tambahan']);
const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const numberValue = (value) => Number(String(value ?? '').replace(',', '.'));

const state = {
  bootstrap: null,
  user: null,
  queue: [],
  transactionType: 'keluar',
  items: [],
  targets: [],
  defaultTarget: null,
  editingItemIndex: null,
  returnDraft: { transaction: null, photo: null },
  cancelReturnDraft: null,
  historyPhotoDraft: { transaction: null, slot: 2, photo: null },
  cancelDraft: null,
  editDraft: null,
  postingDraft: null,
  correctionDraft: null,
  photoViewerUrl: null,
  historySelection: new Set(),
  opnameSessionId: null,
  photos: { photo: null, photo_2: null, return_photo: null },
  update: null,
};

const localDate = () => {
  const date = new Date();
  const offset = date.getTimezoneOffset() * 60000;
  return new Date(date.getTime() - offset).toISOString().slice(0, 10);
};

const escapeText = (value) => String(value ?? '');

const toast = (message) => {
  const node = $('#toast');
  node.textContent = message;
  node.classList.add('show');
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => node.classList.remove('show'), 3200);
};

const showDialog = (title, body) => {
  $('#dialogTitle').textContent = title;
  const target = $('#dialogBody');
  target.replaceChildren();
  if (body instanceof Node) target.append(body);
  else {
    const paragraph = document.createElement('p');
    paragraph.textContent = String(body);
    target.append(paragraph);
  }
  $('#messageDialog').showModal();
};

const go = (name) => {
  $$('.screen').forEach((screen) => screen.classList.toggle('active', screen.id === `screen${name[0].toUpperCase()}${name.slice(1)}`));
  $$('.bottom-nav button').forEach((button) => button.classList.toggle('active', button.dataset.screen === name));
  if (name === 'queue') renderQueue();
  if (name === 'returns') renderPendingReturns();
  if (name === 'stock') renderStock();
  if (name === 'history') renderHistory();
  if (name === 'opname') renderOpname();
  if (name === 'input') renderInputAvailability();
  window.scrollTo({ top: 0, behavior: 'smooth' });
};

const todayReady = () => state.bootstrap?.valid_for_date === localDate();

const updateMandatory = () => compareVersions(
  state.bootstrap?.update?.minimum_version, APP_VERSION,
) === 1;

const pendingQueue = () => state.queue.filter((entry) => !['sent'].includes(entry.status));

const renderConnection = async (probe = false) => {
  const badge = $('#netBadge');
  if (!navigator.onLine) {
    badge.textContent = 'Offline'; badge.className = 'status-pill warn'; return false;
  }
  if (!probe) {
    badge.textContent = 'Ada jaringan'; badge.className = 'status-pill neutral'; return true;
  }
  try {
    await health();
    badge.textContent = 'Server terhubung'; badge.className = 'status-pill ok'; return true;
  } catch {
    badge.textContent = 'Server terputus'; badge.className = 'status-pill warn'; return false;
  }
};

const renderHome = async () => {
  const token = await getSetting('token');
  state.user = await getSetting('user');
  $('#setupCard').hidden = Boolean(token);
  $('#syncCard').hidden = !token;
  $('#summaryGrid').hidden = !token;
  $('#mainNav').hidden = !token;
  $('#settingsShortcut').hidden = !token;
  $('#headerUser').textContent = state.user?.name || 'Aplikasi Gudang';
  $('#settingsServer').textContent = await getSetting('server_url', 'Belum diatur');
  $('#deviceIdText').textContent = await getOrCreateDeviceId();
  $('#appVersion').textContent = `v${APP_VERSION}`;
  $('#materialCount').textContent = state.bootstrap?.materials?.length ?? 0;
  $('#pendingCount').textContent = pendingQueue().length;
  const queuedReturns = new Set(pendingQueue().filter((entry) => entry.kind === 'return').map((entry) => entry.transaction_id));
  const pendingReturns = (state.bootstrap?.pending_returns || []).filter((row) => !queuedReturns.has(row.transaction_id));
  $('#pendingReturnCount').textContent = pendingReturns.length;
  $('#serverTransactionCount').textContent = new Set(
    (state.bootstrap?.recent_transactions || []).map((row) => row.bon || `#${row.id}`),
  ).size;
  $('#opnameSessionCount').textContent = state.bootstrap?.opname?.sessions?.length ?? 0;
  const ready = todayReady();
  $('#syncState').textContent = ready ? '✓' : '!';
  $('#syncState').classList.toggle('ok', ready);
  $('#syncInfo').textContent = ready
    ? `Siap offline · ${state.bootstrap.materials.length} material · sinkron ${new Date(state.bootstrap.synced_at).toLocaleTimeString('id-ID', { hour: '2-digit', minute: '2-digit' })}`
    : 'Belum ada data master dan saldo untuk hari ini.';
  $('#syncButton').textContent = ready ? 'Perbarui data pagi' : 'Sinkron sekarang';
  if (updateMandatory() && state.bootstrap?.update?.github_repo) {
    $('#updateCard').hidden = false;
    $('#updateTitle').textContent = `Pembaruan wajib minimal ${state.bootstrap.update.minimum_version}`;
    $('#updateNotes').textContent = 'Admin mewajibkan versi aplikasi yang lebih baru sebelum membuat transaksi baru.';
  }
  renderInputAvailability();
};

const targetLabel = (target) => {
  const type = { blok: 'Blok', unit: 'Unit', workshop: 'Workshop', office: 'Kantor', staff: 'Staff', damkar: 'Damkar' }[target?.type] || target?.type || 'Tujuan';
  const code = target?.code || '-';
  return `${type} · ${code}${target?.name ? ` — ${target.name}` : ''}`;
};

const buildTargets = () => {
  const fixed = (state.bootstrap?.fixed_targets || []).map((target) => ({
    type: target.type, code: target.code, sapCode: target.code, name: target.name,
    plant: state.bootstrap.user?.plant || '', activity: '', division: '', phase: '',
  }));
  const dynamic = (state.bootstrap?.targets || []).map((target) => ({
    type: target.target_type, code: target.code, name: target.name || target.sap_code || '',
    sapCode: target.sap_code || '', plant: target.plant, activity: '',
    division: target.division || '', phase: target.phase || '',
  }));
  const unique = new Map();
  [...fixed, ...dynamic].forEach((target) => unique.set(`${target.plant}|${target.type}|${target.code}`, target));
  state.targets = [...unique.values()].sort((a, b) => targetLabel(a).localeCompare(targetLabel(b), 'id'));
};

const isOutgoing = () => state.transactionType === 'keluar';
const isTransfer = () => state.transactionType.startsWith('transfer_');
const isStockOutflow = () => ['keluar', 'transfer_keluar'].includes(state.transactionType);
const isStockOutflowType = (type) => ['keluar', 'transfer_keluar'].includes(type);
const isMobileReadOnly = (row) => Boolean(
  row?.mobile_read_only || row?.sap_posted || ['sap_mb51', 'sinkron_sap'].includes(row?.sumber),
);
const displayDate = (value) => {
  if (!value) return '-';
  const [year, month, day] = String(value).slice(0, 10).split('-');
  return year && month && day ? `${day}/${month}/${year}` : String(value);
};
const transactionTypeLabel = (type) => ({
  keluar: 'Barang keluar', masuk: 'Barang masuk',
  transfer_keluar: 'Transfer keluar', transfer_masuk: 'Transfer masuk',
}[type] || type);

const renderTransferGardens = () => {
  const select = $('#transferGarden');
  const selected = select.value;
  select.replaceChildren(new Option('— pilih kebun —', ''));
  (state.bootstrap?.gardens || [])
    .filter((garden) => garden.kode !== state.bootstrap?.user?.plant)
    .forEach((garden) => select.add(new Option(`${garden.nama} (${garden.kode})`, garden.kode)));
  if ([...select.options].some((option) => option.value === selected)) select.value = selected;
};

const renderTransactionType = () => {
  const outgoing = isOutgoing();
  const stockOutflow = isStockOutflow();
  $('#inputTitle').textContent = transactionTypeLabel(state.transactionType);
  $$('[data-transaction-type]').forEach((button) => {
    const value = button.dataset.transactionType;
    button.classList.toggle('active', value === state.transactionType || (value === 'transfer' && isTransfer()));
  });
  $$('[data-transfer-direction]').forEach((button) => {
    button.classList.toggle('active', button.dataset.transferDirection === state.transactionType);
  });
  $('#transferFields').hidden = !isTransfer();
  $('#transferGardenLabel').textContent = state.transactionType === 'transfer_masuk' ? 'Kebun asal' : 'Kebun tujuan';
  renderTransferGardens();
  $('#recipientField').hidden = !stockOutflow;
  $('#outgoingTargetFields').hidden = !outgoing;
  $('#incomingDocumentField').hidden = outgoing;
  $('#documentLabel').textContent = isTransfer() ? 'Nomor dokumen transfer' : 'Nomor dokumen / surat jalan';
  $('#documentRequiredMark').hidden = !isTransfer();
  $('#transactionDocument').placeholder = isTransfer() ? 'wajib, mis. STO / surat jalan' : 'opsional, mis. GRN atau DO';
  $('#photoSectionTitle').textContent = stockOutflow ? '3. Foto bukti' : '3. Foto penerimaan (opsional)';
  $('#photo1Label').textContent = stockOutflow ? 'Foto 1 — orang / penerima' : 'Foto 1 — barang diterima';
  $('#photoRequiredMark').hidden = !stockOutflow;
  renderItems();
};

const setTransactionType = (transactionType) => {
  const nextType = transactionType === 'transfer' ? 'transfer_keluar' : transactionType;
  if (!['keluar', 'masuk', 'transfer_keluar', 'transfer_masuk'].includes(nextType)
      || nextType === state.transactionType) return;
  state.transactionType = nextType;
  state.defaultTarget = null;
  state.items.forEach((item) => {
    item.target = null;
    item.return_enabled = nextType === 'keluar' && Boolean(item.material.wajib_kembali);
    item.return_qty = '';
    item.return_qty_touched = false;
    item.return_condition = '';
  });
  state.photos.return_photo = null;
  $('#targetSearch').value = '';
  $('#targetResults').hidden = true;
  renderDefaultTargetConfirmation();
  renderTransactionType();
};

const renderInputAvailability = () => {
  const opnameLocked = Boolean(state.bootstrap?.user?.plant
    && state.bootstrap?.opname?.sessions?.some((session) => session.plant === state.bootstrap.user.plant));
  const locked = !todayReady() || updateMandatory() || opnameLocked;
  $('#inputLocked').hidden = !locked;
  $('#inputLocked').textContent = updateMandatory()
    ? `Versi ${APP_VERSION} sudah di bawah versi minimum ${state.bootstrap.update.minimum_version}. Perbarui aplikasi sebelum input baru.`
    : opnameLocked ? 'Gudang sedang Stock Opname. Transaksi baru dikunci sampai sesi selesai.'
      : 'Sinkron pagi wajib dilakukan sebelum membuat transaksi hari ini.';
  $$('#transactionForm input, #transactionForm select, #transactionForm button').forEach((element) => {
    element.disabled = locked;
  });
};

const filteredMaterials = (query) => {
  const words = String(query).toUpperCase().trim().split(/\s+/).filter(Boolean);
  if (!words.length) return [];
  return (state.bootstrap?.materials || []).filter((material) => {
    const haystack = `${material.material} ${material.deskripsi}`.toUpperCase();
    return words.every((word) => haystack.includes(word));
  }).slice(0, 30);
};

const requiresUnitOrWorkshop = (material) => {
  const category = normalizeTargetText(material?.kategori).replace(/[^A-Z0-9]/g, '');
  const description = normalizeTargetText(material?.deskripsi);
  return ['BBM', 'SPAREPART'].includes(category) || description.includes('FAME');
};

const physicalReturnPresent = () => state.items.some((item) => item.return_enabled
  && item.return_condition && !NO_PHYSICAL_RETURN.has(item.return_condition)
  && numberValue(item.return_qty) > 0);

const renderMaterialResults = () => {
  const results = filteredMaterials($('#materialSearch').value);
  const box = $('#materialResults');
  box.replaceChildren();
  results.forEach((material) => {
    const button = document.createElement('button');
    button.type = 'button';
    const name = document.createElement('b');
    name.textContent = material.deskripsi;
    const detail = document.createElement('small');
    detail.textContent = `${material.material} · stok ${material.stok} ${material.satuan}`;
    if (material.expired || material.input_locked) {
      detail.className = 'locked-text';
      detail.textContent += material.expired ? ' · EXPIRED' : ` · DIKUNCI: ${material.input_lock_reason}`;
      button.disabled = true;
    }
    button.append(name, detail);
    button.addEventListener('click', () => addMaterial(material));
    box.append(button);
  });
  box.hidden = !results.length;
};

const normalizeTargetText = (value) => String(value ?? '')
  .toUpperCase().replace(/\s+/g, ' ').trim();

const compactTargetCode = (value) => normalizeTargetText(value).replace(/[^A-Z0-9]/g, '');

const targetDetail = (target) => [
  target.sapCode && target.sapCode !== target.code ? `SAP ${target.sapCode}` : '',
  target.division ? `AFD ${target.division}` : '',
  target.phase || '',
  target.plant || '',
].filter(Boolean).join(' · ') || 'Tujuan terdaftar di master';

const fixedTargetAliases = {
  workshop: new Set(['WORKSHOP', 'BENGKEL', 'GOH WORKSHOP', 'GOH-WORKSHOP']),
  office: new Set(['KANTOR', 'OFFICE', 'GOH OFFICE', 'GOH-OFFICE', 'GOH OFFICE LABOUR']),
  staff: new Set(['STAFF', 'STAF', 'GOH STAFF', 'GOH-STAFF']),
};

const searchTargets = (rawQuery, plant = '') => {
  const input = normalizeTargetText(rawQuery);
  if (!input) return [];

  const divisionMatch = input.match(/^(?:AFD|AFDELING|DIVISI|DIV)\s*[-:]?\s*(\d+)(?:\s+(.+))?$/);
  if (/^(?:AFD|AFDELING|DIVISI|DIV)\s*[-:]?\s*$/.test(input)) return [];

  let query = input;
  let requestedType = '';
  let division = '';
  let damkar = false;
  if (divisionMatch) {
    division = divisionMatch[1];
    query = (divisionMatch[2] || '').trim();
    requestedType = 'blok';
  } else {
    const prefix = query.match(/^(BLOK|BLOCK|UNIT|DAMKAR|KEBAKARAN)\s*[-:]?\s*(.*)$/);
    if (prefix) {
      query = prefix[2].trim();
      if (['BLOK', 'BLOCK'].includes(prefix[1])) requestedType = 'blok';
      else if (prefix[1] === 'UNIT') requestedType = 'unit';
      else { requestedType = 'blok'; damkar = true; }
    }
  }

  let queryCode = compactTargetCode(query);
  if (/^EX\d+$/.test(queryCode)) {
    queryCode = `EXA${queryCode.slice(2)}`;
    query = queryCode;
  }
  const words = normalizeTargetText(query).split(' ').filter(Boolean);

  return state.targets
    .filter((target) => !plant || !target.plant || target.plant === plant)
    .filter((target) => !requestedType || target.type === requestedType)
    .filter((target) => !division || String(target.division) === division)
    .map((target) => {
      const code = compactTargetCode(target.code);
      const sapCode = compactTargetCode(target.sapCode);
      const haystack = normalizeTargetText(`${target.code} ${target.sapCode} ${target.name} ${target.division} ${target.phase}`);
      const aliasMatch = fixedTargetAliases[target.type]?.has(input) || false;
      const exact = Boolean(aliasMatch || (queryCode && [code, sapCode].includes(queryCode)));
      const matches = division || aliasMatch || words.every((word) => haystack.includes(word));
      let score = exact ? 100 : 0;
      if (queryCode && code.startsWith(queryCode)) score += 30;
      if (queryCode && sapCode.startsWith(queryCode)) score += 25;
      if (words.length && words.every((word) => haystack.includes(word))) score += 10;
      return { target: damkar ? { ...target, type: 'damkar', name: `Damkar · ${target.name}` } : target, exact, matches, score };
    })
    .filter((result) => result.matches)
    .sort((a, b) => b.score - a.score || targetLabel(a.target).localeCompare(targetLabel(b.target), 'id'))
    .slice(0, division ? 100 : 30);
};

const renderTargetResults = (container, results, onChoose) => {
  container.replaceChildren();
  results.forEach(({ target, exact }) => {
    const button = document.createElement('button');
    button.type = 'button';
    button.classList.toggle('exact', exact);
    const name = document.createElement('b'); name.textContent = targetLabel(target);
    const detail = document.createElement('small'); detail.textContent = targetDetail(target);
    button.append(name, detail);
    button.addEventListener('click', () => onChoose(target));
    container.append(button);
  });
  container.hidden = !results.length;
};

const renderNoTargetResult = (container, query) => {
  container.replaceChildren();
  if (!String(query).trim()) { container.hidden = true; return; }
  const message = document.createElement('p');
  message.className = 'target-empty';
  message.textContent = `“${String(query).trim()}” tidak terdaftar. Periksa kode atau minta Admin memperbarui master supplementary.`;
  container.append(message);
  container.hidden = false;
};

const renderDefaultTargetConfirmation = () => {
  const confirmation = $('#targetConfirmation');
  confirmation.hidden = !state.defaultTarget;
  if (!state.defaultTarget) return;
  $('#targetConfirmationName').textContent = targetLabel(state.defaultTarget);
  $('#targetConfirmationDetail').textContent = targetDetail(state.defaultTarget);
};

const selectDefaultTarget = (target) => {
  state.defaultTarget = { ...target };
  $('#targetSearch').value = target.code;
  $('#targetResults').hidden = true;
  state.items.forEach((item) => {
    item.target = { ...target };
  });
  renderDefaultTargetConfirmation();
  renderItems();
};

const handleDefaultTargetSearch = () => {
  const previous = state.defaultTarget;
  state.defaultTarget = null;
  if (previous) {
    state.items.forEach((item) => {
      if (item.target?.type === previous.type && item.target?.code === previous.code) item.target = null;
    });
    renderItems();
  }
  renderDefaultTargetConfirmation();
  const query = $('#targetSearch').value;
  const plant = state.items[0]?.material?.plant || state.bootstrap?.user?.plant || '';
  const results = searchTargets(query, plant);
  const exact = results.filter((result) => result.exact);
  if (exact.length === 1) {
    selectDefaultTarget(exact[0].target);
    return;
  }
  if (!results.length) { renderNoTargetResult($('#targetResults'), query); return; }
  renderTargetResults($('#targetResults'), results, selectDefaultTarget);
};

const addMaterial = (material) => {
  const target = isOutgoing() && state.defaultTarget
    && (!state.defaultTarget.plant || state.defaultTarget.plant === material.plant)
    ? { ...state.defaultTarget } : null;
  if (state.items.some((item) => item.material.id === material.id
      && item.target?.type === target?.type && item.target?.code === target?.code)) {
    toast(isOutgoing()
      ? 'Barang dengan tujuan yang sama sudah ada; ubah tujuan dahulu untuk membaginya'
      : 'Barang tersebut sudah dipilih; ubah qty pada baris yang ada');
    return;
  }
  state.items.push({
    material, qty: '', target,
    return_enabled: isOutgoing() && Boolean(material.wajib_kembali), return_qty: '', return_qty_touched: false,
    return_condition: '',
  });
  $('#materialSearch').value = '';
  $('#materialResults').hidden = true;
  renderItems();
};

const splitItemToAnotherTarget = (itemIndex) => {
  if (!isOutgoing()) return;
  const source = state.items[itemIndex];
  if (!source) return;
  state.items.splice(itemIndex + 1, 0, {
    material: source.material, qty: '', target: null,
    return_enabled: Boolean(source.material.wajib_kembali), return_qty: '', return_qty_touched: false,
    return_condition: '',
  });
  renderItems();
  openTargetDialog(itemIndex + 1);
};

const closeTargetDialog = () => {
  state.editingItemIndex = null;
  $('#targetDialog').close();
};

const selectItemTarget = (target) => {
  const index = state.editingItemIndex;
  if (!Number.isInteger(index) || !state.items[index]) return;
  state.items[index].target = { ...target };
  closeTargetDialog();
  renderItems();
  toast(`Tujuan dipilih: ${target.code}`);
};

const renderItemTargetSearch = () => {
  const item = state.items[state.editingItemIndex];
  if (!item) return;
  const results = searchTargets($('#targetDialogSearch').value, item.material.plant);
  const exact = results.filter((result) => result.exact);
  if (exact.length === 1) {
    selectItemTarget(exact[0].target);
    return;
  }
  if (!results.length) { renderNoTargetResult($('#targetDialogResults'), $('#targetDialogSearch').value); return; }
  renderTargetResults($('#targetDialogResults'), results, selectItemTarget);
};

const openTargetDialog = (itemIndex) => {
  const item = state.items[itemIndex];
  if (!item) return;
  state.editingItemIndex = itemIndex;
  $('#targetDialogTitle').textContent = item.material.deskripsi;
  $('#targetDialogSearch').value = '';
  $('#targetDialogResults').replaceChildren();
  $('#targetDialogResults').hidden = true;
  $('#targetDialog').showModal();
  setTimeout(() => $('#targetDialogSearch').focus(), 50);
};

const targetButton = (item, itemIndex) => {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = `target-button${item.target ? '' : ' missing'}`;
  button.textContent = item.target ? targetLabel(item.target) : 'Cari tujuan barang…';
  button.setAttribute('aria-label', `Tujuan ${item.material.deskripsi}`);
  button.addEventListener('click', () => openTargetDialog(itemIndex));
  return button;
};

const renderItems = () => {
  const list = $('#selectedItems');
  list.replaceChildren();
  state.items.forEach((item, index) => {
    const card = document.createElement('div'); card.className = `item-card${isOutgoing() ? '' : ' incoming'}`; card.dataset.itemIndex = String(index);
    const head = document.createElement('div'); head.className = 'item-head';
    const title = document.createElement('div');
    const name = document.createElement('b'); name.textContent = item.material.deskripsi;
    const code = document.createElement('small'); code.textContent = `${item.material.material} · stok ${item.material.stok} ${item.material.satuan}`;
    title.append(name, code);
    const remove = document.createElement('button'); remove.type = 'button'; remove.textContent = '×'; remove.setAttribute('aria-label', 'Hapus barang');
    remove.addEventListener('click', () => { state.items.splice(index, 1); renderItems(); });
    head.append(title, remove);

    const meta = document.createElement('div'); meta.className = 'item-meta';
    let returnQtyInput = null;
    const qtyLabel = document.createElement('label'); qtyLabel.textContent = `Qty (${item.material.satuan})`;
    const qty = document.createElement('input'); qty.inputMode = 'decimal'; qty.value = item.qty; qty.placeholder = '0';
    qty.addEventListener('input', () => {
      state.items[index].qty = qty.value;
      if (isOutgoing() && item.material.wajib_kembali && !state.items[index].return_qty_touched) {
        state.items[index].return_qty = qty.value;
        if (returnQtyInput) returnQtyInput.value = qty.value;
      }
      updateStockHints(); renderReturnPhoto();
    });
    qtyLabel.append(qty);
    meta.append(qtyLabel);
    if (isOutgoing()) {
      const targetLabelNode = document.createElement('label'); targetLabelNode.textContent = 'Tujuan barang';
      targetLabelNode.append(targetButton(item, index));
      meta.append(targetLabelNode);
    }

    if (item.target?.type === 'damkar') {
      const activityLabel = document.createElement('label'); activityLabel.textContent = 'Aktivitas Damkar';
      const activity = document.createElement('select');
      activity.add(new Option('— pilih aktivitas —', ''));
      activity.add(new Option('330 · Pencegahan kebakaran', '330'));
      activity.add(new Option('331 · Penanggulangan kebakaran', '331'));
      activity.value = item.target.activity || '';
      activity.addEventListener('change', () => { state.items[index].target.activity = activity.value; });
      activityLabel.append(activity); meta.append(activityLabel);
    }

    if (isOutgoing()) {
      const actions = document.createElement('div'); actions.className = 'item-actions';
      const split = document.createElement('button'); split.type = 'button'; split.className = 'item-link'; split.textContent = '+ Bagi ke tujuan lain';
      split.addEventListener('click', () => splitItemToAnotherTarget(index));
      actions.append(split);
      if (!item.return_enabled) {
        const addReturn = document.createElement('button'); addReturn.type = 'button'; addReturn.className = 'item-link';
        addReturn.textContent = '+ Ada barang lama dikembalikan';
        addReturn.addEventListener('click', () => {
          state.items[index].return_enabled = true;
          state.items[index].return_qty = state.items[index].qty;
          renderItems();
        });
        actions.append(addReturn);
      }
      meta.append(actions);
    }

    if (isOutgoing() && item.return_enabled) {
      const returns = document.createElement('div'); returns.className = 'return-fields';
      const returnHead = document.createElement('div'); returnHead.className = 'return-head';
      const returnTitle = document.createElement('b');
      returnTitle.textContent = item.material.wajib_kembali ? '↩ Barang lama · wajib tukar' : '↩ Barang lama · opsional';
      returnHead.append(returnTitle);
      if (!item.material.wajib_kembali) {
        const cancelReturn = document.createElement('button'); cancelReturn.type = 'button'; cancelReturn.className = 'item-link danger-link'; cancelReturn.textContent = 'Batalkan';
        cancelReturn.addEventListener('click', () => {
          state.items[index].return_enabled = false;
          state.items[index].return_qty = '';
          state.items[index].return_qty_touched = false;
          state.items[index].return_condition = '';
          renderItems();
        });
        returnHead.append(cancelReturn);
      }
      const returnQtyLabel = document.createElement('label'); returnQtyLabel.textContent = 'Qty barang lama';
      const returnQty = document.createElement('input'); returnQty.inputMode = 'decimal'; returnQty.value = item.return_qty; returnQty.placeholder = '0';
      returnQty.className = 'return-qty-input'; returnQtyInput = returnQty;
      returnQty.addEventListener('input', () => {
        state.items[index].return_qty = returnQty.value;
        state.items[index].return_qty_touched = true;
        renderReturnPhoto();
      });
      returnQtyLabel.append(returnQty);
      const conditionLabel = document.createElement('label'); conditionLabel.textContent = 'Kondisi barang lama';
      const condition = document.createElement('select'); condition.add(new Option('— pilih kondisi —', ''));
      RETURN_CONDITIONS.forEach((value) => condition.add(new Option(value, value)));
      condition.value = item.return_condition;
      condition.addEventListener('change', () => { state.items[index].return_condition = condition.value; renderReturnPhoto(); });
      conditionLabel.append(condition);
      returns.append(returnHead, returnQtyLabel, conditionLabel); meta.append(returns);
    }
    const stockHint = document.createElement('small'); stockHint.className = 'item-stock-hint';
    card.append(head, meta, stockHint); list.append(card);
  });
  $('#emptyItems').hidden = state.items.length > 0;
  updateStockHints();
  renderReturnPhoto();
};

const updateStockHints = () => {
  const totals = new Map();
  state.items.forEach((item) => totals.set(
    item.material.id, (totals.get(item.material.id) || 0) + (numberValue(item.qty) || 0),
  ));
  $$('.item-card').forEach((card) => {
    const item = state.items[Number(card.dataset.itemIndex)];
    if (!item) return;
    const total = totals.get(item.material.id) || 0;
    const resulting = Number(item.material.stok) + (isStockOutflow() ? -total : total);
    const splitCount = state.items.filter((row) => row.material.id === item.material.id).length;
    const hint = card.querySelector('.item-stock-hint');
    if (isOutgoing()) {
      hint.textContent = `${splitCount > 1 ? `Total dibagi ${total} · ` : ''}Sisa ${Math.round(resulting * 1000) / 1000} ${item.material.satuan}`;
    } else if (state.transactionType === 'transfer_keluar') {
      hint.textContent = `Sisa setelah dikirim ${Math.round(resulting * 1000) / 1000} ${item.material.satuan}`;
    } else if (state.transactionType === 'transfer_masuk') {
      hint.textContent = `Stok setelah transfer diterima ${Math.round(resulting * 1000) / 1000} ${item.material.satuan}`;
    } else {
      hint.textContent = `Stok setelah diterima ${Math.round(resulting * 1000) / 1000} ${item.material.satuan}`;
    }
    hint.classList.toggle('danger-text', isStockOutflow() && resulting < 0);
  });
};

const renderReturnPhoto = () => {
  const visible = isOutgoing() && state.items.some((item) => item.return_enabled);
  const required = isOutgoing() && physicalReturnPresent();
  $('#returnPhotoBlock').hidden = !visible;
  $('#returnPhotoRequired').hidden = !required;
  $('#returnPhotoHint').textContent = required
    ? 'Wajib karena ada barang lama yang benar-benar diserahkan.'
    : 'Tidak wajib untuk kondisi “Hilang” atau “Tidak ada — pekerja baru”.';
};

const bindPhoto = (cameraSelector, gallerySelector, previewSelector, stateKey) => {
  const handler = async (event) => {
    const file = event.target.files?.[0];
    if (!file) return;
    try {
      const result = await compressPhoto(file);
      state.photos[stateKey] = result;
      const preview = $(previewSelector);
      preview.src = result.preview; preview.hidden = false;
      toast('Foto siap disimpan offline');
    } catch (error) { showDialog('Foto gagal dibaca', error.message); }
    event.target.value = '';
  };
  $(cameraSelector).addEventListener('change', handler);
  $(gallerySelector).addEventListener('change', handler);
};

const availablePendingReturns = () => {
  const queued = new Set(pendingQueue().filter((entry) => entry.kind === 'return').map((entry) => entry.transaction_id));
  return (state.bootstrap?.pending_returns || []).filter((row) => !queued.has(row.transaction_id));
};

const renderPendingReturns = () => {
  const rows = availablePendingReturns();
  const list = $('#pendingReturnList'); list.replaceChildren();
  $('#returnsBadge').textContent = `${rows.length} barang`;
  $('#returnsEmpty').hidden = rows.length > 0;
  rows.forEach((row) => {
    const card = document.createElement('div'); card.className = 'queue-card';
    const head = document.createElement('div'); head.className = 'queue-head';
    const title = document.createElement('div');
    const name = document.createElement('b'); name.textContent = row.deskripsi;
    const detail = document.createElement('small');
    detail.textContent = `${row.bon || `Transaksi #${row.transaction_id}`} · keluar ${row.qty_out} ${row.satuan} · ${row.penerima || '-'}`;
    title.append(name, detail);
    const date = document.createElement('span'); date.className = 'status-pill warn'; date.textContent = row.tanggal;
    head.append(title, date);
    const action = document.createElement('button'); action.type = 'button'; action.className = 'btn primary block'; action.textContent = 'Catat pengembalian';
    action.addEventListener('click', () => openReturnDialog(row));
    card.append(head, action); list.append(card);
  });
};

const stockState = (material) => {
  if (material.expired || material.input_locked) return 'locked';
  const stock = Number(material.stok) || 0;
  if (stock <= 0) return 'empty';
  const minimum = Number(material.stok_minimum) || 0;
  if (minimum > 0 && stock <= minimum) return 'low';
  return 'available';
};

const filteredStockRows = () => {
  const words = normalizeTargetText($('#stockSearch')?.value).split(' ').filter(Boolean);
  const status = $('#stockStatus')?.value || '';
  const sort = $('#stockSort')?.value || 'name';
  const rows = (state.bootstrap?.materials || []).filter((material) => {
    if (status && stockState(material) !== status) return false;
    const haystack = normalizeTargetText([
      material.material, material.deskripsi, material.kategori, material.plant, material.lokasi_blok,
    ].join(' '));
    return words.every((word) => haystack.includes(word));
  });
  rows.sort((a, b) => {
    if (sort === 'code') return a.material.localeCompare(b.material, 'id');
    if (sort === 'stock_desc') return Number(b.stok) - Number(a.stok) || a.deskripsi.localeCompare(b.deskripsi, 'id');
    if (sort === 'stock_asc') return Number(a.stok) - Number(b.stok) || a.deskripsi.localeCompare(b.deskripsi, 'id');
    return a.deskripsi.localeCompare(b.deskripsi, 'id');
  });
  return rows;
};

const renderStock = () => {
  const materials = state.bootstrap?.materials || [];
  const rows = filteredStockRows();
  const states = materials.map(stockState);
  $('#stockAvailableCount').textContent = states.filter((value) => value === 'available').length;
  $('#stockLowCount').textContent = states.filter((value) => value === 'low').length;
  $('#stockEmptyCount').textContent = states.filter((value) => value === 'empty').length;
  $('#stockBadge').textContent = `${rows.length} material`;
  $('#stockEmpty').hidden = rows.length > 0;
  $('#stockSyncInfo').textContent = state.bootstrap?.synced_at
    ? `Saldo terakhir disinkron ${new Date(state.bootstrap.synced_at).toLocaleString('id-ID', { dateStyle: 'medium', timeStyle: 'short' })}. Tetap dapat dilihat tanpa sinyal.`
    : 'Lakukan Sinkron pagi untuk memperbarui saldo.';
  const list = $('#stockList'); list.replaceChildren();
  rows.forEach((material) => {
    const status = stockState(material);
    const card = document.createElement('article'); card.className = `stock-card ${status}`;
    const info = document.createElement('div');
    const name = document.createElement('h2'); name.textContent = material.deskripsi;
    const code = document.createElement('small'); code.className = 'stock-code';
    code.textContent = `${material.material} · ${material.plant}${material.kategori ? ` · ${material.kategori}` : ''}`;
    info.append(name, code);
    const quantity = document.createElement('div'); quantity.className = 'stock-qty';
    const amount = document.createElement('b'); amount.textContent = `${material.stok} ${material.satuan}`;
    const label = document.createElement('small');
    label.textContent = status === 'locked' ? 'Tidak dapat diinput' : status === 'empty' ? 'Stok habis' : status === 'low' ? 'Stok menipis' : 'Stok tersedia';
    quantity.append(amount, label);
    const flags = document.createElement('div'); flags.className = 'stock-flags';
    const addFlag = (text, className = '') => {
      if (!text) return;
      const flag = document.createElement('span'); flag.textContent = text; flag.className = className; flags.append(flag);
    };
    addFlag(material.lokasi_blok ? `Lokasi ${material.lokasi_blok}` : 'Lokasi belum diisi');
    if (Number(material.stok_minimum) > 0) addFlag(`Minimum ${material.stok_minimum} ${material.satuan}`, status === 'low' ? 'warn' : '');
    if (material.expired) addFlag('EXPIRED', 'danger');
    if (material.input_locked) addFlag(material.input_lock_reason || 'DIKUNCI ADMIN', 'danger');
    card.append(info, quantity, flags); list.append(card);
  });
};

const opnameSessions = () => state.bootstrap?.opname?.sessions || [];

const activeOpnameSession = () => {
  const sessions = opnameSessions();
  let session = sessions.find((row) => row.id === state.opnameSessionId);
  if (!session && sessions.length) {
    [session] = sessions;
    state.opnameSessionId = session.id;
  }
  return session || null;
};

const pendingOpnameMaterials = (sessionId) => {
  const result = new Map();
  pendingQueue().filter((entry) => entry.kind === 'opname_count' && entry.session_id === sessionId)
    .forEach((entry) => entry.payload.items.forEach((item) => result.set(Number(item.material_id), entry)));
  return result;
};

const fillOpnameStartGardens = () => {
  const select = $('#opnamePlant');
  const selected = select.value;
  select.replaceChildren(new Option('— pilih gudang —', ''));
  (state.bootstrap?.gardens || []).forEach((garden) => select.add(new Option(`${garden.nama} (${garden.kode})`, garden.kode)));
  const delegated = state.bootstrap?.opname?.delegated_plant || state.bootstrap?.user?.plant || '';
  if ([...select.options].some((option) => option.value === (selected || delegated))) select.value = selected || delegated;
};

const renderOpnameMaterials = (session) => {
  const query = normalizeTargetText($('#opnameSearch').value);
  const words = query.split(' ').filter(Boolean);
  const existing = new Map((session.items || []).map((item) => [Number(item.material_id), item]));
  const queued = pendingOpnameMaterials(session.id);
  const onlyUncounted = $('#opnameUncountedOnly').checked;
  const materials = (state.bootstrap?.materials || [])
    .filter((material) => material.plant === session.plant)
    .filter((material) => {
      if (onlyUncounted && (existing.has(Number(material.id)) || queued.has(Number(material.id)))) return false;
      const haystack = normalizeTargetText(`${material.material} ${material.deskripsi} ${material.kategori || ''}`);
      return words.every((word) => haystack.includes(word));
    })
    .sort((a, b) => {
      const aExact = compactTargetCode(a.material) === compactTargetCode(query) ? 1 : 0;
      const bExact = compactTargetCode(b.material) === compactTargetCode(query) ? 1 : 0;
      return bExact - aExact || a.deskripsi.localeCompare(b.deskripsi, 'id');
    })
    .slice(0, query ? 60 : 30);
  const list = $('#opnameMaterialList'); list.replaceChildren();
  $('#opnameMaterialEmpty').hidden = materials.length > 0;
  materials.forEach((material) => {
    const counted = existing.get(Number(material.id));
    const queuedEntry = queued.get(Number(material.id));
    const countedByOther = counted && Number(counted.counted_by) !== Number(state.bootstrap.user.id);
    const card = document.createElement('article');
    card.className = `stock-card opname-card${countedByOther ? ' locked' : ''}`;
    const info = document.createElement('div');
    const name = document.createElement('h2'); name.textContent = material.deskripsi;
    const code = document.createElement('small'); code.className = 'stock-code';
    code.textContent = `${material.material} · stok buku ${material.stok} ${material.satuan}`;
    info.append(name, code);
    const status = document.createElement('div'); status.className = 'stock-qty';
    const statusText = document.createElement('b');
    statusText.textContent = queuedEntry ? 'Antrean' : counted ? `${counted.fisik} ${material.satuan}` : 'Belum';
    const statusSub = document.createElement('small');
    statusSub.textContent = queuedEntry ? 'Menunggu upload' : counted ? `oleh ${counted.counted_by_name}` : 'Belum dihitung';
    status.append(statusText, statusSub);
    const fields = document.createElement('div'); fields.className = 'opname-fields';
    const qtyLabel = document.createElement('label'); qtyLabel.textContent = `Qty fisik (${material.satuan})`;
    const qty = document.createElement('input'); qty.inputMode = 'decimal'; qty.placeholder = '0';
    qty.value = counted && !countedByOther ? counted.fisik : '';
    qty.disabled = Boolean(countedByOther || queuedEntry);
    qtyLabel.append(qty);
    const noteLabel = document.createElement('label'); noteLabel.textContent = 'Keterangan';
    const note = document.createElement('input'); note.placeholder = 'opsional, mis. rak A';
    note.value = counted && !countedByOther ? (counted.keterangan || '') : '';
    note.disabled = Boolean(countedByOther || queuedEntry); noteLabel.append(note);
    const save = document.createElement('button'); save.type = 'button'; save.className = 'btn primary';
    save.textContent = counted ? 'Perbarui hitungan' : 'Simpan hitungan';
    save.disabled = Boolean(countedByOther || queuedEntry);
    save.addEventListener('click', async () => {
      try { await saveOpnameDraft(session, material, qty.value, note.value); }
      catch (error) { showDialog('Hitungan belum disimpan', error.message); }
    });
    fields.append(qtyLabel, noteLabel, save);
    card.append(info, status, fields); list.append(card);
  });
};

const renderOpname = () => {
  const sessions = opnameSessions();
  const session = activeOpnameSession();
  $('#opnameNoSession').hidden = Boolean(session);
  $('#opnameActive').hidden = !session;
  $('#opnameBadge').textContent = session ? `Sesi #${session.id}` : 'Belum ada sesi';
  const canStart = Boolean(state.bootstrap?.opname?.can_start);
  $('#opnameStartForm').hidden = !canStart || Boolean(session);
  $('#opnameDate').value ||= localDate();
  fillOpnameStartGardens();
  const fixedPlant = state.bootstrap?.user?.role === 'petugas';
  $('#opnamePlantField').hidden = fixedPlant;
  if (!session) return;
  const picker = $('#opnameSessionPicker'); picker.replaceChildren();
  sessions.forEach((row) => picker.add(new Option(`#${row.id} · ${row.garden_name} · ${row.jenis_barang}`, row.id)));
  picker.value = String(session.id); $('#opnameSessionPickerField').hidden = sessions.length < 2;
  $('#opnameSessionMeta').textContent = `SESI #${session.id} · ${session.tanggal}`;
  $('#opnameSessionName').textContent = `${session.jenis_barang} · ${session.garden_name}`;
  $('#opnameSessionNote').textContent = [session.lokasi, session.keterangan].filter(Boolean).join(' · ') || session.plant;
  $('#opnameProgress').textContent = session.counted;
  const members = $('#opnameMembers'); members.replaceChildren();
  (session.members || []).forEach((member) => {
    const badge = document.createElement('span'); badge.textContent = `${member.name}: ${member.count} barang`; members.append(badge);
  });
  if (!session.members?.length) {
    const badge = document.createElement('span'); badge.textContent = 'Belum ada anggota yang posting'; members.append(badge);
  }
  $('#opnameCloseCard').hidden = !session.can_close;
  renderOpnameMaterials(session);
};

const saveOpnameDraft = async (session, material, rawQuantity, note) => {
  const quantity = numberValue(rawQuantity);
  if (!Number.isFinite(quantity) || quantity < 0) throw new Error('Isi qty fisik 0 atau lebih');
  const clientUuid = crypto.randomUUID();
  await putQueue({
    kind: 'opname_count', client_uuid: clientUuid, session_id: session.id,
    owner_user_id: state.bootstrap.user.id, status: 'pending', created_at: new Date().toISOString(),
    payload: { client_uuid: clientUuid, items: [{ material_id: material.id, quantity, note: note.trim() }] },
    summary: [{ material: material.material, description: `Opname · ${material.deskripsi}`, qty: quantity, unit: material.satuan }],
    error: '', matches: [], server_result: null,
  });
  state.queue = await listQueue();
  renderOpname(); await renderHome();
  toast('Hitungan tersimpan di antrean HP');
};

const submitOpnameStart = async () => {
  if (!navigator.onLine) throw new Error('Membuka sesi membutuhkan koneksi server');
  const plant = state.bootstrap.user.role === 'petugas'
    ? (state.bootstrap.opname.delegated_plant || state.bootstrap.user.plant) : $('#opnamePlant').value;
  if (!plant) throw new Error('Pilih gudang yang akan diopname');
  const result = await startOpname({
    plant, date: $('#opnameDate').value || localDate(), item_type: $('#opnameItemType').value.trim() || 'UMUM',
    location: $('#opnameLocation').value.trim(), note: $('#opnameNote').value.trim(),
  });
  state.bootstrap.opname.sessions = [
    ...state.bootstrap.opname.sessions.filter((row) => row.id !== result.session.id), result.session,
  ];
  state.opnameSessionId = result.session.id;
  await setCache('bootstrap', state.bootstrap); renderOpname(); await renderHome();
  toast(result.created ? 'Sesi opname berhasil dibuka' : 'Sesi opname aktif dibuka');
};

const openOpnameCloseDialog = () => {
  const session = activeOpnameSession();
  if (!session) return;
  const pending = pendingQueue().filter((entry) => entry.kind === 'opname_count' && entry.session_id === session.id);
  if (pending.length) {
    showDialog('Masih ada hitungan belum di-upload', `Upload ${pending.length} antrean opname terlebih dahulu sebelum menutup sesi.`); return;
  }
  $('#opnameConfirmAll').checked = false; $('#opnameCloseConfirm').disabled = true;
  const members = $('#opnameCloseMembers'); members.replaceChildren();
  (session.members || []).forEach((member) => {
    const badge = document.createElement('span'); badge.textContent = `${member.name}: ${member.count} barang`; members.append(badge);
  });
  $('#opnameCloseDialog').showModal();
};

const submitOpnameClose = async () => {
  const session = activeOpnameSession();
  if (!session || !$('#opnameConfirmAll').checked) throw new Error('Konfirmasi semua anggota wajib dicentang');
  const result = await closeOpname(session.id);
  Object.entries(result.stocks || {}).forEach(([materialId, stock]) => {
    const material = state.bootstrap.materials.find((row) => Number(row.id) === Number(materialId));
    if (material) material.stok = Number(stock);
  });
  state.bootstrap.opname.sessions = state.bootstrap.opname.sessions.filter((row) => row.id !== session.id);
  state.opnameSessionId = null; await setCache('bootstrap', state.bootstrap);
  $('#opnameCloseDialog').close(); renderOpname(); renderStock(); await renderHome();
  showDialog('Sesi opname selesai', `${result.counted} material dihitung dan ${result.adjusted} stok disesuaikan.`);
};

const filteredHistoryRows = () => {
  const words = normalizeTargetText($('#historySearch')?.value).split(' ').filter(Boolean);
  const status = $('#historyStatus')?.value || '';
  const transactionType = $('#historyType')?.value || '';
  return (state.bootstrap?.recent_transactions || []).filter((row) => {
    if (transactionType && row.jenis !== transactionType) return false;
    if (status === 'pending' && (row.batal || row.sap_posted)) return false;
    if (status === 'posted' && (row.batal || !row.sap_posted)) return false;
    if (status === 'cancelled' && !row.batal) return false;
    const haystack = normalizeTargetText([
      row.bon, row.jenis, row.material, row.deskripsi, row.penerima, row.keterangan,
      row.tujuan_tipe, row.tujuan_kode, row.kebun_asal, row.kebun_tujuan,
      row.nomor_dokumen, row.sap_doc,
    ].join(' '));
    return words.every((word) => haystack.includes(word));
  });
};

const renderHistory = () => {
  const rows = filteredHistoryRows();
  const groups = new Map();
  rows.forEach((row) => {
    const key = row.bon || `#${row.id}`;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(row);
  });
  const list = $('#historyList'); list.replaceChildren();
  $('#historyBadge').textContent = `${groups.size} BON`;
  $('#historyEmpty').hidden = groups.size > 0;
  groups.forEach((items, bon) => {
    const first = items[0];
    const card = document.createElement('div'); card.className = 'queue-card history-card';
    const head = document.createElement('div'); head.className = 'queue-head';
    const title = document.createElement('div');
    const bonNode = document.createElement('b'); bonNode.textContent = first.bon || `Transaksi #${first.id}`;
    const detail = document.createElement('small');
    const party = first.jenis === 'transfer_masuk' ? ` · dari ${first.kebun_asal || '-'}`
      : first.jenis === 'transfer_keluar' ? ` · ke ${first.kebun_tujuan || '-'}`
        : isStockOutflowType(first.jenis) ? ` · ${first.penerima || '-'}` : '';
    detail.textContent = `${displayDate(first.tanggal)} · ${transactionTypeLabel(first.jenis)}${party}`;
    title.append(bonNode, detail);
    const allCancelled = items.every((row) => row.batal);
    const activeItems = items.filter((row) => !row.batal);
    const allPosted = activeItems.length > 0 && activeItems.every((row) => row.sap_posted);
    const groupReadOnly = activeItems.some(isMobileReadOnly);
    const status = document.createElement('span');
    status.className = `status-pill ${allCancelled ? 'danger' : allPosted ? 'ok' : 'warn'}`;
    status.textContent = allCancelled ? 'BATAL' : allPosted ? 'Sudah SAP' : 'Belum SAP';
    head.append(title, status); card.append(head);

    const itemList = document.createElement('div'); itemList.className = 'history-items';
    items.forEach((row) => {
      const item = document.createElement('div'); item.className = `history-item${row.batal ? ' cancelled' : ''}`;
      const name = document.createElement('b'); name.textContent = row.deskripsi;
      const code = document.createElement('small');
      if (row.jenis === 'masuk') {
        code.textContent = `${row.material} · +${row.qty} ${row.satuan}${row.nomor_dokumen ? ` · Dok ${row.nomor_dokumen}` : ''}`;
      } else if (row.jenis === 'transfer_masuk') {
        code.textContent = `${row.material} · +${row.qty} ${row.satuan} · ← ${row.kebun_asal || '-'} · Dok ${row.nomor_dokumen || '-'}`;
      } else if (row.jenis === 'transfer_keluar') {
        code.textContent = `${row.material} · −${row.qty} ${row.satuan} · → ${row.kebun_tujuan || '-'} · Dok ${row.nomor_dokumen || '-'}`;
      } else {
        const destination = row.tujuan_tipe || row.tujuan_kode
          ? targetLabel({ type: row.tujuan_tipe, code: row.tujuan_kode, name: '' })
          : row.keterangan || row.penerima || 'Tujuan tidak tersedia';
        code.textContent = `${row.material} · −${row.qty} ${row.satuan} · ${destination}`;
      }
      const flags = document.createElement('small'); flags.className = 'history-flags';
      const returnFlag = row.jenis === 'keluar'
        ? (row.has_return ? '↩ kembali tercatat' : row.wajib_kembali ? '⚠ belum kembali' : '') : '';
      const photoFlag = `📷 ${Number(row.has_photo) + Number(row.has_photo_2)}/2`;
      const sapDate = row.sap_posting_date ? `posting ${displayDate(row.sap_posting_date)}` : '';
      const source = row.sumber === 'sap_mb51' ? 'Impor MB51'
        : row.sumber === 'sinkron_sap' ? 'Penyesuaian SAP' : row.created_by_name ? `dicatat ${row.created_by_name}` : '';
      flags.textContent = [photoFlag, returnFlag, row.sap_doc ? `SAP ${row.sap_doc}` : '', sapDate, source, row.batal_alasan || '']
        .filter(Boolean).join(' · ');
      const content = document.createElement('div'); content.append(name, code, flags);
      if (state.bootstrap?.user?.role === 'admin' && !row.batal && !isMobileReadOnly(row)) {
        const correct = document.createElement('button'); correct.type = 'button'; correct.className = 'item-link';
        correct.textContent = 'Koreksi qty';
        correct.addEventListener('click', () => openCorrectionDialog(row));
        const cancel = document.createElement('button'); cancel.type = 'button'; cancel.className = 'item-link danger-link';
        cancel.textContent = 'Batalkan transaksi ini';
        cancel.addEventListener('click', () => openCancelDialog(row));
        content.append(correct);
        if (row.return_id) {
          const cancelReturnButton = document.createElement('button');
          cancelReturnButton.type = 'button'; cancelReturnButton.className = 'item-link danger-link';
          cancelReturnButton.textContent = 'Batalkan catatan kembali';
          cancelReturnButton.addEventListener('click', () => openCancelReturnDialog(row));
          content.append(cancelReturnButton);
        }
        content.append(cancel);
        if (row.jenis === 'keluar') {
          const selection = document.createElement('div'); selection.className = 'history-select';
          const checkbox = document.createElement('input'); checkbox.type = 'checkbox'; checkbox.checked = state.historySelection.has(row.id);
          checkbox.setAttribute('aria-label', `Pilih transaksi ${row.id}`);
          checkbox.addEventListener('change', () => {
            if (checkbox.checked) state.historySelection.add(row.id); else state.historySelection.delete(row.id);
            renderHistorySelectionBar();
          });
          selection.append(checkbox, content); item.append(selection);
        } else item.append(content);
      } else item.append(content);
      itemList.append(item);
    });
    card.append(itemList);
    const actions = document.createElement('div'); actions.className = 'queue-actions';
    [1, 2].forEach((slot) => {
      const field = slot === 1 ? 'has_photo' : 'has_photo_2';
      const target = items.find((row) => row[field]);
      if (!target) return;
      const view = document.createElement('button'); view.type = 'button'; view.className = 'btn';
      view.textContent = `Lihat Foto ${slot}`;
      view.addEventListener('click', () => openPhotoViewer(target, slot));
      actions.append(view);
    });
    if (['admin', 'petugas'].includes(state.bootstrap?.user?.role) && !allCancelled && !groupReadOnly) {
      const queuedPhotos = new Set(pendingQueue().filter((entry) => entry.kind === 'transaction_photo')
        .map((entry) => `${entry.bon || `#${entry.transaction_id}`}|${entry.payload.slot}`));
      [1, 2].forEach((slot) => {
        if (queuedPhotos.has(`${bon}|${slot}`)) return;
        const field = slot === 1 ? 'has_photo' : 'has_photo_2';
        const target = items.find((row) => !row.batal && !isMobileReadOnly(row) && !row[field]);
        if (!target) return;
        const button = document.createElement('button'); button.type = 'button'; button.className = 'btn';
        button.textContent = `+ Foto ${slot}`;
        button.addEventListener('click', () => openHistoryPhotoDialog(target, slot));
        actions.append(button);
      });
      const active = items.find((row) => !row.batal);
      if (state.bootstrap.user.role === 'admin' && active) {
        const edit = document.createElement('button'); edit.type = 'button'; edit.className = 'btn'; edit.textContent = 'Edit detail BON';
        edit.addEventListener('click', () => openEditTransactionDialog(active));
        actions.append(edit);
      }
      if (active && !allPosted) {
        const posting = document.createElement('button'); posting.type = 'button'; posting.className = 'btn';
        posting.textContent = 'Tandai sudah SAP';
        posting.addEventListener('click', () => openPostingDialog(active, false));
        actions.append(posting);
      }
    }
    if (groupReadOnly && !allCancelled) {
      const locked = document.createElement('div'); locked.className = 'history-readonly';
      locked.textContent = first.sumber === 'sap_mb51'
        ? 'Impor SAP · hanya baca. Transaksi ini tidak berasal dari input aplikasi.'
        : 'Sudah direalisasikan SAP · foto dan detail dikunci. Koreksi dilakukan melalui Admin web.';
      card.append(locked);
    }
    if (actions.childElementCount) card.append(actions);
    list.append(card);
  });
  renderHistorySelectionBar();
};

const renderHistorySelectionBar = () => {
  const knownIds = new Set((state.bootstrap?.recent_transactions || [])
    .filter((row) => !row.batal && !isMobileReadOnly(row) && row.jenis === 'keluar').map((row) => row.id));
  [...state.historySelection].forEach((id) => { if (!knownIds.has(id)) state.historySelection.delete(id); });
  const count = state.historySelection.size;
  $('#historySelectionBar').hidden = count === 0;
  $('#historySelectionCount').textContent = count;
  $('#mergeBonButton').disabled = count < 2;
};

const changeSelectedBon = async (action, confirmPosted = false) => {
  const ids = [...state.historySelection];
  if (!ids.length) throw new Error('Pilih transaksi terlebih dahulu');
  if (action === 'merge' && ids.length < 2) throw new Error('Pilih minimal dua transaksi untuk disatukan');
  try {
    const result = action === 'merge'
      ? await mergeBons(ids, confirmPosted) : await splitBon(ids, confirmPosted);
    const changed = new Set(result.transaction_ids);
    state.bootstrap.recent_transactions.forEach((row) => {
      if (!changed.has(row.id)) return;
      row.bon = result.bon;
      if (action === 'merge') {
        if (result.has_photo) row.has_photo = 1;
        if (result.has_photo_2) row.has_photo_2 = 1;
      }
    });
    state.historySelection.clear();
    await setCache('bootstrap', state.bootstrap);
    renderHistory();
    toast(action === 'merge' ? `Transaksi disatukan ke ${result.bon}` : `Transaksi dipisahkan ke ${result.bon}`);
  } catch (error) {
    if (error.body?.needs_posted_confirmation && !confirmPosted) {
      const approved = confirm('Ada transaksi yang sudah posting SAP. Nomor BON pada SAP mungkin perlu disesuaikan. Tetap lanjutkan?');
      if (approved) return changeSelectedBon(action, true);
      return;
    }
    throw error;
  }
};

const closePhotoViewer = () => {
  if (state.photoViewerUrl) URL.revokeObjectURL(state.photoViewerUrl);
  state.photoViewerUrl = null;
  $('#photoViewerImage').hidden = true;
  $('#photoViewerImage').removeAttribute('src');
  $('#photoViewerDialog').close();
};

const openPhotoViewer = async (transaction, slot) => {
  if (state.photoViewerUrl) URL.revokeObjectURL(state.photoViewerUrl);
  state.photoViewerUrl = null;
  $('#photoViewerTitle').textContent = `Foto ${slot} · ${transaction.bon || `#${transaction.id}`}`;
  $('#photoViewerLoading').hidden = false;
  $('#photoViewerImage').hidden = true;
  $('#photoViewerDialog').showModal();
  try {
    const blob = await fetchTransactionPhoto(transaction.id, slot);
    state.photoViewerUrl = URL.createObjectURL(blob);
    $('#photoViewerImage').src = state.photoViewerUrl;
    $('#photoViewerImage').hidden = false;
    $('#photoViewerLoading').hidden = true;
  } catch (error) {
    closePhotoViewer(); showDialog('Foto tidak dapat dibuka', error.message);
  }
};

const openHistoryPhotoDialog = (transaction, slot) => {
  if (isMobileReadOnly(transaction)) {
    showDialog('Transaksi dikunci', 'Transaksi sudah direalisasikan SAP. Foto hanya dapat dilihat dan tidak dapat ditambahkan dari HP.');
    return;
  }
  state.historyPhotoDraft = { transaction, slot, photo: null };
  $('#historyPhotoTitle').textContent = `Tambahkan Foto ${slot}`;
  $('#historyPhotoInfo').textContent = `${transaction.bon || `Transaksi #${transaction.id}`} · ${transaction.deskripsi}`;
  $('#historyPhotoPreview').hidden = true;
  $('#historyPhotoPreview').removeAttribute('src');
  $('#historyPhotoSave').disabled = true;
  $('#historyPhotoDialog').showModal();
};

const closeHistoryPhotoDialog = () => {
  state.historyPhotoDraft = { transaction: null, slot: 2, photo: null };
  $('#historyPhotoDialog').close();
};

const bindHistoryPhoto = () => {
  const handler = async (event) => {
    const file = event.target.files?.[0];
    if (!file) return;
    try {
      const photo = await compressPhoto(file);
      state.historyPhotoDraft.photo = photo;
      $('#historyPhotoPreview').src = photo.preview;
      $('#historyPhotoPreview').hidden = false;
      $('#historyPhotoSave').disabled = false;
    } catch (error) { showDialog('Foto gagal dibaca', error.message); }
    event.target.value = '';
  };
  $('#historyPhotoCamera').addEventListener('change', handler);
  $('#historyPhotoGallery').addEventListener('change', handler);
};

const saveHistoryPhotoDraft = async () => {
  const { transaction, slot, photo } = state.historyPhotoDraft;
  if (!transaction || !photo) throw new Error('Ambil atau pilih foto terlebih dahulu');
  if (isMobileReadOnly(transaction)) throw new Error('Transaksi sudah SAP dan tidak dapat diberi foto tambahan');
  const clientUuid = crypto.randomUUID();
  await putQueue({
    kind: 'transaction_photo', client_uuid: clientUuid, transaction_id: transaction.id,
    bon: transaction.bon || `#${transaction.id}`,
    owner_user_id: state.bootstrap.user.id, status: 'pending', created_at: new Date().toISOString(),
    payload: { client_uuid: clientUuid, slot, photo_time: photo.captured_at },
    summary: [{
      material: transaction.material, description: `Foto ${slot} · ${transaction.deskripsi}`,
      qty: transaction.qty, unit: transaction.satuan,
    }],
    photo: photo.blob, error: '', server_result: null,
  });
  state.queue = await listQueue();
  closeHistoryPhotoDialog();
  await renderHome(); renderHistory(); renderQueue();
  toast(`Foto ${slot} tersimpan di antrean HP`);
  go('queue');
};

const openCancelDialog = (transaction) => {
  state.cancelDraft = transaction;
  $('#cancelDialogTitle').textContent = transaction.deskripsi;
  const stockAddition = ['masuk', 'transfer_masuk'].includes(transaction.jenis);
  $('#cancelDialogInfo').textContent = `${transaction.bon || `Transaksi #${transaction.id}`} · ${transactionTypeLabel(transaction.jenis)} ${transaction.qty} ${transaction.satuan} · ${transaction.penerima || transaction.nomor_dokumen || '-'}`;
  $('#cancelStockNotice').innerHTML = stockAddition
    ? '<b>Stok penerimaan akan dikurangi kembali.</b><br>Jika sudah direalisasikan di SAP, dokumen SAP juga harus dibatalkan atau di-reverse.'
    : '<b>Stok barang keluar akan dikembalikan.</b><br>Jika sudah direalisasikan di SAP, dokumen SAP juga harus dibatalkan atau di-reverse.';
  $('#cancelReason').value = '';
  $('#cancelDialog').showModal();
  setTimeout(() => $('#cancelReason').focus(), 50);
};

const closeCancelDialog = () => {
  state.cancelDraft = null;
  $('#cancelDialog').close();
};

const submitCancellation = async () => {
  const transaction = state.cancelDraft;
  const reason = $('#cancelReason').value.trim();
  if (!transaction) throw new Error('Transaksi tidak ditemukan');
  if (!reason) throw new Error('Alasan pembatalan wajib diisi');
  const button = $('#cancelConfirm'); button.disabled = true; button.textContent = 'Membatalkan…';
  try {
    const result = await cancelTransaction(transaction.id, reason);
    const cached = state.bootstrap?.recent_transactions?.find((row) => row.id === transaction.id);
    if (cached) { cached.batal = 1; cached.batal_alasan = reason; }
    const material = state.bootstrap?.materials?.find((row) => row.id === result.material_id);
    if (material && Number.isFinite(Number(result.stock))) material.stok = Number(result.stock);
    await setCache('bootstrap', state.bootstrap);
    closeCancelDialog(); renderHistory(); await renderHome();
    showDialog('Transaksi dibatalkan', result.sap_warning
      ? 'Stok server sudah disesuaikan. Transaksi ini sudah masuk SAP, jadi dokumen SAP juga wajib dibatalkan atau di-reverse.'
      : 'Stok server sudah disesuaikan dan riwayat ditandai BATAL.');
  } finally {
    button.disabled = false; button.textContent = 'Ya, batalkan transaksi';
  }
};

const openCancelReturnDialog = (transaction) => {
  state.cancelReturnDraft = transaction;
  $('#cancelReturnTitle').textContent = transaction.deskripsi;
  $('#cancelReturnInfo').textContent = `${transaction.bon || `Transaksi #${transaction.id}`} · ${transaction.return_qty} ${transaction.satuan} · ${transaction.return_condition || '-'}`;
  $('#cancelReturnReason').value = '';
  $('#cancelReturnDialog').showModal();
  setTimeout(() => $('#cancelReturnReason').focus(), 50);
};

const closeCancelReturnDialog = () => {
  state.cancelReturnDraft = null;
  $('#cancelReturnDialog').close();
};

const submitReturnCancellation = async () => {
  const transaction = state.cancelReturnDraft;
  const reason = $('#cancelReturnReason').value.trim();
  if (!transaction?.return_id) throw new Error('Catatan pengembalian tidak ditemukan');
  if (!reason) throw new Error('Alasan pembatalan wajib diisi');
  const button = $('#cancelReturnConfirm'); button.disabled = true; button.textContent = 'Membatalkan…';
  try {
    const result = await cancelReturn(transaction.return_id, reason);
    const cached = state.bootstrap?.recent_transactions?.find((row) => row.id === transaction.id);
    if (cached) {
      cached.has_return = 0; cached.return_id = null; cached.return_qty = null;
      cached.return_condition = null; cached.return_date = null; cached.return_by = null;
    }
    if (result.pending_again && !state.bootstrap.pending_returns.some((row) => row.transaction_id === transaction.id)) {
      state.bootstrap.pending_returns.push({
        transaction_id: transaction.id, bon: transaction.bon, tanggal: transaction.tanggal,
        qty_out: transaction.qty, penerima: transaction.penerima, material_id: transaction.material_id,
        material: transaction.material, deskripsi: transaction.deskripsi, satuan: transaction.satuan,
        plant: transaction.plant,
      });
    }
    await setCache('bootstrap', state.bootstrap);
    closeCancelReturnDialog(); renderHistory(); renderPendingReturns(); await renderHome();
    showDialog('Catatan pengembalian dibatalkan', 'Barang kembali masuk ke daftar “belum kembali”. Saldo stok gudang tidak berubah.');
  } finally {
    button.disabled = false; button.textContent = 'Ya, batalkan catatan pengembalian';
  }
};

const openEditTransactionDialog = (transaction) => {
  state.editDraft = transaction;
  $('#editTransactionTitle').textContent = transaction.bon || `Transaksi #${transaction.id}`;
  $('#editRecipientLabel').textContent = ['masuk', 'transfer_masuk'].includes(transaction.jenis)
    ? 'Pengirim / pemasok (opsional)' : 'Nama penerima';
  $('#editRecipient').value = transaction.penerima || '';
  $('#editNote').value = transaction.keterangan || '';
  $('#editDocument').value = transaction.nomor_dokumen || '';
  $('#editWholeBon').checked = Boolean(transaction.bon);
  $('#editWholeBon').disabled = !transaction.bon;
  $('#editTransactionDialog').showModal();
};

const closeEditTransactionDialog = () => {
  state.editDraft = null;
  $('#editTransactionDialog').close();
};

const submitTransactionEdit = async () => {
  const transaction = state.editDraft;
  const recipient = $('#editRecipient').value.trim();
  if (!transaction) throw new Error('Transaksi tidak ditemukan');
  if (isStockOutflowType(transaction.jenis) && !recipient) throw new Error('Nama penerima wajib diisi');
  const button = $('#editTransactionSave'); button.disabled = true; button.textContent = 'Menyimpan…';
  try {
    const result = await editTransaction(transaction.id, {
      recipient, note: $('#editNote').value.trim(), document: $('#editDocument').value.trim(),
      all_bon: $('#editWholeBon').checked,
    });
    const ids = new Set(result.transaction_ids);
    state.bootstrap.recent_transactions.forEach((row) => {
      if (!ids.has(row.id)) return;
      row.penerima = result.recipient; row.keterangan = result.note; row.nomor_dokumen = result.document;
    });
    await setCache('bootstrap', state.bootstrap);
    closeEditTransactionDialog(); renderHistory();
    toast('Detail transaksi diperbarui');
  } finally {
    button.disabled = false; button.textContent = 'Simpan perubahan';
  }
};

const openPostingDialog = (transaction, currentlyPosted) => {
  state.postingDraft = { transaction, currentlyPosted };
  $('#postingDialogTitle').textContent = currentlyPosted ? 'Batalkan status posting SAP' : 'Tandai sudah posting SAP';
  $('#postingDialogInfo').textContent = `${transaction.bon || `Transaksi #${transaction.id}`} · seluruh item aktif dalam BON`;
  $('#postingDocument').value = transaction.sap_doc || '';
  $('#postingDocument').disabled = currentlyPosted;
  const button = $('#postingConfirm');
  button.textContent = currentlyPosted ? 'Ya, tandai belum posting SAP' : 'Tandai sudah posting SAP';
  button.className = `btn ${currentlyPosted ? 'danger' : 'primary'} block`;
  $('#postingDialog').showModal();
};

const closePostingDialog = () => {
  state.postingDraft = null;
  $('#postingDialog').close();
};

const submitPostingStatus = async () => {
  const draft = state.postingDraft;
  if (!draft) throw new Error('Transaksi tidak ditemukan');
  const posted = !draft.currentlyPosted;
  const button = $('#postingConfirm'); button.disabled = true;
  try {
    const result = await setTransactionPosting(draft.transaction.id, posted, $('#postingDocument').value.trim());
    const ids = new Set(result.transaction_ids);
    state.bootstrap.recent_transactions.forEach((row) => {
      if (!ids.has(row.id)) return;
      row.sap_posted = result.posted ? 1 : 0;
      if (result.posted && result.document) row.sap_doc = result.document;
    });
    await setCache('bootstrap', state.bootstrap);
    closePostingDialog(); renderHistory();
    toast(result.posted ? 'Seluruh BON ditandai sudah posting SAP' : 'Seluruh BON ditandai belum posting SAP');
  } finally { button.disabled = false; }
};

const openCorrectionDialog = (transaction) => {
  state.correctionDraft = transaction;
  $('#correctionTitle').textContent = transaction.deskripsi;
  $('#correctionInfo').textContent = `${transaction.bon || `Transaksi #${transaction.id}`} · qty saat ini ${transaction.qty} ${transaction.satuan}`;
  $('#correctionQty').value = transaction.qty;
  $('#correctionReason').value = '';
  $('#correctionDialog').showModal();
};

const closeCorrectionDialog = () => {
  state.correctionDraft = null;
  $('#correctionDialog').close();
};

const submitCorrection = async () => {
  const transaction = state.correctionDraft;
  const quantity = numberValue($('#correctionQty').value);
  const reason = $('#correctionReason').value.trim();
  if (!transaction) throw new Error('Transaksi tidak ditemukan');
  if (!(quantity > 0)) throw new Error('Qty koreksi harus lebih dari 0');
  if (!reason) throw new Error('Alasan koreksi wajib diisi');
  const button = $('#correctionSave'); button.disabled = true; button.textContent = 'Mengoreksi…';
  try {
    const result = await correctTransaction(transaction.id, quantity, reason);
    const cached = state.bootstrap.recent_transactions.find((row) => row.id === transaction.id);
    if (cached) cached.qty = result.quantity;
    const material = state.bootstrap.materials.find((row) => row.id === result.material_id);
    if (material) material.stok = result.stock;
    await setCache('bootstrap', state.bootstrap);
    closeCorrectionDialog(); renderHistory(); await renderHome();
    showDialog('Qty berhasil dikoreksi', result.sap_warning
      ? 'Saldo server sudah diperbarui. Transaksi ini sudah posting SAP, sehingga qty pada SAP juga wajib dikoreksi.'
      : 'Qty transaksi dan saldo stok server sudah diperbarui.');
  } finally {
    button.disabled = false; button.textContent = 'Simpan koreksi qty';
  }
};

const renderLaterReturnPhotoRule = () => {
  const condition = $('#laterReturnCondition').value;
  const required = Boolean(condition && !NO_PHYSICAL_RETURN.has(condition));
  $('#laterReturnPhotoRequired').hidden = !required;
  $('#laterReturnPhotoHint').textContent = required
    ? 'Foto wajib karena barang lama benar-benar diserahkan.'
    : 'Foto tidak wajib untuk kondisi “Hilang” atau “Tidak ada — pekerja baru”.';
};

const openReturnDialog = (row) => {
  state.returnDraft = { transaction: row, photo: null };
  $('#returnDialogTitle').textContent = row.deskripsi;
  $('#returnDialogInfo').textContent = `${row.bon || `Transaksi #${row.transaction_id}`} · keluar ${row.qty_out} ${row.satuan} · ${row.penerima || '-'}`;
  $('#laterReturnQty').value = row.qty_out;
  $('#laterReturnCondition').value = '';
  $('#laterReturnedBy').value = row.penerima || '';
  $('#laterReturnDate').value = localDate();
  $('#laterReturnNote').value = '';
  $('#laterReturnPhotoPreview').hidden = true;
  $('#laterReturnPhotoPreview').removeAttribute('src');
  renderLaterReturnPhotoRule();
  $('#returnDialog').showModal();
};

const closeReturnDialog = () => {
  state.returnDraft = { transaction: null, photo: null };
  $('#returnDialog').close();
};

const bindLaterReturnPhoto = () => {
  const handler = async (event) => {
    const file = event.target.files?.[0];
    if (!file) return;
    try {
      const photo = await compressPhoto(file);
      state.returnDraft.photo = photo;
      $('#laterReturnPhotoPreview').src = photo.preview;
      $('#laterReturnPhotoPreview').hidden = false;
      toast('Foto barang lama siap disimpan offline');
    } catch (error) { showDialog('Foto gagal dibaca', error.message); }
    event.target.value = '';
  };
  $('#laterReturnPhotoCamera').addEventListener('change', handler);
  $('#laterReturnPhotoGallery').addEventListener('change', handler);
};

const saveLaterReturnDraft = async () => {
  const row = state.returnDraft.transaction;
  if (!row) throw new Error('Transaksi pengembalian tidak ditemukan');
  const condition = $('#laterReturnCondition').value;
  const qty = numberValue($('#laterReturnQty').value);
  if (!condition) throw new Error('Pilih kondisi barang lama');
  if (!NO_PHYSICAL_RETURN.has(condition)) {
    if (!(qty > 0)) throw new Error('Isi qty barang lama yang dikembalikan');
    if (qty > Number(row.qty_out)) throw new Error('Qty barang lama melebihi qty barang keluar');
    if (!state.returnDraft.photo) throw new Error('Foto barang lama wajib diambil');
  }
  const date = $('#laterReturnDate').value;
  if (!date || date > localDate()) throw new Error('Tanggal pengembalian tidak valid');
  const clientUuid = crypto.randomUUID();
  const entry = {
    kind: 'return', client_uuid: clientUuid, transaction_id: row.transaction_id,
    owner_user_id: state.bootstrap.user.id, status: 'pending', created_at: new Date().toISOString(),
    payload: {
      client_uuid: clientUuid, date, qty: qty > 0 ? qty : 0, condition,
      returned_by: $('#laterReturnedBy').value.trim(), note: $('#laterReturnNote').value.trim(),
      photo_time: state.returnDraft.photo?.captured_at || '',
    },
    summary: [{
      material: row.material, description: row.deskripsi, qty: row.qty_out, unit: row.satuan,
      return_qty: qty > 0 ? qty : 0, return_condition: condition,
    }],
    photo: state.returnDraft.photo?.blob || null, error: '', server_result: null,
  };
  await putQueue(entry);
  state.queue = await listQueue();
  closeReturnDialog();
  await renderHome(); renderPendingReturns(); renderQueue();
  toast('Pengembalian tersimpan di antrean HP');
  go('queue');
};

const validateDraft = () => {
  if (!todayReady()) throw new Error('Lakukan Sinkron pagi terlebih dahulu');
  if (updateMandatory()) throw new Error('Perbarui aplikasi ke versi minimum yang ditentukan Admin');
  if (!state.items.length) throw new Error('Tambahkan minimal satu barang');
  const totalByMaterial = new Map();
  const targetKeys = new Set();
  for (const item of state.items) {
    const qty = numberValue(item.qty);
    if (!(qty > 0)) throw new Error(`${item.material.deskripsi}: isi qty yang benar`);
    if (isStockOutflow() && qty > Number(item.material.stok)) throw new Error(`${item.material.deskripsi}: qty melebihi stok hasil sinkron`);
    if (isOutgoing() && !item.target) throw new Error(`${item.material.deskripsi}: pilih tujuan barang`);
    const targetKey = `${item.material.id}|${item.target?.type || ''}|${item.target?.code || ''}|${item.target?.activity || ''}`;
    if (targetKeys.has(targetKey)) throw new Error(`${item.material.deskripsi}: tujuan yang sama tercantum dua kali`);
    targetKeys.add(targetKey);
    if (isOutgoing() && requiresUnitOrWorkshop(item.material) && !['unit', 'workshop'].includes(item.target.type)) {
      throw new Error(`${item.material.deskripsi}: FAME, BBM, dan sparepart wajib ke Unit atau Workshop`);
    }
    if (isOutgoing() && item.target.type === 'damkar' && !['330', '331'].includes(item.target.activity)) {
      throw new Error(`${item.material.deskripsi}: pilih aktivitas Damkar 330 atau 331`);
    }
    if (isOutgoing() && item.material.wajib_kembali && !item.return_condition) {
      throw new Error(`${item.material.deskripsi}: wajib tukar — isi kondisi barang lama`);
    }
    if (isOutgoing() && item.return_enabled && item.return_condition && !NO_PHYSICAL_RETURN.has(item.return_condition)) {
      const returnQty = numberValue(item.return_qty);
      if (!(returnQty > 0)) throw new Error(`${item.material.deskripsi}: isi qty barang lama`);
      if (returnQty > qty) throw new Error(`${item.material.deskripsi}: qty barang lama melebihi qty keluar`);
    }
    totalByMaterial.set(item.material.id, (totalByMaterial.get(item.material.id) || 0) + qty);
  }
  if (isStockOutflow()) {
    for (const [materialId, total] of totalByMaterial) {
      const material = state.items.find((item) => item.material.id === materialId).material;
      if (total > Number(material.stok)) throw new Error(`${material.deskripsi}: total pembagian melebihi stok hasil sinkron`);
    }
  }
  if (isStockOutflow() && !$('#recipient').value.trim()) throw new Error('Nama penerima wajib diisi');
  if (isStockOutflow() && !state.photos.photo) throw new Error('Foto orang/penerima wajib diambil');
  if (isTransfer() && !$('#transferGarden').value) {
    throw new Error(`Pilih kebun ${state.transactionType === 'transfer_masuk' ? 'asal' : 'tujuan'} transfer`);
  }
  if (isTransfer() && !$('#transactionDocument').value.trim()) throw new Error('Nomor dokumen transfer wajib diisi');
  if (!$('#transactionDate').value) throw new Error('Tanggal transaksi wajib diisi');
  if ($('#transactionDate').value > localDate()) throw new Error('Tanggal transaksi tidak boleh di masa depan');
  if (isOutgoing() && physicalReturnPresent() && !state.photos.return_photo) throw new Error('Foto barang lama wajib diambil');
};

const resetDraft = () => {
  state.items = [];
  state.defaultTarget = null;
  state.editingItemIndex = null;
  state.photos = { photo: null, photo_2: null, return_photo: null };
  $('#transactionForm').reset();
  $('#transactionDate').value = localDate();
  $('#targetResults').replaceChildren();
  $('#targetResults').hidden = true;
  renderDefaultTargetConfirmation();
  ['#photoPreview', '#photo2Preview', '#returnPhotoPreview'].forEach((selector) => { $(selector).hidden = true; $(selector).removeAttribute('src'); });
  buildTargets(); renderTransferGardens(); renderItems();
};

const saveDraft = async () => {
  validateDraft();
  const clientUuid = crypto.randomUUID();
  const outgoing = isOutgoing();
  const stockOutflow = isStockOutflow();
  const payload = {
    client_uuid: clientUuid,
    type: state.transactionType,
    date: $('#transactionDate').value,
    recipient: stockOutflow ? $('#recipient').value.trim() : '',
    document: outgoing ? '' : $('#transactionDocument').value.trim(),
    counterparty_plant: isTransfer() ? $('#transferGarden').value : '',
    note: $('#note').value.trim(),
    items: state.items.map((item) => ({
      material_id: item.material.id,
      qty: numberValue(item.qty),
      target: outgoing ? { type: item.target.type, code: item.target.code, activity: item.target.activity || '' } : null,
      return_qty: outgoing && item.return_enabled ? (item.return_qty || '') : '',
      return_condition: outgoing && item.return_enabled ? (item.return_condition || '') : '',
    })),
    photo_time: state.photos.photo?.captured_at || '',
    photo_2_time: state.photos.photo_2?.captured_at || '',
    return_photo_time: state.photos.return_photo?.captured_at || '',
  };
  const entry = {
    client_uuid: clientUuid,
    owner_user_id: state.bootstrap.user.id,
    status: 'pending',
    created_at: new Date().toISOString(),
    payload,
    summary: state.items.map((item, index) => ({
      material: item.material.material, description: item.material.deskripsi,
      qty: payload.items[index].qty, unit: item.material.satuan,
      return_qty: payload.items[index].return_qty,
      return_condition: payload.items[index].return_condition,
    })),
    photo: state.photos.photo?.blob || null,
    photo_2: state.photos.photo_2?.blob || null,
    return_photo: state.photos.return_photo?.blob || null,
    error: '', matches: [], server_result: null,
  };
  await putQueue(entry);
  state.queue = await listQueue();
  resetDraft();
  await renderHome();
  toast('Transaksi tersimpan aman di antrean HP');
  go('queue');
};

const statusLabel = (entry) => ({
  pending: 'Menunggu upload', uploading: 'Mengunggah…', error: 'Gagal — coba lagi',
  duplicate: 'Perlu konfirmasi',
  sent: entry.kind === 'return' ? 'Pengembalian terkirim'
    : entry.kind === 'transaction_photo' ? 'Foto terkirim'
      : entry.kind === 'opname_count' ? 'Hitungan terkirim'
      : `Terkirim · ${entry.server_result?.bon || ''}`,
}[entry.status] || entry.status);

const renderQueue = async () => {
  state.queue = await listQueue();
  const list = $('#queueList'); list.replaceChildren();
  const active = pendingQueue();
  $('#queueCountBadge').textContent = `${active.length} antrean`;
  $('#pendingCount').textContent = active.length;
  $('#queueEmpty').hidden = state.queue.length > 0;
  $('#uploadAllButton').disabled = !active.some((entry) => ['pending', 'error'].includes(entry.status));
  state.queue.forEach((entry) => {
    const card = document.createElement('div'); card.className = `queue-card ${entry.status}`;
    const head = document.createElement('div'); head.className = 'queue-head';
    const title = document.createElement('div');
    const recipient = document.createElement('b');
    recipient.textContent = entry.kind === 'return' ? '↩ Pengembalian barang lama'
      : entry.kind === 'transaction_photo' ? `📷 Foto ${entry.payload.slot} transaksi`
        : entry.kind === 'opname_count' ? `▤ Stock Opname · sesi #${entry.session_id}`
        : entry.payload.type === 'masuk'
          ? `↓ Barang masuk${entry.payload.document ? ` · ${entry.payload.document}` : ''}`
          : entry.payload.type === 'transfer_masuk'
            ? `⇠ Transfer masuk · ${entry.payload.counterparty_plant} · ${entry.payload.document}`
            : entry.payload.type === 'transfer_keluar'
              ? `⇢ Transfer keluar · ${entry.payload.counterparty_plant} · ${entry.payload.recipient}`
              : `↑ Barang keluar · ${entry.payload.recipient}`;
    const date = document.createElement('small'); date.textContent = `${entry.payload.date} · ${new Date(entry.created_at).toLocaleTimeString('id-ID', { hour: '2-digit', minute: '2-digit' })}`;
    title.append(recipient, date);
    const badge = document.createElement('span'); badge.className = `status-pill ${entry.status === 'sent' ? 'ok' : entry.status === 'duplicate' || entry.status === 'error' ? 'warn' : 'neutral'}`; badge.textContent = statusLabel(entry);
    head.append(title, badge); card.append(head);
    const items = document.createElement('ul'); items.className = 'queue-items';
    entry.summary.forEach((row) => {
      const li = document.createElement('li');
      li.textContent = `${row.description} · ${row.qty} ${row.unit}`;
      if (row.return_condition) {
        const oldItem = document.createElement('small'); oldItem.className = 'queue-return';
        oldItem.textContent = `↩ Barang lama: ${row.return_qty || 0} · ${row.return_condition}`;
        li.append(oldItem);
      }
      items.append(li);
    });
    card.append(items);
    if (entry.error) { const error = document.createElement('p'); error.className = 'notice warn'; error.textContent = entry.error; card.append(error); }
    if (entry.status !== 'sent') {
      const actions = document.createElement('div'); actions.className = 'queue-actions';
      const send = document.createElement('button'); send.className = 'btn primary'; send.type = 'button'; send.textContent = entry.status === 'duplicate' ? 'Ya, tetap upload' : 'Upload';
      send.addEventListener('click', () => uploadEntry(entry.client_uuid, entry.status === 'duplicate'));
      const remove = document.createElement('button'); remove.className = 'btn danger'; remove.type = 'button'; remove.textContent = 'Hapus antrean';
      remove.addEventListener('click', async () => {
        if (!confirm('Hapus transaksi yang belum diterima server dari HP?')) return;
        await deleteQueue(entry.client_uuid); await renderQueue(); await renderHome();
      });
      actions.append(send, remove); card.append(actions);
    }
    list.append(card);
  });
};

const uploadEntry = async (clientUuid, confirmDuplicate = false) => {
  const entry = await getQueue(clientUuid);
  if (!entry || entry.status === 'sent') return;
  if (state.bootstrap?.user?.id !== entry.owner_user_id) {
    showDialog('Akun berbeda', 'Transaksi ini dibuat oleh akun lain. Login dengan akun pembuatnya untuk mengunggah.'); return;
  }
  entry.status = 'uploading'; entry.error = ''; await putQueue(entry); await renderQueue();
  try {
    if (confirmDuplicate) entry.payload.confirm_duplicate = true;
    const result = entry.kind === 'return' ? await uploadReturn(entry)
      : entry.kind === 'transaction_photo' ? await uploadTransactionPhoto(entry)
        : entry.kind === 'opname_count' ? await uploadOpnameCount(entry)
        : await uploadTransaction(entry);
    entry.status = 'sent'; entry.server_result = result; entry.error = '';
    entry.photo = null; entry.photo_2 = null; entry.return_photo = null;
    if (entry.kind === 'return' && state.bootstrap?.pending_returns) {
      state.bootstrap.pending_returns = state.bootstrap.pending_returns.filter(
        (row) => row.transaction_id !== entry.transaction_id,
      );
      await setCache('bootstrap', state.bootstrap);
    }
    if (entry.kind === 'transaction_photo' && state.bootstrap?.recent_transactions) {
      const field = Number(entry.payload.slot) === 2 ? 'has_photo_2' : 'has_photo';
      state.bootstrap.recent_transactions.forEach((row) => {
        if ((row.bon || `#${row.id}`) === entry.bon) row[field] = 1;
      });
      await setCache('bootstrap', state.bootstrap);
    }
    if (entry.kind === 'opname_count' && result.session && state.bootstrap?.opname) {
      state.bootstrap.opname.sessions = [
        ...state.bootstrap.opname.sessions.filter((session) => session.id !== result.session.id),
        result.session,
      ];
      await setCache('bootstrap', state.bootstrap);
    }
    if (!entry.kind && result.stocks && state.bootstrap?.materials) {
      Object.entries(result.stocks).forEach(([materialId, stock]) => {
        const material = state.bootstrap.materials.find((row) => Number(row.id) === Number(materialId));
        if (material) material.stok = Number(stock);
      });
      await setCache('bootstrap', state.bootstrap);
    }
    await putQueue(entry);
    toast(entry.kind === 'return' ? 'Pengembalian diterima server'
      : entry.kind === 'transaction_photo' ? `Foto ${entry.payload.slot} diterima server`
        : entry.kind === 'opname_count' ? 'Hitungan opname diterima server'
        : `${result.bon} diterima server`);
  } catch (error) {
    entry.status = error.body?.duplicate ? 'duplicate' : 'error';
    entry.error = error.message; entry.matches = error.body?.matches || [];
    if (error.status === 401) await clearAuth();
    await putQueue(entry);
  }
  state.queue = await listQueue(); await renderQueue(); renderOpname(); await renderHome(); await renderConnection(true);
};

const uploadAll = async () => {
  const rows = (await listQueue()).filter((entry) => ['pending', 'error'].includes(entry.status));
  for (const entry of rows) await uploadEntry(entry.client_uuid, false);
};

const syncMorning = async () => {
  const button = $('#syncButton'); button.disabled = true; button.textContent = 'Menyinkronkan…';
  try {
    const data = await fetchBootstrap(APP_VERSION);
    state.bootstrap = data;
    await setCache('bootstrap', data);
    await setSetting('user', data.user);
    state.user = data.user;
    buildTargets(); renderTransactionType(); renderStock(); renderOpname(); await renderHome();
    toast('Sinkron pagi berhasil. Aplikasi siap dipakai offline.');
    checkForUpdates(false);
  } catch (error) {
    if (error.status === 401) { await clearAuth(); await renderHome(); }
    showDialog('Sinkron gagal', error.message);
  } finally { button.disabled = false; await renderHome(); await renderConnection(true); }
};

const doLogin = async (event) => {
  event.preventDefault();
  const url = normalizeServerUrl($('#serverUrl').value);
  if (!url) { showDialog('Alamat tidak valid', 'Gunakan alamat seperti http://100.x.x.x:8000'); return; }
  const deviceName = $('#deviceName').value.trim();
  await setSetting('server_url', url); await setSetting('device_name', deviceName);
  try {
    await health();
    const result = await login({
      username: $('#username').value.trim(), password: $('#password').value,
      deviceUuid: await getOrCreateDeviceId(), deviceName, appVersion: APP_VERSION,
    });
    await setSetting('token', result.token); await setSetting('user', result.user);
    $('#password').value = ''; await renderHome(); await syncMorning();
  } catch (error) { showDialog('Tidak dapat login', error.message); await renderConnection(true); }
};

const checkForUpdates = async (showResult = true) => {
  const repo = state.bootstrap?.update?.github_repo;
  if (!repo) {
    $('#updateCard').hidden = true;
    if (showResult) showDialog('Belum diatur', 'Admin belum memasukkan link repositori GitHub pada panel Aplikasi Mobile.');
    return;
  }
  try {
    state.update = await checkUpdate(repo, APP_VERSION);
    const minimum = state.bootstrap?.update?.minimum_version;
    const mandatory = minimum && compareVersions(minimum, APP_VERSION) === 1;
    $('#updateCard').hidden = !state.update.available && !mandatory;
    $('#updateTitle').textContent = mandatory ? `Pembaruan wajib ${state.update.version}` : `Versi ${state.update.version} tersedia`;
    $('#updateNotes').textContent = state.update.notes || 'Pembaruan berisi perbaikan dan peningkatan aplikasi.';
    $('#updateSettingsInfo').textContent = `${repo} · versi terpasang ${APP_VERSION} · terbaru ${state.update.version}`;
    if (showResult && !state.update.available) showDialog('Aplikasi sudah terbaru', `Versi terpasang: ${APP_VERSION}`);
  } catch (error) { if (showResult) showDialog('Pemeriksaan gagal', error.message); }
};

const startUpdate = async () => {
  if (!state.update?.available) return;
  try {
    await downloadUpdate(state.update);
    showDialog('Unduhan dimulai', 'APK diunduh oleh Android. Setelah selesai, buka notifikasi/daftar Unduhan lalu ketuk berkas untuk memasang.');
  } catch (error) { showDialog('Unduhan gagal', error.message || 'Tidak dapat memulai unduhan APK'); }
};

const bindEvents = () => {
  $$('.bottom-nav button').forEach((button) => button.addEventListener('click', () => go(button.dataset.screen)));
  $$('[data-go]').forEach((button) => button.addEventListener('click', () => go(button.dataset.go)));
  $('#settingsShortcut').addEventListener('click', () => go('settings'));
  $$('[data-transaction-type]').forEach((button) => {
    button.addEventListener('click', () => setTransactionType(button.dataset.transactionType));
  });
  $$('[data-transfer-direction]').forEach((button) => {
    button.addEventListener('click', () => setTransactionType(button.dataset.transferDirection));
  });
  $('#loginForm').addEventListener('submit', doLogin);
  $('#syncButton').addEventListener('click', syncMorning);
  $('#materialSearch').addEventListener('input', renderMaterialResults);
  $('#historySearch').addEventListener('input', renderHistory);
  $('#historyType').addEventListener('change', renderHistory);
  $('#historyStatus').addEventListener('change', renderHistory);
  $('#stockSearch').addEventListener('input', renderStock);
  $('#stockStatus').addEventListener('change', renderStock);
  $('#stockSort').addEventListener('change', renderStock);
  $('#opnameSearch').addEventListener('input', () => {
    const session = activeOpnameSession(); if (session) renderOpnameMaterials(session);
  });
  $('#opnameUncountedOnly').addEventListener('change', () => {
    const session = activeOpnameSession(); if (session) renderOpnameMaterials(session);
  });
  $('#opnameSessionPicker').addEventListener('change', () => {
    state.opnameSessionId = Number($('#opnameSessionPicker').value); renderOpname();
  });
  $('#opnameStartForm').addEventListener('submit', async (event) => {
    event.preventDefault();
    try { await submitOpnameStart(); } catch (error) { showDialog('Sesi belum dapat dibuka', error.message); }
  });
  $('#openOpnameClose').addEventListener('click', openOpnameCloseDialog);
  $('#opnameCloseDialogButton').addEventListener('click', () => $('#opnameCloseDialog').close());
  $('#opnameCloseDialog').addEventListener('cancel', (event) => {
    event.preventDefault(); $('#opnameCloseDialog').close();
  });
  $('#opnameConfirmAll').addEventListener('change', () => {
    $('#opnameCloseConfirm').disabled = !$('#opnameConfirmAll').checked;
  });
  $('#opnameCloseConfirm').addEventListener('click', async () => {
    const button = $('#opnameCloseConfirm'); button.disabled = true;
    try { await submitOpnameClose(); } catch (error) { showDialog('Sesi belum dapat ditutup', error.message); }
    finally { button.disabled = !$('#opnameConfirmAll').checked; }
  });
  $('#clearBonSelection').addEventListener('click', () => { state.historySelection.clear(); renderHistory(); });
  $('#mergeBonButton').addEventListener('click', async () => {
    try { await changeSelectedBon('merge'); } catch (error) { showDialog('BON tidak dapat disatukan', error.message); }
  });
  $('#splitBonButton').addEventListener('click', async () => {
    try { await changeSelectedBon('split'); } catch (error) { showDialog('BON tidak dapat dipisahkan', error.message); }
  });
  $('#targetSearch').addEventListener('input', handleDefaultTargetSearch);
  $('#targetDialogSearch').addEventListener('input', renderItemTargetSearch);
  $('#targetDialogClose').addEventListener('click', closeTargetDialog);
  $('#targetDialog').addEventListener('cancel', (event) => {
    event.preventDefault(); closeTargetDialog();
  });
  RETURN_CONDITIONS.forEach((value) => $('#laterReturnCondition').add(new Option(value, value)));
  $('#laterReturnCondition').addEventListener('change', renderLaterReturnPhotoRule);
  $('#returnDialogClose').addEventListener('click', closeReturnDialog);
  $('#returnDialog').addEventListener('cancel', (event) => {
    event.preventDefault(); closeReturnDialog();
  });
  $('#returnForm').addEventListener('submit', async (event) => {
    event.preventDefault();
    try { await saveLaterReturnDraft(); } catch (error) { showDialog('Periksa pengembalian', error.message); }
  });
  $('#historyPhotoClose').addEventListener('click', closeHistoryPhotoDialog);
  $('#historyPhotoDialog').addEventListener('cancel', (event) => {
    event.preventDefault(); closeHistoryPhotoDialog();
  });
  $('#historyPhotoSave').addEventListener('click', async () => {
    try { await saveHistoryPhotoDraft(); } catch (error) { showDialog('Foto belum dapat disimpan', error.message); }
  });
  $('#cancelDialogClose').addEventListener('click', closeCancelDialog);
  $('#cancelDialog').addEventListener('cancel', (event) => {
    event.preventDefault(); closeCancelDialog();
  });
  $('#cancelConfirm').addEventListener('click', async () => {
    try { await submitCancellation(); } catch (error) { showDialog('Pembatalan gagal', error.message); }
  });
  $('#cancelReturnClose').addEventListener('click', closeCancelReturnDialog);
  $('#cancelReturnDialog').addEventListener('cancel', (event) => {
    event.preventDefault(); closeCancelReturnDialog();
  });
  $('#cancelReturnConfirm').addEventListener('click', async () => {
    try { await submitReturnCancellation(); } catch (error) { showDialog('Pembatalan pengembalian gagal', error.message); }
  });
  $('#editTransactionClose').addEventListener('click', closeEditTransactionDialog);
  $('#editTransactionDialog').addEventListener('cancel', (event) => {
    event.preventDefault(); closeEditTransactionDialog();
  });
  $('#editTransactionSave').addEventListener('click', async () => {
    try { await submitTransactionEdit(); } catch (error) { showDialog('Perubahan gagal', error.message); }
  });
  $('#postingDialogClose').addEventListener('click', closePostingDialog);
  $('#postingDialog').addEventListener('cancel', (event) => {
    event.preventDefault(); closePostingDialog();
  });
  $('#postingConfirm').addEventListener('click', async () => {
    try { await submitPostingStatus(); } catch (error) { showDialog('Status SAP gagal diperbarui', error.message); }
  });
  $('#correctionClose').addEventListener('click', closeCorrectionDialog);
  $('#correctionDialog').addEventListener('cancel', (event) => {
    event.preventDefault(); closeCorrectionDialog();
  });
  $('#correctionSave').addEventListener('click', async () => {
    try { await submitCorrection(); } catch (error) { showDialog('Koreksi gagal', error.message); }
  });
  $('#photoViewerClose').addEventListener('click', closePhotoViewer);
  $('#photoViewerDialog').addEventListener('cancel', (event) => {
    event.preventDefault(); closePhotoViewer();
  });
  $('#transactionForm').addEventListener('submit', async (event) => {
    event.preventDefault();
    try { await saveDraft(); } catch (error) { showDialog('Periksa transaksi', error.message); }
  });
  $('#uploadAllButton').addEventListener('click', uploadAll);
  $('#testConnection').addEventListener('click', async () => {
    const connected = await renderConnection(true);
    showDialog(connected ? 'Server terhubung' : 'Server belum terhubung', connected
      ? 'Jalur Tailscale menuju laptop dapat dipakai.' : 'Pastikan Tailscale aktif, laptop menyala, dan server flowStok berjalan.');
  });
  $('#logoutButton').addEventListener('click', async () => {
    if (pendingQueue().length && !confirm('Masih ada transaksi belum terunggah. Keluar tidak menghapus antrean, tetapi hanya akun pembuat yang dapat mengunggahnya. Lanjutkan?')) return;
    await clearAuth(); state.bootstrap = null; await setCache('bootstrap', null); await renderHome(); go('home');
  });
  $('#checkUpdateButton').addEventListener('click', () => checkForUpdates(true));
  $('#updateButton').addEventListener('click', startUpdate);
  $('#dialogClose').addEventListener('click', () => $('#messageDialog').close());
  window.addEventListener('online', () => renderConnection(true));
  window.addEventListener('offline', () => renderConnection(false));
  bindPhoto('#photoCamera', '#photoGallery', '#photoPreview', 'photo');
  bindPhoto('#photo2Camera', '#photo2Gallery', '#photo2Preview', 'photo_2');
  bindPhoto('#returnPhotoCamera', '#returnPhotoGallery', '#returnPhotoPreview', 'return_photo');
  bindLaterReturnPhoto();
  bindHistoryPhoto();
  App.addListener('backButton', () => {
    const active = $('.screen.active')?.id;
    if (active && active !== 'screenHome') go('home'); else App.exitApp();
  });
};

const init = async () => {
  bindEvents();
  $('#transactionDate').value = localDate();
  $('#serverUrl').value = await getSetting('server_url', '');
  $('#deviceName').value = await getSetting('device_name', 'HP Gudang');
  state.bootstrap = await getCache('bootstrap');
  state.queue = await listQueue();
  // Bila aplikasi mati saat request berjalan, retry memakai client_uuid yang
  // sama sehingga server mengembalikan BON lama tanpa menggandakan transaksi.
  for (const entry of state.queue.filter((row) => row.status === 'uploading')) {
    entry.status = 'pending';
    entry.error = 'Upload sebelumnya terputus; aman untuk dicoba kembali.';
    await putQueue(entry);
  }
  state.queue = await listQueue();
  buildTargets(); renderTransactionType(); renderStock(); renderOpname(); await renderHome(); await renderQueue();
  renderConnection(Boolean(await getSetting('server_url')));
  if (state.bootstrap?.update?.github_repo) checkForUpdates(false);
};

init().catch((error) => showDialog('Aplikasi gagal dibuka', escapeText(error.message)));
