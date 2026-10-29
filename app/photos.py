"""Pemrosesan foto bukti transaksi.

Modul ini sengaja dipisahkan dari ``views_main`` agar aturan upload, pembacaan
metadata, watermark, dan kompresi berada di satu tempat. Route cukup memanggil
``simpan_foto`` dan tidak perlu mengetahui detail Pillow atau EXIF.
"""

import datetime as dt
import uuid
from pathlib import Path

from flask import current_app
from PIL import Image, ImageDraw, ImageFont, ImageOps, UnidentifiedImageError

from .stock import StockError


EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
MAX_IMAGE_SIDE = 1600
JPEG_QUALITY = 72


def _watermark_font(size):
    """Kembalikan font bawaan Pillow dengan fallback untuk versi lama."""
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow lama belum menerima parameter ``size``.
        return ImageFont.load_default()


def parse_photo_time(value):
    """Ubah nilai waktu EXIF/browser menjadi ``datetime`` yang tervalidasi.

    EXIF memakai pemisah titik dua pada bagian tanggal, sedangkan JavaScript
    mengirim format ISO sederhana setelah membaca metadata sebelum kompresi.
    Nilai di luar rentang wajar ditolak supaya teks bebas tidak masuk watermark.
    """
    if isinstance(value, bytes):
        value = value.decode("utf-8", "ignore")
    value = str(value or "").split("\x00", 1)[0].strip()
    for pattern in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            result = dt.datetime.strptime(value, pattern)
            return result if 1900 <= result.year <= 2100 else None
        except ValueError:
            continue
    return None


def original_photo_time(source):
    """Ambil waktu asli foto dari tag EXIF dengan urutan prioritas standar."""
    try:
        exif = source.getexif()
        if not exif:
            return None

        detailed_exif = exif
        try:
            if exif.get(34665):  # ExifIFDPointer
                detailed_exif = exif.get_ifd(34665)
        except (AttributeError, KeyError, TypeError, ValueError):
            pass

        # 36867 = DateTimeOriginal; 36868 = DateTimeDigitized.
        for tag in (36867, 36868):
            result = parse_photo_time(detailed_exif.get(tag) or exif.get(tag))
            if result:
                return result
        return parse_photo_time(exif.get(306))  # DateTime sebagai fallback terakhir.
    except (AttributeError, KeyError, TypeError, ValueError, OSError):
        return None


def choose_photo_time(source, browser_time, upload_time):
    """Pilih waktu watermark dan tandai apakah berasal dari metadata foto.

    EXIF di file asli selalu lebih dipercaya. Nilai dari browser hanya dipakai
    saat kompresi di perangkat telah membuang EXIF sebelum file diunggah.
    """
    metadata_time = original_photo_time(source) or parse_photo_time(browser_time)
    return (metadata_time, True) if metadata_time else (upload_time, False)


def _watermark_text(photo_time, from_metadata):
    """Susun teks watermark yang menjelaskan sumber waktunya secara jujur."""
    if from_metadata:
        return f"Waktu foto (metadata)\n{photo_time:%d-%m-%Y  %H:%M:%S}"
    return f"Metadata waktu tidak tersedia\nDiunggah {photo_time:%d-%m-%Y  %H:%M:%S} WIB"


def _draw_watermark(photo, text):
    """Gambar watermark dua baris yang tetap terbaca pada berbagai ukuran foto."""
    font_size = max(14, min(48, photo.width // 28))
    font = _watermark_font(font_size)
    drawing = ImageDraw.Draw(photo, "RGBA")
    box = drawing.multiline_textbbox((0, 0), text, font=font, stroke_width=1, spacing=3)
    width, height = box[2] - box[0], box[3] - box[1]
    padding = max(8, photo.width // 80)
    x = max(padding, photo.width - width - padding * 2)
    y = max(padding, photo.height - height - padding * 2)
    drawing.rounded_rectangle(
        (x - padding, y - padding, min(photo.width - 1, x + width + padding),
         min(photo.height - 1, y + height + padding)),
        radius=max(4, padding // 2), fill=(0, 0, 0, 175),
    )
    drawing.multiline_text(
        (x, y), text, font=font, fill=(255, 255, 255, 255), spacing=3,
        stroke_width=1, stroke_fill=(0, 0, 0, 220),
    )


def save_photo(file, upload_time=None, metadata_time=None):
    """Validasi, orientasikan, watermark, kompres, lalu simpan foto sebagai JPEG.

    Args:
        file: ``FileStorage`` dari formulir Flask.
        upload_time: Waktu server; dapat diinjeksi oleh pengujian.
        metadata_time: Waktu EXIF yang dibaca browser sebelum kompresi.

    Returns:
        Path relatif foto untuk disimpan di database, atau ``None`` bila kosong.

    Raises:
        StockError: Jika format tidak didukung atau isi gambar rusak.
    """
    if not file or not file.filename:
        return None

    extension = Path(file.filename).suffix.lower() or ".jpg"
    if extension not in EXTENSIONS:
        raise StockError("Format foto tidak didukung untuk watermark (gunakan JPG, PNG, atau WEBP)")

    uploaded_at = upload_time or dt.datetime.now()
    relative_name = f"{uploaded_at:%Y%m}/{uuid.uuid4().hex}.jpg"
    destination = Path(current_app.config["UPLOAD_FOLDER"]) / relative_name
    destination.parent.mkdir(parents=True, exist_ok=True)

    try:
        file.stream.seek(0)
        with Image.open(file.stream) as source:
            photo_time, from_metadata = choose_photo_time(source, metadata_time, uploaded_at)
            photo = ImageOps.exif_transpose(source)
            if getattr(photo, "is_animated", False):
                photo.seek(0)
            photo = photo.convert("RGB")
            photo.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE), Image.Resampling.LANCZOS)
            _draw_watermark(photo, _watermark_text(photo_time, from_metadata))

            # Kualitas 72 dan chroma subsampling menjaga tulisan bon terbaca,
            # tetapi umumnya menekan foto HP menjadi beberapa ratus kilobyte.
            photo.save(destination, "JPEG", quality=JPEG_QUALITY, optimize=True, progressive=True, subsampling=2)
    except (UnidentifiedImageError, OSError, ValueError) as error:
        destination.unlink(missing_ok=True)
        raise StockError("File foto rusak atau tidak dapat dibaca. Ambil ulang dalam format JPG/PNG.") from error

    return relative_name

