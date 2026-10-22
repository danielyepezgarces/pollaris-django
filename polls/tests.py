from django.test import TestCase, RequestFactory
from django.core.management import call_command
from polls.services.translation_converter import TranslationConverter
from polls.services.translator import (
    trans,
    set_current_locale,
    match_accept_language,
    SUPPORTED_LOCALES,
)
from polls.middleware import LocaleMiddleware
from polls.views import preferences_view


class TranslationConverterTest(TestCase):
    def test_icu_to_mediawiki_simple(self):
        icu = "Hello {name}, your code is {code}!"
        mw = TranslationConverter.convert_icu_to_mediawiki(icu, 'en_GB')
        self.assertEqual(mw, "Hello $1, your code is $2!")

    def test_mediawiki_to_icu_simple(self):
        mw = "Hello $1, your code is $2!"
        source_en = "Hello {name}, your code is {code}!"
        icu = TranslationConverter.convert_mediawiki_to_icu(mw, 'en_GB', source_en)
        self.assertEqual(icu, "Hello {name}, your code is {code}!")

    def test_icu_plural_roundtrip(self):
        icu = "{count, plural, one {# day} other {# days}}"
        mw = TranslationConverter.convert_icu_to_mediawiki(icu, 'en_GB')
        self.assertIn("{{PLURAL:$1|$1 day|$1 days}}", mw)

        recovered = TranslationConverter.convert_mediawiki_to_icu(mw, 'en_GB', icu)
        self.assertIn("one {# day}", recovered)
        self.assertIn("other {# days}", recovered)


class TranslatorServiceTest(TestCase):
    def test_trans_basic(self):
        set_current_locale('en_GB')
        self.assertEqual(trans('admin.title'), 'Administration')

        set_current_locale('es')
        self.assertEqual(trans('admin.title'), 'Administración')

    def test_trans_interpolation(self):
        set_current_locale('en_GB')
        res = trans('base.default_title', {'appName': 'Pollaris'})
        self.assertEqual(res, 'Welcome to Pollaris!')

    def test_trans_icu_plural(self):
        set_current_locale('en_GB')
        one = trans('home.stats.votes', {'count': 1})
        self.assertEqual('vote cast', one)

        multiple = trans('home.stats.votes', {'count': 5})
        self.assertEqual('votes cast', multiple)

    def test_match_accept_language(self):
        self.assertEqual(match_accept_language('es-ES,es;q=0.9'), 'es_ES')
        self.assertEqual(match_accept_language('de-DE,de;q=0.8'), 'de')
        self.assertEqual(match_accept_language('unknown;q=0.1'), 'en_GB')


class ManagementCommandsTest(TestCase):
    def test_translatewiki_commands(self):
        # Test export and import commands run without error
        call_command('translatewiki_import')
        call_command('translatewiki_export')


class LocaleMiddlewareAndPreferencesTest(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_locale_middleware_query_param(self):
        request = self.factory.get('/?_locale=es')
        request.session = {}
        request.COOKIES = {}
        middleware = LocaleMiddleware(lambda req: None)
        middleware.process_request(request)
        self.assertEqual(request.locale, 'es')
        self.assertEqual(request.session.get('_locale'), 'es')

    def test_locale_middleware_accept_language(self):
        request = self.factory.get('/', HTTP_ACCEPT_LANGUAGE='fr-FR,fr;q=0.9')
        request.session = {}
        request.COOKIES = {}
        middleware = LocaleMiddleware(lambda req: None)
        middleware.process_request(request)
        self.assertEqual(request.locale, 'fr_FR')

    def test_preferences_post(self):
        from django.contrib.messages.storage.fallback import FallbackStorage
        request = self.factory.post('/preferences', {'locale': 'de'})
        request.session = {}
        request.COOKIES = {}
        setattr(request, '_messages', FallbackStorage(request))
        response = preferences_view(request)
        self.assertEqual(request.session.get('_locale'), 'de')
        self.assertIn('_locale', response.cookies)
        self.assertEqual(response.cookies['_locale'].value, 'de')
