"""Penemuan artefak APK stabil yang boleh dibagikan melalui aplikasi web."""
import re
from pathlib import Path


APK_DIR = Path(__file__).resolve().parent.parent / "mobile" / "dist-apk"
STABLE_APK = re.compile(r"^(flowStok|MonitoringStok)-(\d{1,6})\.(\d{1,2})\.(\d{1,2})\.apk$")


def latest_stable_apk():
    """Kembalikan APK stabil dengan versi semantik tertinggi, bukan APK debug."""
    candidates = []
    if APK_DIR.exists():
        for path in APK_DIR.glob("*.apk"):
            match = STABLE_APK.fullmatch(path.name)
            if match:
                version = tuple(map(int, match.groups()[1:]))
                brand_priority = 1 if match.group(1) == "flowStok" else 0
                candidates.append((version, brand_priority, path))
    return max(candidates, default=(None, None, None), key=lambda item: (item[0], item[1]))[2]


def apk_info(path):
    """Bentuk metadata aman untuk tampilan tanpa membuka isi APK."""
    if not path:
        return None
    match = STABLE_APK.fullmatch(path.name)
    return {
        "name": path.name,
        "version": ".".join(match.groups()[1:]) if match else "",
        "size_mb": round(path.stat().st_size / 1024 / 1024, 1),
        "modified_at": path.stat().st_mtime,
    }
