/** Klien API laptop server. Semua mutasi memakai token perangkat, bukan cookie. */
import { getSetting } from './db.js';

const TIMEOUT_MS = 15000;

export const normalizeServerUrl = (value) => {
  let url = String(value ?? '').trim().replace(/\/+$/, '');
  if (url && !/^https?:\/\//i.test(url)) url = `http://${url}`;
  try {
    const parsed = new URL(url);
    if (!['http:', 'https:'].includes(parsed.protocol)) return null;
    if (parsed.username || parsed.password || parsed.pathname !== '/' || parsed.search || parsed.hash) return null;
    return parsed.origin;
  } catch {
    return null;
  }
};

const withTimeout = async (url, options = {}) => {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  try {
    return await fetch(url, { ...options, signal: controller.signal });
  } catch (error) {
    if (error?.name === 'AbortError') throw new Error('Server tidak menjawab. Periksa Tailscale dan laptop server.');
    throw new Error('Tidak dapat terhubung ke server. Periksa sinyal, Tailscale, dan alamat laptop.');
  } finally {
    clearTimeout(timer);
  }
};

const server = async () => {
  const url = normalizeServerUrl(await getSetting('server_url'));
  if (!url) throw new Error('Alamat server belum diatur');
  return url;
};

const parseResponse = async (response) => {
  let body;
  try { body = await response.json(); } catch { body = null; }
  if (!response.ok) {
    const error = new Error(body?.error || `Server menolak permintaan (${response.status})`);
    error.status = response.status;
    error.body = body;
    throw error;
  }
  return body;
};

const authHeaders = async () => {
  const token = await getSetting('token');
  return token ? { Authorization: `Bearer ${token}` } : {};
};

export const health = async () => parseResponse(await withTimeout(`${await server()}/api/mobile/v1/status`));

export const login = async ({ username, password, deviceUuid, deviceName, appVersion }) => {
  const response = await withTimeout(`${await server()}/api/mobile/v1/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      username, password, device_uuid: deviceUuid, device_name: deviceName, app_version: appVersion,
    }),
  });
  return parseResponse(response);
};

export const bootstrap = async (appVersion) => {
  const response = await withTimeout(`${await server()}/api/mobile/v1/bootstrap`, {
    headers: { ...(await authHeaders()), 'X-App-Version': appVersion },
  });
  return parseResponse(response);
};

export const uploadTransaction = async (entry) => {
  const form = new FormData();
  form.set('payload', JSON.stringify(entry.payload));
  form.set('photo', entry.photo, 'foto-orang.jpg');
  if (entry.photo_2) form.set('photo_2', entry.photo_2, 'foto-bon.jpg');
  if (entry.return_photo) form.set('return_photo', entry.return_photo, 'foto-barang-lama.jpg');
  const response = await withTimeout(`${await server()}/api/mobile/v1/transactions`, {
    method: 'POST', headers: await authHeaders(), body: form,
  });
  return parseResponse(response);
};

export const uploadReturn = async (entry) => {
  const form = new FormData();
  form.set('payload', JSON.stringify(entry.payload));
  if (entry.photo) form.set('photo', entry.photo, 'foto-barang-lama.jpg');
  const response = await withTimeout(
    `${await server()}/api/mobile/v1/returns/${entry.transaction_id}`,
    { method: 'POST', headers: await authHeaders(), body: form },
  );
  return parseResponse(response);
};
