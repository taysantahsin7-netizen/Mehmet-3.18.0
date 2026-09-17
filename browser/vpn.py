# -*- coding: utf-8 -*-
"""
NAVİGATÖR VPN — tek tıkla aç/kapa (Chrome eklentisi gibi).

MİMARİ NOT:
    QtWebEngine (Chromium) çalışma zamanında proxy değiştirmeyi
    desteklemez — QNetworkProxy.setApplicationProxy() etkisizdir ve
    --proxy-server yalnızca başlangıçta okunur (empirik olarak test edildi).
    Bu yüzden trafik HER ZAMAN yerel bir aktarma katmanından geçirilir:

        Chromium ──► 127.0.0.1:<port>  (yerel HTTP CONNECT proxy)
                          │
                          ├─ VPN KAPALI → doğrudan hedefe bağlan
                          └─ VPN AÇIK   → seçili SOCKS5/HTTP uç noktasına tünel

    Tek tık = aktarma katmanının hedefini değiştirmek. Sıfır yeniden
    başlatma, anında geçiş. Tünel kurulumu başarısız olursa bağlantı
    düşer — asla doğrudana sızdırılmaz (kill switch davranışı).

Kullanıcı profilleri: SOCKS5 / HTTP uç noktaları (host, port, kullanıcı adı,
şifre). Kalıcılık: config/vpn.json
"""
from __future__ import annotations

import base64
import json
import os
import re
import select
import socket
import threading
from urllib.parse import urlparse
import time
from dataclasses import dataclass, asdict
from pathlib import Path

PROTOCOLS = ("socks5", "http")

# Aktarma katmanının dinleyeceği bağlantı noktaları (ilki doluysa sıradaki)
RELAY_PORTS = (18899, 18901, 18903, 18907, 18909)

# Hazır bölge ön ayarları: tek tıkla profil oluşturur (host, VPN sağlayıcısından)
REGION_PRESETS = [
    ("Almanya",   "DE", 1080),
    ("Hollanda",  "NL", 1080),
    ("Amerika",   "US", 8080),
    ("İngiltere", "UK", 1080),
    ("Fransa",    "FR", 1080),
    ("Japonya",   "JP", 1080),
    ("Kanada",    "CA", 1080),
    ("Türkiye",   "TR", 1080),
]

# Hazır hızlı-profil iskeletleri (kullanıcı host:port doldurur)
DEFAULT_PROFILES = [
    {
        "id": "eu-socks",
        "name": "Avrupa SOCKS",
        "region": "EU",
        "protocol": "socks5",
        "host": "",
        "port": 1080,
        "username": "",
        "password": "",
    },
    {
        "id": "us-http",
        "name": "Amerika HTTP",
        "region": "US",
        "protocol": "http",
        "host": "",
        "port": 8080,
        "username": "",
        "password": "",
    },
]


# ── yerel aktarma katmanı ────────────────────────────────────────────────────

@dataclass
class _Upstream:
    protocol: str
    host: str
    port: int
    username: str = ""
    password: str = ""


_HEAD_RE = re.compile(rb"^[A-Z]+ [^ ]+ HTTP/[0-9.]+\r?\n")
_AUTH_RE = re.compile(rb"^HTTP/1\.[01] (\d{3})")


