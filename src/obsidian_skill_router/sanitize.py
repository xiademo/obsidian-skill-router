from __future__ import annotations


_BIDI_CONTROLS = {
    "\u061c", "\u200e", "\u200f", "\u202a", "\u202b", "\u202c", "\u202d", "\u202e",
    "\u2066", "\u2067", "\u2068", "\u2069",
}


def markdown_plain(value: object) -> str:
    safe = "".join(
        character
        for character in str(value or "")
        if ord(character) >= 32
        and not 127 <= ord(character) <= 159
        and character not in _BIDI_CONTROLS
    )
    text = " ".join(safe.split())
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = text.replace("[", "&#91;").replace("]", "&#93;")
    for marker in ("\\", "`", "*", "_", "|", "!"):
        text = text.replace(marker, "\\" + marker)
    return text
