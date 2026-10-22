from django import template
from django.utils.safestring import mark_safe
from django.utils.html import escape

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
