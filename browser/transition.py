# -*- coding: utf-8 -*-
"""
NAVİGATÖR GEÇİŞ PERDESİ — JARVIS tarzı sinematik AI ↔ tarayıcı geçişi.

Üç aşamalı koreografi:
    1. PERDE   : merkezden açılan iki altın yarım-perde, MEHMET alanını kapatır
    2. DALGA   : perdenin ortasında parlak yatay tarama çizgisi
                 (altın→beyaz konik parıltı, JARVIS imza hareketi)
    3. ÇEKİLME : perde geçiş yönünde kayarak toplanır ve altındaki katman
                 (tarayıcı veya HUD) görünür hale gelir

Kullanım:
    curtain = BrowserCurtain(parent)
    curtain.open_curtain(target_rect)   # açılış: kapan → dalga → çekil
    curtain.close_curtain(target_rect)  # kapanış: kapan → dalga → sönerek sil

Not: perde parent'ın child'ıdır; open() ile raise edilir, animasyon bitince
hide() edilir. Ana pencerenin resizeEvent'i varsa curtain.track_parent()
çağrılmalı ki perde pencereyle birlikte kaymasın.
"""
from __future__ import annotations

import math

from PyQt6.QtCore import (
    QEasingCurve, QParallelAnimationGroup, QPropertyAnimation, QRect,
    QSequentialAnimationGroup, Qt, QTimer, pyqtProperty,
)
from PyQt6.QtGui import (
    QConicalGradient, QFont, QPainter, QColor, QLinearGradient, QPen,
    QRadialGradient,
)
from PyQt6.QtWidgets import QWidget

try:
    from ui import C as _C          # canlı tema (accent değişimini takip eder)
except Exception:                   # pragma: no cover - döngüsel import koruması
    _C = None


def _col(name: str, fallback: str) -> QColor:
    """Temadan renk al; tema modülü yoksa fallback kullan."""
    if _C is not None:
        v = getattr(_C, name, None)
        if isinstance(v, str):
            return QColor(v)
    return QColor(fallback)


# ── zamanlama (ms) — uygulamanın sinematik diline göre kalibre edildi ────────
T_CLOSE = 380      # perde kapanışı (kolonlar merkeze gelir)
T_WAVE  = 460      # tarama dalgası süresi
T_OPEN  = 460      # perde açılışı / çekilmesi
PAUSE   = 140      # dalga sonrası mini bekleme (nefes)


