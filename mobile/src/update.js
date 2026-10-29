/** Pemeriksa GitHub Releases. Link repositori datang dari panel Admin saat sinkron. */
import { CapacitorHttp, registerPlugin } from '@capacitor/core';

const UpdateDownload = registerPlugin('UpdateDownload');
const VERSION_PATTERN = /^(\d{1,6})\.(\d{1,2})\.(\d{1,2})$/;

const parts = (value) => {
  const match = VERSION_PATTERN.exec(String(value ?? '').replace(/^v/, ''));
  return match ? match.slice(1).map(Number) : null;
};

export const compareVersions = (left, right) => {
  const a = parts(left); const b = parts(right);
  if (!a || !b) return null;
  for (let index = 0; index < 3; index += 1) {
    if (a[index] !== b[index]) return a[index] > b[index] ? 1 : -1;
  }
  return 0;
};

const safeRepo = (value) => {
  const repo = String(value ?? '').trim();
  return /^[A-Za-z0-9_.-]{1,100}\/[A-Za-z0-9_.-]{1,100}$/.test(repo) ? repo : null;
};

const safeAssetUrl = (url, repo) => {
  try {
    const parsed = new URL(url);
    const prefix = `/${repo}/releases/download/`.toLowerCase();
    return parsed.protocol === 'https:' && parsed.hostname === 'github.com'
      && parsed.pathname.toLowerCase().startsWith(prefix) ? parsed.toString() : null;
  } catch { return null; }
};

export const checkUpdate = async (repoValue, currentVersion) => {
  const repo = safeRepo(repoValue);
  if (!repo) return { configured: false };
  const response = await CapacitorHttp.get({
    url: `https://api.github.com/repos/${repo}/releases/latest`,
    headers: { Accept: 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28' },
    connectTimeout: 10000, readTimeout: 10000,
  });
  if (response.status < 200 || response.status >= 300) throw new Error(`GitHub menjawab ${response.status}`);
  const release = response.data;
  const candidates = [];
  for (const asset of Array.isArray(release?.assets) ? release.assets : []) {
    const match = /^MonitoringStok-(\d{1,6}\.\d{1,2}\.\d{1,2})\.apk$/i.exec(String(asset?.name ?? ''));
    const url = match ? safeAssetUrl(asset.browser_download_url, repo) : null;
    if (url) candidates.push({ version: match[1], url, name: asset.name, size: Number(asset.size) || 0 });
  }
  candidates.sort((a, b) => compareVersions(b.version, a.version) ?? 0);
  const latest = candidates[0];
  if (!latest) throw new Error('Rilis terbaru belum memiliki APK bernama MonitoringStok-x.y.z.apk');
  return {
    configured: true,
    available: compareVersions(latest.version, currentVersion) === 1,
    currentVersion,
    ...latest,
    notes: String(release?.body ?? '').trim().slice(0, 1000),
  };
};

export const downloadUpdate = async (update) => UpdateDownload.start({
  url: update.url, name: update.name, size: update.size,
});
