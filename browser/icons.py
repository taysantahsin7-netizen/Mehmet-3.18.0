# -*- coding: utf-8 -*-
"""
NAVİGATÖR İKONLARI — emoji yerine kodla üretilen PNG ikonlar ve GIF animasyonlar.

Tüm görseller ilk istekte ``assets/icons/`` altına üretilir ve diske
önbelleklenir; sonraki açılışlarda dosyadan yüklenir. Tema accent rengi
değişirse ``regenerate_all()`` ile yeniden boyanabilir.

PNG ikonlar: QPainter ile çizilir (vektör kalitesi, şeffaf zemin).
GIF'ler   : Pillow ile kare kare üretilir (canlı durum göstergeleri).
"""
from __future__ import annotations

import math
from pathlib import Path

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import (QBrush, QColor, QFont, QIcon, QPainter,
                         QPainterPath, QPen, QPixmap, QLinearGradient,
                         QRadialGradient, QConicalGradient)

_ICONS_DIR = Path(__file__).resolve().parent.parent / "assets" / "icons"

# ── yardımcılar ──────────────────────────────────────────────────────────────

def _dir() -> Path:
    _ICONS_DIR.mkdir(parents=True, exist_ok=True)
    return _ICONS_DIR


def _painter(size: int) -> tuple[QPainter, QPixmap]:
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    return p, pm


def _accent() -> QColor:
    """Canlı tema accent'i (ui.C.PRI) — döngüsel import olmadan."""
    try:
        from ui import C
        v = getattr(C, "PRI", None)
        if isinstance(v, str):
            return QColor(v)
    except Exception:
        pass
    return QColor("#f2b632")


def _green() -> QColor:
    try:
        from ui import C
        v = getattr(C, "GREEN", None)
        if isinstance(v, str):
            return QColor(v)
    except Exception:
        pass
    return QColor("#00ff8c")


def _red() -> QColor:
    try:
        from ui import C
        v = getattr(C, "RED", None)
        if isinstance(v, str):
            return QColor(v)
    except Exception:
        pass
    return QColor("#ff4d6d")


# ── PNG ikon çizimleri ───────────────────────────────────────────────────────

def _draw_shield(p: QPainter, s: int, color: QColor, check: bool) -> None:
    """Kalkan gövdesi (adblock). check=True → içinde onay işareti."""
    w = s * 0.78
    x0, y0 = (s - w) / 2, s * 0.12
    path = QPainterPath()
    path.moveTo(x0 + w / 2, y0)
    path.lineTo(x0 + w, y0 + w * 0.18)
    path.lineTo(x0 + w, y0 + w * 0.55)
    path.cubicTo(x0 + w, y0 + w * 0.85, x0 + w * 0.6, y0 + w * 1.02,
                 x0 + w / 2, y0 + w * 1.12)
    path.cubicTo(x0 + w * 0.4, y0 + w * 1.02, x0, y0 + w * 0.85,
                 x0, y0 + w * 0.55)
    path.lineTo(x0, y0 + w * 0.18)
    path.closeSubpath()
    grad = QLinearGradient(x0, y0, x0 + w, y0 + w)
    c1 = QColor(color); c1.setAlpha(70)
    grad.setColorAt(0.0, c1)
    grad.setColorAt(1.0, color)
    p.setPen(QPen(color, s * 0.05))
    p.setBrush(QBrush(grad))
    p.drawPath(path)
    if check:
        pen = QPen(QColor(255, 255, 255, 240), s * 0.075)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        cy = y0 + w * 0.52
        p.drawPolyline([QPointF(x0 + w * 0.28, cy + w * 0.02),
                        QPointF(x0 + w * 0.45, cy + w * 0.2),
                        QPointF(x0 + w * 0.74, cy - w * 0.18)])


def _draw_globe(p: QPainter, s: int, color: QColor) -> None:
    """Dünya (tarayıcı) — meridyen/paralel çizgili küre."""
    pen = QPen(color, s * 0.055)
    p.setPen(pen)
    r = s * 0.34
    c = s / 2
    p.drawEllipse(QPointF(c, c), r, r)
    p.drawLine(QPointF(c, c - r), QPointF(c, c + r))
    p.drawEllipse(QPointF(c, c), r * 0.55, r)
    p.drawLine(QPointF(c - r, c), QPointF(c + r, c))
    p.drawEllipse(QPointF(c, c), r, r * 0.55)


