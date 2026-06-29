import pyautogui
import time
import os

def davinci_macro_action(parameters: dict, player=None) -> str:
    media_paths = parameters.get("media_paths", [])
    
    # DaVinci'yi pencere başlığıyla bulup öne getir (daha garantili)
    import pygetwindow as gw
    windows = gw.getWindowsWithTitle('DaVinci Resolve')
    if windows:
        windows[0].activate()

    for path in media_paths:
        if os.path.exists(path):
            # 1. Medyayı İçe Aktar (Ctrl + I)
            pyautogui.hotkey('ctrl', 'i')
            time.sleep(0.5)
            pyautogui.write(path)
            pyautogui.press('enter')
            time.sleep(1)
            
            # 2. Seçili dosyayı Timeline'a at (F9 - Insert)
            pyautogui.press('f9') 
            time.sleep(0.5)
            
    return f"{len(media_paths)} dosya timeline'a aktarıldı Kral!"