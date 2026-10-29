"""Login, peran pengguna, dan proteksi CSRF sederhana."""
import functools
import secrets

from flask import Blueprint, abort, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash

from .db import get_db

bp = Blueprint("auth", __name__)

ROLE_LABEL = {"admin": "Admin", "petugas": "Petugas Gudang", "pimpinan": "Pimpinan"}


def pasang(app):
    """Pasang pemuatan pengguna, proteksi CSRF, dan context template global."""
    @app.before_request
    def muat_user():
        """Muat pengguna sesi dan validasi token CSRF sebelum setiap request."""
        g.user = None
        uid = session.get("uid")
        if uid:
            u = get_db().execute("SELECT * FROM users WHERE id = ? AND aktif = 1", (uid,)).fetchone()
            g.user = u
            if not u:
                session.clear()
        # API APK memakai token Bearer dan tidak memakai cookie browser. CSRF
        # tetap wajib untuk seluruh form web, tetapi bukan untuk blueprint ini.
        if (request.method == "POST" and request.endpoint not in ("auth.login",)
                and request.blueprint != "mobile_api"):
            token = request.form.get("_csrf") or request.headers.get("X-CSRF-Token")
            if not token or token != session.get("csrf"):
                abort(400, "Sesi kedaluwarsa, muat ulang halaman lalu coba lagi.")

    @app.context_processor
    def ctx():
        """Sediakan pengguna, CSRF, dan label peran untuk semua template."""
        if "csrf" not in session:
            session["csrf"] = secrets.token_hex(16)
        return {"user": g.get("user"), "csrf": session["csrf"], "ROLE_LABEL": ROLE_LABEL}


def login_required(*roles):
    """Batasi route untuk pengguna login dan, bila diberikan, peran tertentu."""
    def deco(view):
        """Bungkus view dengan pemeriksaan login dan peran."""
        @functools.wraps(view)
        def wrapped(*a, **kw):
            """Jalankan view hanya setelah otorisasi berhasil."""
            if g.user is None:
                return redirect(url_for("auth.login", next=request.path))
            if roles and g.user["role"] not in roles:
                abort(403)
            return view(*a, **kw)
        return wrapped
    return deco


@bp.route("/login", methods=["GET", "POST"])
def login():
    """Autentikasi pengguna aktif dan arahkan ke halaman sesuai perannya."""
    if request.method == "POST":
        u = get_db().execute("SELECT * FROM users WHERE username = ? AND aktif = 1",
                             (request.form.get("username", "").strip().lower(),)).fetchone()
        if u and check_password_hash(u["password"], request.form.get("password", "")):
            session.clear()
            session.permanent = True
            session["uid"] = u["id"]
            nxt = request.args.get("next", "")
            if not nxt.startswith("/") or nxt.startswith("//"):
                nxt = url_for("main.input_transaksi" if u["role"] == "petugas" else "main.dashboard")
            return redirect(nxt)
        flash("Username atau password salah", "error")
    return render_template("login.html")


@bp.route("/logout", methods=["POST"])
def logout():
    """Hapus seluruh sesi pengguna dan kembali ke halaman login."""
    session.clear()
    return redirect(url_for("auth.login"))