def _draw_satellite(p: QPainter, s: int, color: QColor) -> None:
    """Uydu + sinyal dalgaları (VPN)."""
    pen = QPen(color, s * 0.06)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    # gövde
    p.save()
    p.translate(s / 2, s / 2)
    p.rotate(-35)
    p.setBrush(QBrush(color))
    p.drawRoundedRect(QRectF(-s * 0.08, -s * 0.22, s * 0.16, s * 0.44), 3, 3)
    p.setBrush(QBrush(QColor(255, 255, 255, 200)))
    p.drawRect(QRectF(-s * 0.22, -s * 0.16, s * 0.14, s * 0.32))
    p.drawRect(QRectF(s * 0.08, -s * 0.16, s * 0.14, s * 0.32))
    p.restore()
    # sinyal yayları
    p.setBrush(Qt.BrushStyle.NoBrush)
    for k in (1.0, 1.6, 2.2):
        arc_r = s * 0.14 * k
        p.drawArc(QRectF(s * 0.62 - arc_r, s * 0.38 - arc_r,
                         arc_r * 2, arc_r * 2), 30 * 16, 120 * 16)


def _draw_dots(p: QPainter, s: int, color: QColor) -> None:
    """Dikey üç nokta (⋮ ana menü)."""
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(color))
    for k in (-1, 0, 1):
        r = s * 0.075
        p.drawEllipse(QPointF(s / 2, s / 2 + k * s * 0.22), r, r)


def _draw_home(p: QPainter, s: int, color: QColor) -> None:
    """Ev (ana sayfa)."""
    pen = QPen(color, s * 0.06)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPolyline([QPointF(s * 0.14, s * 0.5), QPointF(s * 0.5, s * 0.16),
                    QPointF(s * 0.86, s * 0.5)])
    p.drawRect(QRectF(s * 0.26, s * 0.5, s * 0.48, s * 0.34))
    p.drawRect(QRectF(s * 0.42, s * 0.6, s * 0.16, s * 0.24))


def _draw_close(p: QPainter, s: int, color: QColor) -> None:
    """✕ kapat."""
    pen = QPen(color, s * 0.075)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    m = s * 0.28
    p.drawLine(QPointF(m, m), QPointF(s - m, s - m))
    p.drawLine(QPointF(s - m, m), QPointF(m, s - m))


def _draw_star(p: QPainter, s: int, color: QColor, filled: bool) -> None:
    """Yıldız (yer imi) — dolu veya kontur."""
    import math as _m
    cx, cy, R, r = s / 2, s * 0.54, s * 0.36, s * 0.155
    pts = []
    for i in range(10):
        ang = -_m.pi / 2 + i * _m.pi / 5
        rad = R if i % 2 == 0 else r
        pts.append(QPointF(cx + rad * _m.cos(ang), cy + rad * _m.sin(ang)))
    pen = QPen(color, s * 0.05)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(QBrush(color) if filled else Qt.BrushStyle.NoBrush)
    p.drawPolygon(pts)


def _draw_bolt(p: QPainter, s: int, color: QColor) -> None:
    """Şimşek (hız testi)."""
    path = QPainterPath()
    path.moveTo(s * 0.56, s * 0.08)
    path.lineTo(s * 0.26, s * 0.56)
    path.lineTo(s * 0.47, s * 0.56)
    path.lineTo(s * 0.4, s * 0.92)
    path.lineTo(s * 0.74, s * 0.4)
    path.lineTo(s * 0.52, s * 0.4)
    path.closeSubpath()
    p.setPen(QPen(color, s * 0.03))
    p.setBrush(QBrush(color))
    p.drawPath(path)


def _draw_arrow(p: QPainter, s: int, color: QColor, flip: bool = False) -> None:
    """Geri/ileri oku (◀ / ▶)."""
    pen = QPen(color, s * 0.075)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)

    def mx(x: float) -> float:
        return (s - x) if flip else x

    p.drawPolyline([QPointF(mx(s * 0.62), s * 0.2),
                    QPointF(mx(s * 0.3), s * 0.5),
                    QPointF(mx(s * 0.62), s * 0.8)])


