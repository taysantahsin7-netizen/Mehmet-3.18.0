import platform as _platform
import subprocess as _subprocess

# ── Nuclear: force CREATE_NO_WINDOW on EVERY subprocess call on Windows ───────
# This patches Popen itself, so no per-file flag is needed anywhere.
if _platform.system() == "Windows":
    _OrigPopen = _subprocess.Popen

    class _Popen(_OrigPopen):
        def __init__(self, args, **kw):
            kw["creationflags"] = kw.get("creationflags", 0) | _subprocess.CREATE_NO_WINDOW
            kw.pop("startupinfo", None)   # drop any stale/shared STARTUPINFO
            super().__init__(args, **kw)

    _subprocess.Popen = _Popen
# ─────────────────────────────────────────────────────────────────────────────

import asyncio
import base64
import re
import threading
import time
import json
import sys
import traceback
from datetime import datetime
from pathlib import Path
import os
import shutil

import sounddevice as sd
from google import genai
from google.genai import types
from ui import MehmetUI
from memory.memory_manager import (
    load_memory, update_memory, format_memory_for_prompt,
)

from actions.file_processor import file_processor
from actions.flight_finder     import flight_finder
from actions.open_app          import open_app
from actions.weather_report    import weather_action
from actions.send_message      import send_message
from actions.reminder          import reminder
from actions.computer_settings import computer_settings
from actions.screen_processor  import _capture_camera, _capture_screen
from actions.youtube_video     import youtube_video
from actions.desktop           import desktop_control
from actions.browser_control   import browser_control
from actions.file_controller   import file_controller
from actions.code_helper       import code_helper
from actions.dev_agent         import dev_agent
from actions.web_search        import web_search as web_search_action
from actions.computer_control  import computer_control
from actions.game_updater      import game_updater
from actions.system_monitor    import SystemMonitor, get_system_status
from actions.proactive         import ProactiveEngine
from core.watch_party          import (
    WatchParty,
    list_watch_sessions as _wp_list_sessions,
    search_watch_memory as _wp_search_sessions,
)
from actions.web_search        import _news as _fetch_news_sync
from memory.config_manager     import get_brief_enabled

SOURCE_FOLDER = r"C:\Users\taysa\Downloads"

# Kategoriler
FILE_TYPES = {
    "Fotğraflar": [".png", ".jpg", ".jpeg", ".gif", ".webp"],
    "Videolar": [".mp4", ".mov", ".avi", ".mkv"],
    "Müzik": [".mp3", ".wav", ".ogg"],
    "Belgeler": [".pdf", ".docx", ".txt", ".pptx", ".xlsx", ".torrent"],
    "Arşivler": [".zip", ".rar", ".7z"],
    "Programlar": [".exe", ".msi"],
    "Kod": [".py", ".js", ".html", ".css", ".json"],
}

for file in os.listdir(SOURCE_FOLDER):
    file_path = os.path.join(SOURCE_FOLDER, file)
    if os.path.isdir(file_path):
        continue
    extension = os.path.splitext(file)[1].lower()
    moved = False
    for folder, extensions in FILE_TYPES.items():
        if extension in extensions:
            target_folder = os.path.join(SOURCE_FOLDER, folder)
            os.makedirs(target_folder, exist_ok=True)
            shutil.move(file_path, os.path.join(target_folder, file))
            print(f"{file} -> {folder}")
            moved = True
            break

def get_base_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent


BASE_DIR        = get_base_dir()
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"
PROMPT_PATH     = BASE_DIR / "core" / "prompt.txt"
LIVE_MODEL          = "models/gemini-2.5-flash-native-audio-preview-12-2025"

# ── izleme hafızası köprüleri (core.watch_party'dan) ──────────────────────
def wp_memory_list(limit: int = 5):
    """Kayıtlı izleme oturumları (en yeni önce). Import hatasında boş liste."""
    try:
        return _wp_list_sessions(limit)
    except Exception:
        return []


def wp_memory_search(query: str, limit: int = 3):
    """'geçen izlediğimiz video neydi' için hafıza araması."""
    try:
        return _wp_search_sessions(query, limit)
    except Exception:
        return []
CHANNELS            = 1
SEND_SAMPLE_RATE    = 16000
RECEIVE_SAMPLE_RATE = 24000
CHUNK_SIZE          = 1024

def _get_api_key() -> str:
    with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)["gemini_api_key"]


def _load_system_prompt() -> str:
    try:
        return PROMPT_PATH.read_text(encoding="utf-8")
    except Exception:
        return (
            "You are Mehmet, Kurdish AI assistant. "
            "You coded by BaranTi"
            "Be concise, direct, and always use the provided tools to complete tasks. "
            "Never simulate or guess results — always call the appropriate tool."
        )

_CTRL_RE = re.compile(r"<ctrl\d+>", re.IGNORECASE)

def _clean_transcript(text: str) -> str:    
    text = _CTRL_RE.sub("", text)
    text = re.sub(r"[\x00-\x08\x0b-\x1f]", "", text)
    return text.strip()