class VpnRelay:
    """Yerel HTTP CONNECT aktarma proxy'si (saf Python; Qt bağımsız).

    Chromium başlangıçta hep bu adrese bağlanır; VPN açık/kapalı anında
    ``set_upstream``/``set_direct`` ile değiştirilir.
    """

    def __init__(self):
        self._srv: socket.socket | None = None
        self._port: int = 0
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._upstream: _Upstream | None = None
        self.stats = {
            "conn": 0, "active": 0, "errors": 0,
            "sent": 0, "recv": 0, "started": 0.0,
        }

    # ── yaşam döngüsü ────────────────────────────────────────────────────────
    @property
    def running(self) -> bool:
        return self._srv is not None

    @property
    def port(self) -> int:
        return self._port

    def start(self) -> bool:
        """Dinlemeye başla. Zaten çalışıyorsa True."""
        if self.running:
            return True
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        for port in RELAY_PORTS:
            try:
                srv.bind(("127.0.0.1", port))
                self._port = port
                break
            except OSError:
                continue
        else:
            try:
                srv.close()
            except Exception:
                pass
            return False
        srv.listen(64)
        srv.settimeout(1.0)
        self._srv = srv
        self.stats["started"] = time.time()
        self._thread = threading.Thread(
            target=self._accept_loop, daemon=True, name="NavVpnRelay")
        self._thread.start()
        return True

    def stop(self) -> None:
        srv, self._srv = self._srv, None
        if srv is not None:
            try:
                srv.close()
            except Exception:
                pass

    # ── yönlendirme hedefi ───────────────────────────────────────────────────
    @property
    def upstream_active(self) -> bool:
        with self._lock:
            return self._upstream is not None

    def upstream_label(self) -> str:
        with self._lock:
            u = self._upstream
        if u is None:
            return ""
        return f"{u.protocol}://{u.host}:{u.port}"

    def set_direct(self) -> None:
        """VPN KAPALI: tüm trafik doğrudan."""
        with self._lock:
            self._upstream = None

    def set_upstream(self, protocol: str, host: str, port: int,
                     username: str = "", password: str = "") -> bool:
        """VPN AÇIK: tüm trafik verilen uç noktadan tünelenir."""
        protocol = (protocol or "socks5").lower()
        if protocol not in PROTOCOLS or not host or int(port or 0) <= 0:
            return False
        with self._lock:
            self._upstream = _Upstream(protocol, host, int(port),
                                       username or "", password or "")
        return True

    # ── kabul döngüsü ────────────────────────────────────────────────────────
    def _accept_loop(self) -> None:
        while self._srv is not None:
            try:
                conn, _addr = self._srv.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            t = threading.Thread(target=self._handle, args=(conn,),
                                 daemon=True)
            t.start()

    # ── bağlantı işleyici ────────────────────────────────────────────────────
    def _handle(self, conn: socket.socket) -> None:
        self.stats["conn"] += 1
        self.stats["active"] += 1
        try:
            self._serve(conn)
        except Exception:
            self.stats["errors"] += 1
        finally:
            try:
                conn.close()
            except Exception:
                pass
            self.stats["active"] -= 1

    def _serve(self, conn: socket.socket) -> None:
        conn.settimeout(20)
        head = self._recv_head(conn)
        if not head or b"\r\n" not in head:
            return
        first = head.split(b"\r\n", 1)[0]
        parts = first.split(b" ")
        if len(parts) < 3:
            return
        method, target = parts[0].upper(), parts[1]

        if method == b"CONNECT":
            host, port = self._split_authority(target)
            if not host:
                return
            remote = self._tunnel_socket(host, port)
            if remote is None:
                # Tünel kurulamadı → KILL SWITCH: doğrudana düşme, kes.
                return
            conn.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
            self._pump(conn, remote)
            return

        # Düz HTTP (CONNECT değil): mutlak URI'yi origin-form'a çevir
        if not target.startswith(b"http://"):
            return
        rest = target[len(b"http://"):]
        slash = rest.find(b"/")
        if slash < 0:
            return
        authority, path = rest[:slash], rest[slash:]
        host, port = self._split_authority(authority)
        if not host:
            return
        remote = self._tunnel_socket(host, port)
        if remote is None:
            return
        # istek satırını hedef biçimine indir (GET http://h/p → GET /p)
        new_first = b" ".join((parts[0], path, parts[2]))
        body = head.split(b"\r\n", 1)[1]
        remote.sendall(new_first + b"\r\n" + body)
        self._pump(conn, remote)

    @staticmethod
    def _recv_head(conn: socket.socket) -> bytes:
        buf = b""
        while b"\r\n\r\n" not in buf and len(buf) < 32768:
            try:
                chunk = conn.recv(8192)
            except (socket.timeout, OSError):
                break
            if not chunk:
                break
            buf += chunk
        return buf

    @staticmethod
    def _split_authority(target: bytes) -> tuple[str, int]:
        auth = target.decode("latin-1", "replace")
        if ":" in auth:
            host, _, p = auth.rpartition(":")
            try:
                return host.strip("[]"), int(p)
            except ValueError:
                return "", 0
        return auth, 443

    # ── tünel kurulumu ───────────────────────────────────────────────────────
    def _tunnel_socket(self, host: str, port: int) -> socket.socket | None:
        with self._lock:
            u = self._upstream
        if u is None:
            return self._dial_direct(host, port)
        try:
            if u.protocol == "socks5":
                return self._dial_socks5(u, host, port)
            return self._dial_http(u, host, port)
        except Exception:
            return None

    @staticmethod
    def _dial_direct(host: str, port: int) -> socket.socket | None:
        try:
            s = socket.create_connection((host, port), timeout=15)
            s.settimeout(None)
            return s
        except OSError:
            return None

    @staticmethod
    def _dial_socks5(u: _Upstream, host: str, port: int) -> socket.socket:
        s = socket.create_connection((u.host, u.port), timeout=15)
        s.settimeout(15)
        want_auth = 0x02 if u.username else 0x00
        s.sendall(bytes([5, 2, 0x00, want_auth] if u.username
                        else [5, 1, 0x00]))
        resp = s.recv(2)
        if len(resp) < 2 or resp[0] != 5:
            s.close()
            raise OSError("SOCKS5 el sıkışması başarısız")
        if resp[1] == 0x02:
            ub = u.username.encode()
            pb = u.password.encode()
            s.sendall(bytes([1, len(ub)]) + ub + bytes([len(pb)]) + pb)
            ar = s.recv(2)
            if len(ar) < 2 or ar[1] != 0x00:
                s.close()
                raise OSError("SOCKS5 kimlik doğrulaması reddedildi")
        elif resp[1] != 0x00:
            s.close()
            raise OSError("SOCKS5 yöntem reddi")
        hb = host.encode("idna") if any(ord(c) > 127 for c in host) \
            else host.encode()
        req = bytes([5, 1, 0, 3, len(hb)]) + hb + int(port).to_bytes(2, "big")
        s.sendall(req)
        rr = s.recv(4)
        if len(rr) < 4 or rr[1] != 0:
            s.close()
            raise OSError(f"SOCKS5 bağlantı hatası {rr[1:]}")
        atyp = rr[3]
        if atyp == 1:
            s.recv(4 + 2)
        elif atyp == 3:
            ln = s.recv(1)[0]
            s.recv(ln + 2)
        elif atyp == 4:
            s.recv(16 + 2)
        s.settimeout(None)
        return s

    @staticmethod
    def _dial_http(u: _Upstream, host: str, port: int) -> socket.socket:
        s = socket.create_connection((u.host, u.port), timeout=15)
        s.settimeout(15)
        req = (f"CONNECT {host}:{port} HTTP/1.1\r\n"
               f"Host: {host}:{port}\r\n")
        if u.username:
            tok = base64.b64encode(
                f"{u.username}:{u.password}".encode()).decode()
            req += f"Proxy-Authorization: Basic {tok}\r\n"
        req += "Proxy-Connection: keep-alive\r\n\r\n"
        s.sendall(req.encode())
        buf = b""
        while b"\r\n\r\n" not in buf and len(buf) < 8192:
            chunk = s.recv(4096)
            if not chunk:
                break
            buf += chunk
        m = _AUTH_RE.match(buf)
        if not m or not (200 <= int(m.group(1)) < 300):
            s.close()
            raise OSError("HTTP proxy CONNECT reddedildi")
        s.settimeout(None)
        return s

    # ── çift yönlü veri pompası ──────────────────────────────────────────────
    def _pump(self, a: socket.socket, b: socket.socket) -> None:
        socks = (a, b)
        a.setblocking(False)
        b.setblocking(False)
        try:
            while True:
                r, _, _ = select.select(socks, (), (), 60)
                if not r:
                    continue
                for src in r:
                    dst = b if src is a else a
                    try:
                        data = src.recv(65536)
                    except (BlockingIOError, InterruptedError):
                        continue
                    except OSError:
                        return
                    if not data:
                        return
                    key = "sent" if src is a else "recv"
                    self.stats[key] += len(data)
                    try:
                        dst.sendall(data)
                    except OSError:
                        return
        finally:
            for s in socks:
                try:
                    s.close()
                except Exception:
                    pass


