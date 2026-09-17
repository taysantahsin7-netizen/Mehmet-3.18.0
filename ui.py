from __future__ import annotations

import json
import math
import os
import platform
import random
import subprocess
import sys
import threading
import time
from pathlib import Path

import psutil

if platform.system() == "Windows":
    _WIN_HIDE: dict = {"creationflags": subprocess.CREATE_NO_WINDOW}
else:
    _WIN_HIDE: dict = {}

from PyQt6.QtCore import (
    QEasingCurve, QMimeData, QObject, QPointF, QRectF, QSize, Qt,
    QPropertyAnimation, QSequentialAnimationGroup,
    QTimer, QUrl, pyqtSignal,
)
from PyQt6.QtGui import (
    QBrush, QColor, QConicalGradient, QDragEnterEvent, QDropEvent, QFont,
    QFontDatabase, QKeySequence, QLinearGradient, QPainter, QPainterPath,
    QPen, QPixmap, QRadialGradient, QShortcut,
)
from PyQt6.QtWidgets import (
    QApplication, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QMainWindow, QPushButton, QScrollArea, QSizePolicy, QSplitter,
    QStackedWidget, QTextEdit, QVBoxLayout, QWidget, QProgressBar,
    QGraphicsOpacityEffect,
)

# NAVİGATÖR çekirdeği (dahili tarayıcı): QApplication ÖNCE içe alınmalı,
# aksi halde QtWebEngine çalışmayı reddeder. Paket yoksa panel zarif düşer.
try:
    # VPN aktarma katmanı: Chromium her zaman yerel relay'e bağlanır;
    # VPN aç/kapa bu katmanın hedefini değiştirir (yeniden başlatma yok).
    from browser.vpn import ensure_relay_flags
    ensure_relay_flags()
    from PyQt6.QtWebEngineWidgets import QWebEngineView  # noqa: F401
    # Chromium, paylaşılan GL bağlamı ister — QApplication kurulmadan ÖNCE:
    QApplication.setAttribute(
        Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)
    _WEBENGINE_OK = True
except Exception:
    _WEBENGINE_OK = False

def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent

BASE_DIR   = _base_dir()
CONFIG_DIR = BASE_DIR / "config"
API_FILE   = CONFIG_DIR / "api_keys.json"


def _read_full_config() -> dict:
    """Read api_keys.json config dict. Returns {} on any error."""
    try:
        return json.loads(API_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


_DEFAULT_W, _DEFAULT_H = 1240, 780
_MIN_W,     _MIN_H     = 1040, 660
_LEFT_W  = 188
_RIGHT_W = 360

_OS = platform.system()  # "Windows" | "Darwin" | "Linux"


class C:
    BG        = "#070502"
    PANEL     = "#120d04"
    PANEL2    = "#171105"
    BORDER    = "#3d2f10"
    BORDER_B  = "#6b5220"
    BORDER_A  = "#523d16"
    PRI       = "#ffb300"
    PRI_DIM   = "#8f6a10"
    PRI_GHO   = "#241a04"
    ACC       = "#ff6b00"
    ACC2      = "#ffd766"
    GREEN     = "#9dffb0"
    GREEN_D   = "#2f7a45"
    RED       = "#ff4455"
    MUTED_C   = "#ff6677"
    TEXT      = "#ffe9b8"
    TEXT_DIM  = "#8a744a"
    TEXT_MED  = "#c2a566"
    WHITE     = "#fff6df"
    DARK      = "#0d0a03"
    BAR_BG    = "#1c1505"


# Ana renge (accent) bağlı anahtarlar — durum renkleri (ACC, GREEN, RED…) sabit kalır
_HUE_LINKED = (
    "BG", "PANEL", "PANEL2", "BORDER", "BORDER_B", "BORDER_A",
    "PRI", "PRI_DIM", "PRI_GHO", "TEXT", "TEXT_DIM", "TEXT_MED",
    "WHITE", "DARK", "BAR_BG",
)
_PALETTE_DEFAULTS: dict[str, str] = {k: getattr(C, k) for k in _HUE_LINKED}

DEFAULT_UI_COLOR = _PALETTE_DEFAULTS["PRI"]
_LEGACY_DEFAULT_COLOR = "#00d4ff"   # eski cyan tema — bir kez yeni varsayılana göçürülür

FONT_DISPLAY = "Exo 2"
FONT_UI      = "Rajdhani"
FONT_MONO    = "JetBrains Mono"


def _font(family: str, size: int, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    """Paketli temaları kullan; yoksa Qt'nin güvenli sistem fontlarına düş."""
    fallback = {
        FONT_DISPLAY: "Bahnschrift",
        FONT_UI: "Segoe UI Variable",
        FONT_MONO: "Cascadia Mono",
    }.get(family)
    if fallback and not _FONTS_LOADED:
        family = fallback
    return QFont(family, size, weight)


# ── Uygulama sürümü ve Türkçe durum yardımcıları ─────────────────────────────
APP_VERSION = "4.0.0"

_STATE_TR = {
    "LISTENING":   "DİNLİYOR",
    "SPEAKING":    "KONUŞUYOR",
    "THINKING":    "DÜŞÜNÜYOR",
    "PROCESSING":  "İŞLİYOR",
    "SLEEPING":    "UYKU MODU",
    "INITIALISING": "BAŞLATILIYOR",
    "MUTED":       "MİKROFON SESSİZ",
}


def _tr_state(state: str) -> str:
    """İç durum adını Türkçe ekranda gösterilecek metne çevirir."""
    return _STATE_TR.get((state or "").upper(), (state or "").upper())


_FONT_FILES = [
    "Exo2.ttf", "Exo2-Italic.ttf",
    "Rajdhani-Regular.ttf", "Rajdhani-SemiBold.ttf", "Rajdhani-Bold.ttf",
    "JetBrainsMono.ttf",
]
_FONTS_LOADED = False


def _load_app_fonts() -> bool:
    """assets/fonts altındaki paketli fontları yükle. Başarısızlıkta sistem
    fontlarına sessizce düşülür; uygulama çalışmaya devam eder."""
    global _FONTS_LOADED
    if _FONTS_LOADED:
        return True
    ok = 0
    for fname in _FONT_FILES:
        path = BASE_DIR / "assets" / "fonts" / fname
        if path.exists():
            fid = QFontDatabase.addApplicationFont(str(path))
            if fid >= 0:
                ok += 1
    _FONTS_LOADED = ok > 0
    return _FONTS_LOADED


class NeoCanvas(QWidget):
    """MehmetNEO — kodle çizilen taktik operasyon ekranı: radar taraması,
    hedef kilitleri, altıgen ızgara ve canlı Türkçe telemetri."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self._phase = 0.0
        self._sweep = 0.0            # radar tarama açısı (derece)
        self._state = "DİNLİYOR"
        self._speaking = False
        self._events = [
            "ÇEKİRDEK BAĞLANTISI GÜVENLİ",
            "BARANT PROTOKOLÜ ETKİN",
            "MEHMETNEO HAZIR",
            "SİSTEM TELEMETRİSİ AKIYOR",
        ]
        # Radar temas noktaları: (açı°, yarıçap oranı, canlılık)
        self._blips = [[42.0, 0.55, 1.0], [205.0, 0.38, 1.0], [318.0, 0.66, 1.0]]
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._step)
        self._timer.start(24)

    def set_state(self, state: str, speaking: bool = False) -> None:
        self._state = _tr_state(state)
        self._speaking = speaking
        self.update()

    def _step(self) -> None:
        speed = 0.075 if self._speaking else 0.025
        self._phase = (self._phase + speed) % (2 * math.pi)
        self._sweep = (self._sweep + (7.5 if self._speaking else 3.2)) % 360
        # Tarama ışığı temas noktalarını canlandırır
        for blip in self._blips:
            diff = (blip[0] - self._sweep) % 360
            if diff < 22:
                blip[2] = min(1.0, blip[2] + 0.5)
            else:
                blip[2] = max(0.08, blip[2] - 0.012)
        if random.random() < 0.006:
            self._blips.append([random.uniform(0, 360), random.uniform(0.25, 0.72), 1.0])
            if len(self._blips) > 6:
                self._blips.pop(0)
        if random.random() < 0.02:
            self._events.append(random.choice([
                f"SEKTÖR {random.randint(1, 9):02d} Taranıyor",
                f"KANAL {random.randint(11, 99):02d} Senkronize",
                "PERİMETRE Güvenli",
                f"Paket {random.randint(4096, 65535):04X} Alındı",
                "Şifreleme AES-256 Etkin",
            ]))
            self._events = self._events[-5:]
        self.update()

    # ── çizim ────────────────────────────────────────────────────────────────
    def paintEvent(self, _):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        width, height = self.width(), self.height()
        center_x, center_y = width / 2, height / 2
        unit = min(width, height)

        # zemin + vinyet
        painter.fillRect(self.rect(), QColor("#0a0206"))
        painter.fillRect(self.rect(), QColor(52, 8, 20, 60))

        self._paint_hex_grid(painter, width, height)

        # ── radar düyarı ────────────────────────────────────────────────────
        radar_r = unit * 0.30
        painter.setPen(QPen(QColor(228, 72, 116, 60), 1))
        painter.setBrush(QBrush(QColor(30, 5, 12, 120)))
        painter.drawEllipse(QRectF(center_x - radar_r, center_y - radar_r,
                                   radar_r * 2, radar_r * 2))
        for frac, alpha in ((0.33, 46), (0.66, 52), (1.0, 70)):
            rr = radar_r * frac
            painter.setPen(QPen(QColor(228, 72, 116, alpha), 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QRectF(center_x - rr, center_y - rr, rr * 2, rr * 2))
        painter.setPen(QPen(QColor(228, 72, 116, 26), 1))
        painter.drawLine(QPointF(center_x - radar_r, center_y),
                         QPointF(center_x + radar_r, center_y))
        painter.drawLine(QPointF(center_x, center_y - radar_r),
                         QPointF(center_x, center_y + radar_r))

        # tarama ışığı (koni gradyanı)
        cone = QConicalGradient(QPointF(center_x, center_y), -self._sweep + 90)
        cone.setColorAt(0.00, QColor(255, 60, 100, 165))
        cone.setColorAt(0.06, QColor(255, 60, 100, 60))
        cone.setColorAt(0.16, QColor(255, 60, 100, 0))
        cone.setColorAt(0.86, QColor(255, 60, 100, 0))
        cone.setColorAt(1.00, QColor(255, 60, 100, 165))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(cone))
        painter.drawEllipse(QRectF(center_x - radar_r, center_y - radar_r,
                                   radar_r * 2, radar_r * 2))

        # temas noktaları
        for blip in self._blips:
            a_rad = math.radians(blip[0])
            bx = center_x + math.cos(a_rad) * radar_r * blip[1]
            by = center_y - math.sin(a_rad) * radar_r * blip[1]
            life = blip[2]
            painter.setPen(QPen(QColor(0, 255, 136, int(200 * life)), 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QPointF(bx, by), 6.5, 6.5)
            painter.setBrush(QBrush(QColor(0, 255, 136, int(235 * life))))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(QPointF(bx, by), 2.6, 2.6)

        # merkez çekirdek
        core_r = unit * (0.030 + 0.004 * math.sin(self._phase * 2.2))
        halo = QRadialGradient(QPointF(center_x, center_y), core_r * 3.4)
        halo.setColorAt(0, QColor("#ffd9e4"))
        halo.setColorAt(0.22, QColor("#ff5c85"))
        halo.setColorAt(0.55, QColor(150, 24, 56, 110))
        halo.setColorAt(1, QColor(30, 6, 14, 0))
        painter.setBrush(QBrush(halo))
        painter.drawEllipse(QRectF(center_x - core_r * 3.4, center_y - core_r * 3.4,
                                   core_r * 6.8, core_r * 6.8))
        painter.setBrush(QBrush(QColor("#ff2f63")))
        painter.drawEllipse(QRectF(center_x - core_r, center_y - core_r,
                                   core_r * 2, core_r * 2))

        # dönen dış yaylar
        for index, radius_factor in enumerate((0.36, 0.40, 0.45)):
            radius = unit * radius_factor
            painter.setPen(QPen(QColor(228, 72, 116, 70 + index * 25), 2 - index * 0.5))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            rect = QRectF(center_x - radius, center_y - radius, radius * 2, radius * 2)
            start = int((self._phase * (58 + index * 30) + index * 121) * 16)
            painter.drawArc(rect, start, 1160)
            painter.drawArc(rect, start + 2670, 780)

        # ── hedef kilitleri ─────────────────────────────────────────────────
        self._paint_target_lock(painter, width * 0.20, height * 0.30, unit * 0.11, self._phase)
        self._paint_target_lock(painter, width * 0.80, height * 0.68, unit * 0.13,
                                -self._phase * 1.3 + 2.0)

        # ── başlık bloğu ────────────────────────────────────────────────────
        painter.setFont(_font(FONT_DISPLAY, 24, QFont.Weight.Bold))
        painter.setPen(QColor("#ffe3ec"))
        painter.drawText(QRectF(0, center_y + unit * 0.36, width, 34),
                         Qt.AlignmentFlag.AlignCenter, "MEHMETNEO")
        painter.setFont(_font(FONT_MONO, 9, QFont.Weight.DemiBold))
        painter.setPen(QColor("#ff7d9d"))
        painter.drawText(QRectF(0, center_y + unit * 0.36 + 32, width, 20),
                         Qt.AlignmentFlag.AlignCenter, f"CİDDİ MOD  •  {self._state}")

        # alt osiloskop dalgası
        wave_y = center_y + unit * 0.47
        painter.setPen(QPen(QColor(228, 72, 116, 200), 1.6))
        amp = unit * (0.030 if self._speaking else 0.008)
        prev_x = prev_y = None
        for i in range(0, int(width * 0.55), 6):
            x = width * 0.225 + i
            y = wave_y + math.sin(self._phase * 3 + i * 0.045) * amp * (0.6 + 0.4 * math.sin(i * 0.012))
            if prev_x is not None:
                painter.drawLine(QPointF(prev_x, prev_y), QPointF(x, y))
            prev_x, prev_y = x, y

        # ── köşe telemetri blokları ─────────────────────────────────────────
        self._draw_status_block(painter, 20, 20, "NEO // DURUM", [
            ("DURUM",  self._state),
            ("KANAL",  "ŞİFRELİ"),
            ("KİMLİK", "MEHMETNEO"),
        ])
        self._draw_status_block(painter, width - 220, 20, "BARANT // AKIŞ", [
            ("01", self._events[-1]),
            ("02", self._events[-2] if len(self._events) > 1 else "HAZIR"),
            ("03", "OTONOMİ İZLENİYOR"),
        ])

        # tehlike seviyesi göstergesi (sol alt)
        painter.setFont(_font(FONT_MONO, 8, QFont.Weight.DemiBold))
        painter.setPen(QColor("#ff7d9d"))
        painter.drawText(QRectF(20, height - 44, 150, 16),
                         Qt.AlignmentFlag.AlignLeft, "TEHLİKE SEVİYESİ")
        for i in range(5):
            bx = 20 + i * 20
            col = QColor(255, 60, 100, 230) if i < 2 else QColor(90, 30, 45, 120)
            painter.fillRect(QRectF(bx, height - 24, 14, 8), col)

        # tarama çizgileri (CRT)
        painter.setPen(QColor(0, 0, 0, 70))
        for y in range(0, height, 3):
            painter.drawLine(0, y, width, y)

    # ── yardımcı çizimler ────────────────────────────────────────────────────
    def _paint_hex_grid(self, painter: QPainter, width: int, height: int) -> None:
        """Altıgen ızgara — taktik arka plan."""
        size = 34
        hex_h = size * math.sqrt(3)
        painter.setPen(QPen(QColor(120, 30, 52, 34), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        row = 0
        y = -hex_h
        while y < height + hex_h:
            offset = (size * 1.5) if row % 2 else 0.0
            x = -size * 2 + offset
            while x < width + size * 2:
                cxp, cyp = x, y + hex_h / 2
                pts = []
                for k in range(6):
                    ang = math.pi / 3 * k
                    pts.append(QPointF(cxp + size * 0.86 * math.cos(ang),
                                       cyp + size * 0.86 * math.sin(ang)))
                painter.drawPolygon(pts)
                x += size * 1.5
            y += hex_h / 2
            row += 1

    def _paint_target_lock(self, painter: QPainter, cx: float, cy: float,
                           r: float, phase: float) -> None:
        """Animasyonlu hedef kilit braketleri + dönen kadran."""
        col = QColor(255, 60, 100, 170)
        painter.setPen(QPen(col, 1.6))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        rect = QRectF(cx - r, cy - r, r * 2, r * 2)
        painter.drawArc(rect, 0, 5760)
        # dönen kadran çizgileri
        for k in range(4):
            ang = phase + k * math.pi / 2
            x1 = cx + math.cos(ang) * r * 0.82
            y1 = cy - math.sin(ang) * r * 0.82
            x2 = cx + math.cos(ang) * r
            y2 = cy - math.sin(ang) * r
            painter.drawLine(QPointF(x1, y1), QPointF(x2, y2))
        # köşe braketleri
        b = r * 0.42
        for sx, sy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
            corner_x = cx + sx * r * 1.18
            corner_y = cy + sy * r * 1.18
            painter.drawLine(QPointF(corner_x, corner_y),
                             QPointF(corner_x - sx * b, corner_y))
            painter.drawLine(QPointF(corner_x, corner_y),
                             QPointF(corner_x, corner_y - sy * b))
        # merkez nokta
        painter.setBrush(QBrush(QColor(255, 60, 100, 220)))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QPointF(cx, cy), 2.2, 2.2)

    def _draw_status_block(self, painter: QPainter, x: float, y: float, title: str,
                           rows: list[tuple[str, str]]) -> None:
        block_width, block_height = 200, 106
        painter.setBrush(QBrush(QColor(14, 4, 9, 195)))
        painter.setPen(QPen(QColor("#8a2c48"), 1))
        painter.drawRoundedRect(QRectF(x, y, block_width, block_height), 6, 6)
        painter.setPen(QPen(QColor("#ff4d79"), 2))
        painter.drawLine(QPointF(x + 10, y + 26), QPointF(x + 58, y + 26))
        painter.setFont(_font(FONT_MONO, 8, QFont.Weight.Bold))
        painter.setPen(QColor("#ff9db6"))
        painter.drawText(QRectF(x + 10, y + 8, block_width - 20, 16),
                         Qt.AlignmentFlag.AlignLeft, title)
        painter.setFont(_font(FONT_MONO, 7))
        for index, (key, value) in enumerate(rows):
            row_y = y + 42 + index * 20
            painter.setPen(QColor("#b0637c"))
            painter.drawText(x + 10, row_y, key)
            painter.setPen(QColor("#f2dde4"))
            painter.drawText(QRectF(x + 62, row_y - 11, block_width - 72, 15),
                             Qt.AlignmentFlag.AlignRight, value[:20])


def apply_ui_accent(accent_hex: str) -> bool:
    """
    Seçilen accent rengine göre tüm turkuaz-ailesi paleti yeniden türetir
    (hue kaydırma — parlaklık/doygunluk oranları korunur, tasarım bozulmaz).
    Boyanan öğeler (HUD, dalga formu, metrikler) bir sonraki karede yeni
    rengi alır; stylesheet tabanlı paneller yeniden kurulduklarında alır.
    """
    import colorsys

    accent_hex = (accent_hex or "").strip().lower()
    if not (accent_hex.startswith("#") and len(accent_hex) == 7):
        return False
    try:
        int(accent_hex[1:], 16)
    except ValueError:
        return False

    def _hsv(h: str) -> tuple[float, float, float]:
        r = int(h[1:3], 16) / 255
        g = int(h[3:5], 16) / 255
        b = int(h[5:7], 16) / 255
        return colorsys.rgb_to_hsv(r, g, b)

    base_h            = _hsv(_PALETTE_DEFAULTS["PRI"])[0]
    acc_h, acc_s, _av = _hsv(accent_hex)
    dh   = acc_h - base_h
    grey = acc_s < 0.08   # griye yakın accent → tüm tema desaturize edilir

    for key, hex0 in _PALETTE_DEFAULTS.items():
        h, s, v = _hsv(hex0)
        if grey:
            s *= 0.15
        r, g, b = colorsys.hsv_to_rgb((h + dh) % 1.0, s, v)
        setattr(C, key, "#{:02x}{:02x}{:02x}".format(
            int(r * 255 + 0.5), int(g * 255 + 0.5), int(b * 255 + 0.5)))
    return True


def current_palette() -> dict[str, str]:
    """C sınıfındaki accent'e bağlı renklerin anlık kopyası."""
    return {k: getattr(C, k) for k in _HUE_LINKED}


def retheme_all_widgets(old: dict[str, str], new: dict[str, str]) -> None:
    """
    CANLI tam tema değişimi. Uygulamadaki HER widget'ın stylesheet'inde eski
    palet renklerini yenileriyle değiştirir ve yeniden çizdirir. Böylece renk
    değişimi yalnızca boyanan öğelerde değil, panel/buton/kenarlık dahil tüm
    arayüzde ANINDA uygulanır — yeniden başlatma gerekmez.
    """
    mapping = {old[k].lower(): new[k].lower()
               for k in old if old[k].lower() != new.get(k, old[k]).lower()}
    if not mapping:
        return
    app = QApplication.instance()
    if app is None:
        return
    for w in app.allWidgets():
        try:
            ss = w.styleSheet()
            if ss:
                s2 = ss
                for o, n in mapping.items():
                    if o in s2:
                        s2 = s2.replace(o, n)
                if s2 != ss:
                    w.setStyleSheet(s2)
            w.update()
        except Exception:
            pass


def qcol(h: str, a: int = 255) -> QColor:
    c = QColor(h); c.setAlpha(a); return c


# ── Windows GPU via NVML DLL (no subprocess, no console window) ──────────────
_nvml_lib: object = None   # cached ctypes DLL
_nvml_ok:  object = None   # None=untested, True=works, False=unavailable


def _nvml_gpu_windows() -> float:
    """Return NVIDIA GPU utilisation % using nvml.dll directly — zero subprocess."""
    global _nvml_lib, _nvml_ok
    if _nvml_ok is False:
        return -1.0
    try:
        import ctypes

        class _Util(ctypes.Structure):
            _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]

        if _nvml_lib is None:
            for dll_name in ("nvml", r"C:\Windows\System32\nvml.dll"):
                try:
                    lib = ctypes.WinDLL(dll_name)
                    lib.nvmlInit_v2()
                    _nvml_lib = lib
                    break
                except Exception:
                    continue

        if _nvml_lib is None:
            import pynvml  # type: ignore
            pynvml.nvmlInit()
            h = pynvml.nvmlDeviceGetHandleByIndex(0)
            _nvml_ok = True
            return float(pynvml.nvmlDeviceGetUtilizationRates(h).gpu)

        dev = ctypes.c_void_p()
        _nvml_lib.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(dev))
        util = _Util()
        _nvml_lib.nvmlDeviceGetUtilizationRates(dev, ctypes.byref(util))
        _nvml_ok = True
        return float(util.gpu)
    except Exception:
        _nvml_ok = False
        return -1.0


