import hashlib
import hmac
from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from polls.models import Poll, Vote, User
from .wikimedia_eligibility import WikimediaEligibilityChecker


class PollSecurity:
    @staticmethod
    def get_auth_key(poll: Poll) -> str:
        return f"poll-{poll.id}-auth"

    @staticmethod
    def generate_hash(poll: Poll) -> str:
        data = f"{poll.id}:{poll.password}".encode('utf-8')
        secret = settings.SECRET_KEY.encode('utf-8')
        return hmac.new(secret, data, hashlib.sha256).hexdigest()

    @classmethod
    def is_authenticated(cls, poll: Poll, session) -> bool:
        if not poll.password or poll.is_password_for_votes_only:
            return True

        expected_hash = cls.generate_hash(poll)
        session_hash = session.get(cls.get_auth_key(poll))
        if not session_hash or not isinstance(session_hash, str):
            return False

        return hmac.compare_digest(expected_hash, session_hash)

    @classmethod
    def is_authenticated_for_voting(cls, poll: Poll, session) -> bool:
        if not poll.password:
            return True
        expected_hash = cls.generate_hash(poll)
        session_hash = session.get(cls.get_auth_key(poll))
        if not session_hash or not isinstance(session_hash, str):
            return False
        return hmac.compare_digest(expected_hash, session_hash)

    @classmethod
    def authenticate(cls, poll: Poll, session) -> None:
        session[cls.get_auth_key(poll)] = cls.generate_hash(poll)

    @classmethod
    def verify_password(cls, poll: Poll, plain_password: str) -> bool:
        if not poll.password:
            return True
        # Support both Symfony password hash ($2y$ bcrypt, argon2, etc.) and Django hasher or plain comparison
        if poll.password.startswith(('pbkdf2_', 'bcrypt', '$2y$', '$2a$', '$argon2')):
            try:
                if check_password(plain_password, poll.password):
                    return True
            except Exception:
                pass
        return hmac.compare_digest(poll.password, plain_password)

    @classmethod
    def check_vote_eligibility(cls, poll: Poll, user, session) -> tuple[bool, list[str]]:
        if poll.is_closed:
            return False, ["Esta encuesta ha sido cerrada y no acepta nuevos votos."]

        if poll.password and not cls.is_authenticated_for_voting(poll, session):
            return False, ["Esta encuesta requiere ingresar la contraseña para votar."]

        checker = WikimediaEligibilityChecker()
        errors = checker.get_vote_eligibility_errors(poll, user)
        if errors:
            return False, errors

        return True, []