# Modül düzeyi tekil — Chromium flag'leri ile panel aynı örneği kullanır
GLOBAL_RELAY = VpnRelay()


def ensure_relay_flags() -> bool:
    """Aktarmayı başlatır ve QTWEBENGINE_CHROMIUM_FLAGS'e proxy'yi ekler.

    QtWebEngine profili yaratılmadan ÖNCE çağrılmalı (ui.py erken blok).
    """
    if not GLOBAL_RELAY.start():
        return False
    flag = f"--proxy-server=http://127.0.0.1:{GLOBAL_RELAY.port}"
    bypass = "--proxy-bypass-list=<-loopback>"
    env = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "")
    if "--proxy-server=" not in env:
        env = (env + " " + flag + " " + bypass).strip()
        os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = env
    return True


# ── profil yönetimi (kalıcı yapılandırma) ────────────────────────────────────

@dataclass
class VpnStats:
    connected_since: float = 0.0
    requests_routed: int = 0
    last_proxy: str = ""

    @property
    def uptime_seconds(self) -> int:
        if not self.connected_since:
            return 0
        return int(time.time() - self.connected_since)


class VpnManager:
    """Profilleri yönetir; aç/kapa komutlarını aktarma katmanına iletir."""

    def __init__(self, config_path: Path | None = None,
                 relay: VpnRelay | None = None):
        self._path = Path(config_path) if config_path else None
        self.relay = relay or GLOBAL_RELAY
        self.profiles: list[dict] = []
        self.active_id: str = ""          # VPN açıkken seçili profil
        self.last_profile_id: str = ""    # kapatıldığında hatırlanır
        self.kill_switch: bool = True     # sızıntı koruması (her zaman uygulanır)
        self.enabled: bool = False
        self.stats = VpnStats()
        self.load()
        self.relay.start()
        self._apply_relay_state()

    # ── kalıcılık ────────────────────────────────────────────────────────────
    def load(self) -> None:
        data: dict = {}
        if self._path and self._path.exists():
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
            except Exception:
                data = {}
        self.profiles = data.get("profiles") or [dict(p) for p in DEFAULT_PROFILES]
        self.enabled = bool(data.get("enabled", False))
        self.last_profile_id = data.get("last_profile_id", "")
        self.kill_switch = bool(data.get("kill_switch", True))
        # eski sürüm göçü: active_id doluysa VPN açıktı
        legacy = data.get("active_id", "")
        if legacy and not self.last_profile_id:
            self.last_profile_id = legacy
            self.enabled = True
        if self.last_profile_id and not self._find(self.last_profile_id):
            self.last_profile_id = ""
        if self.enabled and not self.last_profile_id:
            self.enabled = False

    def save(self) -> None:
        if not self._path:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "profiles": self.profiles,
            "enabled": self.enabled,
            "last_profile_id": self.last_profile_id,
            "kill_switch": self.kill_switch,
        }
        self._path.write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                              encoding="utf-8")

    def _apply_relay_state(self) -> None:
        """Yükleme sonrası aktarmayı yapılandırılan duruma getir."""
        if self.enabled:
            p = self._find(self.last_profile_id)
            if p and VpnManager.is_profile_complete(p):
                ok = self.relay.set_upstream(
                    p.get("protocol", "socks5"), p.get("host", ""),
                    int(p.get("port") or 0), p.get("username", ""),
                    p.get("password", ""))
                if ok:
                    self.active_id = p["id"]
                    self.stats.connected_since = time.time()
                    return
        self.relay.set_direct()
        self.active_id = ""
        self.enabled = False

    # ── profil yardımcıları ──────────────────────────────────────────────────
    def _find(self, profile_id: str) -> dict | None:
        for p in self.profiles:
            if p.get("id") == profile_id:
                return p
        return None

    @staticmethod
    def is_profile_complete(p: dict) -> bool:
        return bool(p.get("host")) and int(p.get("port") or 0) > 0

    def active_profile(self) -> dict | None:
        return self._find(self.active_id) if self.active_id else None

    def first_complete_profile(self) -> dict | None:
        for p in self.profiles:
            if VpnManager.is_profile_complete(p):
                return p
        return None

    # ── TEK TIK aç / kapa ────────────────────────────────────────────────────
    def connect(self, profile_id: str) -> tuple[bool, str]:
        """VPN'i AÇAR (verilen profille). Dönüş: (başarılı, mesaj)."""
        p = self._find(profile_id)
        if not p:
            return False, "Profil bulunamadı."
        if not VpnManager.is_profile_complete(p):
            return False, "Profil eksik: host ve port gerekli."
        if not self.relay.running and not self.relay.start():
            return False, "Aktarma katmanı başlatılamadı."
        ok = self.relay.set_upstream(
            p.get("protocol", "socks5"), p.get("host", ""),
            int(p.get("port") or 0), p.get("username", ""),
            p.get("password", ""))
        if not ok:
            return False, "Geçersiz profil verisi."
        self.active_id = profile_id
        self.last_profile_id = profile_id
        self.enabled = True
        self.stats.connected_since = time.time()
        self.stats.last_proxy = self.relay.upstream_label()
        self.save()
        return True, f"VPN açıldı: {p.get('name', profile_id)}"

    def disconnect(self) -> None:
        """VPN'i KAPATIR: trafik doğrudan akar."""
        self.relay.set_direct()
        self.active_id = ""
        self.enabled = False
        self.stats.connected_since = 0.0
        self.stats.last_proxy = ""
        self.save()

    def toggle(self) -> tuple[bool, str]:
        """TEK TIK: kapalıysa son profille açar, açıksa kapatır."""
        if self.is_active:
            self.disconnect()
            return False, "VPN kapatıldı — doğrudan bağlantı."
        target = self.last_profile_id or (
            self.first_complete_profile() or {}).get("id", "")
        if not target:
            return False, ("VPN profili yok — kişiselleştir > VPN "
                           "bölümünden uç nokta ekleyin.")
        return self.connect(target)

    @property
    def is_active(self) -> bool:
        return bool(self.active_id) and self.relay.upstream_active

    def upstream_label(self) -> str:
        """Aktif tünelin etiketi (relay'e delege eder)."""
        return self.relay.upstream_label()

    # ── profil ekle/çıkar ────────────────────────────────────────────────────
    def upsert_profile(self, profile: dict) -> str:
        pid = profile.get("id") or ""
        if not pid:
            pid = f"p{int(time.time() * 1000) % 10_000_000}"
            profile["id"] = pid
            self.profiles.append(profile)
        else:
            p = self._find(pid)
            if p:
                p.update(profile)
            else:
                self.profiles.append(profile)
        # aktif profil düzenlendiyse tüneli tazele
        if self.active_id == pid and self.enabled:
            self._apply_relay_state()
        self.save()
        return pid

    def remove_profile(self, profile_id: str) -> bool:
        before = len(self.profiles)
        self.profiles = [p for p in self.profiles if p.get("id") != profile_id]
        if self.last_profile_id == profile_id:
            self.last_profile_id = ""
        if self.active_id == profile_id:
            self.disconnect()
        else:
            self.save()
        return len(self.profiles) < before

    # ── chromium entegrasyonu ────────────────────────────────────────────────
    def proxy_uri(self) -> str:
        """Aktarma adresi (VPN durumundan bağımsız — hep yerel aktarma)."""
        if not self.relay.running:
            return ""
        return f"http://127.0.0.1:{self.relay.port}"

    def chromium_args(self) -> list[str]:
        """QtWebEngine'e iletilecek sabit proxy argümanları."""
        uri = self.proxy_uri()
        if not uri:
            return []
        return [f"--proxy-server={uri}",
                "--proxy-bypass-list=<-loopback>"]

    def apply_env(self) -> None:
        """Alt süreçler için ortam değişkenlerini yerel aktarmaya yönelt."""
        uri = self.proxy_uri()
        if uri:
            for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
                os.environ[k] = uri
        else:
            for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
                os.environ.pop(k, None)

    def set_kill_switch(self, on: bool) -> None:
        """Sızıntı koruması tercihi (tünel hatasında doğrudana düşmeme)."""
        self.kill_switch = bool(on)
        self.save()

    # ── raporlama ────────────────────────────────────────────────────────────
    def status_text(self) -> str:
        if not self.is_active:
            return "PASİF — doğrudan bağlantı"
        p = self.active_profile() or {}
        return f"AKTİF — {p.get('name', '?')} ({p.get('region', '?')})"

    def export_profiles(self) -> list[dict]:
        return [asdict(p) if isinstance(p, dict) else dict(p)
                for p in self.profiles]

    # ── otomatik koruma (kritik sitelerde VPN uyarısı) ───────────────────────
    AUTO_PROTECT_DOMAINS = (
        # bankacılık / finans
        "garanti", "isbank", "akbank", "yapikredi", "ziraat", "halkbank",
        "vakifbank", "denizbank", "qnbfinansbank", "kuveytturk",
        "paypal", "wise.com", "revolut", "binance", "coinbase",
        # kamu / resmi
        "e-devlet", "turkiye.gov.tr", "gib.gov.tr", "sgk.gov.tr",
        "ysk.gov.tr", "egm.gov.tr",
        # e-posta / hesap girişleri
        "mail.google.com", "outlook.live.com", "outlook.office.com",
        "login.microsoftonline.com", "accounts.google.com",
    )

    @classmethod
    def is_critical_url(cls, url: str) -> bool:
        """URL, VPN'in açık olması tercih edilen kritik bir alan adındaysa True."""
        try:
            host = urlparse(url).netloc.lower()
        except Exception:
            return False
        if not host:
            return False
        return any(d in host for d in cls.AUTO_PROTECT_DOMAINS)

    def check_protection(self, url: str) -> str | None:
        """Kritik sayfaya girildiğinde VPN kapalıysa Türkçe uyarı döner;
        açıkken None. Panel bu uyarıyı Mehmet'e iletir."""
        if not self.enabled and VpnManager.is_critical_url(url):
            return ("DİKKAT: VPN kapalıyken hassas bir siteye girildi — "
                    "'vpn aç' demen yeterli.")
        return None

    def connect_word(self, word: str) -> tuple[bool, str]:
        """Tek kelimeyle bağlanma: bölge adı/protokol ('eu', 'japonya',
        'en hızlı'...) ya da boş → son/en hazır profil."""
        w = (word or "").strip().lower()
        if not w or w in ("en hızlı", "hizli", "fastest", "en iyi"):
            p, _r = self.auto_select_fastest(4.0)
            if p:
                return True, (f"VPN açıldı: {p.get('name')} "
                              f"({p.get('region')}) — en hızlı profil.")
            return False, "Hiçbir profile ulaşılamadı — host/port kontrol et."
        # bölge kısaltma eşlemesi
        aliases = {
            "tr": ("tr", "türkiye"), "eu": ("eu", "avrupa"),
            "us": ("us", "abd", "amerika"), "uk": ("uk", "ingiltere"),
            "de": ("de", "almanya"), "nl": ("nl", "hollanda"),
            "fr": ("fr", "fransa"), "jp": ("jp", "japonya"),
            "ca": ("ca", "kanada"),
        }
        wanted = None
        for key, names in aliases.items():
            if w in names:
                wanted = key
                break
        cands = [p for p in self.profiles
                 if VpnManager.is_profile_complete(p)
                 and ((wanted is not None
                       and str(p.get("region", "")).lower() == wanted)
                      or w in str(p.get("name", "")).lower()
                      or w in str(p.get("region", "")).lower())]
        if not cands:
            return False, (f"'{word}' ile eşleşen profil yok — "
                           "bölgeleri 'vpn profilleri' ile listeleyebilirsin.")
        ok, msg = self.connect(cands[0]["id"])
        return ok, msg

    # ── bölge ön ayarları ────────────────────────────────────────────────────
    def add_region_presets(self, host: str, protocol: str = "socks5",
                           username: str = "", password: str = "") -> int:
        if not host:
            return 0
        added = 0
        existing = {(p.get("region"), p.get("host")) for p in self.profiles}
        for name, region, port in REGION_PRESETS:
            if (region, host) in existing:
                continue
            self.upsert_profile({
                "id": "", "name": f"{name} ({protocol.upper()})",
                "region": region, "protocol": protocol, "host": host,
                "port": port, "username": username, "password": password,
            })
            added += 1
        return added

    def add_preset_host(self, region: str, host: str, port: int,
                        protocol: str = "socks5",
                        username: str = "", password: str = "") -> str:
        """Tek bir bölge için profil ekler/günceller; profil id döner."""
        name = next((n for n, r, _ in REGION_PRESETS
                     if r.lower() == region.lower()), region.upper())
        return self.upsert_profile({
            "id": "", "name": name, "region": region.upper(),
            "protocol": protocol, "host": host, "port": port,
            "username": username, "password": password,
        })

    # ── hız testi ────────────────────────────────────────────────────────────
    def probe_profile(self, p: dict, timeout: float = 6.0) -> float | None:
        """Profille gerçek HTTPS el sıkışma gecikmesini ölçer (ms).
        Tünel: proxy'ye TCP + CONNECT example.com:443 → süre.
        Başarısız profil → None. (Arka plan thread'inden çağrılmalı.)"""
        if not VpnManager.is_profile_complete(p):
            return None
        t0 = time.perf_counter()
        try:
            if (p.get("protocol") or "socks5") == "socks5":
                u = _Upstream("socks5", p.get("host", ""),
                              int(p.get("port") or 0),
                              p.get("username", ""), p.get("password", ""))
                s = self._dial_socks5(u, "example.com", 443)
            else:
                u = _Upstream("http", p.get("host", ""),
                              int(p.get("port") or 0),
                              p.get("username", ""), p.get("password", ""))
                s = self._dial_http(u, "example.com", 443)
            s.close()
        except Exception:
            return None
        return (time.perf_counter() - t0) * 1000.0

    def speed_test(self, timeout: float = 6.0) -> list[tuple[str, str, float | None]]:
        """Tüm TAM profilleri sırayla ölçer: [(ad, bölge, ms|None)].
        Engelleyici iş — arka plan thread'inde çalıştırın."""
        results = []
        for p in self.profiles:
            if not VpnManager.is_profile_complete(p):
                continue
            ms = self.probe_profile(p, timeout)
            results.append((p.get("name", "?"), p.get("region", "?"), ms))
        return results

    def auto_select_fastest(self, timeout: float = 6.0) -> tuple[dict | None, list]:
        """En hızlı profili ölçüp seçer; VPN kapalıysa açar.
        Dönüş: (seçilen profil, ölçüm listesi)."""
        results = self.speed_test(timeout)
        best_name, best_region, best_ms = None, None, None
        for name, region, ms in results:
            if ms is not None and (best_ms is None or ms < best_ms):
                best_name, best_region, best_ms = name, region, ms
        if best_name is None:
            return None, results
        p = next((q for q in self.profiles
                  if q.get("name") == best_name
                  and q.get("region") == best_region), None)
        if p is None:
            return None, results
        ok, _msg = self.connect(p["id"])
        return (p if ok else None), results