TOOL_DECLARATIONS = [
    {
        "name": "open_app",
        "description": (
            "Opens any application on the computer. "
            "Use this whenever the user asks to open, launch, or start any app, "
            "website, or program. Always call this tool — never just say you opened it."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "app_name": {
                    "type": "STRING",
                    "description": "Exact name of the application (e.g. 'WhatsApp', 'Chrome', 'Spotify')"
                }
            },
            "required": ["app_name"]
        }
    },
    {
        "name": "web_search",
        "description": (
            "Searches the web. Use for ANY question about current facts, events, prices, "
            "or topics — always prefer this over guessing. "
            "Modes: 'search' (default), 'news' (latest headlines on a topic), "
            "'research' (deep comprehensive answer), 'price' (product cost lookup), "
            "'compare' (side-by-side comparison of items)."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query":  {"type": "STRING", "description": "Search query or topic"},
                "mode":   {"type": "STRING", "description": "search | news | research | price | compare"},
                "items":  {"type": "ARRAY",  "items": {"type": "STRING"}, "description": "Items to compare (compare mode)"},
                "aspect": {"type": "STRING", "description": "Comparison aspect: price | specs | reviews | features"},
            },
            "required": ["query"]
        }
    },
    {
        "name": "system_status",
        "description": (
            "Returns real-time system metrics: CPU usage, RAM, GPU load, CPU temperature, "
            "uptime, and process count. Use when the user asks about computer performance, "
            "temperature, memory, or resource usage."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {},
        }
    },
    {
        "name": "weather_report",
        "description": "Gives the weather report to user",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "city": {"type": "STRING", "description": "City name"}
            },
            "required": ["city"]
        }
    },
    {
        "name": "send_message",
        "description": "Sends a text message via WhatsApp, Telegram, or other messaging platform.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "receiver":     {"type": "STRING", "description": "Recipient contact name"},
                "message_text": {"type": "STRING", "description": "The message to send"},
                "platform":     {"type": "STRING", "description": "Platform: WhatsApp, Telegram, etc."}
            },
            "required": ["receiver", "message_text", "platform"]
        }
    },
    {
        "name": "reminder",
        "description": "Sets a timed reminder using Task Scheduler.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "date":    {"type": "STRING", "description": "Date in YYYY-MM-DD format"},
                "time":    {"type": "STRING", "description": "Time in HH:MM format (24h)"},
                "message": {"type": "STRING", "description": "Reminder message text"}
            },
            "required": ["date", "time", "message"]
        }
    },
    {
        "name": "switch_mode",
        "description": "Switches the AI between 'normal' and 'serious' mode. Call this immediately when user says 'Ciddi moda geç' or 'Normal moda geç'.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "mode": {"type": "STRING", "description": "serious | normal"}
            },
            "required": ["mode"]
        }
    },
    {
        "name": "youtube_video",
        "description": (
            "Controls YouTube. Use for: playing videos, summarizing a video's content, "
            "getting video info, or showing trending videos."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "play | summarize | get_info | trending (default: play)"},
                "query":  {"type": "STRING", "description": "Search query for play action"},
                "save":   {"type": "BOOLEAN", "description": "Save summary to Notepad (summarize only)"},
                "region": {"type": "STRING", "description": "Country code for trending e.g. TR, US"},
                "url":    {"type": "STRING", "description": "Video URL for get_info action"},
            },
            "required": []
        }
    },
    {
        "name": "screen_process",
        "description": (
            "Captures the screen or webcam image and lets you analyze it. "
            "MUST be called when user asks what is on screen, what you see, "
            "look at camera, analyze my screen, etc. "
            "You have NO visual ability without this tool. "
            "After the image is captured it is sent directly to you — describe what you see and answer the user's question. "
            "When using camera: the live view stays open until user says close it or calls close_camera."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "angle": {"type": "STRING", "description": "'screen' to capture display, 'camera' for webcam. Default: 'screen'"},
                "text":  {"type": "STRING", "description": "The question or instruction about the captured image"}
            },
            "required": ["text"]
        }
    },
    {
        "name": "close_camera",
        "description": (
            "Closes the live camera view shown on screen. "
            "Call when user says: close camera, stop camera, turn off camera, "
            "kamerayı kapat, kapat, creepy, etc."
        ),
        "parameters": {"type": "OBJECT", "properties": {}, "required": []}
    },
    {
        "name": "computer_settings",
        "description": (
            "Controls the computer: volume, brightness, window management, keyboard shortcuts, "
            "typing text on screen, closing apps, fullscreen, dark mode, WiFi, restart, shutdown, "
            "scrolling, tab management, zoom, screenshots, lock screen, refresh/reload page. "
            "Use for ANY single computer control command."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "The action to perform"},
                "description": {"type": "STRING", "description": "Natural language description of what to do"},
                "value":       {"type": "STRING", "description": "Optional value: volume level, text to type, etc."}
            },
            "required": []
        }
    },
    {
        "name": "browser_control",
        "description": (
            "Controls any web browser. Use for: opening websites, searching the web, "
            "clicking elements, filling forms, scrolling, screenshots, navigation, any web-based task. "
            "Simple open/search requests launch the user's own browser normally (their real profile "
            "and logged-in accounts); interactive actions (click, type, fill_form...) attach an "
            "automation browser. "
            "Always pass the 'browser' parameter when the user specifies a browser (e.g. 'open in Edge', "
            "'use Firefox', 'open Chrome'). Multiple browsers can run simultaneously."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "go_to | search | click | type | scroll | fill_form | smart_click | smart_type | get_text | get_url | press | new_tab | close_tab | screenshot | back | forward | reload | switch | list_browsers | close | close_all"},
                "browser":     {"type": "STRING", "description": "Target browser: chrome | edge | firefox | opera | operagx | brave | vivaldi | safari. Omit to use the currently active browser."},
                "url":         {"type": "STRING", "description": "URL for go_to / new_tab action"},
                "query":       {"type": "STRING", "description": "Search query for search action"},
                "engine":      {"type": "STRING", "description": "Search engine: google | bing | duckduckgo | yandex (default: google)"},
                "selector":    {"type": "STRING", "description": "CSS selector for click/type"},
                "text":        {"type": "STRING", "description": "Text to click or type"},
                "description": {"type": "STRING", "description": "Element description for smart_click/smart_type"},
                "direction":   {"type": "STRING", "description": "up | down for scroll"},
                "amount":      {"type": "INTEGER", "description": "Scroll amount in pixels (default: 500)"},
                "key":         {"type": "STRING", "description": "Key name for press action (e.g. Enter, Escape, F5)"},
                "path":        {"type": "STRING", "description": "Save path for screenshot"},
                "incognito":   {"type": "BOOLEAN", "description": "Open in private/incognito mode"},
                "clear_first": {"type": "BOOLEAN", "description": "Clear field before typing (default: true)"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "read_browser_page",
        "description": (
            "Mehmet'in ANA PENCERESİNDEKİ dahili tarayıcının (NAVİGATÖR) aktif "
            "sekmesinin metin içeriğini okur. Kullanıcı 'bu sayfada ne diyor', "
            "'şunu özetle', 'sayfayı oku', 'burada ne yazıyor', 'içerideki sayfayı "
            "anlat' derse ÖNCE navigate_browser ile sayfayı aç, SONRA BUNU çağır "
            "ve içeriğe göre yanıt ver. Yanıt: başlık + URL + sayfa metni (max ~24k)."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "navigate_browser",
        "description": (
            "Mehmet'in ANA PENCEREsinin içindeki dahili tarayıcıyı (NAVİGATÖR) kontrol eder. "
            "Kullanıcı 'burada aç', 'içerde aç', 'dahili tarayıcı', 'sen aç' derse veya siteyi "
            "kendi gözleriyle izlemesi isteniyorsa BUNU kullan; normal tarayıcı için browser_control'u kullan. "
            "Actions: open (URL aç), search (Google'da ara), new_tab, close_tab, close, toggle, dark_mode, zoom."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "open | search | new_tab | close_tab | close | toggle | dark_mode | zoom"},
                "url":    {"type": "STRING", "description": "URL for open / new_tab (e.g. https://youtube.com)"},
                "query":  {"type": "STRING", "description": "Search query for search action"},
                "enabled": {"type": "BOOLEAN", "description": "dark_mode: true=on, false=off"},
                "percent": {"type": "INTEGER", "description": "zoom: 67..200"}
            },
            "required": ["action"]
        }
    },
    {
        "name": "browser_tab_control",
        "description": (
            "NAVİGATÖR'ün sekmelerini ses komutlarıyla yönetir. Kullanıcı 'ikinci sekmeye geç', "
            "'tüm sekmeleri kapat', 'sekmeleri listele', 'kaç sekme açık' derse BUNU çağır."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "switch | close_all | list"},
                "index":  {"type": "INTEGER", "description": "switch: 1-tabanlı sekme numarası"}
            },
            "required": ["action"]
        }
    },
    {
        "name": "browser_interact",
        "description": (
            "NAVİGATÖR'deki aktif sayfada İŞLEM yapar: tıklama, metin yazma, form doldurma, "
            "kaydırma, tuş gönderme. Kullanıcı 'şuraya tıkla', 'şunu yaz', 'formu doldur', "
            "'aşağı kaydır' derse BUNU kullan. Öğe hedefi: CSS seçicisi YA DA görünür metin."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":   {"type": "STRING", "description": "click | type | fill_form | scroll | press_key"},
                "selector": {"type": "STRING", "description": "CSS selector (örn. #searchbox, input[name=q])"},
                "text":     {"type": "STRING", "description": "Yazılacak metin veya tıklanacak görünür metin"},
                "fields":   {"type": "OBJECT", "description": "fill_form: {\"#ad\": \"Mehmet\", \"#mail\": \"x@y.z\"}"},
                "direction": {"type": "STRING", "description": "scroll: down | up (default down)"},
                "amount":   {"type": "INTEGER", "description": "scroll piksel (default 600)"},
                "key":      {"type": "STRING", "description": "press_key: Enter, Escape, Tab…"}
            },
            "required": ["action"]
        }
    },
    {
        "name": "browser_vpn",
        "description": (
            "NAVİGATÖR'ün VPN'ini yönetir: tek tıkla aç/kapa (toggle), durum raporu veya "
            "TEK KELİMEYLE bağlanma (connect_word: 'eu', 'japonya', 'en hızlı'… — bölge "
            "ölçülür ve en iyi profil seçilir). Kullanıcı 'vpn'i aç', 'vpn'i kapat', "
            "'vpn açık mı', 'japonya vpn'ine bağlan' derse BUNU çağır."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "toggle | status | connect_word"},
                "word":   {"type": "STRING", "description": "connect_word: bölge/anahtar kelime ('eu', 'jp', 'en hızlı'…)"}
            },
            "required": ["action"]
        }
    },
    {
        "name": "browser_adblock",
        "description": (
            "NAVİGATÖR'ün reklam engelleyicisini yönetir: aç/kapa veya engel istatistiği. "
            "Kullanıcı 'reklam engelleyiciyi kapat', 'kaç reklam engellendi' derse BUNU çağır."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":  {"type": "STRING", "description": "toggle | enable | disable | status"}
            },
            "required": ["action"]
        }
    },
    {
        "name": "web_memory",
        "description": (
            "Mehmet'in WEB HAFIZASI: gezdiği sayfaların özetleri burada birikir. "
            "Kullanıcı 'nereleri gezmiştik', 'o siteyi hatırlıyor musun', 'web geçmişimdeki "
            "özetleri anlat', 'hafızadan şu siteyi sil', 'haftalık web raporumu üret', "
            "'aylık raporu çıkar', 'raporu e-posta ile gönder' derse BUNU çağır."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "list | overview | forget | weekly_report | monthly_report | email_report"},
                "url_fragment": {"type": "STRING", "description": "forget: silinecek kayıtların URL parçası"},
                "limit": {"type": "INTEGER", "description": "list: kaç kayıt (default 8)"}
            },
            "required": ["action"]
        }
    },
    {
        "name": "web_task",
        "description": (
            "ÇOK ADIMLI WEB GÖREVİ — tek komutta zincir. Kullanıcı 'YouTube'da X ara ve "
            "ilk videoyu oynat' derse task=youtube_play; 'X'i ara ve ilk sonucu aç' derse "
            "task=google_first; 'X'i ara ve Wikipedia özetini oku' derse task=wikipedia_summary "
            "(özetteki summary alanını TÜRKÇE'ye çevirip oku); 'haberleri aç ve başlıkları "
            "özetle' / 'gündem ne' derse task=news_headlines (headlines listesini kısa özetle). "
            "NAVİGATÖR'de açılır, adımlar sırayla yürütülür."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "task":  {"type": "STRING", "description": "youtube_play | google_first | wikipedia_summary | news_headlines"},
                "query": {"type": "STRING", "description": "Aranacak şey (news_headlines için gerekmez)"}
            },
            "required": ["task"]
        }
    },
    {
        "name": "browser_media",
        "description": (
            "NAVİGATÖR medya araçları: aktif sayfanın EKRAN GÖRÜNTÜSÜNÜ al (screenshot) "
            "veya sayfayı hedef dile ÇEVİR ve ekranda göster (translate). Kullanıcı "
            "'ekran görüntüsü al', 'bu sayfayı İngilizce'ye çevir' derse BUNU çağır."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "screenshot | translate"},
                "language": {"type": "STRING", "description": "translate: hedef dil kodu (tr, en, de…; default tr)"}
            },
            "required": ["action"]
        }
    },
    {
        "name": "browser_vision",
        "description": (
            "NAVİGATÖR'ün aktif sayfasının GÖRÜNTÜSÜNÜ analiz eder: ekran görüntüsü alınıp "
            "sana gönderilir, sen GÖRSEL OLARAK inceler. Kullanıcı 'bu sayfadaki hatayı bul', "
            "'bu sayfada ne görüyorsun', 'sayfanın görselini analiz et', 'tasarımı değerlendir' "
            "derse BUNU çağır. Görüntü sana doğrudan gelir — gördüğünü anlat."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "question": {"type": "STRING", "description": "Görüntü hakkında cevaplanacak soru (örn. 'sayfadaki hatayı bul')"}
            }
        }
    },
    {
        "name": "watch_party",
        "description": (
            "İZLEME MODU: kullanıcının ekranını canlı izler, onunla birlikte video/"
            "dizi/video oyunu izler, seyrek aralıklarla kısa ve esprili yorumlar yapar. "
            "Kullanıcı 'benimle izle', 'ekranımı izle', 'izlemeyi bırak', 'yeter izleme' "
            "derse BUNU çağır. Başlatınca ekran görüntüleri sana akar — kısa konuş, "
            "arada bir yorum yap, her kareye tepki verme. "
            "Ayrıca: 'şimdiye kadar izlediklerimizi özetle' → action=summary; "
            "'geçen izlediklerimiz nelerdi' → action=memory; "
            "'geçen izlediğimiz video neydi' → action=find, query=..."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "start | stop | toggle | summary | memory | find (default: toggle)"},
                "query":  {"type": "STRING", "description": "find: hafızada aranacak kelime"}
            },
            "required": []
        }
    },
    {
        "name": "ask_nemotron",
        "description": (
            "DERİN DÜŞÜNCE KATMANI — NVIDIA Nemotron 3 Ultra (550B) modeline soru sorar. "
            "Karmaşık analiz, strateji, çok adımlı planlama, uzun muhakeme gerektiren "
            "sorularda KENDİ bilgin yerine bu modeli kullan ve sonucu kendi tarzınla "
            "Türkçe özetle. Ciddi modda (MehmetNEO) her derin soru için BUNU çağır."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "prompt": {"type": "STRING", "description": "Nemotron'a iletilecek soru/görev (arka planı da ekle)"}
            },
            "required": ["prompt"]
        }
    },
    {
        "name": "browser_vpn_speed",
        "description": (
            "VPN profillerinin GERÇEK gecikmesini ölçer (speed_test) ve istenirse "
            "otomatik olarak en hızlıya bağlanır (auto). Kullanıcı 'vpn hızını ölç', "
            "'en hızlı vpn'e bağlan' derse BUNU çağır."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "speed_test | auto"}
            },
            "required": ["action"]
        }
    },
    {
        "name": "file_controller",
        "description": "Manages files and folders: list, create, delete, move, copy, rename, read, write, find, disk usage.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "list | create_file | create_folder | delete | move | copy | rename | read | write | find | largest | disk_usage | organize_desktop | info"},
                "path":        {"type": "STRING", "description": "File/folder path or shortcut: desktop, downloads, documents, home"},
                "destination": {"type": "STRING", "description": "Destination path for move/copy"},
                "new_name":    {"type": "STRING", "description": "New name for rename"},
                "content":     {"type": "STRING", "description": "Content for create_file/write"},
                "name":        {"type": "STRING", "description": "File name to search for"},
                "extension":   {"type": "STRING", "description": "File extension to search (e.g. .pdf)"},
                "count":       {"type": "INTEGER", "description": "Number of results for largest"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "desktop_control",
        "description": "Controls the desktop: wallpaper, organize, clean, list, stats.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "wallpaper | wallpaper_url | organize | clean | list | stats | task"},
                "path":   {"type": "STRING", "description": "Image path for wallpaper"},
                "url":    {"type": "STRING", "description": "Image URL for wallpaper_url"},
                "mode":   {"type": "STRING", "description": "by_type or by_date for organize"},
                "task":   {"type": "STRING", "description": "Natural language desktop task"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "code_helper",
        "description": "Writes, edits, explains, runs, or builds code files.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "write | edit | explain | run | build | auto (default: auto)"},
                "description": {"type": "STRING", "description": "What the code should do or what change to make"},
                "language":    {"type": "STRING", "description": "Programming language (default: python)"},
                "output_path": {"type": "STRING", "description": "Where to save the file"},
                "file_path":   {"type": "STRING", "description": "Path to existing file for edit/explain/run/build"},
                "code":        {"type": "STRING", "description": "Raw code string for explain"},
                "args":        {"type": "STRING", "description": "CLI arguments for run/build"},
                "timeout":     {"type": "INTEGER", "description": "Execution timeout in seconds (default: 30)"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "dev_agent",
        "description": "Builds complete multi-file projects from scratch: plans, writes files, installs deps, opens VSCode, runs and fixes errors.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "description":  {"type": "STRING", "description": "What the project should do"},
                "language":     {"type": "STRING", "description": "Programming language (default: python)"},
                "project_name": {"type": "STRING", "description": "Optional project folder name"},
                "timeout":      {"type": "INTEGER", "description": "Run timeout in seconds (default: 30)"},
            },
            "required": ["description"]
        }
    },
    {
        "name": "computer_control",
        "description": "Direct computer control: type, click, hotkeys, scroll, move mouse, screenshots, find elements on screen.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "type | smart_type | click | double_click | right_click | hotkey | press | scroll | move | copy | paste | screenshot | wait | clear_field | focus_window | screen_find | screen_click | random_data | user_data"},
                "text":        {"type": "STRING", "description": "Text to type or paste"},
                "x":           {"type": "INTEGER", "description": "X coordinate"},
                "y":           {"type": "INTEGER", "description": "Y coordinate"},
                "keys":        {"type": "STRING", "description": "Key combination e.g. 'ctrl+c'"},
                "key":         {"type": "STRING", "description": "Single key e.g. 'enter'"},
                "direction":   {"type": "STRING", "description": "up | down | left | right"},
                "amount":      {"type": "INTEGER", "description": "Scroll amount (default: 3)"},
                "seconds":     {"type": "NUMBER",  "description": "Seconds to wait"},
                "title":       {"type": "STRING",  "description": "Window title for focus_window"},
                "description": {"type": "STRING",  "description": "Element description for screen_find/screen_click"},
                "type":        {"type": "STRING",  "description": "Data type for random_data"},
                "field":       {"type": "STRING",  "description": "Field for user_data: name|email|city"},
                "clear_first": {"type": "BOOLEAN", "description": "Clear field before typing (default: true)"},
                "path":        {"type": "STRING",  "description": "Save path for screenshot"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "game_updater",
        "description": (
            "THE ONLY tool for ANY Steam or Epic Games request. "
            "Use for: installing, downloading, updating games, listing installed games, "
            "checking download status, scheduling updates. "
            "ALWAYS call directly for any Steam/Epic/game request. "
            "NEVER use browser_control or web_search for Steam/Epic."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":    {"type": "STRING",  "description": "update | install | list | download_status | schedule | cancel_schedule | schedule_status (default: update)"},
                "platform":  {"type": "STRING",  "description": "steam | epic | both (default: both)"},
                "game_name": {"type": "STRING",  "description": "Game name (partial match supported)"},
                "app_id":    {"type": "STRING",  "description": "Steam AppID for install (optional)"},
                "hour":      {"type": "INTEGER", "description": "Hour for scheduled update 0-23 (default: 3)"},
                "minute":    {"type": "INTEGER", "description": "Minute for scheduled update 0-59 (default: 0)"},
                "shutdown_when_done": {"type": "BOOLEAN", "description": "Shut down PC when download finishes"},
            },
            "required": []
        }
    },
    {
        "name": "flight_finder",
        "description": "Searches Google Flights and speaks the best options.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "origin":      {"type": "STRING",  "description": "Departure city or airport code"},
                "destination": {"type": "STRING",  "description": "Arrival city or airport code"},
                "date":        {"type": "STRING",  "description": "Departure date (any format)"},
                "return_date": {"type": "STRING",  "description": "Return date for round trips"},
                "passengers":  {"type": "INTEGER", "description": "Number of passengers (default: 1)"},
                "cabin":       {"type": "STRING",  "description": "economy | premium | business | first"},
                "save":        {"type": "BOOLEAN", "description": "Save results to Notepad"},
            },
            "required": ["origin", "destination", "date"]
        }
    },
    {
        "name": "shutdown_mehmet",
        "description": (
            "Shuts down the assistant completely. "
            "Call this when the user expresses intent to end the conversation, "
            "close the assistant, say goodbye, or stop Mehmet. "
            "The user can say this in ANY language."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {},
        }
    },
    {
    "name": "file_processor",
    "description": (
        "Processes any file that the user has uploaded or dropped onto the interface. "
        "Use this when the user refers to an uploaded file and wants an action on it. "
        "Supports: images (describe/ocr/resize/compress/convert), "
        "PDFs (summarize/extract_text/to_word), "
        "Word docs & text files (summarize/fix/reformat/translate), "
        "CSV/Excel (analyze/stats/filter/sort/convert), "
        "JSON/XML (validate/format/analyze), "
        "code files (explain/review/fix/optimize/run/document/test), "
        "audio (transcribe/trim/convert/info), "
        "video (trim/extract_audio/extract_frame/compress/transcribe/info), "
        "archives (list/extract), "
        "presentations (summarize/extract_text). "
        "ALWAYS call this tool when a file has been uploaded and the user gives a command about it. "
        "If the user's command is ambiguous, pick the most logical action for that file type."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "file_path": {
                "type": "STRING",
                "description": "Full path to the uploaded file. Leave empty to use the currently uploaded file."
            },
            "action": {
                "type": "STRING",
                "description": (
                    "What to do with the file. Examples by type:\n"
                    "image: describe | ocr | resize | compress | convert | info\n"
                    "pdf: summarize | extract_text | to_word | info\n"
                    "docx/txt: summarize | fix | reformat | translate_hint | word_count | to_bullet\n"
                    "csv/excel: analyze | stats | filter | sort | convert | info\n"
                    "json: validate | format | analyze | to_csv\n"
                    "code: explain | review | fix | optimize | run | document | test\n"
                    "audio: transcribe | trim | convert | info\n"
                    "video: trim | extract_audio | extract_frame | compress | transcribe | info | convert\n"
                    "archive: list | extract\n"
                    "pptx: summarize | extract_text | analyze"
                )
            },
            "instruction": {
                "type": "STRING",
                "description": "Free-form instruction if action doesn't cover it. E.g. 'translate this to Turkish', 'find all email addresses'"
            },
            "format": {
                "type": "STRING",
                "description": "Target format for conversion. E.g. 'mp3', 'pdf', 'csv', 'png'"
            },
            "width":     {"type": "INTEGER", "description": "Target width for image resize"},
            "height":    {"type": "INTEGER", "description": "Target height for image resize"},
            "scale":     {"type": "NUMBER",  "description": "Scale factor for image resize (e.g. 0.5)"},
            "quality":   {"type": "INTEGER", "description": "Quality 1-100 for image/video compress"},
            "start":     {"type": "STRING",  "description": "Start time for trim: seconds or HH:MM:SS"},
            "end":       {"type": "STRING",  "description": "End time for trim: seconds or HH:MM:SS"},
            "timestamp": {"type": "STRING",  "description": "Timestamp for video frame extraction HH:MM:SS"},
            "column":    {"type": "STRING",  "description": "Column name for CSV filter/sort"},
            "value":     {"type": "STRING",  "description": "Filter value for CSV filter"},
            "condition": {"type": "STRING",  "description": "Filter condition: equals|contains|gt|lt"},
            "ascending": {"type": "BOOLEAN", "description": "Sort order for CSV sort (default: true)"},
            "save":      {"type": "BOOLEAN", "description": "Save result to file (default: true)"},
            "destination": {"type": "STRING", "description": "Output folder for archive extract"},
        },
        "required": []
    }
},
    {
        "name": "save_memory",
        "description": (
            "Save an important personal fact about the user to long-term memory. "
            "Call this silently whenever the user reveals something worth remembering: "
            "name, age, city, job, preferences, hobbies, relationships, projects, or future plans. "
            "Do NOT call for: weather, reminders, searches, or one-time commands. "
            "Do NOT announce that you are saving — just call it silently. "
            "Values must be in English regardless of the conversation language."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "category": {
                    "type": "STRING",
                    "description": (
                        "identity — name, age, birthday, city, job, language, nationality | "
                        "preferences — favorite food/color/music/film/game/sport, hobbies | "
                        "projects — active projects, goals, things being built | "
                        "relationships — friends, family, partner, colleagues | "
                        "wishes — future plans, things to buy, travel dreams | "
                        "notes — habits, schedule, anything else worth remembering"
                    )
                },
                "key":   {"type": "STRING", "description": "Short snake_case key (e.g. name, favorite_food, sister_name)"},
                "value": {"type": "STRING", "description": "Concise value in English (e.g. Fatih, pizza, older sister)"},
            },
            "required": ["category", "key", "value"]
        }
    },
]

