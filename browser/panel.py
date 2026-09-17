# -*- coding: utf-8 -*-
"""
NAVİGATÖR — Mehmet'in ana pencere içinde çalışan sekmeli tarayıcı paneli.

Özellikler:
    • Klasik sekme sistemi (çoklu sekme, orta tuşla kapatma, + ile yeni sekme)
    • Google arama motoru entegrasyonu (adres çubuğu = arama + adres)
    • Adblock: kural motoru + kozmetik filtre enjeksiyonu (browser.adblock)
    • VPN: proxy profilleri, acil durdurma (browser.vpn)
    • Kişiselleştirme overlay'i: adblock kuralları, VPN profilleri,
      arama motoru seçimi — hepsi arayüzden yönetilir

Tüm renkler C sınıfından gelir: ana pencerede canlı tema değişimi
(retheme_all_widgets) bu paneli de otomatik günceller.
"""
from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import quote_plus
import threading as _threading
import time as _time

from PyQt6.QtCore import (
    Qt, QStringListModel, QTimer, QUrl, pyqtSignal,
)
from PyQt6.QtGui import QAction, QFont, QIcon, QMovie, QPixmap
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QCompleter, QFormLayout, QGridLayout,
    QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMenu, QPushButton,
    QStackedWidget, QTabWidget, QTextEdit,
    QVBoxLayout, QWidget,
)

from ui import C, _font, FONT_UI, FONT_MONO, FONT_DISPLAY
from browser import icons as _icons

# WebEngine isteğe bağlıdır — paket yoksa panel düzgün bir uyarı gösterir
try:
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    from PyQt6.QtWebEngineCore import (
        QWebEngineProfile, QWebEnginePage, QWebEngineScript,
        QWebEngineUrlRequestInterceptor, QWebEngineUrlRequestInfo,
        QWebEngineSettings,
    )
    WEBENGINE_OK = True
except Exception:  # pragma: no cover
    WEBENGINE_OK = False

if not WEBENGINE_OK:
    # Zarif düşüş: paket yoksa sınıf tanımları NameError vermesin diye
    # boş iskelet sınıflar sağlanır (panel uyarı metniyle açılır).
    class QWebEngineView:                       # noqa: D401
        pass

    class QWebEngineProfile:                    # noqa: D401
        pass

    class QWebEnginePage:                       # noqa: D401
        pass

    class QWebEngineScript:                     # noqa: D401
        pass

    class QWebEngineUrlRequestInterceptor:      # noqa: D401
        def __init__(self, *args, **kwargs):
            pass

    class QWebEngineUrlRequestInfo:             # noqa: D401
        pass

    class QWebEngineSettings:                   # noqa: D401
        pass

from browser.adblock import AdblockEngine
from browser.store import NavigatorStore
from browser.vpn import VpnManager, GLOBAL_RELAY, REGION_PRESETS

# hız testi & ekran görüntüsü yardımcıları
import threading as _threading
import time as _time

# Chrome tarzı TEMA galerisi için (ui içe aktarması zaten var)
try:
    from ui import apply_ui_accent as _apply_accent
except Exception:                          # pragma: no cover
    def _apply_accent(_hx):
        return False

# Tarayıcı temaları (Chrome galerisi gibi tek tıkla uygulanır)
NAV_THEMES = [
    ("JARVIS",   "#f2b632", "Klasik altın — BaranT imzası"),
    ("ELEKTRİK", "#2ec9ff", "Soğuk mavi — teknik görünüm"),
    ("MATRIX",   "#37e05f", "Yeşil fosfor terminal"),
    ("AMETHYST", "#a06bff", "Mor neon — gece modu"),
    ("RUBİ",     "#ff4d6d", "Kızıl alarm havası"),
    ("GÜMÜŞ",    "#c9d4dd", "Nötr gri-metalik"),
]

# Kalıcı tarayıcı tercihleri (config/navigator_ui.json)
NAV_UI_DEFAULTS = {"menu_vpn": True, "menu_dark": True,
                   "menu_zoom": 100, "theme": "JARVIS"}


def _load_nav_ui() -> dict:
    p = Path(__file__).resolve().parent.parent / "config" / "navigator_ui.json"
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        out = dict(NAV_UI_DEFAULTS)
        out.update({k: d[k] for k in NAV_UI_DEFAULTS if k in d})
        return out
    except Exception:
        return dict(NAV_UI_DEFAULTS)


def _save_nav_ui(d: dict) -> None:
    p = Path(__file__).resolve().parent.parent / "config" / "navigator_ui.json"
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(d, indent=1, ensure_ascii=False),
                     encoding="utf-8")
    except Exception:
        pass

_HOME_URL = "https://www.google.com"
_SEARCH_URL = "https://www.google.com/search?q="
_SCREENSHOT_DIR = Path(__file__).resolve().parent.parent / "screenshots"

# çeviri hedef dilleri (çeviri katmanı overlay'i için)
TRANSLATE_LANGS = [
    ("Türkçe", "tr"), ("English", "en"), ("Deutsch", "de"),
    ("Français", "fr"), ("Español", "es"), ("Italiano", "it"),
    ("日本語", "ja"), ("中文", "zh-CN"), ("Русский", "ru"),
    ("العربية", "ar"),
]


def _search_url(query: str) -> str:
    from urllib.parse import quote_plus
    return _SEARCH_URL + quote_plus(query)


def _normalize_address(text: str) -> str:
    """Adres çubuğu girdisini URL'ye çevir: kelime → Google araması,
    alan adı → https, tam URL → aynen."""
    text = text.strip()
    if not text:
        return _HOME_URL
    if "://" in text:
        return text
    if text.startswith("localhost") or text.startswith("127.0.0.1"):
        return "http://" + text
    if " " in text or "." not in text:
        return _search_url(text)
    host = text.split("/")[0]
    if host.replace(".", "").replace("-", "").isalnum() and "." in host:
        return "https://" + text
    return _search_url(text)


# ── WebEngine çekirdek yardımcıları ──────────────────────────────────────────

class _RequestInterceptor(QWebEngineUrlRequestInterceptor):
    """Kaynak isteklerini adblock motorundan geçirir."""

    def __init__(self, engine: AdblockEngine, parent=None):
        super().__init__(parent)
        self._engine = engine

    def interceptRequest(self, info: "QWebEngineUrlRequestInfo"):
        if self._engine.decide(info.requestUrl().toString()):
            info.block(True)


class _AdblockPage(QWebEnginePage):
    """Her sayfaya kozmetik filtreleri enjekte eden özel page sınıfı."""

    def __init__(self, profile, engine: AdblockEngine, parent=None):
        super().__init__(profile, parent)
        self._engine = engine

    def javaScriptConsoleMessage(self, level, message, lineNumber, sourceID):
        pass  # sayfa konsol gürültüsünü yut

# ── Tek sekme ────────────────────────────────────────────────────────────────