def _draw_back(p: QPainter, s: int, color: QColor) -> None:
    _draw_arrow(p, s, color, flip=False)


def _draw_forward(p: QPainter, s: int, color: QColor) -> None:
    _draw_arrow(p, s, color, flip=True)


def _draw_plus(p: QPainter, s: int, color: QColor) -> None:
    """＋ yeni sekme."""
    pen = QPen(color, s * 0.085)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    p.drawLine(QPointF(s * 0.28, s * 0.5), QPointF(s * 0.72, s * 0.5))
    p.drawLine(QPointF(s * 0.5, s * 0.28), QPointF(s * 0.5, s * 0.72))


def _draw_gear(p: QPainter, s: int, color: QColor) -> None:
    """Dişli (ayarlar)."""
    c = s / 2
    R = s * 0.28
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(color))
    p.save()
    p.translate(c, c)
    for i in range(8):
        p.save()
        p.rotate(i * 45)
        p.drawRect(QRectF(-s * 0.045, -R - s * 0.075, s * 0.09, s * 0.11))
        p.restore()
    p.restore()
    pen = QPen(color, s * 0.07)
    p.setPen(pen)
    p.setBrush(QBrush(QColor(0, 0, 0, 0)))
    p.drawEllipse(QPointF(c, c), R, R)
    p.setBrush(QBrush(color))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(QPointF(c, c), s * 0.075, s * 0.075)


def _draw_moon(p: QPainter, s: int, color: QColor) -> None:
    """Hilal (karanlık mod)."""
    big = QPainterPath()
    big.addEllipse(QRectF(s * 0.18, s * 0.12, s * 0.66, s * 0.66))
    cut = QPainterPath()
    cut.addEllipse(QRectF(s * 0.36, s * 0.05, s * 0.6, s * 0.6))
    p.setPen(QPen(color, s * 0.035))
    p.setBrush(QBrush(color))
    p.drawPath(big.subtracted(cut))


def _draw_zoom(p: QPainter, s: int, color: QColor) -> None:
    """İçinde + olan büyüteç (yakınlaştırma)."""
    pen = QPen(color, s * 0.055)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(QPointF(s * 0.42, s * 0.42), s * 0.24, s * 0.24)
    pen2 = QPen(color, s * 0.07)
    pen2.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen2)
    p.drawLine(QPointF(s * 0.6, s * 0.6), QPointF(s * 0.84, s * 0.84))
    p.drawLine(QPointF(s * 0.32, s * 0.42), QPointF(s * 0.52, s * 0.42))
    p.drawLine(QPointF(s * 0.42, s * 0.32), QPointF(s * 0.42, s * 0.52))


def _draw_camera(p: QPainter, s: int, color: QColor) -> None:
    """Fotoğraf makinesi (ekran görüntüsü)."""
    pen = QPen(color, s * 0.055)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(QRectF(s * 0.14, s * 0.3, s * 0.72, s * 0.5), 4, 4)
    p.drawRoundedRect(QRectF(s * 0.34, s * 0.2, s * 0.32, s * 0.12), 3, 3)
    p.drawEllipse(QPointF(s / 2, s * 0.55), s * 0.16, s * 0.16)


def _draw_translate(p: QPainter, s: int, color: QColor) -> None:
    """Çeviri: A harfi + konuşma balonu içinde 文 benzeri çizgi."""
    pen = QPen(color, s * 0.055)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(QRectF(s * 0.1, s * 0.12, s * 0.8, s * 0.62), 6, 6)
    p.drawLine(QPointF(s * 0.14, s * 0.86), QPointF(s * 0.32, s * 0.72))
    # 'A'
    p.drawLine(QPointF(s * 0.32, s * 0.6), QPointF(s * 0.42, s * 0.26))
    p.drawLine(QPointF(s * 0.42, s * 0.26), QPointF(s * 0.52, s * 0.6))
    p.drawLine(QPointF(s * 0.36, s * 0.47), QPointF(s * 0.48, s * 0.47))
    # karakök çizgisi (ikinci dil imzası)
    p.drawLine(QPointF(s * 0.6, s * 0.3), QPointF(s * 0.74, s * 0.3))
    p.drawLine(QPointF(s * 0.67, s * 0.3), QPointF(s * 0.67, s * 0.4))
    p.drawLine(QPointF(s * 0.58, s * 0.48), QPointF(s * 0.76, s * 0.48))


