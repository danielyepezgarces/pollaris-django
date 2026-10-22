import re
import requests
from datetime import datetime
from dateutil.relativedelta import relativedelta
from django.utils import timezone
from polls.models import Poll, User


class WikimediaEligibilityChecker:
    def __init__(self, oauth_base_url: str = 'https://meta.wikimedia.org/w/rest.php/oauth'):
        self.oauth_base_url = oauth_base_url
        self._site_matrix = None

    def has_eligibility_restrictions(self, poll: Poll) -> bool:
        return (
            poll.min_wikimedia_account_age_months is not None
            or (poll.min_wikimedia_edits_project is not None and poll.min_wikimedia_edits_count is not None)
        )

    def get_vote_eligibility_errors(self, poll: Poll, user) -> list[str]:
        if not self.has_eligibility_restrictions(poll):
            return []

        if not user or not user.is_authenticated:
            return ["Esta encuesta requiere iniciar sesión con una cuenta de Wikimedia para votar."]

        try:
            return self._check_eligibility(poll, user)
        except Exception as e:
            return [f"No se pudieron verificar los requisitos de Wikimedia en este momento: {e}"]

    def _check_eligibility(self, poll: Poll, user: User) -> list[str]:
        errors = []
        username = user.username

        # 1. Check account age
        if poll.min_wikimedia_account_age_months is not None:
            global_info = self._fetch_global_user_info(username)
            registration = global_info.get('registration')

            if not registration:
                raise RuntimeError("No se encontró la fecha de registro en Wikimedia.")

            # Format: '2020-05-10T14:30:00Z'
            registered_at = datetime.fromisoformat(registration.replace('Z', '+00:00'))
            min_months = poll.min_wikimedia_account_age_months
            cutoff_date = timezone.now() - relativedelta(months=min_months)

            if registered_at > cutoff_date:
                errors.append(
                    f"Tu cuenta de Wikimedia debe tener al menos {min_months} meses de antigüedad para participar."
                )

        # 2. Check project edit count
        if poll.min_wikimedia_edits_project and poll.min_wikimedia_edits_count is not None:
            project = poll.min_wikimedia_edits_project
            project_info = self._fetch_project_user_info(username, project)
            edit_count = project_info.get('editcount')

            if edit_count is None:
                raise RuntimeError("No se pudo obtener el número de ediciones en Wikimedia.")

            if edit_count < poll.min_wikimedia_edits_count:
                errors.append(
                    f"Tu cuenta debe tener al menos {poll.min_wikimedia_edits_count} ediciones en el proyecto {project} para participar."
                )

        return errors

    def _get_meta_api_url(self) -> str:
        if self.oauth_base_url.endswith('/w/rest.php/oauth'):
            return self.oauth_base_url[:-len('/w/rest.php/oauth')] + '/w/api.php'
        return 'https://meta.wikimedia.org/w/api.php'

    def _fetch_global_user_info(self, username: str) -> dict:
        url = self._get_meta_api_url()
        params = {
            'action': 'query',
            'meta': 'globaluserinfo',
            'guiuser': username,
            'guiprop': 'editcount|merged',
            'format': 'json',
        }
        res = requests.get(url, params=params, headers={'User-Agent': 'Pollaris/1.1 (Wikimedia eligibility)'}, timeout=10)
        res.raise_for_status()
        data = res.json()
        return data.get('query', {}).get('globaluserinfo', {})

    def _fetch_project_user_info(self, username: str, project_dbname: str) -> dict:
        project_url = self._get_project_api_url(project_dbname)
        params = {
            'action': 'query',
            'list': 'users',
            'ususers': username,
            'usprop': 'editcount|registration',
            'format': 'json',
        }
        res = requests.get(project_url, params=params, headers={'User-Agent': 'Pollaris/1.1 (Wikimedia eligibility)'}, timeout=10)
        res.raise_for_status()
        data = res.json()
        users = data.get('query', {}).get('users', [])
        return users[0] if users else {}

    def _get_project_api_url(self, project_dbname: str) -> str:
        project_dbname = project_dbname.strip().lower()
        if not re.match(r'^[a-z0-9_-]+$', project_dbname):
            raise RuntimeError('Nombre de proyecto inválido.')

        site_matrix = self._get_site_matrix()
        if project_dbname not in site_matrix:
            raise RuntimeError(f"Base de datos de proyecto Wikimedia '{project_dbname}' desconocida.")

        return site_matrix[project_dbname]

    def _get_site_matrix(self) -> dict[str, str]:
        if self._site_matrix is not None:
            return self._site_matrix

        url = self._get_meta_api_url()
        params = {
            'action': 'sitematrix',
            'formatversion': '2',
            'format': 'json',
        }
        res = requests.get(url, params=params, headers={'User-Agent': 'Pollaris/1.1 (Wikimedia eligibility)'}, timeout=10)
        res.raise_for_status()
        data = res.json()

        matrix = {}
        for key, entry in data.get('sitematrix', {}).items():
            if key == 'count':
                continue
            if key == 'specials' and isinstance(entry, list):
                for site in entry:
                    self._register_site(matrix, site)
                continue
            if isinstance(entry, dict):
                for site in entry.get('site', []):
                    self._register_site(matrix, site)

        self._site_matrix = matrix
        return self._site_matrix

    def _register_site(self, matrix: dict, site: dict):
        if not isinstance(site, dict):
            return
        dbname = site.get('dbname')
        url = site.get('url')
        if dbname and url and not site.get('closed'):
            matrix[dbname.lower()] = url.rstrip('/') + '/w/api.php'
