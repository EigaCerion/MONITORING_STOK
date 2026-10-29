/** Penyimpanan lokal APK. IndexedDB mempertahankan Blob foto tanpa base64. */
const DB_NAME = 'monitoring-stok-mobile';
const DB_VERSION = 1;

let connection;

const openDb = () => {
  if (connection) return connection;
  connection = new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, DB_VERSION);
    request.onupgradeneeded = () => {
      const db = request.result;
      if (!db.objectStoreNames.contains('settings')) db.createObjectStore('settings', { keyPath: 'key' });
      if (!db.objectStoreNames.contains('cache')) db.createObjectStore('cache', { keyPath: 'key' });
      if (!db.objectStoreNames.contains('queue')) {
        const queue = db.createObjectStore('queue', { keyPath: 'client_uuid' });
        queue.createIndex('created_at', 'created_at');
        queue.createIndex('status', 'status');
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
  return connection;
};

const requestResult = (request) => new Promise((resolve, reject) => {
  request.onsuccess = () => resolve(request.result);
  request.onerror = () => reject(request.error);
});

const store = async (name, mode = 'readonly') => (await openDb()).transaction(name, mode).objectStore(name);

export const getSetting = async (key, fallback = null) => {
  const row = await requestResult((await store('settings')).get(key));
  return row ? row.value : fallback;
};

export const setSetting = async (key, value) => {
  await requestResult((await store('settings', 'readwrite')).put({ key, value }));
  return value;
};

export const removeSetting = async (key) => requestResult((await store('settings', 'readwrite')).delete(key));

export const getCache = async (key, fallback = null) => {
  const row = await requestResult((await store('cache')).get(key));
  return row ? row.value : fallback;
};

export const setCache = async (key, value) => requestResult((await store('cache', 'readwrite')).put({ key, value }));

export const putQueue = async (entry) => requestResult((await store('queue', 'readwrite')).put(entry));

export const getQueue = async (clientUuid) => requestResult((await store('queue')).get(clientUuid));

export const listQueue = async () => {
  const rows = await requestResult((await store('queue')).getAll());
  return rows.sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)));
};

export const deleteQueue = async (clientUuid) => requestResult(
  (await store('queue', 'readwrite')).delete(clientUuid),
);

export const clearAuth = async () => {
  await Promise.all([removeSetting('token'), removeSetting('user')]);
};

export const getOrCreateDeviceId = async () => {
  let value = await getSetting('device_uuid');
  if (!value) {
    value = crypto.randomUUID();
    await setSetting('device_uuid', value);
  }
  return value;
};
