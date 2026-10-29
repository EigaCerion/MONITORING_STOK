/** Kompres foto sebelum masuk antrean agar penyimpanan HP dan upload tetap ringan. */
const MAX_SIDE = 1600;
const QUALITY = 0.76;

const loadImage = (file) => new Promise((resolve, reject) => {
  const url = URL.createObjectURL(file);
  const image = new Image();
  image.onload = () => { URL.revokeObjectURL(url); resolve(image); };
  image.onerror = () => { URL.revokeObjectURL(url); reject(new Error('Foto tidak dapat dibaca')); };
  image.src = url;
});

export const compressPhoto = async (file) => {
  if (!file) return null;
  if (!String(file.type).startsWith('image/')) throw new Error('Pilih file foto JPG, PNG, atau WEBP');
  const image = await loadImage(file);
  const scale = Math.min(1, MAX_SIDE / Math.max(image.naturalWidth, image.naturalHeight));
  const canvas = document.createElement('canvas');
  canvas.width = Math.max(1, Math.round(image.naturalWidth * scale));
  canvas.height = Math.max(1, Math.round(image.naturalHeight * scale));
  canvas.getContext('2d', { alpha: false }).drawImage(image, 0, 0, canvas.width, canvas.height);
  const blob = await new Promise((resolve) => canvas.toBlob(resolve, 'image/jpeg', QUALITY));
  if (!blob) throw new Error('Foto gagal dikompres');
  return {
    blob,
    captured_at: new Date(file.lastModified || Date.now()).toISOString().slice(0, 19),
    preview: URL.createObjectURL(blob),
  };
};
