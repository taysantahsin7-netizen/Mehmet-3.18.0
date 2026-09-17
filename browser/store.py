# -*- coding: utf-8 -*-
"""
NAVİGATÖR DEPOSU — gezinme geçmişi + sık kullanılanlar + görüntü ayarları.

Kalıcılık: config/navigator_store.json
    {
      "history":   [{"title":…, "url":…, "count":…, "ts":…}, …],
      "bookmarks": [{"title":…, "url":…, "ts":…}, …],
      "view":      {"dark": false, "font_pt": 0, "zoom_pct": 100}
    }

Akıllı tamamlama: yer imleri önce, sonra geçmiş (sıklık + tazelik puanı).
"""
from __future__ import annotations

import json
import time
from pathlib import Path

MAX_HISTORY = 500          # tutulan toplam kayıt
MAX_SUGGEST = 8            # tamamlama öneri sayısı


class NavigatorStore:
    """Geçmiş + yer imleri + görüntü ayarları (tek JSON dosyası)."""

    def __init__(self, path: Path | None = None):
        self._path = Path(path) if path else None
        self.history: list[dict] = []       # en yeni sonda
        self.bookmarks: list[dict] = []
        self.view: dict = {"dark": False, "font_pt": 0, "zoom_pct": 100}
        self.load()

    # ── kalıcılık ────────────────────────────────────────────────────────────
    def load(self) -> None:
        data: dict = {}
        if self._path and self._path.exists():
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
            except Exception:
                data = {}
        self.history = list(data.get("history") or [])
        self.bookmarks = list(data.get("bookmarks") or [])
        v = data.get("view") or {}
        self.view = {
            "dark": bool(v.get("dark", False)),
            "font_pt": int(v.get("font_pt", 0)),
            "zoom_pct": int(v.get("zoom_pct", 100)),
        }

    def save(self) -> None:
        if not self._path:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "history": self.history[-MAX_HISTORY:],
            "bookmarks": self.bookmarks,
            "view": self.view,
        }
        try:
            self._path.write_text(
                json.dumps(payload, indent=1, ensure_ascii=False),
                encoding="utf-8")
        except Exception:
            pass

    # ── geçmiş ───────────────────────────────────────────────────────────────
    @staticmethod
    def _norm(url: str) -> str:
        """Sorgu/fragma at — aynı sayfa tekrar sayılmasın."""
        u = url.split("#", 1)[0]
        if "?" in u:
            u = u.split("?", 1)[0]
        return u

    def add_visit(self, url: str, title: str = "") -> None:
        if not url.startswith(("http://", "https://")):
            return
        key = self._norm(url)
        now = time.time()
        for entry in reversed(self.history):
            if entry.get("key") == key:
                entry["count"] = int(entry.get("count", 1)) + 1
                entry["ts"] = now
                if title:
                    entry["title"] = title
                break
        else:
            self.history.append({
                "key": key, "url": url,
                "title": title or url,
                "count": 1, "ts": now,
            })
        if len(self.history) > MAX_HISTORY:
            self.history = self.history[-MAX_HISTORY:]
        self.save()

    def clear_history(self) -> None:
        self.history.clear()
        self.save()

    # ── yer imleri ───────────────────────────────────────────────────────────
    def is_bookmarked(self, url: str) -> bool:
        key = self._norm(url)
        return any(b.get("key") == key for b in self.bookmarks)

    def toggle_bookmark(self, url: str, title: str = "") -> bool:
        """Yer imini çevir. Dönüş: artık yer imi mi?"""
        key = self._norm(url)
        for b in self.bookmarks:
            if b.get("key") == key:
                self.bookmarks.remove(b)
                self.save()
                return False
        self.bookmarks.append({
            "key": key, "url": url,
            "title": title or url, "ts": time.time(),
        })
        self.save()
        return True

    def remove_bookmark(self, url: str) -> bool:
        key = self._norm(url)
        before = len(self.bookmarks)
        self.bookmarks = [b for b in self.bookmarks if b.get("key") != key]
        if len(self.bookmarks) < before:
            self.save()
            return True
        return False

    # ── akıllı tamamlama ─────────────────────────────────────────────────────
    @staticmethod
    def _score(entry: dict, q: str) -> float:
        """Sıklık + tazelik + başlık/url eşleşmesi. q=zaten küçük harf."""
        title = str(entry.get("title", "")).lower()
        url = str(entry.get("url", "")).lower()
        if q:
            if q not in title and q not in url:
                return 0.0
            # başlıkta/alan adında baştan eşleşme bonusu
            if title.startswith(q) or url.startswith(q):
                base = 2.0
            else:
                base = 1.0
        else:
            base = 1.0
        freq = 1.0 + float(entry.get("count", 1)) ** 0.5
        age_h = max(0.0, time.time() - float(entry.get("ts", 0))) / 3600.0
        fresh = 1.0 + 4.0 / (1.0 + age_h)      # 1..5 arası, yeni yüksek
        bookmark = 2.0 if entry.get("bm") else 0.0
        return base * freq * fresh + bookmark

    def suggestions(self, query: str, limit: int = MAX_SUGGEST) -> list[dict]:
        """Adres çubuğu önerileri: [{'title', 'url', 'bm': bool}, …]."""
        q = (query or "").strip().lower()
        pool: list[dict] = []
        seen: set[str] = set()
        for b in self.bookmarks:
            pool.append({"title": b.get("title", ""), "url": b.get("url", ""),
                         "bm": True, "count": b.get("count", 1),
                         "ts": b.get("ts", 0)})
            seen.add(b.get("key", ""))
        for h in self.history:
            if h.get("key") in seen:
                continue
            pool.append({"title": h.get("title", ""), "url": h.get("url", ""),
                         "bm": False, "count": h.get("count", 1),
                         "ts": h.get("ts", 0)})
        scored = [(self._score(e, q), e) for e in pool]
        scored = [se for se in scored if se[0] > 0]
        scored.sort(key=lambda se: se[0], reverse=True)
        return [{"title": e["title"], "url": e["url"], "bm": e["bm"]}
                for _, e in scored[:limit]]

    # ── görüntü ayarları ─────────────────────────────────────────────────────
    def get_view(self) -> dict:
        return dict(self.view)

    def set_view(self, **kv) -> None:
        self.view.update(kv)
        self.save()
