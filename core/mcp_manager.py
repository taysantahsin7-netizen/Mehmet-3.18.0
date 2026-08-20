import asyncio
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from mcp.client.session import ClientSession
from mcp.client.sse import sse_client
from mcp.client.stdio import StdioServerParameters, stdio_client


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR = get_base_dir()
CONFIG_DIR = BASE_DIR / "config"
CONFIG_FILE = CONFIG_DIR / "mcp_plugins.json"
PLUGINS_DIR = BASE_DIR / "plugins" / "mcp"


def _clean_schema_for_gemini(schema: Any) -> Any:
    """
    JSON Schema tiplerini Gemini FunctionDeclaration formatına (büyük harf tipler)
    dönüştürür ve desteklenmeyen meta alanları temizler.
    """
    if not isinstance(schema, dict):
        return schema

    type_mapping = {
        "string": "STRING",
        "number": "NUMBER",
        "integer": "INTEGER",
        "boolean": "BOOLEAN",
        "array": "ARRAY",
        "object": "OBJECT",
    }

    result = {}
    raw_type = schema.get("type")
    if isinstance(raw_type, str):
        result["type"] = type_mapping.get(raw_type.lower(), raw_type.upper())
    elif isinstance(raw_type, list):
        # Union type (örn: ["string", "null"]) -> ilk geçerli tipi seç
        valid_types = [t for t in raw_type if t != "null"]
        first_type = valid_types[0] if valid_types else "string"
        result["type"] = type_mapping.get(first_type.lower(), "STRING")
    else:
        # Tip belirtilmemişse varsayılan OBJECT
        result["type"] = "OBJECT"

    if "description" in schema and isinstance(schema["description"], str):
        result["description"] = schema["description"]

    if "properties" in schema and isinstance(schema["properties"], dict):
        result["properties"] = {
            k: _clean_schema_for_gemini(v)
            for k, v in schema["properties"].items()
        }

    if "required" in schema and isinstance(schema["required"], list):
        result["required"] = [str(r) for r in schema["required"]]

    if "items" in schema and isinstance(schema["items"], dict):
        result["items"] = _clean_schema_for_gemini(schema["items"])

    if "enum" in schema and isinstance(schema["enum"], list):
        result["enum"] = [str(e) for e in schema["enum"]]

    return result


class _SafeErrLog:
    """Windows cp1254/charmap stderr çökmesini önleyen güvenli log yazıcı."""
    def write(self, s: str):
        try:
            sys.stderr.write(s)
        except Exception:
            try:
                sys.stderr.write(s.encode("ascii", errors="replace").decode("ascii"))
            except Exception:
                pass

    def flush(self):
        try:
            sys.stderr.flush()
        except Exception:
            pass


_SAFE_ERR_LOG = _SafeErrLog()


