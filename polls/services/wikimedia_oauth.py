import base64
import hashlib
import hmac
import json
import os
import secrets
import time
import urllib.parse
import requests
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
            f"{self.app_name}/2.0.0 ({self.app_base_url}; Wikimedia OAuth login)"
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

    def _rawurlencode(self, val: str) -> str:
        return urllib.parse.quote(str(val), safe='~')

    def send_signed_request(
        self,
        method: str,
        query_params: dict,
        token_secret: str = '',
        auth_overrides: dict = None
    ) -> str:
        auth_params = {
            'oauth_consumer_key': self.client_id,
            'oauth_nonce': secrets.token_hex(16),
            'oauth_signature_method': 'HMAC-SHA1',
            'oauth_timestamp': str(int(time.time())),
            'oauth_version': '1.0',
        }
        if auth_overrides:
            auth_params.update(auth_overrides)

        all_params = {**auth_params, **query_params}
        sorted_pairs = sorted([(self._rawurlencode(k), self._rawurlencode(v)) for k, v in all_params.items()])
        normalized_params = '&'.join(f"{k}={v}" for k, v in sorted_pairs)

        signature_base = '&'.join([
            method.upper(),
            self._rawurlencode(self.get_index_php_url()),
            self._rawurlencode(normalized_params)
        ])

        signing_key = f"{self._rawurlencode(self.client_secret)}&{self._rawurlencode(token_secret)}"
        signature = base64.b64encode(
            hmac.new(signing_key.encode('utf-8'), signature_base.encode('utf-8'), hashlib.sha1).digest()
        ).decode('utf-8')

        auth_params['oauth_signature'] = signature

        auth_header = 'OAuth ' + ', '.join([
            f'{self._rawurlencode(k)}="{self._rawurlencode(v)}"'
            for k, v in auth_params.items()
        ])

        headers = {
            'Authorization': auth_header,
            'User-Agent': self.user_agent,
        }

        res = requests.get(self.get_index_php_url(), params=query_params, headers=headers, timeout=15)
        res.raise_for_status()
        return res.text

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
        query_params = {
            'title': 'Special:OAuth/initiate',
            'format': 'json',
            'oauth_callback': 'oob',
        }
        res_text = self.send_signed_request('GET', query_params)
        payload = self.parse_token_response(res_text)
        return self.extract_token_pair(payload, 'request')

    def fetch_access_token(self, request_token: str, request_token_secret: str, oauth_verifier: str) -> dict:
        query_params = {
            'title': 'Special:OAuth/token',
            'format': 'json',
        }
        auth_overrides = {
            'oauth_token': request_token,
            'oauth_verifier': oauth_verifier,
        }
        res_text = self.send_signed_request('GET', query_params, token_secret=request_token_secret, auth_overrides=auth_overrides)
        payload = self.parse_token_response(res_text)
        return self.extract_token_pair(payload, 'access')

    def identify(self, access_token: str, access_token_secret: str) -> dict:
        query_params = {
            'title': 'Special:OAuth/identify',
        }
        auth_overrides = {
            'oauth_token': access_token,
        }
        res_text = self.send_signed_request('GET', query_params, token_secret=access_token_secret, auth_overrides=auth_overrides)

        jwt_raw = res_text.strip()
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
                    raise RuntimeError(str(data.get('message') or data['error']))
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
