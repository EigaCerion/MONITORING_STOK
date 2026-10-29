"""Alamat laptop yang bisa dibuka dari HP anggota + QR code-nya."""
import io
import ipaddress
import socket

import qrcode
import qrcode.image.svg

HOTSPOT_WINDOWS = ipaddress.ip_network("192.168.137.0/24")  # Mobile Hotspot Windows selalu memakai subnet ini
TAILSCALE = ipaddress.ip_network("100.64.0.0/10")


def alamat_lokal():
    """Daftar IPv4 laptop (tanpa loopback / link-local), diberi jenis jaringan + saran pemakaian."""
    ips = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    try:  # IP yang dipakai untuk keluar jaringan (tidak mengirim paket apa pun)
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    hasil = []
    for ip in ips:
        a = ipaddress.ip_address(ip)
        if a.is_loopback or a.is_link_local:
            continue
        if a in HOTSPOT_WINDOWS:
            jenis, urut, catatan = "Hotspot laptop", 0, "HP tersambung ke hotspot dari laptop ini. Paling andal."
        elif a in TAILSCALE:
            jenis, urut, catatan = "Tailscale", 2, "Hanya untuk HP yang memasang & menyalakan Tailscale."
        else:
            jenis, urut, catatan = "WiFi / LAN", 1, "HP harus tersambung ke WiFi yang sama dengan laptop."
        hasil.append({"ip": ip, "jenis": jenis, "urut": urut, "catatan": catatan})
    return sorted(hasil, key=lambda x: (x["urut"], x["ip"]))


def qr_svg(teks):
    """Buat QR code SVG dari alamat akses aplikasi tanpa menyimpan file sementara."""
    img = qrcode.make(teks, image_factory=qrcode.image.svg.SvgPathImage, box_size=8, border=2)
    buf = io.BytesIO()
    img.save(buf)
    return buf.getvalue().decode("utf-8")