class _BrowserTab(QWidget):
    """Bir sekme = QWebEngineView + kozmetik enjeksiyon + durum sinyalleri."""

    title_changed = pyqtSignal(str)
    url_changed = pyqtSignal(str)
    load_progress = pyqtSignal(int)
    load_finished = pyqtSignal(bool)
    favicon_changed = pyqtSignal(QIcon)

    def __init__(self, profile, engine: AdblockEngine, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.page = _AdblockPage(profile, engine, self)
        self.view = QWebEngineView(self)
        self.view.setPage(self.page)
        lay.addWidget(self.view)

        v = self.view
        v.titleChanged.connect(self.title_changed)
        v.urlChanged.connect(lambda u: self.url_changed.emit(u.toString()))
        v.loadProgress.connect(self.load_progress)
        v.loadFinished.connect(self._on_finished)
        v.iconChanged.connect(self.favicon_changed)

        self._pending_url = ""

    # ── sayfa metni okuma (Mehmet için) ─────────────────────────────────
    def read_text(self, callback) -> None:
        """Sayfanın görünür metnini asenkron çeker; tek parametreyle
        `callback(text)` çağrılır (sayfa hazır değilse None)."""
        js = (
            "(function(){"
            "var s=document.body?document.body.innerText:'';"
            "return s.length>24000? s.slice(0,24000)+'…':s;})();"
        )
        self.view.page().runJavaScript(js, callback)

    # ── sayfa etkileşimi (Mehmet'in tıkla/yaz/kaydır yetenekleri) ────────
    @staticmethod
    def _js_str(s: str) -> str:
        return json.dumps(s, ensure_ascii=False)

    def js_click(self, selector: str, callback=None) -> None:
        """CSS seçicisiyle eşleşen ilk öğeye tıklar. callback(found:bool)."""
        sel = self._js_str(selector)
        js = ("(function(){var el=document.querySelector(" + sel + ");"
              "if(!el) return false;"
              "['mousedown','mouseup','click'].forEach(t=>{"
              "el.dispatchEvent(new MouseEvent(t,{bubbles:true,cancelable:true,"
              "view:window}));});"
              "el.click && el.click(); return true;})();")
        self.view.page().runJavaScript(js, callback)

    def js_type(self, selector: str, text: str, clear: bool = True,
                callback=None) -> None:
        """Seçiciyle bulunan girişe metin yazar (input olayını tetikler)."""
        sel = self._js_str(selector)
        val = self._js_str(text)
        js = ("(function(){var el=document.querySelector(" + sel + ");"
              "if(!el) return false;"
              "el.focus();"
              + ("el.value='';" if clear else "") +
              "el.value=" + val + ";"
              "el.dispatchEvent(new Event('input',{bubbles:true}));"
              "el.dispatchEvent(new Event('change',{bubbles:true}));"
              "return true;})();")
        self.view.page().runJavaScript(js, callback)

    def js_press_key(self, key: str, callback=None) -> None:
        """Odaklı öğeye / sayfaya tuş gönderir (Enter, Escape…)."""
        k = self._js_str(key)
        js = ("(function(){var t=document.activeElement||document.body;"
              "['keydown','keyup'].forEach(function(type){"
              "t.dispatchEvent(new KeyboardEvent(type,{key:" + k + ","
              "bubbles:true,cancelable:true}));}); return true;})();")
        self.view.page().runJavaScript(js, callback)

    def js_click_deep(self, selector: str, callback=None) -> None:
        """Seçiciyle bulunan öğeye tıklar; öğe bir bağlantı içindeyse
        (örn. <a><h3>başlık</h3></a>) tıklama EN YAKIN <a>'ya gider."""
        sel = self._js_str(selector)
        js = ("(function(){var el=document.querySelector(" + sel + ");"
              "if(!el) return false;"
              "var t=el.closest ? (el.closest('a')||el) : el;"
              "['mousedown','mouseup','click'].forEach(function(n){"
              "t.dispatchEvent(new MouseEvent(n,{bubbles:true,cancelable:true,"
              "view:window}));});"
              "if(t.click) t.click(); return true;})();")
        self.view.page().runJavaScript(js, callback)

    def js_scroll(self, direction: str = "down", amount: int = 600,
                  callback=None) -> None:
        """Sayfayı kaydırır."""
        dy = amount if direction == "down" else -amount
        js = f"window.scrollBy({{top: {int(dy)}, behavior: 'smooth'}}); true;"
        self.view.page().runJavaScript(js, callback)

    def js_find_and_click(self, text: str, callback=None) -> None:
        """Görünür metninden öğe bulur ve tıklar (Mehmet'in 'akıllı tık')."""
        needle = self._js_str(text.lower())
        js = ("(function(){var n=" + needle + ";"
              "var els=document.querySelectorAll('a,button,[role=button],input[type=submit],input[type=button]');"
              "for(var i=0;i<els.length;i++){var el=els[i];"
              "var t=(el.innerText||el.value||'').toLowerCase().trim();"
              "if(t && t.indexOf(n)>=0){"
              "['mousedown','mouseup','click'].forEach(t2=>{"
              "el.dispatchEvent(new MouseEvent(t2,{bubbles:true,cancelable:true,view:window}));});"
              "return true;}} return false;})();")
        self.view.page().runJavaScript(js, callback)

    def js_fill_form(self, fields: dict, callback=None) -> None:
        """{"#ad": "Mehmet", "#email": "..."} — çoklu alan doldurur.
        callback(filled:int)."""
        payload = json.dumps(fields, ensure_ascii=False)
        js = ("(function(){var f=" + payload + ";var n=0;"
              "for(var sel in f){var el=document.querySelector(sel);"
              "if(!el) continue; el.focus(); el.value=f[sel];"
              "el.dispatchEvent(new Event('input',{bubbles:true}));"
              "el.dispatchEvent(new Event('change',{bubbles:true})); n++;}"
              "return n;})();")
        self.view.page().runJavaScript(js, callback)
    # ── gezinme ──────────────────────────────────────────────────────────────
    def navigate(self, url: str) -> None:
        self._pending_url = url
        self.view.load(QUrl(url))

    def reload_cosmetic(self) -> None:
        """Aktif sayfaya kozmetik gizleme script'ini (yeniden) uygula.
        YouTube'da ayrıca player-reklam atlayıcıyı çalıştırır."""
        url = self.view.url().toString()
        engine = self.page._engine
        script = engine.cosmetic_script(url)
        if script:
            self.view.page().runJavaScript(
                script, QWebEngineScript.ScriptWorldId.ApplicationWorld)
        # YouTube: player reklam atlayıcı (skip + katman temizliği)
        if engine.enabled and "youtube.com" in AdblockEngine._host_of(url):
            try:
                self.view.page().runJavaScript(
                    engine.youtube_skip_script(),
                    QWebEngineScript.ScriptWorldId.ApplicationWorld)
            except Exception:
                pass

    def stop(self) -> None:
        """Yüklemeyi durdurur ve sinyal bağlantılarını koparır
        (kapanış sırasında 'deleted object' yarışlarını önler)."""
        try:
            self.view.stop()
            self.view.disconnect()
        except Exception:
            pass

    def _on_finished(self, ok: bool) -> None:
        self.load_finished.emit(ok)
        self.reload_cosmetic()


# ── Kişiselleştirme overlay'i ────────────────────────────────────────────────

class _CustomizeOverlay(QWidget):
    """Tarayıcı üstüne binen kişiselleştirme paneli: adblock, VPN, arama."""

    settings_changed = pyqtSignal()   # herhangi bir ayar değişti

    def __init__(self, engine: AdblockEngine, vpn: VpnManager,
                 store: NavigatorStore, parent=None):
        super().__init__(parent)
        self._engine = engine
        self._vpn = vpn
        self._store = store
        self.setObjectName("NavCustomize")
        self.setStyleSheet(f"""
            QWidget#NavCustomize {{
                background: {C.PANEL};
                border: 1px solid {C.BORDER_B};
                border-radius: 12px;
            }}
        """)
        self.hide()

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(8)

        hdr = QHBoxLayout()
        ttl_ico = QLabel()
        ttl_ico.setPixmap(_icons.get_pixmap("gear", 32, C.PRI))
        ttl_ico.setFixedSize(20, 20)
        ttl_ico.setScaledContents(True)
        ttl_ico.setStyleSheet("background: transparent;")
        hdr.addWidget(ttl_ico)
        ttl = QLabel("NAVİGATÖR KİŞİSELLEŞTİRME")
        ttl.setFont(_font(FONT_DISPLAY, 12, QFont.Weight.DemiBold))
        ttl.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        hdr.addWidget(ttl)
        hdr.addStretch()
        close = QPushButton()
        from PyQt6.QtGui import QIcon as _QIconO
        close.setIcon(_QIconO(_icons.get_pixmap("close", 32, C.TEXT_DIM)))
        close.setFixedSize(26, 26)
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.TEXT_DIM};
                           border: 1px solid {C.BORDER}; border-radius: 6px; }}
            QPushButton:hover {{ color: {C.PRI}; border-color: {C.PRI_DIM}; }}
        """)
        close.clicked.connect(self.hide)
        hdr.addWidget(close)
        root.addLayout(hdr)

        # ── sekmeler ────────────────────────────────────────────────────────
        self._tabs = QTabWidget()
        self._tabs.setFont(_font(FONT_UI, 9, QFont.Weight.DemiBold))
        self._tabs.setStyleSheet(f"""
            QTabWidget::pane {{ border: 1px solid {C.BORDER}; border-radius: 8px;
                                background: {C.PANEL2}; top: -1px; }}
            QTabBar::tab {{
                background: transparent; color: {C.TEXT_MED};
                padding: 6px 14px; border: 1px solid {C.BORDER};
                border-bottom: none;
                border-top-left-radius: 6px; border-top-right-radius: 6px;
            }}
            QTabBar::tab:selected {{ color: {C.PRI}; border-color: {C.PRI_DIM};
                                     background: {C.PRI_GHO}; }}
            QTabBar::tab:hover {{ color: {C.WHITE}; }}
        """)
        root.addWidget(self._tabs, stretch=1)

        self._build_adblock_tab()
        self._build_vpn_tab()
        self._build_search_tab()
        self._build_view_tab()

    # ── adblock sekmesi ──────────────────────────────────────────────────────
    def _build_adblock_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)

        self._ab_enabled = QCheckBox("Adblock motoru etkin")
        self._ab_enabled.setFont(_font(FONT_UI, 10, QFont.Weight.DemiBold))
        self._ab_enabled.setStyleSheet(
            f"color: {C.WHITE}; background: transparent;")
        self._ab_enabled.setChecked(self._engine.enabled)
        self._ab_enabled.toggled.connect(self._on_adblock_toggle)
        lay.addWidget(self._ab_enabled)

        info = QLabel(f"Kural sözdizimi: ||domain^ engeller · @@||domain^ istisna "
                      f"· domain##.css seçici gizler. Kaydet → motor yeniden derlenir.")
        info.setFont(_font(FONT_UI, 8))
        info.setWordWrap(True)
        info.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
        lay.addWidget(info)

        self._rules_edit = QTextEdit()
        self._rules_edit.setFont(_font(FONT_MONO, 9))
        self._rules_edit.setPlainText(self._engine.user_rules_text())
        self._rules_edit.setStyleSheet(f"""
            QTextEdit {{
                background: {C.DARK}; color: {C.TEXT};
                border: 1px solid {C.BORDER}; border-radius: 8px; padding: 6px;
            }}
        """)
        lay.addWidget(self._rules_edit, stretch=1)

        row = QHBoxLayout()
        save = QPushButton("KURALLARI KAYDET")
        save.setFont(_font(FONT_UI, 9, QFont.Weight.DemiBold))
        save.setCursor(Qt.CursorShape.PointingHandCursor)
        save.setStyleSheet(f"""
            QPushButton {{ background: {C.PRI_GHO}; color: {C.PRI};
                           border: 1px solid {C.PRI_DIM}; border-radius: 8px;
                           padding: 6px 14px; }}
            QPushButton:hover {{ border-color: {C.PRI}; color: {C.WHITE}; }}
        """)
        save.clicked.connect(self._save_rules)
        row.addWidget(save)

        self._ab_stats = QLabel(self._stats_text())
        self._ab_stats.setFont(_font(FONT_MONO, 9))
        self._ab_stats.setStyleSheet(f"color: {C.GREEN}; background: transparent;")
        row.addStretch()
        row.addWidget(self._ab_stats)
        lay.addLayout(row)

        self._tabs.addTab(w, _icons.get_icon("shield"), "  ADBLOCK")

    def _stats_text(self) -> str:
        s = self._engine.stats
        return (f"ENGELLENEN: {s.blocked}   "
                f"KOZMETİK: {len(self._engine.cosmetic_rules())} kural")

    def _on_adblock_toggle(self, on: bool):
        self._engine.set_enabled(on)
        self._ab_stats.setText(self._stats_text())
        self.settings_changed.emit()

    def _save_rules(self):
        self._engine.save_user_rules(self._rules_edit.toPlainText())
        self._ab_stats.setText(self._stats_text())
        self.settings_changed.emit()

    # ── vpn sekmesi ──────────────────────────────────────────────────────────
    def _build_vpn_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)

        warn = QLabel("VPN katmanı Chromium çekirdeğini seçilen proxy üzerinden "
                      "çalıştırır. Profil değişince sekmeler yeniden yüklenir.")
        warn.setFont(_font(FONT_UI, 8))
        warn.setWordWrap(True)
        warn.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
        lay.addWidget(warn)

        self._vpn_status = QLabel(self._vpn.status_text())
        self._vpn_status.setFont(_font(FONT_MONO, 10, QFont.Weight.DemiBold))
        self._vpn_status.setStyleSheet(f"color: {C.ACC2}; background: transparent;")
        lay.addWidget(self._vpn_status)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(6)

        def _lbl(t):
            l = QLabel(t)
            l.setFont(_font(FONT_UI, 9, QFont.Weight.DemiBold))
            l.setStyleSheet(f"color: {C.WHITE}; background: transparent;")
            return l

        self._vp_name = QLineEdit(); self._vp_host = QLineEdit()
        self._vp_port = QLineEdit(); self._vp_user = QLineEdit()
        self._vp_pass = QLineEdit(); self._vp_pass.setEchoMode(
            QLineEdit.EchoMode.Password)
        self._vp_proto = QComboBox(); self._vp_proto.addItems(["socks5", "http"])
        for fld in (self._vp_name, self._vp_host, self._vp_port,
                    self._vp_user, self._vp_pass):
            fld.setFont(_font(FONT_UI, 9))
            fld.setStyleSheet(f"""
                QLineEdit {{ background: {C.DARK}; color: {C.TEXT};
                             border: 1px solid {C.BORDER}; border-radius: 6px;
                             padding: 4px 8px; }}
                QLineEdit:focus {{ border-color: {C.PRI_DIM}; }}
            """)
        self._vp_proto.setFont(_font(FONT_UI, 9))
        self._vp_proto.setStyleSheet(f"""
            QComboBox {{ background: {C.DARK}; color: {C.TEXT};
                         border: 1px solid {C.BORDER}; border-radius: 6px;
                         padding: 4px 8px; }}
        """)

        form.addRow(_lbl("Profil adı"), self._vp_name)
        form.addRow(_lbl("Protokol"), self._vp_proto)
        form.addRow(_lbl("Host"), self._vp_host)
        form.addRow(_lbl("Port"), self._vp_port)
        form.addRow(_lbl("Kullanıcı"), self._vp_user)
        form.addRow(_lbl("Şifre"), self._vp_pass)
        lay.addLayout(form)

        btns = QHBoxLayout()
        add = QPushButton("+ PROFİL EKLE / GÜNCELLE")
        add.setFont(_font(FONT_UI, 9, QFont.Weight.DemiBold))
        add.setCursor(Qt.CursorShape.PointingHandCursor)
        add.setStyleSheet(f"""
            QPushButton {{ background: {C.PRI_GHO}; color: {C.PRI};
                           border: 1px solid {C.PRI_DIM}; border-radius: 8px;
                           padding: 5px 12px; }}
            QPushButton:hover {{ border-color: {C.PRI}; }}
        """)
        add.clicked.connect(self._add_profile)
        btns.addWidget(add)

        conn = QPushButton("BAĞLAN")
        conn.setFont(_font(FONT_UI, 9, QFont.Weight.DemiBold))
        conn.setCursor(Qt.CursorShape.PointingHandCursor)
        conn.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.GREEN};
                           border: 1px solid {C.GREEN}; border-radius: 8px;
                           padding: 5px 12px; }}
            QPushButton:hover {{ background: rgba(0,255,140,0.08); }}
        """)
        conn.clicked.connect(lambda: self._connect_profile())
        btns.addWidget(conn)

        disc = QPushButton("BAĞLANTIYI KES")
        disc.setFont(_font(FONT_UI, 9, QFont.Weight.DemiBold))
        disc.setCursor(Qt.CursorShape.PointingHandCursor)
        disc.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.MUTED_C};
                           border: 1px solid {C.MUTED_C}; border-radius: 8px;
                           padding: 5px 12px; }}
            QPushButton:hover {{ background: rgba(255,80,120,0.08); }}
        """)
        disc.clicked.connect(self._disconnect)
        btns.addWidget(disc)
        btns.addStretch()
        lay.addLayout(btns)

        self._ks = QCheckBox("Acil durdurma (kill switch) — proxy yokken trafiği kes")
        self._ks.setFont(_font(FONT_UI, 9))
        self._ks.setStyleSheet(f"color: {C.WHITE}; background: transparent;")
        self._ks.setChecked(self._vpn.kill_switch)
        self._ks.toggled.connect(self._on_kill_switch)
        lay.addWidget(self._ks)

        # ── hız testi + bölge ön ayarları ──────────────────────────────
        sp = QHBoxLayout()
        self._speed_btn = QPushButton(" HIZ TESTİ")
        self._speed_btn.setIcon(_icons.get_icon("bolt"))
        self._speed_btn.setFont(_font(FONT_UI, 9, QFont.Weight.DemiBold))
        self._speed_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._speed_btn.setToolTip("Tüm profilleri ölç, sonuçları sırala")
        self._speed_btn.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.PRI};
                           border: 1px solid {C.PRI_DIM}; border-radius: 8px;
                           padding: 5px 12px; }}
            QPushButton:hover {{ background: {C.PRI_GHO}; }}
            QPushButton:disabled {{ color: {C.BORDER}; border-color: {C.BORDER}; }}
        """)
        self._speed_btn.clicked.connect(self._run_speed_test)
        sp.addWidget(self._speed_btn)

        auto = QPushButton(" EN HIZLIYA BAĞLAN")
        auto.setIcon(_icons.get_icon("satellite"))
        auto.setFont(_font(FONT_UI, 9, QFont.Weight.DemiBold))
        auto.setCursor(Qt.CursorShape.PointingHandCursor)
        auto.setToolTip("Ölç ve otomatik olarak en hızlı profile bağlan")
        auto.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.GREEN};
                           border: 1px solid {C.GREEN}; border-radius: 8px;
                           padding: 5px 12px; }}
            QPushButton:hover {{ background: rgba(0,255,140,0.08); }}
            QPushButton:disabled {{ color: {C.BORDER}; border-color: {C.BORDER}; }}
        """)
        auto.clicked.connect(self._speed_best_and_connect)
        sp.addWidget(auto)

        regions = QPushButton(" BÖLGE EKLE")
        regions.setIcon(_icons.get_icon("globe"))
        regions.setFont(_font(FONT_UI, 9, QFont.Weight.DemiBold))
        regions.setCursor(Qt.CursorShape.PointingHandCursor)
        regions.setToolTip("Host'a yazdığın adres için 8 hazır bölge profili oluşturur")
        regions.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.ACC2};
                           border: 1px solid {C.BORDER}; border-radius: 8px;
                           padding: 5px 12px; }}
            QPushButton:hover {{ border-color: {C.ACC2}; }}
        """)
        regions.clicked.connect(self._add_region_presets)
        sp.addWidget(regions)
        sp.addStretch()
        lay.addLayout(sp)

        self._vp_list = QLabel()
        self._vp_list.setFont(_font(FONT_MONO, 8))
        self._vp_list.setWordWrap(True)
        self._vp_list.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
        # ölçüm sırasında canlı GIF göstergesi (bolt animasyonu)
        self._vp_movie = QLabel()
        self._vp_movie.setStyleSheet("background: transparent;")
        gif = _icons.get_gif_path("bolt")
        self._vp_movie.setMovie(QMovie(gif) if gif else None)
        self._vp_movie.hide()
        vp_row = QHBoxLayout()
        vp_row.setSpacing(6)
        vp_row.addWidget(self._vp_movie)
        vp_row.addWidget(self._vp_list, stretch=1)
        lay.addLayout(vp_row)
        self._refresh_profile_list()

        self._tabs.addTab(w, _icons.get_icon("satellite"), "  VPN")

    def _vpn_status_text(self) -> str:
        return self._vpn.status_text()

    def refresh_vpn(self):
        """VPN durumu dışarıdan değişti (tek tık buton) → sekme tazelensin."""
        if hasattr(self, "_vp_list"):
            self._refresh_profile_list()
        if hasattr(self, "_vpn_status"):
            self._vpn_status.setText(self._vpn_status_text())

    def _refresh_profile_list(self):
        lines = []
        for p in self._vpn.profiles:
            ok = "✔" if self._vpn.is_profile_complete(p) else "…"
            active = " ◄ AKTİF" if p.get("id") == self._vpn.active_id else ""
            lines.append(f"{ok} {p.get('name','?')} — "
                         f"{p.get('protocol','?')}://{p.get('host') or '?'}:"
                         f"{p.get('port') or '?'}{active}")
        self._vp_list.setText("\n".join(lines) or "Profil yok.")
        self._vpn_status.setText(self._vpn_status_text())

    def _add_profile(self):
        name = self._vp_name.text().strip() or "Profil"
        try:
            port = int(self._vp_port.text().strip() or "0")
        except ValueError:
            port = 0
        prof = {
            "id": "",
            "name": name,
            "region": name[:2].upper(),
            "protocol": self._vp_proto.currentText(),
            "host": self._vp_host.text().strip(),
            "port": port,
            "username": self._vp_user.text(),
            "password": self._vp_pass.text(),
        }
        self._vpn.upsert_profile(prof)
        self._refresh_profile_list()
        self.settings_changed.emit()

    def _connect_profile(self):
        # listeden tam eşleşen ilk profili bul
        for p in self._vpn.profiles:
            if (self._vp_host.text().strip() == (p.get("host") or "")
                    and str(p.get("port") or "") == self._vp_port.text().strip()):
                ok, _ = self._vpn.connect(p["id"])
                break
        else:
            # kayıtlı değilse önce ekle
            self._add_profile()
            ok, _ = self._vpn.connect(self._vpn.profiles[-1]["id"])
        self._refresh_profile_list()
        self.settings_changed.emit()

    def _disconnect(self):
        self._vpn.disconnect()
        self._refresh_profile_list()
        self.settings_changed.emit()

    def _on_kill_switch(self, on):
        self._vpn.set_kill_switch(on)
        self.settings_changed.emit()

    # ── VPN hız testi ────────────────────────────────────────────────────
        # ── VPN hız testi ────────────────────────────────────────────────────
    def _speed_busy(self, on: bool) -> None:
        """Hız testi çalışırken bolt GIF'ini göster/duraklat."""
        mv = getattr(self, "_vp_movie", None)
        if mv is None or mv.movie() is None:
            return
        if on:
            mv.movie().start()
            mv.show()
        else:
            mv.movie().stop()
            mv.hide()

    def _run_speed_test(self):
        """Tüm profilleri arka planda ölç; sonuçları listeye yaz,
        en hızlısını vurgula."""
        if getattr(self, "_speed_running", False):
            return
        self._speed_running = True
        self._speed_btn.setEnabled(False)
        self._speed_busy(True)
        self._vp_list.setText("Hız testi çalışıyor — tüm profiller "
                              "sırayla ölçülüyor (birkaç saniye sürebilir)…")

        def work():
            results = self._vpn.speed_test(5.0)
            # ana thread'e dön
            QTimer.singleShot(0, lambda: self._speed_done(results))

        _threading.Thread(target=work, daemon=True).start()

    def _speed_done(self, results):
        self._speed_running = False
        self._speed_btn.setEnabled(True)
        self._speed_busy(False)
        ok = [(n, r, ms) for n, r, ms in results if ms is not None]
        ok.sort(key=lambda x: x[2])
        lines = []
        for i, (n, r, ms) in enumerate(ok):
            mark = (">" if i == 0 else (">>" if i == 1 else (">>>" if i == 2 else "  ")))
            lines.append(f"{mark} {n} [{r}] — {ms:.0f} ms")
        for n, r, ms in results:
            if ms is None:
                lines.append(f"x {n} [{r}] — erişilemedi")
        if ok:
            best = ok[0]
            lines.append("")
            lines.append(f"► En hızlı: {best[0]} ({best[2]:.0f} ms) — "
                         "'en hızlıya bağlan' ile otomatik seçilir.")
        self._vp_list.setText("\n".join(lines) or "Ölçülecek tam profil yok.")
        self._vpn_status.setText(self._vpn_status_text())

    def _speed_best_and_connect(self):
        """Ölç → en hızlı profili otomatik seç → VPN'i aç."""
        if getattr(self, "_speed_running", False):
            return
        self._speed_running = True
        self._speed_btn.setEnabled(False)
        self._speed_busy(True)
        self._vp_list.setText("Profiller ölçülüyor ve en hızlısına "
                              "bağlanılıyor…")

        def work():
            p, results = self._vpn.auto_select_fastest(5.0)
            QTimer.singleShot(0, lambda: self._speed_best_done(p, results))

        _threading.Thread(target=work, daemon=True).start()

    def _speed_best_done(self, p, results):
        self._speed_running = False
        self._speed_btn.setEnabled(True)
        if p is None:
            reachable = [r for r in results if r[2] is not None]
            self._vp_list.setText(
                "x Hiçbir profile ulaşılamadı.\n" +
                "\n".join(f"- {n} [{r}]" for n, r, _ in results))
        else:
            self._refresh_profile_list()
            self._sync_vpn_button()
            self._vp_list.setText(
                f"OK En hızlı profile bağlanıldı: {p.get('name')}\n"
                f"{self._vpn.upstream_label()}")
            self._log_line("SİSTEM: VPN en hızlı profile bağlandı — "
                           + p.get("name", ""))
        self.settings_changed.emit()

    def _add_region_presets(self):
        """Hazır bölge profillerini ekler (host boşsa uyarı verir)."""
        host = self._vp_host.text().strip()
        if not host:
            self._vp_list.setText("UYARI: Önce 'Host' alanına VPN sunucu adresini "
                                  "yaz, sonra bölge ön ayarlarını ekle.")
            return
        proto = self._vp_proto.currentText()
        n = self._vpn.add_region_presets(host, proto,
                                         self._vp_user.text(),
                                         self._vp_pass.text())
        self._refresh_profile_list()
        self._log_line(f"SİSTEM: {n} bölge profili eklendi ({host}).")

    # ── arama sekmesi ────────────────────────────────────────────────────────
    def _build_search_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)

        info = QLabel("Adres çubuğunda kullanılacak varsayılan arama motoru. "
                      "NAVİGATÖR Google ile birlikte gelir.")
        info.setFont(_font(FONT_UI, 8))
        info.setWordWrap(True)
        info.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
        lay.addWidget(info)

        self._engine_box = QComboBox()
        self._engine_box.setFont(_font(FONT_UI, 10))
        self._engine_box.addItems(["Google", "DuckDuckGo", "Bing", "Yandex"])
        self._engine_box.setStyleSheet(f"""
            QComboBox {{ background: {C.DARK}; color: {C.TEXT};
                         border: 1px solid {C.BORDER}; border-radius: 8px;
                         padding: 6px 10px; }}
        """)
        lay.addWidget(self._engine_box)

        note = QLabel("Bağlantı gizliliği: VPN pasifken istekler doğrudan "
                      "gider. Adblock her modda çalışır.")
        note.setFont(_font(FONT_UI, 8))
        note.setWordWrap(True)
        note.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        lay.addWidget(note)
        lay.addStretch()

        self._tabs.addTab(w, "🔎  ARAMA")

    # ── görünüm sekmesi ───────────────────────────────────────────────────
    def _build_view_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)
        v = self._store.get_view()

        self._dark_box = QCheckBox("Karanlık mod zorla (Force Dark)")
        self._dark_box.setFont(_font(FONT_UI, 10, QFont.Weight.DemiBold))
        self._dark_box.setStyleSheet(f"color: {C.WHITE}; background: transparent;")
        self._dark_box.setChecked(bool(v.get("dark")))
        self._dark_box.toggled.connect(lambda on: self._set_view(dark=bool(on)))
        lay.addWidget(self._dark_box)

        hint = QLabel("Karanlık mod yeni yüklenen sayfalarda tam etkinleşir.")
        hint.setFont(_font(FONT_UI, 8))
        hint.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
        lay.addWidget(hint)

        frow = QHBoxLayout()
        flab = QLabel("Yazı boyutu")
        flab.setFont(_font(FONT_UI, 9, QFont.Weight.DemiBold))
        flab.setStyleSheet(f"color: {C.WHITE}; background: transparent;")
        frow.addWidget(flab)
        self._font_box = QComboBox()
        self._font_box.addItems(["Küçük", "Normal", "Büyük", "Çok Büyük"])
        idx = 1 + int(v.get("font_pt", 0))
        self._font_box.setCurrentIndex(max(0, min(3, idx)))
        self._font_box.currentIndexChanged.connect(
            lambda i: self._set_view(font_pt=int(i) - 1))
        frow.addWidget(self._font_box, stretch=1)
        lay.addLayout(frow)

        zrow = QHBoxLayout()
        zlab = QLabel("Varsayılan yakınlaştırma")
        zlab.setFont(_font(FONT_UI, 9, QFont.Weight.DemiBold))
        zlab.setStyleSheet(f"color: {C.WHITE}; background: transparent;")
        zrow.addWidget(zlab)
        self._zoom_box = QComboBox()
        for pct in (67, 80, 100, 120, 140, 170, 200):
            self._zoom_box.addItem(f"%{pct}", pct)
        pos = self._zoom_box.findData(int(v.get("zoom_pct", 100)))
        self._zoom_box.setCurrentIndex(pos if pos >= 0 else 2)
        self._zoom_box.currentIndexChanged.connect(
            lambda _i: self._set_view(
                zoom_pct=int(self._zoom_box.currentData() or 100)))
        zrow.addWidget(self._zoom_box, stretch=1)
        lay.addLayout(zrow)

        hist_row = QHBoxLayout()
        clr = QPushButton("GEÇMİŞİ TEMİZLE")
        clr.setFont(_font(FONT_UI, 9, QFont.Weight.DemiBold))
        clr.setCursor(Qt.CursorShape.PointingHandCursor)
        clr.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.MUTED_C};
                           border: 1px solid {C.MUTED_C}; border-radius: 8px;
                           padding: 5px 12px; }}
            QPushButton:hover {{ background: rgba(255,80,120,0.08); }}
        """)
        clr.clicked.connect(self._clear_history)
        hist_row.addWidget(clr)
        hist_row.addStretch()
        lay.addLayout(hist_row)
        lay.addStretch()

        self._tabs.addTab(w, _icons.get_icon("moon"), "  GÖRÜNÜM")

    def _set_view(self, **kv):
        self._store.set_view(**kv)
        self.settings_changed.emit()

    def _clear_history(self):
        self._store.clear_history()
        self.settings_changed.emit()


# ── Ana tarayıcı paneli ──────────────────────────────────────────────────────

class BrowserPanel(QWidget):
    """Ana pencere içine gömülen tam tarayıcı. Klasik sekme sistemi."""

    exit_requested = pyqtSignal()   # "Mehmet'e dön" (HUD katmanına geri)
    vpn_alert = pyqtSignal(str, str)  # (url, uyarı) — kritik sitelerde VPN kapalı

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("NavigatorPanel")

        base = Path(__file__).resolve().parent.parent / "config"
        self.adblock = AdblockEngine(base / "adblock_rules.txt")
        self.vpn = VpnManager(base / "vpn.json")
        self._vpn = self.vpn          # kısa takma ad (tutarlı erişim)
        self.store = NavigatorStore(base / "navigator_store.json")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── üst araç çubuğu ──────────────────────────────────────────────────
        bar = QWidget()
        bar.setObjectName("NavToolbar")
        bar.setStyleSheet(f"""
            QWidget#NavToolbar {{
                background: {C.PANEL};
                border-bottom: 1px solid {C.BORDER_B};
            }}
        """)
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(10, 6, 10, 6)
        bl.setSpacing(6)

        def _tool_btn(txt, tip, checked_style=False):
            b = QPushButton(txt)
            b.setFixedSize(34, 30)
            b.setFont(_font(FONT_DISPLAY, 12, QFont.Weight.DemiBold))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setToolTip(tip)
            b.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; color: {C.TEXT_DIM};
                    border: 1px solid {C.BORDER}; border-radius: 7px;
                }}
                QPushButton:hover {{ color: {C.PRI}; border-color: {C.PRI_DIM}; }}
                QPushButton:checked {{ color: {C.PRI}; border-color: {C.PRI};
                                       background: {C.PRI_GHO}; }}
                QPushButton:disabled {{ color: {C.BORDER}; border-color: {C.BORDER}; }}
            """)
            return b

        self._btn_back = _tool_btn("", "Geri")
        self._btn_fwd = _tool_btn("", "İleri")
        self._btn_reload = _tool_btn("", "Yenile")
        self._btn_home = _tool_btn("", "Google ana sayfa")
        self._btn_back.setIcon(_icons.get_icon("back"))
        self._btn_fwd.setIcon(_icons.get_icon("forward"))
        self._btn_reload.setIcon(_icons.get_icon("reload"))
        self._btn_home.setIcon(_icons.get_icon("home"))
        self._btn_back.setIconSize(self._btn_back.iconSize().scaled(20, 20, Qt.AspectRatioMode.KeepAspectRatio))
        self._btn_fwd.setIconSize(self._btn_back.iconSize())
        self._btn_reload.setIconSize(self._btn_back.iconSize())
        self._btn_home.setIconSize(self._btn_back.iconSize())
        for b, slot in ((self._btn_back, self._go_back),
                        (self._btn_fwd, self._go_forward),
                        (self._btn_reload, self._reload),
                        (self._btn_home, self._go_home)):
            b.clicked.connect(slot)
            bl.addWidget(b)

        self._url_edit = QLineEdit()
        self._url_edit.setFont(_font(FONT_MONO, 10))
        self._url_edit.setPlaceholderText(
            "Ara veya adres gir — Google ile aranır  ⏎")
        self._url_edit.setStyleSheet(f"""
            QLineEdit {{
                background: {C.DARK}; color: {C.TEXT};
                border: 1px solid {C.BORDER}; border-radius: 15px;
                padding: 5px 14px; selection-background-color: {C.PRI};
            }}
            QLineEdit:focus {{ border-color: {C.PRI_DIM}; }}
        """)
        self._url_edit.returnPressed.connect(self._on_address)
        bl.addWidget(self._url_edit, stretch=1)

        self._bm_btn = _tool_btn("", "Sık kullanılanlara ekle / çıkar")
        self._bm_btn.setIcon(_icons.get_icon("star"))
        self._bm_btn.setIconSize(self._btn_back.iconSize())
        self._bm_btn.setCheckable(True)
        self._bm_btn.toggled.connect(self._toggle_bookmark)
        bl.addWidget(self._bm_btn)

        self._ab_btn = QPushButton()
        self._ab_btn.setIcon(_icons.get_icon("shield"))
        self._ab_btn.setIconSize(self._btn_back.iconSize())
        self._ab_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._ab_btn.setToolTip("Adblock durumunu değiştir")
        self._ab_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.GREEN};
                border: 1px solid {C.BORDER}; border-radius: 7px;
            }}
            QPushButton:hover {{ border-color: {C.PRI_DIM}; }}
            QPushButton:checked {{ color: {C.MUTED_C}; }}
        """)
        self._ab_btn.setCheckable(True)
        # setChecked sinyal ateşlediği için connect'ten ÖNCE yapılır
        # (init sırasında _stack henüz yokken slot çalışmasın)
        self._ab_btn.setChecked(self.adblock.enabled)
        self._ab_btn.toggled.connect(self._toggle_adblock)
        bl.addWidget(self._ab_btn)

        self._vpn_btn = QPushButton()
        self._vpn_btn.setIcon(_icons.get_icon("satellite"))
        self._vpn_btn.setIconSize(self._btn_back.iconSize())
        self._vpn_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._vpn_btn.setCheckable(True)
        self._vpn_btn.setToolTip("VPN'i AÇ/KAPA (tek tık)")
        self._vpn_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.ACC2};
                border: 1px solid {C.BORDER}; border-radius: 7px;
            }}
            QPushButton:hover {{ border-color: {C.PRI_DIM}; }}
            QPushButton:checked {{
                color: {C.GREEN}; border-color: {C.GREEN};
                background: rgba(0, 255, 140, 0.10);
            }}
        """)
        self._vpn_btn.clicked.connect(self._toggle_vpn_quick)
        bl.addWidget(self._vpn_btn)

        # ⋮ Chrome tarzı ana menü
        self._menu_btn = QPushButton()
        self._menu_btn.setIcon(_icons.get_icon("menu"))
        self._menu_btn.setIconSize(self._btn_back.iconSize())
        self._menu_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._menu_btn.setToolTip("NAVİGATÖR ana menüsü")
        self._menu_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 7px;
            }}
            QPushButton:hover {{ color: {C.PRI}; border-color: {C.PRI_DIM}; }}
        """)
        self._menu_btn.clicked.connect(self._open_main_menu)
        bl.addWidget(self._menu_btn)

        self._btn_cfg = _tool_btn("", "Kişiselleştir")
        self._btn_cfg.setIcon(_icons.get_icon("gear"))
        self._btn_cfg.setIconSize(self._btn_back.iconSize())
        self._btn_cfg.clicked.connect(self._show_customize)
        bl.addWidget(self._btn_cfg)

        # belirgin kırmızı ✕ — tarayıcıyı kapat, Mehmet'e dön
        self._exit_btn = _tool_btn("", "Tarayıcıyı kapat — Mehmet'e dön (F6)")
        from PyQt6.QtGui import QIcon as _QIcon
        self._exit_btn.setIcon(_QIcon(_icons.get_pixmap("close", 32, C.RED)))
        self._exit_btn.setIconSize(self._btn_back.iconSize())
        self._exit_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.RED};
                border: 1px solid {C.RED}; border-radius: 7px;
                font-weight: 700;
            }}
            QPushButton:hover {{ background: {C.RED}; color: {C.WHITE}; }}
        """)
        self._exit_btn.clicked.connect(self.exit_requested.emit)
        bl.addWidget(self._exit_btn)

        root.addWidget(bar)

        # ── sekme alanı: QTabWidget (sekmeler + sayfa bölmesi) kalan alanın
        #    TAMAMINI doldurur — web sayfası asla küçülmez ───────────────────
        self._tabwidget = QTabWidget()
        self._tabwidget.setFont(_font(FONT_UI, 9))
        self._tabwidget.setTabsClosable(True)
        self._tabwidget.setMovable(True)
        self._tabwidget.setDocumentMode(True)
        self._tabwidget.setStyleSheet(f"""
            QTabWidget::pane {{ border: 1px solid {C.BORDER}; top: -1px;
                                background: {C.BG}; }}
            QTabBar::tab {{
                background: {C.PANEL2}; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-bottom: none;
                border-top-left-radius: 7px; border-top-right-radius: 7px;
                padding: 5px 12px; margin-right: 3px;
                max-width: 190px; min-width: 90px;
            }}
            QTabBar::tab:selected {{ background: {C.PRI_GHO};
                                     color: {C.PRI}; border-color: {C.PRI_DIM}; }}
            QTabBar::tab:hover {{ color: {C.WHITE}; }}
        """)
        self._tabwidget.tabCloseRequested.connect(self._close_tab)
        self._tabwidget.currentChanged.connect(self._on_tab_switched)
        root.addWidget(self._tabwidget, stretch=1)

        self._new_tab_btn = QPushButton("＋")
        self._new_tab_btn.setFixedSize(30, 26)
        self._new_tab_btn.setFont(_font(FONT_DISPLAY, 13, QFont.Weight.DemiBold))
        self._new_tab_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._new_tab_btn.setToolTip("Yeni sekme")
        self._new_tab_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.PRI_DIM};
                border: 1px solid {C.BORDER}; border-radius: 7px;
            }}
            QPushButton:hover {{ color: {C.PRI}; border-color: {C.PRI_DIM}; }}
        """)
        self._new_tab_btn.clicked.connect(lambda: self.add_tab())
        # ＋ düğmesi sekme çubuğunun sağ köşesinde (klasik tarayıcı düzeni)
        self._tabwidget.setCornerWidget(self._new_tab_btn, Qt.Corner.TopRightCorner)

        # ── durum çubuğu ─────────────────────────────────────────────────────
        status = QWidget()
        status.setObjectName("NavStatus")
        status.setStyleSheet(f"""
            QWidget#NavStatus {{
                background: {C.PANEL2}; border-top: 1px solid {C.BORDER};
            }}
        """)
        sl = QHBoxLayout(status)
        sl.setContentsMargins(10, 3, 10, 3)
        sl.setSpacing(10)
        self._status_lbl = QLabel("NAVİGATÖR HAZIR")
        self._status_lbl.setFont(_font(FONT_MONO, 8, QFont.Weight.DemiBold))
        self._status_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        sl.addWidget(self._status_lbl)
        sl.addStretch()
        def _st_icon(name: str, color: str) -> QLabel:
            lb = QLabel()
            lb.setPixmap(_icons.get_pixmap(name, 32, color))
            lb.setFixedSize(18, 18)
            lb.setScaledContents(True)
            lb.setStyleSheet("background: transparent;")
            return lb

        self._ab_ico = _st_icon("shield", C.GREEN)
        self._ab_count_lbl = QLabel()
        self._ab_count_lbl.setFont(_font(FONT_MONO, 8, QFont.Weight.DemiBold))
        self._ab_count_lbl.setStyleSheet(f"color: {C.GREEN}; background: transparent;")
        sl.addWidget(self._ab_ico)
        sl.addWidget(self._ab_count_lbl)
        self._vpn_ico = _st_icon("satellite", C.ACC2)
        self._vpn_lbl = QLabel()
        self._vpn_lbl.setFont(_font(FONT_MONO, 8, QFont.Weight.DemiBold))
        self._vpn_lbl.setStyleSheet(f"color: {C.ACC2}; background: transparent;")
        sl.addWidget(self._vpn_ico)
        sl.addWidget(self._vpn_lbl)
        self._refresh_status()
        root.addWidget(status)

        # ── WebEngine kurulumu + overlay ─────────────────────────────────────
        # Not: sayfalar QTabWidget bölmesi içinde yaşar; _stack artık yalnızca
        # WebEngine kullanılabilirlik bayrağıdır (mevcut None-kontrolleri korunur).
        if WEBENGINE_OK:
            self._profile = QWebEngineProfile("barant-navigator", self)
            self._profile.setHttpUserAgent(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
            self._interceptor = _RequestInterceptor(self.adblock, self._profile)
            self._profile.setUrlRequestInterceptor(self._interceptor)
            self._stack = QStackedWidget(self)
            self._stack.hide()
            self._fallback_lbl = None
            self.add_tab()
        else:
            self._stack = None
            self._fallback_lbl = QLabel(
                "NAVİGATÖR için PyQt6-WebEngine eksik.\n\n"
                "Kurulum:  pip install PyQt6-WebEngine\n\n"
                "Ardından Mehmet'i yeniden başlat.")
            self._fallback_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._fallback_lbl.setFont(_font(FONT_UI, 11, QFont.Weight.DemiBold))
            self._fallback_lbl.setStyleSheet(
                f"color: {C.TEXT_MED}; background: {C.BG};")
            root.addWidget(self._fallback_lbl, stretch=1)

        # kişiselleştirme overlay'i (panel üstünde yüzer)
        self._customize = _CustomizeOverlay(self.adblock, self.vpn, self.store, self)
        self._customize.settings_changed.connect(self._on_settings_changed)
        self._customize.hide()

        # akıllı adres tamamlama + kayıtlı görüntü ayarları
        self._init_address_suggestions()
        self._apply_view_to_pages()

    # ── sekme yönetimi ───────────────────────────────────────────────────────
    def add_tab(self, url: str = _HOME_URL, activate: bool = True) -> "_BrowserTab | None":
        if not WEBENGINE_OK or self._stack is None:
            return None
        tab = _BrowserTab(self._profile, self.adblock, self)
        idx = self._tabwidget.addTab(tab, "Yeni Sekme")
        # widget referansıyla bağla: taşınabilir sekmelerde indeks kayar
        tab.title_changed.connect(lambda t, w=tab: self._on_title(w, t))
        tab.url_changed.connect(lambda u, w=tab: self._on_url(w, u))
        tab.load_progress.connect(self._on_load_progress)
        tab.load_finished.connect(lambda ok, w=tab: self._on_load_finished(w, ok))
        tab.favicon_changed.connect(lambda ic, w=tab: self._set_tab_icon(w, ic))
        tab.navigate(url)
        if activate:
            self._tabwidget.setCurrentIndex(idx)
        return tab

    def _current_tab(self) -> "_BrowserTab | None":
        if not WEBENGINE_OK or self._stack is None:
            return None
        w = self._tabwidget.currentWidget()
        return w if isinstance(w, _BrowserTab) else None

    def _close_tab(self, index: int):
        if self._tabwidget.count() <= 1:
            self._tabwidget.widget(index).deleteLater()
            self.add_tab()
            return
        w = self._tabwidget.widget(index)
        if isinstance(w, _BrowserTab):
            w.stop()
        self._tabwidget.removeTab(index)
        w.deleteLater()

    def _on_tab_switched(self, index: int):
        t = self._current_tab()
        if t:
            self._url_edit.setText(t.view.url().toString())
            self._refresh_status()

    def _on_title(self, tab, title: str):
        i = self._tabwidget.indexOf(tab)
        if i >= 0:
            self._tabwidget.setTabText(i, title[:28] or "Sekme")
            self._tabwidget.setTabToolTip(i, title)

    def _on_url(self, tab, url: str):
        if self._tabwidget.currentWidget() is tab:
            self._url_edit.setText(url)
            self._refresh_status()
            self._update_bm_btn()

    def _set_tab_icon(self, tab, icon: QIcon):
        i = self._tabwidget.indexOf(tab)
        if i >= 0:
            self._tabwidget.setTabIcon(i, icon)

    def _on_load_progress(self, pct: int):
        self._status_lbl.setText(f"YÜKLENİYOR… %{pct}")

    def _on_load_finished(self, tab, ok: bool):
        self._status_lbl.setText("NAVİGATÖR HAZIR" if ok else "YÜKLEME HATASI")
        self._update_ab_count()
        if ok and tab is not None:
            url = tab.view.url().toString()
            if url.startswith(("http://", "https://")):
                self.store.add_visit(url, tab.view.title() or url)
                # VPN otomatik koruma: kritik site + VPN kapalı → Mehmet'e haber ver
                warn = self.vpn.check_protection(url)
                if warn:
                    self._log_line("UYARI: " + warn)
                    self.vpn_alert.emit(url, warn)
            self._update_suggestions()
        self._update_bm_btn()

    # ── gezinme ──────────────────────────────────────────────────────────────
    def _on_address(self):
        t = self._current_tab()
        if not t:
            return
        url = _normalize_address(self._url_edit.text())
        t.navigate(url)
        t.view.setFocus()

    def navigate(self, url: str):
        """Dış arayüz için: aktif sekmede URL aç (yoksa sekme yarat)."""
        if self._current_tab() is None:
            self.add_tab(url)
        else:
            self._current_tab().navigate(url)
            self._url_edit.setText(url)

    def _go_back(self):
        t = self._current_tab()
        if t:
            t.view.back()

    def _go_forward(self):
        t = self._current_tab()
        if t:
            t.view.forward()

    def _reload(self):
        t = self._current_tab()
        if t:
            t.view.reload()

    def _go_home(self):
        self.navigate(_HOME_URL)

    # ── adblock / vpn ────────────────────────────────────────────────────────
    def _toggle_adblock(self, on: bool):
        self.adblock.set_enabled(on)
        # init sırası erken tetiklenirse bileşenler henüz hazır olmayabilir
        if getattr(self, "_stack", None) is None or not hasattr(self, "_tabwidget"):
            return
        self._refresh_status()
        t = self._current_tab()
        if t and on:
            t.reload_cosmetic()

    # ── VPN tek tık + ⋮ ana menü ─────────────────────────────────────────────
    def _toggle_vpn_quick(self):
        """TEK TIK: VPN'i aç/kapa (Chrome eklentisi gibi)."""
        if self._vpn.is_active:
            self._vpn.disconnect()
            self._log_line("SİSTEM: VPN kapatıldı — doğrudan bağlantı.")
        else:
            ok, msg = self._vpn.toggle()
            self._log_line("SİSTEM: " + msg)
        self._sync_vpn_button()
        self._refresh_status()
        if hasattr(self, "_customize"):
            self._customize.refresh_vpn()

    def _sync_vpn_button(self):
        on = self._vpn.is_active
        self._vpn_btn.blockSignals(True)
        self._vpn_btn.setChecked(on)
        self._vpn_btn.blockSignals(False)
        self._vpn_btn.setToolTip(
            f"VPN AÇIK — {self._vpn.upstream_label()}\n(Tek tıkla kapat)"
            if on else "VPN KAPALI — doğrudan bağlantı\n(Tek tıkla aç)")

    def _log_line(self, text: str):
        """Ana pencereye log satırı gönder (varsa)."""
        try:
            win = self.window()
            if hasattr(win, "_log"):
                win._log.append_log(text)
        except Exception:
            pass

    def _open_main_menu(self):
        """Chrome'un ⋮ menüsü gibi hızlı eylem menüsü."""
        m = QMenu(self)
        m.setStyleSheet(f"""
            QMenu {{
                background: {C.PANEL}; color: {C.TEXT};
                border: 1px solid {C.BORDER_B}; border-radius: 10px;
                padding: 6px;
            }}
            QMenu::item {{ padding: 7px 22px 7px 14px; border-radius: 7px; }}
            QMenu::item:selected {{ background: {C.PRI_GHO}; color: {C.PRI}; }}
            QMenu::separator {{ height: 1px; background: {C.BORDER};
                                margin: 5px 8px; }}
            QMenu::title {{ color: {C.TEXT_DIM}; padding: 4px 12px; }}
        """)

        m.addSection("— GEZİNME —")
        a_new = m.addAction("Yeni sekme  (Ctrl+T)", self._menu_new_tab)
        a_new.setIcon(_icons.get_icon("plus"))
        a_rl = m.addAction("Sayfayı yenile  (F5)", self._reload)
        a_rl.setIcon(_icons.get_icon("reload"))
        m.addSeparator()
        m.addSection("— GİZLİLİK —")
        a_ab = m.addAction(self._ab_menu_text(), self._menu_toggle_adblock)
        a_ab.setIcon(_icons.get_icon("shield"))
        a_vp = m.addAction(self._vpn_menu_text(), self._toggle_vpn_quick)
        a_vp.setIcon(_icons.get_icon("satellite"))
        m.addSeparator()
        m.addSection("— GÖRÜNÜM —")
        a_th = m.addAction("Tema galerisi…", self._show_theme_gallery)
        a_th.setIcon(_icons.get_icon("star"))
        a_dk = m.addAction("Karanlık mod", self._menu_toggle_dark)
        a_dk.setIcon(_icons.get_icon("moon"))
        zm = m.addMenu(_icons.get_icon("zoom"), "Yakınlaştırma")
        zm.setStyleSheet(m.styleSheet())
        for pct in (75, 100, 125, 150, 200):
            zm.addAction(f"%{pct}", lambda p=pct: self.set_zoom_pct(pct))
        m.addSeparator()
        m.addSection("— KİŞİSELLEŞTİR —")
        a_cfg = m.addAction("Ayarlar (adblock / VPN / arama / görünüm)",
                            self._show_customize)
        a_cfg.setIcon(_icons.get_icon("gear"))
        m.addSeparator()
        a_ex = m.addAction("Tarayıcıyı kapat (Mehmet'e dön)",
                           self.exit_requested.emit)
        a_ex.setIcon(_icons.get_pixmap("close", 32, C.RED))

        # butonun hemen altında aç
        btn = self._menu_btn
        from PyQt6.QtCore import QPoint
        gp = btn.mapToGlobal(QPoint(0, btn.height() + 4))
        # sağ kenara taşmasın
        w = m.sizeHint().width()
        gp.setX(max(8, min(gp.x(), self.width() - w - 12)))
        m.exec(gp)

    # ── menü eylemleri ───────────────────────────────────────────────────────
    def _ab_menu_text(self) -> str:
        s = self.adblock.stats
        return (f"Reklam engelleyici: AÇIK ({s.blocked})"
                if self.adblock.enabled else "Reklam engelleyici: KAPALI")

    def _vpn_menu_text(self) -> str:
        return (f"VPN: AÇIK — {self._vpn.upstream_label()}"
                if self._vpn.is_active else "VPN: KAPALI")

    def _menu_toggle_adblock(self):
        self._ab_btn.setChecked(not self.adblock.enabled)

    def _menu_toggle_dark(self):
        self.set_dark_mode(not self.store.get_view().get("dark", False))

    def _menu_new_tab(self):
        self.add_tab()
        self._url_edit.setFocus()

    # ── TEMA galerisi (Chrome web mağazası hissi) ──────────────────────────
    def _show_theme_gallery(self):
        from PyQt6.QtCore import QPoint
        gal = QMenu(self)
        gal.setStyleSheet(f"""
            QMenu {{
                background: {C.PANEL}; color: {C.TEXT};
                border: 1px solid {C.BORDER_B}; border-radius: 10px;
                padding: 8px;
            }}
            QMenu::item {{ padding: 8px 26px 8px 12px; border-radius: 8px; }}
            QMenu::item:selected {{ background: {C.PRI_GHO}; }}
            QMenu::title {{ color: {C.TEXT_DIM}; padding: 2px 12px 6px; }}
        """)
        gal.addSection("— NAVİGATÖR TEMALARI —")
        cur = _load_nav_ui().get("theme", "JARVIS")
        for name, hx, desc in NAV_THEMES:
            mark = "● " if name == cur else "  "
            act = gal.addAction(f"{mark}{name}   — {desc}")
            act.setData(hx)
            act.triggered.connect(
                lambda _c=False, a=act, n=name: self._apply_theme(a, n))
        gp = self._menu_btn.mapToGlobal(QPoint(0, self._menu_btn.height() + 4))
        gal.exec(gp)

    def _apply_theme(self, action, name: str):
        hx = action.data()
        if hx and _apply_accent(hx):
            ui = _load_nav_ui()
            ui["theme"] = name
            _save_nav_ui(ui)
            self._log_line(f"SİSTEM: NAVİGATÖR teması {name} olarak uygulandı.")

    def _show_customize(self):
        cw = self.width() if self.parent() is None else self.parentWidget().width()
        self._customize.resize(min(560, cw - 40), min(520, self.height() - 30))
        self._customize.move((self.width() - self._customize.width()) // 2, 8)
        self._customize.raise_()
        self._customize.show()

    def _on_settings_changed(self):
        """Kişiselleştirme değişti: interceptor + sekmeleri tazele."""
        if WEBENGINE_OK and self._stack is not None:
            self._interceptor._engine = self.adblock
            for i in range(self._tabwidget.count()):
                t = self._tabwidget.widget(i)
                if isinstance(t, _BrowserTab):
                    t.reload_cosmetic()
        self._apply_view_to_pages()
        self._refresh_status()

    def _refresh_status(self):
        if not hasattr(self, "_ab_count_lbl"):
            return   # init tamamlanmadan tetiklendiyse atla
        s = self.adblock.stats
        ab_on = self.adblock.enabled
        self._ab_ico.setPixmap(_icons.get_pixmap(
            "shield", 32, C.GREEN if ab_on else C.MUTED_C))
        self._ab_count_lbl.setText(
            f"{s.blocked} ENGELLENDİ" if ab_on else "KAPALI")
        self._ab_count_lbl.setStyleSheet(
            f"color: {C.GREEN if ab_on else C.MUTED_C}; background: transparent;")
        vp_on = self.vpn.is_active
        self._vpn_ico.setPixmap(_icons.get_pixmap(
            "satellite", 32, C.GREEN if vp_on else C.ACC2))
        self._vpn_lbl.setText(self.vpn.status_text())

    def _update_ab_count(self):
        s = self.adblock.stats
        ab_on = self.adblock.enabled
        self._ab_ico.setPixmap(_icons.get_pixmap(
            "shield", 32, C.GREEN if ab_on else C.MUTED_C))
        self._ab_count_lbl.setText(f"{s.blocked} ENGELLENDİ"
                                   if ab_on else "KAPALI")

    # ── Mehmet / dış API ───────────────────────────────────────────────────
    def new_tab(self, url: str = _HOME_URL) -> None:
        """Yeni sekme aç (varsayılan: Google)."""
        if WEBENGINE_OK and self._stack is not None:
            self.add_tab(url or _HOME_URL)

    def close_current_tab(self) -> None:
        i = self._tabwidget.currentIndex()
        if i >= 0:
            self._close_tab(i)

    # ── sesli sekme yönetimi (Mehmet) ────────────────────────────────────────
    def switch_tab(self, index: int) -> bool:
        """N. sekmeye geç (1-tabanlı). Başarı: True."""
        i = int(index) - 1
        if 0 <= i < self._tabwidget.count():
            self._tabwidget.setCurrentIndex(i)
            return True
        return False

    def close_all_tabs(self) -> int:
        """Aktif sekme dışındaki tüm sekmeleri kapatır; kapatılan sayısını
        döndürür. (Tamamen boşalmamak için aktif sekme korunur.)"""
        cur = self._tabwidget.currentIndex()
        closed = 0
        for i in range(self._tabwidget.count() - 1, -1, -1):
            if i == cur:
                continue
            w = self._tabwidget.widget(i)
            if isinstance(w, _BrowserTab):
                w.stop()
            self._tabwidget.removeTab(i)
            w.deleteLater()
            closed += 1
            if cur > i:
                cur -= 1
        if closed:
            self._refresh_status()
        return closed

    # ── çok adımlı görev zincirleri (Mehmet: tek komut → çok adım) ───────
    _GOOGLE_RESULT_SELS = (
        "#search a h3", "#rso a h3", "a h3", "div.g a",
        "#search a[href^='http']", "#search a[href^='/url?']")
    _YOUTUBE_RESULT_SELS = (
        "ytd-video-renderer a#video-title-link",
        "ytd-video-renderer a#thumbnail",
        "a#video-title-link",
        "ytd-rich-item-renderer a#thumbnail",
        "ytd-item-section-renderer a#thumbnail")

    def _wait_selector(self, tab, selector: str, timeout: float = 5.0,
                       pump=None) -> bool:
        """Seçicinin DOM'da belirmesini bekler (zincir içi adım)."""
        t0 = _time.time()
        res = {"n": None}
        js = ("(function(){try{return document.querySelectorAll(" +
              json.dumps(selector) + ").length;}catch(e){return 0;}})();")
        while _time.time() - t0 < timeout:
            res["n"] = None
            tab.view.page().runJavaScript(
                js, lambda n: res.__setitem__("n", n))
            t1 = _time.time()
            while _time.time() - t1 < 1.0 and res["n"] is None:
                if pump:
                    pump()
                _time.sleep(0.02)
            if res["n"]:
                return True
            _time.sleep(0.25)
        return False

    def _wait_title_ready(self, tab, old_title: str, timeout: float = 12.0,
                          pump=None) -> bool:
        """Sekmenin yeni yükleme bitirmesini bekler (bloklayan döngü —
        yalnızca zincir API'sinden; pump verildiyse olay döngüsü pompalanır)."""
        t0 = _time.time()
        while _time.time() - t0 < timeout:
            if pump is not None:
                pump()
            if tab.view.title() and tab.view.title() != old_title:
                return True
            _time.sleep(0.05)
        return False

    def run_chain(self, spec: dict, pump=None) -> dict:
        """Çok adımlı web görevi çalıştırır (ANA THREAD'den).

        spec:
          {"task": "youtube_play", "query": "..."}
            → YouTube'da ara, ilk videoya tıkla, oynat.
          {"task": "google_first", "query": "..."}
            → Google'da ara, ilk sonucu aç.
        pump: ana olay döngüsünü pompalayan fonksiyon (testte gerekir).
        Dönüş: {"ok", "steps", "title", "url", "error?"}
        """
        task = (spec.get("task") or "").lower()
        query = (spec.get("query") or "").strip()
        steps = []
        if task == "wikipedia_summary":
            return self._chain_wikipedia(query, steps, pump)
        if task == "news_headlines":
            return self._chain_news(steps, pump)
        if task == "youtube_play":
            self.navigate("https://www.youtube.com/results?search_query="
                          + quote_plus(query))
            steps.append("youtube arama sayfası açıldı")
            tab = self._current_tab()
            if tab is None:
                return {"ok": False, "steps": steps, "title": "", "url": "",
                        "error": "sekme yok"}
            old = tab.view.title()
            self._wait_title_ready(tab, old, 10.0, pump)
            _time.sleep(1.2)          # sonuç grid'i otursun
            clicked = {"v": None}
            for sel in self._YOUTUBE_RESULT_SELS:
                if not self._wait_selector(tab, sel, 3.0, pump):
                    continue
                tab.js_click_deep(sel, lambda r: clicked.__setitem__("v", r))
                t0 = _time.time()
                while _time.time() - t0 < 2 and clicked["v"] is None:
                    if pump: pump()
                    _time.sleep(0.05)
                if clicked["v"]:
                    break
            if not clicked["v"]:
                # son çare: metin bazlı akıllı tık
                tab.js_find_and_click(query[:40],
                    lambda r: clicked.__setitem__("v", r))
                t0 = _time.time()
                while _time.time() - t0 < 3 and clicked["v"] is None:
                    if pump: pump()
                    _time.sleep(0.05)
            if not clicked["v"]:
                return {"ok": False, "steps": steps, "title": "", "url": "",
                        "error": "ilk video bulunamadı"}
            steps.append("ilk videoya tıklandı")
            old = tab.view.title()
            self._wait_title_ready(tab, old, 10.0, pump)
            _time.sleep(1.0)          # player başlaması
            steps.append("video oynatılıyor")
            return {"ok": True, "steps": steps,
                    "title": tab.view.title() or "",
                    "url": tab.view.url().toString()}

        elif task == "google_first":
            self.navigate(_search_url(query))
            steps.append("google araması yapıldı")
            tab = self._current_tab()
            if tab is None:
                return {"ok": False, "steps": steps, "title": "", "url": "",
                        "error": "sekme yok"}
            old = tab.view.title()
            self._wait_title_ready(tab, old, 10.0, pump)
            _time.sleep(2.0)
            clicked = {"v": None}
            for sel in self._GOOGLE_RESULT_SELS:
                if not self._wait_selector(tab, sel, 4.0, pump):
                    continue
                tab.js_click_deep(sel, lambda r: clicked.__setitem__("v", r))
                t0 = _time.time()
                while _time.time() - t0 < 2 and clicked["v"] is None:
                    if pump: pump()
                    _time.sleep(0.05)
                if clicked["v"]:
                    break
            if not clicked["v"]:
                return {"ok": False, "steps": steps, "title": "", "url": "",
                        "error": "ilk sonuç bulunamadı"}
            steps.append("ilk sonuca tıklandı")
            old = tab.view.title()
            self._wait_title_ready(tab, old, 10.0, pump)
            return {"ok": True, "steps": steps,
                    "title": tab.view.title() or "",
                    "url": tab.view.url().toString()}

        return {"ok": False, "steps": steps, "title": "", "url": "",
                "error": f"bilinmeyen görev: {task}"}

    # ── yeni zincirler: wikipedia + haberler ───────────────────────────────
    def _js_extract(self, tab, script: str, out: dict, pump=None,
                    timeout: float = 6.0) -> None:
        """Sayfadan JS ile veri çıkarır (senkron, event-tabanlı)."""
        done = {"v": None}
        def cb(res):
            done["v"] = res
        try:
            tab.view.page().runJavaScript(script, 0, cb)
        except Exception:
            return
        t0 = _time.time()
        while _time.time() - t0 < timeout and done["v"] is None:
            if pump: pump()
            _time.sleep(0.05)
        if done["v"] is not None:
            out["v"] = done["v"]

    def _chain_wikipedia(self, query: str, steps: list, pump=None) -> dict:
        """Wikipedia'da ara → madde sayfasını aç → özet paragrafları çek."""
        lang = "tr"
        self.navigate(f"https://{lang}.wikipedia.org/wiki/Special:Search?"
                      f"search={quote_plus(query)}&fulltext=1")
        steps.append("wikipedia arama yapıldı")
        tab = self._current_tab()
        if tab is None:
            return {"ok": False, "steps": steps, "title": "", "url": "",
                    "error": "sekme yok"}
        old = tab.view.title()
        self._wait_title_ready(tab, old, 10.0, pump)
        _time.sleep(1.5)
        # arama sonuç sayfasındaysak ilk maddeye tıkla
        url_now = tab.view.url().toString()
        if "/wiki/Special:Search" in url_now:
            clicked = {"v": None}
            for sel in (".mw-search-result-heading a",
                        ".results-info a", "li.mw-search-result a"):
                if not self._wait_selector(tab, sel, 3.0, pump):
                    continue
                tab.js_click_deep(sel, lambda r: clicked.__setitem__("v", r))
                t0 = _time.time()
                while _time.time() - t0 < 2 and clicked["v"] is None:
                    if pump: pump()
                    _time.sleep(0.05)
                if clicked["v"]:
                    break
            if not clicked["v"]:
                # doğrudan madde adı deneyin
                self.navigate(f"https://{lang}.wikipedia.org/wiki/"
                              + quote_plus(query))
            else:
                steps.append("ilk maddeye tıklandı")
                old = tab.view.title()
                self._wait_title_ready(tab, old, 10.0, pump)
        _time.sleep(1.0)
        # özet paragraflarını çek
        out = {}
        js = ("(function(){ var ps = document.querySelectorAll("
              "'#mw-content-text .mw-parser-output > p, "
              "#mw-content-text > p'); var t=''; "
              "for (var i=0;i<ps.length && t.length<1200;i++){ "
              "var x=(ps[i].innerText||'').trim(); "
              "if(x.length>40) t+=(t?' ':'')+x; } "
              "return t; })()")
        self._js_extract(tab, js, out, pump)
        summary = (out.get("v") or "")[:1200]
        if not summary:
            return {"ok": False, "steps": steps, "title": tab.view.title() or "",
                    "url": tab.view.url().toString(),
                    "error": "madde özeti okunamadı"}
        steps.append("madde özeti okundu")
        return {"ok": True, "steps": steps,
                "title": tab.view.title() or "",
                "url": tab.view.url().toString(),
                "summary": summary}

    def _chain_news(self, steps: list, pump=None) -> dict:
        """Google News (Türkçe) başlıklarını toplar."""
        self.navigate("https://news.google.com/home?hl=tr&gl=TR"
                      "&ceid=TR:tr")
        steps.append("haber sayfası açıldı")
        tab = self._current_tab()
        if tab is None:
            return {"ok": False, "steps": steps, "title": "", "url": "",
                    "error": "sekme yok"}
        old = tab.view.title()
        self._wait_title_ready(tab, old, 12.0, pump)
        _time.sleep(2.5)   # dinamik akış otursun
        out = {}
        js = ("(function(){ var els = document.querySelectorAll("
              "'article h3, article h4, a.JtKRv, c-wiz h3'); "
              "var seen={}, out=[]; "
              "for (var i=0;i<els.length && out.length<10;i++){ "
              "var x=(els[i].innerText||'').trim().replace(/\\s+/g,' '); "
              "if(x.length>15 && !seen[x]){ seen[x]=1; out.push(x); }} "
              "return out; })()")
        self._js_extract(tab, js, out, pump, timeout=8.0)
        heads = out.get("v") or []
        if not heads:
            # yedek: sayfa metninden ilk satırları al
            out2 = {}
            js2 = "document.body ? document.body.innerText.slice(0, 3000) : ''"
            self._js_extract(tab, js2, out2, pump)
            txt = (out2.get("v") or "")
            heads = [ln.strip() for ln in txt.split("\n")
                     if 25 < len(ln.strip()) < 120][:8]
        if not heads:
            return {"ok": False, "steps": steps, "title": tab.view.title() or "",
                    "url": tab.view.url().toString(),
                    "error": "başlıklar okunamadı"}
        steps.append(f"{len(heads)} başlık toplandı")
        return {"ok": True, "steps": steps,
                "title": tab.view.title() or "",
                "url": tab.view.url().toString(),
                "headlines": heads}

    # ── ekran görüntüsü ───────────────────────────────────────────────────
    def screenshot_active(self) -> dict:
        """Aktif sekmenin görüntüsünü PNG olarak kaydeder (ANA THREAD).

        Not: QWidget.grab() WebEngine içeriğinde boş döner (chromium kendi
        kompozitöründe çizer) — bu yüzden ekran düzeyi yakalama kullanılır:
        QScreen.grabWindow(view.winId()). Tarayıcı katmanı görünürken çalışır.
        Dönüş: {"ok", "path", "error?"}
        """
        res = self.capture_image()
        if not res.get("ok"):
            return {"ok": False, "error": res.get("error", "?")}
        return {"ok": True, "path": res.get("path", ""),
                "title": res.get("title", ""), "url": res.get("url", "")}

    def capture_image(self) -> dict:
        """Aktif sekmenin PNG görüntüsünü yakalar (ANA THREAD).
        Görsel analiz için bayt da döner: {"ok", "path", "png", "mime",
        "title", "url", "error?"}
        """
        tab = self._current_tab()
        if tab is None:
            return {"ok": False, "error": "açık sekme yok"}
        try:
            _SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
            stamp = _time.strftime("%Y%m%d_%H%M%S")
            safe = "".join(c for c in (tab.view.title() or "sayfa")
                           if c.isalnum() or c in " -_").strip()[:40] or "sayfa"
            path = _SCREENSHOT_DIR / f"nav_{safe}_{stamp}.png"

            pix = tab.view.grab()          # hızlı yol: widget raster (bazı ortamlarda çalışır)
            if pix.isNull() or pix.width() < 2:
                # sağlam yol: ekran yakalama (view görünür olmalı)
                if not tab.view.isVisible():
                    return {"ok": False,
                            "error": "sayfa görünür değil — NAVİGATÖR'ü açın"}
                screen = tab.view.screen()
                pix = screen.grabWindow(int(tab.view.winId()))
            if pix.isNull() or pix.width() < 2:
                return {"ok": False, "error": "görüntü alınamadı"}
            if not pix.save(str(path)):
                return {"ok": False, "error": "PNG kaydedilemedi"}
            png = bytes(path.read_bytes()) if path.exists() else b""
            return {"ok": True, "path": str(path), "png": png,
                    "mime": "image/png",
                    "title": tab.view.title() or "",
                    "url": tab.view.url().toString()}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ── çeviri katmanı ────────────────────────────────────────────────────
    def translate_active(self, target: str = "tr", pump=None) -> dict:
        """Aktif sayfanın metnini çevirip overlay'de gösterir.
        Kaynak: Google translate gtx endpoint (anahtar gerektirmez).
        Dönüş: {"ok", "text", "from", "error?"} (ANA THREAD'den çağırın;
        pump ile olay döngüsü pompalanır)."""
        meta = {"v": None}
        self.read_page(lambda m: meta.__setitem__("v", m))
        t0 = _time.time()
        while _time.time() - t0 < 8 and meta["v"] is None:
            if pump: pump()
            _time.sleep(0.05)
        m = meta["v"] or {}
        if not m.get("ok"):
            return {"ok": False, "error": m.get("error") or "sayfa okunamadı"}
        text = (m.get("text") or "")[:3000]
        if not text.strip():
            return {"ok": False, "error": "sayfada çevrilecek metin yok"}
        try:
            from urllib.parse import quote
            import urllib.request
            url = ("https://translate.googleapis.com/translate_a/single"
                   "?client=gtx&sl=auto&tl=" + quote(target) +
                   "&dt=t&q=" + quote(text))
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
            with urllib.request.urlopen(req, timeout=12) as r:
                data = json.loads(r.read().decode("utf-8"))
            translated = "".join(seg[0] for seg in data[0] if seg and seg[0])
        except Exception as e:
            return {"ok": False, "error": f"çeviri servisi: {e}"}
        # sonucu overlay'de göster
        self._show_translate_overlay(m.get("title", ""), m.get("url", ""),
                                     translated, target)
        return {"ok": True, "text": translated,
                "from": m.get("url", "")}

    def _show_translate_overlay(self, title: str, url: str,
                                text: str, target: str) -> None:
        """Çeviri sonucunu tarayıcı üstünde şık bir overlay'de göster."""
        ov = QWidget(self)
        ov.setObjectName("NavTranslate")
        ov.setWindowFlags(Qt.WindowType.Widget | Qt.WindowType.FramelessWindowHint)
        ov.setStyleSheet(f"""
            QWidget#NavTranslate {{
                background: {C.PANEL};
                border: 1px solid {C.BORDER_B}; border-radius: 12px;
            }}
        """)
        lay = QVBoxLayout(ov)
        lay.setContentsMargins(14, 10, 14, 12)
        lay.setSpacing(6)
        head_row = QHBoxLayout()
        head_ico = QLabel()
        head_ico.setPixmap(_icons.get_pixmap("translate", 32, C.PRI))
        head_ico.setFixedSize(20, 20)
        head_ico.setScaledContents(True)
        head_ico.setStyleSheet("background: transparent;")
        head_row.addWidget(head_ico)
        head = QLabel(f"ÇEVİRİ → {target.upper()}   |   {title[:52]}")
        head.setFont(_font(FONT_UI, 10, QFont.Weight.DemiBold))
        head.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        head.setWordWrap(True)
        head_row.addWidget(head, stretch=1)
        lay.addLayout(head_row)
        lay.addWidget(head)
        body = QTextEdit()
        body.setReadOnly(True)
        body.setFont(_font(FONT_UI, 10))
        body.setStyleSheet(f"""
            QTextEdit {{ background: {C.DARK}; color: {C.TEXT};
                         border: 1px solid {C.BORDER}; border-radius: 8px;
                         padding: 6px; }}
        """)
        body.setPlainText(text)
        lay.addWidget(body, stretch=1)
        row = QHBoxLayout()
        note = QLabel(url[:80])
        note.setFont(_font(FONT_MONO, 8))
        note.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        row.addWidget(note)
        row.addStretch()
        close = QPushButton(" KAPAT")
        from PyQt6.QtGui import QIcon as _QIconT
        close.setIcon(_QIconT(_icons.get_pixmap("close", 32, C.RED)))
        close.setFont(_font(FONT_UI, 9, QFont.Weight.DemiBold))
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.RED};
                           border: 1px solid {C.RED}; border-radius: 7px;
                           padding: 4px 10px; }}
            QPushButton:hover {{ background: {C.RED}; color: {C.WHITE}; }}
        """)
        close.clicked.connect(ov.deleteLater)
        row.addWidget(close)
        lay.addLayout(row)

        w = min(560, max(360, self.width() - 60))
        h = min(460, max(280, self.height() - 60))
        ov.resize(w, h)
        ov.move((self.width() - w) // 2, 40)
        ov.show()
        ov.raise_()
        QTimer.singleShot(30000, ov.deleteLater)   # 30 sn sonra kendiliğinden kapanır

    def list_tabs(self) -> str:
        """Açık sekmeleri numaralı liste olarak döndürür (Mehmet raporu)."""
        lines = []
        cur = self._tabwidget.currentIndex()
        for i in range(self._tabwidget.count()):
            w = self._tabwidget.widget(i)
            title = (w.view.title() if isinstance(w, _BrowserTab) else "") or "Sekme"
            url = (w.view.url().toString() if isinstance(w, _BrowserTab) else "")
            mark = "►" if i == cur else " "
            lines.append(f"{mark} {i + 1}. {title[:44]} — {url[:60]}")
        return "\n".join(lines) or "Açık sekme yok."

    def search(self, query: str) -> None:
        """Google araması (aktif sekme)."""
        if query.strip():
            self.navigate(_search_url(query.strip()))

    def current_url(self) -> str:
        t = self._current_tab()
        return t.view.url().toString() if t else ""

    def current_title(self) -> str:
        t = self._current_tab()
        return (t.view.title() or "") if t else ""

    def read_page(self, callback) -> None:
        """Aktif sekmenin metnini asenkron okur → callback(meta_dict).

        meta_dict: {"ok", "title", "url", "text"} — sekme yok ya da
        sayfa hazır değilse ok=False. (Mehmet'in sayfa okuma yeteneği.)
        """
        t = self._current_tab()
        if t is None:
            callback({"ok": False, "title": "", "url": "", "text": "",
                      "error": "açık sekme yok"})
            return
        title = t.view.title() or ""
        url = t.view.url().toString()

        def _got(text):
            callback({
                "ok": isinstance(text, str) and bool(text.strip()),
                "title": title,
                "url": url,
                "text": (text or "") if isinstance(text, str) else "",
                "error": "" if isinstance(text, str) and text.strip()
                         else "sayfa metni okunamadı (boş veya hazır değil)",
            })

        t.read_text(_got)

    # ── sık kullanılanlar ───────────────────────────────────────────────────
    def _toggle_bookmark(self, checked: bool):
        t = self._current_tab()
        if t is None:
            return
        url = t.view.url().toString()
        if not url.startswith(("http://", "https://")):
            self._bm_btn.setChecked(False)
            return
        is_bm = self.store.toggle_bookmark(url, t.view.title() or url)
        self._bm_btn.blockSignals(True)
        self._bm_btn.setChecked(is_bm)
        self._bm_btn.blockSignals(False)
        self._update_suggestions()

    def _update_bm_btn(self):
        if not hasattr(self, "_bm_btn"):
            return
        t = self._current_tab()
        url = t.view.url().toString() if t else ""
        self._bm_btn.blockSignals(True)
        self._bm_btn.setChecked(
            url.startswith(("http://", "https://"))
            and self.store.is_bookmarked(url))
        self._bm_btn.blockSignals(False)

    # ── akıllı tamamlama ────────────────────────────────────────────────────
    def _init_address_suggestions(self):
        self._sugg_map: dict[str, str] = {}
        self._completer = QCompleter(self)
        self._completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._completer.setCompletionMode(
            QCompleter.CompletionMode.PopupCompletion)
        self._completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self._sugg_model = QStringListModel(self)
        self._completer.setModel(self._sugg_model)
        self._completer.activated.connect(self._on_suggestion_activated)
        self._completer.popup().setStyleSheet(
            f"QListView {{ background: {C.DARK}; color: {C.TEXT};"
            f" border: 1px solid {C.BORDER_B}; outline: none; }}"
            f"QListView::item {{ padding: 4px 8px; }}"
            f"QListView::item:selected {{ background: {C.PRI_GHO};"
            f" color: {C.PRI}; }}"
        )
        self._url_edit.setCompleter(self._completer)
        self._url_edit.textEdited.connect(self._update_suggestions)
        self._update_suggestions()

    def _update_suggestions(self):
        if not hasattr(self, "_completer"):
            return
        sugg = self.store.suggestions(self._url_edit.text())
        self._sugg_map = {}
        rows: list[str] = []
        for s in sugg:
            star = "★ " if s["bm"] else ""
            disp = f"{star}{s['title'][:36]}  ·  {s['url'][:52]}"
            self._sugg_map[disp] = s["url"]
            rows.append(disp)
        self._sugg_model.setStringList(rows)

    def _on_suggestion_activated(self, text: str):
        url = self._sugg_map.get(text)
        if url:
            self.navigate(url)

    # ── görüntü ayarları ────────────────────────────────────────────────────
    def _apply_view_to_pages(self, reload_active: bool = False):
        """Kayıtlı görünüm ayarlarını (karanlık mod, font, zoom) tüm
        sekmelere uygular."""
        if not WEBENGINE_OK or self._stack is None:
            return
        v = self.store.get_view()
        zoom = max(0.30, min(3.0, float(v.get("zoom_pct", 100)) / 100.0))
        base_font = 14 + int(v.get("font_pt", 0))
        for i in range(self._tabwidget.count()):
            t = self._tabwidget.widget(i)
            if not isinstance(t, _BrowserTab):
                continue
            st = t.view.settings()
            st.setAttribute(QWebEngineSettings.WebAttribute.ForceDarkMode,
                            bool(v.get("dark")))
            st.setFontSize(QWebEngineSettings.FontSize.DefaultFontSize,
                           base_font)
            t.view.setZoomFactor(zoom)
        if reload_active:
            t = self._current_tab()
            if t:
                t.view.reload()

    def set_dark_mode(self, on: bool) -> None:
        self.store.set_view(dark=bool(on))
        self._apply_view_to_pages(reload_active=True)

    def set_font_delta(self, delta: int) -> None:
        self.store.set_view(font_pt=int(delta))
        self._apply_view_to_pages()

    def set_zoom_pct(self, pct: int) -> None:
        self.store.set_view(zoom_pct=int(pct))
        self._apply_view_to_pages()

    # ── resize: overlay'i ortala ─────────────────────────────────────────────
    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        if self._customize.isVisible():
            self._customize.move(
                (self.width() - self._customize.width()) // 2, 8)

    def shutdown(self):
        """Uygulama kapanırken temizlik: sekmeleri durdur, bağlantıları
        kopar ve widget'ları kontrollü sil."""
        if not WEBENGINE_OK or getattr(self, "_stack", None) is None:
            return
        for i in range(self._tabwidget.count()):
            t = self._tabwidget.widget(i)
            if isinstance(t, _BrowserTab):
                t.stop()
                t.deleteLater()
        self._tabwidget.clear()
        # VPN aktarma katmanı daemon thread'dir; uygulama çıkışıyla ölür.
        try:
            GLOBAL_RELAY.stop()
        except Exception:
            pass