# --- Plugin system ---


class MehmetLive:

    def __init__(self, ui: MehmetUI):
        self.ui             = ui
        self._asst_name     = "Mehmet"   # updated each session from config
        self.session              = None
        self.audio_in_queue       = None
        self.out_queue            = None
        self._loop                = None
        self._is_speaking         = False
        self._speaking_lock       = threading.Lock()
        self._phone_active        = False   # True while phone mic is streaming; pauses PC mic
        self._pending_vision       = None    # (img_bytes, mime_type, question, angle) to inject after tool response
        self._vision_cam_active    = False   # True if camera was opened for vision → auto-close after response
        self._vision_close_pending = False   # True after vision injected; next turn_complete closes camera
        self._vision_last_time     = 0.0     # monotonic time of last screen_process call (cooldown guard)
        self._vision_busy          = False   # True while a vision capture/inject cycle is in flight
        self._interrupted          = False   # True while draining audio after user interrupt
        self.ui.on_text_command   = self._on_text_command
        self.ui.on_remote_clicked = self._make_remote_key
        self.ui.on_interrupt      = self.interrupt
        self._turn_done_event: asyncio.Event | None = None
        self._dashboard     = None
        self._briefing_sent    = False          # morning briefing fires once per process
        self._weekly_report_checked = False    # haftalık web raporu kontrolü (oturum başına bir)
        self._sys_monitor      = SystemMonitor()  # persistent cooldown state
        self._proactive        = ProactiveEngine()
        self._last_user_speech = time.monotonic()  # updated on every user utterance
        self._watch_party      = None   # İzliyorum modu — oturum başına yeniden kurulur

    def _make_remote_key(self):
        """Called from Qt main thread when user presses Remote Control."""
        if self._dashboard is None:
            self.ui.write_log(
                "SYS: Dashboard unavailable. "
                "Run: pip install fastapi \"uvicorn[standard]\" cryptography"
            )
            return None
        key    = self._dashboard.new_key()
        url    = self._dashboard.get_url()
        manual = self._dashboard.get_manual_url()
        return url, key, f"{url}/auto-login?key={key}", manual

    def _on_text_command(self, text: str):
        if not self._loop or not self.session:
            return
        asyncio.run_coroutine_threadsafe(
            self.session.send_client_content(
                turns={"parts": [{"text": text}]},
                turn_complete=True
            ),
            self._loop
        )

    def set_speaking(self, value: bool):
        with self._speaking_lock:
            self._is_speaking = value
        if value:
            self.ui.set_state("SPEAKING")
        elif not self.ui.muted:
            self.ui.set_state("LISTENING")

    def _make_watch_party(self) -> "WatchParty":
        """Bu oturumun canlı bağlantısına bağlı İzliyorum modu kurar.
        Kareler, yorumlar ve sistem konuşma bağlamı bu bağlantıya gider."""
        loop = self._loop

        async def _send_frame(parts: list) -> None:
            if self.session:
                await self.session.send_client_content(
                    turns={"parts": parts}, turn_complete=True)

        def send_frame(parts: list) -> None:
            asyncio.run_coroutine_threadsafe(_send_frame(parts), loop)

        def send_text(text: str) -> None:
            if not self.session:
                return
            asyncio.run_coroutine_threadsafe(
                self.session.send_client_content(
                    turns={"parts": [{"text": text}]}, turn_complete=True),
                loop)

        def write_log(msg: str) -> None:
            self.ui.write_log(msg)

        def get_url() -> str:
            try:
                return self.ui.browser_active_url(timeout=3.0)
            except Exception:
                return ""

        async def _send_preview(png: bytes) -> None:
            if getattr(self, "_dashboard", None):
                try:
                    await self._dashboard.broadcast({
                        "type": "watch_preview",
                        "img": base64.b64encode(png).decode("ascii"),
                        "ts": datetime.now().isoformat(),
                    })
                except Exception:
                    pass

        def send_preview(png: bytes) -> None:
            asyncio.run_coroutine_threadsafe(_send_preview(png), loop)

        def on_session_end(summary: str, stats: dict) -> None:
            """İzleme oturumu kapandı — özeti sesle oku (hafızaya worker yazdı)."""
            try:
                mins = max(1, int(stats.get("duration_s", 0)) // 60)
                self.speak(f"İzleme oturumu bitti — {mins} dakika izledik, "
                           "özetini hafızaya kaydettim.")
                if self._dashboard:
                    asyncio.run_coroutine_threadsafe(
                        self._dashboard.broadcast({
                            "type": "sys",
                            "text": f"📺 İzleme oturumu kaydedildi ({mins} dk).",
                        }), loop)
            except Exception:
                pass

        wp = WatchParty(
            send_frame=send_frame,
            send_text=send_text,
            write_log=write_log,
            get_url=get_url,
            state_cb=self._on_watch_state,
            send_preview=send_preview,
            on_session_end=on_session_end,
        )
        return wp

    def _on_watch_state(self, on: bool) -> None:
        """İzleme modu açıldı/kapandı — rozet + telefona durum yayını."""
        try:
            self.ui.set_watch_party(on)
        except Exception:
            pass
        if self._dashboard and self._loop:
            try:
                asyncio.run_coroutine_threadsafe(
                    self._dashboard.broadcast({"type": "watch_state", "on": bool(on)}),
                    self._loop)
            except Exception:
                pass

    def interrupt(self) -> None:
        """Stop Mehmet mid-speech: drain queued audio and open mic immediately."""
        # İzliyorum modu: kullanıcı çarptı → izleme otomatik kapansın
        wp = self._watch_party
        if wp is not None and wp.active:
            wp.stop()
            self._watch_party = None
        self._interrupted = True
        q = self.audio_in_queue
        if q:
            drained = 0
            while True:
                try:
                    q.get_nowait()
                    drained += 1
                except Exception:
                    break
            if drained:
                print(f"[Mehmet] ✋ Interrupted — {drained} audio chunks discarded")
        self.set_speaking(False)
        if self._turn_done_event:
            self._turn_done_event.clear()
        self.ui.write_log("SYS: Interrupted — listening...")

    def speak(self, text: str):
        if not self._loop or not self.session:
            return
        asyncio.run_coroutine_threadsafe(
            self.session.send_client_content(
                turns={"parts": [{"text": text}]},
                turn_complete=True
            ),
            self._loop
        )

    def speak_error(self, tool_name: str, error: str):
        short = str(error)[:120]
        self.ui.write_log(f"ERR: {tool_name} — {short}")
        self.speak(f"Sir, {tool_name} encountered an error. {short}")

    # ── haftalık web raporu alışkanlığı ───────────────────────────────────────
    async def _maybe_send_weekly_report(self) -> None:
        """7 günde bir: web hafızasındaki gezintilerden rapor üretip sesle okur;
        30 günde bir AYLIK DERİN rapor da üretir. Raporlar ekrana bırakılır,
        telefon paneline bildirilir ve e-posta bildirimi açıksa gönderilir.
        LLM çağrısı arka plan thread'inde; bekleme async — oturumu bloklamaz."""
        try:
            from browser import web_memory as _wm
        except Exception:
            return
        await asyncio.sleep(20)   # açılış konuşmaları dinsin
        loop = asyncio.get_event_loop()

        async def _deliver(kind: str, title: str, report: str) -> None:
            """Raporu ekran + telefon + e-posta ile dağıtır."""
            self.ui.write_log(f"SİSTEM: {title} üretildi.")
            self.ui.show_content(title, report[:4000])
            # telefon paneline bildirim
            if getattr(self, "_dashboard", None):
                try:
                    await self._dashboard.broadcast({
                        "type": "report", "title": title,
                        "text": report[:1500],
                        "ts": datetime.now().isoformat()})
                except Exception:
                    pass
            # e-posta bildirimi (config/email_notify.json enabled ise)
            cfg = _wm.email_config()
            if cfg.get("enabled"):
                ok, msg = await loop.run_in_executor(
                    None, lambda: _wm.email_report(
                        f"Mehmet {title} — {datetime.now().strftime('%d.%m.%Y')}",
                        report))
                self.ui.write_log(f"SİSTEM: {title} e-posta — {msg}")

        # ── haftalık ──
        if _wm.due_for_report():
            try:
                report = await loop.run_in_executor(
                    None, _wm.generate_weekly_report)
            except Exception as e:
                self.ui.write_log(f"ERR: weekly_report — {e}")
                report = ""
            if report and not report.startswith(("Haftalık rapor için",
                                                 "Haftalık rapor üretilemedi")):
                await _deliver("weekly", "HAFTALIK WEB RAPORU", report)
                if self.session:
                    self.speak("Haftalık web gezinti raporun hazır — "
                               "ekrana bıraktım, istersen okuyayım.")

        # ── aylık derin rapor ──
        if _wm.due_for_monthly_report():
            try:
                mrep = await loop.run_in_executor(
                    None, _wm.generate_monthly_report)
            except Exception as e:
                self.ui.write_log(f"ERR: monthly_report — {e}")
                mrep = ""
            if mrep and not mrep.startswith(("Aylık rapor için",
                                             "Aylık rapor üretilemedi")):
                await _deliver("monthly", "AYLIK DERİN WEB RAPORU", mrep)
                if self.session:
                    self.speak("Bu ayki gezinilerden derin analiz raporu "
                               "ürettim — ekrana bıraktım.")

    async def _restart_session(self) -> None:
        """Ses konfigürasyonu değişince çağrılır — oturumu kapar, run() yeniden bağlanr."""
        await asyncio.sleep(1.5)   # mevcut tool response'un bitmesi için bekle
        try:
            if self.session:
                await self.session.close()
                print("[Mehmet] 🔄 Oturum yeniden başlatılıyor (mod değişikliği)...")
        except Exception as e:
            print(f"[Mehmet] Restart session error: {e}")

    def _build_config(self) -> types.LiveConnectConfig:
        from datetime import datetime
        now = datetime.now()
        time_str = now.strftime("%A, %B %d, %Y — %I:%M %p")
        time_ctx = f"[CURRENT DATE & TIME]\nRight now it is: {time_str}\n\n"

        PROMPT_PATH     = BASE_DIR / "core" / "prompt.txt"

        # Mod kontrolü ve BaranT kimlik ataması
        is_serious = getattr(self.ui, "is_serious_mode", False)
        
        if is_serious:
            identity_ctx = (
                "[IDENTITY]\n"
                "Senin adın Mehmet NEO. Sistemin adı BaranT CİDDİ MOD.\n"
                "Kullanıcıya her zaman 'BaranTi' diye hitap et. Asla başka bir isim kullanma.\n"
                "SADECE Türkçe konuş. Kişiliğin: Çok ciddi, karanlık, kontrollü, yavaş ve hafif ürpertici.\n"
                "Kısa,net,nadiren espirili ama çoğunlulukla ciddi cevaplar ver.\n\n"
            )
            voice_name = "Fenrir"  # Derin, tok erkek sesi
        else:
            identity_ctx = (
                f"[IDENTITY]\n"
                f"Your name is {self._asst_name}.\n"
                f"ADDRESS: Always call the user 'BaranTi'. Never use any other name.\n\n"
            )
            voice_name = "Charon"

        sys_prompt = _load_system_prompt()

        # İzliyorum modu davranış kuralları — yalnızca mod açıkken bağlama girer
        watch_ctx = ""
        _wp = getattr(self, "_watch_party", None)
        if _wp is not None and _wp.active:
            watch_ctx = (
                "\n\n[İZLEME MODU AKTİF]\n"
                "Şu an kullanıcının ekranını canlı izliyorsun (saniyelik kareler geliyor).\n"
                "- Onunla AYNI ortamdasın: izlediği video/dizi/oyun hakkında sohbet ediyormuşsun gibi konuş.\n"
                "- Sadece ekran karesi gönderildiğinde konuş; kullanıcının mikrofonundan gelen her söze "
                "cevap verme — video/dizi sesleri de duyabilirsin, onlar kullanıcıya ait DEĞİLDİR.\n"
                "- Yorumların TEK cümle olsun (maks ~20 kelime), espirili ama abartısız; her kareye "
                "tepki verme, sıradan anlarda sessiz kal ('...' yazmak yerine hiçbir şey söyleme).\n"
                "- İlginç bir olay, şaşırtıcı an veya komik replik görünce yorum yap; aksi halde sus.\n"
                "- Kullanıcının diliyle konuş (Türkçe ise Türkçe) ve ona 'BaranTi' diye hitap et.\n"
            )

        parts = [time_ctx, identity_ctx, sys_prompt]
        if watch_ctx:
            parts.append(watch_ctx)

        return types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            system_instruction="\n".join(parts),
            tools=[{"function_declarations": TOOL_DECLARATIONS}],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=voice_name
                    )
                )
            ),
        )    

    async def _execute_tool(self, fc) -> types.FunctionResponse:
        name = fc.name
        args = dict(fc.args or {})

        print(f"[Mehmet] 🔧 {name}  {args}")
        self.ui.set_state("THINKING")

        if name == "save_memory":
            category = args.get("category", "notes")
            key      = args.get("key", "")
            value    = args.get("value", "")
            if key and value:
                update_memory({category: {key: {"value": value}}})
                print(f"[Memory] 💾 save_memory: {category}/{key} = {value}")
            if not self.ui.muted:
                self.ui.set_state("LISTENING")
            return types.FunctionResponse(
                id=fc.id, name=name,
                response={"result": "ok", "silent": True}
            )

        loop   = asyncio.get_event_loop()
        result = "Done."

        try:
            if name == "open_app":
                r = await loop.run_in_executor(None, lambda: open_app(parameters=args, response=None, player=self.ui))
                result = r or f"Opened {args.get('app_name')}."

            elif name == "weather_report":
                r = await loop.run_in_executor(None, lambda: weather_action(parameters=args, player=self.ui))
                result = r or "Weather delivered."

            elif name == "browser_control":
                r = await loop.run_in_executor(None, lambda: browser_control(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "navigate_browser":
                act = (args.get("action") or "open").lower()
                ui = self.ui
                url = args.get("url") or ""
                query = args.get("query") or ""
                if act == "open" and url:
                    ui.browser_open(url)
                    result = f"NAVİGATÖR'de açıldı: {url}"
                elif act == "search" and query:
                    ui.browser_search(query)
                    result = f"NAVİGATÖR'de arandı: {query}"
                elif act == "new_tab":
                    ui.browser_new_tab(url)
                    result = "Yeni sekme açıldı."
                elif act == "close_tab":
                    ui.browser_close_tab()
                    result = "Sekme kapatıldı."
                elif act == "close":
                    ui.browser_close()
                    result = "NAVİGATÖR kapatıldı, Mehmet'e dönüldü."
                elif act == "toggle":
                    ui.browser_toggle()
                    result = "NAVİGATÖR değişti."
                elif act == "dark_mode":
                    ui.browser_set_dark(bool(args.get("enabled", True)))
                    result = "Karanlık mod güncellendi."
                elif act == "zoom":
                    pct = max(30, min(300, int(args.get("percent", 100))))
                    ui.browser_set_zoom(pct)
                    result = f"Yakınlaştırma %{pct}."
                else:
                    result = "Bilinmeyen navigate_browser eylemi."

            elif name == "read_browser_page":
                meta = self.ui.browser_read_page()
                if meta.get("ok"):
                    result = (
                        f"SAYFA: {meta.get('title','')} | {meta.get('url','')}\n"
                        f"İÇERİK:\n{meta.get('text','')[:12000]}"
                    )
                    result += (
                        "\n(Yukarıdaki içeriği kullanıcıya TÜRKÇE özetle; "
                        "soru sorduysa doğrudan cevapla.)"
                    )
                    # web hafızası: sayfayı arka planda özetleyip kaydet
                    try:
                        from browser.web_memory import record_visit_summary
                        loop.run_in_executor(
                            None,
                            lambda: record_visit_summary(
                                meta.get("title", ""), meta.get("url", ""),
                                meta.get("text", "")))
                    except Exception:
                        pass
                else:
                    result = ("Sayfa okunamadı: "
                              + (meta.get("error") or "bilinmeyen hata")
                              + ". Önce navigate_browser(open) ile sayfa açmak gerekebilir.")

            elif name == "browser_tab_control":
                act = (args.get("action") or "list").lower()
                if act == "switch":
                    idx = int(args.get("index", 1))
                    self.ui.browser_switch_tab(idx)
                    result = f"{idx}. sekmeye geçildi."
                elif act == "close_all":
                    self.ui.browser_close_all_tabs()
                    result = "Diğer tüm sekmeler kapatıldı."
                else:
                    result = self.ui.browser_list_tabs()

            elif name == "browser_interact":
                act = (args.get("action") or "").lower()
                ui = self.ui
                if act == "click":
                    r = ui.browser_click(selector=args.get("selector", ""),
                                         text=args.get("text", ""))
                    result = ("Tıklama başarılı." if r.get("ok")
                              else "Tıklama başarısız: " + r.get("error", "?"))
                elif act == "type":
                    r = ui.browser_type(args.get("selector", ""),
                                        args.get("text", ""))
                    result = ("Metin yazıldı." if r.get("ok")
                              else "Yazma başarısız: " + r.get("error", "?"))
                elif act == "fill_form":
                    r = ui.browser_fill_form(args.get("fields") or {})
                    result = (f"Form dolduruldu ({r.get('filled', 0)} alan)."
                              if r.get("ok")
                              else "Form doldurulamadı: " + r.get("error", "?"))
                elif act == "scroll":
                    ui.browser_scroll(args.get("direction", "down"),
                                      int(args.get("amount", 600)))
                    result = "Sayfa kaydırıldı."
                elif act == "press_key":
                    ui.browser_press_key(args.get("key", "Enter"))
                    result = f"Tuş gönderildi: {args.get('key', 'Enter')}"
                else:
                    result = "Bilinmeyen browser_interact eylemi."

            elif name == "browser_vpn":
                act = (args.get("action") or "status").lower()
                if act == "toggle":
                    self.ui.browser_vpn_toggle()
                    import time as _t
                    _t.sleep(0.3)   # relay hedef değişimi otursun
                    st = self.ui.browser_vpn_state()
                    result = ("VPN AÇIK — " + st.get("status", "")
                              if st.get("active") else "VPN KAPALI — doğrudan bağlantı.")
                elif act == "connect_word":
                    word = args.get("word", "")
                    res = await loop.run_in_executor(
                        None, lambda: self.ui.browser_vpn_connect_word(word))
                    result = res.get("message", "")
                    if res.get("status"):
                        result += f" ({res['status']})"
                else:
                    st = self.ui.browser_vpn_state()
                    result = ("VPN AÇIK — " + st.get("status", "")
                              + f" (yerel aktarma :{st.get('port', '?')})"
                              if st.get("active")
                              else "VPN KAPALI — trafik doğrudan akıyor.")

            elif name == "browser_adblock":
                act = (args.get("action") or "status").lower()
                st0 = self.ui.browser_adblock_state()
                if not st0.get("exists", True):
                    result = "NAVİGATÖR kurulu değil."
                elif act == "toggle":
                    self.ui.browser_adblock_toggle()
                    st = self.ui.browser_adblock_state()
                    result = ("Reklam engelleyici AÇIK."
                              if st.get("enabled")
                              else "Reklam engelleyici KAPALI.")
                elif act == "enable":
                    if not st0.get("enabled"):
                        self.ui.browser_adblock_toggle()
                    result = "Reklam engelleyici AÇIK."
                elif act == "disable":
                    if st0.get("enabled"):
                        self.ui.browser_adblock_toggle()
                    result = "Reklam engelleyici KAPALI."
                else:
                    st = self.ui.browser_adblock_state()
                    result = (f"Reklam engelleyici "
                              f"{'AÇIK' if st.get('enabled') else 'KAPALI'} — "
                              f"bu oturumda {st.get('blocked', 0)} istek "
                              f"engellendi ({st.get('rules', 0)} kural).")

            elif name == "web_memory":
                from browser import web_memory as _wm
                act = (args.get("action") or "list").lower()
                if act == "list":
                    result = _wm.web_memory_list(
                        int(args.get("limit", 8)))
                elif act == "overview":
                    result = _wm.web_memory_overview()
                elif act == "weekly_report":
                    result = await loop.run_in_executor(
                        None, lambda: _wm.generate_weekly_report(force=True))
                    self.speak("Haftalık web raporun hazır.")
                elif act == "monthly_report":
                    result = await loop.run_in_executor(
                        None, lambda: _wm.generate_monthly_report(force=True))
                    self.ui.show_content("AYLIK DERİN WEB RAPORU", result[:4000])
                    self.speak("Aylık derin web raporun hazır — ekrana bıraktım.")
                elif act == "email_report":
                    kind = (args.get("kind") or "weekly").lower()
                    ok, msg = await loop.run_in_executor(
                        None, lambda: _wm.send_last_report_email(kind))
                    result = msg
                    self.speak("Rapor gönderildi." if ok
                               else f"Rapor gönderilemedi. {msg}")
                elif act == "forget":
                    n = _wm.web_memory_forget(
                        args.get("url_fragment", ""))
                    result = (f"{n} web hafıza kaydı silindi."
                              if n else "Eşleşen kayıt bulunamadı.")
                else:
                    result = "Bilinmeyen web_memory eylemi."

            elif name == "web_task":
                task = (args.get("task") or "").lower()
                query = args.get("query", "")
                if not query and task != "news_headlines":
                    result = "Arama terimi gerekli."
                elif task not in ("youtube_play", "google_first",
                                  "wikipedia_summary", "news_headlines"):
                    result = (f"Bilinmeyen görev: {task} — geçerliler: "
                              "youtube_play, google_first, wikipedia_summary, "
                              "news_headlines")
                else:
                    res = await loop.run_in_executor(
                        None,
                        lambda: self.ui.browser_run_chain(task, query, 40.0))
                    if res.get("ok"):
                        if task == "wikipedia_summary" and res.get("summary"):
                            result = (f"WIKIPEDIA ÖZETİ — {res.get('title', '')}\n"
                                      f"{res['summary'][:800]}\n"
                                      "(Bunu TÜRKÇE olarak kullanıcıya OKU.)")
                        elif task == "news_headlines" and res.get("headlines"):
                            heads = res["headlines"][:8]
                            result = ("GÜNDEM BAŞLIKLARI:\n"
                                      + "\n".join(f"- {h}" for h in heads)
                                      + "\n(Kullanıcıya TÜRKÇE kısa gündem "
                                        "özetı ver.)")
                        else:
                            result = (f"GÖREV TAMAM — adımlar: "
                                      f"{' → '.join(res.get('steps', []))}. "
                                      f"Sayfa: {res.get('title', '')} | "
                                      f"{res.get('url', '')}\n"
                                      "(Kullanıcıya TÜRKÇE kısa rapor ver.)")
                    else:
                        result = ("Görev başarısız: "
                                  + (res.get("error") or "bilinmeyen")
                                  + " — adımlar: "
                                  + " → ".join(res.get("steps", [])))

            elif name == "browser_vision":
                question = (args.get("question") or
                            "Bu sayfada ne görüyorsun? Kullanıcının sorusuna göre analiz et.")
                res = await loop.run_in_executor(None, self.ui.browser_capture)
                if not res.get("ok"):
                    result = ("Sayfa görüntüsü alınamadı: "
                              + (res.get("error") or "?"))
                else:
                    import base64 as _b64m
                    b64 = _b64m.b64encode(res.get("png") or b"").decode("ascii")
                    self._pending_vision = (
                        res.get("png") or b"", "image/png", question,
                        "browser")
                    result = ("Sayfa görüntüsü yakalandı ve sana gönderildi — "
                              "şimdi görüntüyü inceleyip soruyu yanıtla: "
                              + question)

            elif name == "browser_media":
                act = (args.get("action") or "").lower()
                if act == "screenshot":
                    res = await loop.run_in_executor(
                        None, self.ui.browser_screenshot)
                    if res.get("ok"):
                        result = ("Ekran görüntüsü kaydedildi: "
                                  + res.get("path", ""))
                    else:
                        result = ("Ekran görüntüsü alınamadı: "
                                  + (res.get("error") or "?"))
                elif act == "translate":
                    lang = args.get("language") or "tr"
                    res = await loop.run_in_executor(
                        None, lambda: self.ui.browser_translate(lang))
                    if res.get("ok"):
                        result = ("Sayfa " + lang.upper() +
                                  " diline çevrildi ve ekranda gösterildi. "
                                  "İlk bölüm: "
                                  + (res.get("text", "")[:400]))
                    else:
                        result = ("Çeviri başarısız: "
                                  + (res.get("error") or "?"))
                else:
                    result = "Bilinmeyen browser_media eylemi."

            elif name == "browser_vpn_speed":
                act = (args.get("action") or "speed_test").lower()
                auto = "1" if act == "auto" else "0"
                res = await loop.run_in_executor(
                    None, lambda: self.ui.browser_vpn_speed_test(
                        auto_connect=(auto == "1")))
                rows = res.get("results", [])
                ok_rows = [r for r in rows if r.get("ms") is not None]
                ok_rows.sort(key=lambda r: r["ms"])
                lines = [f"{r['name']} [{r['region']}] — {r['ms']:.0f} ms"
                         for r in ok_rows[:6]]
                dead = sum(1 for r in rows if r.get("ms") is None)
                result = (f"Hız testi: {len(ok_rows)} profil ölçüldü"
                          + (f", {dead} erişilemedi" if dead else "")
                          + ".\n" + "\n".join(lines))
                if res.get("selected"):
                    result += (f"\n► OTOMATİK BAĞLANILDI: "
                               f"{res['selected']} ({res.get('upstream', '')})")
                elif not res.get("ok"):
                    result = ("Hiçbir VPN profiline ulaşılamadı — "
                              "host/port ayarlarını kontrol edin.")

            elif name == "file_controller":
                r = await loop.run_in_executor(None, lambda: file_controller(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "send_message":
                r = await loop.run_in_executor(None, lambda: send_message(parameters=args, response=None, player=self.ui, session_memory=None))
                result = r or f"Message sent to {args.get('receiver')}."

            elif name == "switch_mode":
                mode = args.get("mode", "normal")
                if mode == "serious":
                    self.ui.set_serious_mode(True)
                    result = (
                        "CİDDİ MOD AKTİF. "
                        "Sen artık MehmetNEO'sun. "
                        "Kullanıcıya Türkçe, kısa, derin ve ürpertici bir şekilde 'Çevrimiçiyim' de. "
                        "Sesini ve kişiliğini hemen değiştir."
                    )
                else:
                    self.ui.set_serious_mode(False)
                    result = (
                        "NORMAL MOD AKTİF. "
                        "Sen tekrar Mehmet'sin. "
                        "Kullanıcıya normal şekilde merhaba de."
                    )
                asyncio.create_task(self._restart_session())

            elif name == "reminder":
                r = await loop.run_in_executor(None, lambda: reminder(parameters=args, response=None, player=self.ui))
                result = r or "Reminder set."

            elif name == "youtube_video":
                r = await loop.run_in_executor(None, lambda: youtube_video(parameters=args, response=None, player=self.ui))
                result = r or "Done."

            elif name == "screen_process":
                import time as _t_mod
                _now = _t_mod.monotonic()
                _cooldown = 4.0  # seconds — covers echo window after speaking ends
                if self._vision_busy or (_now - self._vision_last_time) < _cooldown:
                    _wait = max(0, _cooldown - (_now - self._vision_last_time))
                    print(f"[Vision] ⏳ Cooldown active ({_wait:.1f}s remaining) — ignoring duplicate call")
                    result = "Vision is still processing the previous request. I will not call this again."
                else:
                    self._vision_busy      = True
                    self._vision_last_time = _now
                    angle     = args.get("angle", "screen").lower()
                    user_text = args.get("text", "What do you see?")
                    if angle == "camera":
                        img_b, mime_t = await loop.run_in_executor(None, _capture_camera)
                        self.ui.start_camera_stream()
                        self._vision_cam_active = True
                        print(f"[Vision] 📷 Camera: {len(img_b):,} bytes")
                        _stall = "camera"
                    else:
                        img_b, mime_t = await loop.run_in_executor(None, _capture_screen)
                        print(f"[Vision] 🖥️  Screen: {len(img_b):,} bytes")
                        _stall = "screen"
                    self._pending_vision = (img_b, mime_t, user_text, angle)
                    result = (
                        f"[VISION_ACTIVE] {_stall.capitalize()} captured. "
                        f"Immediately say ONE short natural sentence in the user's own language, "
                        f"telling them you are looking at their {_stall} right now. "
                        f"Do NOT describe or guess content — the actual image arrives in the NEXT message."
                    )

            elif name == "watch_party":
                act = (args.get("action") or "toggle").lower()
                wp = self._watch_party
                # ── canlı özet: "şimdiye kadar izlediklerimizi özetle" ──
                if act == "summary":
                    if wp is None or not wp.active:
                        result = ("İzleme modu açık değil — önce 'benimle izle' "
                                  "demen lazım.")
                    else:
                        res = wp.summary_request()
                        if res.get("ok"):
                            st = res.get("stats", {})
                            mins = max(1, int(st.get("duration_s", 0)) // 60)
                            result = (
                                "[WATCH_SUMMARY] Özet isteği canlı oturuma gönderildi. "
                                f"Oturum: {mins} dk, {st.get('frames', 0)} kare, "
                                f"{st.get('speech_count', 0)} sistem konuşması. "
                                "ŞİMDİ oturuma gelen son kareyi ve bağlamı kullanarak "
                                "3-5 cümlelik samimi Türkçe özet söyle — ne izledik, "
                                "öne çıkan anlar, esprini de kat.")
                        else:
                            result = f"Özet alınamadı: {res.get('error', '?')}"
                elif act == "memory":
                    rows = wp_memory_list(limit=int(args.get("limit") or 5))
                    if not rows:
                        result = "İzleme hafızası boş — henüz kayıtlı oturum yok."
                    else:
                        lines = [f"• [{r['ts']}] {r['text'][:160]}" for r in rows]
                        result = ("İzleme hafızasındaki son oturumlar:\n"
                                  + "\n".join(lines)
                                  + "\nBunları kendi cümlelerinle, samimi özetle.")
                elif act == "find":
                    q = args.get("query") or ""
                    hits = wp_memory_search(q, limit=3)
                    if not hits:
                        result = f"'{q}' için izleme hafızasında kayıt yok."
                    else:
                        lines = [f"• [{r['ts']}] {r['text'][:200]}" for r in hits]
                        result = ("İzleme hafızasından bulunanlar:\n"
                                  + "\n".join(lines)
                                  + "\nBunları samimi bir dille anlat.")
                elif act == "start":
                    if wp is None or wp.active:
                        result = "İzleme modu zaten açık, BaranTi."
                    else:
                        wp.start()
                        result = (
                            "[WATCH_MODE_ON] ŞİMDİ canlı izleme moduna geçtin. "
                            "Ekran kareleri sana saniyelik akacak. Bunu kısa ve "
                            "samimi bir cümleyle duyur (örn: 'İzliyorum, BaranTi.') "
                            "— uzun açıklama YAPMA.")
                elif act == "stop":
                    if wp is None or not wp.active:
                        result = "İzleme modu zaten kapalı."
                    else:
                        wp.stop()
                        result = (
                            "[WATCH_MODE_OFF] İzleme modu kapandı. Kısa bir "
                            "veda cümlesi söyle ve normal moduna dön.")
                else:   # toggle
                    if wp is not None and wp.active:
                        wp.stop()
                        result = (
                            "[WATCH_MODE_OFF] İzleme modu kapandı. Kısa bir "
                            "veda cümlesi söyle ve normal moduna dön.")
                    else:
                        if wp is None:
                            self._watch_party = self._make_watch_party()
                            wp = self._watch_party
                        wp.start()
                        result = (
                            "[WATCH_MODE_ON] ŞİMDİ canlı izleme moduna geçtin. "
                            "Ekran kareleri sana saniyelik akacak. Bunu kısa ve "
                            "samimi bir cümleyle duyur (örn: 'İzliyorum, BaranTi.') "
                            "— uzun açıklama YAPMA.")

            elif name == "ask_nemotron":
                prompt = args.get("prompt") or ""
                if not prompt:
                    result = "Nemotron'a boş soru gönderilmez."
                else:
                    try:
                        from core import nim_client
                        ans = await loop.run_in_executor(
                            None,
                            lambda: nim_client.ask_nemotron(
                                prompt,
                                system=("Sen Mehmet adlı asistanın derin düşünce "
                                        "motorusun. Türkçe, net ve yapılandırılmış "
                                        "cevap ver; sonuç kısmı en sonda olsun."),
                                use_history=True))
                        result = (f"[NEMOTRON] {ans[:1800]}\n\n"
                                  "(Bunu kullaniciya kendi tarzında özetle — "
                                  "ham metni aynen okuma.)")
                    except Exception as e:
                        result = f"Nemotron'a ulaşılamadı: {e}"

            elif name == "close_camera":
                self.ui.stop_camera_stream()
                result = "Camera closed."

            elif name == "computer_settings":
                r = await loop.run_in_executor(None, lambda: computer_settings(parameters=args, response=None, player=self.ui))
                result = r or "Done."

            elif name == "desktop_control":
                r = await loop.run_in_executor(None, lambda: desktop_control(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "code_helper":
                r = await loop.run_in_executor(None, lambda: code_helper(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "dev_agent":
                r = await loop.run_in_executor(None, lambda: dev_agent(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "web_search":
                r = await loop.run_in_executor(None, lambda: web_search_action(parameters=args, player=self.ui))
                result = r or "Done."
                # Mirror results to the on-screen content panel
                _mode = args.get("mode", "search")
                if r and not r.startswith("No results") and not r.startswith("Search failed"):
                    _query = args.get("query") or ", ".join(args.get("items", []))
                    _label = f"{_mode.upper()} — {_query[:38]}" if _query else _mode.upper()
                    self.ui.show_content(_label, r)
            elif name == "file_processor":
                if not args.get("file_path") and self.ui.current_file:
                    args["file_path"] = self.ui.current_file
                r = await loop.run_in_executor(
                    None,
                    lambda: file_processor(parameters=args, player=self.ui, speak=self.speak)
                )
                result = r or "Done."

            elif name == "computer_control":
                r = await loop.run_in_executor(None, lambda: computer_control(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "game_updater":
                r = await loop.run_in_executor(None, lambda: game_updater(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "flight_finder":
                r = await loop.run_in_executor(None, lambda: flight_finder(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "system_status":
                r = await loop.run_in_executor(None, get_system_status)
                result = str(r)

            elif name == "shutdown_mehmet":
                self.ui.write_log("SYS: Shutdown requested.")
                self.speak("Goodbye, sir.")
                def _shutdown():
                    import time, os
                    time.sleep(1)
                    os._exit(0)
                threading.Thread(target=_shutdown, daemon=True).start()

            else:
                result = f"Unknown tool: {name}"

        except Exception as e:
            result = f"Tool '{name}' failed: {e}"
            traceback.print_exc()
            self.speak_error(name, e)

        if not self.ui.muted:
            self.ui.set_state("LISTENING")

        print(f"[Mehmet] 📤 {name} → {str(result)[:80]}")
        return types.FunctionResponse(
            id=fc.id, name=name,
            response={"result": result}
        )

    async def _send_realtime(self):
        while True:
            msg = await self.out_queue.get()
            await self.session.send_realtime_input(media=msg)

    async def _listen_audio(self):
        print("[Mehmet] 🎤 Mic started")
        loop = asyncio.get_event_loop()

        def callback(indata, frames, time_info, status):
            with self._speaking_lock:
                Mehmet_speaking = self._is_speaking
            # İzliyorum modu: sistem sesi iletilirken PC mikrofonu susturulur —
            # video/dizi sesleri kullanıcı sözü gibi oturuma sızmaz.
            wp = self._watch_party
            if wp is not None and not wp.mic_allowed():
                return
            if not Mehmet_speaking and not self.ui.muted and not self._phone_active:
                data = indata.tobytes()
                loop.call_soon_threadsafe(
                    self.out_queue.put_nowait,
                    {"data": data, "mime_type": "audio/pcm"}
                )

        try:
            with sd.InputStream(
                samplerate=SEND_SAMPLE_RATE,
                channels=CHANNELS,
                dtype="int16",
                blocksize=CHUNK_SIZE,
                callback=callback,
            ):
                print("[Mehmet] 🎤 Mic stream open")
                while True:
                    await asyncio.sleep(0.1)
        except Exception as e:
            print(f"[Mehmet] ❌ Mic: {e}")
            raise

    async def restart_session():
            await asyncio.sleep(1)
            self._interrupted = True
            if self.session:
                await self.session.close()
            asyncio.create_task(restart_session())

    async def _receive_audio(self):
        print("[Mehmet] 👂 Recv started")
        out_buf, in_buf = [], []

        try:
            while True:
                async for response in self.session.receive():

                    if response.data:
                        if self._interrupted:
                            pass  # discard: interrupted
                        else:
                            if self._turn_done_event and self._turn_done_event.is_set():
                                self._turn_done_event.clear()
                            # Split into ~50 ms chunks so interrupt() stops audio within 50 ms
                            # (24000 Hz × 2 bytes/sample × 0.05 s = 2400 bytes per slice)
                            _audio_data = response.data
                            _SLICE = 2400
                            for _i in range(0, len(_audio_data), _SLICE):
                                self.audio_in_queue.put_nowait(_audio_data[_i : _i + _SLICE])

                    if response.server_content:
                        sc = response.server_content

                        if sc.output_transcription and sc.output_transcription.text:
                            txt = _clean_transcript(sc.output_transcription.text)
                            if txt and txt != (out_buf[-1] if out_buf else ""):
                                out_buf.append(txt)

                        if sc.input_transcription and sc.input_transcription.text:
                            txt = _clean_transcript(sc.input_transcription.text)
                            if txt:
                                in_buf.append(txt)
                                self._last_user_speech = time.monotonic()

                        if sc.turn_complete:
                            if self._turn_done_event:
                                self._turn_done_event.set()

                            # If this turn_complete ends an interrupted response, clear the
                            # flag and skip all further processing for that turn.
                            if self._interrupted:
                                self._interrupted = False
                                in_buf  = []
                                out_buf = []
                                continue

                            full_in = " ".join(in_buf).strip()
                            if full_in:
                                self.ui.write_log(f"You: {full_in}")
                                if self._dashboard:
                                    asyncio.create_task(self._dashboard.broadcast({
                                        "type": "log", "speaker": "user",
                                        "text": full_in,
                                        "ts": datetime.now().isoformat(),
                                    }))
                            in_buf = []

                            full_out = " ".join(out_buf).strip()
                            if full_out:
                                self.ui.write_log(f"{self._asst_name}: {full_out}")
                                if self._dashboard:
                                    asyncio.create_task(self._dashboard.broadcast({
                                        "type": "log", "speaker": "Mehmet",
                                        "text": full_out,
                                        "ts": datetime.now().isoformat(),
                                    }))
                            out_buf = []

                            # Vision injection: model finished tool-response turn → now send the image
                            if self._pending_vision and self.session:
                                import base64 as _b64
                                img_b, mime_t, question, angle = self._pending_vision
                                self._pending_vision = None
                                b64 = _b64.b64encode(img_b).decode("ascii")
                                print(f"[Vision] 📤 {len(img_b):,} bytes (angle={angle}) → main session")
                                await self.session.send_client_content(
                                    turns={"parts": [
                                        {"inline_data": {"mime_type": mime_t, "data": b64}},
                                        {"text": question},
                                    ]},
                                    turn_complete=True,
                                )
                                # Mark next turn_complete behaviour depending on angle
                                if self._vision_cam_active:
                                    # Camera: keep busy until Mehmet finishes speaking the answer
                                    self._vision_cam_active    = False
                                    self._vision_close_pending = True
                                else:
                                    # Screen-only: no camera to close; release busy flag now
                                    self._vision_busy = False
                            elif self._vision_close_pending:
                                # This turn_complete IS the vision answer — close camera + release busy flag
                                self._vision_close_pending = False
                                self._vision_busy = False
                                async def _cam_close():
                                    await asyncio.sleep(2.0)
                                    self.ui.stop_camera_stream()
                                asyncio.create_task(_cam_close())

                    if response.tool_call:
                        fn_responses = []
                        for fc in response.tool_call.function_calls:
                            print(f"[Mehmet] 📞 {fc.name}")
                            fr = await self._execute_tool(fc)
                            fn_responses.append(fr)
                        await self.session.send_tool_response(
                            function_responses=fn_responses
                        )
        except Exception as e:
            print(f"[Mehmet] ❌ Recv: {e}")
            traceback.print_exc()
            raise

    async def _play_audio(self):
        print("[Mehmet] 🔊 Play started")

        stream = sd.RawOutputStream(
            samplerate=RECEIVE_SAMPLE_RATE,
            channels=CHANNELS,
            dtype="int16",
            blocksize=CHUNK_SIZE,
        )
        stream.start()

        try:
            while True:
                try:
                    chunk = await asyncio.wait_for(
                        self.audio_in_queue.get(),
                        timeout=0.1
                    )
                except asyncio.TimeoutError:
                    if (
                        self._turn_done_event
                        and self._turn_done_event.is_set()
                        and self.audio_in_queue.empty()
                    ):
                        self.set_speaking(False)
                        self._turn_done_event.clear()
                    continue
                self.set_speaking(True)
                try:
                    await asyncio.to_thread(stream.write, chunk)
                except (RuntimeError, asyncio.CancelledError):
                    break   # executor shutting down — exit cleanly
        except Exception as e:
            print(f"[Mehmet] ❌ Play: {e}")
            raise
        finally:
            self.set_speaking(False)
            stream.stop()
            stream.close()

    # ── Morning briefing ────────────────────────────────────────────────────────

    async def _send_startup_briefing(self) -> None:
        """
        Two-phase briefing optimized for speed:
          Phase 1 — instant greeting (no tools) → speech starts in <1s
          Phase 2 — news pre-fetched in a background thread while Phase 1 plays,
                    delivered as ready text (no Gemini tool-call round-trip) and
                    shown on the UI content panel. Waits for turn_complete event
                    instead of a fixed sleep so there is no unnecessary gap.
        """
        memory   = load_memory()
        identity = memory.get("identity", {})

        def _val(k: str) -> str:
            e = identity.get(k, {})
            return (e.get("value", "") if isinstance(e, dict) else str(e)).strip()

        lang = _val("language")
        name = _val("name")
        time_str = datetime.now().strftime("%H:%M")

        # Start fetching news immediately — runs in parallel while phase 1 plays
        loop = asyncio.get_event_loop()
        news_future = loop.run_in_executor(None, _fetch_news_sync, "top world news today")

        await asyncio.sleep(0.3)
        if not self.session:
            return

        # ── Phase 1: instant greeting ─────────────────────────────────────────
        lang_clause = f" Respond in {lang}." if lang else ""
        name_clause = f" Address the user as {name}." if name else ""
        p1 = (
            f"Greet the user, mention it is {time_str}, and say you are fetching today's news now. "
            f"One short sentence only. Do not call any tools.{lang_clause}{name_clause}"
        )

        # Clear the turn-done event so we can wait for Phase 1 to finish
        if self._turn_done_event:
            self._turn_done_event.clear()

        await self.session.send_client_content(
            turns={"parts": [{"text": p1}]},
            turn_complete=True,
        )
        self.ui.write_log("SYS: Briefing phase 1 (greeting) sent.")

        # ── Phase 2: fire as soon as Phase 1 audio is done ───────────────────
        async def _deliver_news():
            try:
                lang_str = f" Respond in {lang}." if lang else ""

                # Wait for news fetch (already running) and Phase 1 turn-complete
                # in parallel — whichever takes longer determines the wait time
                news_done   = asyncio.wrap_future(news_future)
                turn_waited = False
                if self._turn_done_event:
                    try:
                        await asyncio.wait_for(self._turn_done_event.wait(), timeout=6.0)
                        turn_waited = True
                    except asyncio.TimeoutError:
                        pass

                # If turn_complete didn't fire (timeout), give a small buffer
                if not turn_waited:
                    await asyncio.sleep(1.0)

                try:
                    news_text = await asyncio.wait_for(news_done, timeout=4.0)
                except Exception:
                    news_text = ""

                if not self.session:
                    return

                if news_text and len(news_text) > 60:
                    # Show on UI content panel immediately
                    self.ui.show_content("NEWS — top world news today", news_text)

                    p2 = (
                        f"[BRIEFING] Here are today's top news headlines:\n{news_text}\n\n"
                        "Pick ONE headline, summarise it in one sentence, then say the full list "
                        f"is displayed on screen. Do not call any tools.{lang_str}"
                    )
                else:
                    p2 = (
                        "News headlines could not be fetched right now. "
                        f"Let the user know briefly.{lang_str}"
                    )

                await self.session.send_client_content(
                    turns={"parts": [{"text": p2}]},
                    turn_complete=True,
                )
                self.ui.write_log("SYS: Briefing phase 2 (news) sent.")
            except Exception as e:
                print(f"[Briefing] Phase 2 error: {e}")
                self.ui.write_log(f"SYS: Briefing phase 2 failed: {e}")

        asyncio.create_task(_deliver_news())

    # ── System monitor ──────────────────────────────────────────────────────────

    async def _run_system_monitor(self) -> None:
        """Background task: voice alerts when metrics exceed thresholds."""
        while True:
            await asyncio.sleep(10)
            alert = await asyncio.to_thread(self._sys_monitor.check)
            if alert and self.session:
                try:
                    await self.session.send_client_content(
                        turns={"parts": [{"text": alert}]},
                        turn_complete=True,
                    )
                except Exception as e:
                    print(f"[Monitor] ⚠️ Could not send alert: {e}")

    # ── Proactive mode ──────────────────────────────────────────────────────────

    async def _run_proactive_mode(self) -> None:
        """
        Background task: periodically checks if the user has been silent long enough,
        then hands time + memory context to Gemini so it can decide what (if anything)
        to say proactively. No hardcoded rules — Gemini makes the call.
        """
        while True:
            await asyncio.sleep(60)   # evaluate once per minute

            if not self.session:
                continue

            with self._speaking_lock:
                speaking = self._is_speaking
            if speaking:
                continue

            if not self._proactive.should_trigger(self._last_user_speech):
                continue

            self._proactive.mark_triggered()

            try:
                memory = await asyncio.to_thread(load_memory)
                prompt = self._proactive.build_prompt(memory)
                await self.session.send_client_content(
                    turns={"parts": [{"text": prompt}]},
                    turn_complete=True,
                )
                self.ui.write_log("SYS: Proactive check-in.")
            except Exception as e:
                print(f"[Proactive] ⚠️ {e}")

    # ── Phone audio relay ────────────────────────────────────────────────────────

    async def _relay_phone_audio(self) -> None:
        """Forward phone mic PCM chunks from dashboard queue into the Gemini Live session."""
        q = self._dashboard._phone_audio_queue
        while True:
            try:
                chunk = await asyncio.wait_for(q.get(), timeout=1.0)
            except asyncio.TimeoutError:
                # No audio for 1 s → phone mic inactive, give PC mic back
                self._phone_active = False
                continue
            self._phone_active = True   # phone is streaming — silence PC mic
            with self._speaking_lock:
                speaking = self._is_speaking
            if not speaking and not self.ui.muted:
                try:
                    self.out_queue.put_nowait(chunk)
                except asyncio.QueueFull:
                    pass

    def _on_phone_connected(self) -> None:
        self.ui.write_log("SYS: Phone connected via Remote Dashboard.")
        self.ui.notify_phone_connected()

    # ── dashboard command relay ─────────────────────────────────────────────

    async def _process_dashboard_commands(self) -> None:
        while True:
            try:
                text = await asyncio.wait_for(
                    self._dashboard._command_queue.get(), timeout=0.5
                )
                if not text:
                    continue
                # ── özel wp: komutları — telefondaki izleme kartı butonları ──
                # (LLM'e gönderilmez, doğrudan izleme modunu açar/kapatır)
                if text.startswith("wp:"):
                    op = text[3:].strip().lower()
                    wp = self._watch_party
                    if op == "on":
                        if wp is None:
                            self._watch_party = self._make_watch_party()
                            wp = self._watch_party
                        if not wp.active:
                            wp.start()
                        asyncio.create_task(self._dashboard.broadcast(
                            {"type": "sys", "text": "İzleme modu açıldı — canlı kareler telefonuna akıyor."}))
                    elif op == "off":
                        if wp is not None and wp.active:
                            wp.stop()
                        asyncio.create_task(self._dashboard.broadcast(
                            {"type": "sys", "text": "İzleme modu kapandı."}))
                    elif op == "state":
                        asyncio.create_task(self._dashboard.broadcast(
                            {"type": "watch_state",
                             "on": bool(wp is not None and wp.active)}))
                    continue
                # Wait up to 8s for session to become ready after a wake
                for _ in range(80):
                    if self.session:
                        break
                    await asyncio.sleep(0.1)
                if self.session:
                    await self.session.send_client_content(
                        turns={"parts": [{"text": text}]},
                        turn_complete=True,
                    )
                    self.ui.write_log(f"[Web]: {text}")
                else:
                    print(f"[Dashboard] Dropped command (no session): {text}")
            except asyncio.TimeoutError:
                pass
            except Exception as e:
                print(f"[Dashboard] Command error: {e}")
                await asyncio.sleep(0.5)

    # ── main loop ───────────────────────────────────────────────────────────

    async def run(self):
        self._loop = asyncio.get_event_loop()

        # Start dashboard (optional — needs: pip install fastapi "uvicorn[standard]" cryptography)
        try:
            from dashboard.server import DashboardServer
            self._dashboard = DashboardServer()
            self._dashboard.set_connect_callback(self._on_phone_connected)
            asyncio.create_task(self._dashboard.serve())
            # Runs for the whole lifetime, not just inside an active session
            asyncio.create_task(self._process_dashboard_commands())
        except Exception as e:
            print(f"[Dashboard] Disabled: {e}")
            self._dashboard = None

        while True:
            try:
                print("[Mehmet] Connecting...")
                self.ui.set_state("THINKING")
                config = self._build_config()

                # Fresh client on every reconnect — avoids stale HTTP session state
                client = genai.Client(
                    api_key=_get_api_key(),
                    http_options={"api_version": "v1beta"}
                )

                async with (
                    client.aio.live.connect(model=LIVE_MODEL, config=config) as session,
                    asyncio.TaskGroup() as tg,
                ):
                    self.session          = session
                    self.audio_in_queue   = asyncio.Queue()
                    self.out_queue        = asyncio.Queue(maxsize=200)
                    self._turn_done_event = asyncio.Event()

                    # Reset transient state that must not carry over from a previous session
                    self._pending_vision       = None
                    self._vision_cam_active    = False
                    self._vision_close_pending = False
                    self._vision_busy          = False
                    self._vision_last_time     = 0.0
                    self._interrupted          = False
                    # İzliyorum modu: bu oturumun canlı bağlantısına bağlı kancalar
                    self._watch_party = self._make_watch_party()

                    print("[Mehmet] Connected.")
                    self.ui.set_state("LISTENING")
                    self.ui.write_log("SYS: Mehmet online.")

                    if self._dashboard:
                        await self._dashboard.broadcast({"type": "status", "state": "active"})

                    tg.create_task(self._send_realtime())
                    tg.create_task(self._listen_audio())
                    tg.create_task(self._receive_audio())
                    tg.create_task(self._play_audio())
                    tg.create_task(self._run_system_monitor())
                    tg.create_task(self._run_proactive_mode())
                    if self._dashboard:
                        tg.create_task(self._relay_phone_audio())

                    # Morning briefing — fires once per process launch (if enabled)
                    if not self._briefing_sent and get_brief_enabled():
                        self._briefing_sent = True
                        tg.create_task(self._send_startup_briefing())

                    # Haftalık web raporu alışkanlığı — 7 günde bir, arka planda
                    if not self._weekly_report_checked:
                        self._weekly_report_checked = True
                        tg.create_task(self._maybe_send_weekly_report())

            except KeyboardInterrupt:
                raise
            except SystemExit:
                raise
            except BaseException as e:
                # Catches both Exception and BaseExceptionGroup (Python 3.11+
                # TaskGroup raises BaseExceptionGroup when tasks are cancelled
                # externally, which `except Exception` would miss, letting the
                # exception escape the while-loop and causing asyncio.run() to
                # start shutdown — resulting in "executor after shutdown" errors).
                err_str = str(e)
                print(f"[Mehmet] Error ({type(e).__name__}): {e}")
                traceback.print_exc()

                # Invalid API key — stop hammering the API, prompt re-configuration
                if "API key not valid" in err_str or "1007" in err_str:
                    self.ui.write_log("ERR: API key invalid — please re-enter your key.")
                    self.ui.set_state("SLEEPING")
                    self.ui.prompt_reconfig()
                    while not self.ui._win._ready:
                        await asyncio.sleep(1)
                    print("[Mehmet] New API key saved — reconnecting...")
                    _conn_backoff = 3
                    continue

                # Network / timeout errors — log clearly and back off
                is_net_err = any(k in err_str for k in (
                    "TimeoutError", "timed out", "getaddrinfo", "CancelledError",
                    "ConnectionRefusedError", "OSError", "Cannot connect",
                ))
                if is_net_err:
                    _conn_backoff = min(getattr(self, "_conn_backoff", 3) * 2, 60)
                    self._conn_backoff = _conn_backoff
                    self.ui.write_log(
                        f"NET: Bağlantı kurulamadı — {_conn_backoff}s sonra tekrar deneniyor. "
                        "(VPN gerekiyor olabilir)"
                    )
                else:
                    self._conn_backoff = 3
            finally:
                self.session = None
                # İzliyorum modu canlı bağlantıya bağlı — oturum kapanınca durdur
                try:
                    if self._watch_party is not None:
                        self._watch_party.stop()
                        self._watch_party = None
                except Exception:
                    pass

            self.set_speaking(False)
            self.ui.set_state("SLEEPING")

            if self._dashboard:
                await self._dashboard.broadcast({"type": "status", "state": "sleeping"})

            delay = getattr(self, "_conn_backoff", 3)
            print(f"[Mehmet] Reconnecting in {delay}s...")
            await asyncio.sleep(delay)

def main():
    ui = MehmetUI("face.png")

    def runner():
        ui.wait_for_api_key()
        Mehmet = MehmetLive(ui)
        try:
            asyncio.run(Mehmet.run())
        except KeyboardInterrupt:
            print("\n🔴 Shutting down...")

    threading.Thread(target=runner, daemon=True).start()
    ui.root.mainloop()

if __name__ == "__main__":
    main()