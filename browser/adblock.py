# -*- coding: utf-8 -*-
"""
NAVİGATÖR ADBLOCK — reklam / izleyici engelleme motoru.

Kural sözdizimi (basitleştirilmiş uBlock-stili):
    ||example.com^                → alan adı engelle
    @@||example.com^              → alan adını beyaz listeye al
    ||cdn.com/banner              → yol / alt alan adı engelle
    example.com##.ad-banner       → kozmetik filtre (CSS seçicisi gizle)
    ! yorum                       → kural değil

Kurallar dosyadan yüklenir: config/adblock_rules.txt
İstek engelleme QtWebEngine URL interceptor ile yapılır; kozmetik filtreler
QWebEngineScript ile sayfaya enjekte edilir (MutationObserver destekli —
dinamik yüklenen reklamları da yakalar).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

# ── varsayılan gömülü kurallar (kullanıcı dosyası yoksa kullanılır) ──────────
DEFAULT_RULES = """\
! NAVİGATÖR ADBLOCK — gömülü varsayılan kurallar
! Alan adı engelleri
||doubleclick.net^
||googlesyndication.com^
||googleadservices.com^
||google-analytics.com^
||analytics.google.com^
||googletagmanager.com^
||adservice.google.com^
||facebook.net^
||connect.facebook.net^
||ads.twitter.com^
||analytics.tiktok.com^
||ads.linkedin.com^
||ads.pinterest.com^
||scorecardresearch.com^
||quantserve.com^
||criteo.com^
||criteo.net^
||outbrain.com^
||taboola.com^
||hotjar.com^
||mouseflow.com^
||fullstory.com^
||clickaine.com^
||propellerads.com^
||popads.net^
||adnxs.com^
||rtbhouse.com^
||smartadserver.com^
||pubmatic.com^
||rubiconproject.com^
||openx.net^
||casalemedia.com^
||adform.net^
||yieldmo.com^
||moatads.com^
||adsrvr.org^
||amazon-adsystem.com^
||3lift.com^
||bidswitch.net^
||sharethrough.com^
||zedo.com^
! --- YouTube reklam uçları (video reklamlar + API istekleri) ---
||youtube.com/api/stats/ads
||youtube.com/pagead/
||youtube.com/ptracking
||youtube.com/get_midroll_info
||youtube.com/youtubei/v1/log_event
||youtube.com/error_204
||youtube.com/csi_204
||youtube-nocookie.com/api/stats/ads
! --- Google reklam sunucuları (arama sayfası reklamları) ---
||google.com/pagead/
||google.com/adsbygoogle
||google.com/aclk
||googleads.g.doubleclick.net^
! --- video reklam UPR/videoplayback reklam segmentleri ---
/googleads/gpt/
/pagead/adview?
/pagead/interaction
/ptracking?
! Beyaz liste (istisnalar) — yalnızca GERÇEK media uçları
@@||googlevideo.com/videoplayback
@@||youtube.com/api/stats/qoe
@@||youtube.com/api/stats/atr
@@||youtube.com/watch$
@@||youtube.com/get_video_info
! --- kozmetik: yaygın reklam konteynerleri ---
##.ad-banner
##.ad-container
##.adsbygoogle
##[id^="google_ads_"]
##[id^="div-gpt-ad"]
##iframe[src*="doubleclick.net"]
##iframe[src*="googlesyndication.com"]
! --- YouTube kozmetik: player reklam katmanları + üst banner ---
youtube.com##.ytp-ad-player-overlay
youtube.com##.ytp-ad-module
youtube.com##.ytp-ad-overlay-container
youtube.com##.ytp-ad-text-overlay
youtube.com##.ytp-ad-image-overlay
youtube.com##.ytp-ad-action-interstitial
youtube.com##.ytd-display-ad-renderer
youtube.com##.ytd-promoted-sparkles-web-renderer
youtube.com##.ytd-promoted-video-renderer
youtube.com##.ytd-companion-slot-renderer
youtube.com##.ytd-ads-engagement-panel-content-renderer
youtube.com##ytd-rich-item-renderer[is-slim]
youtube.com##ytd-ad-slot-renderer
youtube.com##ytm-companion-slot
youtube.com###masthead-ad
youtube.com##ytd-mealbar-promo-renderer
youtube.com##.ytd-merch-shelf-renderer
"""


@dataclass
class AdblockStats:
    blocked: int = 0
    allowed: int = 0
    cosmetic_hidden: int = 0
    last_host: str = ""

    def reset(self) -> None:
        self.blocked = 0
        self.allowed = 0
        self.cosmetic_hidden = 0


@dataclass
class _CompiledRule:
    """Tek bir derlenmiş engelleme/beyaz liste kuralı."""
    pattern: str
    is_exception: bool = False
    regex: re.Pattern | None = None


class AdblockEngine:
    """URL çubuğu temelli hızlı engelleme motoru.

    ``decide(url)`` her kaynak isteği için çağrılır: True → istek engellenir.
    ``cosmetic_script()`` sayfaya enjekte edilecek CSS/JS'i döndürür.
    """

    def __init__(self, rules_path: Path | None = None):
        self.stats = AdblockStats()
        self._rules_path = Path(rules_path) if rules_path else None
        self._block: list[_CompiledRule] = []
        self._allow: list[_CompiledRule] = []
        self._cosmetic: list[tuple[str, str]] = []   # (domain-filter, css-selector)
        self._enabled = True
        self.reload()

    # ── durum ────────────────────────────────────────────────────────────────
    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, on: bool) -> None:
        self._enabled = bool(on)
        if not on:
            self.stats.reset()

    @property
    def rule_count(self) -> int:
        return len(self._block) + len(self._allow) + len(self._cosmetic)

    def last_host(self) -> str:
        return self.stats.last_host

    # ── kural yükleme ────────────────────────────────────────────────────────
    def reload(self) -> None:
        """Kuralları diske ve gömülü varsayılanlara göre yeniden derle."""
        text = DEFAULT_RULES
        if self._rules_path and self._rules_path.exists():
            try:
                text += "\n" + self._rules_path.read_text(
                    encoding="utf-8", errors="replace")
            except Exception:
                pass
        block: list[_CompiledRule] = []
        allow: list[_CompiledRule] = []
        self._cosmetic.clear()
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith("!") or line.startswith("["):
                continue
            if line.startswith("@@"):
                rule = self._compile(line[2:])
                if rule:
                    rule.is_exception = True
                    allow.append(rule)
                continue
            if "##" in line:
                dom, _, sel = line.partition("##")
                self._cosmetic.append((dom.strip().lower(), sel.strip()))
                continue
            rule = self._compile(line)
            if rule:
                block.append(rule)
        self._block = block
        self._allow = allow
        self._raw_cosmetic = [
            l for l in text.splitlines()
            if l.strip() and "##" in l and not l.strip().startswith(("!", "["))
        ]

    def _compile(self, pat: str) -> _CompiledRule | None:
        """ABP benzeri kuralı tam-URL regex'ine derler.

        Desteklenen sözdizimi:
            ||host^              → host (ve alt alan adları) herhangi bir yolda
            ||host/yol*          → host + yol öneki (joker *)
            /regex/              → ham JS-uyumlu regex (tüm URL'de arar)
            düz metin            → URL'nin herhangi bir yerinde alt dize
        """
        pat = pat.strip()
        if not pat or pat in ("@", "@@"):
            return None

        # ham regex kuralı: /pattern/
        if len(pat) > 2 and pat.startswith("/") and pat.endswith("/"):
            try:
                rx = re.compile(pat[1:-1], re.IGNORECASE)
                return _CompiledRule(pattern=pat, regex=rx)
            except re.error:
                return None

        anchor_host = pat.startswith("||")
        if anchor_host:
            pat = pat[2:]
        pat = pat.replace("^", "/") if pat.endswith("^") else pat
        pat = pat.rstrip("^")

        # joker karakterleri regex'e çevir, gerisini kaçişle
        parts = pat.split("*")
        body = ".*".join(re.escape(p) for p in parts)

        host = pat.split("/")[0].lower()
        if anchor_host:
            if "/" in pat:
                # ||host/yol → host sınırlayıcı + yol
                host_part = re.escape(host)
                path_part = body[len(host_part):] or "/"
                rx = re.compile(
                    f"^[a-z]+://([^/]*\\.)?{host_part}{path_part}",
                    re.IGNORECASE)
            else:
                rx = re.compile(
                    f"^[a-z]+://([^/]*\\.)?{re.escape(host)}(:|/|$)",
                    re.IGNORECASE)
        else:
            rx = re.compile(body, re.IGNORECASE)
        return _CompiledRule(pattern=pat, regex=rx)

    @staticmethod
    def _rule_matches(rule: _CompiledRule, url: str, host: str) -> bool:
        """Derlenmiş kuralı tam URL'ye karşı değerlendirir."""
        if rule.regex is not None:
            return rule.regex.search(url) is not None
        return AdblockEngine._matches(host, rule.pattern)

    # ── karar motoru ─────────────────────────────────────────────────────────
    @staticmethod
    def _host_of(url: str) -> str:
        h = url.split("://", 1)[-1]
        h = h.split("/", 1)[0]
        h = h.split(":", 1)[0]
        return h.lower().lstrip(".")

    @staticmethod
    def _matches(host: str, rule_host: str) -> bool:
        if not rule_host:
            return False
        return host == rule_host or host.endswith("." + rule_host)

    def decide(self, url: str) -> bool:
        """True dönerse istek ENGELLENİR. Yalnızca http(s) şemaları değerlendirilir."""
        if not self._enabled:
            return False
        if not url.startswith(("http://", "https://")):
            return False
        host = self._host_of(url)
        if not host:
            return False
        self.stats.last_host = host
        # istisnalar önce
        for r in self._allow:
            if self._rule_matches(r, url, host):
                self.stats.allowed += 1
                return False
        for r in self._block:
            if self._rule_matches(r, url, host):
                self.stats.blocked += 1
                return True
        return False

    def match_count(self, url: str) -> int:
        """URL'e eşleşen engelleme kuralı sayısı (tanılama için)."""
        if not url.startswith(("http://", "https://")):
            return 0
        host = self._host_of(url)
        return sum(1 for r in self._block if self._rule_matches(r, url, host))

    # ── kozmetik filtreler ───────────────────────────────────────────────────
    def cosmetic_rules(self) -> list[tuple[str, str]]:
        return list(self._cosmetic)

    def cosmetic_script(self, current_url: str) -> str:
        """Verilen sayfa için kozmetik gizleme JS'i (sekme başına enjeksiyon).

        MutationObserver içerir: SPA/dinamik yüklenen reklam kutuları DOM'a
        eklendiği anda gizlenir (tek seferlik CSS yeterli değildir).
        """
        if not self._enabled or not self._cosmetic:
            return ""
        host = self._host_of(current_url)
        sels: list[str] = []
        for dom, sel in self._cosmetic:
            if dom and dom != "*" and not self._matches(host, dom):
                continue
            if sel:
                sels.append(sel)
        if not sels:
            return ""
        joined = ",\n".join(sels).replace("`", "\\`")
        return (
            "(() => {"
            "const css = `" + joined + "`;"
            "const st = document.createElement('style');"
            "st.id = '__nav_sect_kozmetik';"
            "st.textContent = css + '{display:none!important}';"
            "(document.head||document.documentElement).appendChild(st);"
            "const hide = (root) => { try {"
            "root.querySelectorAll(" + json.dumps(",".join(sels)) + ")"
            ".forEach(el => { el.style.display='none';"
            "el.dataset.navHidden='1'; }); } catch(e){} };"
            "hide(document.body||document.documentElement);"
            "const mo = new MutationObserver(muts => {"
            "for (const m of muts) { m.addedNodes && m.addedNodes.forEach(n => {"
            "if (n.nodeType===1) hide(n.parentElement||document.body); }); } });"
            "mo.observe(document.documentElement,"
            "{childList:true,subtree:true});"
            "})();"
        )

    # ── YouTube player-ad skipper ────────────────────────────────────────────
    @staticmethod
    def youtube_skip_script() -> str:
        """YouTube'a özgü player-reklam atlayıcı (her yükleme sonunda
        enjekte edilir). Reklam katmanını kapatır, videoyu ileri sarar,
        'Reklamı atla' düğmesine otomatik basar; MutationObserver ile
        yeni reklamları da yakalar. Reklamsız deneyim için kural
        engellemeyle birlikte çalışır."""
        return (
            "(function(){"
            "if(window.__navYtSkip)return; window.__navYtSkip=true;"
            "const kill=()=>{"
            "document.querySelectorAll('.ytp-ad-player-overlay,'"
            "'.ytp-ad-module,'"
            "'.ytp-ad-overlay-container,'"
            "'.ytp-ad-text-overlay,'"
            "'.ytp-ad-image-overlay,'"
            "'.ytd-companion-slot-renderer,'"
            "'ytd-ad-slot-renderer').forEach(e=>{e.remove();});"
            "const v=document.querySelector('video.video-stream');"
            "if(v){"
            "const ad=document.querySelector('.ytp-ad-player-overlay,'"
            "'.ad-showing,.ytp-ad-module');"
            "if(ad){try{v.currentTime=v.duration>0?v.duration:1e5;}catch(e){}}"
            "}"
            "const btn=document.querySelector('.ytp-ad-skip-button,.ytp-ad-skip-button-modern,'"
            "'.ytp-skip-ad-button');"
            "if(btn){btn.click();}"
            "};"
            "setInterval(kill,300);"
            "const mo=new MutationObserver(kill);"
            "mo.observe(document.documentElement,{childList:true,subtree:true});"
            "})();"
        )

    # ── kullanıcı kural dosyası ──────────────────────────────────────────────
    def user_rules_text(self) -> str:
        if self._rules_path and self._rules_path.exists():
            try:
                return self._rules_path.read_text(encoding="utf-8",
                                                  errors="replace")
            except Exception:
                return ""
        return ""

    def save_user_rules(self, text: str) -> None:
        if not self._rules_path:
            return
        self._rules_path.parent.mkdir(parents=True, exist_ok=True)
        self._rules_path.write_text(text, encoding="utf-8")
        self.reload()

    @property
    def rules_path(self) -> Path | None:
        return self._rules_path
