"""Физические клавиши Win32 для стандартных команд в разных раскладках."""

import os


def is_paste_shortcut(keycode, keysym, platform=None):
    """Вызывается только из Tk события с удерживаемой клавишей Control.

    В Win32 VK_V=0x56 независимо от раскладки. В Linux/macOS fallback по
    латинскому keysym, штатные клавиатурные привязки Tk остаются доступными.
    """
    platform = os.name if platform is None else platform
    if platform == "nt":
        return keycode == 0x56
    return isinstance(keysym, str) and keysym.lower() == "v"
