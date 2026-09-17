# -*- coding: utf-8 -*-
"""
NAVİGATÖR WEB HAFIZASI — Mehmet'in gezdiği sayfaları özetleyerek kaydeder.

Akış:
    1) Mehmet bir sayfayı read_browser_page ile okuduğunda main.py
       ``record_visit_summary``'yi arka planda çağırır.
    2) Bu modül sayfa metnini yerel LLM ile ≤220 karakterlik Türkçe
       bir özete indirger ve ``config/web_memory.json``'a yazar.
    3) ``web_memory_list`` / ``web_memory_overview`` araçlarıyla Mehmet
       "nereleri gezmiştik?" sorusunu kendi hafızasından yanıtlar.

Kalıcılık: config/web_memory.json  (en fazla MAX_ENTRIES kayıt)
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

MAX_ENTRIES = 40
MIN_TEXT_LEN = 200          # bundan kısa sayfa özetlenmez
SUMMARY_MAX = 220           # özet uzunluk sınırı (karakter)

_lock = threading.Lock()
_path: Path | None = None


def _store_path() -> Path:
    global _path
    if _path is None:
        _path = Path(__file__).resolve().parent.parent / "config" / \
            "web_memory.json"
    return _path


def _load() -> list[dict]:
    p = _store_path()
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save(items: list[dict]) -> None:
    p = _store_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        p.write_text(json.dumps(items[-MAX_ENTRIES:], indent=1,
                                ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _norm_url(url: str) -> str:
    """Aynı sayfanın farklı sorgu/fragma varyantları teke indirilir."""
    u = (url or "").split("#", 1)[0]
    if "?" in u:
        u = u.split("?", 1)[0]
    return u.rstrip("/")


def _already_seen(items: list[dict], url: str) -> bool:
    nu = _norm_url(url)
    return any(_norm_url(it.get("url", "")) == nu for it in items)


# ── kayıt ────────────────────────────────────────────────────────────────────

def record_visit_summary(title: str, url: str, text: str) -> str | None:
    """Sayfayı LLM ile özetler ve hafızaya ekler. (Arka plan thread'i.)

    Dönüş: özet metni; özetlenmediyse None (kural gereği).
    """
    url = (url or "").strip()
    title = (title or "").strip()
    text = (text or "").strip()
    if not url.startswith(("http://", "https://")):
        return None
    if len(text) < MIN_TEXT_LEN:
        return None

    with _lock:
        items = _load()
        if _already_seen(items, url):
            return None

    # Yerel LLM ile kısa Türkçe özet (ağır iş — çağıran thread'de)
    try:
        from core.llm_client import call_llm_text
        clip = text[:6000]
        summary = call_llm_text(
            f"BAŞLIK: {title}\nURL: {url}\n\nSAYFA METNİ:\n{clip}\n\n"
            "Bu sayfayı en fazla 220 karakterlik TEK paragraf Türkçe özetle. "
            "Sadece özeti yaz, başlık/karşılama ekleme.",
            system="Sen özet uzmanısın. Yalnızca özet metnini döndürürsün.",
            timeout=30,
        )
    except Exception:
        return None
    summary = (summary or "").strip().strip('"')
    if not summary:
        return None
    summary = summary[:SUMMARY_MAX]

    entry = {"title": title[:120], "url": url,
             "summary": summary, "ts": time.time()}
    with _lock:
        items = _load()
        if _already_seen(items, url):
            return summary
        items.append(entry)
        _save(items)
    return summary


# ── sorgulama (Mehmet araçları) ──────────────────────────────────────────────

def web_memory_list(limit: int = 8) -> str:
    """Son gezip özetlediği sayfalar (yeni → eski)."""
    items = _load()
    if not items:
        return ("Web hafızası henüz boş — NAVİGATÖR'de gezindikçe sayfa "
                "özetleri burada birikecek.")
    items = sorted(items, key=lambda it: it.get("ts", 0), reverse=True)
    lines = []
    for it in items[:max(1, min(limit, MAX_ENTRIES))]:
        ts = time.strftime("%d.%m %H:%M", time.localtime(it.get("ts", 0)))
        lines.append(f"• [{ts}] {it.get('title') or '?'}\n"
                     f"  {it.get('url', '')[:90]}\n"
                     f"  ↳ {it.get('summary', '')}")
    return "\n".join(lines)


def web_memory_overview() -> str:
    """Hafıza genel durumu: kayıt sayısı + en çok ziyaret edilen alan adları."""
    items = _load()
    if not items:
        return ("Web hafızası boş. NAVİGATÖR ile gezinti yaptıkça sayfa "
                "özetleri otomatik kaydedilir.")
    from collections import Counter
    hosts = Counter()
    for it in items:
        h = it.get("url", "").split("://", 1)[-1].split("/", 1)[0]
        if h:
            hosts[h] += 1
    top = ", ".join(f"{h} ({n})" for h, n in hosts.most_common(6))
    return (f"Web hafızasında {len(items)} sayfa özeti var.\n"
            f"En çok gezilen: {top}")


def web_memory_forget(url_substr: str = "") -> int:
    """Eşleşen kayıtları siler (url parçasına göre); boşsa hepsini temizler."""
    with _lock:
        items = _load()
        if not url_substr:
            n = len(items)
            _save([])
            return n
        keep = [it for it in items
                if url_substr.lower() not in it.get("url", "").lower()]
        removed = len(items) - len(keep)
        if removed:
            _save(keep)
        return removed


# ── haftalık rapor (alışkanlık: 7 günde bir LLM özeti) ──────────────────────

REPORT_PATH = Path(__file__).resolve().parent.parent / "config" / \
    "web_memory_reports.json"
REPORT_INTERVAL = 7 * 24 * 3600.0    # 7 gün
MIN_ENTRIES_FOR_REPORT = 5           # bundan az yeni kayıt varsa rapor yazma


def _load_reports() -> dict:
    if not REPORT_PATH.exists():
        return {"last_ts": 0.0, "reports": []}
    try:
        d = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
        if isinstance(d, dict) and "reports" in d:
            return d
    except Exception:
        pass
    return {"last_ts": 0.0, "reports": []}


def _save_reports(d: dict) -> None:
    try:
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(json.dumps(d, indent=1, ensure_ascii=False),
                               encoding="utf-8")
    except Exception:
        pass


def due_for_report() -> bool:
    """Son rapordan bu yana 7 gün geçti mi VE yeterli yeni kayıt var mı?"""
    d = _load_reports()
    if time.time() - d.get("last_ts", 0.0) < REPORT_INTERVAL:
        return False
    items = _load()
    cutoff = d.get("last_ts", 0.0)
    fresh = [it for it in items if it.get("ts", 0) > cutoff]
    return len(fresh) >= MIN_ENTRIES_FOR_REPORT


def generate_weekly_report(force: bool = False) -> str:
    """Son 7 günün gezip özetlediği sayfalardan haftalık rapor üretir.

    Alışkanlık akışı: uygulama açılışında ``due_for_report()`` kontrol edilir;
    due ise bu fonksiyon arka plan thread'inde çağrılır, Mehmet raporu sesle
    okur. ``force=True`` kullanıcı talebiyle anında üretir.
    Dönüş: rapor metni; üretilmediyse kısa açıklama.
    """
    d = _load_reports()
    cutoff = 0.0 if force else d.get("last_ts", 0.0)
    items = [it for it in _load() if it.get("ts", 0) > cutoff]
    items.sort(key=lambda it: it.get("ts", 0))
    if len(items) < MIN_ENTRIES_FOR_REPORT and not force:
        return ("Haftalık rapor için yeterli yeni gezinti yok "
                f"({len(items)}/{MIN_ENTRIES_FOR_REPORT}).")

    listing = "\n".join(
        f"- [{time.strftime('%d.%m', time.localtime(it.get('ts', 0)))}] "
        f"{it.get('title') or '?'} — {it.get('summary', '')}"
        for it in items[-30:])
    try:
        from core.llm_client import call_llm_text
        report = call_llm_text(
            "Aşağıda son bir haftada gezilen web sayfalarının özetleri var:\n\n"
            + listing +
            "\n\nBunlardan 5-8 cümlelik TÜRKÇE bir haftalık gezinti raporu yaz: "
            "temaları, ilgi alanlarını ve dikkat çeken bulguları vurgula. "
            "Sadece raporu yaz, selamlama ekleme.",
            system="Sen kısa ve net raporlar yazarsın.", timeout=45)
    except Exception as e:
        return f"Haftalık rapor üretilemedi: {e}"
    report = (report or "").strip()
    if not report:
        return "Haftalık rapor üretilemedi (LLM boş yanıt)."

    if not force:
        d["last_ts"] = time.time()
        d.setdefault("reports", []).append({
            "ts": time.time(), "pages": len(items), "text": report[:2000]})
        d["reports"] = d["reports"][-26:]
        _save_reports(d)
    return report


# ── aylık derin rapor ────────────────────────────────────────────────────────

MONTHLY_INTERVAL = 30 * 24 * 3600.0   # 30 gün
MONTHLY_REPORT_PATH = Path(__file__).resolve().parent.parent / "config" / \
    "web_memory_monthly.json"


def _load_monthly() -> dict:
    if not MONTHLY_REPORT_PATH.exists():
        return {"last_ts": 0.0, "reports": []}
    try:
        d = json.loads(MONTHLY_REPORT_PATH.read_text(encoding="utf-8"))
        if isinstance(d, dict) and "reports" in d:
            return d
    except Exception:
        pass
    return {"last_ts": 0.0, "reports": []}


def _save_monthly(d: dict) -> None:
    try:
        MONTHLY_REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        MONTHLY_REPORT_PATH.write_text(
            json.dumps(d, indent=1, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def due_for_monthly_report() -> bool:
    """Son aylık rapordan 30 gün geçti mi VE en az 10 yeni kayıt var mı?"""
    d = _load_monthly()
    if time.time() - d.get("last_ts", 0.0) < MONTHLY_INTERVAL:
        return False
    items = _load()
    cutoff = d.get("last_ts", 0.0)
    fresh = [it for it in items if it.get("ts", 0) > cutoff]
    return len(fresh) >= 10


def generate_monthly_report(force: bool = False) -> str:
    """Son 30 günün gezilerinden DERİN analiz raporu üretir.

    Haftalık rapordan farkı: temalar, ilgi alanı kümeleri, zaman dağılımı,
    değişim eğilimleri — 10-15 cümlelik yapılandırılmış Türkçe analiz.
    """
    d = _load_monthly()
    cutoff = 0.0 if force else d.get("last_ts", 0.0)
    month_ago = time.time() - MONTHLY_INTERVAL
    items = [it for it in _load()
             if it.get("ts", 0) > max(cutoff, 0.0)
             and it.get("ts", 0) >= month_ago]
    items.sort(key=lambda it: it.get("ts", 0))
    if len(items) < 10 and not force:
        return ("Aylık rapor için yeterli gezinti yok "
                f"({len(items)}/10).")

    listing = "\n".join(
        f"- [{time.strftime('%d.%m', time.localtime(it.get('ts', 0)))}] "
        f"{(it.get('url') or '').split('://', 1)[-1].split('/', 1)[0]}: "
        f"{it.get('title') or '?'} — {it.get('summary', '')}"
        for it in items[-60:])
    try:
        from core.llm_client import call_llm_text
        report = call_llm_text(
            "Aşağıda son bir AYDA gezilen web sayfalarının özetleri var "
            f"(toplam {len(items)} sayfa):\n\n" + listing +
            "\n\nBunlardan 10-15 cümlelik TÜRKÇE bir AYLIK DERİN RAPOR yaz. "
            "Şu başlıkları kaplasın: (1) en belirgin ilgi alanları/temalar, "
            "(2) zaman içindeki değişim veya odak kaymaları, "
            "(3) dikkat çeken veya tekrarlayan konular, "
            "(4) kısa değerlendirme. Sadece raporu yaz, selamlama ekleme.",
            system="Sen analitik ve kısa raporlar yazarsın.", timeout=60)
    except Exception as e:
        return f"Aylık rapor üretilemedi: {e}"
    report = (report or "").strip()
    if not report:
        return "Aylık rapor üretilemedi (LLM boş yanıt)."

    if not force:
        d["last_ts"] = time.time()
        d.setdefault("reports", []).append({
            "ts": time.time(), "pages": len(items), "text": report[:4000]})
        d["reports"] = d["reports"][-24:]
        _save_monthly(d)
    return report


# ── rapor e-posta / telefon bildirimi ────────────────────────────────────────

EMAIL_CFG_PATH = Path(__file__).resolve().parent.parent / "config" / \
    "email_notify.json"


def email_config() -> dict:
    """E-posta bildirim ayarları (yoksa kapalı fabrika değeri)."""
    default = {
        "enabled": False,
        "smtp_host": "smtp.gmail.com",
        "smtp_port": 587,
        "tls": True,
        "user": "",
        "password": "",
        "to_addr": "",
    }
    if not EMAIL_CFG_PATH.exists():
        try:
            EMAIL_CFG_PATH.parent.mkdir(parents=True, exist_ok=True)
            EMAIL_CFG_PATH.write_text(json.dumps(default, indent=1),
                                      encoding="utf-8")
        except Exception:
            pass
        return default
    try:
        d = json.loads(EMAIL_CFG_PATH.read_text(encoding="utf-8"))
        default.update(d if isinstance(d, dict) else {})
    except Exception:
        pass
    return default


def save_email_config(cfg: dict) -> None:
    try:
        EMAIL_CFG_PATH.parent.mkdir(parents=True, exist_ok=True)
        EMAIL_CFG_PATH.write_text(json.dumps(cfg, indent=1,
                                             ensure_ascii=False),
                                  encoding="utf-8")
    except Exception:
        pass


def email_report(subject: str, body: str, to_addr: str = "") -> tuple[bool, str]:
    """Raporu SMTP ile gönderir. Dönüş: (başarılı, açıklama).

    Yapılandırma config/email_notify.json'dan okunur; ``enabled=false``
    veya eksik alanlarda sessizce (False, ...) döner — asla çökmez.
    """
    import smtplib
    from email.mime.text import MIMEText
    from email.header import Header
    from email.utils import formataddr

    cfg = email_config()
    if not cfg.get("enabled"):
        return False, "E-posta bildirimi kapalı (config/email_notify.json)."
    host = cfg.get("smtp_host") or ""
    user = cfg.get("user") or ""
    pwd = cfg.get("password") or ""
    to_addr = to_addr or cfg.get("to_addr") or user
    if not (host and user and pwd and to_addr):
        return False, "E-posta ayarları eksik (host/user/password/to_addr)."
    try:
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = Header(subject, "utf-8")
        msg["From"] = formataddr(("Mehmet", user))
        msg["To"] = to_addr
        port = int(cfg.get("smtp_port") or 587)
        with smtplib.SMTP(host, port, timeout=20) as s:
            if cfg.get("tls", True):
                s.starttls()
            s.login(user, pwd)
            s.send_message(msg)
        return True, f"Rapor gönderildi → {to_addr}"
    except Exception as e:
        return False, f"E-posta gönderilemedi: {e}"


def send_last_report_email(kind: str = "weekly") -> tuple[bool, str]:
    """Üretilmiş son raporu e-postalar (``kind``: weekly|monthly).
    Gönderim sonucunu rapor kaydına işler."""
    if kind == "monthly":
        d = _load_monthly()
    else:
        d = _load_reports()
    reps = d.get("reports") or []
    if not reps:
        return False, "Gönderilecek rapor yok."
    last = reps[-1]
    stamp = time.strftime("%d.%m.%Y", time.localtime(last.get("ts", 0)))
    title = "Mehmet Aylık Web Raporu" if kind == "monthly" \
        else "Mehmet Haftalık Web Raporu"
    ok, msg = email_report(f"{title} — {stamp}", last.get("text", ""))
    last["emailed"] = bool(ok)
    last["email_msg"] = msg
    if kind == "monthly":
        _save_monthly(d)
    else:
        _save_reports(d)
    return ok, msg
