from __future__ import annotations

import os
import re
from dataclasses import dataclass

import httpx

try:
    from deep_translator import GoogleTranslator  # type: ignore
except Exception:  # pragma: no cover
    GoogleTranslator = None

try:
    from deep_translator import MyMemoryTranslator  # type: ignore
except Exception:  # pragma: no cover
    MyMemoryTranslator = None

try:
    from deep_translator import LingueeTranslator  # type: ignore
except Exception:  # pragma: no cover
    LingueeTranslator = None

try:
    from deep_translator import PonsTranslator  # type: ignore
except Exception:  # pragma: no cover
    PonsTranslator = None

try:
    from langdetect import detect  # type: ignore
except Exception:  # pragma: no cover
    detect = None


_CYRILLIC_RE = re.compile(r"[А-Яа-яЁё]")
_LATIN_RE = re.compile(r"[A-Za-z]")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")  # CJK Unified Ideographs (basic)


def detect_language(text: str) -> str:
    """
    Lightweight language detection for short user texts.
    Returns ISO-ish codes: 'ru' | 'en' | 'unknown'.
    """
    t = (text or "").strip()
    if not t:
        return "unknown"

    # Fast script heuristics first (more stable for short texts).
    has_cyr = bool(_CYRILLIC_RE.search(t))
    has_lat = bool(_LATIN_RE.search(t))
    has_cjk = bool(_CJK_RE.search(t))
    if has_cyr and not has_lat:
        return "ru"
    if has_lat and not has_cyr:
        return "en"
    if has_cjk and not (has_cyr or has_lat):
        return "zh"

    if detect is None:
        return "unknown"

    try:
        lang = detect(t)
    except Exception:
        return "unknown"

    if lang in {"ru", "en", "zh"}:
        return lang
    return "unknown"


def _translate_google(text: str, target: str) -> str:
    if GoogleTranslator is None:
        raise RuntimeError("GoogleTranslator unavailable")
    return GoogleTranslator(source="auto", target=target).translate(text)


def _translate_mymemory(text: str, target: str) -> str:
    if MyMemoryTranslator is None:
        raise RuntimeError("MyMemoryTranslator unavailable")
    # deep-translator expects "langpair" like "ru|en"
    detected = detect_language(text)
    source = detected if detected in {"ru", "en"} else "auto"
    if source == "auto":
        # MyMemory doesn't support auto reliably; assume opposite of target as a fallback.
        source = "ru" if target == "en" else "en"
    return MyMemoryTranslator(source=source, target=target).translate(text)

def _translate_linguee(text: str, target: str) -> str:
    if LingueeTranslator is None:
        raise RuntimeError("LingueeTranslator unavailable")
    detected = detect_language(text)
    if detected not in {"ru", "en"} or target not in {"ru", "en"}:
        raise RuntimeError("Linguee supports ru/en only")
    return LingueeTranslator(source=detected, target=target).translate(text)


def _translate_pons(text: str, target: str) -> str:
    if PonsTranslator is None:
        raise RuntimeError("PonsTranslator unavailable")
    detected = detect_language(text)
    if detected not in {"ru", "en"} or target not in {"ru", "en"}:
        raise RuntimeError("Pons supports ru/en only")
    return PonsTranslator(source=detected, target=target).translate(text)


def _llm_enabled() -> bool:
    mode = (os.getenv("TRANSLATION_MODE", "") or "").strip().lower()
    if mode in {"llm", "openai"}:
        return True
    return False


