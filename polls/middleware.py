from django.utils.deprecation import MiddlewareMixin
from polls.services.translator import (
    match_accept_language,
    set_current_locale,
    DEFAULT_LOCALE,
    SUPPORTED_LOCALES,
)


class LocaleMiddleware(MiddlewareMixin):
    """
    Sets the active request locale based on:
    1. Query param: ?_locale=...
    2. Session: request.session['_locale']
    3. Cookie: request.COOKIES['_locale']
    4. HTTP_ACCEPT_LANGUAGE
    5. Default 'en_GB'
    """

    def process_request(self, request):
        locale = None

        # 1. Query parameter
        query_locale = request.GET.get('_locale')
        if query_locale and query_locale in SUPPORTED_LOCALES:
            locale = query_locale
            if hasattr(request, 'session'):
                request.session['_locale'] = locale

        # 2. Session
        if not locale and hasattr(request, 'session'):
            session_locale = request.session.get('_locale')
            if session_locale and session_locale in SUPPORTED_LOCALES:
                locale = session_locale

        # 3. Cookie
        if not locale:
            cookie_locale = request.COOKIES.get('_locale')
            if cookie_locale and cookie_locale in SUPPORTED_LOCALES:
                locale = cookie_locale

        # 4. Accept-Language header
        if not locale:
            accept_lang = request.META.get('HTTP_ACCEPT_LANGUAGE')
            locale = match_accept_language(accept_lang)

        if not locale:
            locale = DEFAULT_LOCALE

        request.locale = locale
        set_current_locale(locale)

    def process_response(self, request, response):
        locale = getattr(request, 'locale', None)
        if locale and request.COOKIES.get('_locale') != locale:
            response.set_cookie('_locale', locale, max_age=365 * 24 * 3600, samesite='Lax')
        return response