class MCPManager:
    """
    Mehmet için Evrensel MCP Eklenti Yöneticisi.
    Stdio, SSE, GitHub repo klonlama, npx/py komutları ve Claude/MCP JSON'larını destekler.
    """

    def __init__(self):
        self._lock = threading.RLock()
        self.plugins: List[Dict[str, Any]] = []
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        PLUGINS_DIR.mkdir(parents=True, exist_ok=True)
        self.load_plugins()

    def load_plugins(self) -> List[Dict[str, Any]]:
        with self._lock:
            if CONFIG_FILE.exists():
                try:
                    self.plugins = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
                except Exception as e:
                    print(f"[MCPManager] ⚠️ Config yüklenirken hata: {e}")
                    self.plugins = []
            else:
                self.plugins = []
                self.save_plugins()
            return self.plugins

    def save_plugins(self) -> None:
        with self._lock:
            try:
                CONFIG_FILE.write_text(
                    json.dumps(self.plugins, indent=2, ensure_ascii=False),
                    encoding="utf-8"
                )
            except Exception as e:
                print(f"[MCPManager] ❌ Config kaydedilemedi: {e}")

    def get_plugin(self, plugin_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            for p in self.plugins:
                if p.get("id") == plugin_id:
                    return p
            return None

    def add_plugin(self, plugin_data: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            # Var olanı güncelle veya yeni ekle
            plugin_id = plugin_data.get("id") or re.sub(r"[^a-zA-Z0-9_-]", "_", plugin_data.get("name", "plugin")).lower()
            plugin_data["id"] = plugin_id
            plugin_data.setdefault("enabled", True)
            plugin_data.setdefault("tools", [])

            self.plugins = [p for p in self.plugins if p.get("id") != plugin_id]
            self.plugins.append(plugin_data)
            self.save_plugins()
            return plugin_data

    def remove_plugin(self, plugin_id: str) -> bool:
        with self._lock:
            plugin = self.get_plugin(plugin_id)
            if not plugin:
                return False

            # Eğer klonlanmış yerel bir dizini varsa temizle
            install_dir = plugin.get("install_dir")
            if install_dir and os.path.exists(install_dir):
                try:
                    shutil.rmtree(install_dir, ignore_errors=True)
                except Exception as e:
                    print(f"[MCPManager] ⚠️ Dizin silinirken uyarı: {e}")

            self.plugins = [p for p in self.plugins if p.get("id") != plugin_id]
            self.save_plugins()
            return True

    def toggle_plugin(self, plugin_id: str, enabled: bool) -> bool:
        with self._lock:
            plugin = self.get_plugin(plugin_id)
            if plugin:
                plugin["enabled"] = enabled
                self.save_plugins()
                return True
            return False

    def get_active_plugins(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [p for p in self.plugins if p.get("enabled", True)]

    def get_active_declarations(self) -> List[Dict[str, Any]]:
        """
        Gemini Live Session için tüm aktif MCP eklentilerinin araç şemalarını döndürür.
        """
        declarations = []
        with self._lock:
            for plugin in self.get_active_plugins():
                tools = plugin.get("tools", [])
                for tool in tools:
                    # Tool formatı: { name, description, parameters }
                    declarations.append({
                        "name": tool["name"],
                        "description": tool.get("description", f"MCP Tool from {plugin.get('name')}"),
                        "parameters": tool.get("parameters", {"type": "OBJECT", "properties": {}}),
                    })
        return declarations

    def has_tool(self, tool_name: str) -> bool:
        with self._lock:
            for plugin in self.get_active_plugins():
                for tool in plugin.get("tools", []):
                    if tool.get("name") == tool_name:
                        return True
            return False

    def find_plugin_for_tool(self, tool_name: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            for plugin in self.get_active_plugins():
                for tool in plugin.get("tools", []):
                    if tool.get("name") == tool_name:
                        return plugin
            return None

    # ── Evrensel Kaynak Yükleyici / Parser ──────────────────────────────────
    def install_from_source(self, raw_input: str, custom_name: str = "") -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        """
        Kullanıcının girdiği metni analiz eder:
        1. JSON yapılandırma (Claude Desktop formatı veya doğrudan obje)
        2. SSE / HTTP URL
        3. GitHub Repo URL (Klonlar, bağımlılıkları yükler, çalıştırıcıyı ayarlar)
        4. CLI Komutu (npx, uvx, py, node, python...)
        """
        text = raw_input.strip()
        if not text:
            return False, "Lütfen bir link, komut veya JSON yapılandırması girin.", None

        # 1. JSON Yapılandırması mı?
        if text.startswith("{") and text.endswith("}"):
            try:
                data = json.loads(text)
                return self._install_from_json(data, custom_name)
            except Exception as e:
                return False, f"Geçersiz JSON formatı: {e}", None

        # 2. SSE / HTTP URL mi?
        if (text.startswith("http://") or text.startswith("https://")) and not ("github.com" in text or text.endswith(".git")):
            name = custom_name or "SSE MCP Server"
            plugin_id = re.sub(r"[^a-zA-Z0-9_-]", "_", name).lower()
            plugin_data = {
                "id": plugin_id,
                "name": name,
                "enabled": True,
                "transport": "sse",
                "url": text,
                "source": text,
                "tools": []
            }
            added = self.add_plugin(plugin_data)
            # Otomatik araçları keşfet
            tools, err = self.sync_fetch_tools(added)
            if err:
                return True, f"SSE Sunucusu eklendi ancak araçlar alınamadı: {err}", added
            return True, f"SSE Sunucusu başarıyla eklendi! {len(tools)} araç bulundu.", added

        # 3. GitHub Repo URL mi?
        if "github.com" in text or text.endswith(".git"):
            return self._install_from_github(text, custom_name)

        # 4. CLI Komutu (npx, uvx, py, node...)
        return self._install_from_command(text, custom_name)

    def _install_from_json(self, data: Dict[str, Any], custom_name: str = "") -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        # Claude Desktop formatı: { "mcpServers": { "server_name": { "command": "...", "args": [...] } } }
        if "mcpServers" in data and isinstance(data["mcpServers"], dict):
            added_count = 0
            last_added = None
            for srv_name, srv_conf in data["mcpServers"].items():
                p_data = {
                    "id": re.sub(r"[^a-zA-Z0-9_-]", "_", srv_name).lower(),
                    "name": srv_name,
                    "enabled": True,
                    "transport": srv_conf.get("transport", "stdio"),
                    "command": srv_conf.get("command", ""),
                    "args": srv_conf.get("args", []),
                    "env": srv_conf.get("env", {}),
                    "cwd": srv_conf.get("cwd", ""),
                    "url": srv_conf.get("url", ""),
                    "source": "JSON Config",
                    "tools": []
                }
                last_added = self.add_plugin(p_data)
                self.sync_fetch_tools(last_added)
                added_count += 1
            return True, f"{added_count} adet MCP sunucusu başarıyla içe aktarıldı.", last_added

        # Tekil sunucu formatı
        name = custom_name or data.get("name", "Custom MCP")
        plugin_id = re.sub(r"[^a-zA-Z0-9_-]", "_", name).lower()
        plugin_data = {
            "id": plugin_id,
            "name": name,
            "enabled": True,
            "transport": data.get("transport", "stdio"),
            "command": data.get("command", ""),
            "args": data.get("args", []),
            "env": data.get("env", {}),
            "cwd": data.get("cwd", ""),
            "url": data.get("url", ""),
            "source": "JSON Config",
            "tools": []
        }
        added = self.add_plugin(plugin_data)
        tools, err = self.sync_fetch_tools(added)
        return True, f"MCP Eklentisi eklendi ({len(tools)} araç).", added

    def _install_from_github(self, git_url: str, custom_name: str = "") -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        clean_url = git_url.strip()
        match = re.search(r"github\.com/([^/]+)/([^/#?]+)", clean_url)
        if match:
            owner, repo_name = match.group(1), match.group(2).removesuffix(".git")
            default_name = f"{owner}_{repo_name}"
        else:
            default_name = "github_mcp_plugin"

        name = custom_name or default_name
        plugin_id = re.sub(r"[^a-zA-Z0-9_-]", "_", name).lower()
        target_dir = PLUGINS_DIR / plugin_id

        if target_dir.exists():
            shutil.rmtree(target_dir, ignore_errors=True)

        try:
            git_proc = subprocess.run(
                ["git", "clone", "--depth", "1", clean_url, str(target_dir)],
                capture_output=True,
                text=True,
                timeout=60,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "windows" else 0
            )
            if git_proc.returncode != 0:
                return False, f"Git klonlama başarısız oldu: {git_proc.stderr.strip()}", None
        except Exception as e:
            return False, f"Git çalıştırılamadı: {e}", None

        cmd = ""
        args: List[str] = []

        # Node / TypeScript / JavaScript projesi
        if (target_dir / "package.json").exists():
            try:
                pkg_data = json.loads((target_dir / "package.json").read_text(encoding="utf-8"))
            except Exception:
                pkg_data = {}

            try:
                npm_cmd = "npm.cmd" if sys.platform == "windows" else "npm"
                subprocess.run(
                    [npm_cmd, "install"],
                    cwd=str(target_dir),
                    capture_output=True,
                    timeout=120,
                    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "windows" else 0
                )
                if "build" in pkg_data.get("scripts", {}):
                    subprocess.run(
                        [npm_cmd, "run", "build"],
                        cwd=str(target_dir),
                        capture_output=True,
                        timeout=120,
                        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "windows" else 0
                    )
            except Exception as e:
                print(f"[MCPManager] npm install uyarısı: {e}")

            if (target_dir / "dist" / "index.js").exists():
                cmd = "node"
                args = [str(target_dir / "dist" / "index.js")]
            elif (target_dir / "build" / "index.js").exists():
                cmd = "node"
                args = [str(target_dir / "build" / "index.js")]
            elif (target_dir / "index.js").exists():
                cmd = "node"
                args = [str(target_dir / "index.js")]
            elif pkg_data.get("main"):
                cmd = "node"
                args = [str(target_dir / pkg_data["main"])]
            else:
                cmd = "node"
                args = ["index.js"]

        # Python projesi
        elif (target_dir / "requirements.txt").exists() or (target_dir / "pyproject.toml").exists() or list(target_dir.glob("*.py")):
            if (target_dir / "requirements.txt").exists():
                try:
                    subprocess.run(
                        ["py", "-m", "pip", "install", "-r", "requirements.txt"],
                        cwd=str(target_dir),
                        capture_output=True,
                        timeout=120,
                        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "windows" else 0
                    )
                except Exception as e:
                    print(f"[MCPManager] pip install uyarısı: {e}")

            py_entry = None
            for cand in ["server.py", "main.py", "app.py", "__main__.py"]:
                if (target_dir / cand).exists():
                    py_entry = cand
                    break
            if not py_entry:
                py_files = list(target_dir.glob("*.py"))
                if py_files:
                    py_entry = py_files[0].name

            cmd = "py"
            args = [py_entry or "server.py"]

        else:
            cmd = "py"
            args = ["server.py"]

        plugin_data = {
            "id": plugin_id,
            "name": name,
            "enabled": True,
            "transport": "stdio",
            "command": cmd,
            "args": args,
            "cwd": str(target_dir),
            "install_dir": str(target_dir),
            "source": clean_url,
            "tools": []
        }

        added = self.add_plugin(plugin_data)
        tools, err = self.sync_fetch_tools(added)
        if err:
            return True, f"Eklenti indirildi ({name}) fakat araç listesi alınamadı: {err}", added
        return True, f"GitHub Eklentisi başarıyla kuruldu! ({len(tools)} araç aktif)", added

    def _install_from_command(self, cmd_text: str, custom_name: str = "") -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        parts = shlex.split(cmd_text, posix=(sys.platform != "windows"))
        if not parts:
            return False, "Komut ayrıştırılamadı.", None

        cmd = parts[0]
        args = parts[1:]

        if not custom_name:
            if "npx" in cmd or "@modelcontextprotocol" in cmd_text:
                for arg in args:
                    if "@modelcontextprotocol/server-" in arg:
                        custom_name = arg.split("/")[-1].replace("server-", "mcp_")
                        break
                    elif "mcp-server-" in arg:
                        custom_name = arg
                        break
            if not custom_name:
                custom_name = f"mcp_{Path(cmd).stem}"

        name = custom_name
        plugin_id = re.sub(r"[^a-zA-Z0-9_-]", "_", name).lower()

        plugin_data = {
            "id": plugin_id,
            "name": name,
            "enabled": True,
            "transport": "stdio",
            "command": cmd,
            "args": args,
            "cwd": "",
            "source": cmd_text,
            "tools": []
        }

        added = self.add_plugin(plugin_data)
        tools, err = self.sync_fetch_tools(added)
        if err:
            return True, f"Komut eklentisi kaydedildi ancak araçlar alınamadı: {err}", added
        return True, f"MCP Komut Eklentisi başarıyla eklendi! ({len(tools)} araç bulundu)", added

    # ── Araç Keşfi & Sorgulama ─────────────────────────────────────────────
    async def async_fetch_tools(self, plugin: Dict[str, Any], timeout: float = 15.0) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        async def _do_fetch():
            transport = plugin.get("transport", "stdio").lower()
            tools_list = []

            if transport == "sse":
                url = plugin.get("url", "")
                if not url:
                    return [], "SSE URL belirtilmemiş."
                async with sse_client(url) as (read_stream, write_stream):
                    async with ClientSession(read_stream, write_stream) as session:
                        await session.initialize()
                        mcp_tools = await session.list_tools()
                        for t in mcp_tools.tools:
                            gemini_params = _clean_schema_for_gemini(t.inputSchema or {"type": "object"})
                            tools_list.append({
                                "name": t.name,
                                "description": t.description or f"MCP tool: {t.name}",
                                "parameters": gemini_params,
                            })

            else:  # stdio
                cmd = plugin.get("command", "")
                args = plugin.get("args", [])
                cwd = plugin.get("cwd") or None
                env = plugin.get("env") or None

                if not cmd:
                    return [], "Komut belirtilmemiş."

                # Windows uyumluluğu için komut yolunu çöz (npx -> npx.cmd)
                resolved_cmd = shutil.which(cmd) or cmd

                server_params = StdioServerParameters(
                    command=resolved_cmd,
                    args=args,
                    env=env,
                    cwd=cwd,
                    encoding="utf-8",
                    encoding_error_handler="ignore"
                )

                async with stdio_client(server_params, errlog=_SAFE_ERR_LOG) as (read_stream, write_stream):
                    async with ClientSession(read_stream, write_stream) as session:
                        await session.initialize()
                        mcp_tools = await session.list_tools()
                        for t in mcp_tools.tools:
                            gemini_params = _clean_schema_for_gemini(t.inputSchema or {"type": "object"})
                            tools_list.append({
                                "name": t.name,
                                "description": t.description or f"MCP tool: {t.name}",
                                "parameters": gemini_params,
                            })

            with self._lock:
                plugin["tools"] = tools_list
                self.save_plugins()

            return tools_list, None

        try:
            return await asyncio.wait_for(_do_fetch(), timeout=timeout)
        except asyncio.TimeoutError:
            return [], f"Zaman aşımı ({timeout}s): MCP sunucusundan yanıt alınamadı."
        except Exception as e:
            err_msg = str(e)
            print(f"[MCPManager] ⚠️ Araç keşif hatası ({plugin.get('name')}): {err_msg}")
            return [], err_msg

    def sync_fetch_tools(self, plugin: Dict[str, Any], timeout: float = 15.0) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        try:
            return asyncio.run(self.async_fetch_tools(plugin, timeout=timeout))
        except Exception as e:
            return [], str(e)

    # ── Araç Çağırma (Call Tool) ──────────────────────────────────────────
    async def async_call_tool(self, tool_name: str, arguments: Dict[str, Any], timeout: float = 30.0) -> str:
        plugin = self.find_plugin_for_tool(tool_name)
        if not plugin:
            return f"MCP Hatası: '{tool_name}' adlı araca sahip aktif bir MCP eklentisi bulunamadı."

        transport = plugin.get("transport", "stdio").lower()

        async def _do_call():
            if transport == "sse":
                url = plugin.get("url", "")
                async with sse_client(url) as (read_stream, write_stream):
                    async with ClientSession(read_stream, write_stream) as session:
                        await session.initialize()
                        result = await session.call_tool(tool_name, arguments=arguments)
                        if result.content:
                            texts = [c.text for c in result.content if hasattr(c, "text") and c.text]
                            return "\n".join(texts) if texts else "Komut başarıyla çalıştı (boş yanıt)."
                        return "Komut başarıyla tamamlandı."

            else:  # stdio
                cmd = plugin.get("command", "")
                args = plugin.get("args", [])
                cwd = plugin.get("cwd") or None
                env = plugin.get("env") or None

                resolved_cmd = shutil.which(cmd) or cmd

                server_params = StdioServerParameters(
                    command=resolved_cmd,
                    args=args,
                    env=env,
                    cwd=cwd,
                    encoding="utf-8",
                    encoding_error_handler="ignore"
                )

                async with stdio_client(server_params, errlog=_SAFE_ERR_LOG) as (read_stream, write_stream):
                    async with ClientSession(read_stream, write_stream) as session:
                        await session.initialize()
                        result = await session.call_tool(tool_name, arguments=arguments)
                        if result.content:
                            texts = [c.text for c in result.content if hasattr(c, "text") and c.text]
                            return "\n".join(texts) if texts else "Komut başarıyla çalıştı (boş yanıt)."
                        return "Komut başarıyla tamamlandı."

        try:
            return await asyncio.wait_for(_do_call(), timeout=timeout)
        except asyncio.TimeoutError:
            return f"MCP Hatası: '{tool_name}' çalıştırma işlemi zaman aşımına uğradı ({timeout}s)."
        except Exception as e:
            return f"MCP '{plugin.get('name')}' - '{tool_name}' çalıştırma hatası: {str(e)}"

    def sync_call_tool(self, tool_name: str, arguments: Dict[str, Any], timeout: float = 30.0) -> str:
        try:
            return asyncio.run(self.async_call_tool(tool_name, arguments, timeout=timeout))
        except Exception as e:
            return f"MCP Hatası: {e}"


mcp_manager = MCPManager()
