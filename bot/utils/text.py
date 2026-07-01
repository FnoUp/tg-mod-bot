import re
import unicodedata

_LINK_RE = re.compile(
    r"(https?://[^\s]+|www\.[^\s]+|t(?:elegram)?\.me/[^\s]+)", re.IGNORECASE
)

# Латиница/цифры, похожие на кириллицу — приводим к кириллице, чтобы ловить
# обходы вида "kазино", "нaркo", "к4зино".
_HOMOGLYPHS = str.maketrans({
    "a": "а", "b": "в", "c": "с", "e": "е", "h": "н", "k": "к", "m": "м",
    "o": "о", "p": "р", "t": "т", "x": "х", "y": "у", "3": "з",
    "0": "о", "4": "а", "6": "б",
})

_KEEP_RE = re.compile(r"[^0-9a-zа-яё]")


def _squeeze(text: str) -> str:
    """Убирает регистр, диакритику, разделители и гомоглифы: 'к.л.а.д'->'клад'."""
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.translate(_HOMOGLYPHS)
    return _KEEP_RE.sub("", text)


def extract_links(text: str) -> list[str]:
    return _LINK_RE.findall(text or "")


def contains_link(text: str) -> bool:
    return bool(_LINK_RE.search(text or ""))


def is_whitelisted(link: str, whitelist_domains: list[str]) -> bool:
    link_lower = link.lower()
    return any(domain in link_lower for domain in whitelist_domains)


def contains_banned_word(text: str, banned_words: list[str]) -> bool:
    if not text or not banned_words:
        return False
    lowered = text.lower()
    squeezed = _squeeze(text)
    for word in banned_words:
        if word.lower() in lowered:
            return True
        sq = _squeeze(word)
        if sq and sq in squeezed:
            return True
    return False
