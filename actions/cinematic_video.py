"""
actions/cinematic_video.py
──────────────────────────────────────────────────────────────────────────────
NİKO AI — Sinematik Video Üretim Motoru
Pipeline: Gemini (senaryo) → Pollinations.AI FLUX (görseller) → FFmpeg (video)

%100 ÜCRETSİZ:
  - Pollinations.AI → API key yok, kayıt yok, kota yok
  - FFmpeg          → açık kaynak, ücretsiz
  - Gemini          → mevcut API key'iniz

Kurulum:
    pip install requests pillow gtts pydub
    winget install FFmpeg
"""

import os
import io
import json
import time
import tempfile
import subprocess
import requests
import urllib.parse
from pathlib import Path
from datetime import datetime

try:
    from PIL import Image, ImageDraw, ImageFont
    PIL_OK = True
except ImportError:
    PIL_OK = False

try:
    from google import genai
    GENAI_OK = True
except ImportError:
    GENAI_OK = False

try:
    from gtts import gTTS
    GTTS_OK = True
except ImportError:
    GTTS_OK = False

try:
    from pydub import AudioSegment
    PYDUB_OK = True
except ImportError:
    PYDUB_OK = False

# ── Sabitler ──────────────────────────────────────────────────────────────────
BASE_DIR           = Path(__file__).resolve().parent.parent
API_CONFIG_PATH    = BASE_DIR / "config" / "api_keys.json"
OUTPUT_DIR         = Path.home() / "Videos" / "NikoVideos"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_SCENE_DURATION = 8
FFMPEG_PRESET          = "fast"
VIDEO_RESOLUTION       = (1280, 720)
POLLINATIONS_BASE      = "https://image.pollinations.ai/prompt/"
POLLINATIONS_MODEL     = "flux"


def _load_keys() -> dict:
    try:
        with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


# ══════════════════════════════════════════════════════════════════════════════
# YARDIMCI FONKSİYONLAR
# ══════════════════════════════════════════════════════════════════════════════

def create_srt(scenes: list, output_file: Path):
    """Sahnelerden SRT altyazı dosyası oluşturur."""
    current = 0
    with open(output_file, "w", encoding="utf-8") as f:
        for i, scene in enumerate(scenes, start=1):
            duration = int(scene.get("duration", DEFAULT_SCENE_DURATION))
            start = current
            end   = current + duration

            def fmt(sec):
                h = sec // 3600
                m = (sec % 3600) // 60
                s = sec % 60
                return f"{h:02}:{m:02}:{s:02},000"

            f.write(f"{i}\n")
            f.write(f"{fmt(start)} --> {fmt(end)}\n")
            f.write(scene.get("narration", "") + "\n\n")
            current += duration


def generate_voice(text: str, output_path: Path, language: str = "tr") -> bool:
    """gTTS ile metni sese dönüştürür. Kütüphane yoksa sessizce atlar."""
    if not GTTS_OK:
        return False
    try:
        tts = gTTS(text=text, lang=language)
        tts.save(str(output_path))
        return True
    except Exception:
        return False


def create_background_music(path: Path, duration_sec: int) -> bool:
    """pydub ile sessiz arka plan müziği oluşturur (placeholder)."""
    if not PYDUB_OK:
        return False
    try:
        audio = AudioSegment.silent(duration=duration_sec * 1000)
        audio.export(str(path), format="mp3")
        return True
    except Exception:
        return False


