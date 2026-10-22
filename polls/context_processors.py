from polls.services.translator import get_current_locale, SUPPORTED_LOCALES


def global_context(request):
    locale = getattr(request, 'locale', None) or get_current_locale()
    bcp47 = locale.replace('_', '-')
    return {
        'appName': 'Pollaris',
        'current_locale': locale,
        'bcp47_locale': bcp47,
        'supported_locales': SUPPORTED_LOCALES,
    }