class _SysMetrics:
    def __init__(self):
        self.cpu  = 0.0
        self.mem  = 0.0
        self.net  = 0.0   
        self.gpu  = -1.0  
        self.tmp  = -1.0  
        self._lock = threading.Lock()
        self._last_net = psutil.net_io_counters()
        self._last_net_t = time.time()
        self._running = True
        t = threading.Thread(target=self._loop, daemon=True)
        t.start()

    def _loop(self):
        while self._running:
            try:
                self._update()
            except Exception:
                pass
            time.sleep(1.5)

    def _update(self):
        cpu = psutil.cpu_percent(interval=None)
        mem = psutil.virtual_memory().percent

        nc  = psutil.net_io_counters()
        now = time.time()
        dt  = now - self._last_net_t
        if dt > 0:
            sent = (nc.bytes_sent - self._last_net.bytes_sent) / dt
            recv = (nc.bytes_recv - self._last_net.bytes_recv) / dt
            net  = (sent + recv) / (1024 * 1024)
        else:
            net = 0.0
        self._last_net   = nc
        self._last_net_t = now

        gpu = self._get_gpu()

        tmp = self._get_temp()

        with self._lock:
            self.cpu = cpu
            self.mem = mem
            self.net = net
            self.gpu = gpu
            self.tmp = tmp

    def _get_gpu(self) -> float:
        # pynvml — subprocess-free, works on all platforms if installed
        try:
            import pynvml  # type: ignore
            pynvml.nvmlInit()
            h = pynvml.nvmlDeviceGetHandleByIndex(0)
            return float(pynvml.nvmlDeviceGetUtilizationRates(h).gpu)
        except Exception:
            pass

        # Windows: nvml.dll via ctypes (already cached in _nvml_gpu_windows)
        if _OS == "Windows":
            return _nvml_gpu_windows()

        # Linux / macOS: libnvidia-ml shared lib via ctypes
        try:
            import ctypes
            _lib = "libnvidia-ml.so.1" if _OS == "Linux" else "libnvidia-ml.dylib"

            class _Util(ctypes.Structure):
                _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]

            nv = ctypes.CDLL(_lib)
            nv.nvmlInit_v2()
            dev = ctypes.c_void_p()
            nv.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(dev))
            u = _Util()
            nv.nvmlDeviceGetUtilizationRates(dev, ctypes.byref(u))
            return float(u.gpu)
        except Exception:
            pass

        return -1.0   # N/A — zero subprocess on all platforms

    def _get_temp(self) -> float:
        # psutil — works on Linux; occasionally Windows with driver support
        try:
            temps = psutil.sensors_temperatures()
            for name in ["coretemp", "k10temp", "cpu_thermal", "acpitz",
                         "cpu-thermal", "zenpower", "it8688"]:
                if name in temps and temps[name]:
                    return temps[name][0].current
            for entries in temps.values():
                if entries:
                    return entries[0].current
        except Exception:
            pass

        # Windows: wmi module (pure Python COM, zero subprocess)
        if _OS == "Windows":
            try:
                import wmi  # type: ignore
                w = wmi.WMI(namespace="root/wmi")
                tz = w.MSAcpi_ThermalZoneTemperature()
                if tz:
                    return (tz[0].CurrentTemperature / 10.0) - 273.15
            except Exception:
                pass

        return -1.0   # N/A — zero subprocess on all platforms

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "cpu": self.cpu,
                "mem": self.mem,
                "net": self.net,
                "gpu": self.gpu,
                "tmp": self.tmp,
            }


_metrics = _SysMetrics()

class HudCanvas(QWidget):
    def __init__(self, face_path: str, assistant_name: str = "Mehmet", parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setMinimumSize(300, 300)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self.muted    = False
        self.speaking = False
        self.state    = "INITIALISING"
        self._assistant_name = assistant_name

        self._tick       = 0
        self._scale      = 1.0
        self._tgt_scale  = 1.0
        self._halo       = 55.0
        self._tgt_halo   = 55.0
        self._last_t     = time.time()
        self._scan       = 0.0
        self._scan2      = 180.0
        self._rings      = [0.0, 120.0, 240.0]
        self._pulses: list[float] = [0.0, 50.0, 100.0]
        self._blink      = True
        self._blink_tick = 0
        self._particles: list[list[float]] = []
        self._face_px: QPixmap | None = None
        self._load_face(face_path)

        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self._step)
        self._tmr.start(16)

    def _load_face(self, path: str):
        try:
            from PIL import Image, ImageDraw
            import io
            img = Image.open(path).convert("RGBA")
            sz  = min(img.size)
            img = img.resize((sz, sz), Image.LANCZOS)
            mk  = Image.new("L", (sz, sz), 0)
            ImageDraw.Draw(mk).ellipse((2, 2, sz - 2, sz - 2), fill=255)
            img.putalpha(mk)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            px = QPixmap(); px.loadFromData(buf.getvalue())
            self._face_px = px
        except Exception:
            self._face_px = None

    def _step(self):
        self._tick += 1
        now = time.time()
        if now - self._last_t > (0.12 if self.speaking else 0.5):
            if self.speaking:
                self._tgt_scale = random.uniform(1.06, 1.14)
                self._tgt_halo  = random.uniform(145, 190)
            elif self.muted:
                self._tgt_scale = random.uniform(0.998, 1.002)
                self._tgt_halo  = random.uniform(15, 28)
            else:
                self._tgt_scale = random.uniform(1.001, 1.008)
                self._tgt_halo  = random.uniform(48, 68)
            self._last_t = now

        sp = 0.38 if self.speaking else 0.15
        self._scale += (self._tgt_scale - self._scale) * sp
        self._halo  += (self._tgt_halo  - self._halo)  * sp

        speeds = [1.3, -0.9, 2.0] if self.speaking else [0.55, -0.35, 0.9]
        for i, spd in enumerate(speeds):
            self._rings[i] = (self._rings[i] + spd) % 360

        self._scan  = (self._scan  + (3.0 if self.speaking else 1.3)) % 360
        self._scan2 = (self._scan2 + (-2.0 if self.speaking else -0.75)) % 360

        fw  = min(self.width(), self.height())
        lim = fw * 0.74
        spd = 4.2 if self.speaking else 2.0
        self._pulses = [r + spd for r in self._pulses if r + spd < lim]
        if len(self._pulses) < 3 and random.random() < (0.07 if self.speaking else 0.025):
            self._pulses.append(0.0)

        if self.speaking and random.random() < 0.28:
            cx, cy = self.width() / 2, self.height() / 2
            ang = random.uniform(0, 2 * math.pi)
            r_s = fw * 0.28
            self._particles.append([
                cx + math.cos(ang) * r_s, cy + math.sin(ang) * r_s,
                math.cos(ang) * random.uniform(0.9, 2.4),
                math.sin(ang) * random.uniform(0.9, 2.4) - 0.4, 1.0,
            ])
        self._particles = [
            [p[0]+p[2], p[1]+p[3], p[2]*0.97, p[3]*0.97, p[4]-0.028]
            for p in self._particles if p[4] > 0
        ]

        self._blink_tick += 1
        if self._blink_tick >= 38:
            self._blink = not self._blink
            self._blink_tick = 0
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), qcol(C.BG))

        W, H = self.width(), self.height()
        cx, cy = W / 2, H / 2
        fw = min(W, H)

        # grid dots
        p.setPen(QPen(qcol(C.PRI_GHO), 1))
        for x in range(0, W, 48):
            for y in range(0, H, 48):
                p.drawPoint(x, y)

        r_face = fw * 0.31

        # halo glow
        for i in range(10):
            r   = r_face * (1.8 - i * 0.08)
            frc = 1.0 - i / 10
            a   = max(0, min(255, int(self._halo * 0.085 * frc)))
            col = qcol(C.MUTED_C if self.muted else C.PRI, a)
            p.setPen(QPen(col, 1.5)); p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QRectF(cx - r, cy - r, r * 2, r * 2))

        # pulse rings
        for pr in self._pulses:
            a   = max(0, int(230 * (1.0 - pr / (fw * 0.74))))
            col = qcol(C.MUTED_C if self.muted else C.PRI, a)
            p.setPen(QPen(col, 1.5)); p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QRectF(cx - pr, cy - pr, pr * 2, pr * 2))

        # spinning arc rings
        for idx, (r_frac, w_r, arc_l, gap) in enumerate(
            [(0.48, 3, 115, 78), (0.40, 2, 78, 55), (0.32, 1, 56, 40)]
        ):
            ring_r = fw * r_frac
            base   = self._rings[idx]
            a_val  = max(0, min(255, int(self._halo * (1.0 - idx * 0.18))))
            col    = qcol(C.MUTED_C if self.muted else C.PRI, a_val)
            p.setPen(QPen(col, w_r)); p.setBrush(Qt.BrushStyle.NoBrush)
            angle = base
            rect  = QRectF(cx - ring_r, cy - ring_r, ring_r * 2, ring_r * 2)
            while angle < base + 360:
                p.drawArc(rect, int(angle * 16), int(arc_l * 16))
                angle += arc_l + gap

        # scanners
        sr = fw * 0.50
        sa = min(255, int(self._halo * 1.5))
        ex = 75 if self.speaking else 44
        p.setPen(QPen(qcol(C.MUTED_C if self.muted else C.PRI, sa), 2.5))
        p.setBrush(Qt.BrushStyle.NoBrush)
        srect = QRectF(cx - sr, cy - sr, sr * 2, sr * 2)
        p.drawArc(srect, int(self._scan * 16), int(ex * 16))
        p.setPen(QPen(qcol(C.ACC, sa // 2), 1.5))
        p.drawArc(srect, int(self._scan2 * 16), int(ex * 16))

        # tick marks
        t_out, t_in = fw * 0.497, fw * 0.474
        p.setPen(QPen(qcol(C.PRI, 140), 1))
        for deg in range(0, 360, 10):
            rad = math.radians(deg)
            inn = t_in if deg % 30 == 0 else t_in + 6
            p.drawLine(
                QPointF(cx + t_out * math.cos(rad), cy - t_out * math.sin(rad)),
                QPointF(cx + inn  * math.cos(rad), cy - inn  * math.sin(rad)),
            )

        # crosshair
        ch_r, gap_h = fw * 0.51, fw * 0.16
        p.setPen(QPen(qcol(C.PRI, int(self._halo * 0.5)), 1))
        p.drawLine(QPointF(cx - ch_r, cy), QPointF(cx - gap_h, cy))
        p.drawLine(QPointF(cx + gap_h, cy), QPointF(cx + ch_r, cy))
        p.drawLine(QPointF(cx, cy - ch_r), QPointF(cx, cy - gap_h))
        p.drawLine(QPointF(cx, cy + gap_h), QPointF(cx, cy + ch_r))

        # corner brackets
        bl = 24
        bc = qcol(C.PRI, 210)
        hl, hr = cx - fw // 2, cx + fw // 2
        ht, hb = cy - fw // 2, cy + fw // 2
        p.setPen(QPen(bc, 2))
        for bx, by, dx, dy in [(hl,ht,1,1),(hr,ht,-1,1),(hl,hb,1,-1),(hr,hb,-1,-1)]:
            p.drawLine(QPointF(bx, by), QPointF(bx + dx * bl, by))
            p.drawLine(QPointF(bx, by), QPointF(bx, by + dy * bl))

        # face
        if self._face_px:
            fsz    = int(fw * 0.62 * self._scale)
            scaled = self._face_px.scaled(
                fsz, fsz,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            p.drawPixmap(int(cx - fsz / 2), int(cy - fsz / 2), scaled)
        else:
            orb_r = int(fw * 0.27 * self._scale)
            oc    = (200, 0, 50) if self.muted else (0, 60, 110)
            for i in range(8, 0, -1):
                r2  = int(orb_r * i / 8)
                frc = i / 8
                a   = max(0, min(255, int(self._halo * 1.1 * frc)))
                p.setBrush(QBrush(QColor(int(oc[0]*frc), int(oc[1]*frc), int(oc[2]*frc), a)))
                p.setPen(Qt.PenStyle.NoPen)
                p.drawEllipse(QRectF(cx - r2, cy - r2, r2 * 2, r2 * 2))
            p.setPen(QPen(qcol(C.PRI, min(255, int(self._halo * 2))), 1))
            p.setFont(_font(FONT_DISPLAY, 15, QFont.Weight.DemiBold))
            p.drawText(QRectF(cx - 80, cy - 14, 160, 28),
                       Qt.AlignmentFlag.AlignCenter, self._assistant_name)

        # particles
        for pt in self._particles:
            a = max(0, min(255, int(pt[4] * 255)))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(qcol(C.PRI, a)))
            p.drawEllipse(QPointF(pt[0], pt[1]), 2.5, 2.5)

        # durum metni (Türkçe)
        sy = cy + fw * 0.40
        if self.muted:
            txt, col = "⊘  MİKROFON SESSİZ", qcol(C.MUTED_C)
        elif self.speaking:
            txt, col = "●  KONUŞUYOR", qcol(C.ACC)
        elif self.state == "THINKING":
            sym = "◈" if self._blink else "◇"
            txt, col = f"{sym}  DÜŞÜNÜYOR", qcol(C.ACC2)
        elif self.state == "PROCESSING":
            sym = "▷" if self._blink else "▶"
            txt, col = f"{sym}  İŞLİYOR", qcol(C.ACC2)
        elif self.state in ("LISTENING", "DİNLİYOR", "Dinliyor"):
            sym = "●" if self._blink else "○"
            txt, col = f"{sym}  DİNLİYOR", qcol(C.GREEN)
        elif self.state == "SLEEPING":
            sym = "☾" if self._blink else "☽"
            txt, col = f"{sym}  UYKU MODU", qcol(C.TEXT_DIM)
        else:
            sym = "●" if self._blink else "○"
            txt, col = f"{sym}  {_tr_state(self.state)}", qcol(C.PRI)

        p.setPen(QPen(col, 1))
        p.setFont(_font(FONT_DISPLAY, 12, QFont.Weight.DemiBold))
        p.drawText(QRectF(0, sy, W, 26), Qt.AlignmentFlag.AlignCenter, txt)

        # waveform
        wy = sy + 30
        N, bw = 36, 8
        wx0 = (W - N * bw) / 2
        for i in range(N):
            if self.muted:
                hgt, cl = 2, qcol(C.MUTED_C)
            elif self.speaking:
                hgt = random.randint(3, 20)
                cl  = qcol(C.PRI) if hgt > 12 else qcol(C.PRI_DIM)
            else:
                hgt = int(3 + 2 * math.sin(self._tick * 0.09 + i * 0.6))
                cl  = qcol(C.BORDER_B)
            p.fillRect(QRectF(wx0 + i * bw, wy + 20 - hgt, bw - 1, hgt), cl)

class MetricBar(QWidget):
    """JARVIS tarzı telemetri çubuğu: gradyan dolgu, parlama ucu ve kadran çizgileri."""

    def __init__(self, label: str, color: str = C.PRI, parent=None):
        super().__init__(parent)
        self._label = label
        self._color = color
        self._value = 0.0       # 0–100
        self._text  = "--"
        self.setFixedHeight(44)
        self.setMinimumWidth(80)

    def set_value(self, pct: float, text: str):
        self._value = max(0.0, min(100.0, pct))
        self._text  = text
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()

        # gövde
        p.setBrush(QBrush(qcol(C.PANEL2)))
        p.setPen(QPen(qcol(C.BORDER_A), 1))
        p.drawRoundedRect(QRectF(1, 1, W - 2, H - 2), 6, 6)

        bar_h   = 7
        bar_y   = H - bar_h - 6
        bar_w   = W - 12
        bar_x   = 6
        fill_w  = bar_w * self._value / 100

        p.setBrush(QBrush(qcol(C.BAR_BG)))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(QRectF(bar_x, bar_y, bar_w, bar_h), 3, 3)

        # kadran çizgileri (arka planda ince tık işaretleri)
        p.setPen(QPen(qcol(C.BORDER, 130), 1))
        for i in range(1, 10):
            tx = bar_x + bar_w * i / 10
            p.drawLine(QPointF(tx, bar_y), QPointF(tx, bar_y + bar_h))

        if self._value > 85:
            bar_col = qcol(C.RED)
        elif self._value > 65:
            bar_col = qcol(C.ACC)
        else:
            bar_col = qcol(self._color)

        if fill_w > 1:
            grad = QLinearGradient(QPointF(bar_x, 0), QPointF(bar_x + fill_w, 0))
            c0 = QColor(bar_col); c0.setAlpha(90)
            grad.setColorAt(0.0, c0)
            grad.setColorAt(1.0, bar_col)
            p.setBrush(QBrush(grad))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(QRectF(bar_x, bar_y, fill_w, bar_h), 3, 3)
            # parlama ucu
            p.setBrush(QBrush(QColor(255, 255, 255, 150)))
            p.drawRoundedRect(QRectF(bar_x + fill_w - 2.5, bar_y, 2.5, bar_h), 1, 1)

        # etiket + değer
        p.setFont(_font(FONT_MONO, 9, QFont.Weight.DemiBold))
        p.setPen(QPen(qcol(C.TEXT_MED), 1))
        p.drawText(QRectF(9, 6, 60, 16), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self._label)

        p.setFont(_font(FONT_DISPLAY, 12, QFont.Weight.DemiBold))
        p.setPen(QPen(bar_col if self._text != "--" else qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(0, 3, W - 9, 18), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, self._text)

class LogWidget(QTextEdit):
    _sig = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setFont(_font(FONT_MONO, 10))
        self.setStyleSheet(f"""
            QTextEdit {{
                background: {C.PANEL};
                color: {C.TEXT};
                border: 1px solid {C.BORDER};
                border-radius: 10px;
                padding: 10px;
                selection-background-color: {C.PRI_GHO};
            }}
            QScrollBar:vertical {{
                background: {C.BG};
                width: 8px;
                border: none;
            }}
            QScrollBar::handle:vertical {{
                background: {C.BORDER_B};
                border-radius: 4px;
                min-height: 20px;
            }}
        """)
        self._queue: list[str] = []
        self._typing  = False
        self._text    = ""
        self._disp    = ""   # ekranda gösterilen (Türkçeleştirilmiş) metin
        self._pos     = 0
        self._tag     = "sys"
        self._ai_name_lc = "Mehmet"   # updated when assistant name changes
        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self._step)
        self._sig.connect(self._enqueue)

    def append_log(self, text: str):
        self._sig.emit(text)

    def _enqueue(self, text: str):
        self._queue.append(text)
        if not self._typing:
            self._next()

    def _next(self):
        if not self._queue:
            self._typing = False
            return
        self._typing = True
        self._text   = self._queue.pop(0)
        self._pos    = 0
        tl = self._text.lower()
        _ai_pfx = f"{self._ai_name_lc}:"
        if   tl.startswith("you:"):                              self._tag = "you"
        elif tl.startswith(_ai_pfx) or tl.startswith("Mehmet:"): self._tag = "ai"
        elif tl.startswith("file:") or tl.startswith("dosya:"):  self._tag = "file"
        elif tl.startswith("err:") or tl.startswith("hata:"):    self._tag = "err"
        else:                                                    self._tag = "sys"
        # Ekranda Türkçe ön ek göster (etiketleme orijinal metinle yapılır)
        if   self._tag == "you" and tl.startswith("you:"):
            self._disp = "Sen: " + self._text[4:].lstrip()
        elif self._tag == "sys" and tl.startswith("sys:"):
            self._disp = "SİSTEM: " + self._text[4:].lstrip()
        elif self._tag == "err" and tl.startswith("err:"):
            self._disp = "HATA: " + self._text[4:].lstrip()
        elif self._tag == "file" and tl.startswith("file:"):
            self._disp = "DOSYA: " + self._text[5:].lstrip()
        else:
            self._disp = self._text
        self._tmr.start(6)

    def _step(self):
        if self._pos < len(self._text):
            ch  = self._disp[self._pos]
            cur = self.textCursor()
            fmt = cur.charFormat()
            col = {
                "you":  qcol(C.WHITE),
                "ai":   qcol(C.PRI),
                "err":  qcol(C.RED),
                "file": qcol(C.GREEN),
                "sys":  qcol(C.ACC2),
            }.get(self._tag, qcol(C.TEXT))
            fmt.setForeground(QBrush(col))
            cur.movePosition(cur.MoveOperation.End)
            cur.insertText(ch, fmt)
            self.setTextCursor(cur)
            self.ensureCursorVisible()
            self._pos += 1
        else:
            self._tmr.stop()
            cur = self.textCursor()
            cur.movePosition(cur.MoveOperation.End)
            cur.insertText("\n")
            self.setTextCursor(cur)
            self.ensureCursorVisible()
            QTimer.singleShot(20, self._next)

_FILE_ICONS = {
    "image":   ("🖼", "#00d4ff"), "video":   ("🎬", "#ff6b00"),
    "audio":   ("🎵", "#cc44ff"), "pdf":     ("📄", "#ff4444"),
    "word":    ("📝", "#4488ff"), "excel":   ("📊", "#44bb44"),
    "code":    ("💻", "#ffcc00"), "archive": ("📦", "#ff8844"),
    "pptx":    ("📊", "#ff6622"), "text":    ("📃", "#aaaaaa"),
    "data":    ("🔧", "#88ddff"), "unknown": ("📎", "#888888"),
}
_EXT_TO_CAT = {
    **dict.fromkeys(["jpg","jpeg","png","gif","webp","bmp","tiff","svg","ico"], "image"),
    **dict.fromkeys(["mp4","avi","mov","mkv","wmv","flv","webm","m4v"],         "video"),
    **dict.fromkeys(["mp3","wav","ogg","m4a","aac","flac","wma","opus"],        "audio"),
    **dict.fromkeys(["pdf"],                                                     "pdf"),
    **dict.fromkeys(["doc","docx"],                                              "word"),
    **dict.fromkeys(["xls","xlsx","ods"],                                        "excel"),
    **dict.fromkeys(["ppt","pptx"],                                              "pptx"),
    **dict.fromkeys(["py","js","ts","jsx","tsx","html","css","java","c","cpp",
                     "cs","go","rs","rb","php","swift","kt","sh","sql","lua"],   "code"),
    **dict.fromkeys(["zip","rar","tar","gz","7z","bz2","xz"],                   "archive"),
    **dict.fromkeys(["txt","md","rst","log"],                                    "text"),
    **dict.fromkeys(["csv","tsv","json","xml"],                                  "data"),
}

def _file_category(path: Path) -> str:
    return _EXT_TO_CAT.get(path.suffix.lower().lstrip("."), "unknown")

def _fmt_size(size: int) -> str:
    if   size < 1024:    return f"{size} B"
    elif size < 1024**2: return f"{size/1024:.1f} KB"
    elif size < 1024**3: return f"{size/1024**2:.1f} MB"
    else:                return f"{size/1024**3:.1f} GB"


class FileDropZone(QWidget):
    file_selected = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(100)
        self._current_file: str | None = None
        self._hovering  = False
        self._drag_over = False
        self._dash_offset = 0.0
        self._anim_tmr = QTimer(self)
        self._anim_tmr.timeout.connect(self._animate)
        self._anim_tmr.start(40)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._canvas = _DropCanvas(self)
        layout.addWidget(self._canvas)

    def _animate(self):
        self._dash_offset = (self._dash_offset + 0.8) % 20
        self._canvas.update()

    def dragEnterEvent(self, e: QDragEnterEvent):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self._drag_over = True; self._canvas.update()

    def dragLeaveEvent(self, e):
        self._drag_over = False; self._canvas.update()

    def dropEvent(self, e: QDropEvent):
        self._drag_over = False
        urls = e.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            if Path(path).is_file():
                self._set_file(path)
        self._canvas.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._browse()

    def enterEvent(self, e):
        self._hovering = True; self._canvas.update()

    def leaveEvent(self, e):
        self._hovering = False; self._canvas.update()

    def current_file(self) -> str | None:
        return self._current_file

    def clear_file(self):
        self._current_file = None; self._canvas.update()

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Mehmet için dosya seçin", str(Path.home()),
            "Tüm Dosyalar (*.*);;"
            "Görseller (*.jpg *.jpeg *.png *.gif *.webp *.bmp *.svg);;"
            "Belgeler (*.pdf *.docx *.txt *.md *.pptx);;"
            "Veriler (*.csv *.xlsx *.json *.xml);;"
            "Kod (*.py *.js *.ts *.html *.css *.java *.cpp *.go);;"
            "Ses (*.mp3 *.wav *.ogg *.m4a *.aac *.flac);;"
            "Videolar (*.mp4 *.avi *.mov *.mkv *.wmv *.webm);;"
            "Arşivler (*.zip *.rar *.tar *.gz *.7z)",
        )
        if path:
            self._set_file(path)

    def _set_file(self, path: str):
        self._current_file = path
        self._canvas.update()
        self.file_selected.emit(path)


class _DropCanvas(QWidget):
    def __init__(self, zone: FileDropZone):
        super().__init__(zone)
        self._z = zone

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        z    = self._z
        W, H = self.width(), self.height()
        pad  = 6
        rect = QRectF(pad, pad, W - pad * 2, H - pad * 2)

        bg_col = qcol("#241a04" if z._drag_over else ("#1a1305" if z._hovering else C.PANEL))
        p.setBrush(QBrush(bg_col)); p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(rect, 6, 6)

        if z._current_file:   border_col = qcol(C.GREEN, 200)
        elif z._drag_over:    border_col = qcol(C.PRI, 230)
        elif z._hovering:     border_col = qcol(C.BORDER_B, 200)
        else:                 border_col = qcol(C.BORDER, 160)

        pen = QPen(border_col, 1.5, Qt.PenStyle.DashLine)
        pen.setDashOffset(z._dash_offset)
        p.setPen(pen); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(rect, 6, 6)

        if z._current_file:   self._paint_file(p, W, H)
        elif z._drag_over:    self._paint_drag_over(p, W, H)
        else:                 self._paint_idle(p, W, H, z._hovering)

    def _paint_idle(self, p, W, H, hover):
        cx, cy = W / 2, H / 2
        col = qcol(C.PRI_DIM if not hover else C.PRI)
        p.setPen(QPen(col, 2)); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawLine(QPointF(cx, cy - 14), QPointF(cx, cy + 4))
        p.drawLine(QPointF(cx - 8, cy - 6), QPointF(cx, cy - 14))
        p.drawLine(QPointF(cx + 8, cy - 6), QPointF(cx, cy - 14))
        p.drawLine(QPointF(cx - 14, cy + 4), QPointF(cx + 14, cy + 4))
        p.setFont(_font(FONT_UI, 11, QFont.Weight.DemiBold))
        p.setPen(QPen(qcol(C.PRI_DIM if not hover else C.TEXT), 1))
        p.drawText(QRectF(0, cy + 8, W, 18), Qt.AlignmentFlag.AlignCenter,
                   "Dosyayı buraya bırakın veya seçmek için tıklayın")
        p.setFont(_font(FONT_MONO, 8))
        p.setPen(QPen(qcol("#5a4620"), 1))
        p.drawText(QRectF(0, cy + 26, W, 15), Qt.AlignmentFlag.AlignCenter,
                   "Görsel · Video · Ses · PDF · Belge · Kod · Veri")

    def _paint_drag_over(self, p, W, H):
        cx, cy = W / 2, H / 2
        p.setFont(QFont(FONT_DISPLAY, 20))
        p.setPen(QPen(qcol(C.PRI), 1))
        p.drawText(QRectF(0, cy - 24, W, 32), Qt.AlignmentFlag.AlignCenter, "⬇")
        p.setFont(_font(FONT_UI, 11, QFont.Weight.DemiBold))
        p.setPen(QPen(qcol(C.PRI), 1))
        p.drawText(QRectF(0, cy + 12, W, 18), Qt.AlignmentFlag.AlignCenter, "Yüklemek için bırakın")

    def _paint_file(self, p, W, H):
        path = Path(self._z._current_file)
        cat  = _file_category(path)
        icon, icon_col = _FILE_ICONS.get(cat, _FILE_ICONS["unknown"])
        size_str = _fmt_size(path.stat().st_size)
        ext_str  = path.suffix.upper().lstrip(".") or "FILE"

        block_x, block_w = 10, 60
        p.setFont(QFont("Segoe UI Emoji", 22) if _OS == "Windows" else QFont("Arial", 22))
        p.setPen(QPen(qcol(icon_col), 1))
        p.drawText(QRectF(block_x, 0, block_w, H), Qt.AlignmentFlag.AlignCenter, icon)

        tx = block_x + block_w + 6
        tw = W - tx - 38

        p.setFont(_font(FONT_MONO, 10, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.WHITE), 1))
        name = path.name if len(path.name) <= 34 else path.name[:31] + "..."
        p.drawText(QRectF(tx, H * 0.14, tw, 18),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, name)

        p.setFont(_font(FONT_MONO, 9))
        p.setPen(QPen(qcol(C.TEXT_MED), 1))
        p.drawText(QRectF(tx, H * 0.14 + 19, tw, 16),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   f"{ext_str}  ·  {size_str}")

        p.setFont(_font(FONT_MONO, 8))
        p.setPen(QPen(qcol("#5a4620"), 1))
        par = str(path.parent)
        if len(par) > 42: par = "…" + par[-41:]
        p.drawText(QRectF(tx, H * 0.14 + 36, tw, 14),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, par)

        p.setFont(_font(FONT_DISPLAY, 12, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.RED, 180), 1))
        p.drawText(QRectF(W - 34, 0, 28, H), Qt.AlignmentFlag.AlignCenter, "✕")

    def mousePressEvent(self, e):
        z = self._z
        if z._current_file and e.pos().x() > self.width() - 34:
            z.clear_file()
        else:
            z.mousePressEvent(e)