def _draw_reload(p: QPainter, s: int, color: QColor) -> None:
    """Yenile oku."""
    pen = QPen(color, s * 0.07)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    r = s * 0.3
    c = s / 2
    p.drawArc(QRectF(c - r, c - r, 2 * r, 2 * r), 40 * 16, 280 * 16)
    p.setBrush(QBrush(color))
    p.setPen(Qt.PenStyle.NoPen)
    path = QPainterPath()
    path.moveTo(c + r * 0.9, c - r * 0.55)
    path.lineTo(c + r * 1.15, c + r * 0.25)
    path.lineTo(c + r * 0.25, c + r * 0.05)
    path.closeSubpath()
    p.drawPath(path)


def _draw_search(p: QPainter, s: int, color: QColor) -> None:
    """Büyüteç."""
    pen = QPen(color, s * 0.06)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(QPointF(s * 0.42, s * 0.42), s * 0.24, s * 0.24)
    pen2 = QPen(color, s * 0.08)
    pen2.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen2)
    p.drawLine(QPointF(s * 0.6, s * 0.6), QPointF(s * 0.84, s * 0.84))


def _draw_mail(p: QPainter, s: int, color: QColor) -> None:
    """Zarf (rapor)."""
    pen = QPen(color, s * 0.055)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(QRectF(s * 0.1, s * 0.24, s * 0.8, s * 0.52), 4, 4)
    p.drawPolyline([QPointF(s * 0.12, s * 0.28), QPointF(s * 0.5, s * 0.58),
                    QPointF(s * 0.88, s * 0.28)])


def _draw_eye(p: QPainter, s: int, color: QColor) -> None:
    """Göz (görsel analiz)."""
    pen = QPen(color, s * 0.05)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    path = QPainterPath()
    path.moveTo(s * 0.1, s * 0.5)
    path.quadTo(s * 0.5, s * 0.12, s * 0.9, s * 0.5)
    path.quadTo(s * 0.5, s * 0.88, s * 0.1, s * 0.5)
    p.drawPath(path)
    p.drawEllipse(QPointF(s / 2, s / 2), s * 0.14, s * 0.14)
    p.setBrush(QBrush(color))
    p.drawEllipse(QPointF(s / 2, s / 2), s * 0.06, s * 0.06)


_PNG_SPEC = {
    # name: (fonksiyon, kwargs)
    "shield":   (_draw_shield, {"check": True}),
    "globe":    (_draw_globe, {}),
    "satellite": (_draw_satellite, {}),
    "menu":     (_draw_dots, {}),
    "home":     (_draw_home, {}),
    "close":    (_draw_close, {}),
    "star":     (_draw_star, {"filled": False}),
    "star_filled": (_draw_star, {"filled": True}),
    "bolt":     (_draw_bolt, {}),
    "back":     (_draw_back, {}),
    "forward":  (_draw_forward, {}),
    "plus":     (_draw_plus, {}),
    "gear":     (_draw_gear, {}),
    "moon":     (_draw_moon, {}),
    "zoom":     (_draw_zoom, {}),
    "camera":   (_draw_camera, {}),
    "translate": (_draw_translate, {}),
    "reload":   (_draw_reload, {}),
    "search":   (_draw_search, {}),
    "mail":     (_draw_mail, {}),
    "eye":      (_draw_eye, {}),
}

ICON_SIZES = (32, 64)      # üretilecek çözünürlükler (Qt ölçekler)


def get_icon(name: str) -> QIcon:
    """Adıyla ikon al (önbellekten ya da üretip kaydederek)."""
    d = _dir()
    ic = QIcon()
    accent = _accent().name()
    for size in ICON_SIZES:
        f = d / f"{name}_{accent.strip('#')}_{size}.png"
        if not f.exists():
            fn, kw = _PNG_SPEC[name]
            p, pm = _painter(size)
            fn(p, size, _accent(), **kw)
            p.end()
            pm.save(str(f), "PNG")
        ic.addFile(str(f), __import__("PyQt6.QtCore", fromlist=["QSize"]).QSize(size, size))
    return ic


