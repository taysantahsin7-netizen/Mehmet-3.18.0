# -*- coding: utf-8 -*-
"""
İZLİYORUM MODU — Mehmet ekranı saniyelik karelerle izler, sistem sesini dinler,
kullanıcı sesinden ayırt eder ve seyrek aralıklarla kısa Türkçe yorumlar yapar.

Mimari:
    • FrameWatcher   : mss ile ~1 fps ekran yakalama (küçük JPEG)
    • SystemAudioVAD : sounddevice WasapiSettings(loopback=True) ile varsayılan
                       hoparlörün çıkışı → 16 kHz mono int16, enerji-VAD;
                       konuşma segmenti bitince Whisper (varsa) transkript üretir
    • WatchParty     : zamanlayıcı — sessiz ekranda yorum üretmez; konuşma/olay
                       sonrası COOLDOWN içinde yorumlar, uzun aralıkla da bakar

Kullanım (main.py):
    party = WatchParty(send_frame=..., send_text=..., say_system=..., ...)
    party.start(); ...; party.stop()
"""
from __future__ import annotations

import base64
import io
import json
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

_WATCH_MEMORY_PATH = Path(__file__).resolve().parent.parent / "config" / "watch_memory.json"


def record_watch_session(summary: str) -> bool:
    """Biten izleme oturumunun özetini uzun süreli hafızaya kaydeder.
    config/watch_memory.json → {"sessions": [...]} (en yeni önce, tavan 120)."""
    if not (summary or "").strip():
        return False
    d: dict = {"sessions": []}
    try:
        d = json.loads(_WATCH_MEMORY_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    sessions = d.get("sessions") or []
    sessions.insert(0, {
        "ts": time.strftime("%Y-%m-%d %H:%M"),
        "text": summary.strip()[:1500],
    })
    d["sessions"] = sessions[:120]
    try:
        _WATCH_MEMORY_PATH.write_text(
            json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
        return True
    except Exception:
        return False


def list_watch_sessions(limit: int = 8) -> list[dict]:
    """Kayıtlı izleme anıları (en yeni önce)."""
    try:
        d = json.loads(_WATCH_MEMORY_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []
    return (d.get("sessions") or [])[: max(1, limit)]


def search_watch_memory(query: str, limit: int = 5) -> list[dict]:
    """'geçen izlediğimiz video neydi' için metin araması."""
    q = (query or "").lower().strip()
    if not q:
        return []
    hits = []
    for s in list_watch_sessions(limit=120):
        if q in (s.get("text", "").lower()):
            hits.append(s)
            if len(hits) >= limit:
                break
    return hits


MAX_SESSION_S = 240.0   # tek oturum tavanı — üzerine çıkınca otomatik kapanır

_RUN_START = time.monotonic()   # süreç başlangıcı — ilk serbest yorum kontrolü bunu temel alır


def _p(msg: str) -> None:
    """Konsol-uyumlu print — Windows cp konsollarında emoji çökmesini engeller."""
    try:
        print(msg)
    except UnicodeEncodeError:
        try:
            out = getattr(sys.stdout, "buffer", None)
            if out is not None:
                out.write((msg + "\n").encode("utf-8", errors="replace"))
            else:
                print(msg.encode("ascii", errors="replace").decode("ascii"))
        except Exception:
            pass

# ── konfigürasyon ────────────────────────────────────────────────────────────
FRAME_INTERVAL_S     = 1.0      # kare yakalama aralığı (~1 fps)
FRAME_MAX_W          = 960      # genişlik sınırı — bant genişliği için
FRAME_JPEG_Q         = 70
LOOPBACK_RATE        = 16000
LOOPBACK_BLOCKSIZE   = 1600     # 100 ms bloklar
RMS_SPEECH_THRESH    = 0.0035   # konuşma eşiği (loopback genlik)
SPEECH_PAD_S         = 0.6      # konuşma başı/sonu dolgu
MAX_SEGMENT_S        = 25.0     # tek segment tavanı
SEGMENT_GAP_S        = 1.2      # segment arası sessizlik — yeni segment sayılır
MIN_SPEECH_S         = 0.4      # daha kısa sesler gürültü sayılır

COMMENT_EVENT_WIN_S  = 10.0     # konuşma/olay sonrası yorum penceresi
COMMENT_MAX_INTERVAL = 150.0    # uzun sessizlikte en çok bu sürede bir kontrol
MIN_COMMENT_GAP_S    = 45.0     # "her saniye yapmasın" — yorumlar arası taban boşluk
COMMENT_COOLDOWN_S   = 6.0      # son yorumdan sonra olay penceresi kapanana dek sessiz

# ── STT (varsa) ──────────────────────────────────────────────────────────────
_STT = None
_STT_TRIED = False


def _get_stt():
    """faster-whisper modelini tembel yükler (yoksa None — transkriptsiz mod)."""
    global _STT, _STT_TRIED
    if _STT_TRIED:
        return _STT
    _STT_TRIED = True
    try:
        from core.stt import WhisperSTT
        _STT = WhisperSTT(model_name="base", language=None)
    except Exception as e:
        print(f"[WatchParty] STT yüklenemedi (transkriptsiz devam): {e}")
        _STT = None
    return _STT


# ═════════════════════════════════════════════════════════════════════════════
@dataclass
class WatchParty:
    """Ekran + sistem sesi izleme oturumu. start()/stop() ile yönetilir."""

    # callback'ler (hepsi opsiyonel)
    send_frame: object = None    # send_frame(b64, mime)   → canlı oturuma görüntü
    send_text:  object = None    # send_text(str)          → canlı oturuma metin
    say_system: object = None    # say_system(str)         → STT benzeri sistem konuşması
    write_log:  object = None    # write_log(str)          → ekrana
    get_url:    object = None    # get_url() -> str        → aktif sekme adresi
    state_cb:   object = None    # state_cb(bool)          → rozet (her thread'den)
    send_preview: object = None  # send_preview(png_bytes) → telefona canlı kare
    on_session_end: object = None  # on_session_end(summary, stats) — stop sonrası

    # ── çalışma zamanı ──
    _active:   bool = field(default=False, init=False)
    _thread:   object = field(default=None, init=False)
    _lock:     object = field(default_factory=threading.Lock, init=False)

    # ses durumu
    _seg_buf:      list = field(default_factory=list, init=False)
    _seg_open:     bool = field(default=False, init=False)
    _last_voice_t: float = field(default=0.0, init=False)
    _seg_start_t:  float = field(default=0.0, init=False)
    _pending_seg:  list = field(default_factory=list, init=False)  # seri işleme kuyruğu
    _seg_worker:   object = field(default=None, init=False)

    # yakın sistem konuşmaları — yorum bağlamında kullanılır (oturuma METİN olarak
    # ASLA gönderilmez; bu sayede video diyalogları kullanıcı sözü gibi sayılmaz)
    _recent_speech: deque = field(default_factory=lambda: deque(maxlen=5), init=False)
    _recent_speech_t: float = field(default=0.0, init=False)

    # kare durumu
    _frame_b64:  str = field(default="", init=False)
    _frame_mime: str = field(default="", init=False)
    _frame_seq:  int = field(default=0, init=False)
    _last_preview_t: float = field(default=0.0, init=False)

    # olay / zamanlama
    _last_event_t:   float = field(default=0.0, init=False)
    _last_comment_t: float = field(default=0.0, init=False)
    _mute_mic_until: float = field(default=0.0, init=False)
    _mic_paused:     bool = field(default=False, init=False)
    _mic_pause_lock: object = field(default_factory=threading.Lock, init=False)

    # ── oturum hafızası (özet için) ──
    _sess_start:  float = field(default=0.0, init=False)      # monotonic başlangıç
    _sess_url:    str   = field(default="", init=False)       # en sık görülen adres
    _sess_frames: int   = field(default=0, init=False)        # yakalanan kare sayısı
    _sess_speech: list  = field(default_factory=list, init=False)  # sistem konuşmaları
    _sess_subs:   list  = field(default_factory=list, init=False)  # görünür yazı örneği
    _sess_sampled: int  = field(default=0, init=False)        # örnekleme sayacı

    # ── yaşam döngüsü ────────────────────────────────────────────────────────
    def start(self) -> bool:
        with self._lock:
            if self._active:
                return True
            self._active = True
        self._thread = threading.Thread(target=self._run, daemon=True, name="WatchParty")
        self._thread.start()
        self._notify(True)
        with self._lock:
            self._sess_start  = time.monotonic()
            self._sess_frames = 0
            self._sess_speech = []
            self._sess_subs   = []
            self._sess_sampled = 0
            self._sess_url    = self._current_url()
        self._log("SYS: 📺 İzleme modu AÇIK — ekranı ve sistem sesini dinliyorum.")
        return True

    def stop(self) -> None:
        with self._lock:
            if not self._active:
                return
            self._active = False
        t = self._thread
        if t and t is not threading.current_thread():
            t.join(timeout=6)
        self._thread = None
        self._seg_buf.clear()
        self._pending_seg.clear()
        stats, summary = self._finalize_summary()
        self._notify(False)
        self._log("SYS: 📺 İzleme modu KAPALI — normal moda döndüm.")
        if summary:
            # arka planda: özeti kalıcı hafızaya yaz + main.py'ye haber ver (sesle)
            threading.Thread(target=self._record_summary, daemon=True,
                             args=(summary, stats), name="WatchParty-Memory").start()
        elif stats:
            self._log("SYS: İzleme oturumu çok kısaydı — hafızaya kaydedilmedi.")

    @property
    def active(self) -> bool:
        with self._lock:
            return self._active

    def _notify(self, on: bool) -> None:
        cb = self.state_cb
        if cb:
            try:
                cb(on)
            except Exception:
                pass

    def _log(self, msg: str) -> None:
        cb = self.write_log
        if cb:
            try:
                cb(msg)
            except Exception:
                pass

    # ── mikrofon bastırma (user'ın gerçek konuşması ezilmesin) ───────────────
    def pause_mic(self, seconds: float) -> None:
        """Sistem sesi Mehmet'e iletilirken PC mikrofonunu kısa süre susturur."""
        with self._mic_pause_lock:
            self._mute_mic_until = max(self._mute_mic_until, time.monotonic() + seconds)
            self._mic_paused = True

    def mic_allowed(self) -> bool:
        """main.py mikrofon callback'i: şu an sistem sesi iletiliyor mu?"""
        with self._mic_pause_lock:
            if not self._mic_paused:
                return True
            if time.monotonic() >= self._mute_mic_until:
                self._mic_paused = False
                return True
            return False

    # ── ana döngü ────────────────────────────────────────────────────────────
    def _run(self) -> None:
        try:
            import mss
        except Exception as e:
            self._log("SYS: İzleme modu başlatılamadı — 'mss' eksik.")
            print(f"[WatchParty] mss yok: {e}")
            self._notify(False)
            return

        audio = None
        try:
            audio = _LoopbackVAD(self)
        except Exception as e:
            print(f"[WatchParty] Sistem sesi açılamadı (sadece görüntü): {e}")

        frame_t  = 0.0
        next_comment_check = time.monotonic() + 25.0   # açılışta 25 sn sessizlik

        while True:
            with self._lock:
                if not self._active:
                    break
            now = time.monotonic()

            # ── kare yakalama (~1 fps) ──
            if now - frame_t >= FRAME_INTERVAL_S:
                frame_t = now
                try:
                    self._capture_frame(mss)
                except Exception as e:
                    print(f"[WatchParty] kare hatası: {e}")

            # ── segment işleyiciyi çalıştır (boşsa) ──
            self._pump_segment_worker()

            # ── yorum zamanlaması ──
            if self._should_comment(now, periodic_due=(now >= next_comment_check)):
                next_comment_check = now + COMMENT_MAX_INTERVAL
                self._trigger_comment(now)

            time.sleep(0.05)

        # temizlik
        if audio:
            audio.close()
        self._notify(False)

    # ── kare yakalama ────────────────────────────────────────────────────────
    def _capture_frame(self, mss) -> None:
        with mss.mss() as sct:
            monitors = sct.monitors          # [0] = tümü, [1..] = gerçek ekranlar
            target   = monitors[1] if len(monitors) > 1 else monitors[0]
            shot     = sct.grab(target)
            png      = mss.tools.to_png(shot.rgb, shot.size)

        # küçült + JPEG'e çevir (PIL varsa)
        data, mime = png, "image/png"
        try:
            from PIL import Image
            img = Image.open(io.BytesIO(png)).convert("RGB")
            if img.width > FRAME_MAX_W:
                h = int(img.height * FRAME_MAX_W / img.width)
                img = img.resize((FRAME_MAX_W, h), Image.BILINEAR)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=FRAME_JPEG_Q)
            data, mime = buf.getvalue(), "image/jpeg"
        except Exception:
            pass

        with self._lock:
            self._frame_b64  = base64.b64encode(data).decode("ascii")
            self._frame_mime = mime
            self._frame_seq += 1
            self._sess_frames += 1
            # başlıklar/adresler değişmiştir — periyodik güncelle
            self._sess_url = self._current_url() or self._sess_url
            # görünür yazı örneği (her 6. kare = ~6 sn'de bir) — özet bağlamı
            self._sess_sampled += 1
            if self._sess_sampled % 6 == 0:
                self._sess_subs = self._sample_visible_text()
            # telefona düşük fps önizleme (2 sn'de bir)
            self._maybe_send_preview(data)

    # ── yorum zamanlayıcı ────────────────────────────────────────────────────
    def _should_comment(self, now: float, periodic_due: bool) -> bool:
        with self._lock:
            # olay penceresi: yakın zamanda konuşma/olay oldu mu?
            event = (now - self._last_event_t) <= COMMENT_EVENT_WIN_S
            # yorum ZAMANLAYICISI: son olay/yorumdan bu kadar süre geçtiyse serbest kontrol
            periodic = (now - max(self._last_event_t, self._last_comment_t,
                                  _RUN_START)) >= COMMENT_MAX_INTERVAL
            due = periodic or event
            if not due:
                return False
            # son yoruma göre cooldown — "bokunu çıkarmadan"
            if now - self._last_comment_t < COMMENT_COOLDOWN_S:
                return False
            # yorumlar arası minimum boşluk
            if event and now - self._last_comment_t < MIN_COMMENT_GAP_S:
                return False
            return True

    def _trigger_comment(self, now: float) -> None:
        # yorum anındaki kareyi + bağlamı canlı oturuma gönder
        with self._lock:
            b64, mime = self._frame_b64, self._frame_mime
            self._last_comment_t = now
        if not b64:
            return
        ctx = self._context_text()
        parts = [{"inline_data": {"mime_type": mime, "data": b64}}]
        if ctx:
            parts.append({"text": ctx})
        if self.send_frame:
            try:
                self.send_frame(parts)
            except Exception as e:
                print(f"[WatchParty] kare gönderilemedi: {e}")

    # ── telefon önizleme ─────────────────────────────────────────────────────
    def _maybe_send_preview(self, jpeg: bytes) -> None:
        """Kareleri telefona 2 sn'de bir yayınla (dashboard broadcast)."""
        cb = self.send_preview
        if cb is None:
            return
        now = time.monotonic()
        if now - self._last_preview_t < 2.0:
            return
        self._last_preview_t = now
        try:
            cb(jpeg)
        except Exception:
            pass

    # ── oturum özeti ("şimdiye kadar izlediklerimizi özetle") ────────────────
    def _current_url(self) -> str:
        cb = self.get_url
        if cb:
            try:
                return (cb() or "").strip()
            except Exception:
                pass
        return ""

    def _sample_visible_text(self) -> list:
        """Yakın zamandaki sistem konuşmaları + ekran adresi — hafif bağlam.
        (OCR ağır; özet için konuşma transkriptleri asıl kanıttır.)"""
        bits = list(self._recent_speech)
        url = self._current_url()
        if url:
            bits.append(f"ekran: {url}")
        return bits

    def summary_request(self) -> dict:
        """Ana oturumdan canlı özet ister. (Thread-safe; tool thread'inden çağrılır.)
        Dönüş: {"ok", "error?", "summary?", "stats": {...}}"""
        if not self.active:
            return {"ok": False, "error": "izleme modu kapalı"}
        with self._lock:
            dur   = time.monotonic() - self._sess_start if self._sess_start else 0.0
            if dur < 20.0:
                return {"ok": False,
                        "error": f"oturum henüz çok kısa ({int(dur)} sn) — "
                                 "20 sn sonra tekrar sor"}
            ctx = self._summary_context()
            self._last_comment_t = time.monotonic()   # özet = bir yorum; cooldown işlesin
        cb = self.send_frame
        if cb is None:
            return {"ok": False, "error": "canlı oturum yok"}
        b64, mime = self._frame_b64, self._frame_mime
        if not b64:
            return {"ok": False, "error": "henüz kare yok"}
        try:
            parts = [
                {"inline_data": {"mime_type": mime, "data": b64}},
                {"text": ctx},
            ]
            cb(parts)
        except Exception as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True, "stats": {
            "duration_s": int(dur), "frames": self._sess_frames,
            "speech_count": len(self._sess_speech)}}

    def _summary_context(self) -> str:
        url = self._current_url()
        with self._lock:
            dur = int(time.monotonic() - self._sess_start) if self._sess_start else 0
            speech = list(self._sess_speech)[-8:]
            subs   = list(self._sess_subs)[-6:]
        mins, secs = divmod(dur, 60)
        bits = [
            "[İZLEME ÖZETİ İSTEĞİ] Şu ana kadar birlikte izlediklerimizi özetle.",
            f"Oturum süresi: {mins} dk {secs} sn ({self._sess_frames} kare gördün).",
        ]
        if url:
            bits.append(f"Şu anki ekran: {url}")
        if speech:
            bits.append("Oturum boyunca duyduğun SİSTEM sesleri (kullanıcının sözü değil):")
            bits.extend(f"  • {s[:120]}" for s in speech)
        if subs:
            bits.append("Periyodik ekran bağlamı: " + " | ".join(subs))
        bits.append(
            "BaranTi'ye Türkçe, samimi ama kısa konuş (3-5 cümle): ne izledik, "
            "ne konuşuldu, öne çıkan anlar — kendi yorumunu da esprili kat.")
        return "\n".join(bits)

    # ── bağlam metni ─────────────────────────────────────────────────────────
    def _finalize_summary(self) -> tuple:
        """Oturum istatistikleri + yerel LLM ile kısa özet (stop anında, son kareyle).
        Dönüş: ({stats}, summary|"")."""
        with self._lock:
            dur = time.monotonic() - self._sess_start if self._sess_start else 0.0
            if dur < 30.0:
                return {}, ""
            stats = {
                "duration_s": int(dur),
                "frames": self._sess_frames,
                "speech_count": len(self._sess_speech),
                "url": self._sess_url or self._current_url(),
            }
            speech = list(self._sess_speech)[-12:]
            b64, mime = self._frame_b64, self._frame_mime
        img_bytes = b""
        if b64:
            try:
                img_bytes = base64.b64decode(b64)
            except Exception:
                img_bytes = b""
        mins, secs = divmod(stats["duration_s"], 60)
        lines = [
            "Aşağıda bir yapay zekâ asistanının kullanıcısıyla birlikte ekranda izlediği oturumun verileri var.",
            f"Süre: {mins} dk {secs} sn; {stats['frames']} kare; "
            f"{stats['speech_count']} sistem konuşması algılandı.",
        ]
        if stats.get("url"):
            lines.append(f"Ekran/sekme: {stats['url']}")
        if speech:
            lines.append("Oturumda duyulan sistem sesleri (video/dizi konuşmaları — kullanıcının sözü değil):")
            lines.extend(f"  • {s}" for s in speech)
        lines.append("Bunları 4-6 cümlelik SAMİMİ bir Türkçe özete çevir: ne izlendi, "
                     "ne konuşuldu, öne çıkan anlar.")
        prompt = "\n".join(lines)
        summary = ""
        try:
            if img_bytes:
                summary = _vision_summarize(prompt, img_bytes, mime or "image/jpeg")
            else:
                from core.llm_client import call_llm_text
                summary = (call_llm_text(prompt) or "").strip()
        except Exception as e:
            print(f"[WatchParty] özet hatası: {e}")
        return stats, summary

    def _record_summary(self, summary: str, stats: dict) -> None:
        """Worker thread: özeti config/watch_memory.json'a yaz + main.py'ye haber ver."""
        ok = False
        try:
            ok = record_watch_session(summary)
        except Exception as e:
            print(f"[WatchParty] hafıza hatası: {e}")
        self._log("SYS: 🧠 İzleme oturumu hafızaya kaydedildi." if ok
                  else "SYS: İzleme özeti kaydedilemedi.")
        cb = self.on_session_end
        if cb:
            try:
                cb(summary, stats)
            except Exception:
                pass

    def _context_text(self) -> str:
        url = ""
        if self.get_url:
            try:
                url = (self.get_url() or "").strip()
            except Exception:
                url = ""
        bits = ["[İZLEME MODU] Şu an kullanıcının ekranını canlı izliyorsun."]
        if url:
            bits.append(f"Aktif sekme: {url}")
        with self._lock:
            fresh = (time.monotonic() - self._recent_speech_t) < 120
            speech = list(self._recent_speech) if fresh else []
        if speech:
            bits.append("Son duyduğun SİSTEM sesleri (kullanıcının sözü değil, video/dizi sesi):")
            bits.extend(f"  • {s}" for s in speech[-3:])
        bits.append(
            "Videoda/dialogda yeni bir şey olduysa TEK cümlelik, esprili ama kısa "
            "bir yorum yap (maks ~20 kelime). Sıkıcı/tekrar bir şey yoksa SADECE "
            "'...' yaz — sessiz kal."
        )
        return "\n".join(bits)

    # ── sistem sesi segmentleri (LoopbackVAD çağırır) ─────────────────────────
    def push_audio(self, pcm16: np.ndarray) -> None:
        """LoopbackVAD'dan 100 ms'lik int16 bloklar gelir."""
        with self._lock:
            self._seg_buf.append(pcm16.copy())
            if len(self._seg_buf) > 300:      # ~30 sn taşma koruması
                self._seg_buf = self._seg_buf[-300:]

    def _pump_segment_worker(self) -> None:
        # bitmiş segment varsa ve worker boşsa başlat
        if self._seg_worker is not None and self._seg_worker.is_alive():
            return
        with self._lock:
            if not self._pending_seg:
                return
            seg = self._pending_seg.pop(0)
        t = threading.Thread(
            target=self._process_segment, args=(seg,), daemon=True,
            name="WatchParty-Seg")
        t.start()
        self._seg_worker = t

    def _flush_segment(self, force: bool = False) -> None:
        """Birikmiş sesi segment olarak kapatır (VAD'ın bitiş kararı)."""
        with self._lock:
            if not self._seg_buf:
                return
            seg = self._seg_buf[:]
            self._seg_buf.clear()
        self._pending_seg.append(seg)
        self._last_event_t = time.monotonic()

    def mark_event(self) -> None:
        """VAD konuşma başlangıcı — olay işaretle."""
        self._last_event_t = time.monotonic()

    # ── segment işleme (worker thread) ───────────────────────────────────────
    def _process_segment(self, seg: list) -> None:
        try:
            audio = np.concatenate(seg)
            dur = len(audio) / LOOPBACK_RATE
            if dur < MIN_SPEECH_S:
                return
            stt = _get_stt()
            text = ""
            if stt is not None:
                try:
                    audio_f = audio.astype(np.float32) / 32768.0
                    text = (stt.transcribe(audio_f) or "").strip()
                except Exception as e:
                    print(f"[WatchParty] transkript hatası: {e}")
            if not text:
                return
            self._relay_system_speech(text)
        except Exception as e:
            print(f"[WatchParty] segment hatası: {e}")

    def _relay_system_speech(self, text: str) -> None:
        """Sistemden gelen konuşmayı kaydeder. OTURUMA METİN olarak gönderilmez —
        yalnızca yorum bağlamında ([SİSTEM SESİ]) referans verilebilir."""
        self._log(f"🎬 Sistem: {text}")
        with self._lock:
            self._recent_speech.append(text)
            self._recent_speech_t = time.monotonic()
            # oturum hafızası — özet ve kayıt için (tavan 60)
            self._sess_speech.append(text[:200])
            if len(self._sess_speech) > 60:
                self._sess_speech.pop(0)


# ═══════════════════════════════════════════════════ party çalışanı ═════════
class _LoopbackVAD:
    """WASAPI loopback: hoparlör çıkışını kaydeder, enerji-VAD uygular.

    ``soundcard`` kütüphanesi ile çalışır (Windows'ta WASAPI loopback).
    Kendi thread'inde blok blok kaydeder; native örnek hızından 16 kHz'e
    ``np.interp`` ile çevirir. Kurulum/ses hatasında video-only moda düşer.
    """

    def __init__(self, party: WatchParty):
        import soundcard as sc
        self._sc    = sc
        self._party = party
        self._stop  = threading.Event()
        # kayıt uyarılarını sustur (data discontinuity — zararsız)
        try:
            import warnings as _warnings
            _warnings.filterwarnings("ignore", category=sc.SoundcardRuntimeWarning)
        except Exception:
            pass

        spk = sc.default_speaker()
        self._mic = sc.get_microphone(id=str(spk.name), include_loopback=True)
        self._ch  = max(1, min(int(getattr(spk, "channels", None) or 2), 2))
        self._native_rate = int(getattr(spk, "samplerate", None) or 48000)
        self._native_block = max(64, int(LOOPBACK_BLOCKSIZE * self._native_rate
                                        / LOOPBACK_RATE))

        _p("[WatchParty] Sistem sesi (loopback) dinleniyor")
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="WatchParty-Loopback")
        self._thread.start()

    def _loop(self) -> None:
        first_err = True
        try:
            with self._mic.recorder(samplerate=self._native_rate,
                                    channels=self._ch) as rec:
                while not self._stop.is_set():
                    try:
                        data = rec.record(numframes=self._native_block)
                    except Exception as e:
                        if first_err:
                            first_err = False
                            _p(f"[WatchParty] loopback kayıt hatası: {e}")
                            self._party._log(
                                "SYS: Sistem sesi yakalanamadı — sadece görüntü modu.")
                        time.sleep(0.5)
                        continue
                    if data is None or len(data) == 0:
                        continue
                    mono_f = np.asarray(data, dtype=np.float64).mean(axis=1)
                    # native → 16 kHz (np.interp, blok başına)
                    if len(mono_f) != LOOPBACK_BLOCKSIZE:
                        idx = np.linspace(0.0, len(mono_f) - 1.0, LOOPBACK_BLOCKSIZE)
                        mono_f = np.interp(idx, np.arange(len(mono_f)), mono_f)
                    pcm = (np.clip(mono_f, -1.0, 1.0) * 32767.0).astype(np.int16)
                    self._process_block(pcm)
        except Exception as e:
            if not self._stop.is_set():
                _p(f"[WatchParty] loopback hatası: {e}")
                self._party._log(
                    "SYS: Sistem sesi yakalanamadı — sadece görüntü modu.")

    def _process_block(self, pcm: np.ndarray) -> None:
        """100 ms'lik 16 kHz int16 blok: enerji-VAD + segment yönetimi."""
        party = self._party
        rms = float(np.sqrt(np.mean(pcm.astype(np.float64) ** 2))) if len(pcm) else 0.0
        now = time.monotonic()
        if rms >= RMS_SPEECH_THRESH:
            party.mark_event()
            if not party._seg_open:
                party._seg_open = True
                party._seg_start_t = now
            party._last_voice_t = now
            party.push_audio(pcm)
        else:
            if party._seg_open:
                # sessiz kuyruk örnekleri de sakla (doğal bitiş yakalansın)
                party.push_audio(pcm)
                if (now - party._last_voice_t >= SPEECH_PAD_S
                        or now - party._seg_start_t > MAX_SEGMENT_S):
                    party._seg_open = False
                    party._flush_segment()

    def close(self) -> None:
        self._stop.set()
        t = self._thread
        if t and t is not threading.current_thread():
            t.join(timeout=3)


# ── yardımcı: son kareyi vision LLM'ine verip özet üret ──────────────────────
def _vision_summarize(prompt: str, img_bytes: bytes, mime: str) -> str:
    """Gemini Flash'a (görüntü + metin) tek seferlik özet sorusu."""
    from google import genai
    from google.genai import types as _t
    cfg = {}
    try:
        cfg = json.loads(_WATCH_MEMORY_PATH.with_name("api_keys.json")
                         .read_text(encoding="utf-8"))
    except Exception:
        pass
    client = genai.Client(api_key=cfg.get("gemini_api_key"))
    resp = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=[
            _t.Part.from_bytes(data=img_bytes, mime_type=mime or "image/jpeg"),
            prompt,
        ],
    )
    return (resp.text or "").strip()


# ── yardımcı: cv2 olmadan JPEG piksellerini Qt pixmap'e çevirmek gerekmez —
#    rozet metin tabanlı; kareler canlı oturuma gider, UI'ya basılmaz.
