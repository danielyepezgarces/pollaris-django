import re
import threading
from pathlib import Path
from typing import Any, Dict, Optional
import yaml
from django.conf import settings

_local = threading.local()

SUPPORTED_LOCALES = {
    'en_GB': 'English',
    'cs': 'Čeština',
    'de': 'Deutsch',
    'es': 'Español',
    'es_ES': 'Español (España)',
    'fr_FR': 'Français',
    'gl': 'Galego',
    'hu': 'Magyar',
    'it': 'Italiano',
    'kab': 'Taqbaylit',
    'ko': '한국어',
    'lb': 'Lëtzebuergesch',
    'ne': 'नेपाली',
    'nl_NL': 'Nederlands',
    'oc': 'Occitan',
    'ps': 'پښتو',
    'pt': 'Português',
    'pt_BR': 'Português (Brasil)',
    'pt_PT': 'Português (Portugal)',
    'sk': 'Slovenčina',
    'tr': 'Türkçe',
    'uk': 'Українська мова',
}

DEFAULT_LOCALE = 'en_GB'


def set_current_locale(locale: str) -> None:
    _local.locale = locale


def get_current_locale() -> str:
    return getattr(_local, 'locale', DEFAULT_LOCALE)


class Translator:
    _catalogs: Dict[str, Dict[str, str]] = {}
    _loaded = False

    @classmethod
    def _load_catalog(cls, domain: str, locale: str) -> Dict[str, str]:
        cache_key = f"{domain}:{locale}"
        if cache_key in cls._catalogs:
            return cls._catalogs[cache_key]

        translations_dir = Path(settings.BASE_DIR) / 'translations'
        yaml_path = translations_dir / f"{domain}+intl-icu.{locale}.yaml"

        data = {}
        if yaml_path.exists():
            try:
                with open(yaml_path, 'r', encoding='utf-8') as f:
                    loaded = yaml.safe_load(f)
                    if isinstance(loaded, dict):
                        data = loaded
            except Exception:
                data = {}
        else:
            # Fallback to translatewiki JSON if available
            json_path = Path(settings.BASE_DIR) / 'translatewiki' / f"{domain}.{locale}.json"
            if json_path.exists():
                try:
                    import json
                    with open(json_path, 'r', encoding='utf-8') as f:
                        loaded = json.load(f)
                        if isinstance(loaded, dict):
                            data = loaded
                except Exception:
                    data = {}

        cls._catalogs[cache_key] = data
        return data

    @classmethod
    def trans(
        cls,
        key: str,
        params: Optional[Dict[str, Any]] = None,
        locale: Optional[str] = None,
        domain: str = 'messages',
    ) -> str:
        if not key:
            return ''

        target_locale = locale or get_current_locale()
        catalog = cls._load_catalog(domain, target_locale)

        message = catalog.get(key)
        if message is None and '_' in target_locale:
            # Try language base, e.g., 'es' for 'es_ES'
            base_lang = target_locale.split('_')[0]
            catalog_base = cls._load_catalog(domain, base_lang)
            message = catalog_base.get(key)

        if message is None and target_locale != DEFAULT_LOCALE:
            # Fallback to English
            fallback_catalog = cls._load_catalog(domain, DEFAULT_LOCALE)
            message = fallback_catalog.get(key)

        if message is None:
            # Key not found
            return key

        if not isinstance(message, str):
            return str(message)

        return cls._interpolate(message, params or {}, target_locale)

    @classmethod
    def _interpolate(cls, message: str, params: Dict[str, Any], locale: str) -> str:
        # Handle ICU plural expressions {var, plural, ...}
        offset = 0
        while True:
            pos = message.find('{', offset)
            if pos == -1:
                break

            comma1 = message.find(',', pos)
            if comma1 == -1:
                offset = pos + 1
                continue

            var_name = message[pos + 1:comma1].strip()
            comma2 = message.find(',', comma1 + 1)
            if comma2 == -1:
                offset = pos + 1
                continue

            type_name = message[comma1 + 1:comma2].strip()
            if type_name != 'plural':
                offset = pos + 1
                continue

            # Find matching '}'
            brace_count = 1
            i = comma2 + 1
            length = len(message)
            while i < length and brace_count > 0:
                if message[i] == '{':
                    brace_count += 1
                elif message[i] == '}':
                    brace_count -= 1
                i += 1

            if brace_count > 0:
                offset = pos + 1
                continue

            plural_block = message[pos:i]
            inner = message[comma2 + 1:i - 1]

            val = params.get(var_name, 0)
            try:
                num = int(val)
            except (ValueError, TypeError):
                num = 0

            branch_text = cls._resolve_icu_plural(inner, num, locale)
            branch_text = branch_text.replace('#', str(num))

            message = message[:pos] + branch_text + message[pos + len(plural_block):]
            offset = pos + len(branch_text)

        # Replace named placeholders {var} and __var__
        for k, v in params.items():
            str_val = str(v)
            message = message.replace(f'{{{k}}}', str_val)
            message = message.replace(f'__{k}__', str_val)

        return message

    @classmethod
    def _resolve_icu_plural(cls, inner: str, count: int, locale: str) -> str:
        # Parse categories from inner
        from polls.services.translation_converter import TranslationConverter
        categories = TranslationConverter._parse_icu_categories(inner)

        exact_key = f'={count}'
        if exact_key in categories:
            return categories[exact_key]

        cat = cls._get_plural_category(count, locale)
        if cat in categories:
            return categories[cat]
        if 'other' in categories:
            return categories['other']
        if categories:
            return next(iter(categories.values()))
        return ''

    @classmethod
    def _get_plural_category(cls, count: int, locale: str) -> str:
        lang = locale.split('_')[0]
        if lang in ('cs', 'sk'):
            if count == 1:
                return 'one'
            elif 2 <= count <= 4:
                return 'few'
            return 'other'
        elif lang == 'uk':
            if count % 10 == 1 and count % 100 != 11:
                return 'one'
            elif 2 <= count % 10 <= 4 and (count % 100 < 10 or count % 100 >= 20):
                return 'few'
            return 'many'
        else:
            return 'one' if count == 1 else 'other'


def trans(key: str, params: Optional[Dict[str, Any]] = None, locale: Optional[str] = None, domain: str = 'messages') -> str:
    return Translator.trans(key, params, locale, domain)


def match_accept_language(header: Optional[str]) -> str:
    if not header:
        return DEFAULT_LOCALE

    codes = list(SUPPORTED_LOCALES.keys())
    # Parse accept-language header: "es-ES,es;q=0.9,en;q=0.8"
    parts = [p.strip() for p in header.split(',') if p.strip()]
    for part in parts:
        lang_tag = part.split(';')[0].strip().replace('-', '_')
        if lang_tag in codes:
            return lang_tag
        # Match prefix e.g. "es" matches "es" or "es_ES"
        base = lang_tag.split('_')[0]
        for c in codes:
            if c == base or c.startswith(base + '_'):
                return c

    return DEFAULT_LOCALE