def get_pixmap(name: str, size: int = 32,
               color: "str | QColor | None" = None) -> QPixmap:
    """Tek kare pixmap al. ``color`` verilirse o renkle üretilir
    (örn. devre dışı durumlar için gri varyant)."""
    if color is None:
        col = _accent()
    elif isinstance(color, QColor):
        col = QColor(color)
    else:
        col = QColor(color)
    d = _dir()
    key = col.name().strip("#")
    f = d / f"{name}_{key}_{size}.png"
    if not f.exists():
        fn, kw = _PNG_SPEC[name]
        p, pm = _painter(size)
        fn(p, size, col, **kw)
        p.end()
        pm.save(str(f), "PNG")
    pm = QPixmap(str(f))
    if pm.isNull():
        fn, kw = _PNG_SPEC[name]
        p, pm2 = _painter(size)
        fn(p, size, col, **kw)
        p.end()
        return pm2
    return pm


# ── GIF üretimi (Pillow) ─────────────────────────────────────────────────────

def _gif_path(name: str, accent: str) -> Path:
    return _dir() / f"{name}_{accent.strip('#')}.gif"


def _pillow():
    try:
        from PIL import Image, ImageDraw
        return Image, ImageDraw
    except Exception:
        return None, None


def _gif_shield(accent: str):
    """Kalkan nabız animasyonu — adblock aktif."""
    Image, ImageDraw = _pillow()
    if Image is None:
        return None
    frames = []
    S = 64
    col = tuple(int(accent.strip('#')[i:i+2], 16) for i in (0, 2, 4))
    for k in range(8):
        img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        dr = ImageDraw.Draw(img)
        t = k / 8
        pulse = 1.0 + 0.12 * math.sin(2 * math.pi * t)
        w = int(S * 0.6 * pulse)
        x0, y0 = (S - w) // 2, int(S * 0.14)
        pts = [(x0 + w // 2, y0), (x0 + w, y0 + int(w * 0.2)),
               (x0 + w, y0 + int(w * 0.5)),
               (x0 + w // 2, y0 + int(w * 0.95)),
               (x0, y0 + int(w * 0.5)), (x0, y0 + int(w * 0.2))]
        dr.polygon(pts, outline=col + (255,), width=4)
        # onay işareti
        dr.line([(x0 + int(w*0.3), y0 + int(w*0.5)),
                 (x0 + int(w*0.45), y0 + int(w*0.66)),
                 (x0 + int(w*0.72), y0 + int(w*0.32))],
                fill=(255, 255, 255, 240), width=5)
        frames.append(img)
    return frames


def _gif_globe(accent: str):
    """Dönen dünya — gezinme göstergesi."""
    Image, ImageDraw = _pillow()
    if Image is None:
        return None
    frames = []
    S = 64
    col = tuple(int(accent.strip('#')[i:i+2], 16) for i in (0, 2, 4))
    r = S * 0.34
    c = S / 2
    for k in range(10):
        img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        dr = ImageDraw.Draw(img)
        dr.ellipse([c - r, c - r, c + r, c + r], outline=col + (255,), width=3)
        dr.line([(c, c - r), (c, c + r)], fill=col + (255,), width=2)
        # hareketli paralel (kutu her zaman y0<y1 olacak şekilde normalize)
        ph = math.sin(2 * math.pi * k / 10) * r * 0.5
        dr.arc([c - r, min(c - ph, c + ph), c + r, max(c - ph, c + ph)],
               0, 360, fill=col + (255,), width=2)
        # hareketli meridyen
        mx = math.cos(2 * math.pi * k / 10) * r * 0.7
        dr.ellipse([c - abs(mx), c - r, c + abs(mx), c + r],
                   outline=col + (180,), width=2)
        frames.append(img)
    return frames


def _gif_satellite(accent: str):
    """VPN bağlanıyor: sinyal yayları genişleyip kaybolur."""
    Image, ImageDraw = _pillow()
    if Image is None:
        return None
    frames = []
    S = 64
    col = tuple(int(accent.strip('#')[i:i+2], 16) for i in (0, 2, 4))
    for k in range(10):
        img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        dr = ImageDraw.Draw(img)
        t = k / 10
        # uydu gövdesi
        dr.rounded_rectangle([S*0.42, S*0.28, S*0.58, S*0.6], 4,
                             outline=col + (255,), width=3)
        dr.rectangle([S*0.26, S*0.34, S*0.4, S*0.54], outline=col + (255,), width=2)
        dr.rectangle([S*0.6, S*0.34, S*0.74, S*0.54], outline=col + (255,), width=2)
        # yaylar
        for j in range(3):
            rad = S * (0.16 + 0.14 * j + 0.06 * t)
            alpha = max(0, int(255 * (1 - ((t + j * 0.15) % 1.0))))
            if alpha > 20:
                dr.arc([S*0.78 - rad, S*0.44 - rad, S*0.78 + rad, S*0.44 + rad],
                       -50, 60, fill=col + (alpha,), width=3)
        frames.append(img)
    return frames


def _gif_bolt(accent: str):
    """Hız testi çalışıyor: parlayan şimşek."""
    Image, ImageDraw = _pillow()
    if Image is None:
        return None
    frames = []
    S = 64
    col = tuple(int(accent.strip('#')[i:i+2], 16) for i in (0, 2, 4))
    pts = [(S*0.56, S*0.08), (S*0.26, S*0.56), (S*0.47, S*0.56),
           (S*0.4, S*0.92), (S*0.74, S*0.4), (S*0.52, S*0.4)]
    for k in range(8):
        img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        dr = ImageDraw.Draw(img)
        t = k / 8
        glow = int(120 + 135 * math.sin(2 * math.pi * t))
        dr.polygon(pts, fill=col + (glow,))
        frames.append(img)
    return frames


def _gif_eye(accent: str):
    """Görsel analiz: kırpışan göz."""
    Image, ImageDraw = _pillow()
    if Image is None:
        return None
    frames = []
    S = 64
    col = tuple(int(accent.strip('#')[i:i+2], 16) for i in (0, 2, 4))
    for k in range(8):
        img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        dr = ImageDraw.Draw(img)
        t = k / 8
        blink = max(0.08, math.sin(2 * math.pi * t))
        dr.arc([S*0.1, S*(0.5 - blink*0.38), S*0.9, S*(0.5 + blink*0.38)],
               0, 360, fill=col + (255,), width=3)
        dr.ellipse([S*0.5 - S*0.11, S*0.5 - S*0.11*blink - 1,
                    S*0.5 + S*0.11, S*0.5 + S*0.11*blink + 1],
                   outline=col + (255,), width=3)
        frames.append(img)
    return frames


_GIF_SPEC = {
    "shield":    _gif_shield,
    "globe":     _gif_globe,
    "satellite": _gif_satellite,
    "bolt":      _gif_bolt,
    "eye":       _gif_eye,
}

_gif_cache: dict[str, str] = {}


def get_gif_path(name: str) -> str:
    """Animasyonlu GIF dosya yolunu al (üretip önbelleğe alır).

    QLabel + QMovie ile kullanılır. Pillow yoksa '' döner —
    çağıran taraf statik PNG'ye düşer.
    """
    if name in _gif_cache:
        return _gif_cache[name]
    Image, _ = _pillow()
    if Image is None:
        return ""
    accent = _accent().name()
    f = _gif_path(name, accent)
    if not f.exists():
        fn = _GIF_SPEC[name]
        frames = fn(accent)
        if not frames:
            return ""
        durations = [70] * len(frames)
        frames[0].save(str(f), save_all=True, append_images=frames[1:],
                       loop=0, duration=durations, disposal=2)
    _gif_cache[name] = str(f)
    return str(f)


def regenerate_all() -> int:
    """Accent değişiminde çağır: tüm ikon/GIF varyantlarını tazele.
    Üretilen dosya sayısını döndürür."""
    d = _dir()
    count = 0
    for name in _PNG_SPEC:
        for size in ICON_SIZES:
            f = d / f"{name}_{_accent().name().strip('#')}_{size}.png"
            if not f.exists():
                get_icon(name)
                count += 1
    for name in _GIF_SPEC:
        accent = _accent().name()
        f = _gif_path(name, accent)
        if not f.exists():
            get_gif_path(name)
            count += 1
    return count
