import './styles.css';
import { App } from '@capacitor/app';
import {
  clearAuth, deleteQueue, getCache, getOrCreateDeviceId, getQueue, getSetting,
  listQueue, putQueue, setCache, setSetting,
} from './db.js';
import {
  bootstrap as fetchBootstrap, health, login, normalizeServerUrl, uploadReturn, uploadTransaction,
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
  items: [],
  targets: [],
  defaultTarget: null,
  editingItemIndex: null,
  returnDraft: { transaction: null, photo: null },
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
  $('#headerUser').textContent = state.user?.name || 'Aplikasi Gudang';
  $('#settingsServer').textContent = await getSetting('server_url', 'Belum diatur');
  $('#deviceIdText').textContent = await getOrCreateDeviceId();
  $('#appVersion').textContent = `v${APP_VERSION}`;
  $('#materialCount').textContent = state.bootstrap?.materials?.length ?? 0;
  $('#pendingCount').textContent = pendingQueue().length;
  const queuedReturns = new Set(pendingQueue().filter((entry) => entry.kind === 'return').map((entry) => entry.transaction_id));
  const pendingReturns = (state.bootstrap?.pending_returns || []).filter((row) => !queuedReturns.has(row.transaction_id));
  $('#pendingReturnCount').textContent = pendingReturns.length;
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
  const type = { blok: 'Blok', unit: 'Unit', workshop: 'Workshop', office: 'Kantor', staff: 'Staff', damkar: 'Damkar' }[target.type] || target.type;
  return `${type} · ${target.code}${target.name ? ` — ${target.name}` : ''}`;
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

const renderInputAvailability = () => {
  const locked = !todayReady() || updateMandatory();
  $('#inputLocked').hidden = !locked;
  $('#inputLocked').textContent = updateMandatory()
    ? `Versi ${APP_VERSION} sudah di bawah versi minimum ${state.bootstrap.update.minimum_version}. Perbarui aplikasi sebelum input baru.`
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
  const target = state.defaultTarget
    && (!state.defaultTarget.plant || state.defaultTarget.plant === material.plant)
    ? { ...state.defaultTarget } : null;
  if (state.items.some((item) => item.material.id === material.id
      && item.target?.type === target?.type && item.target?.code === target?.code)) {
    toast('Barang dengan tujuan yang sama sudah ada; ubah tujuan dahulu untuk membaginya'); return;
  }
  state.items.push({
    material, qty: '', target,
    return_enabled: Boolean(material.wajib_kembali), return_qty: '', return_qty_touched: false,
    return_condition: '',
  });
  $('#materialSearch').value = '';
  $('#materialResults').hidden = true;
  renderItems();
};

const splitItemToAnotherTarget = (itemIndex) => {
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
    const card = document.createElement('div'); card.className = 'item-card'; card.dataset.itemIndex = String(index);
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
      if (item.material.wajib_kembali && !state.items[index].return_qty_touched) {
        state.items[index].return_qty = qty.value;
        if (returnQtyInput) returnQtyInput.value = qty.value;
      }
      updateStockHints(); renderReturnPhoto();
    });
    qtyLabel.append(qty);
    const targetLabelNode = document.createElement('label'); targetLabelNode.textContent = 'Tujuan barang';
    targetLabelNode.append(targetButton(item, index));
    meta.append(qtyLabel, targetLabelNode);

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

    if (item.return_enabled) {
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
    const remaining = Number(item.material.stok) - total;
    const splitCount = state.items.filter((row) => row.material.id === item.material.id).length;
    const hint = card.querySelector('.item-stock-hint');
    hint.textContent = `${splitCount > 1 ? `Total dibagi ${total} · ` : ''}Sisa ${Math.round(remaining * 1000) / 1000} ${item.material.satuan}`;
    hint.classList.toggle('danger-text', remaining < 0);
  });
};

