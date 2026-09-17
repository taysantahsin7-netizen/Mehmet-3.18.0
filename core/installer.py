"""
MARK XL — Dependency auto-installer.

Called automatically on first launch and after engine reconfiguration.
Installs only the packages that are actually missing, then exits cleanly.
"""
from __future__ import annotations

import importlib.util
import platform
import subprocess
import sys
from typing import Callable

# ── Package lists ─────────────────────────────────────────────────────────
# Each entry: (import_name, pip_package_name)

_CORE: list[tuple[str, str]] = [
    ("psutil",             "psutil"),
    ("PIL",                "pillow"),
    ("sounddevice",        "sounddevice"),
    ("numpy",              "numpy"),
    ("requests",           "requests"),
    ("bs4",                "beautifulsoup4"),
    ("duckduckgo_search",  "duckduckgo-search"),
    ("pyautogui",          "pyautogui"),
    ("pyperclip",          "pyperclip"),
    ("pygetwindow",        "pygetwindow"),
    ("mss",                "mss"),
    ("cv2",                "opencv-python"),
    ("soundfile",          "soundfile"),
    ("miniaudio",          "miniaudio"),
    ("send2trash",         "send2trash"),
    ("pptx",               "python-pptx"),
    ("youtube_transcript_api", "youtube-transcript-api"),
    # NAVİGATÖR (dahili tarayıcı) çekirdeği
    ("PyQt6.QtWebEngineWidgets", "PyQt6-WebEngine"),
    # ── requirements.txt ile senkron blok ──────────────────────────────
    ("PyQt6",              "PyQt6"),
    # yapay zekâ çekirdeği (Gemini Live)
    ("google.genai",       "google-genai"),
    # MCP istemcisi (mcp_manager, mcp_unity)
    ("mcp",                "mcp"),
    # web_search.py yeni ddgs import'u
    ("ddgs",               "ddgs"),
    # GPU / donanım izleme
    ("pynvml",             "nvidia-ml-py"),
    # doküman işleme (file_processor)
    ("pandas",             "pandas"),
    ("openpyxl",           "openpyxl"),
    ("pdfplumber",         "pdfplumber"),
    ("PyPDF2",             "PyPDF2"),
    ("docx",               "python-docx"),
    # telefon paneli (dashboard server)
    ("fastapi",            "fastapi"),
    ("uvicorn",            "uvicorn"),
    ("cryptography",       "cryptography"),
    ("multipart",          "python-multipart"),
    # QR / medya yardımcıları
    ("qrcode",             "qrcode"),
    ("gtts",               "gTTS"),
    ("pydub",              "pydub"),
]

# Windows-only (pywinauto, pycaw, win10toast, comtypes, soundcard, wmi)
_WINDOWS: list[tuple[str, str]] = [
    ("comtypes",   "comtypes"),
    ("pycaw",      "pycaw"),
    ("win10toast", "win10toast"),
    ("pywinauto",  "pywinauto"),
    ("soundcard",  "SoundCard"),   # izleme modu WASAPI loopback
    ("wmi",        "wmi"),          # system_monitor donanım sorguları
]

# STT engine packages
_STT: dict[str, list[tuple[str, str]]] = {
    # torch: faster_whisper GPU tespiti + kokoro — BÜYÜK (~2.5 GB CUDA)
    "whisper": [("faster_whisper", "faster-whisper"), ("torch", "torch")],
    "vosk":    [("vosk",           "vosk")],
}

# TTS engine packages
_TTS: dict[str, list[tuple[str, str]]] = {
    "edgetts":    [("edge_tts", "edge-tts")],
    # kokoro>=0.9 dropped AlbertModel/AutoModel from transformers — version pin is critical
    # NOT: kokoro 0.9.x yalnızca Python 3.10-3.12'yi destekler (3.13+ için edgetts kullan)
    "kokoro":     [("kokoro",   "kokoro>=0.9"), ("soundfile", "soundfile"),
                   ("torch",    "torch")],
    "elevenlabs": [],   # uses only requests, already in core
}


# ── Helpers ───────────────────────────────────────────────────────────────

def _available(module: str) -> bool:
    """Return True if the module can be imported (no actual import).
    Dotted names (e.g. "google.genai") raise ModuleNotFoundError when the
    parent package is missing entirely — treat that as "not available"."""
    try:
        return importlib.util.find_spec(module) is not None
    except Exception:
        return False


def _pip(package: str, log: Callable | None = None) -> bool:
    if log:
        log(f"SYS: pip install {package} …")
    result = subprocess.run(
        [
            sys.executable, "-m", "pip", "install", package,
            "--quiet", "--disable-pip-version-check",
        ],
        capture_output=True,
    )
    ok = result.returncode == 0
    if not ok and log:
        stderr = result.stderr.decode(errors="replace").strip()
        log(f"ERR: {package} install failed — {stderr[:140]}")
    return ok


# ── Public API ────────────────────────────────────────────────────────────

def install_for_config(config: dict, log: Callable | None = None) -> None:
    """
    Install all missing packages required by *config*.

    Blocking — always call from a background thread.
    Progress is reported via the optional *log* callback (receives a str).
    """
    stt = config.get("stt_engine", "whisper").lower()
    tts = config.get("tts_engine", "edgetts").lower()

    needed: list[tuple[str, str]] = list(_CORE)
    needed += _STT.get(stt, [])
    needed += _TTS.get(tts, [])
    if platform.system() == "Windows":
        needed += _WINDOWS

    # Deduplicate (preserve order, key = pip name)
    seen: set[str] = set()
    unique: list[tuple[str, str]] = []
    for mod, pkg in needed:
        if pkg not in seen:
            seen.add(pkg)
            unique.append((mod, pkg))

    missing = [(mod, pkg) for mod, pkg in unique if not _available(mod)]

    if not missing:
        if log:
            log("SYS: All dependencies already installed ✓")
        return

    pkg_names = ", ".join(p for _, p in missing)
    if log:
        log(f"SYS: Installing {len(missing)} package(s): {pkg_names}")

    for _mod, pkg in missing:
        _pip(pkg, log)

    # Playwright: install the package + download Chromium browser
    if not _available("playwright"):
        _pip("playwright", log)
        if log:
            log("SYS: Downloading Playwright browser (Chromium, ~150 MB — one-time)…")
        subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            capture_output=True,
        )
        if log:
            log("SYS: Playwright browser ready.")

    if log:
        log("SYS: All dependencies ready ✓")