# ══════════════════════════════════════════════════════════════════════════════
# 1) SENARYO ÜRETİCİ  (Gemini)
# ══════════════════════════════════════════════════════════════════════════════
def generate_script(topic, duration_minutes, style, language, keys) -> list:
    scene_count = max(1, (duration_minutes * 60) // DEFAULT_SCENE_DURATION)

    system = (
        "You are a professional cinematic screenplay writer and image prompt engineer. "
        "Return ONLY a valid JSON array, no markdown, no extra text."
    )
    user = f"""
Create a cinematic video script about: "{topic}"
Video style: {style}
Total scenes needed: {scene_count}
Narration language: {language}

Return a JSON array with exactly {scene_count} objects, each having:
- "scene": integer scene number
- "description": short scene description in {language} (1-2 sentences)
- "prompt_en": detailed English image prompt for FLUX AI. Include subject, environment,
               lighting, mood, camera angle, color palette. No text or watermarks.
               Example: "wide cinematic shot of a massive alien spacecraft hovering over
               a futuristic city at night, neon reflections on wet streets, dramatic
               volumetric fog, cool blue and purple tones, photorealistic 8k, epic scale,
               anamorphic bokeh, no text"
- "narration": voice-over text in {language} (1-3 sentences)
- "duration": {DEFAULT_SCENE_DURATION}
"""

    if not GENAI_OK:
        raise RuntimeError("google-genai kütüphanesi kurulu değil.")

    client = genai.Client(api_key=keys.get("gemini_api_key", ""))

    # Model fallback zinciri — kota doluysa sonrakini dene
    models_to_try = [
        "gemini-2.0-flash",
        "gemini-1.5-flash",
        "gemini-1.5-flash-8b",
        "gemini-1.0-pro",
    ]

    last_error = None
    for model_name in models_to_try:
        try:
            response = client.models.generate_content(
                model=model_name,
                config=genai.types.GenerateContentConfig(system_instruction=system),
                contents=user,
            )
            raw = response.text.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            return json.loads(raw.strip())
        except Exception as e:
            err_str = str(e).lower()
            if any(x in err_str for x in ["quota", "429", "resource", "exhausted", "limit"]):
                last_error = e
                time.sleep(2)
                continue
            raise

    raise RuntimeError(f"Tüm Gemini modelleri kota limitine ulaşti: {last_error}")


# ══════════════════════════════════════════════════════════════════════════════
# 2) GÖRSEL ÜRETİCİ  (Pollinations.AI)
# ══════════════════════════════════════════════════════════════════════════════
def generate_image_pollinations(prompt, output_path, scene_index=0, player=None, retries=3) -> bool:
    encoded_prompt = urllib.parse.quote(prompt, safe="")
    url = (
        f"{POLLINATIONS_BASE}{encoded_prompt}"
        f"?width={VIDEO_RESOLUTION[0]}"
        f"&height={VIDEO_RESOLUTION[1]}"
        f"&model={POLLINATIONS_MODEL}"
        f"&nologo=true"
        f"&seed={scene_index * 42 + 7}"
    )

    for attempt in range(retries):
        try:
            _log(player, f"[Video] 🌸 Pollinations isteği (deneme {attempt+1})...")
            resp = requests.get(url, timeout=90)

            if resp.status_code == 200:
                content_type = resp.headers.get("content-type", "")
                if "image" in content_type:
                    img = Image.open(io.BytesIO(resp.content))
                    img = img.resize(VIDEO_RESOLUTION, Image.LANCZOS)
                    img.save(str(output_path), "PNG")
                    return True
                else:
                    _log(player, f"[Video] ⚠️  Beklenmeyen yanıt: {content_type}")
            elif resp.status_code == 429:
                wait = 10 * (attempt + 1)
                _log(player, f"[Video] ⏳ Rate limit, {wait}sn bekleniyor...")
                time.sleep(wait)
            elif resp.status_code == 503:
                _log(player, "[Video] ⏳ Sunucu meşgul, 15sn bekleniyor...")
                time.sleep(15)
            else:
                _log(player, f"[Video] ⚠️  HTTP {resp.status_code}")
                time.sleep(5)

        except requests.exceptions.Timeout:
            _log(player, f"[Video] ⚠️  Timeout (deneme {attempt+1}/{retries})")
            time.sleep(5)
        except Exception as e:
            _log(player, f"[Video] ⚠️  Hata: {e}")
            time.sleep(3)

    return False


# ── Placeholder görsel ────────────────────────────────────────────────────────
def generate_image_placeholder(scene: dict, output_path: Path) -> bool:
    if not PIL_OK:
        _create_black_image(output_path)
        return True

    w, h = VIDEO_RESOLUTION
    palettes = [
        [(5,  5,  30), (20, 10, 70)],
        [(30, 5,  5),  (70, 15, 10)],
        [(5,  25, 35), (10, 55, 75)],
        [(5,  30, 10), (10, 65, 25)],
        [(30, 25, 5),  (65, 50, 10)],
        [(25, 5,  35), (55, 10, 75)],
    ]
    idx = scene.get("scene", 1) % len(palettes)
    top_c, bot_c = palettes[idx]

    img = Image.new("RGB", (w, h), top_c)
    draw = ImageDraw.Draw(img)

    for y in range(h):
        t = y / h
        r = int(top_c[0] + (bot_c[0] - top_c[0]) * t)
        g = int(top_c[1] + (bot_c[1] - top_c[1]) * t)
        b = int(top_c[2] + (bot_c[2] - top_c[2]) * t)
        draw.line([(0, y), (w, y)], fill=(r, g, b))

    import random
    rng = random.Random(scene.get("scene", 1))
    for _ in range(250):
        sx = rng.randint(0, w)
        sy = rng.randint(0, h // 2)
        br = rng.randint(80, 255)
        rr = rng.randint(0, 1)
        draw.ellipse([sx-rr, sy-rr, sx+rr+1, sy+rr+1], fill=(br, br, br))

    try:
        font_l = ImageFont.truetype("arial.ttf", 30)
        font_s = ImageFont.truetype("arial.ttf", 18)
    except Exception:
        font_l = ImageFont.load_default()
        font_s = font_l

    desc = scene.get("description", "")[:65]
    bbox = draw.textbbox((0, 0), desc, font=font_l)
    draw.text(((w - (bbox[2]-bbox[0])) // 2, h//2 - 28), desc, fill=(200, 200, 225), font=font_l)

    label = f"Sahne {scene.get('scene', '?')}"
    bbox2 = draw.textbbox((0, 0), label, font=font_s)
    draw.text(((w - (bbox2[2]-bbox2[0])) // 2, h//2 + 18), label, fill=(110, 110, 155), font=font_s)

    img.save(str(output_path), "PNG")
    return True


def _create_black_image(output_path: Path):
    import struct, zlib
    w, h = VIDEO_RESOLUTION
    def chunk(name, data):
        c = zlib.crc32(name + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + name + data + struct.pack(">I", c)
    raw_data = zlib.compress((b"\x00" + b"\x00\x00\x00" * w) * h)
    with open(output_path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)))
        f.write(chunk(b"IDAT", raw_data))
        f.write(chunk(b"IEND", b""))


# ══════════════════════════════════════════════════════════════════════════════
# 3) VIDEO BİRLEŞTİRİCİ  (FFmpeg)
# ══════════════════════════════════════════════════════════════════════════════
def check_ffmpeg() -> bool:
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False


def build_video(image_paths, durations, output_path, srt_file=None, music_file=None, player=None) -> bool:
    if not check_ffmpeg():
        raise RuntimeError(
            "FFmpeg bulunamadı.\n"
            "Kurulum: winget install FFmpeg\n"
            "Manuel: https://ffmpeg.org/download.html"
        )

    concat_file = output_path.parent / "niko_concat_list.txt"
    with open(concat_file, "w", encoding="utf-8") as f:
        for img, dur in zip(image_paths, durations):
            f.write(f"file '{Path(img).as_posix()}'\n")
            f.write(f"duration {dur}\n")
        if image_paths:
            f.write(f"file '{Path(image_paths[-1]).as_posix()}'\n")

    _log(player, f"[Video] 🎬 FFmpeg birleştiriyor ({len(image_paths)} sahne)...")

    # Temel video filtresi
    vf = (
        f"scale={VIDEO_RESOLUTION[0]*2}:{VIDEO_RESOLUTION[1]*2},"
        f"zoompan=z='min(zoom+0.0015,1.3)':d=192:fps=24,"
        f"scale={VIDEO_RESOLUTION[0]}:{VIDEO_RESOLUTION[1]},"
        f"fade=t=in:st=0:d=1"
    )

    # Altyazı varsa ekle
    if srt_file and Path(srt_file).exists():
        srt_posix = Path(srt_file).as_posix().replace(":", "\\:")
        vf += f",subtitles='{srt_posix}'"

    cmd = [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(concat_file),
    ]

    # Müzik varsa ekle
    if music_file and Path(music_file).exists():
        cmd += ["-i", str(music_file)]
        cmd += ["-map", "0:v", "-map", "1:a", "-shortest"]

    cmd += [
        "-vf", vf,
        "-c:v", "libx264",
        "-preset", FFMPEG_PRESET,
        "-crf", "22",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        str(output_path),
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    concat_file.unlink(missing_ok=True)

    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg hatası:\n{result.stderr[-600:]}")
    return True


# ══════════════════════════════════════════════════════════════════════════════
# 4) ANA ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════
def cinematic_video(parameters: dict, player=None, speak=None) -> str:
    topic            = parameters.get("topic", "A cinematic journey through space")
    duration_minutes = int(parameters.get("duration_minutes", 2))
    style            = parameters.get("style", "cinematic dramatic epic sci-fi")
    language         = parameters.get("language", "Turkish")
    custom_output    = parameters.get("output_path", "")
    use_ai_images    = parameters.get("use_ai_images", True)

    duration_minutes = max(1, min(duration_minutes, 90))
    keys = _load_keys()

    timestamp   = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_topic  = "".join(c for c in topic[:30] if c.isalnum() or c in " _-").strip().replace(" ", "_")
    output_path = Path(custom_output) if custom_output else OUTPUT_DIR / f"{safe_topic}_{timestamp}.mp4"

    _log(player, f"[Video] 🎬 Başlıyor: '{topic}' — {duration_minutes} dakika")
    _log(player, "[Video] 🌸 Görsel motoru: Pollinations.AI FLUX (ücretsiz, key yok)")
    _speak(speak, f"Sinematik video oluşturuluyor. Konu: {topic}. Süre: {duration_minutes} dakika.")

    # ── Senaryo ───────────────────────────────────────────────────────────────
    _log(player, "[Video] 📝 Gemini ile senaryo üretiliyor...")
    try:
        scenes = generate_script(topic, duration_minutes, style, language, keys)
        _log(player, f"[Video] ✅ {len(scenes)} sahne planlandı.")
    except Exception as e:
        return f"Senaryo üretilemedi: {e}"

    # ── Görseller + Sesler ────────────────────────────────────────────────────
    _log(player, f"[Video] 🖼️  Görseller üretiliyor ({'Pollinations FLUX' if use_ai_images else 'Placeholder'})...")

    image_paths, durations_list = [], []
    tmp_dir = Path(tempfile.mkdtemp(prefix="niko_video_"))

    for i, scene in enumerate(scenes):
        img_path   = tmp_dir / f"scene_{i:04d}.png"
        audio_path = tmp_dir / f"voice_{i:04d}.mp3"
        dur        = int(scene.get("duration", DEFAULT_SCENE_DURATION))
        desc       = scene.get("description", "")[:55]

        _log(player, f"[Video] 🖼️  Sahne {i+1}/{len(scenes)}: {desc}")

        # Sesli anlatım
        if GTTS_OK:
            generate_voice(scene.get("narration", ""), audio_path, "tr")

        # Görsel üret
        success = False
        try:
            if use_ai_images:
                success = generate_image_pollinations(
                    prompt      = scene.get("prompt_en", topic),
                    output_path = img_path,
                    scene_index = i,
                    player      = player,
                )
            if not success:
                _log(player, "[Video] 🎨 Placeholder görsel üretiliyor...")
                generate_image_placeholder(scene, img_path)
        except Exception as e:
            _log(player, f"[Video] ⚠️  Sahne {i+1} hata: {e} — placeholder")
            generate_image_placeholder(scene, img_path)

        if img_path.exists():
            image_paths.append(img_path)
            durations_list.append(dur)

        # Rate limit koruması
        if use_ai_images and i < len(scenes) - 1:
            time.sleep(2)

    if not image_paths:
        return "Hiç görsel üretilemedi."

    # ── Altyazı ───────────────────────────────────────────────────────────────
    srt_file = tmp_dir / "subtitles.srt"
    create_srt(scenes, srt_file)

    # ── Arka plan müziği ─────────────────────────────────────────────────────
    music_file = None
    if PYDUB_OK:
        music_file = tmp_dir / "music.mp3"
        if not create_background_music(music_file, sum(durations_list)):
            music_file = None

    # ── Video Birleştir ───────────────────────────────────────────────────────
    _log(player, "[Video] 🎞️  Video birleştiriliyor (FFmpeg)...")
    _speak(speak, "Sahneler birleştiriliyor, lütfen bekleyin.")

    try:
        build_video(image_paths, durations_list, output_path,
                    srt_file=srt_file, music_file=music_file, player=player)
    except RuntimeError as e:
        frames_dir = output_path.parent / f"{safe_topic}_frames"
        frames_dir.mkdir(exist_ok=True)
        import shutil
        for p in image_paths:
            shutil.copy(p, frames_dir / p.name)
        _cleanup(tmp_dir)
        _speak(speak, "FFmpeg bulunamadı. Görseller kaydedildi.")
        return (
            f"⚠️ FFmpeg bulunamadı.\n"
            f"{len(image_paths)} sahne görseli '{frames_dir}' klasörüne kaydedildi.\n"
            f"Hata: {e}"
        )

    _cleanup(tmp_dir)

    total_secs = sum(durations_list)
    mins, secs = divmod(total_secs, 60)
    size_mb    = output_path.stat().st_size / 1_048_576 if output_path.exists() else 0

    try:
        os.startfile(str(output_path))
    except Exception:
        pass

    summary = (
        f"✅ Video hazır!\n"
        f"📁 Konum : {output_path}\n"
        f"⏱️  Süre  : {mins}dk {secs}s ({len(scenes)} sahne)\n"
        f"💾 Boyut : {size_mb:.1f} MB\n"
        f"🎬 Format: 720p MP4, H.264, 24fps, Ken Burns efekti\n"
        f"🌸 Görsel : Pollinations.AI FLUX"
    )
    _log(player, summary)
    _speak(speak, f"Video hazır! {mins} dakika {secs} saniye, {len(scenes)} sahne.")
    return summary


def _log(player, msg: str):
    print(msg)
    if player and hasattr(player, "write_log"):
        player.write_log(msg)

def _speak(speak, msg: str):
    if speak and callable(speak):
        speak(msg)

def _cleanup(tmp_dir: Path):
    try:
        import shutil
        shutil.rmtree(tmp_dir, ignore_errors=True)
    except Exception:
        pass