class _HalfCurtain(QWidget):
    """Tek yarım perde: dikey altın çizgiler + kenar parlaması + başlık."""

    def __init__(self, parent: QWidget, from_left: bool):
        super().__init__(parent)
        self._from_left = bool(from_left)
        self._progress = 0.0        # 0 = tam açık, 1 = tam kapalı
        self.label_text = ""
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    # QProperty animate edilen alan
    @pyqtProperty(float)
    def progress(self) -> float:
        return self._progress

    @progress.setter
    def progress(self, v: float) -> None:
        self._progress = max(0.0, min(1.0, float(v)))
        self.update()

    def paintEvent(self, _event) -> None:      # noqa: N802 (Qt ismi)
        p = self._progress
        if p <= 0.0:
            return
        w, h = self.width(), self.height()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        accent = _col("PRI", "#f2b632")
        dark   = _col("BG",  "#05070a")

        # kapanan kolon genişliği
        cover = w * p
        x0 = 0 if self._from_left else w - cover

        # gövde: koyu zemin + yatay altın degrade
        body = QLinearGradient(x0, 0, x0 + cover, 0)
        edge_a, edge_b = (0.0, 1.0) if self._from_left else (1.0, 0.0)
        body.setColorAt(edge_a, QColor(dark.red(), dark.green(), dark.blue(), 245))
        body.setColorAt(edge_b, QColor(12, 14, 18, 235))
        painter.fillRect(QRect(int(x0), 0, max(1, int(cover)), h), body)

        # JARVIS dikey ince çizgileri
        pen = QPen(QColor(accent.red(), accent.green(), accent.blue(), 34))
        pen.setWidth(1)
        painter.setPen(pen)
        step = 26
        start = int(x0) - (int(x0) % step)
        for x in range(start, int(x0 + cover) + 1, step):
            painter.drawLine(x, 0, x, h)

        # iç kenarda parlak altın hat
        edge_x = int(x0 + cover) if self._from_left else int(x0)
        edge = QLinearGradient(edge_x - 10, 0, edge_x + 10, 0)
        glow = QColor(accent)
        glow.setAlpha(200)
        transparent = QColor(accent)
        transparent.setAlpha(0)
        edge.setColorAt(0.0, transparent)
        edge.setColorAt(0.5, glow)
        edge.setColorAt(1.0, transparent)
        painter.fillRect(QRect(edge_x - 10, 0, 20, h), edge)

        # perde ortasında büyük başlık (yalnızca perde yarıdan fazla kapalıyken)
        if self.label_text and p > 0.55:
            f = QFont()
            f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 10)
            f.setPixelSize(max(18, min(34, h // 22)))
            f.setBold(True)
            painter.setFont(f)
            alpha = int(210 * ((p - 0.55) / 0.45))
            lc = QColor(accent)
            lc.setAlpha(alpha)
            painter.setPen(lc)
            cx = int(x0 + cover / 2)
            painter.drawText(QRect(cx - 300, int(h * 0.5) - 30, 600, 60),
                             int(Qt.AlignmentFlag.AlignCenter), self.label_text)

        painter.end()


class _WaveBand(QWidget):
    """Tarama dalgası: soldan sağa koşan parlak yatay çizgi + ışıma."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self._t = 0.0                # 0..1 tarama konumu
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    @pyqtProperty(float)
    def sweep(self) -> float:
        return self._t

    @sweep.setter
    def sweep(self, v: float) -> None:
        self._t = max(0.0, min(1.0, float(v)))
        self.update()

    def paintEvent(self, _event) -> None:      # noqa: N802 (Qt ismi)
        p = self._t
        w, h = self.width(), self.height()
        if p <= 0.0 or p >= 1.0 or w <= 0 or h <= 0:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        accent = _col("PRI", "#f2b632")
        white  = QColor(255, 244, 214)
        y = h / 2.0
        x = w * p

        # geniş yumuşak ışıma şeridi
        band_h = h * 0.55 * math.sin(math.pi * p)
        glow = QLinearGradient(0, y - band_h / 2, 0, y + band_h / 2)
        ga = QColor(accent); ga.setAlpha(0)
        gb = QColor(accent); gb.setAlpha(52)
        glow.setColorAt(0.0, ga)
        glow.setColorAt(0.5, gb)
        glow.setColorAt(1.0, ga)
        painter.fillRect(QRect(0, int(y - band_h / 2), w, max(1, int(band_h))), glow)

        # merkez yatay tarama çizgisi
        line = QLinearGradient(0, 0, w, 0)
        c0 = QColor(accent); c0.setAlpha(30)
        c1 = QColor(white);  c1.setAlpha(235)
        c2 = QColor(accent); c2.setAlpha(30)
        line.setColorAt(0.0, c0)
        line.setColorAt(0.5, c1)
        line.setColorAt(1.0, c2)
        pen = QPen()
        pen.setWidthF(2.2)
        pen.setBrush(line)
        painter.setPen(pen)
        painter.drawLine(0, int(y), w, int(y))

        # çizginin önünde küçük konik parıltı noktası
        halo = QRadialGradient(x, y, 46)
        h1 = QColor(white); h1.setAlpha(190)
        h2 = QColor(accent); h2.setAlpha(0)
        halo.setColorAt(0.0, h1)
        halo.setColorAt(1.0, h2)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(halo)
        painter.drawEllipse(int(x - 46), int(y - 46), 92, 92)

        # konik dönen altın vurgu (JARVIS imzası)
        cone = QConicalGradient(x, y, -(p * 360.0))
        k0 = QColor(accent); k0.setAlpha(0)
        k1 = QColor(accent); k1.setAlpha(70)
        cone.setColorAt(0.0, k0)
        cone.setColorAt(0.25, k1)
        cone.setColorAt(0.5, k0)
        cone.setColorAt(0.75, k1)
        cone.setColorAt(1.0, k0)
        pen2 = QPen()
        pen2.setWidthF(1.4)
        pen2.setBrush(cone)
        painter.setPen(pen2)
        r = min(w, h) * 0.18
        painter.drawEllipse(int(x - r), int(y - r), int(2 * r), int(2 * r))

        painter.end()


class BrowserCurtain(QWidget):
    """AI ↔ NAVİGATÖR sinematik geçiş perdesi. Child overlay; hiçbir şeyi
    engellemez, animasyon bitince kendini gizler."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.hide()

        self._left = _HalfCurtain(self, from_left=True)
        self._right = _HalfCurtain(self, from_left=False)
        self._wave = _WaveBand(self)
        self._label = "NAVİGATÖR"

        self._anim: QPropertyAnimation | None = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

    # ── genel API ────────────────────────────────────────────────────────────
    def set_label(self, text: str) -> None:
        """Perde ortasında gösterilecek başlık (NAVİGATÖR / MEHMETNEO…)."""
        self._label = (text or "").upper()
        self._left.label_text = self._label
        self._right.label_text = self._label

    def open_curtain(self, target: QRect) -> None:
        """Tarayıcıya geçiş: kapan → dalga → sağa doğru çekil."""
        self._run(target, exit_to_right=True)

    def close_curtain(self, target: QRect) -> None:
        """Mehmet'e dönüş: kapan → dalga → sola doğru çekil."""
        self._run(target, exit_to_right=False)

    def track_parent(self) -> None:
        """Parent resize olduysa geometriyi tazele (perde sabit kalmasın)."""
        if self.isVisible():
            r = self._target_rect()
            if r.isValid():
                self._place(r)

    # ── iç koreografi ────────────────────────────────────────────────────────
    def _target_rect(self) -> QRect:
        par = self.parentWidget()
        return par.rect() if par else QRect()

    def _place(self, r: QRect) -> None:
        self.setGeometry(r)
        self._left.setGeometry(0, 0, r.width(), r.height())
        self._right.setGeometry(0, 0, r.width(), r.height())
        self._wave.setGeometry(0, 0, r.width(), r.height())

    def _run(self, target: QRect, *, exit_to_right: bool) -> None:
        r = QRect(target) if target.isValid() else self._target_rect()
        if r.width() <= 0 or r.height() <= 0:
            return
        self._place(r)
        self.raise_()
        self.show()

        # mevcut animasyonu iptal et
        if self._anim is not None:
            self._anim.stop()
            self._anim.deleteLater()
            self._anim = None
        self._timer.stop()

        # 1) perde kapanışı (iki yarım aynı anda)
        self._left.progress = 0.0
        self._right.progress = 0.0
        self._wave.sweep = 0.0

        close_l = QPropertyAnimation(self._left, b"progress", self)
        close_l.setDuration(T_CLOSE)
        close_l.setStartValue(0.0)
        close_l.setEndValue(1.0)
        close_l.setEasingCurve(QEasingCurve.Type.InOutQuart)

        close_r = QPropertyAnimation(self._right, b"progress", self)
        close_r.setDuration(T_CLOSE)
        close_r.setStartValue(0.0)
        close_r.setEndValue(1.0)
        close_r.setEasingCurve(QEasingCurve.Type.InOutQuart)

        close_grp = QParallelAnimationGroup(self)
        close_grp.addAnimation(close_l)
        close_grp.addAnimation(close_r)

        # 2) tarama dalgası
        wave = QPropertyAnimation(self._wave, b"sweep", self)
        wave.setDuration(T_WAVE)
        wave.setStartValue(0.0)
        wave.setEndValue(1.0)
        wave.setEasingCurve(QEasingCurve.Type.InOutCubic)

        # 3) çekilme: yarımlar progress=1→0'a düşer; çıkış yönündeki yarım
        #    hafif hızlı toplanır → perde o yöne doğru "süpürülür"
        lead_ms, trail_ms = (T_OPEN, int(T_OPEN * 1.25)) if exit_to_right \
            else (int(T_OPEN * 1.25), T_OPEN)
        lead_ease = QEasingCurve.Type.OutQuart
        trail_ease = QEasingCurve.Type.OutBack

        open_l = QPropertyAnimation(self._left, b"progress", self)
        open_l.setDuration(lead_ms if exit_to_right else trail_ms)
        open_l.setStartValue(1.0)
        open_l.setEndValue(0.0)
        open_l.setEasingCurve(lead_ease if exit_to_right else trail_ease)

        open_r = QPropertyAnimation(self._right, b"progress", self)
        open_r.setDuration(trail_ms if exit_to_right else lead_ms)
        open_r.setStartValue(1.0)
        open_r.setEndValue(0.0)
        open_r.setEasingCurve(trail_ease if exit_to_right else lead_ease)

        open_grp = QParallelAnimationGroup(self)
        open_grp.addAnimation(open_l)
        open_grp.addAnimation(open_r)

        # sıra: kapan → dalga → mini nefes → aç
        seq = QSequentialAnimationGroup(self)
        seq.addAnimation(close_grp)
        seq.addAnimation(wave)
        seq.addPause(PAUSE)
        seq.addAnimation(open_grp)
        seq.finished.connect(self._on_done)
        self._anim = seq

        seq.start()

    def _on_done(self) -> None:
        self._left.progress = 0.0
        self._right.progress = 0.0
        self._wave.sweep = 0.0
        self.hide()
        self._anim = None
