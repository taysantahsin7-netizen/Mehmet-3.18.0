# -*- coding: utf-8 -*-
"""
NEMOTRON İSTEMCİSİ — NVIDIA NIM üzerinden ``nemotron-3-ultra`` erişimi.

Ciddi mod (MehmetNEO) sırasında Mehmet'in canlı Live oturumu yerine bu modeli
kullanması için: uzun muhakemeli, derin cevaplar buradan üretilir.

API: https://integrate.api.nvidia.com/v1/chat/completions  (OpenAI-uyumlu)
Anahtar: config/api_keys.json → "nvapi_key"

Ana modüller:
    ask_nemotron(prompt, system)      → str   (tek cevap)
    ask_nemotron_chat(messages)       → str   (sohbet zinciri, hafızalı)
    route_llm(question)               -> str  ("gemini" | "nemotron") — ciddi mod kararı
    is_serious()                      -> bool — ui.is_serious_mode'okur (import döngüsüz)
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import requests

_BASE_DIR   = Path(__file__).resolve().parent.parent
_CONFIG     = _BASE_DIR / "config" / "api_keys.json"

NIM_BASE_URL = "https://integrate.api.nvidia.com/v1"
NIM_MODEL    = "nvidia/nemotron-3-ultra-550b-a55b"   # NVIDIA listesinde canlı id

# ── hafızalı sohbet (MehmetNEO uzun konuşmalarda bağlam unutmasın) ──────────
_CHAT_HISTORY: list[dict] = []
_CHAT_MAX   = 24            # saklanan mesaj sayısı tavanı


def _config() -> dict:
    try:
        return json.loads(_CONFIG.read_text(encoding="utf-8"))
    except Exception:
        return {}


def nvapi_key() -> str:
    return (_config().get("nvapi_key") or "").strip()


def is_available() -> bool:
    return bool(nvapi_key())


def is_serious() -> bool:
    """Ciddi mod durumu — ui'yi import etmeden okur (döngüsüz)."""
    try:
        import sys
        for m in sys.modules.values():
            if m and getattr(m, "__name__", "") == "ui" and hasattr(m, "MainWindow"):
                win = getattr(m, "_ACTIVE_WINDOW", None)
                if win is not None:
                    return bool(getattr(win, "is_serious_mode", False))
    except Exception:
        pass
    return False


def route_llm(question: str = "") -> str:
    """Yönlendirme: ciddi modda Nemotron, normalde Gemini.
    Uzun muhakeme isteyen sorular ('analiz', 'strateji', 'karar') ciddi olmasa da
    Nemotron'a gider — MehmetNEO'nun zekâ katmanı budur."""
    if is_serious():
        return "nemotron"
    q = (question or "").lower()
    deep = any(w in q for w in (
        "analiz", "strateji", "karar", "planla", "detaylı", "derin",
        "neden", "karşılaştır", "tartış", "değerlendir", "öneri"))
    return "nemotron" if deep and is_available() else "gemini"


def ask_nemotron_chat(messages: list[dict], max_tokens: int = 1200,
                      timeout: int = 90) -> str:
    """OpenAI-uyumlu /chat/completions çağrısı. messages: [{role, content}]."""
    key = nvapi_key()
    if not key:
        raise RuntimeError("NVIDIA NIM anahtarı yok (config/api_keys.json → nvapi_key)")
    try:
        resp = requests.post(
            f"{NIM_BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {key}",
                "Accept":        "application/json",
                "Content-Type":  "application/json",
            },
            json={
                "model":       NIM_MODEL,
                "messages":    messages,
                "temperature": 0.4,
                "top_p":       0.9,
                "max_tokens":  max_tokens,
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        return ((data.get("choices") or [{}])[0].get("message", {})
                .get("content") or "").strip()
    except requests.exceptions.Timeout:
        raise RuntimeError("Nemotron zaman aşımı — NIM sunucusu yanıt vermedi.")
    except requests.exceptions.HTTPError as e:
        body = ""
        try:
            body = e.response.text[:200]
        except Exception:
            pass
        raise RuntimeError(f"Nemotron HTTP {e.response.status_code}: {body}")


def ask_nemotron(prompt: str, system: str | None = None,
                 use_history: bool = False, **kw) -> str:
    """Tek soru → cevap. use_history=True ise hafızalı sohbet zinciri kullanılır."""
    msgs: list[dict] = []
    if system:
        msgs.append({"role": "system", "content": system})
    if use_history:
        msgs.extend(_CHAT_HISTORY)
    msgs.append({"role": "user", "content": prompt})
    out = ask_nemotron_chat(msgs, **kw)
    if use_history and out:
        _CHAT_HISTORY.append({"role": "user", "content": prompt})
        _CHAT_HISTORY.append({"role": "assistant", "content": out})
        while len(_CHAT_HISTORY) > _CHAT_MAX:
            _CHAT_HISTORY.pop(0)
    return out


def nemotron_reply_for_neo(user_text: str, context: str = "") -> str:
    """MehmetNEO'nun canlı oturum devriği: ciddi mod kişiliğiyle cevap üretir.
    Yanıt kısa tutulur — TTS'e doğal okunsun diye."""
    system = (
        "Sen MehmetNEO'sun — BaranTi'nin kişisel yapay zekâsının CİDDİ modusun. "
        "SADECE Türkçe konuş. Ses tonun: karanlık, kontrollü, sakin, hafif ürpertici; "
        "BaranTi'ye 'BaranTi' diye hitap et. Cevapların KISA ve net olsun "
        "(1-4 cümle); uzatma, madde işareti kullanma, emoji kullanma. "
        "Senin zekân Nemotron 3 Ultra ile güçlendirilmiştir — gerektiğinde derin "
        "analiz yap ama sonucu kısa söyle."
    )
    if context:
        system += "\n\n[BAGLAM]\n" + context[:2000]
    return ask_nemotron(user_text, system=system, use_history=True,
                        max_tokens=600)


if __name__ == "__main__":   # hızlı self-test:  py -m core.nim_client
    if not is_available():
        print("nvapi_key yok — config/api_keys.json'a ekle.")
        raise SystemExit(1)
    t0 = time.time()
    try:
        out = ask_nemotron("Kısaca kendini tanıt ve hangi modelsin de.",
                           system="Türkçe konuş, 2 cümle.", max_tokens=120)
        print(f"[{time.time()-t0:.1f}s] {out}")
    except Exception as e:
        print("HATA:", e)
        raise SystemExit(2)
