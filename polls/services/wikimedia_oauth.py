import base64
import hashlib
import hmac
import json
import os
import urllib.parse
import requests
from requests_oauthlib import OAuth1
from django.conf import settings
from polls.models import User, generate_hex_id


class WikimediaOAuth:
    SESSION_REQUEST_TOKEN_KEY = 'wikimedia_oauth_request_token_key'
    SESSION_REQUEST_TOKEN_SECRET = 'wikimedia_oauth_request_token_secret'

    def __init__(self):
        self.enabled = os.getenv('WIKIMEDIA_OAUTH_ENABLED', 'false').lower() in ('true', '1', 'yes')
        self.base_url = os.getenv('WIKIMEDIA_OAUTH_BASE_URL', 'https://meta.wikimedia.org/w/rest.php/oauth')
        self.client_id = os.getenv('WIKIMEDIA_OAUTH_CLIENT_ID', '')
        self.client_secret = os.getenv('WIKIMEDIA_OAUTH_CLIENT_SECRET', '')
        self.app_name = os.getenv('APP_NAME', 'Pollaris')
        self.app_base_url = os.getenv('APP_BASE_URL', 'https://pollaris.toolforge.org')
        self.user_agent = os.getenv(
            'WIKIMEDIA_OAUTH_USER_AGENT',
            f"{self.app_name}/1.1.3 ({self.app_base_url}; Wikimedia OAuth login)"
        )

    def is_configured(self) -> bool:
        return bool(self.enabled and self.base_url and self.client_id and self.client_secret)

    def get_index_php_url(self) -> str:
        base = self.base_url.rstrip('/')
        if base.endswith('/w/index.php'):
            return base
        if base.endswith('/w/rest.php/oauth'):
            return base[:-len('/w/rest.php/oauth')] + '/w/index.php'
        if base.endswith('/wiki/Special:OAuth'):
            return base[:-len('/wiki/Special:OAuth')] + '/w/index.php'
        return f"{base}/w/index.php"

    def build_authorization_url(self, session) -> str:
        token = self.fetch_request_token()
        session[self.SESSION_REQUEST_TOKEN_KEY] = token['key']
        session[self.SESSION_REQUEST_TOKEN_SECRET] = token['secret']

        params = urllib.parse.urlencode({
            'title': 'Special:OAuth/authenticate',
            'oauth_token': token['key'],
            'oauth_consumer_key': self.client_id,
        })
        return f"{self.get_index_php_url()}?{params}"

    def has_valid_callback(self, session, oauth_token: str, oauth_verifier: str) -> bool:
        request_token = session.get(self.SESSION_REQUEST_TOKEN_KEY)
        request_token_secret = session.get(self.SESSION_REQUEST_TOKEN_SECRET)

        if not request_token or not request_token_secret or not oauth_verifier:
            return False

        if oauth_token and not hmac.compare_digest(request_token, oauth_token):
            return False

        return True

    def fetch_profile(self, session, oauth_verifier: str) -> dict:
        request_token = session.get(self.SESSION_REQUEST_TOKEN_KEY, '')
        request_token_secret = session.get(self.SESSION_REQUEST_TOKEN_SECRET, '')

        session.pop(self.SESSION_REQUEST_TOKEN_KEY, None)
        session.pop(self.SESSION_REQUEST_TOKEN_SECRET, None)

        if not request_token or not request_token_secret:
            raise RuntimeError('Missing Wikimedia request token in session.')

        access_token = self.fetch_access_token(request_token, request_token_secret, oauth_verifier)
        return self.identify(access_token['key'], access_token['secret'])

    def fetch_request_token(self) -> dict:
        auth = OAuth1(
            self.client_id,
            client_secret=self.client_secret,
            callback_uri='oob',
            signature_type='auth_header'
        )
        url = self.get_index_php_url()
        params = {
            'title': 'Special:OAuth/initiate',
            'format': 'json',
            'oauth_callback': 'oob',
        }
        headers = {'User-Agent': self.user_agent}

        res = requests.get(url, params=params, auth=auth, headers=headers, timeout=10)
        res.raise_for_status()

        payload = self.parse_token_response(res.text)
        return self.extract_token_pair(payload, 'request')

    def fetch_access_token(self, request_token: str, request_token_secret: str, oauth_verifier: str) -> dict:
        auth = OAuth1(
            self.client_id,
            client_secret=self.client_secret,
            resource_owner_key=request_token,
            resource_owner_secret=request_token_secret,
            verifier=oauth_verifier,
            signature_type='auth_header'
        )
        url = self.get_index_php_url()
        params = {
            'title': 'Special:OAuth/token',
            'format': 'json',
        }
        headers = {'User-Agent': self.user_agent}

        res = requests.get(url, params=params, auth=auth, headers=headers, timeout=10)
        res.raise_for_status()

        payload = self.parse_token_response(res.text)
        return self.extract_token_pair(payload, 'access')

    def identify(self, access_token: str, access_token_secret: str) -> dict:
        auth = OAuth1(
            self.client_id,
            client_secret=self.client_secret,
            resource_owner_key=access_token,
            resource_owner_secret=access_token_secret,
            signature_type='auth_header'
        )
        url = self.get_index_php_url()
        params = {
            'title': 'Special:OAuth/identify',
        }
        headers = {'User-Agent': self.user_agent}

        res = requests.get(url, params=params, auth=auth, headers=headers, timeout=10)
        res.raise_for_status()

        jwt_raw = res.text.strip()
        payload = self.decode_identity_jwt(jwt_raw)
        payload['username'] = payload.get('username')
        payload['sub'] = str(payload.get('sub') or payload.get('username') or '')
        return payload

    def parse_token_response(self, text: str) -> dict:
        text = text.strip()
        if text.startswith('Error: '):
            raise RuntimeError(text[len('Error: '):])

        try:
            data = json.loads(text)
            if isinstance(data, dict):
                if 'error' in data:
                    raise RuntimeError(str(data['error']))
                return data
        except json.JSONDecodeError:
            pass

        parsed = urllib.parse.parse_qs(text)
        return {k: v[0] for k, v in parsed.items()}

    def extract_token_pair(self, payload: dict, kind: str) -> dict:
        if 'key' in payload and 'secret' in payload:
            key, secret = payload['key'], payload['secret']
        elif 'token' in payload and 'key' in payload:
            key, secret = payload['token'], payload['key']
        else:
            key = payload.get('oauth_token')
            secret = payload.get('oauth_token_secret')

        if not key or not secret:
            raise RuntimeError(f"Wikimedia did not return a valid {kind} token.")

        return {'key': key, 'secret': secret}

    def decode_identity_jwt(self, jwt_str: str) -> dict:
        parts = jwt_str.split('.')
        if len(parts) != 3:
            raise RuntimeError("Could not read response from 'Special:OAuth/identify'.")

        encoded_header, encoded_payload, encoded_sig = parts

        header_bytes = self.base64url_decode(encoded_header)
        header = json.loads(header_bytes.decode('utf-8'))
        if header.get('typ') != 'JWT' or header.get('alg') != 'HS256':
            raise RuntimeError('Invalid Wikimedia identity token header.')

        expected_sig = hmac.new(
            self.client_secret.encode('utf-8'),
            f"{encoded_header}.{encoded_payload}".encode('utf-8'),
            hashlib.sha256
        ).digest()

        sig = self.base64url_decode(encoded_sig)
        if not hmac.compare_digest(expected_sig, sig):
            raise RuntimeError('Unable to verify Wikimedia identity token signature.')

        payload_bytes = self.base64url_decode(encoded_payload)
        return json.loads(payload_bytes.decode('utf-8'))

    def base64url_decode(self, val: str) -> bytes:
        rem = len(val) % 4
        if rem > 0:
            val += '=' * (4 - rem)
        return base64.urlsafe_b64decode(val.encode('ascii'))

    def synchronize_wikimedia_user(self, profile: dict) -> User:
        wikimedia_id = str(profile.get('sub') or profile.get('username') or '').strip()
        wikimedia_username = str(profile.get('username') or '').strip()

        if not wikimedia_id or not wikimedia_username:
            raise RuntimeError('Wikimedia profile is incomplete.')

        user = User.objects.filter(wikimedia_id=wikimedia_id).first()

        if not user:
            existing_by_username = User.objects.filter(username=wikimedia_username).first()
            if existing_by_username and existing_by_username.wikimedia_id is None:
                user = existing_by_username
                user.wikimedia_id = wikimedia_id
            else:
                user = User(
                    wikimedia_id=wikimedia_id,
                    username=self.build_available_username(wikimedia_username),
                    roles=['ROLE_USER'],
                    password=generate_hex_id(64),
                )
        else:
            user.wikimedia_id = wikimedia_id
            if user.username != wikimedia_username:
                user.username = self.build_available_username(wikimedia_username, current_user=user)

        real_name = str(profile.get('realname') or '').strip()
        email = str(profile.get('email') or '').strip()

        user.real_name = real_name if real_name else None
        user.email = email if email else None
        user.save()

        return user

    def build_available_username(self, preferred_username: str, current_user: User = None) -> str:
        base_username = preferred_username[:User.MAX_USERNAME_LENGTH]

        existing = User.objects.filter(username=base_username).first()
        if existing is None or (current_user and existing.id == current_user.id):
            return base_username

        suffix = 2
        while True:
            candidate = f"{base_username[:User.MAX_USERNAME_LENGTH - len(str(suffix)) - 1]}_{suffix}"
            existing = User.objects.filter(username=candidate).first()
            if existing is None or (current_user and existing.id == current_user.id):
                return candidate
            suffix += 1