class _CameraPreview(QWidget):
    """Floating overlay that briefly shows what the camera captured."""

    _W, _H = 244, 188

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            _CameraPreview {{
                background: rgba(7, 5, 2, 242);
                border: 1px solid {C.PRI};
                border-radius: 6px;
            }}
        """)
        self.setFixedWidth(self._W)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 5, 6, 6)
        lay.setSpacing(4)

        hdr = QHBoxLayout()
        title = QLabel("◈  GÖRSEL GİRİŞ")
        title.setFont(_font(FONT_MONO, 9, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        hdr.addWidget(title)
        hdr.addStretch()
        close_btn = QPushButton("✕")
        close_btn.setFixedSize(18, 18)
        close_btn.setFont(_font(FONT_UI, 10))
        close_btn.setStyleSheet(
            f"color: {C.TEXT_DIM}; background: transparent; border: none;"
        )
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self.hide)
        hdr.addWidget(close_btn)
        lay.addLayout(hdr)

        self._img_lbl = QLabel()
        self._img_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._img_lbl.setStyleSheet("background: transparent;")
        lay.addWidget(self._img_lbl)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

        self.hide()

    def show_frame(self, img_bytes: bytes) -> None:
        px = QPixmap()
        px.loadFromData(img_bytes)
        if not px.isNull():
            max_w = self._W - 12
            scaled = px.scaled(
                max_w, 160,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self._img_lbl.setPixmap(scaled)
            self._img_lbl.setFixedSize(scaled.width(), scaled.height())
            self.adjustSize()
        self.show()
        self.raise_()
        self._timer.start(6_000)   # auto-dismiss after 6 s


class SetupOverlay(QWidget):
    done = pyqtSignal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            SetupOverlay {{
                background: rgba(7, 5, 2, 245);
                border: 1px solid {C.BORDER_B};
                border-radius: 10px;
            }}
        """)

        detected = {"darwin": "mac", "windows": "windows"}.get(
            _OS.lower(), "linux"
        )
        self._sel_os = detected

        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 24, 30, 24)
        layout.setSpacing(8)

        def _lbl(txt, font_size=10, bold=False, color=C.PRI,
                 align=Qt.AlignmentFlag.AlignCenter):
            w = QLabel(txt)
            w.setAlignment(align)
            w.setFont(_font(FONT_UI, font_size,
                            QFont.Weight.Bold if bold else QFont.Weight.Normal))
            w.setStyleSheet(f"color: {color}; background: transparent;")
            return w

        layout.addWidget(_lbl("◈  KURULUM GEREKLİ", 16, True))
        layout.addWidget(_lbl("İlk başlatmadan önce Mehmet'in sistemine bağlanın.", 11, color=C.PRI_DIM))
        layout.addSpacing(6)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER};"); layout.addWidget(sep)
        layout.addSpacing(4)

        layout.addWidget(_lbl("GEMINI API ANAHTARI", 10, color=C.TEXT_DIM,
                               align=Qt.AlignmentFlag.AlignLeft))
        self._key_input = QLineEdit()
        self._key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._key_input.setPlaceholderText("AIza…")
        self._key_input.setFont(_font(FONT_MONO, 11))
        self._key_input.setFixedHeight(36)
        self._key_input.setStyleSheet(f"""
            QLineEdit {{
                background: {C.DARK}; color: {C.TEXT};
                border: 1px solid {C.BORDER}; border-radius: 5px; padding: 5px 10px;
            }}
            QLineEdit:focus {{ border: 1px solid {C.PRI}; }}
        """)
        layout.addWidget(self._key_input)
        layout.addSpacing(12)

        sep2 = QFrame(); sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet(f"color: {C.BORDER};"); layout.addWidget(sep2)
        layout.addSpacing(4)

        layout.addWidget(_lbl("İŞLETİM SİSTEMİ", 10, color=C.TEXT_DIM,
                               align=Qt.AlignmentFlag.AlignLeft))
        det_name = {"windows": "Windows", "mac": "macOS", "linux": "Linux"}[detected]
        layout.addWidget(_lbl(f"Otomatik algılandı: {det_name}", 10, color=C.ACC2,
                               align=Qt.AlignmentFlag.AlignLeft))

        os_row = QHBoxLayout(); os_row.setSpacing(6)
        self._os_btns: dict[str, QPushButton] = {}
        for key, label in [("windows","⊞  Windows"),("mac","  macOS"),("linux","🐧  Linux")]:
            btn = QPushButton(label)
            btn.setFont(_font(FONT_UI, 11, QFont.Weight.Bold))
            btn.setFixedHeight(36)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _, k=key: self._sel(k))
            os_row.addWidget(btn)
            self._os_btns[key] = btn
        layout.addLayout(os_row)
        self._sel(detected)
        layout.addSpacing(12)

        init_btn = QPushButton("▸  SİSTEMİ BAŞLAT")
        init_btn.setFont(_font(FONT_UI, 12, QFont.Weight.Bold))
        init_btn.setFixedHeight(40)
        init_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        init_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 5px;
            }}
            QPushButton:hover {{
                background: {C.PRI_GHO}; border: 1px solid {C.PRI};
            }}
        """)
        init_btn.clicked.connect(self._submit)
        layout.addWidget(init_btn)

    def _sel(self, key: str):
        self._sel_os = key
        pal = {"windows":(C.PRI,"#001a22"),"mac":(C.ACC2,"#1a1400"),"linux":(C.GREEN,"#001a0d")}
        for k, btn in self._os_btns.items():
            if k == key:
                fg, bg = pal[k]
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background: {fg}; color: {bg};
                        border: none; border-radius: 3px; font-weight: bold;
                    }}
                """)
            else:
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background: {C.DARK}; color: {C.TEXT_DIM};
                        border: 1px solid {C.BORDER}; border-radius: 5px;
                    }}
                    QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}
                """)

    def _submit(self):
        key = self._key_input.text().strip()
        if not key:
            self._key_input.setStyleSheet(
                self._key_input.styleSheet() +
                f" QLineEdit {{ border: 1px solid {C.RED}; }}"
            )
            return
        self.done.emit(key, self._sel_os)


class HueWheel(QWidget):
    """
    Dairesel renk seçici. Kullanıcı tutamacı (küçük beyaz daire) çarkın
    çevresinde sürükleyerek TÜM renk tonları arasından seçim yapar.
    Merkezdeki dolu daire seçilen rengin canlı önizlemesidir.
    """

    hue_picked    = pyqtSignal(str)   # sürükleme sırasında (canlı)
    hue_committed = pyqtSignal(str)   # tutamaç bırakıldığında

    _RING = 16   # halka kalınlığı (px)

    def __init__(self, initial_hex: str = DEFAULT_UI_COLOR, parent=None):
        super().__init__(parent)
        self.setFixedSize(148, 148)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hue  = 0.53
        self._drag = False
        self.set_color(initial_hex)

    # ── API ──────────────────────────────────────────────────────────────────
    def color(self) -> str:
        return QColor.fromHsvF(self._hue, 1.0, 1.0).name()

    def set_color(self, hex_str: str):
        c = QColor((hex_str or "").strip())
        if c.isValid() and c.hsvHueF() >= 0:
            self._hue = c.hsvHueF()
            self.update()

    # ── geometri yardımcıları ────────────────────────────────────────────────
    def _ring_rect(self) -> QRectF:
        m = self._RING / 2 + 3
        return QRectF(self.rect()).adjusted(m, m, -m, -m)

    def _hue_from_pos(self, pos: QPointF) -> float:
        c  = QRectF(self.rect()).center()
        dx = pos.x() - c.x()
        dy = c.y() - pos.y()          # ekran y'si aşağı — matematiksel eksene çevir
        ang = math.atan2(dy, dx)      # [-π, π], saat yönünün tersi
        return (ang / (2 * math.pi)) % 1.0

    # ── çizim ────────────────────────────────────────────────────────────────
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect   = self._ring_rect()
        center = rect.center()

        grad = QConicalGradient(center, 0)
        for i in range(0, 361, 20):
            grad.setColorAt(i / 360.0, QColor.fromHsvF((i % 360) / 360.0, 1.0, 1.0))
        p.setPen(QPen(QBrush(grad), self._RING))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(rect)

        # merkez önizleme dairesi
        preview = QColor.fromHsvF(self._hue, 1.0, 1.0)
        inner   = rect.adjusted(30, 30, -30, -30)
        p.setPen(QPen(qcol(C.BORDER_B), 1))
        p.setBrush(QBrush(preview))
        p.drawEllipse(inner)

        # sürüklenen tutamaç
        r   = rect.width() / 2
        ang = self._hue * 2 * math.pi
        hx  = center.x() + r * math.cos(ang)
        hy  = center.y() - r * math.sin(ang)
        p.setPen(QPen(QColor("#00060a"), 2))
        p.setBrush(QBrush(QColor("#ffffff")))
        p.drawEllipse(QPointF(hx, hy), 7.5, 7.5)

    # ── fare ─────────────────────────────────────────────────────────────────
    def mousePressEvent(self, e):
        self._drag = True
        self._hue  = self._hue_from_pos(e.position())
        self.update()
        self.hue_picked.emit(self.color())

    def mouseMoveEvent(self, e):
        if self._drag:
            self._hue = self._hue_from_pos(e.position())
            self.update()
            self.hue_picked.emit(self.color())

    def mouseReleaseEvent(self, e):
        if self._drag:
            self._drag = False
            self.hue_committed.emit(self.color())


class CustomizeOverlay(QWidget):
    """Floating overlay — change assistant name, user name and UI colour."""

    saved = pyqtSignal(str, str, str)   # assistant_name, user_name, ui_color
    _OW, _OH = 400, 500

    def __init__(self, assistant_name="Mehmet", user_name="",
                 ui_color=DEFAULT_UI_COLOR, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            CustomizeOverlay {{
                background: rgba(7, 5, 2, 245);
                border: 1px solid {C.BORDER_B};
                border-radius: 10px;
            }}
        """)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 18, 24, 18)
        lay.setSpacing(8)

        def _lbl(txt, fs=10, bold=False, color=C.PRI, align=Qt.AlignmentFlag.AlignCenter):
            w = QLabel(txt); w.setAlignment(align)
            w.setFont(_font(FONT_UI, fs,
                            QFont.Weight.Bold if bold else QFont.Weight.Normal))
            w.setStyleSheet(f"color: {color}; background: transparent;")
            return w

        _fs = (f"QLineEdit {{ background: {C.DARK}; color: {C.TEXT}; "
               f"border: 1px solid {C.BORDER}; border-radius: 5px; padding: 5px 10px; }}"
               f"QLineEdit:focus {{ border: 1px solid {C.PRI}; }}")

        lay.addWidget(_lbl("⚙  MEHMET'İ KİŞİSELLEŞTİR", 14, True))
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        lay.addWidget(sep)

        lay.addWidget(_lbl("YAPAY ZEKÂ ADI", 10, color=C.TEXT_DIM,
                            align=Qt.AlignmentFlag.AlignLeft))
        self._name_input = QLineEdit(assistant_name)
        self._name_input.setFont(_font(FONT_UI, 12))
        self._name_input.setFixedHeight(34)
        self._name_input.setStyleSheet(_fs)
        lay.addWidget(self._name_input)

        lay.addSpacing(4)
        lay.addWidget(_lbl("SİZİN ADINIZ  (varsayılan hitap için boş bırakın)", 10,
                            color=C.TEXT_DIM, align=Qt.AlignmentFlag.AlignLeft))
        self._user_input = QLineEdit(user_name)
        self._user_input.setPlaceholderText("ör. Baran  (otomatik için boş bırakın)")
        self._user_input.setFont(_font(FONT_UI, 12))
        self._user_input.setFixedHeight(34)
        self._user_input.setStyleSheet(_fs)
        lay.addWidget(self._user_input)

        # ── UI colour — renk çarkı ───────────────────────────────────────────
        lay.addSpacing(4)
        clr_hdr = QHBoxLayout()
        clr_hdr.addWidget(_lbl("ARAYÜZ RENGİ  —  tutamacı sürükleyin", 10,
                               color=C.TEXT_DIM, align=Qt.AlignmentFlag.AlignLeft))
        clr_hdr.addStretch()
        df_btn = QPushButton("VARSAYILAN")
        df_btn.setFixedSize(76, 22)
        df_btn.setFont(_font(FONT_UI, 9, QFont.Weight.Bold))
        df_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        df_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px;
            }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        df_btn.clicked.connect(lambda: self._set_color(DEFAULT_UI_COLOR))
        clr_hdr.addWidget(df_btn)
        lay.addLayout(clr_hdr)

        self._initial_color = (ui_color or DEFAULT_UI_COLOR).strip().lower()
        self._sel_color     = self._initial_color
        self.on_preview     = None   # callable(hex) — canlı önizleme; MainWindow bağlar

        self._wheel = HueWheel(self._sel_color)
        wheel_row = QHBoxLayout()
        wheel_row.addStretch(); wheel_row.addWidget(self._wheel); wheel_row.addStretch()
        lay.addLayout(wheel_row)
        self._wheel.hue_picked.connect(self._on_wheel_pick)
        self._wheel.hue_committed.connect(self._on_wheel_commit)

        self._hex_input = QLineEdit(self._sel_color)
        self._hex_input.setPlaceholderText("#ffb300   (özel hex renk)")
        self._hex_input.setFont(_font(FONT_MONO, 11))
        self._hex_input.setFixedHeight(30)
        self._hex_input.setStyleSheet(_fs)
        self._hex_input.textEdited.connect(self._on_hex_edited)
        lay.addWidget(self._hex_input)

        lay.addSpacing(6)
        btn_row = QHBoxLayout(); btn_row.setSpacing(8)

        save_btn = QPushButton("▸  DEĞİŞİKLİKLERİ UYGULA")
        save_btn.setFixedHeight(36)
        save_btn.setFont(_font(FONT_UI, 11, QFont.Weight.Bold))
        save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        save_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 5px;
            }}
            QPushButton:hover {{ background: {C.PRI_GHO}; border: 1px solid {C.PRI}; }}
        """)
        save_btn.clicked.connect(self._save)
        btn_row.addWidget(save_btn)

        cancel_btn = QPushButton("VAZGEÇ")
        cancel_btn.setFixedHeight(36)
        cancel_btn.setFont(_font(FONT_UI, 11))
        cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 5px;
            }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        cancel_btn.clicked.connect(self._cancel)
        btn_row.addWidget(cancel_btn)
        lay.addLayout(btn_row)

    # ── renk akışı ───────────────────────────────────────────────────────────
    def _set_color(self, hx: str, update_wheel: bool = True, preview: bool = True):
        """Seçili rengi günceller; hex kutusu + çark senkron kalır, tema canlı önizlenir."""
        self._sel_color = hx.strip().lower()
        self._hex_input.blockSignals(True)
        self._hex_input.setText(self._sel_color)
        self._hex_input.blockSignals(False)
        if update_wheel:
            self._wheel.set_color(self._sel_color)
        if preview and self.on_preview:
            self.on_preview(self._sel_color)

    def _on_wheel_pick(self, hx: str):
        # Sürükleme sırasında: hex kutusunu güncelle, temayı henüz uygulama
        self._sel_color = hx
        self._hex_input.blockSignals(True)
        self._hex_input.setText(hx)
        self._hex_input.blockSignals(False)

    def _on_wheel_commit(self, hx: str):
        # Tutamaç bırakıldı → tüm arayüzü canlı önizle
        self._set_color(hx, update_wheel=False)

    def _on_hex_edited(self, text: str):
        t = text.strip().lower()
        if t.startswith("#") and len(t) == 7:
            try:
                int(t[1:], 16)
            except ValueError:
                return
            self._set_color(t, update_wheel=True, preview=True)

    def _cancel(self):
        # Önizleme uygulandıysa açılıştaki renge geri dön
        if self.on_preview and self._sel_color != self._initial_color:
            self.on_preview(self._initial_color)
        self.hide()

    def _save(self):
        name = self._name_input.text().strip() or "Mehmet"
        user = self._user_input.text().strip()
        self.saved.emit(name, user, self._sel_color or DEFAULT_UI_COLOR)
        self.hide()


class ClipboardPanel(QWidget):
    """Floating panel shown when text is copied — offers quick Mehmet actions."""

    action_requested = pyqtSignal(str)
    _W, _H = 326, 112

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            ClipboardPanel {{
                background: rgba(0, 8, 14, 248);
                border: 1px solid {C.BORDER_B};
                border-radius: 6px;
            }}
        """)
        self.setFixedWidth(self._W)
        self._clip_text = ""

        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 7)
        lay.setSpacing(4)

        hdr = QHBoxLayout(); hdr.setSpacing(4)
        icon_lbl = QLabel("◈  PANO METNİ ALGILANDI")
        icon_lbl.setFont(_font(FONT_MONO, 9, QFont.Weight.Bold))
        icon_lbl.setStyleSheet(f"color: {C.ACC2}; background: transparent;")
        hdr.addWidget(icon_lbl); hdr.addStretch()
        x_btn = QPushButton("✕")
        x_btn.setFixedSize(18, 18)
        x_btn.setFont(_font(FONT_UI, 10))
        x_btn.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent; border: none;")
        x_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        x_btn.clicked.connect(self.hide)
        hdr.addWidget(x_btn)
        lay.addLayout(hdr)

        self._preview = QLabel()
        self._preview.setFont(_font(FONT_MONO, 10))
        self._preview.setStyleSheet(f"""
            color: {C.TEXT}; background: {C.PANEL2};
            border: 1px solid {C.BORDER}; border-radius: 4px; padding: 5px 7px;
        """)
        self._preview.setWordWrap(False)
        self._preview.setFixedHeight(30)
        lay.addWidget(self._preview)

        btn_row = QHBoxLayout(); btn_row.setSpacing(4)
        _bs = (f"QPushButton {{ background: {C.PANEL2}; color: {C.TEXT_MED}; "
               f"border: 1px solid {C.BORDER}; border-radius: 4px; }}"
               f"QPushButton:hover {{ color: {C.PRI}; border-color: {C.BORDER_B}; }}")
        for label, cmd_fmt in [
            ("ÇEVİR", "Translate this text to English: {text}"),
            ("ÖZETLE", "Summarise this: {text}"),
            ("AÇIKLA", "Explain this: {text}"),
            ("DÜZELT", "Fix grammar and spelling: {text}"),
        ]:
            b = QPushButton(label)
            b.setFixedHeight(26)
            b.setFont(_font(FONT_UI, 10, QFont.Weight.Bold))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet(_bs)
            b.clicked.connect(lambda _, c=cmd_fmt: self._trigger(c))
            btn_row.addWidget(b)
        lay.addLayout(btn_row)

        self._dismiss_timer = QTimer(self)
        self._dismiss_timer.setSingleShot(True)
        self._dismiss_timer.timeout.connect(self.hide)
        self.hide()

    def _trigger(self, cmd_fmt: str):
        if self._clip_text:
            self.action_requested.emit(cmd_fmt.format(text=self._clip_text[:800]))
        self.hide()

    def show_clipboard(self, text: str):
        self._clip_text = text
        preview = text[:58].replace('\n', ' ')
        if len(text) > 58:
            preview += "…"
        self._preview.setText(f'"{preview}"')
        self.show(); self.raise_()
        self._dismiss_timer.start(8000)


