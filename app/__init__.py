import datetime as dt
import logging
import os
import secrets
from pathlib import Path

from flask import Flask, g, request

from . import db as dbmod
from .report import rp, tgl_id
from .stock import JENIS_LABEL, fmt

BASE = Path(__file__).resolve().parent.parent


def _secret_key(instance):
    """Baca atau buat secret key lokal yang persisten untuk sesi dan CSRF."""
    f = instance / "secret_key"
    if not f.exists():
        f.write_text(secrets.token_hex(32))
    return f.read_text().strip()


def sidik_kode():
    """Waktu ubah terakhir file kode Python; berubah = aplikasi diperbarui & server perlu di-restart."""
    return max(p.stat().st_mtime for p in Path(__file__).parent.glob("*.py"))


HALAMAN_ERROR = """<!doctype html><html lang="id"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Terjadi kesalahan</title>
<style>body{{font:16px system-ui,sans-serif;max-width:560px;margin:10vh auto;padding:0 16px;color:#1c2630}}
.box{{border:1px solid #f2b8b5;background:#fbe9e7;border-radius:10px;padding:16px}}a{{color:#1f4e78}}</style></head>
<body><div class="box"><h2 style="margin-top:0">Terjadi kesalahan</h2>
<p>Halaman ini gagal dibuka. Data Anda tetap aman.</p>{saran}
<p><a href="javascript:history.back()">← Kembali</a> · <a href="/">Dashboard</a></p>
<p style="color:#5f6b77;font-size:13px">Kode kejadian: {kode} (tercatat di data/error.log)</p></div></body></html>"""


def create_app(test_config=None):
    """Buat dan konfigurasi instance Flask untuk produksi maupun pengujian."""
    app = Flask(__name__)
    data_dir = Path(os.environ.get("STOK_DATA_DIR", BASE / "data"))
    data_dir.mkdir(parents=True, exist_ok=True)
    app.config.update(
        DATABASE=str(data_dir / "stok.db"),
        UPLOAD_FOLDER=str(data_dir / "foto"),
        TMP_FOLDER=str(data_dir / "tmp"),
        MAX_CONTENT_LENGTH=25 * 1024 * 1024,
        ADMIN_DEFAULT_PASSWORD=os.environ.get("STOK_ADMIN_PASSWORD", "admin123"),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        PERMANENT_SESSION_LIFETIME=dt.timedelta(days=14),
        TEMPLATES_AUTO_RELOAD=True,
        SEND_FILE_MAX_AGE_DEFAULT=0,
    )
    if test_config:
        app.config.update(test_config)
    app.config.setdefault("SECRET_KEY", None)
    if not app.config["SECRET_KEY"]:
        app.config["SECRET_KEY"] = _secret_key(data_dir)
    for k in ("UPLOAD_FOLDER", "TMP_FOLDER"):
        Path(app.config[k]).mkdir(parents=True, exist_ok=True)

    # 2) catat error ke data/error.log agar bisa diperiksa
    handler = logging.FileHandler(data_dir / "error.log", encoding="utf-8")
    handler.setLevel(logging.ERROR)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    app.logger.addHandler(handler)

    # 3) deteksi server yang belum di-restart setelah aplikasi diperbarui
    app.config["SIDIK_KODE"] = sidik_kode()

    def perlu_restart():
        """Deteksi perubahan file Python sejak proses aplikasi dimulai."""
        try:
            return sidik_kode() != app.config["SIDIK_KODE"]
        except OSError:
            return False

    @app.context_processor
    def ctx_restart():
        """Sediakan status kebutuhan restart untuk seluruh template."""
        return {"perlu_restart": perlu_restart()}

    @app.errorhandler(500)
    def error_500(e):
        """Catat rincian error dan tampilkan halaman aman dengan kode kejadian."""
        kode = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        user = g.get("user")
        app.logger.error("[%s] %s %s user=%s: %r", kode, request.method, request.full_path,
                         user["username"] if user else "-", getattr(e, "original_exception", e))
        saran = ("<p><b>Aplikasi baru saja diperbarui tetapi server belum di-restart.</b> Tutup jendela start.bat "
                 "lalu jalankan lagi, kemudian buka ulang halaman ini.</p>") if perlu_restart() else ""
        return HALAMAN_ERROR.format(saran=saran, kode=kode), 500

    app.teardown_appcontext(dbmod.close_db)
    dbmod.init_db(app)

    app.jinja_env.filters["angka"] = fmt
    app.jinja_env.filters["rp"] = rp
    app.jinja_env.filters["tgl"] = tgl_id
    app.jinja_env.globals["JENIS_LABEL"] = JENIS_LABEL
    app.jinja_env.globals["today"] = lambda: dt.date.today().isoformat()

    from . import auth, mobile_api, views_admin, views_laporan, views_main, views_opname, views_pr
    app.register_blueprint(auth.bp)
    app.register_blueprint(views_main.bp)
    app.register_blueprint(views_admin.bp)
    app.register_blueprint(views_laporan.bp)
    app.register_blueprint(views_opname.bp)
    app.register_blueprint(views_pr.bp)
    app.register_blueprint(mobile_api.bp)
    auth.pasang(app)
    return app
