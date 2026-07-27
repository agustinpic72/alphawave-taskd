import re
import unicodedata


MATCH_ALIASES = {
    "maniana": "manana",
    "manianas": "mananas",
    "q": "que",
}


def normalize_for_matching(text: str) -> str:
    normalized = unicodedata.normalize("NFD", text.casefold())
    normalized = "".join(char for char in normalized if unicodedata.category(char) != "Mn")
    normalized = normalized.replace("¿", " ").replace("?", " ").replace("¡", " ").replace("!", " ")
    normalized = re.sub(r"[,;:]+", " ", normalized)
    normalized = re.sub(r"[^a-z0-9/]+", " ", normalized)
    normalized = " ".join(normalized.split())
    return _apply_match_aliases(normalized)


def normalized_words(text: str) -> str:
    normalized = unicodedata.normalize("NFD", text.casefold())
    normalized = "".join(char for char in normalized if unicodedata.category(char) != "Mn")
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
    normalized = " ".join(normalized.split())
    return _apply_match_aliases(normalized)


def _apply_match_aliases(text: str) -> str:
    return " ".join(MATCH_ALIASES.get(word, word) for word in text.split())