def _translate_llm(text: str, target: str) -> str:
    """
    LLM translation via OpenAI-compatible Chat Completions API.

    Required env:
    - LLM_API_KEY
    Optional:
    - LLM_API_BASE (default https://api.openai.com/v1)
    - LLM_MODEL (default gpt-4o-mini)
    """
    api_key = (os.getenv("LLM_API_KEY", "") or "").strip()
    if not api_key:
        raise RuntimeError("LLM_API_KEY is not set")

    base = (os.getenv("LLM_API_BASE", "") or "").strip() or "https://api.openai.com/v1"
    model = (os.getenv("LLM_MODEL", "") or "").strip() or "gpt-4o-mini"

    src = detect_language(text)
    # Keep slang/meaning; return ONLY translated text, no quotes.
    system = (
        "You are a professional translator for dorm marketplace posts. "
        "Preserve meaning, tone, and slang. Keep product names/brands. "
        "Return ONLY the translated text, no explanations."
    )
    user = (
        f"Translate from {src} to {target}. "
        "If the text is already in the target language, return it unchanged.\n\n"
        f"TEXT:\n{text}"
    )

    url = base.rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}"}
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.2,
    }

    with httpx.Client(timeout=20) as client:
        r = client.post(url, headers=headers, json=payload)
        r.raise_for_status()
        data = r.json()
        out = data["choices"][0]["message"]["content"]
        return (out or "").strip()


def translate_text(text: str, target: str) -> str:
    """
    Translate text to target language ('ru' or 'en').
    Currently uses deep-translator (Google) as a baseline.
    """
    t = (text or "").strip()
    if not t:
        return ""

    if target not in {"ru", "en", "zh"}:
        return t

    translators = []
    if _llm_enabled():
        translators.append(_translate_llm)
    # fallback translators (optional)
    translators.extend([_translate_google, _translate_mymemory, _translate_linguee, _translate_pons])

    for translator in translators:
        try:
            out = translator(t, target=target)
            out = (out or "").strip()
            if out:
                return out
        except Exception:
            continue

    # Fail safe: return original if translation unavailable.
    return t


@dataclass(frozen=True)
class MultilingualText:
    detected_lang: str
    ru: str
    en: str
    zh: str


def build_multilingual(text: str) -> MultilingualText:
    detected = detect_language(text)
    t = (text or "").strip()
    if detected == "ru":
        ru = t
        en = translate_text(t, "en")
        zh = translate_text(t, "zh")
        return MultilingualText(detected_lang="ru", ru=ru, en=en, zh=zh)
    if detected == "en":
        en = t
        ru = translate_text(t, "ru")
        zh = translate_text(t, "zh")
        return MultilingualText(detected_lang="en", ru=ru, en=en, zh=zh)
    if detected == "zh":
        zh = t
        en = translate_text(t, "en")
        ru = translate_text(t, "ru")
        return MultilingualText(detected_lang="zh", ru=ru, en=en, zh=zh)

    # Unknown: keep as-is, try best-effort EN.
    en = translate_text(t, "en")
    ru = translate_text(t, "ru")
    zh = translate_text(t, "zh")
    return MultilingualText(detected_lang="unknown", ru=ru or t, en=en or t, zh=zh or t)


def format_multilingual_for_user(
    ru: str | None,
    en: str | None,
    zh: str | None,
    user_lang: str,
) -> str:
    """
    Returns primary + other languages in parentheses.
    Example (ru user): "стул\n(EN: chair; 中文: 椅子)"
    """
    ru_t = (ru or "").strip()
    en_t = (en or "").strip()
    zh_t = (zh or "").strip()

    user_lang = (user_lang or "ru").strip().lower()
    if user_lang not in {"ru", "en", "zh"}:
        user_lang = "ru"

    primary = {"ru": ru_t, "en": en_t, "zh": zh_t}.get(user_lang, ru_t) or ""
    if not primary:
        primary = ru_t or en_t or zh_t

    parts: list[str] = []
    if user_lang != "ru" and ru_t and ru_t != primary:
        parts.append(f"RU: {ru_t}")
    if user_lang != "en" and en_t and en_t != primary:
        parts.append(f"EN: {en_t}")
    if user_lang != "zh" and zh_t and zh_t != primary:
        parts.append(f"中文: {zh_t}")

    if not parts:
        return primary
    return f"{primary}\n(" + "; ".join(parts) + ")"

