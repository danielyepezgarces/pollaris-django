import re
from typing import Optional, List, Dict


class TranslationConverter:
    """
    Converts between Symfony ICU plural syntax and MediaWiki plural format.
    Matches the logic of App\\Command\\TranslateWiki\\TranslationConverter from PHP.
    """

    @classmethod
    def convert_icu_to_mediawiki(cls, text: str, locale: str, source_english_message: Optional[str] = None) -> str:
        variables = cls.get_placeholder_variables(source_english_message if source_english_message is not None else text)

        offset = 0
        while True:
            pos = text.find('{', offset)
            if pos == -1:
                break

            next_comma = text.find(',', pos)
            if next_comma == -1:
                offset = pos + 1
                continue

            var_name = text[pos + 1:next_comma].strip()
            if not re.match(r'^[a-zA-Z0-9_-]+$', var_name):
                offset = pos + 1
                continue

            next_comma2 = text.find(',', next_comma + 1)
            if next_comma2 == -1:
                offset = pos + 1
                continue

            type_name = text[next_comma + 1:next_comma2].strip()
            if type_name != 'plural':
                offset = pos + 1
                continue

            # Find matching closing brace
            brace_count = 1
            i = next_comma2 + 1
            length = len(text)
            while i < length and brace_count > 0:
                if text[i] == '{':
                    brace_count += 1
                elif text[i] == '}':
                    brace_count -= 1
                i += 1

            if brace_count > 0:
                offset = pos + 1
                continue

            plural_block = text[pos:i]
            inner = text[next_comma2 + 1:i - 1]

            try:
                var_index = variables.index(var_name)
                var_placeholder_num = var_index + 1
            except ValueError:
                var_placeholder_num = 1

            categories = cls._parse_icu_categories(inner)
            converted = cls._build_mediawiki_plural(categories, locale, var_placeholder_num)

            text = text[:pos] + converted + text[pos + len(plural_block):]
            offset = pos + len(converted)

        # Convert remaining named simple placeholders ({var} or __var__) to $1, $2, etc.
        for index, var in enumerate(variables):
            if var.startswith('__') and var.endswith('__'):
                placeholder = var
            else:
                placeholder = f'{{{var}}}'
            positional = f'${index + 1}'
            text = text.replace(placeholder, positional)

        return text

    @classmethod
    def convert_mediawiki_to_icu(cls, text: str, locale: str, source_english_message: Optional[str] = None) -> str:
        variables = cls.get_placeholder_variables(source_english_message) if source_english_message else []

        offset = 0
        search_target = '{{plural:$'
        while True:
            text_lower = text.lower()
            pos = text_lower.find(search_target, offset)
            if pos == -1:
                break

            i = pos + len(search_target)
            length = len(text)
            num_str = ''
            while i < length and text[i].isdigit():
                num_str += text[i]
                i += 1

            if not num_str or i >= length or text[i] != '|':
                offset = pos + 10
                continue

            var_placeholder_num = int(num_str)
            i += 1  # Skip '|'

            # Find matching '}}'
            brace_count = 2
            start_inner = i
            while i < length and brace_count > 0:
                if text[i] == '{':
                    brace_count += 1
                elif text[i] == '}':
                    brace_count -= 1
                i += 1

            if brace_count > 0:
                offset = pos + 10
                continue

            plural_block = text[pos:i]
            inner = text[start_inner:i - 2]

            var_name = variables[var_placeholder_num - 1] if 0 <= var_placeholder_num - 1 < len(variables) else 'count'

            categories = cls._parse_mediawiki_categories(inner, locale, var_placeholder_num)
            converted = cls._build_icu_plural(var_name, categories)

            text = text[:pos] + converted + text[pos + len(plural_block):]
            offset = pos + len(converted)

        # Convert positional placeholders ($1, $2, etc.) back to named placeholders ({var} or __var__)
        for index, var in enumerate(variables):
            positional = f'${index + 1}'
            if var.startswith('__') and var.endswith('__'):
                placeholder = var
            else:
                placeholder = f'{{{var}}}'
            text = text.replace(positional, placeholder)

        return text

    @classmethod
    def _parse_icu_categories(cls, inner: str) -> Dict[str, str]:
        i = 0
        length = len(inner)
        categories: Dict[str, str] = {}

        while i < length:
            while i < length and inner[i].isspace():
                i += 1
            if i >= length:
                break

            start_cat = i
            while i < length and not inner[i].isspace() and inner[i] != '{':
                i += 1
            category = inner[start_cat:i]

            while i < length and inner[i].isspace():
                i += 1

            if i < length and inner[i] == '{':
                i += 1
                start_text = i
                brace_count = 1
                while i < length and brace_count > 0:
                    if inner[i] == '{':
                        brace_count += 1
                    elif inner[i] == '}':
                        brace_count -= 1
                    i += 1
                val = inner[start_text:i - 1]
                categories[category] = val
            else:
                break

        return categories

    @classmethod
    def _build_mediawiki_plural(cls, categories: Dict[str, str], locale: str, var_placeholder_num: int) -> str:
        parts: List[str] = []
        placeholder = f'${var_placeholder_num}'

        for cat, text in categories.items():
            if cat.startswith('='):
                num = cat[1:]
                text_with_placeholder = text.replace('#', placeholder)
                parts.append(f'{num}={text_with_placeholder}')

        ordered_cats = cls._get_plural_categories_for_locale(locale)
        for cat in ordered_cats:
            if cat in categories:
                cat_text = categories[cat]
            else:
                if cat in ('few', 'many'):
                    cat_text = (
                        categories.get('other')
                        or categories.get('many')
                        or categories.get('few')
                        or categories.get('one', '')
                    )
                else:
                    cat_text = categories.get('other') or categories.get('one', '')
            parts.append(cat_text.replace('#', placeholder))

        return f'{{{{PLURAL:{placeholder}|{"|".join(parts)}}}}}'

    @classmethod
    def _parse_mediawiki_categories(cls, inner: str, locale: str, var_placeholder_num: int) -> Dict[str, str]:
        parts: List[str] = []
        current = ''
        brace_count = 0
        length = len(inner)

        for i in range(length):
            char = inner[i]
            if char == '{':
                brace_count += 1
                current += char
            elif char == '}':
                brace_count -= 1
                current += char
            elif char == '|' and brace_count == 0:
                parts.append(current)
                current = ''
            else:
                current += char
        parts.append(current)

        categories: Dict[str, str] = {}
        positional_parts: List[str] = []

        for part in parts:
            m = re.match(r'^([0-9]+)=(.*)$', part, re.DOTALL)
            if m:
                num = m.group(1)
                val = m.group(2)
                categories[f'={num}'] = val
            else:
                positional_parts.append(part)

        ordered_cats = cls._get_plural_categories_for_locale(locale)
        for idx, part in enumerate(positional_parts):
            if idx < len(ordered_cats):
                cat = ordered_cats[idx]
                categories[cat] = part
            else:
                categories['other'] = part

        placeholder = f'${var_placeholder_num}'
        for cat, text in list(categories.items()):
            categories[cat] = text.replace(placeholder, '#')

        return categories

    @classmethod
    def _build_icu_plural(cls, var_name: str, categories: Dict[str, str]) -> str:
        if 'other' not in categories:
            categories['other'] = categories.get('many') or categories.get('few') or categories.get('one', '')

        parts = [f'{cat} {{{text}}}' for cat, text in categories.items()]
        return f'{{{var_name}, plural, {" ".join(parts)}}}'

    @classmethod
    def get_placeholder_variables(cls, text: str) -> List[str]:
        variables: List[str] = []
        length = len(text)
        i = 0

        while i < length:
            if text[i] == '{':
                start = i
                brace_count = 1
                i += 1
                comma_pos = -1

                while i < length and brace_count > 0:
                    if text[i] == '{':
                        brace_count += 1
                    elif text[i] == '}':
                        brace_count -= 1
                    elif text[i] == ',' and brace_count == 1 and comma_pos == -1:
                        comma_pos = i
                    i += 1

                block = text[start:i]

                if comma_pos != -1:
                    before_comma = text[start + 1:comma_pos]
                    var_name = before_comma.strip()

                    after_comma = text[comma_pos + 1:]
                    next_comma = after_comma.find(',')
                    if next_comma != -1:
                        type_name = after_comma[:next_comma].strip()
                        if type_name == 'plural':
                            if var_name and var_name not in variables:
                                variables.append(var_name)

                            second_comma_pos = comma_pos + 1 + next_comma
                            inner = text[second_comma_pos + 1:i - 1]
                            inner_vars = cls.get_placeholder_variables(inner)
                            for v in inner_vars:
                                if v not in variables:
                                    variables.append(v)
                else:
                    var_name = block[1:-1].strip()
                    if '{' in var_name:
                        inner_vars = cls.get_placeholder_variables(var_name)
                        for v in inner_vars:
                            if v not in variables:
                                variables.append(v)
                    else:
                        if var_name and var_name not in variables:
                            variables.append(var_name)
            elif text[i] == '_' and i + 1 < length and text[i + 1] == '_':
                m = re.match(r'^__([a-zA-Z0-9_-]+)__', text[i:])
                if m:
                    var_name = m.group(0)
                    if var_name not in variables:
                        variables.append(var_name)
                    i += len(var_name)
                else:
                    i += 1
            else:
                i += 1

        return variables

    @classmethod
    def _get_plural_categories_for_locale(cls, locale: str) -> List[str]:
        lang = locale.split('_')[0]
        if lang == 'cs':
            return ['one', 'few', 'other']
        elif lang == 'uk':
            return ['one', 'few', 'many']
        else:
            return ['one', 'other']
