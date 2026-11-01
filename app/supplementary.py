"""Aturan bersama untuk plant dan tujuan pada Master Supplementary SAP."""
import re


# CMA1 memakai satu operasional gudang untuk blok inti CM3A dan plasma CM9A.
# Unit/VRA tetap dibatasi ke plant asal karena nomor order unit bersifat plant-specific.
BLOCK_TARGET_COMPANIONS = {
    "CM3A": ("CM3A", "CM9A"),
    "CM9A": ("CM9A", "CM3A"),
}


def normalize_plant(estate, company_code=""):
    """Ubah estate pendek export SAP (3A/9A) menjadi plant penuh (CM3A/CM9A).

    File Supplementary lama sudah menulis plant penuh di kolom Estate. File baru
    memisahkan Company Code dan Estate, sehingga keduanya harus didukung.
    """
    estate = re.sub(r"\s+", "", str(estate or "").upper())
    company_code = re.sub(r"\s+", "", str(company_code or "").upper())
    if not estate:
        return ""
    if company_code and not estate.startswith(company_code):
        return f"{company_code}{estate}"
    return estate


def target_plants(plant, target_type):
    """Plant master yang boleh dipakai untuk tujuan suatu material.

    Blok inti dan plasma CMA1 dapat dipakai bersama. Unit/VRA tetap hanya boleh
    memakai master dari plant material agar order SAP tidak tertukar.
    """
    plant = re.sub(r"\s+", "", str(plant or "").upper())
    if not plant:
        return ()
    if target_type == "blok":
        return BLOCK_TARGET_COMPANIONS.get(plant, (plant,))
    return (plant,)


def legacy_plant_aliases(plant):
    """Kembalikan alias pendek yang mungkin tersimpan akibat impor format baru lama."""
    plant = re.sub(r"\s+", "", str(plant or "").upper())
    match = re.fullmatch(r"([A-Z]{2})(\d+[A-Z])", plant)
    return (plant, match.group(2)) if match else (plant,)