class RemoteKeyOverlay(QWidget):
    """Floating overlay — QR code for instant phone pairing + manual key fallback."""

    closed = pyqtSignal()

    _OW, _OH = 400, 465

    def __init__(self, url: str, key: str, auto_login_url: str = "",
                 manual_url: str = "", expiry_secs: int = 600, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            RemoteKeyOverlay {{
                background: rgba(7, 5, 2, 0.95);
                border: 1px solid {C.BORDER_B};
                border-radius: 14px;
            }}
        """)
        self._expiry          = time.time() + expiry_secs
        self._on_new_key      = None
        self._auto_login_url  = auto_login_url
        self._manual_url      = manual_url or url

        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 16, 24, 16)
        lay.setSpacing(5)

        def _lbl(txt, fs=10, bold=False, color=C.PRI,
                 align=Qt.AlignmentFlag.AlignCenter):
            w = QLabel(txt)
            w.setAlignment(align)
            w.setFont(_font(FONT_UI, fs,
                            QFont.Weight.Bold if bold else QFont.Weight.Normal))
            w.setStyleSheet(f"color: {color}; background: transparent;")
            w.setWordWrap(True)
            return w

        lay.addWidget(_lbl("◈  UZAKTAN ERİŞİM", 14, True))
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 1px 0;")
        lay.addWidget(sep)

        # ── QR code ───────────────────────────────────────────────────────────
        self._qr_label = QLabel()
        self._qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._qr_label.setFixedSize(176, 176)
        self._qr_label.setStyleSheet(
            "background: white; border-radius: 10px; padding: 4px;"
        )
        qr_row = QHBoxLayout()
        qr_row.addStretch()
        qr_row.addWidget(self._qr_label)
        qr_row.addStretch()
        lay.addLayout(qr_row)

        self._update_qr(auto_login_url)

        lay.addWidget(_lbl("Anında bağlanmak için telefon kamerasıyla tarayın", 10, color=C.TEXT_DIM))

        sep2 = QFrame(); sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet(f"color: {C.BORDER}; margin: 1px 0;")
        lay.addWidget(sep2)

        lay.addWidget(_lbl("Ya da elle girin:", 9, color=C.TEXT_DIM,
                           align=Qt.AlignmentFlag.AlignLeft))

        self._url_lbl = QLabel(self._manual_url)
        self._url_lbl.setFont(_font(FONT_MONO, 10))
        self._url_lbl.setStyleSheet(f"color: {C.PRI_DIM}; background: transparent;")
        self._url_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._url_lbl.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self._url_lbl)

        self._key_lbl = QLabel(key)
        self._key_lbl.setFont(_font(FONT_MONO, 26, QFont.Weight.Bold))
        self._key_lbl.setStyleSheet(f"""
            color: {C.ACC};
            background: {C.PANEL2};
            border: 1px solid {C.BORDER_B};
            border-radius: 8px;
            padding: 6px 4px;
            letter-spacing: 10px;
        """)
        self._key_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._key_lbl)

        self._timer_lbl = QLabel()
        self._timer_lbl.setFont(_font(FONT_UI, 10))
        self._timer_lbl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
        self._timer_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._timer_lbl)

        btn_row = QHBoxLayout(); btn_row.setSpacing(8)
        new_btn = QPushButton("YENİ ANAHTAR")
        new_btn.setFixedHeight(34)
        new_btn.setFont(_font(FONT_UI, 10, QFont.Weight.Bold))
        new_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        new_btn.setStyleSheet(f"""
            QPushButton {{
                background: {C.PANEL}; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 5px;
            }}
            QPushButton:hover {{ background: {C.PRI_GHO}; border: 1px solid {C.PRI}; }}
        """)
        new_btn.clicked.connect(self._refresh_key)
        btn_row.addWidget(new_btn)

        close_btn = QPushButton("KAPAT")
        close_btn.setFixedHeight(34)
        close_btn.setFont(_font(FONT_UI, 10, QFont.Weight.Bold))
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 5px;
            }}
            QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}
        """)
        close_btn.clicked.connect(self._do_close)
        btn_row.addWidget(close_btn)
        lay.addLayout(btn_row)

        self._ctimer = QTimer(self)
        self._ctimer.timeout.connect(self._tick)
        self._ctimer.start(1000)
        self._tick()

    def set_new_key_callback(self, fn) -> None:
        self._on_new_key = fn

    def _update_qr(self, url: str) -> None:
        if not url:
            self._qr_label.setText("—")
            return
        try:
            import qrcode as _qrmod
            from io import BytesIO
            qr = _qrmod.QRCode(
                box_size=5, border=2,
                error_correction=_qrmod.constants.ERROR_CORRECT_M,
            )
            qr.add_data(url)
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")
            buf = BytesIO()
            img.save(buf, format="PNG")
            px = QPixmap()
            px.loadFromData(buf.getvalue())
            self._qr_label.setPixmap(
                px.scaled(170, 170,
                          Qt.AspectRatioMode.KeepAspectRatio,
                          Qt.TransformationMode.SmoothTransformation)
            )
        except ImportError:
            self._qr_label.setText("pip install\nqrcode[pil]")
            self._qr_label.setFont(_font(FONT_MONO, 9))
            self._qr_label.setStyleSheet(
                "color: #888; background: white; border-radius: 10px; padding: 4px;"
            )
        except Exception:
            self._qr_label.setText(url[:28])
            self._qr_label.setFont(_font(FONT_MONO, 8))
            self._qr_label.setStyleSheet(
                f"color: {C.PRI}; background: white; border-radius: 10px; padding: 4px;"
            )

    def _tick(self):
        remaining = max(0, int(self._expiry - time.time()))
        m, s = divmod(remaining, 60)
        self._timer_lbl.setText(f"Anahtarın süresi:  {m:02d}:{s:02d}")
        if remaining == 0:
            self._do_close()

    def mark_connected(self) -> None:
        """Call from any thread when a phone successfully connects."""
        self._ctimer.stop()
        self._key_lbl.setText("BAĞLANDI")
        self._key_lbl.setStyleSheet(f"""
            color: {C.GREEN};
            background: rgba(34,197,94,0.08);
            border: 2px solid rgba(34,197,94,0.4);
            border-radius: 8px;
            padding: 6px 4px;
            letter-spacing: 4px;
        """)
        self._qr_label.setText("✓")
        self._qr_label.setFont(_font(FONT_DISPLAY, 48, QFont.Weight.Bold))
        self._qr_label.setStyleSheet(
            "color: #00ff88; background: #001a0d; border-radius: 10px;"
        )
        self._timer_lbl.setText("Telefon bağlandı — Mehmet hazır")
        self._timer_lbl.setStyleSheet(f"color: {C.GREEN}; background: transparent;")

    def _refresh_key(self):
        if self._on_new_key:
            result = self._on_new_key()
            if result:
                url    = result[0]
                key    = result[1]
                auto   = result[2] if len(result) >= 3 else ""
                manual = result[3] if len(result) >= 4 else url
                self._manual_url     = manual or url
                self._url_lbl.setText(self._manual_url)
                self._key_lbl.setText(key)
                self._auto_login_url = auto
                self._update_qr(auto or url)
                self._expiry = time.time() + 600
                self._key_lbl.setStyleSheet(f"""
                    color: {C.ACC};
                    background: {C.PANEL2};
                    border: 1px solid {C.BORDER_B};
                    border-radius: 8px;
                    padding: 6px 4px;
                    letter-spacing: 10px;
                """)
                self._timer_lbl.setStyleSheet(
                    f"color: {C.TEXT_MED}; background: transparent;"
                )
                self._ctimer.start(1000)
                self._tick()

    def _do_close(self):
        self._ctimer.stop()
        self.hide()
        self.closed.emit()