const renderReturnPhoto = () => {
  const visible = state.items.some((item) => item.return_enabled);
  const required = physicalReturnPresent();
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
    if (qty > Number(item.material.stok)) throw new Error(`${item.material.deskripsi}: qty melebihi stok hasil sinkron`);
    if (!item.target) throw new Error(`${item.material.deskripsi}: pilih tujuan barang`);
    const targetKey = `${item.material.id}|${item.target.type}|${item.target.code}|${item.target.activity || ''}`;
    if (targetKeys.has(targetKey)) throw new Error(`${item.material.deskripsi}: tujuan yang sama tercantum dua kali`);
    targetKeys.add(targetKey);
    if (requiresUnitOrWorkshop(item.material) && !['unit', 'workshop'].includes(item.target.type)) {
      throw new Error(`${item.material.deskripsi}: FAME, BBM, dan sparepart wajib ke Unit atau Workshop`);
    }
    if (item.target.type === 'damkar' && !['330', '331'].includes(item.target.activity)) {
      throw new Error(`${item.material.deskripsi}: pilih aktivitas Damkar 330 atau 331`);
    }
    if (item.material.wajib_kembali && !item.return_condition) {
      throw new Error(`${item.material.deskripsi}: wajib tukar — isi kondisi barang lama`);
    }
    if (item.return_enabled && item.return_condition && !NO_PHYSICAL_RETURN.has(item.return_condition)) {
      const returnQty = numberValue(item.return_qty);
      if (!(returnQty > 0)) throw new Error(`${item.material.deskripsi}: isi qty barang lama`);
      if (returnQty > qty) throw new Error(`${item.material.deskripsi}: qty barang lama melebihi qty keluar`);
    }
    totalByMaterial.set(item.material.id, (totalByMaterial.get(item.material.id) || 0) + qty);
  }
  for (const [materialId, total] of totalByMaterial) {
    const material = state.items.find((item) => item.material.id === materialId).material;
    if (total > Number(material.stok)) throw new Error(`${material.deskripsi}: total pembagian melebihi stok hasil sinkron`);
  }
  if (!$('#recipient').value.trim()) throw new Error('Nama penerima wajib diisi');
  if (!state.photos.photo) throw new Error('Foto orang/penerima wajib diambil');
  if (!$('#transactionDate').value) throw new Error('Tanggal transaksi wajib diisi');
  if ($('#transactionDate').value > localDate()) throw new Error('Tanggal transaksi tidak boleh di masa depan');
  if (physicalReturnPresent() && !state.photos.return_photo) throw new Error('Foto barang lama wajib diambil');
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
  buildTargets(); renderItems();
};

const saveDraft = async () => {
  validateDraft();
  const clientUuid = crypto.randomUUID();
  const payload = {
    client_uuid: clientUuid,
    type: 'keluar',
    date: $('#transactionDate').value,
    recipient: $('#recipient').value.trim(),
    note: $('#note').value.trim(),
    items: state.items.map((item) => ({
      material_id: item.material.id,
      qty: numberValue(item.qty),
      target: { type: item.target.type, code: item.target.code, activity: item.target.activity || '' },
      return_qty: item.return_enabled ? (item.return_qty || '') : '',
      return_condition: item.return_enabled ? (item.return_condition || '') : '',
    })),
    photo_time: state.photos.photo.captured_at,
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
    photo: state.photos.photo.blob,
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
  sent: entry.kind === 'return' ? 'Pengembalian terkirim' : `Terkirim · ${entry.server_result?.bon || ''}`,
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
    recipient.textContent = entry.kind === 'return' ? '↩ Pengembalian barang lama' : entry.payload.recipient;
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
    const result = entry.kind === 'return' ? await uploadReturn(entry) : await uploadTransaction(entry);
    entry.status = 'sent'; entry.server_result = result; entry.error = '';
    entry.photo = null; entry.photo_2 = null; entry.return_photo = null;
    if (entry.kind === 'return' && state.bootstrap?.pending_returns) {
      state.bootstrap.pending_returns = state.bootstrap.pending_returns.filter(
        (row) => row.transaction_id !== entry.transaction_id,
      );
      await setCache('bootstrap', state.bootstrap);
    }
    await putQueue(entry);
    toast(entry.kind === 'return' ? 'Pengembalian diterima server' : `${result.bon} diterima server`);
  } catch (error) {
    entry.status = error.body?.duplicate ? 'duplicate' : 'error';
    entry.error = error.message; entry.matches = error.body?.matches || [];
    if (error.status === 401) await clearAuth();
    await putQueue(entry);
  }
  state.queue = await listQueue(); await renderQueue(); await renderHome(); await renderConnection(true);
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
    buildTargets(); await renderHome();
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
  $('#loginForm').addEventListener('submit', doLogin);
  $('#syncButton').addEventListener('click', syncMorning);
  $('#materialSearch').addEventListener('input', renderMaterialResults);
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
  $('#transactionForm').addEventListener('submit', async (event) => {
    event.preventDefault();
    try { await saveDraft(); } catch (error) { showDialog('Periksa transaksi', error.message); }
  });
  $('#uploadAllButton').addEventListener('click', uploadAll);
  $('#testConnection').addEventListener('click', async () => {
    const connected = await renderConnection(true);
    showDialog(connected ? 'Server terhubung' : 'Server belum terhubung', connected
      ? 'Jalur Tailscale menuju laptop dapat dipakai.' : 'Pastikan Tailscale aktif, laptop menyala, dan server Monitoring Stok berjalan.');
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
  buildTargets(); renderItems(); await renderHome(); await renderQueue();
  renderConnection(Boolean(await getSetting('server_url')));
  if (state.bootstrap?.update?.github_repo) checkForUpdates(false);
};

init().catch((error) => showDialog('Aplikasi gagal dibuka', escapeText(error.message)));
