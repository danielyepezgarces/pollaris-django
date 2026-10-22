from django import template
from django.utils.safestring import mark_safe
from django.utils.html import escape
from polls.services.translator import trans as translator_trans

register = template.Library()


@register.simple_tag
def icon(icon_name: str, additional_class_names: str = '') -> str:
    icon_name = escape(icon_name)
    additional_class_names = escape(additional_class_names)
    classes = f"icon icon--{icon_name}"
    if additional_class_names:
        classes += f" {additional_class_names}"

    svg = (
        f'<svg class="{classes}" aria-hidden="true" width="24" height="24">'
        f'<use xlink:href="/assets/icons.svg#{icon_name}"/>'
        f'</svg>'
    )
    return mark_safe(svg)


@register.filter
def get_item(dictionary, key):
    if isinstance(dictionary, dict):
        return dictionary.get(key)
    return None


@register.filter(name='trans')
def trans_filter(key, params=None):
    if not key:
        return ''
    if isinstance(params, dict):
        return translator_trans(str(key), params=params)
    return translator_trans(str(key))


@register.simple_tag
def trans_msg(key, **kwargs):
    return translator_trans(str(key), params=kwargs if kwargs else None)


@register.filter
def locale_to_bcp47(locale):
    if not locale:
        return ''
    return str(locale).replace('_', '-')
