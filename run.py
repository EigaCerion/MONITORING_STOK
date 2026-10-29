"""Jalankan aplikasi: python run.py  (default http://0.0.0.0:8000, bisa diakses HP di jaringan yang sama)."""
import os

from waitress import serve

from app import create_app
from app.jaringan import alamat_lokal

app = create_app()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    print(f"Monitoring Stok berjalan di http://localhost:{port}")
    for a in alamat_lokal():
        print(f"  HP via {a['jenis']:<14}: http://{a['ip']}:{port}")
    print("  QR code & panduan: menu Pengaturan -> Akses dari HP")
    serve(app, host=os.environ.get("HOST", "0.0.0.0"), port=port, threads=8, max_request_body_size=30 * 1024 * 1024)