class MainWindow(QMainWindow):
    _log_sig        = pyqtSignal(str)
    _state_sig      = pyqtSignal(str)
    _content_sig    = pyqtSignal(str, str)   # (title, text) — thread-safe content display
    _reconfig_sig   = pyqtSignal()           # trigger setup overlay from any thread
    _camera_sig     = pyqtSignal(bytes)      # show camera frame preview (small overlay)
    _cam_stream_sig = pyqtSignal(bool)       # True=start live stream, False=stop
    _cam_frame_sig  = pyqtSignal(bytes)      # live camera frame → HUD area
    _clipboard_sig  = pyqtSignal(str)        # clipboard text changed (thread-safe)
    _serious_sig    = pyqtSignal(bool)       # toggle serious mode (thread-safe)
    _browser_sig    = pyqtSignal(str)        # NAVİGATÖR komutları (thread-safe)
    _page_text_sig  = pyqtSignal(str)        # sekme metni okuma yanıtı (thread-safe)
    _watch_sig      = pyqtSignal(bool)       # izleme modu rozeti (thread-safe)

    def __init__(self, face_path: str):
        super().__init__()
        self._face_path = face_path
        self.is_serious_mode = False

        # Load customization from config
        _cfg = _read_full_config()
        self._assistant_name: str = (_cfg.get("assistant_name") or "Mehmet").strip()
        _display = self._assistant_name.upper()

        # Kayıtlı UI rengini panel/stylesheet'ler kurulmadan ÖNCE uygula
        # Eski cyan varsayılanı özel seçim sayma — yeni altın varsayılana göçür
        _ui_color = (_cfg.get("ui_color") or "").strip()
        if _ui_color.lower() == _LEGACY_DEFAULT_COLOR:
            _ui_color = ""
        if _ui_color and _ui_color.lower() != DEFAULT_UI_COLOR:
            apply_ui_accent(_ui_color)

        self.setWindowTitle(f"{_display} — BaranT  v{APP_VERSION}")
        self.setMinimumSize(_MIN_W, _MIN_H)
        self.resize(_DEFAULT_W, _DEFAULT_H)

        screen = QApplication.primaryScreen().availableGeometry()
        self.move(
            (screen.width()  - _DEFAULT_W) // 2,
            (screen.height() - _DEFAULT_H) // 2,
        )

        self.on_text_command   = None
        self.on_remote_clicked = None   # callable: () -> (url, key) | None
        self.on_interrupt      = None   # callable: () -> None — stop Mehmet mid-speech
        self._muted            = False
        self._current_file: str | None = None
        self._remote_overlay: RemoteKeyOverlay | None = None
        self._customize_overlay: CustomizeOverlay | None = None
        # NAVİGATÖR (dahili tarayıcı) durumu — tam pencere katmanı
        self._browser = None                     # BrowserPanel (tembel kurulum)
        self._browser_page: QWidget | None = None
        self._browser_open = False
        self._browser_fade: QPropertyAnimation | None = None
        self._browser_curtain = None            # sinematik geçiş perdesi
        self._page_waiters: dict[str, dict] = {}   # okuma istekleri (token→Event)

        central = QWidget()
        central.setStyleSheet(f"background: {C.BG};")
        self._transition_effect = QGraphicsOpacityEffect(central)
        self._transition_effect.setOpacity(1.0)
        central.setGraphicsEffect(self._transition_effect)
        self._transition_anim: QPropertyAnimation | None = None
        self._panel_sequence: QSequentialAnimationGroup | None = None
        self.setCentralWidget(central)

        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self._header = self._build_header()
        root.addWidget(self._header)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        self._left_panel = self._build_left_panel()
        body.addWidget(self._left_panel, stretch=0)

        # Center column: HUD + resizable content panel via QSplitter
        self.hud = HudCanvas(face_path, _display)
        self.hud.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._content_panel = self._build_content_panel()

        # Live camera container — replaces HUD when camera stream is active
        _cam_cont = QWidget()
        _cam_cont.setStyleSheet(f"background: {C.DARK};")
        _cam_v = QVBoxLayout(_cam_cont)
        _cam_v.setContentsMargins(0, 0, 0, 0)
        _cam_v.setSpacing(0)
        _cam_hdr = QHBoxLayout()
        _cam_hdr.setContentsMargins(8, 5, 8, 5)
        _cam_title = QLabel("◈  KAMERA ALANI")
        _cam_title.setFont(_font(FONT_MONO, 10, QFont.Weight.Bold))
        _cam_title.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        _cam_hdr.addWidget(_cam_title)
        _cam_hdr.addStretch()
        _cam_x = QPushButton("✕  KAPAT")
        _cam_x.setFont(_font(FONT_UI, 10, QFont.Weight.Bold))
        _cam_x.setCursor(Qt.CursorShape.PointingHandCursor)
        _cam_x.setStyleSheet(f"""
            QPushButton {{
                color: {C.TEXT_DIM}; background: transparent;
                border: none; padding: 2px 6px;
            }}
            QPushButton:hover {{ color: {C.PRI}; }}
        """)
        _cam_x.clicked.connect(self.stop_camera_stream)
        _cam_hdr.addWidget(_cam_x)
        _cam_v.addLayout(_cam_hdr)
        self._cam_live_lbl = QLabel()
        self._cam_live_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._cam_live_lbl.setStyleSheet("background: transparent;")
        self._cam_live_lbl.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        _cam_v.addWidget(self._cam_live_lbl, stretch=1)

        # MehmetNEO için sinematik operasyon merkezi
        self.serious_canvas = NeoCanvas()

        # Stack: 0 = animated HUD, 1 = live camera, 2 = serious mode canvas
        self._hud_cam_stack = QStackedWidget()
        self._hud_cam_stack.addWidget(self.hud)
        self._hud_cam_stack.addWidget(_cam_cont)
        self._hud_cam_stack.addWidget(self.serious_canvas)

        self._center_split = QSplitter(Qt.Orientation.Vertical)
        self._center_split.setStyleSheet(f"""
            QSplitter::handle {{
                background: {C.BORDER};
                height: 4px;
            }}
            QSplitter::handle:hover {{
                background: {C.PRI_DIM};
            }}
        """)
        self._center_split.addWidget(self._hud_cam_stack)
        self._center_split.addWidget(self._content_panel)
        self._center_split.setStretchFactor(0, 3)
        self._center_split.setStretchFactor(1, 1)
        self._center_split.setCollapsible(0, False)
        body.addWidget(self._center_split, stretch=5)

        self._right_panel = self._build_right_panel()
        body.addWidget(self._right_panel, stretch=0)

        root.addLayout(body, stretch=1)
        self._footer = self._build_footer()
        root.addWidget(self._footer)

        # Quick-access drawer (floating overlay, built after central widget layout is done)
        self._quick_drawer = self._build_quick_drawer()
        self._update_autostart_btn(self._check_autostart())
        from memory.config_manager import get_brief_enabled as _gbe
        self._update_brief_btn(_gbe())

        self._clock_tmr = QTimer(self)
        self._clock_tmr.timeout.connect(self._tick_clock)
        self._clock_tmr.start(1000)
        self._tick_clock()

        # Metrik güncelleme timer'ı
        self._metric_tmr = QTimer(self)
        self._metric_tmr.timeout.connect(self._update_metrics)
        self._metric_tmr.start(2000)
        self._update_metrics()

        self._log_sig.connect(self._log.append_log)
        self._state_sig.connect(self._apply_state)
        self._content_sig.connect(self._show_content)
        self._reconfig_sig.connect(self._show_setup)
        self._camera_sig.connect(self._show_camera_frame)
        self._cam_stream_sig.connect(self._on_cam_stream)
        self._cam_frame_sig.connect(self._on_cam_frame)
        self._clipboard_sig.connect(self._show_clipboard_panel)
        self._serious_sig.connect(self._on_serious_mode_toggle)
        self._browser_sig.connect(self._on_browser_command)
        self._page_text_sig.connect(self._on_page_text)
        self._watch_sig.connect(self._on_watch_badge)
        self._cam_stop = threading.Event()

        # Camera preview overlay (child of central widget, positioned in resizeEvent)
        self._cam_preview = _CameraPreview(self.centralWidget())

        # Clipboard panel (child of central widget, bottom-center)
        self._clipboard_panel = ClipboardPanel(self.centralWidget())
        self._clipboard_panel.action_requested.connect(self._on_clipboard_action)
        QApplication.clipboard().dataChanged.connect(self._on_clipboard_changed)

        self._overlay: SetupOverlay | None = None
        self._ready = self._check_config()
        if not self._ready:
            self._show_setup()

        sc_mute = QShortcut(QKeySequence("F4"), self)
        sc_mute.activated.connect(self._toggle_mute)
        sc_full = QShortcut(QKeySequence("F11"), self)
        sc_full.activated.connect(self._toggle_fullscreen)
        sc_intr = QShortcut(QKeySequence("Escape"), self)
        sc_intr.activated.connect(self._do_interrupt)
        # NAVİGATÖR'ü erken kur (tam pencere katmanı olarak)
        self._ensure_browser()
        sc_br = QShortcut(QKeySequence("F6"), self)
        sc_br.activated.connect(self.toggle_browser)
        # JARVIS açılış perdesi: MEHMET alanı perde arkasından aydınlanır
        QTimer.singleShot(60, lambda: self._play_curtain(
            opening=True, label=self._assistant_name.upper()))
        QTimer.singleShot(160, self._animate_panels)

    def _on_serious_mode_toggle(self, is_serious: bool):
        """Sinematik geçiş: perde kapanır → tema değişir → perde açılır."""
        if is_serious == self.is_serious_mode:
            return
        if self._transition_anim is not None:
            self._transition_anim.stop()

        # 1) JARVIS perdesi: MEHMET alanını kapat (kırmızı, ciddi mod başlığı)
        self._play_curtain(
            opening=False,
            label=("MEHMETNEO" if is_serious else "MEHMET"))

        # 2) perde kapanırken arka planda temayı uygula, sonra perdeyi aç
        def _swap():
            self._apply_serious_mode(is_serious)
            self._play_curtain(opening=True,
                               label=("MEHMETNEO" if is_serious else "MEHMET"))
            # 3) paneller teker teker yeniden aydınlanır
            self._animate_panels()
        QTimer.singleShot(430, _swap)

    def _finish_mode_transition(self, is_serious: bool) -> None:
        self._apply_serious_mode(is_serious)
        fade_in = QPropertyAnimation(self._transition_effect, b"opacity", self)
        fade_in.setDuration(480)
        fade_in.setStartValue(0.0)
        fade_in.setEndValue(1.0)
        fade_in.setEasingCurve(QEasingCurve.Type.OutCubic)
        fade_in.finished.connect(self._animate_panels)
        self._transition_anim = fade_in
        fade_in.start()

    def _animate_panels(self) -> None:
        """Ana yüzeyleri kısa gecikmelerle görünür kılar."""
        if self._panel_sequence is not None:
            self._panel_sequence.stop()
        panels = (self._header, self._left_panel, self._center_split,
                  self._right_panel, self._footer)
        effects: list[QGraphicsOpacityEffect] = []
        for panel in panels:
            effect = QGraphicsOpacityEffect(panel)
            effect.setOpacity(0.0)
            panel.setGraphicsEffect(effect)
            effects.append(effect)

        sequence = QSequentialAnimationGroup(self)
        for effect in effects:
            animation = QPropertyAnimation(effect, b"opacity", sequence)
            animation.setDuration(170)
            animation.setStartValue(0.0)
            animation.setEndValue(1.0)
            animation.setEasingCurve(QEasingCurve.Type.OutCubic)
            sequence.addAnimation(animation)
        self._panel_sequence = sequence
        sequence.start()

    def _apply_serious_mode(self, is_serious: bool):
        """Slot — ciddi mod/normal mod geçişini ana thread'de uygular."""
        self.is_serious_mode = is_serious
        old_palette = current_palette()
        if is_serious:
            # Ciddi mod öncesi paleti hatırla → normal moda dönerken geri yükle
            self._pre_serious_color = getattr(self, "_custom_color", "") or ""
            apply_ui_accent("#ff003c")
            new_palette = current_palette()
            retheme_all_widgets(old_palette, new_palette)
            self.setWindowTitle("MehmetNEO — BaranT Ciddi Mod")
            self._title_lbl.setText("MehmetNEO")
            self._sub_lbl.setText("BARANT // CİDDİ MOD PROTOKOLÜ")
            self._title_lbl.setStyleSheet(f"color: {C.PRI}; background: transparent;")
            self._sub_lbl.setStyleSheet(f"color: {C.PRI_DIM}; background: transparent;")
            self._hud_cam_stack.setCurrentIndex(2)
            self.serious_canvas.set_state(self.hud.state, self.hud.speaking)
            # Ciddi modda NAVİGATÖR kapatılır (tüm dikkat operasyona odaklanır)
            if self._browser_open:
                self.close_browser()
            self._log.append_log("SİSTEM: MehmetNEO ciddi modu etkin.")
            self._log.append_log("SİSTEM: BaranT operasyon arayüzü hazır.")
            QApplication.beep()
        else:
            restore = getattr(self, "_pre_serious_color", "")
            if restore:
                apply_ui_accent(restore)
            else:
                apply_ui_accent(DEFAULT_UI_COLOR)
            new_palette = current_palette()
            retheme_all_widgets(old_palette, new_palette)
            _disp = self._assistant_name.upper()
            self.setWindowTitle(f"{_disp} — BaranT  v{APP_VERSION}")
            self._title_lbl.setText(_disp)
            self._sub_lbl.setText("BARANT // KİŞİSEL YAPAY ZEKÂ")
            self._title_lbl.setStyleSheet(f"color: {C.PRI}; background: transparent;")
            self._sub_lbl.setStyleSheet(f"color: {C.PRI_DIM}; background: transparent;")
            self._hud_cam_stack.setCurrentIndex(0)
            self._log.append_log("SİSTEM: Normal mod etkin. Mehmet çevrimiçi.")

    def _show_camera_frame(self, img_bytes: bytes):
        """Slot — display camera preview overlay (main thread)."""
        self._cam_preview.show_frame(img_bytes)
        cw = self.centralWidget()
        pw = _CameraPreview._W
        ph = self._cam_preview.height()
        self._cam_preview.setGeometry(
            cw.width() - _RIGHT_W - pw - 12,
            cw.height() - ph - 28,
            pw, ph,
        )

    # --- Live camera stream in HUD area ------------------------------------
    def _on_cam_stream(self, start: bool) -> None:
        if start:
            self._hud_cam_stack.setCurrentIndex(1)
        else:
            # Ciddi mod aktifse HUD yerine ciddi mod canvas'a dön
            self._hud_cam_stack.setCurrentIndex(2 if self.is_serious_mode else 0)
            self._cam_live_lbl.clear()

    def _on_cam_frame(self, data: bytes) -> None:
        px = QPixmap()
        px.loadFromData(data)
        if not px.isNull():
            w, h = self._cam_live_lbl.width(), self._cam_live_lbl.height()
            if w > 1 and h > 1:
                self._cam_live_lbl.setPixmap(
                    px.scaled(w, h,
                              Qt.AspectRatioMode.KeepAspectRatio,
                              Qt.TransformationMode.SmoothTransformation)
                )

    def start_camera_stream(self) -> None:
        self._cam_stop.clear()
        self._cam_stream_sig.emit(True)
        t = threading.Thread(target=self._cam_loop, daemon=True, name="cam-stream")
        t.start()

    def _cam_loop(self) -> None:
        try:
            import cv2
            # Reuse camera index detected by screen_processor (cached in api_keys.json)
            cam_idx = 0
            try:
                import json as _j
                cfg = _j.loads((CONFIG_DIR / "api_keys.json").read_text())
                cam_idx = int(cfg.get("camera_index", 0))
            except Exception:
                pass
            try:
                backend = cv2.CAP_DSHOW if _OS == "Windows" else cv2.CAP_ANY
            except AttributeError:
                backend = 0
            cap = cv2.VideoCapture(cam_idx, backend)
            if not cap.isOpened():
                cap = cv2.VideoCapture(0)
            if not cap.isOpened():
                return
            # warm-up frames
            for _ in range(5):
                cap.read()
            while not self._cam_stop.wait(0.033) and cap.isOpened():
                ret, frame = cap.read()
                if ret and frame is not None:
                    _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 65])
                    self._cam_frame_sig.emit(buf.tobytes())
            cap.release()
        except Exception as e:
            print(f"[Camera] Stream error: {e}")
        finally:
            self._cam_stream_sig.emit(False)

    def stop_camera_stream(self) -> None:
        self._cam_stop.set()

    # ------------------------------------------------------------------
    # Icon generation — arc-reactor style, rendered with Pillow
    # ------------------------------------------------------------------
    @staticmethod
    def _build_Mehmet_icon(out_path: Path) -> bool:
        """
        Render a Mehmet arc-reactor icon at 4× resolution and downsample
        for crisp results at all sizes. Saves a multi-res .ico to out_path.
        Returns True on success.
        """
        try:
            import math
            import PIL.Image
            import PIL.ImageDraw
            import PIL.ImageFilter
        except ImportError:
            return False

        CYAN   = (0, 212, 255)
        DIM    = (0, 100, 140)
        DARK   = (0, 6, 10)
        GLOW   = (0, 160, 200)
        WHITE  = (220, 240, 255)

        def _render(sz: int) -> PIL.Image.Image:
            S  = sz * 4                     # draw at 4× then downscale
            img = PIL.Image.new("RGBA", (S, S), (0, 0, 0, 0))
            d   = PIL.ImageDraw.Draw(img)
            cx = cy = S // 2

            # ── filled background circle ──────────────────────────────────
            R = S // 2 - 2
            d.ellipse([cx-R, cy-R, cx+R, cy+R], fill=(*DARK, 255))

            # ── outer border ring ─────────────────────────────────────────
            lw = max(2, S // 40)
            d.ellipse([cx-R, cy-R, cx+R, cy+R],
                      outline=(*CYAN, 220), width=lw)

            # ── mid decorative ring ───────────────────────────────────────
            R2 = int(R * 0.72)
            d.ellipse([cx-R2, cy-R2, cx+R2, cy+R2],
                      outline=(*DIM, 180), width=max(1, lw // 2))

            # ── 6 radial spokes (hex bolt) ────────────────────────────────
            R_inner = int(R * 0.30)
            R_outer = int(R * 0.62)
            spoke_w = max(1, S // 80)
            for i in range(6):
                angle = math.radians(i * 60 - 30)
                x1 = cx + int(R_inner * math.cos(angle))
                y1 = cy + int(R_inner * math.sin(angle))
                x2 = cx + int(R_outer * math.cos(angle))
                y2 = cy + int(R_outer * math.sin(angle))
                d.line([x1, y1, x2, y2], fill=(*GLOW, 200), width=spoke_w)

            # ── 6 tick marks on outer ring ────────────────────────────────
            for i in range(6):
                angle = math.radians(i * 60)
                for dr in range(lw * 2):
                    rx = (R - lw - dr)
                    d.point(
                        [cx + int(rx * math.cos(angle)),
                         cy + int(rx * math.sin(angle))],
                        fill=(*WHITE, 220),
                    )

            # ── inner glowing ring ────────────────────────────────────────
            Ri = int(R * 0.26)
            d.ellipse([cx-Ri, cy-Ri, cx+Ri, cy+Ri],
                      outline=(*CYAN, 255), width=max(2, lw))

            # ── bright glow soft blur applied before core ─────────────────
            # (draw a slightly larger cyan circle on a separate layer)
            glow_layer = PIL.Image.new("RGBA", (S, S), (0, 0, 0, 0))
            gd = PIL.ImageDraw.Draw(glow_layer)
            Rc = int(R * 0.13)
            gd.ellipse([cx-Rc*2, cy-Rc*2, cx+Rc*2, cy+Rc*2],
                       fill=(*CYAN, 110))
            glow_layer = glow_layer.filter(PIL.ImageFilter.GaussianBlur(S // 14))
            img = PIL.Image.alpha_composite(img, glow_layer)
            d   = PIL.ImageDraw.Draw(img)

            # ── core dot ──────────────────────────────────────────────────
            d.ellipse([cx-Rc, cy-Rc, cx+Rc, cy+Rc], fill=(*WHITE, 255))

            # ── downscale to target size ──────────────────────────────────
            return img.resize((sz, sz), PIL.Image.LANCZOS)

        try:
            sizes  = [256, 128, 64, 48, 32, 16]
            frames = [_render(s) for s in sizes]
            frames[0].save(
                out_path,
                format="ICO",
                append_images=frames[1:],
                sizes=[(s, s) for s in sizes],
            )
            return True
        except Exception as e:
            print(f"[Shortcut] ⚠️  Icon generation failed: {e}")
            return False

    @staticmethod
    def _get_desktop_dir() -> Path:
        """
        Resolve the user's REAL desktop directory instead of assuming
        ~/Desktop, which breaks when:
          • OneDrive "Known Folder Move" relocates the desktop
            (C:/Users/x/OneDrive/Desktop) — very common on Win 10/11;
          • the XDG desktop is localized on Linux (~/Masaüstü,
            ~/Schreibtisch, ~/Bureau, …).
        Falls back to ~/Desktop only as a last resort.
        """
        home = Path.home()
        _os = platform.system()

        if _os == "Windows":
            # ── 1) SHGetKnownFolderPath(FOLDERID_Desktop) — the canonical
            #       answer; follows OneDrive redirection. No dependencies. ──
            try:
                import ctypes
                from ctypes import wintypes

                class _GUID(ctypes.Structure):
                    _fields_ = [("Data1", wintypes.DWORD),
                                ("Data2", wintypes.WORD),
                                ("Data3", wintypes.WORD),
                                ("Data4", ctypes.c_ubyte * 8)]

                # FOLDERID_Desktop {B4BFCC3A-DB2C-424C-B029-7FE99A87C641}
                fid = _GUID(0xB4BFCC3A, 0xDB2C, 0x424C,
                            (ctypes.c_ubyte * 8)(0xB0, 0x29, 0x7F, 0xE9,
                                                 0x9A, 0x87, 0xC6, 0x41))
                buf = ctypes.c_wchar_p()
                if ctypes.windll.shell32.SHGetKnownFolderPath(
                        ctypes.byref(fid), 0, None, ctypes.byref(buf)) == 0:
                    p = Path(buf.value)
                    ctypes.windll.ole32.CoTaskMemFree(buf)
                    if p.is_dir():
                        return p
            except Exception:
                pass

            # ── 2) Registry: User Shell Folders (may contain %VARS%) ──────
            try:
                import winreg
                with winreg.OpenKey(
                        winreg.HKEY_CURRENT_USER,
                        r"Software\Microsoft\Windows\CurrentVersion"
                        r"\Explorer\User Shell Folders") as key:
                    val, _t = winreg.QueryValueEx(key, "Desktop")
                p = Path(os.path.expandvars(val))
                if p.is_dir():
                    return p
            except Exception:
                pass

        elif _os == "Linux":
            # ── xdg-user-dir honours localized names (~/Masaüstü, …) ──────
            try:
                out = subprocess.run(["xdg-user-dir", "DESKTOP"],
                                     capture_output=True, text=True, timeout=5)
                p = Path(out.stdout.strip())
                if out.stdout.strip() and p != home and p.is_dir():
                    return p
            except Exception:
                pass
            try:
                cfg = home / ".config" / "user-dirs.dirs"
                for line in cfg.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line.startswith("XDG_DESKTOP_DIR"):
                        val = line.split("=", 1)[1].strip().strip('"')
                        p = Path(val.replace("$HOME", str(home)))
                        if p != home and p.is_dir():
                            return p
            except Exception:
                pass

        # macOS: ~/Desktop is always the real path (localization is
        # display-only). Everything else lands here as a last resort.
        return home / "Desktop"

    @staticmethod
    def _create_lnk_windows(lnk: str, target: str, args: str,
                             work_dir: str, icon_loc: str) -> None:
        """
        Create a Windows .lnk shortcut WITHOUT launching PowerShell or cmd.
        Tries win32com (pywin32) first; falls back to wscript.exe + VBScript.
        wscript.exe is a GUI-mode host — it never opens a console window.
        Raises on failure so the caller can log a useful error.
        """
        # ── Option 1: pywin32 (pure Python COM, zero subprocess) ──────────
        com_err: Exception | None = None
        try:
            from win32com.client import Dispatch   # type: ignore
            sh = Dispatch("WScript.Shell")
            sc = sh.CreateShortCut(lnk)
            sc.TargetPath       = target
            sc.Arguments        = f'"{args}"'
            sc.WorkingDirectory = work_dir
            sc.Description      = "Mehmet AI"
            sc.IconLocation     = icon_loc
            sc.save()
            return
        except ImportError:
            pass
        except Exception as e:            # COM error — still try VBScript
            com_err = e

        # ── Option 2: wscript.exe + VBScript (always available on Windows,
        #    GUI-mode executable — never opens a console window) ────────────
        def q(s: str) -> str:              # escape for a VBScript string literal
            return s.replace('"', '""')

        vbs = "\n".join([
            'On Error Resume Next',
            'Set ws = CreateObject("WScript.Shell")',
            f'Set sc = ws.CreateShortcut("{q(lnk)}")',
            f'sc.TargetPath = "{q(target)}"',
            f'sc.Arguments = Chr(34) & "{q(args)}" & Chr(34)',
            f'sc.WorkingDirectory = "{q(work_dir)}"',
            'sc.Description = "Mehmet"',
            f'sc.IconLocation = "{q(icon_loc)}"',
            'sc.Save',
            'If Err.Number <> 0 Then WScript.Quit 1',
        ])
        import tempfile
        fd, tmp = tempfile.mkstemp(suffix=".vbs")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(vbs)
            proc = subprocess.Popen(
                ["wscript.exe", "/nologo", tmp],
                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW,
            )
            proc.wait(timeout=10)
        finally:
            try:
                os.unlink(tmp)
            except Exception:
                pass

        if not Path(lnk).exists():
            raise RuntimeError(
                f"could not create '{lnk}'"
                + (f" ({com_err})" if com_err else "")
            )

    def _create_desktop_shortcut(self):
        """
        Create a desktop shortcut on Windows / macOS / Linux.
        Never opens a terminal, console, or PowerShell window on any platform.
        """
        import stat as _stat
        script  = Path(__file__).resolve().parent / "main.py"
        python  = Path(sys.executable)
        desktop = self._get_desktop_dir()

        # Arc-reactor icon (.ico — also exported as .png for Linux/macOS)
        ico_path = Path(__file__).resolve().parent / "config" / "Mehmet.ico"
        if not ico_path.exists():
            self._build_Mehmet_icon(ico_path)

        try:
            _os = platform.system()
            desktop.mkdir(parents=True, exist_ok=True)

            # ── Windows ───────────────────────────────────────────────────────
            if _os == "Windows":
                pythonw  = python.parent / "pythonw.exe"
                target   = str(pythonw if pythonw.exists() else python)
                lnk      = str(desktop / "Mehmet.lnk")
                icon_loc = str(ico_path) if ico_path.exists() else f"{target},0"
                self._create_lnk_windows(lnk, target, str(script),
                                         str(script.parent), icon_loc)

            # ── macOS — proper .app bundle (no Terminal window) ───────────────
            elif _os == "Darwin":
                app     = desktop / "Mehmet.app"
                mac_dir = app / "Contents" / "MacOS"
                res_dir = app / "Contents" / "Resources"
                mac_dir.mkdir(parents=True, exist_ok=True)
                res_dir.mkdir(exist_ok=True)

                # Launcher executable (bash — runs as background process,
                # macOS does NOT open Terminal for executables inside .app bundles)
                launcher = mac_dir / "Mehmet"
                launcher.write_text(
                    "#!/usr/bin/env bash\n"
                    f'cd "{script.parent}"\n'
                    f'exec "{python}" "{script}"\n'
                )
                launcher.chmod(launcher.stat().st_mode
                               | _stat.S_IEXEC | _stat.S_IXGRP | _stat.S_IXOTH)

                # Minimal Info.plist (required for .app recognition)
                (app / "Contents" / "Info.plist").write_text(
                    '<?xml version="1.0" encoding="UTF-8"?>\n'
                    '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
                    '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
                    '<plist version="1.0"><dict>\n'
                    '  <key>CFBundleExecutable</key><string>Mehmet</string>\n'
                    '  <key>CFBundleIdentifier</key>'
                    '<string>com.Mehmet.assistant</string>\n'
                    '  <key>CFBundleName</key><string>Mehmet</string>\n'
                    '  <key>CFBundlePackageType</key><string>APPL</string>\n'
                    '  <key>CFBundleVersion</key><string>1.0</string>\n'
                    '</dict></plist>\n'
                )

                # Optional: copy icon as .icns (skip silently if Pillow is missing)
                try:
                    import PIL.Image
                    icns = res_dir / "AppIcon.icns"
                    PIL.Image.open(ico_path).save(icns, format="ICNS")
                    # Inject icon reference into plist
                    plist = app / "Contents" / "Info.plist"
                    txt = plist.read_text()
                    plist.write_text(
                        txt.replace(
                            '</dict></plist>',
                            '  <key>CFBundleIconFile</key>'
                            '<string>AppIcon</string>\n</dict></plist>\n',
                        )
                    )
                except Exception:
                    pass  # icon is optional

            # ── Linux — .desktop file (Terminal=false, no console) ────────────
            else:
                # Export .ico → .png for better desktop integration
                png_path = ico_path.with_suffix(".png")
                if not png_path.exists() and ico_path.exists():
                    try:
                        import PIL.Image
                        PIL.Image.open(ico_path).resize(
                            (256, 256), PIL.Image.LANCZOS
                        ).save(png_path, format="PNG")
                    except Exception:
                        png_path = ico_path  # fallback to .ico

                icon_line = f"Icon={png_path}\n" if png_path.exists() else ""
                desk = desktop / "Mehmet.desktop"
                desk.write_text(
                    "[Desktop Entry]\n"
                    "Name=Mehmet\n"
                    f'Exec="{python}" "{script}"\n'
                    f"Path={script.parent}\n"
                    "Type=Application\n"
                    "Terminal=false\n"
                    "Categories=Utility;\n"
                    + icon_line
                )
                desk.chmod(desk.stat().st_mode | 0o755)
                # GNOME refuses to launch desktop files until they are
                # marked trusted ("Allow Launching") — do it automatically.
                try:
                    subprocess.run(
                        ["gio", "set", str(desk),
                         "metadata::trusted", "true"],
                        capture_output=True, timeout=5,
                    )
                except Exception:
                    pass  # non-GNOME desktops don't need (or have) gio

            self._log.append_log(f"SYS: Desktop shortcut created in '{desktop}'.")
        except Exception as e:
            self._log.append_log(
                f"ERR: Shortcut failed — {e} (desktop dir: '{desktop}')"
            )

    def _toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._browser_open:
            self._position_browser()
        if self._browser_curtain is not None:
            self._browser_curtain.track_parent()
        cw = self.centralWidget()
        if self._overlay and self._overlay.isVisible():
            ow, oh = 460, 390
            self._overlay.setGeometry(
                (cw.width()  - ow) // 2,
                (cw.height() - oh) // 2,
                ow, oh,
            )
        if self._remote_overlay and self._remote_overlay.isVisible():
            ow, oh = RemoteKeyOverlay._OW, RemoteKeyOverlay._OH
            self._remote_overlay.setGeometry(
                (cw.width()  - ow) // 2,
                (cw.height() - oh) // 2,
                ow, oh,
            )
        if self._customize_overlay and self._customize_overlay.isVisible():
            ow, oh = CustomizeOverlay._OW, CustomizeOverlay._OH
            self._customize_overlay.setGeometry(
                (cw.width()  - ow) // 2,
                (cw.height() - oh) // 2,
                ow, oh,
            )
        # Camera preview — bottom-right corner of the center/HUD area
        pw = _CameraPreview._W
        ph = self._cam_preview.height() or _CameraPreview._H
        self._cam_preview.setGeometry(
            cw.width() - _RIGHT_W - pw - 12,
            cw.height() - ph - 28,
            pw, ph,
        )
        # Clipboard panel — bottom-center
        if hasattr(self, '_clipboard_panel') and self._clipboard_panel.isVisible():
            self._position_clipboard_panel()
        # Quick drawer — reposition if open
        if hasattr(self, '_quick_drawer') and self._quick_drawer.isVisible():
            self._position_quick_drawer()

    def _update_metrics(self):
        snap = _metrics.snapshot()

        # CPU
        cpu = snap["cpu"]
        self._bar_cpu.set_value(cpu, f"{cpu:.0f}%")

        # MEM
        mem = snap["mem"]
        self._bar_mem.set_value(mem, f"{mem:.0f}%")

        # NET
        net = snap["net"]
        if net < 1.0:
            net_str = f"{net*1024:.0f}KB/s"
        else:
            net_str = f"{net:.1f}MB/s"
        net_pct = min(100, net * 10)  # 10 MB/s = %100
        self._bar_net.set_value(net_pct, net_str)

        # GPU
        gpu = snap["gpu"]
        if gpu >= 0:
            self._bar_gpu.set_value(gpu, f"{gpu:.0f}%")
        else:
            self._bar_gpu.set_value(0, "N/A")

        # TMP
        tmp = snap["tmp"]
        if tmp >= 0:
            tmp_pct = min(100, (tmp / 100) * 100)
            self._bar_tmp.set_value(tmp_pct, f"{tmp:.0f}°C")
        else:
            self._bar_tmp.set_value(0, "N/A")

        try:
            boot_t  = psutil.boot_time()
            elapsed = time.time() - boot_t
            h = int(elapsed // 3600)
            m = int((elapsed % 3600) // 60)
            self._uptime_lbl.setText(f"ÇALIŞMA  {h:02d}:{m:02d}")
        except Exception:
            self._uptime_lbl.setText("ÇALIŞMA  --:--")

        try:
            proc_count = len(psutil.pids())
            self._proc_lbl.setText(f"İŞLEM  {proc_count}")
        except Exception:
            self._proc_lbl.setText("İŞLEM  --")


    # ------------------------------------------------------------------
    # NAVİGATÖR — dahili tarayıcı (browser/panel.py) — tam pencere katmanı
    # ------------------------------------------------------------------
    def _ensure_browser(self) -> None:
        """NAVİGATÖR'ü (tek seferlik) kurar: centralWidget üzerinde tam boy
        yüzen katman. Ana arayüz altında olduğu gibi kalır — geçişte panel
        animasyonları tekrar oynatılmaz."""
        if self._browser is not None or self._browser_page is not None:
            return
        try:
            from browser.panel import BrowserPanel   # ui ↔ browser döngüsünü kırmak için geç import
            page = QWidget(self.centralWidget())
            page.setObjectName("NavigatorPage")
            page.setStyleSheet(f"background: {C.BG};")
            pl = QVBoxLayout(page)
            pl.setContentsMargins(0, 0, 0, 0)
            panel = BrowserPanel(page)
            panel.exit_requested.connect(self.close_browser)
            panel.vpn_alert.connect(self._on_vpn_alert)
            pl.addWidget(panel)
        except Exception as e:
            print(f"[Navigator] kurulamadı: {e}")
            return
        page.hide()
        self._browser = panel
        self._browser_page = page

    def _on_watch_badge(self, on: bool) -> None:
        """İzleme modu canlı rozeti — köşede nabız atan gösterge."""
        import random as _random
        if on:
            if getattr(self, "_watch_badge", None):
                return
            badge = QLabel(self)
            badge.setObjectName("WatchBadge")
            badge.setText("● MEHMET İZLİYOR")
            badge.setStyleSheet(
                "background: rgba(4,14,10,225); color: #7dffb0;"
                "border: 1px solid #35d97a; border-radius: 11px;"
                "padding: 5px 14px; font-size: 11px; font-weight: 700;")
            badge.adjustSize()
            badge.move(self.width() - badge.width() - 26, 64)
            badge.show()
            badge.raise_()
            self._watch_badge = badge
            # nabız animasyonu
            base_alpha = 225
            def _pulse():
                try:
                    b = getattr(self, "_watch_badge", None)
                    if b is None:
                        return
                    a = 0.55 + 0.45 * _random.random()
                    b.setStyleSheet(
                        "background: rgba(4,14,10," + str(int(base_alpha * a)) + ");"
                        "color: #7dffb0; border: 1px solid #35d97a;"
                        "border-radius: 11px; padding: 5px 14px;"
                        "font-size: 11px; font-weight: 700;")
                except Exception:
                    pass
            self._watch_pulse = QTimer(self)
            self._watch_pulse.timeout.connect(_pulse)
            self._watch_pulse.start(1200)
            self._log.append_log("SYS: İzleme modu AÇIK — ekranı ve sistem sesini dinliyorum.")
        else:
            b = getattr(self, "_watch_badge", None)
            if b is not None:
                b.close()
                b.deleteLater()
                self._watch_badge = None
            p = getattr(self, "_watch_pulse", None)
            if p is not None:
                p.stop()
                self._watch_pulse = None
            self._log.append_log("SYS: İzleme modu KAPALI.")

    def _on_vpn_alert(self, url: str, warn: str) -> None:
        """Panel: kritik sitede VPN kapalı — ekranda kırmızı uyarı bandı."""
        try:
            self._log.append_log("UYARI: " + warn)
        except Exception:
            print("UYARI:", warn)
        try:
            if getattr(self, "_vpn_guard_toast", None):
                self._vpn_guard_toast.close()
        except Exception:
            pass
        toast = None
        try:
            from PyQt6.QtWidgets import QLabel as _QLabel
            toast = _QLabel(self)
            toast.setObjectName("VpnGuardToast")
            toast.setStyleSheet(
                "background: rgba(20,10,10,235); color: #ffd7de;"
                "border: 1px solid #ff4d6d; border-radius: 10px;"
                "padding: 10px 16px; font-size: 12px; font-weight: 600;")
            toast.setWordWrap(True)
            toast.setText(warn)
            try:
                from browser import icons as _icons
                pm = _icons.get_pixmap("shield", 32, "#ff4d6d")
            except Exception:
                pm = None
            toast.adjustSize()
            vw = self.width()
            toast.move((vw - toast.width()) // 2, self.height() - 90)
            toast.show()
            toast.raise_()
            from PyQt6.QtCore import QTimer as _QTimer
            _QTimer.singleShot(6000, toast.deleteLater)
        except Exception:
            pass
        self._vpn_guard_toast = toast

    def _position_browser(self) -> None:
        """Tarayıcı katmanını ORTA KOLONA yerleştir: header/sol/sağ panel ve
        footer görünür kalır — sadece MEHMET alanı tarayıcıya devrolur."""
        if self._browser_page is None:
            return
        cw = self.centralWidget()
        left  = self._left_panel.width()  if self._left_panel.isVisible()  else 0
        right = self._right_panel.width() if self._right_panel.isVisible() else 0
        top    = self._header.height()
        bottom = self._footer.height() if self._footer.isVisible() else 0
        geo = cw.rect().adjusted(left, top, -right, -bottom)
        if geo.width() > 0 and geo.height() > 0:
            self._browser_page.setGeometry(geo)

    def open_browser(self, url: str = "") -> None:
        """NAVİGATÖR'ü tam pencere modunda aç (ana thread). url verilirse
        aktif sekmede gezinir; '?' ile başlarsa Google araması yapar."""
        if self.is_serious_mode:
            self._log.append_log("SİSTEM: NAVİGATÖR ciddi modda kullanılamaz.")
            return
        self._ensure_browser()
        if self._browser is None:
            self._log.append_log("SİSTEM: NAVİGATÖR yüklenemedi — PyQt6-WebEngine eksik olabilir.")
            return
        if url:
            if url.startswith("?"):
                self._browser.search(url[1:].strip())
            elif url != "about:blank":
                self._browser.navigate(url)
        self._position_browser()
        if not self._browser_open:
            self._browser_open = True
            self._play_curtain(opening=True)
            self._log.append_log("SİSTEM: NAVİGATÖR MEHMET alanında açık. [F6] geri döner.")
        else:
            self._browser_page.show()
            self._browser_page.raise_()
        if hasattr(self, "_nav_btn"):
            self._nav_btn.setChecked(True)

    def close_browser(self) -> None:
        """NAVİGATÖR'ü sinematik perdeyle kapatır; sekmeler arka planda
        saklanır. Ana arayüz perde açılınca görünür."""
        if not self._browser_open:
            return
        self._browser_open = False
        self._play_curtain(opening=False)
        if hasattr(self, "_nav_btn"):
            self._nav_btn.setChecked(False)
        self._log.append_log("SİSTEM: Mehmet'e dönüldü. NAVİGATÖR beklemede.")

    def toggle_browser(self) -> None:
        if self._browser_open:
            self.close_browser()
        else:
            self.open_browser()

    # ── sinematik geçiş perdesi (JARVIS tarzı) ─────────────────────────────
    def _browser_rect(self):
        """Perdenin kaplayacağı alan: tarayıcı katmanıyla aynı orta kolon."""
        cw = self.centralWidget()
        left  = self._left_panel.width()  if self._left_panel.isVisible()  else 0
        right = self._right_panel.width() if self._right_panel.isVisible() else 0
        top    = self._header.height()
        bottom = self._footer.height() if self._footer.isVisible() else 0
        return cw.rect().adjusted(left, top, -right, -bottom)

    def _play_curtain(self, *, opening: bool, label: str = "NAVİGATÖR") -> None:
        """Sinematik geçiş perdesi (AI↔tarayıcı, ciddi mod, açılış).
        Kapanışta tarayıcı sayfası perde tamamen kapana kadar görünür kalır.
        (browser.transition.BrowserCurtain)
        """
        from browser.transition import BrowserCurtain
        if self._browser_curtain is None:
            self._browser_curtain = BrowserCurtain(self.centralWidget())
        rect = self._browser_rect()
        page = self._browser_page
        curtain = self._browser_curtain
        curtain.set_label(label)
        if opening:
            # tarayıcı perdenin ALTINDA hazır: perde kapanınca zaten görünür
            if page is not None:
                page.show()
                page.raise_()
            curtain.raise_()
            curtain.open_curtain(rect)
        else:
            # sayfa perde tam kapana kadar görünür (perde onu "yer"),
            # dalga koyu perde üstünde koşar, açılış HUD'ı gösterir
            if page is not None:
                def _hide_page():
                    if not self._browser_open:
                        page.hide()
                QTimer.singleShot(360, _hide_page)
            curtain.raise_()
            curtain.close_curtain(rect)

    def _on_browser_command(self, cmd: str) -> None:
        """Slot — thread-safe tarayıcı komutları (Mehmet sesli API'si dahil)."""
        if cmd == "toggle":
            self.toggle_browser()
        elif cmd.startswith("open:"):
            self.open_browser(cmd[5:])
        elif cmd == "close":
            self.close_browser()
        elif self._browser is None:
            return
        elif cmd.startswith("search:"):
            self._browser.search(cmd[7:])
            if not self._browser_open:
                self.open_browser()
        elif cmd.startswith("newtab:"):
            self._browser.new_tab(cmd[7:] or "")
            if not self._browser_open:
                self.open_browser()
        elif cmd == "closetab":
            self._browser.close_current_tab()
        elif cmd.startswith("switchtab:"):
            try:
                self._browser.switch_tab(int(cmd[10:]))
            except ValueError:
                pass
        elif cmd == "closeall":
            closed = self._browser.close_all_tabs()
            self._log.append_log(f"SİSTEM: {closed} sekme kapatıldı.")
        elif cmd.startswith("pageurl:"):
            token = cmd[8:]
            url = ""
            try:
                if self._browser is not None:
                    url = self._browser.current_url() or ""
            except Exception:
                url = ""
            self._page_text_sig.emit(token + "\x1f" + json.dumps(
                {"url": url}, ensure_ascii=False))
        elif cmd.startswith("listtabs:"):
            token = cmd[9:]
            lst = self._browser.list_tabs()
            self._page_text_sig.emit(
                token + "\x1f" + json.dumps({"list": lst},
                                             ensure_ascii=False))
        elif cmd == "vpntoggle":
            self._browser._toggle_vpn_quick()
            st = self._browser.vpn.status_text()
            self._log.append_log("SİSTEM: VPN — " + st)
        elif cmd == "abtoggle":
            new_state = not self._browser.adblock.enabled
            self._browser._ab_btn.setChecked(new_state)
        elif cmd.startswith("chain:"):
            # Çok adımlı görev: zinciri ana thread'de pump'la çalıştır
            token, _, payload = cmd[6:].partition("\x1f")
            try:
                spec = json.loads(payload)
            except Exception:
                spec = {}
            _pump = (QApplication.instance().processEvents
                     if QApplication.instance() else None)
            res = self._browser.run_chain(spec, pump=_pump)
            self._page_text_sig.emit(
                token + "\x1f" + json.dumps(res, ensure_ascii=False))
        elif cmd.startswith("shot:"):
            token = cmd[5:]
            res = self._browser.screenshot_active()
            self._page_text_sig.emit(
                token + "\x1f" + json.dumps(res, ensure_ascii=False))
        elif cmd.startswith("cap:"):
            # Görsel analiz: PNG baytları base64 ile geri akar (JSON uyumlu)
            import base64 as _b64c
            token = cmd[4:]
            res = self._browser.capture_image()
            if res.get("ok") and res.get("png"):
                res["png"] = _b64c.b64encode(res["png"]).decode("ascii")
            self._page_text_sig.emit(
                token + "\x1f" + json.dumps(res, ensure_ascii=False))
        elif cmd.startswith("vpnconn:"):
            # Tek kelimeyle VPN'e bağlan (bölge takma adı veya 'en hızlı')
            token, _, word = cmd[8:].partition("\x1f")
            def _vpn_word_work(token=token, word=word):
                vpn = self._browser.vpn
                ok, msg = vpn.connect_word(word)
                self._page_text_sig.emit(token + "\x1f" + json.dumps(
                    {"ok": ok, "message": msg,
                     "status": vpn.status_text()}, ensure_ascii=False))
            threading.Thread(target=_vpn_word_work, daemon=True).start()
        elif cmd.startswith("translate:"):
            token, _, target = cmd[10:].partition("\x1f")
            _pump = (QApplication.instance().processEvents
                     if QApplication.instance() else None)
            res = self._browser.translate_active(target or "tr", pump=_pump)
            self._page_text_sig.emit(
                token + "\x1f" + json.dumps(res, ensure_ascii=False))
        elif cmd.startswith("vpnspeed:"):
            token, _, auto = cmd[9:].partition("\x1f")
            def _speed_work(token=token, auto=auto):
                import json as _json
                vpn = self._browser.vpn
                if auto == "1":
                    p, results = vpn.auto_select_fastest(5.0)
                    res = {"ok": p is not None, "results": [
                        {"name": n, "region": r, "ms": ms}
                        for n, r, ms in results],
                        "selected": (p or {}).get("name", ""),
                        "upstream": vpn.upstream_label()}
                else:
                    results = vpn.speed_test(5.0)
                    res = {"ok": any(ms is not None for _, _, ms in results),
                           "results": [{"name": n, "region": r, "ms": ms}
                                       for n, r, ms in results]}
                self._page_text_sig.emit(
                    token + "\x1f" + _json.dumps(res, ensure_ascii=False))
            import threading as _th
            _th.Thread(target=_speed_work, daemon=True).start()
        elif cmd.startswith("click:"):
            token, _, payload = cmd[6:].partition("\x1f")
            self._page_action_and_reply(token, "click", payload)
        elif cmd.startswith("type:"):
            token, _, payload = cmd[5:].partition("\x1f")
            self._page_action_and_reply(token, "type", payload)
        elif cmd.startswith("fillform:"):
            token, _, payload = cmd[9:].partition("\x1f")
            self._page_action_and_reply(token, "fillform", payload)
        elif cmd.startswith("scroll:"):
            try:
                _, d, a = cmd.split(":", 2)
                t = self._browser._current_tab()
                if t:
                    t.js_scroll(d, int(a))
            except Exception:
                pass
        elif cmd.startswith("presskey:"):
            try:
                kv = json.loads(cmd[9:])
                t = self._browser._current_tab()
                if t:
                    t.js_press_key(kv.get("key", "Enter"))
            except Exception:
                pass
        elif cmd.startswith("dark:"):
            self._browser.set_dark_mode(cmd[5:] in ("1", "on", "true"))
        elif cmd.startswith("zoom:"):
            try:
                self._browser.set_zoom_pct(int(cmd[5:]))
            except ValueError:
                pass
        elif cmd.startswith("font:"):
            try:
                self._browser.set_font_delta(int(cmd[5:]))
            except ValueError:
                pass
        elif cmd.startswith("readpage:"):
            self._read_page_and_reply(cmd[9:])

    def closeEvent(self, ev) -> None:
        b = self._browser
        if b is not None:
            try:
                b.shutdown()
            except Exception:
                pass
        super().closeEvent(ev)

    # ── sayfa metni okuma (Mehmet'in okuma yeteneği) ──────────────────────
    def _read_page_and_reply(self, token: str) -> None:
        """Aktif sekmenin metnini okur; sonucu `_page_text_sig` üzerinden
        (token ile eşleşen) bekleyen thread'e iletir."""
        if self._browser is None:
            self._ensure_browser()
        if self._browser is None:
            self._page_text_sig.emit(f"{token}\x1f" + json.dumps(
                {"ok": False, "error": "tarayıcı kullanılamıyor"}))
            return
        try:
            self._browser.read_page(
                lambda meta, _tk=token: self._page_text_sig.emit(
                    f"{_tk}\x1f" + json.dumps(meta, ensure_ascii=False)))
        except Exception as e:
            self._page_text_sig.emit(f"{token}\x1f" + json.dumps(
                {"ok": False, "error": str(e)}))

    def _on_page_text(self, payload: str) -> None:
        """Ana thread: okuma/aksi sonucunu bekleyen Event'i uyandır."""
        rec = self._page_waiters.pop(payload.split("\x1f")[0], None)
        if rec is not None:
            rec["payload"] = payload.split("\x1f", 1)[1]
            rec["event"].set()

    def _page_action_and_reply(self, token: str, action: str,
                               payload: str) -> None:
        """Sayfa içi aksiyon (click/type/fill) çalıştırır; sonucu JSON
        olarak bekleyen thread'e iletir."""
        def _reply(res: dict):
            self._page_text_sig.emit(
                token + "\x1f" + json.dumps(res, ensure_ascii=False))

        try:
            args = json.loads(payload) if payload else {}
        except Exception:
            _reply({"ok": False, "error": "parametreler çözümlenemedi"})
            return
        b = self._browser
        if b is None:
            _reply({"ok": False, "error": "tarayıcı yok"})
            return
        t = b._current_tab()
        if t is None:
            _reply({"ok": False, "error": "açık sekme yok"})
            return
        if action == "click":
            if args.get("text"):
                t.js_find_and_click(args["text"], _reply)
            else:
                t.js_click(args.get("selector", ""), _reply)
        elif action == "type":
            t.js_type(args.get("selector", ""), args.get("text", ""),
                      bool(args.get("clear", True)), _reply)
        elif action == "fillform":
            t.js_fill_form(args if isinstance(args, dict) else {}, _reply)
        else:
            _reply({"ok": False, "error": "bilinmeyen aksiyon"})

    def _build_header(self) -> QWidget:
        w = QWidget()
        w.setFixedHeight(72)
        w.setObjectName("AppHeader")
        w.setStyleSheet(f"""
            QWidget#AppHeader {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 {C.DARK}, stop:0.5 {C.PANEL}, stop:1 {C.DARK});
                border-bottom: 1px solid {C.BORDER_B};
            }}
        """)
        lay = QHBoxLayout(w)
        lay.setContentsMargins(16, 0, 16, 0)

        def _badge(txt, color=C.TEXT_MED):
            l = QLabel(txt)
            l.setFont(_font(FONT_MONO, 9, QFont.Weight.DemiBold))
            l.setStyleSheet(f"color: {color}; background: transparent;")
            return l

        lay.addWidget(_badge(f"v{APP_VERSION}", C.PRI_DIM))
        lay.addSpacing(8)
        self._drawer_btn = QPushButton("⚙")
        self._drawer_btn.setFixedSize(36, 36)
        self._drawer_btn.setFont(_font(FONT_DISPLAY, 14, QFont.Weight.DemiBold))
        self._drawer_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._drawer_btn.setToolTip("Ayarlar ve hızlı kontroller")
        self._drawer_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_DIM};
                border: 1px solid {C.BORDER}; border-radius: 8px;
            }}
            QPushButton:hover {{ color: {C.PRI}; border-color: {C.PRI_DIM}; }}
            QPushButton:checked {{ color: {C.PRI}; border-color: {C.PRI}; background: {C.PRI_GHO}; }}
        """)
        self._drawer_btn.setCheckable(True)
        self._drawer_btn.clicked.connect(self._toggle_drawer)
        lay.addWidget(self._drawer_btn)
        lay.addStretch()

        mid = QVBoxLayout(); mid.setSpacing(2)
        _disp = self._assistant_name.upper()
        self._title_lbl = QLabel(_disp)
        self._title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._title_lbl.setFont(_font(FONT_DISPLAY, 24, QFont.Weight.DemiBold))
        self._title_lbl.setStyleSheet(
            f"color: {C.PRI}; background: transparent; letter-spacing: 6px;")
        mid.addWidget(self._title_lbl)
        self._sub_lbl = QLabel("BARANT // KİŞİSEL YAPAY ZEKÂ")
        self._sub_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._sub_lbl.setFont(_font(FONT_MONO, 9, QFont.Weight.DemiBold))
        self._sub_lbl.setStyleSheet(
            f"color: {C.PRI_DIM}; background: transparent; letter-spacing: 2px;")
        mid.addWidget(self._sub_lbl)
        lay.addLayout(mid)
        lay.addStretch()

        right_col = QVBoxLayout(); right_col.setSpacing(2)
        self._clock_lbl = QLabel("00:00:00")
        self._clock_lbl.setFont(_font(FONT_DISPLAY, 20, QFont.Weight.DemiBold))
        self._clock_lbl.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        self._clock_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        right_col.addWidget(self._clock_lbl)
        self._date_lbl = QLabel("")
        self._date_lbl.setFont(_font(FONT_MONO, 9))
        self._date_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        self._date_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        right_col.addWidget(self._date_lbl)
        lay.addLayout(right_col)
        return w

    def _tick_clock(self):
        self._clock_lbl.setText(time.strftime("%H:%M:%S"))
        day_names = ("Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar")
        month_names = ("Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran",
                       "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık")
        now = time.localtime()
        self._date_lbl.setText(
            f"{day_names[now.tm_wday]} · {now.tm_mday} {month_names[now.tm_mon - 1]} {now.tm_year}"
        )

    def _build_left_panel(self) -> QWidget:
        w = QWidget()
        w.setFixedWidth(_LEFT_W)
        w.setObjectName("SystemPanel")
        w.setStyleSheet(f"""
            QWidget#SystemPanel {{
                background: {C.PANEL};
                border-right: 1px solid {C.BORDER};
            }}
        """)
        lay = QVBoxLayout(w)
        lay.setContentsMargins(14, 16, 14, 14)
        lay.setSpacing(9)

        hdr = QLabel("SİSTEM DURUMU")
        hdr.setFont(_font(FONT_DISPLAY, 14, QFont.Weight.DemiBold))
        hdr.setStyleSheet(f"color: {C.WHITE}; background: transparent; "
                          f"border-bottom: 1px solid {C.BORDER}; padding-bottom: 8px;")
        lay.addWidget(hdr)
        sub = QLabel("CANLI TELEMETRİ")
        sub.setFont(_font(FONT_MONO, 8, QFont.Weight.DemiBold))
        sub.setStyleSheet(f"color: {C.PRI_DIM}; background: transparent; letter-spacing: 1px;")
        lay.addWidget(sub)
        lay.addSpacing(2)

        self._bar_cpu = MetricBar("CPU", C.PRI)
        self._bar_mem = MetricBar("MEM", C.ACC2)
        self._bar_net = MetricBar("NET", C.GREEN)
        self._bar_gpu = MetricBar("GPU", C.ACC)
        self._bar_tmp = MetricBar("TMP", "#ff6688")

        for bar in [self._bar_cpu, self._bar_mem, self._bar_net,
                    self._bar_gpu, self._bar_tmp]:
            lay.addWidget(bar)

        lay.addSpacing(4)

        info_panel = QWidget()
        info_panel.setStyleSheet(
            f"background: {C.PANEL2}; border: 1px solid {C.BORDER}; border-radius: 10px;"
        )
        ip_lay = QVBoxLayout(info_panel)
        ip_lay.setContentsMargins(6, 5, 6, 5)
        ip_lay.setSpacing(3)

        self._uptime_lbl = QLabel("ÇALIŞMA  --:--")
        self._uptime_lbl.setFont(_font(FONT_MONO, 10, QFont.Weight.DemiBold))
        self._uptime_lbl.setStyleSheet(f"color: {C.GREEN}; background: transparent; border: none;")
        ip_lay.addWidget(self._uptime_lbl)

        self._proc_lbl = QLabel("İŞLEM  --")
        self._proc_lbl.setFont(_font(FONT_MONO, 10))
        self._proc_lbl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent; border: none;")
        ip_lay.addWidget(self._proc_lbl)

        os_name = {"Windows": "WIN", "Darwin": "macOS", "Linux": "LINUX"}.get(_OS, _OS.upper())
        os_lbl = QLabel(f"OS  {os_name}")
        os_lbl.setFont(_font(FONT_MONO, 10))
        os_lbl.setStyleSheet(f"color: {C.ACC2}; background: transparent; border: none;")
        ip_lay.addWidget(os_lbl)

        lay.addWidget(info_panel)
        lay.addSpacing(4)

        # NAVİGATÖR — dahili tarayıcı geçidi
        self._nav_btn = QPushButton("🌐  NAVİGATÖR")
        self._nav_btn.setFixedHeight(40)
        self._nav_btn.setFont(_font(FONT_UI, 10, QFont.Weight.DemiBold))
        self._nav_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._nav_btn.setToolTip("Dahili tarayıcıyı aç/kapat (sekme destekli)")
        self._nav_btn.setStyleSheet(f"""
            QPushButton {{
                background: {C.PRI_GHO}; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 8px;
            }}
            QPushButton:hover {{ border-color: {C.PRI}; color: {C.WHITE}; }}
        """)
        self._nav_btn.clicked.connect(self.toggle_browser)
        lay.addWidget(self._nav_btn)

        lay.addStretch()

        for txt, col in [
            ("YAPAY ZEKÂ\nÇEVRİMİÇİ",  C.GREEN),
            ("GÜVENLİK\nKORUNUYOR",     C.PRI),
            ("BARANT\nPROTOKOLÜ",        C.TEXT_DIM),
        ]:
            lbl = QLabel(txt)
            lbl.setFont(_font(FONT_UI, 10, QFont.Weight.DemiBold))
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet(
                f"color: {col}; background: {C.PANEL2};"
                f"border: 1px solid {C.BORDER_A}; border-radius: 8px; padding: 8px;"
            )
            lay.addWidget(lbl)

        return w
    def _build_right_panel(self) -> QWidget:
        w = QWidget()
        w.setFixedWidth(_RIGHT_W)
        w.setObjectName("ControlPanel")
        w.setStyleSheet(f"""
            QWidget#ControlPanel {{
                background: {C.PANEL};
                border-left: 1px solid {C.BORDER};
            }}
        """)
        lay = QVBoxLayout(w)
        lay.setContentsMargins(14, 16, 14, 14)
        lay.setSpacing(9)

        def _sec(txt):
            l = QLabel(txt)
            l.setFont(_font(FONT_DISPLAY, 10, QFont.Weight.DemiBold))
            l.setStyleSheet(f"color: {C.WHITE}; background: transparent; padding-top: 2px;")
            return l

        lay.addWidget(_sec("HAREKET GÜNLÜĞÜ"))
        self._log = LogWidget()
        lay.addWidget(self._log, stretch=1)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        lay.addWidget(sep)

        lay.addWidget(_sec("DOSYA ALANI"))
        self._drop_zone = FileDropZone()
        self._drop_zone.file_selected.connect(self._on_file_selected)
        lay.addWidget(self._drop_zone)

        self._file_hint = QLabel("Dosya yüklenmedi — yüklemek için tıkla yada sürükle")
        self._file_hint.setFont(_font(FONT_UI, 8))
        self._file_hint.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
        self._file_hint.setWordWrap(True)
        lay.addWidget(self._file_hint)

        sep2 = QFrame(); sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        lay.addWidget(sep2)

        lay.addWidget(_sec("KOMUT MERKEZİ"))
        lay.addLayout(self._build_input_row())

        self._interrupt_btn = QPushButton("✋  SUSTUR  [ESC]")
        self._interrupt_btn.setFixedHeight(38)
        self._interrupt_btn.setFont(_font(FONT_UI, 9, QFont.Weight.DemiBold))
        self._interrupt_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._interrupt_btn.setStyleSheet(f"""
            QPushButton {{
                background: #140008; color: {C.MUTED_C};
                border: 1px solid {C.MUTED_C}; border-radius: 8px;
            }}
            QPushButton:hover {{
                background: #200010; border: 1px solid #ff6688;
            }}
            QPushButton:pressed {{
                background: #300018;
            }}
        """)
        self._interrupt_btn.clicked.connect(self._do_interrupt)
        lay.addWidget(self._interrupt_btn)

        self._mute_btn = QPushButton("🎙  MİKROFON AKTİF")
        self._mute_btn.setFixedHeight(36)
        self._mute_btn.setFont(_font(FONT_UI, 9, QFont.Weight.DemiBold))
        self._mute_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._mute_btn.clicked.connect(self._toggle_mute)
        self._style_mute_btn()
        lay.addWidget(self._mute_btn)

        return w

    def _build_quick_drawer(self) -> QWidget:
        """Floating overlay panel shown when the ⚙ header button is toggled."""
        _BTN_STYLE_PRI = f"""
            QPushButton {{
                background: #00091a; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 3px;
                text-align: left; padding: 0 8px;
            }}
            QPushButton:hover {{ background: {C.PRI_GHO}; border-color: {C.PRI}; }}
        """
        _BTN_STYLE_DIM = f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px;
                text-align: left; padding: 0 8px;
            }}
            QPushButton:hover {{ color: {C.PRI}; border-color: {C.BORDER_B}; }}
        """

        w = QWidget(self.centralWidget())
        w.setObjectName("QuickDrawer")
        w.setStyleSheet(f"""
            QWidget#QuickDrawer {{
                background: {C.PANEL};
                border: 1px solid {C.BORDER_B};
                border-radius: 10px;
            }}
        """)
        w.hide()

        lay = QVBoxLayout(w)
        lay.setContentsMargins(10, 8, 10, 10)
        lay.setSpacing(5)

        hdr = QLabel("HIZLI KONTROLLER")
        hdr.setFont(_font(FONT_DISPLAY, 10, QFont.Weight.DemiBold))
        hdr.setStyleSheet(f"color: {C.PRI_DIM}; background: transparent; "
                          f"border-bottom: 1px solid {C.BORDER}; padding-bottom: 4px;")
        lay.addWidget(hdr)

        remote_btn = QPushButton("◉  UZAKTAN KONTROL")
        remote_btn.setFixedHeight(30)
        remote_btn.setFont(_font(FONT_UI, 9, QFont.Weight.DemiBold))
        remote_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        remote_btn.setStyleSheet(_BTN_STYLE_PRI)
        remote_btn.clicked.connect(self._open_remote)
        lay.addWidget(remote_btn)

        fs_btn = QPushButton("⛶  TAM EKRAN  [F11]")
        fs_btn.setFixedHeight(26)
        fs_btn.setFont(_font(FONT_UI, 8))
        fs_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        fs_btn.setStyleSheet(_BTN_STYLE_DIM)
        fs_btn.clicked.connect(self._toggle_fullscreen)
        lay.addWidget(fs_btn)

        sc_btn = QPushButton("⊞  MASAÜSTÜ KISAYOLU OLUŞTUR")
        sc_btn.setFixedHeight(26)
        sc_btn.setFont(_font(FONT_UI, 8))
        sc_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        sc_btn.setStyleSheet(_BTN_STYLE_DIM)
        sc_btn.clicked.connect(self._create_desktop_shortcut)
        lay.addWidget(sc_btn)

        self._autostart_btn = QPushButton("◉  OTOMATİK BAŞLAT: KAPALI")
        self._autostart_btn.setFixedHeight(26)
        self._autostart_btn.setFont(_font(FONT_UI, 8))
        self._autostart_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._autostart_btn.clicked.connect(self._toggle_autostart)
        lay.addWidget(self._autostart_btn)

        cust_btn = QPushButton("⚙  MEHMET'İ KİŞİSELLEŞTİR")
        cust_btn.setFixedHeight(26)
        cust_btn.setFont(_font(FONT_UI, 8))
        cust_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cust_btn.setStyleSheet(_BTN_STYLE_DIM)
        cust_btn.clicked.connect(self._open_customize)
        lay.addWidget(cust_btn)

        nav_btn = QPushButton("🌐  NAVİGATÖR'Ü AÇ / KAPAT")
        nav_btn.setFixedHeight(26)
        nav_btn.setFont(_font(FONT_UI, 8, QFont.Weight.DemiBold))
        nav_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        nav_btn.setStyleSheet(_BTN_STYLE_PRI)
        nav_btn.clicked.connect(self.toggle_browser)
        lay.addWidget(nav_btn)

        self._brief_btn = QPushButton()
        self._brief_btn.setFixedHeight(26)
        self._brief_btn.setFont(_font(FONT_UI, 8))
        self._brief_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._brief_btn.clicked.connect(self._toggle_brief)
        lay.addWidget(self._brief_btn)

        w.adjustSize()
        return w

    def _toggle_drawer(self, checked: bool):
        if checked:
            self._position_quick_drawer()
            self._quick_drawer.show()
            self._quick_drawer.raise_()
        else:
            self._quick_drawer.hide()

    def _position_quick_drawer(self):
        if not hasattr(self, '_quick_drawer'):
            return
        _W = 220
        self._quick_drawer.setFixedWidth(_W)
        self._quick_drawer.adjustSize()
        self._quick_drawer.setGeometry(16, 76, _W, self._quick_drawer.sizeHint().height())

    def _build_input_row(self) -> QHBoxLayout:
        row = QHBoxLayout(); row.setSpacing(5)
        self._input = QLineEdit()
        self._input.setPlaceholderText("Bir komut veya soru yazın…")
        self._input.setFont(_font(FONT_UI, 10))
        self._input.setFixedHeight(36)
        self._input.setStyleSheet(f"""
            QLineEdit {{
                background: #000d14; color: {C.WHITE};
                border: 1px solid {C.BORDER}; border-radius: 8px; padding: 4px 10px;
            }}
            QLineEdit:focus {{ border: 1px solid {C.PRI}; }}
        """)
        self._input.returnPressed.connect(self._send)
        row.addWidget(self._input)

        send = QPushButton("▸")
        send.setFixedSize(36, 36)
        send.setFont(_font(FONT_DISPLAY, 13, QFont.Weight.DemiBold))
        send.setCursor(Qt.CursorShape.PointingHandCursor)
        send.setStyleSheet(f"""
            QPushButton {{
                background: {C.PANEL}; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 9px;
            }}
            QPushButton:hover {{ background: {C.PRI_GHO}; border: 1px solid {C.PRI}; }}
        """)
        send.clicked.connect(self._send)
        row.addWidget(send)
        return row

    def _build_content_panel(self) -> QWidget:
        """
        Collapsible panel below the HUD — shows search results, news, briefings.
        Hidden by default; appears when show_content() is called.
        """
        w = QWidget()
        w.setObjectName("ContentPanel")
        w.setStyleSheet(f"""
            QWidget#ContentPanel {{
                background: {C.PANEL};
                border-top: 1px solid {C.BORDER_B};
            }}
        """)
        w.hide()

        lay = QVBoxLayout(w)
        lay.setContentsMargins(12, 7, 12, 8)
        lay.setSpacing(5)

        # ── header row ───────────────────────────────────────────────────────
        hdr = QHBoxLayout(); hdr.setSpacing(6)

        dot = QLabel("◈")
        dot.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        dot.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        hdr.addWidget(dot)

        self._content_title_lbl = QLabel("BİLGİ PANELİ")
        self._content_title_lbl.setFont(_font(FONT_DISPLAY, 11, QFont.Weight.DemiBold))
        self._content_title_lbl.setStyleSheet(
            f"color: {C.PRI}; background: transparent; letter-spacing: 1px;"
        )
        hdr.addWidget(self._content_title_lbl)
        hdr.addStretch()

        self._content_ts_lbl = QLabel("")
        self._content_ts_lbl.setFont(_font(FONT_MONO, 8))
        self._content_ts_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        hdr.addWidget(self._content_ts_lbl)

        dismiss = QPushButton("KAPAT  ✕")
        dismiss.setFont(_font(FONT_UI, 8))
        dismiss.setFixedHeight(18)
        dismiss.setCursor(Qt.CursorShape.PointingHandCursor)
        dismiss.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_DIM};
                border: 1px solid {C.BORDER}; border-radius: 6px; padding: 0 7px;
            }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        dismiss.clicked.connect(w.hide)
        hdr.addWidget(dismiss)
        lay.addLayout(hdr)

        # ── separator ─────────────────────────────────────────────────────────
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER};"); lay.addWidget(sep)

        # ── text display ──────────────────────────────────────────────────────
        self._content_display = QTextEdit()
        self._content_display.setReadOnly(True)
        self._content_display.setFont(_font(FONT_UI, 10))
        self._content_display.setMinimumHeight(60)
        self._content_display.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self._content_display.setStyleSheet(f"""
            QTextEdit {{
                background: {C.DARK};
                color: {C.TEXT};
                border: 1px solid {C.BORDER};
                border-radius: 9px;
                padding: 9px 11px;
                selection-background-color: {C.PRI_GHO};
            }}
            QScrollBar:vertical {{
                background: {C.BG}; width: 6px; border: none;
            }}
            QScrollBar::handle:vertical {{
                background: {C.BORDER_B}; border-radius: 3px; min-height: 16px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0; border: none;
            }}
        """)
        lay.addWidget(self._content_display)

        return w

    def _show_content(self, title: str, text: str):
        """Slot — runs on Qt main thread. Updates and shows the content panel."""
        import time as _time
        self._content_title_lbl.setText(title.upper()[:48])
        self._content_ts_lbl.setText(_time.strftime("%H:%M:%S"))
        self._content_display.setPlainText(text)
        self._content_display.moveCursor(
            self._content_display.textCursor().MoveOperation.Start
        )
        first_show = not self._content_panel.isVisible()
        self._content_panel.show()
        if first_show:
            total = self._center_split.height()
            self._center_split.setSizes([max(total - 220, 120), 220])

    def _build_footer(self) -> QWidget:
        w = QWidget()
        w.setFixedHeight(32)
        w.setStyleSheet(f"background: {C.PANEL}; border-top: 1px solid {C.BORDER};")
        lay = QHBoxLayout(w); lay.setContentsMargins(16, 0, 16, 0)

        def _fl(txt, color=C.TEXT_MED):
            l = QLabel(txt); l.setFont(_font(FONT_MONO, 8))
            l.setStyleSheet(f"color: {color}; background: transparent;")
            return l

        lay.addWidget(_fl("[F4] Mikrofon   •   [F11] Tam ekran   •   [ESC] Yanıtı kes"))
        lay.addStretch()
        lay.addWidget(_fl("BARANT  /  MEHMET YAPAY ZEKÂ", C.PRI_DIM))
        return w

    def _on_file_selected(self, path: str):
        self._current_file = path
        p    = Path(path)
        cat  = _file_category(p)
        icon, _ = _FILE_ICONS.get(cat, _FILE_ICONS["unknown"])
        size = _fmt_size(p.stat().st_size)
        self._file_hint.setText(f"{icon}  {p.name}  ·  {size}  ·  Mehmet'e ne yapılacağını söyleyin")
        self._log.append_log(f"DOSYA: {p.name} ({size}) yüklendi")
        if self.on_text_command:
            msg = (
                f"[FILE_UPLOADED] path={path} | name={p.name} | "
                f"type={p.suffix.lstrip('.')} | size={size} | "
                f"Briefly tell the user you can see the file '{p.name}' "
                f"({size}) has been uploaded and ask what they'd like to do with it."
            )
            threading.Thread(target=self.on_text_command, args=(msg,), daemon=True).start()

    def notify_phone_connected(self) -> None:
        if self._remote_overlay and self._remote_overlay.isVisible():
            self._remote_overlay.mark_connected()

    def _open_remote(self):
        if not self.on_remote_clicked:
            self._log.append_log("SYS: Dashboard not running — remote unavailable.")
            return
        result = self.on_remote_clicked()
        if not result:
            self._log.append_log("SYS: Could not generate remote key.")
            return
        url    = result[0]
        key    = result[1]
        auto   = result[2] if len(result) >= 3 else ""
        manual = result[3] if len(result) >= 4 else url
        if self._remote_overlay:
            self._remote_overlay._do_close()
        cw  = self.centralWidget()
        ow, oh = RemoteKeyOverlay._OW, RemoteKeyOverlay._OH
        ov  = RemoteKeyOverlay(url, key, auto_login_url=auto, manual_url=manual,
                               expiry_secs=600, parent=cw)
        ov.set_new_key_callback(self.on_remote_clicked)
        ov.setGeometry(
            (cw.width()  - ow) // 2,
            (cw.height() - oh) // 2,
            ow, oh,
        )
        ov.closed.connect(lambda: setattr(self, '_remote_overlay', None))
        ov.show()
        self._remote_overlay = ov
        self._log.append_log(f"SYS: Remote key generated — manual: {manual or url}")

    # ── Auto-start ──────────────────────────────────────────────────────────────

    def _check_autostart(self) -> bool:
        """Returns True if auto-start is currently registered on this OS."""
        try:
            if _OS == "Windows":
                import winreg
                key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_READ)
                try:
                    winreg.QueryValueEx(key, "Mehmet_AI")
                    return True
                except FileNotFoundError:
                    return False
                finally:
                    winreg.CloseKey(key)
            elif _OS == "Darwin":
                return (Path.home() / "Library" / "LaunchAgents"
                        / "com.Mehmet.assistant.plist").exists()
            else:
                return (Path.home() / ".config" / "autostart" / "Mehmet.desktop").exists()
        except Exception:
            return False

    def _toggle_autostart(self):
        currently_on = self._check_autostart()
        try:
            script = str(Path(__file__).resolve().parent / "main.py")
            if _OS == "Windows":
                import winreg
                reg = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_ALL_ACCESS)
                if currently_on:
                    winreg.DeleteValue(reg, "Mehmet_AI")
                else:
                    pythonw = Path(sys.executable).parent / "pythonw.exe"
                    exe = str(pythonw if pythonw.exists() else sys.executable)
                    winreg.SetValueEx(reg, "Mehmet_AI", 0, winreg.REG_SZ,
                                      f'"{exe}" "{script}"')
                winreg.CloseKey(reg)
            elif _OS == "Darwin":
                plist_dir = Path.home() / "Library" / "LaunchAgents"
                plist_dir.mkdir(parents=True, exist_ok=True)
                plist = plist_dir / "com.Mehmet.assistant.plist"
                if currently_on:
                    plist.unlink(missing_ok=True)
                else:
                    plist.write_text(
                        '<?xml version="1.0" encoding="UTF-8"?>\n'
                        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
                        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
                        '<plist version="1.0"><dict>\n'
                        '  <key>Label</key><string>com.Mehmet.assistant</string>\n'
                        '  <key>ProgramArguments</key><array>\n'
                        f'    <string>{sys.executable}</string>\n'
                        f'    <string>{script}</string>\n'
                        '  </array>\n'
                        '  <key>RunAtLoad</key><true/>\n'
                        '</dict></plist>\n'
                    )
            else:
                desk_dir = Path.home() / ".config" / "autostart"
                desk_dir.mkdir(parents=True, exist_ok=True)
                desk = desk_dir / "Mehmet.desktop"
                if currently_on:
                    desk.unlink(missing_ok=True)
                else:
                    desk.write_text(
                        "[Desktop Entry]\n"
                        f"Name={self._assistant_name}\n"
                        f"Exec={sys.executable} {script}\n"
                        "Type=Application\nTerminal=false\n"
                        "X-GNOME-Autostart-enabled=true\n"
                    )
            enabled = not currently_on
            self._update_autostart_btn(enabled)
            self._log.append_log(
                f"SYS: Auto-start {'enabled' if enabled else 'disabled'}.")
        except Exception as e:
            self._log.append_log(f"ERR: Auto-start failed — {e}")

    def _update_autostart_btn(self, enabled: bool):
        if not hasattr(self, '_autostart_btn'):
            return
        if enabled:
            self._autostart_btn.setText("◉  OTOMATİK BAŞLAT: AÇIK")
            self._autostart_btn.setStyleSheet(f"""
                QPushButton {{
                    background: #001a08; color: {C.GREEN};
                    border: 1px solid {C.GREEN_D}; border-radius: 3px;
                }}
                QPushButton:hover {{ background: #002010; }}
            """)
        else:
            self._autostart_btn.setText("◉  OTOMATİK BAŞLAT: KAPALI")
            self._autostart_btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; color: {C.TEXT_DIM};
                    border: 1px solid {C.BORDER}; border-radius: 3px;
                }}
                QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}
            """)

    def _toggle_brief(self):
        from memory.config_manager import get_brief_enabled, save_brief_enabled
        new_val = not get_brief_enabled()
        save_brief_enabled(new_val)
        self._update_brief_btn(new_val)

    def _update_brief_btn(self, enabled: bool):
        if not hasattr(self, '_brief_btn'):
            return
        if enabled:
            self._brief_btn.setText("☀  SABAH ÖZETİ: AÇIK")
            self._brief_btn.setStyleSheet(f"""
                QPushButton {{
                    background: #001a08; color: {C.GREEN};
                    border: 1px solid {C.GREEN_D}; border-radius: 3px;
                    text-align: left; padding: 0 8px;
                }}
                QPushButton:hover {{ background: #002010; }}
            """)
        else:
            self._brief_btn.setText("☀  SABAH ÖZETİ: KAPALI")
            self._brief_btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; color: {C.TEXT_DIM};
                    border: 1px solid {C.BORDER}; border-radius: 3px;
                    text-align: left; padding: 0 8px;
                }}
                QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}
            """)

    # ── Customization ────────────────────────────────────────────────────────────

    def _open_customize(self):
        cfg = _read_full_config()
        if self._customize_overlay:
            self._customize_overlay.hide()
        cw = self.centralWidget()
        ov = CustomizeOverlay(
            cfg.get("assistant_name", "Mehmet") or "Mehmet",
            cfg.get("user_name", ""),
            cfg.get("ui_color", "") or DEFAULT_UI_COLOR,
            parent=cw,
        )
        ow, oh = CustomizeOverlay._OW, CustomizeOverlay._OH
        oh = min(oh, cw.height() - 16)
        ov.setGeometry(
            (cw.width()  - ow) // 2,
            (cw.height() - oh) // 2,
            ow, oh,
        )
        ov.on_preview = self._preview_ui_color
        ov.saved.connect(self._apply_name_update)
        ov.show()
        self._customize_overlay = ov

    def _preview_ui_color(self, hex_color: str):
        """Canlı önizleme — tüm arayüzü yeni renge boyar (config'e YAZMAZ)."""
        old = current_palette()
        if apply_ui_accent(hex_color):
            retheme_all_widgets(old, current_palette())

    def _apply_name_update(self, name: str, user_name: str, ui_color: str = ""):
        """Update all name/theme-dependent UI elements and persist to config."""
        self._assistant_name = name.strip() or "Mehmet"
        display = self._assistant_name.upper()
        self.setWindowTitle(f"{display} — Sürüm 3.21.0")
        self._title_lbl.setText(display)
        if display in ("Mehmet", "MEHMET"):
            self._sub_lbl.setText("BARANT // KİŞİSEL YAPAY ZEKÂ")
        else:
            self._sub_lbl.setText("BARANT // KİŞİSEL YAPAY ZEKÂ")
        self._log._ai_name_lc = self._assistant_name.lower()
        self.hud._assistant_name = display

        color_changed = False
        if ui_color:
            old = current_palette()
            if apply_ui_accent(ui_color):
                # Tüm arayüzü (paneller, butonlar, kenarlıklar, HUD) canlı boya
                retheme_all_widgets(old, current_palette())
                color_changed = old["PRI"] != C.PRI

        try:
            data = _read_full_config()
            data["assistant_name"] = self._assistant_name
            data["user_name"] = user_name.strip()
            if ui_color:
                data["ui_color"] = ui_color.strip().lower()
            API_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")
            self._log.append_log(f"SYS: Identity updated — {display}")
            if color_changed:
                self._log.append_log(f"SYS: UI colour applied — {ui_color}")
        except Exception as e:
            self._log.append_log(f"ERR: Config save failed — {e}")

    # ── Clipboard intelligence ───────────────────────────────────────────────────

    def _on_clipboard_changed(self):
        try:
            text = QApplication.clipboard().text().strip()
            if len(text) >= 10:
                self._clipboard_sig.emit(text)
        except Exception:
            pass

    def _show_clipboard_panel(self, text: str):
        self._clipboard_panel.show_clipboard(text)
        self._position_clipboard_panel()

    def _position_clipboard_panel(self):
        cw = self.centralWidget()
        pw = ClipboardPanel._W
        ph = self._clipboard_panel.sizeHint().height() or ClipboardPanel._H
        x = (cw.width() - pw) // 2
        y = cw.height() - ph - 6
        self._clipboard_panel.setGeometry(x, y, pw, ph)
        self._clipboard_panel.raise_()

    def _on_clipboard_action(self, cmd: str):
        if self.on_text_command:
            threading.Thread(target=self.on_text_command, args=(cmd,), daemon=True).start()

    # ────────────────────────────────────────────────────────────────────────────

    def _do_interrupt(self):
        if self.on_interrupt:
            self.on_interrupt()

    def _toggle_mute(self):
        self._muted = not self._muted
        self.hud.muted = self._muted
        self._style_mute_btn()
        if self._muted:
            self._apply_state("Susturuldu")
            self._log.append_log("SYS: Mikrofon Kapalı.")
        else:
            self._apply_state("Dinliyor")
            self._log.append_log("SYS: Mikrofon aktif.")

    def _style_mute_btn(self):
        if self._muted:
            self._mute_btn.setText("🔇  MİKROFON SESSİZ")
            self._mute_btn.setStyleSheet(f"""
                QPushButton {{
                    background: #140006; color: {C.MUTED_C};
                    border: 1px solid {C.MUTED_C}; border-radius: 8px;
                }}
            """)
        else:
            self._mute_btn.setText("🎙  MİKROFON AÇIK")
            self._mute_btn.setStyleSheet(f"""
                QPushButton {{
                    background: #00140a; color: {C.GREEN};
                    border: 1px solid {C.GREEN}; border-radius: 8px;
                }}
                QPushButton:hover {{ background: #001f10; }}
            """)

    def _send(self):
        txt = self._input.text().strip()
        if not txt: return
        self._input.clear()
        self._log.append_log(f"You: {txt}")
        if self.on_text_command:
            threading.Thread(target=self.on_text_command, args=(txt,), daemon=True).start()

    def _apply_state(self, state: str):
        self.hud.state    = state
        self.hud.speaking = (state == "SPEAKING")
        if self.is_serious_mode:
            self.serious_canvas.set_state(state, self.hud.speaking)

    def _check_config(self) -> bool:
        if not API_FILE.exists(): return False
        try:
            d = json.loads(API_FILE.read_text(encoding="utf-8"))
            return bool(d.get("gemini_api_key")) and bool(d.get("os_system"))
        except Exception:
            return False

    def _show_setup(self):
        ov = SetupOverlay(self.centralWidget())
        cw = self.centralWidget()
        ow, oh = 460, 390
        ov.setGeometry(
            (cw.width()  - ow) // 2,
            (cw.height() - oh) // 2,
            ow, oh,
        )
        ov.done.connect(self._on_setup_done)
        ov.show()
        self._overlay = ov

    def _on_setup_done(self, key: str, os_name: str):
        os.makedirs(CONFIG_DIR, exist_ok=True)
        API_FILE.write_text(
            json.dumps({"gemini_api_key": key, "os_system": os_name}, indent=4),
            encoding="utf-8",
        )
        self._ready = True
        if self._overlay:
            self._overlay.hide()
            self._overlay = None
        self._apply_state("LISTENING")
        self._assistant_name = _read_full_config().get("assistant_name", "Mehmet") or "Mehmet"
        self._log.append_log(f"SYS: Initialised. OS={os_name.upper()}. {self._assistant_name} online.")

class _RootShim:
    def __init__(self, app: QApplication):
        self._app = app
    def mainloop(self):
        self._app.exec()
    def protocol(self, *_):
        pass


class MehmetUI:
    def __init__(self, face_path: str, size=None):
        self._app = QApplication.instance() or QApplication(sys.argv)
        self._app.setStyle("Fusion")
        self._app.setFont(_font(FONT_UI, 10))
        self._win = MainWindow(face_path)
        self._win.show()
        self.root = _RootShim(self._app)

    @property
    def muted(self) -> bool:
        return self._win._muted

    @muted.setter
    def muted(self, v: bool):
        if v != self._win._muted:
            self._win._toggle_mute()

    @property
    def current_file(self) -> str | None:
        return self._win._drop_zone.current_file()

    @property
    def on_text_command(self):
        return self._win.on_text_command

    @on_text_command.setter
    def on_text_command(self, cb):
        self._win.on_text_command = cb

    @property
    def on_remote_clicked(self):
        return self._win.on_remote_clicked

    @on_remote_clicked.setter
    def on_remote_clicked(self, cb):
        self._win.on_remote_clicked = cb

    @property
    def on_interrupt(self):
        return self._win.on_interrupt

    @on_interrupt.setter
    def on_interrupt(self, cb):
        self._win.on_interrupt = cb

    def notify_phone_connected(self) -> None:
        self._win.notify_phone_connected()

    def set_state(self, state: str):
        self._win._state_sig.emit(state)

    def write_log(self, text: str):
        self._win._log_sig.emit(text)

    def wait_for_api_key(self):
        while not self._win._ready:
            time.sleep(0.1)

    @property
    def is_serious_mode(self) -> bool:
        return self._win.is_serious_mode

    def set_serious_mode(self, enable: bool = True):
        """Thread-safe: ciddi mod/normal mod geçişini ana thread'de uygular."""
        self._win._serious_sig.emit(enable)

    def show_content(self, title: str, text: str):
        """Thread-safe: display content in the panel below the HUD."""
        self._win._content_sig.emit(title[:48], text[:4000])

    def prompt_reconfig(self):
        """Thread-safe: show the API key setup overlay (e.g. after an auth error)."""
        self._win._ready = False
        self._win._reconfig_sig.emit()

    def show_camera_frame(self, img_bytes: bytes):
        """Thread-safe: show a webcam frame in the small overlay (screen captures)."""
        self._win._camera_sig.emit(img_bytes)

    def start_camera_stream(self) -> None:
        """Thread-safe: start live camera feed in the full HUD area."""
        self._win.start_camera_stream()

    def stop_camera_stream(self) -> None:
        """Thread-safe: stop the live camera feed."""
        self._win.stop_camera_stream()

    # ── NAVİGATÖR (dahili tarayıcı) ──────────────────────────────────────────
    def browser_toggle(self) -> None:
        """Thread-safe: NAVİGATÖR'ü aç/kapat."""
        self._win._browser_sig.emit("toggle")

    def browser_open(self, url: str = "") -> None:
        """Thread-safe: NAVİGATÖR'ü aç. url '?' ile başlarsa Google araması."""
        self._win._browser_sig.emit("open:" + (url or ""))

    def browser_close(self) -> None:
        """Thread-safe: NAVİGATÖR'ü kapat."""
        self._win._browser_sig.emit("close")

    def browser_new_tab(self, url: str = "") -> None:
        """Thread-safe: yeni sekme aç (boşsa Google)."""
        self._win._browser_sig.emit("newtab:" + (url or ""))

    def browser_search(self, query: str) -> None:
        """Thread-safe: Google'da ara."""
        self._win._browser_sig.emit("search:" + (query or ""))

    def browser_close_tab(self) -> None:
        """Thread-safe: aktif sekmeyi kapat."""
        self._win._browser_sig.emit("closetab")

    def browser_set_dark(self, on: bool) -> None:
        """Thread-safe: karanlık mod zorlamasını aç/kapat."""
        self._win._browser_sig.emit("dark:" + ("1" if on else "0"))

    def browser_set_zoom(self, pct: int) -> None:
        """Thread-safe: varsayılan yakınlaştırma (%)."""
        self._win._browser_sig.emit(f"zoom:{int(pct)}")

    def browser_set_font(self, delta: int) -> None:
        """Thread-safe: yazı boyutu değişimi (pt farkı)."""
        self._win._browser_sig.emit(f"font:{int(delta)}")

    def browser_read_page(self, timeout: float = 10.0) -> dict:
        """Thread-safe: aktif sekmenin metnini okur (engelleyici).

        Dönen dict: {"ok", "title", "url", "text", "error?"}.
        Mehmet (arka plan thread'i) bunu çağırıp sayfa içeriğini
        özetleyebilir / sorulara cevap verebilir.
        """
        import json as _json
        import threading as _threading
        import uuid as _uuid

        token = _uuid.uuid4().hex
        ev = _threading.Event()
        rec = {"event": ev, "payload": ""}
        win = self._win

        win._page_waiters[token] = rec          # dict kaydı (GIL ile güvenli)
        win._browser_sig.emit("readpage:" + token)

        if not ev.wait(timeout):
            win._page_waiters.pop(token, None)
            return {"ok": False, "title": "", "url": "", "text": "",
                    "error": "sayfa okuma zaman aşımı"}
        try:
            meta = _json.loads(rec["payload"])
        except Exception:
            return {"ok": False, "title": "", "url": "", "text": "",
                    "error": "okuma yanıtı çözümlenemedi"}
        return meta

    # ── sesli sekme yönetimi + sayfa etkileşimi (thread-safe) ──────────────
    def browser_switch_tab(self, index: int) -> None:
        """Thread-safe: N. sekmeye geç (1-tabanlı)."""
        self._win._browser_sig.emit(f"switchtab:{int(index)}")

    def browser_close_all_tabs(self) -> None:
        """Thread-safe: aktif sekme dışındaki tüm sekmeleri kapat."""
        self._win._browser_sig.emit("closeall")

    def browser_list_tabs(self, timeout: float = 5.0) -> str:
        """Thread-safe: açık sekmeleri liste olarak döndürür (engelleyici)."""
        import json as _json
        import threading as _threading
        import uuid as _uuid

        token = _uuid.uuid4().hex
        ev = _threading.Event()
        rec = {"event": ev, "payload": ""}
        win = self._win
        win._page_waiters[token] = rec
        win._browser_sig.emit("listtabs:" + token)
        if not ev.wait(timeout):
            win._page_waiters.pop(token, None)
            return "Sekme listesi alınamadı (zaman aşımı)."
        try:
            return str(_json.loads(rec["payload"]).get("list", ""))
        except Exception:
            return "Sekme listesi çözümlenemedi."

    def browser_click(self, selector: str = "", text: str = "") -> dict:
        """Thread-safe: seçiciyle ya da görünür metinle öğeye tıkla."""
        import json as _json
        import threading as _threading
        import uuid as _uuid

        token = _uuid.uuid4().hex
        ev = _threading.Event()
        rec = {"event": ev, "payload": ""}
        win = self._win
        win._page_waiters[token] = rec
        cmd = "click:" + token + "\x1f" + _json.dumps(
            {"selector": selector, "text": text}, ensure_ascii=False)
        win._browser_sig.emit(cmd)
        if not ev.wait(8.0):
            win._page_waiters.pop(token, None)
            return {"ok": False, "error": "tıklama zaman aşımı"}
        try:
            return _json.loads(rec["payload"])
        except Exception:
            return {"ok": False, "error": "yanıt çözümlenemedi"}

    def browser_type(self, selector: str, text: str,
                     clear: bool = True) -> dict:
        """Thread-safe: seçiciyle bulunan girişe metin yaz."""
        import json as _json
        import threading as _threading
        import uuid as _uuid

        token = _uuid.uuid4().hex
        ev = _threading.Event()
        rec = {"event": ev, "payload": ""}
        win = self._win
        win._page_waiters[token] = rec
        cmd = "type:" + token + "\x1f" + _json.dumps(
            {"selector": selector, "text": text, "clear": clear},
            ensure_ascii=False)
        win._browser_sig.emit(cmd)
        if not ev.wait(8.0):
            win._page_waiters.pop(token, None)
            return {"ok": False, "error": "yazma zaman aşımı"}
        try:
            return _json.loads(rec["payload"])
        except Exception:
            return {"ok": False, "error": "yanıt çözümlenemedi"}

    def browser_fill_form(self, fields: dict) -> dict:
        """Thread-safe: çoklu form alanı doldur {"#ad": "değer", …}."""
        import json as _json
        import threading as _threading
        import uuid as _uuid

        token = _uuid.uuid4().hex
        ev = _threading.Event()
        rec = {"event": ev, "payload": ""}
        win = self._win
        win._page_waiters[token] = rec
        cmd = "fillform:" + token + "\x1f" + _json.dumps(fields,
                                                          ensure_ascii=False)
        win._browser_sig.emit(cmd)
        if not ev.wait(8.0):
            win._page_waiters.pop(token, None)
            return {"ok": False, "error": "form doldurma zaman aşımı"}
        try:
            return _json.loads(rec["payload"])
        except Exception:
            return {"ok": False, "error": "yanıt çözümlenemedi"}

    def browser_scroll(self, direction: str = "down", amount: int = 600) -> None:
        """Thread-safe: sayfayı kaydır (down|up)."""
        d = "down" if str(direction).lower() == "down" else "up"
        self._win._browser_sig.emit(f"scroll:{d}:{int(amount)}")

    def browser_press_key(self, key: str) -> None:
        """Thread-safe: aktif öğeye tuş gönder (Enter, Escape…)."""
        import json as _json
        k = (key or "Enter").strip()
        self._win._browser_sig.emit(
            "presskey:" + _json.dumps({"key": k}, ensure_ascii=False))

    # ── VPN tek tık + adblock durum (thread-safe) ──────────────────────────
    # ── izleme modu (watch party) ──────────────────────────────────────
    def set_watch_party(self, on: bool) -> None:
        """Thread-safe: izleme modu rozetini göster/kaldır."""
        self._win._watch_sig.emit(bool(on))

    def browser_active_url(self, timeout: float = 4.0) -> str:
        """Thread-safe (engelleyici): NAVİGATÖR aktif sekmesinin adresi."""
        import json as _json
        import threading as _threading
        import uuid as _uuid

        token = _uuid.uuid4().hex
        ev = _threading.Event()
        rec = {"event": ev, "payload": ""}
        win = self._win
        win._page_waiters[token] = rec
        win._browser_sig.emit("pageurl:" + token)
        if not ev.wait(timeout):
            win._page_waiters.pop(token, None)
            return ""
        try:
            return str(_json.loads(rec["payload"]).get("url", ""))
        except Exception:
            return ""

    def browser_vpn_toggle(self) -> None:
        """Thread-safe: VPN'i tek tıkla aç/kapa (Chrome eklentisi gibi)."""
        self._win._browser_sig.emit("vpntoggle")

    def browser_vpn_state(self) -> dict:
        """VPN anlık durumunu oku (ana thread'den; panel üzerinden)."""
        b = self._win._browser
        if b is None:
            return {"active": False, "status": "NAVİGATÖR kurulu değil"}
        vpn = getattr(b, "vpn", None)
        if vpn is None:
            return {"active": False, "status": "VPN modülü yok"}
        return {"active": bool(vpn.is_active),
                "status": vpn.status_text(),
                "upstream": vpn.upstream_label(),
                "port": vpn.relay.port}

    def browser_adblock_state(self) -> dict:
        """Adblock anlık durumunu oku."""
        b = self._win._browser
        if b is None:
            return {"enabled": False, "blocked": 0, "exists": False}
        eng = getattr(b, "adblock", None)
        if eng is None:
            return {"enabled": False, "blocked": 0, "exists": False}
        return {"enabled": bool(eng.enabled),
                "blocked": int(eng.stats.blocked),
                "rules": int(eng.rule_count),
                "exists": True}

    def browser_adblock_toggle(self) -> None:
        """Thread-safe: reklam engelleyiciyi aç/kapa."""
        self._win._browser_sig.emit("abtoggle")

    # ── çok adımlı görev zincirleri + medya araçları (thread-safe) ─────────
    def browser_run_chain(self, task: str, query: str = "",
                          timeout: float = 30.0) -> dict:
        """Thread-safe: çok adımlı web görevi.

        task: "youtube_play" (YouTube'da ara → ilk videoyu oynat)
              | "google_first" (Google'da ara → ilk sonucu aç)
        Dönüş: {"ok", "steps", "title", "url", "error?"}
        """
        import json as _json
        import threading as _threading
        import uuid as _uuid

        token = _uuid.uuid4().hex
        ev = _threading.Event()
        rec = {"event": ev, "payload": ""}
        win = self._win
        win._page_waiters[token] = rec
        win._browser_sig.emit("chain:" + token + "\x1f" + _json.dumps(
            {"task": task, "query": query}, ensure_ascii=False))
        if not ev.wait(timeout):
            win._page_waiters.pop(token, None)
            return {"ok": False, "steps": [], "title": "", "url": "",
                    "error": "görev zaman aşımı"}
        try:
            return _json.loads(rec["payload"])
        except Exception:
            return {"ok": False, "steps": [], "title": "", "url": "",
                    "error": "görev yanıtı çözümlenemedi"}

    def browser_screenshot(self, timeout: float = 8.0) -> dict:
        """Thread-safe: aktif sekmenin ekran görüntüsünü PNG kaydeder.
        Dönüş: {"ok", "path", "error?"}"""
        import json as _json
        import threading as _threading
        import uuid as _uuid

        token = _uuid.uuid4().hex
        ev = _threading.Event()
        rec = {"event": ev, "payload": ""}
        win = self._win
        win._page_waiters[token] = rec
        win._browser_sig.emit("shot:" + token)
        if not ev.wait(timeout):
            win._page_waiters.pop(token, None)
            return {"ok": False, "error": "ekran görüntüsü zaman aşımı"}
        try:
            return _json.loads(rec["payload"])
        except Exception:
            return {"ok": False, "error": "yanıt çözümlenemedi"}

    def browser_translate(self, target: str = "tr",
                          timeout: float = 25.0) -> dict:
        """Thread-safe: aktif sayfayı hedef dile çevirip overlay'de göster.
        Dönüş: {"ok", "text", "from", "error?"}"""
        import json as _json
        import threading as _threading
        import uuid as _uuid

        token = _uuid.uuid4().hex
        ev = _threading.Event()
        rec = {"event": ev, "payload": ""}
        win = self._win
        win._page_waiters[token] = rec
        win._browser_sig.emit("translate:" + token + "\x1f" + target)
        if not ev.wait(timeout):
            win._page_waiters.pop(token, None)
            return {"ok": False, "error": "çeviri zaman aşımı"}
        try:
            return _json.loads(rec["payload"])
        except Exception:
            return {"ok": False, "error": "yanıt çözümlenemedi"}

    def browser_vpn_speed_test(self, auto_connect: bool = False,
                               timeout: float = 60.0) -> dict:
        """Thread-safe: VPN profillerini ölç (auto_connect=True ise en
        hızlıya bağlan). Dönüş: {"ok", "results", "selected?", "error?"}
        (Ölçüm arka planda; bu çağrı ölçüm bitene kadar bekler.)"""
        import json as _json
        import threading as _threading
        import uuid as _uuid

        token = _uuid.uuid4().hex
        ev = _threading.Event()
        rec = {"event": ev, "payload": ""}
        win = self._win
        win._page_waiters[token] = rec
        win._browser_sig.emit("vpnspeed:" + token + "\x1f"
                              + ("1" if auto_connect else "0"))
        if not ev.wait(timeout):
            win._page_waiters.pop(token, None)
            return {"ok": False, "error": "hız testi zaman aşımı"}
        try:
            return _json.loads(rec["payload"])
        except Exception:
            return {"ok": False, "error": "yanıt çözümlenemedi"}

    def browser_capture(self, timeout: float = 8.0) -> dict:
        """Thread-safe: aktif sekmenin PNG görüntüsünü yakalar.
        Dönüş: {"ok", "path", "png": bytes, "mime", "title", "url", "error?"}"""
        import json as _json
        import threading as _threading
        import uuid as _uuid

        token = _uuid.uuid4().hex
        ev = _threading.Event()
        rec = {"event": ev, "payload": ""}
        win = self._win
        win._page_waiters[token] = rec
        win._browser_sig.emit("cap:" + token)
        if not ev.wait(timeout):
            win._page_waiters.pop(token, None)
            return {"ok": False, "error": "görüntü yakalama zaman aşımı"}
        try:
            data = _json.loads(rec["payload"])
        except Exception:
            return {"ok": False, "error": "yanıt çözümlenemedi"}
        if data.get("ok") and isinstance(data.get("png"), str):
            import base64 as _b64u
            try:
                data["png"] = _b64u.b64decode(data["png"])
            except Exception:
                data["png"] = b""
        return data

    def browser_vpn_connect_word(self, word: str = "",
                                 timeout: float = 30.0) -> dict:
        """Thread-safe: tek kelimeyle VPN'e bağlan ('eu', 'japonya',
        'en hızlı'…). Dönüş: {"ok", "message", "status"}"""
        import json as _json
        import threading as _threading
        import uuid as _uuid

        token = _uuid.uuid4().hex
        ev = _threading.Event()
        rec = {"event": ev, "payload": ""}
        win = self._win
        win._page_waiters[token] = rec
        win._browser_sig.emit("vpnconn:" + token + "\x1f" + (word or ""))
        if not ev.wait(timeout):
            win._page_waiters.pop(token, None)
            return {"ok": False, "message": "VPN bağlantı zaman aşımı",
                    "status": ""}
        try:
            return _json.loads(rec["payload"])
        except Exception:
            return {"ok": False, "message": "yanıt çözümlenemedi",
                    "status": ""}

    @property
    def browser_visible(self) -> bool:
        return self._win._browser_open

    @property
    def assistant_name(self) -> str:
        return self._win._assistant_name

    def start_speaking(self):
        self.set_state("SPEAKING")

    def stop_speaking(self):
        if not self.muted:
            self.set_state("LISTENING")
