import json
import asyncio
from mcp.client.sse import sse_client
from mcp.client.session import ClientSession

def execute_unity_tool(parameters: dict, player=None) -> str:
    """
    Mehmet'in Unity MCP sunucusuna (FastMCP) resmi SDK ile komut göndermesini sağlar.
    """
    tool_name = parameters.get("tool_name")
    arguments_str = parameters.get("arguments", "{}")
    
    # Gelen parametre string ise dict formatına çevir
    try:
        if isinstance(arguments_str, str):
            arguments = json.loads(arguments_str)
        else:
            arguments = arguments_str
    except Exception:
        arguments = {}

    # main.py içindeki thread yapısını bozmamak için async fonksiyonu senkron olarak tetikliyoruz
    return asyncio.run(_async_execute_tool(tool_name, arguments))

async def _async_execute_tool(tool_name: str, arguments: dict) -> str:
    # FastMCP SSE (Server-Sent Events) protokolü kullanır ve genelde /sse veya /mcp yolunu dinler
    url = "http://127.0.0.1:8080" 
    
    try:
        # 1. Unity ile SSE bağlantısını kur
        async with sse_client(url) as (read_stream, write_stream):
            # 2. MCP Oturumunu başlat (Resmi El Sıkışma / Handshake)
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                
                # 3. İstenen aracı Unity'ye gönder
                result = await session.call_tool(tool_name, arguments=arguments)
                
                # 4. Unity'den gelen sonucu yakala ve Mehmet'in beynine geri döndür
                if result.content and len(result.content) > 0:
                    return f"Unity'den Gelen Başarı Yanıtı: {result.content[0].text}"
                else:
                    return "Komut başarıyla çalıştı ancak Unity boş bir yanıt döndürdü."
                    
    except Exception as e:
        return (
            f"Unity Bağlantı Hatası: Editor uyku modunda olabilir veya "
            f"sunucu adresi ({url}) hatalı. Detay: {str(e)}"
        )