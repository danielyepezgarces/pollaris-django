import csv
import io
import json
import logging
from django.shortcuts import render, redirect, get_object_or_404
from django.http import HttpResponse, Http404, JsonResponse
from django.core.paginator import Paginator
from django.db import models
from django.db.models import Count, Q
from django.contrib import messages
from django.contrib.auth import login as auth_login, logout as auth_logout
from django.utils import timezone

from .models import Poll, Proposal, Date, Vote, Answer, Comment, User, PollCohost, generate_hex_id
from .services.wikimedia_oauth import WikimediaOAuth
from .services.poll_security import PollSecurity

logger = logging.getLogger(__name__)

AVAILABLE_LANGUAGES = [
    ('en', 'English'),
    ('es', 'Español'),
    ('fr', 'Français'),
    ('de', 'Deutsch'),
    ('it', 'Italiano'),
    ('pt', 'Português'),
    ('ca', 'Català'),
    ('eo', 'Esperanto'),
]


def format_number_short(num: int) -> str:
    if num < 10000:
        return str(num)
    if num < 1000000:
        return f"{round(num / 1000, 1)}K"
    return f"{round(num / 1000000, 1)}M"


def home_view(request):
    try:
        polls_count = Poll.objects.count()
        votes_count = Vote.objects.count()
        voters_count = Vote.objects.values('author_name').distinct().count()
        comments_count = Comment.objects.count()
    except Exception:
        polls_count = votes_count = voters_count = comments_count = 0

    return render(request, 'home/show.html', {
        'polls_count': polls_count,
        'polls_count_formatted': format_number_short(polls_count),
        'votes_count': votes_count,
        'votes_count_formatted': format_number_short(votes_count),
        'voters_count': voters_count,
        'voters_count_formatted': format_number_short(voters_count),
        'comments_count': comments_count,
        'comments_count_formatted': format_number_short(comments_count),
        'pollsCount': polls_count,
        'pollsCountFormatted': format_number_short(polls_count),
        'votesCount': votes_count,
        'votesCountFormatted': format_number_short(votes_count),
        'votersCount': voters_count,
        'votersCountFormatted': format_number_short(voters_count),
        'commentsCount': comments_count,
        'commentsCountFormatted': format_number_short(comments_count),
    })


def public_polls_view(request):
    page_num = request.GET.get('page', 1)
    polls_list = Poll.objects.filter(is_publicly_listed=True, completed_at__isnull=False).order_by('-created_at')
    paginator = Paginator(polls_list, 20)
    page_obj = paginator.get_page(page_num)

    return render(request, 'polls/public.html', {
        'page_obj': page_obj,
    })


def choose_poll_type_view(request):
    return render(request, 'polls/choose.html')


def new_poll_view(request):
    poll_type = request.GET.get('type', 'classic')
    if poll_type not in ['classic', 'date']:
        poll_type = 'classic'

    if request.method == 'POST':
        title = request.POST.get('title', '').strip()
        description = request.POST.get('description', '').strip()
        author_name = request.POST.get('author_name', '').strip()
        author_email = request.POST.get('author_email', '').strip()
        locale = request.POST.get('locale', 'es')

        if not title:
            messages.error(request, 'El título es obligatorio.')
            return render(request, 'polls/new.html', {'poll_type': poll_type})

        poll = Poll(
            title=title,
            description=description,
            author_name=author_name or (request.user.display_name if request.user.is_authenticated else 'Anónimo'),
            author_email=author_email or (request.user.email if request.user.is_authenticated else ''),
            type=poll_type,
            locale=locale,
            owner=request.user if request.user.is_authenticated else None,
            completed_at=timezone.now(),
        )
        poll.slug = generate_hex_id(12)
        poll.save()

        # Handle proposals
        proposals_raw = request.POST.getlist('proposals')
        if not proposals_raw:
            proposals_text = request.POST.get('proposals_text', '')
            proposals_raw = [p.strip() for p in proposals_text.splitlines() if p.strip()]

        for prop_label in proposals_raw:
            if prop_label.strip():
                Proposal.objects.create(poll=poll, label=prop_label.strip())

        messages.success(request, '¡Encuesta creada con éxito!')
        return redirect('poll', slug=poll.slug)

    return render(request, 'polls/new.html', {
        'poll_type': poll_type,
    })


# --- Wizard & Administration Steps ---

def edit_poll_proposals_view(request, id, token):
    poll = get_object_or_404(Poll, id=id, admin_token=token)

    if request.method == 'POST':
        proposals_text = request.POST.get('proposals_text', '')
        labels = [line.strip() for line in proposals_text.splitlines() if line.strip()]

        # Replace existing proposals
        poll.proposals.all().delete()
        for label in labels:
            Proposal.objects.create(poll=poll, label=label)

        messages.success(request, 'Propuestas actualizadas.')
        return redirect('edit poll settings', id=poll.id, token=poll.admin_token)

    current_proposals_text = "\n".join(p.label for p in poll.proposals.all().order_by('id'))
    return render(request, 'polls/proposals.html', {
        'poll': poll,
        'current_proposals_text': current_proposals_text,
    })


def edit_poll_dates_view(request, id, token):
    poll = get_object_or_404(Poll, id=id, admin_token=token)

    if request.method == 'POST':
        dates_text = request.POST.get('dates_text', '')
        labels = [line.strip() for line in dates_text.splitlines() if line.strip()]

        # Replace existing proposals/dates
        poll.proposals.all().delete()
        poll.dates.all().delete()

        for label in labels:
            Proposal.objects.create(poll=poll, label=label)

        messages.success(request, 'Fechas actualizadas.')
        return redirect('edit poll settings', id=poll.id, token=poll.admin_token)

    current_dates_text = "\n".join(p.label for p in poll.proposals.all().order_by('id'))
    return render(request, 'polls/dates.html', {
        'poll': poll,
        'current_dates_text': current_dates_text,
    })


def edit_poll_settings_view(request, id, token):
    poll = get_object_or_404(Poll, id=id, admin_token=token)

    if request.method == 'POST':
        poll.disable_maybe = bool(request.POST.get('disable_maybe'))
        poll.vote_no_by_default = bool(request.POST.get('vote_no_by_default'))
        poll.are_results_public = bool(request.POST.get('are_results_public'))
        poll.is_publicly_listed = bool(request.POST.get('is_publicly_listed'))
        poll.is_password_for_votes_only = bool(request.POST.get('is_password_for_votes_only'))
        poll.edit_vote_mode = request.POST.get('edit_vote_mode', 'own')

        max_votes_raw = request.POST.get('max_votes', '').strip()
        poll.max_votes = int(max_votes_raw) if max_votes_raw.isdigit() else None

        password_raw = request.POST.get('password', '').strip()
        poll.password = password_raw

        # Wikimedia eligibility
        age_raw = request.POST.get('min_wikimedia_account_age_months', '').strip()
        poll.min_wikimedia_account_age_months = int(age_raw) if age_raw.isdigit() else None

        project_raw = request.POST.get('min_wikimedia_edits_project', '').strip()
        poll.min_wikimedia_edits_project = project_raw if project_raw else None

        edits_raw = request.POST.get('min_wikimedia_edits_count', '').strip()
        poll.min_wikimedia_edits_count = int(edits_raw) if edits_raw.isdigit() else None

        poll.save()
        messages.success(request, 'Ajustes actualizados correctamente.')
        return redirect('poll summary', id=poll.id, token=poll.admin_token)

    return render(request, 'polls/settings.html', {
        'poll': poll,
    })


def poll_summary_view(request, id, token):
    poll = get_object_or_404(Poll, id=id, admin_token=token)
    proposals = poll.proposals.all().order_by('id')

    return render(request, 'polls/summary.html', {
        'poll': poll,
        'proposals': proposals,
    })


def complete_poll_view(request, id, token):
    poll = get_object_or_404(Poll, id=id, admin_token=token)

    if request.method == 'POST':
        poll.completed_at = timezone.now()
        if not poll.slug:
            poll.slug = generate_hex_id(12)
        poll.save()
        messages.success(request, '¡La encuesta ha sido publicada exitosamente!')
        return redirect('poll', slug=poll.slug)

    return redirect('poll summary', id=poll.id, token=poll.admin_token)


def edit_poll_titles_view(request, id, token):
    poll = get_object_or_404(Poll, id=id, admin_token=token)
    current_titles = poll.localized_titles or {}

    if request.method == 'POST':
        new_titles = {}
        for code, _ in AVAILABLE_LANGUAGES:
            val = request.POST.get(f"title_{code}", '').strip()
            if val:
                new_titles[code] = val
        poll.localized_titles = new_titles or None
        poll.save()
        messages.success(request, 'Traducciones del título guardadas.')
        return redirect('poll admin', id=poll.id, token=poll.admin_token)

    return render(request, 'polls/edit_titles.html', {
        'poll': poll,
        'available_languages': AVAILABLE_LANGUAGES,
        'current_titles': current_titles,
    })


def edit_poll_descriptions_view(request, id, token):
    poll = get_object_or_404(Poll, id=id, admin_token=token)
    current_descriptions = poll.localized_descriptions or {}

    if request.method == 'POST':
        new_descs = {}
        for code, _ in AVAILABLE_LANGUAGES:
            val = request.POST.get(f"desc_{code}", '').strip()
            if val:
                new_descs[code] = val
        poll.localized_descriptions = new_descs or None
        poll.save()
        messages.success(request, 'Traducciones de la descripción guardadas.')
        return redirect('poll admin', id=poll.id, token=poll.admin_token)

    return render(request, 'polls/edit_descriptions.html', {
        'poll': poll,
        'available_languages': AVAILABLE_LANGUAGES,
        'current_descriptions': current_descriptions,
    })


# --- Poll View & Voting ---

def poll_view(request, slug):
    poll = Poll.objects.filter(models.Q(slug=slug) | models.Q(id=slug)).first()
    if not poll:
        raise Http404("Poll does not exist")

    # 1. Check full password protection
    if not PollSecurity.is_authenticated(poll, request.session):
        return redirect('authenticate poll', slug=poll.slug)

    if poll.is_date_poll:
        dates_and_proposals = poll.get_proposals_by_dates()
        proposals = []
        for _, props in dates_and_proposals:
            proposals.extend(props)

        if not proposals:
            proposals = list(poll.proposals.all().order_by('id'))

        month_groups = []
        current_month_key = None
        for date, date_proposals in dates_and_proposals:
            month_key = date.value.strftime('%Y-%m')
            month_label = date.value.strftime('%B %Y')
            if current_month_key != month_key:
                month_groups.append({
                    'key': month_key,
                    'label': month_label,
                    'date_groups': [[date, date_proposals]],
                    'colspan': len(date_proposals),
                })
                current_month_key = month_key
            else:
                month_groups[-1]['colspan'] += len(date_proposals)
                month_groups[-1]['date_groups'].append([date, date_proposals])
    else:
        dates_and_proposals = []
        month_groups = []
        proposals = list(poll.proposals.all().order_by('id'))

    votes = Vote.objects.filter(models.Q(poll=poll) | models.Q(poll_id=poll.id)).order_by('created_at').prefetch_related('answers', 'owner')
    comments = poll.comments.all().order_by('created_at')
    can_view_results = poll.are_results_public or poll.can_administer(request.user)
    poll_votes_count = poll.votes.count()

    # Current user's vote
    my_vote = None
    if request.user.is_authenticated:
        my_vote = Vote.objects.filter(poll=poll, owner=request.user).first()
        if not my_vote:
            unclaimed = Vote.objects.filter(poll=poll, owner__isnull=True, author_name=request.user.username).first()
            if unclaimed:
                unclaimed.owner = request.user
                unclaimed.save()
                my_vote = unclaimed
    else:
        vote_id = request.session.get(f"vote-{poll.id}")
        if vote_id:
            my_vote = Vote.objects.filter(id=vote_id, poll=poll).first()

    # Check eligibility to vote
    can_vote, eligibility_errors = PollSecurity.check_vote_eligibility(poll, request.user, request.session)

    if request.method == 'POST':
        action = request.POST.get('action', 'vote')

        if action == 'vote':
            if not can_vote:
                for err in eligibility_errors:
                    messages.error(request, err)
                return redirect('poll', slug=poll.slug)

            author_name = request.POST.get('author_name', '').strip()
            if not author_name and request.user.is_authenticated:
                author_name = request.user.display_name

            if not author_name:
                author_name = 'Participante'

            vote = my_vote or Vote(poll=poll)
            vote.author_name = author_name
            if request.user.is_authenticated:
                vote.owner = request.user
            vote.save()

            if not request.user.is_authenticated:
                request.session[f"vote-{poll.id}"] = vote.id

            for proposal in proposals:
                val = request.POST.get(f"proposal_{proposal.id}")
                answer, _ = Answer.objects.get_or_create(vote=vote, proposal=proposal)
                answer.value = val
                answer.save()

            messages.success(request, '¡Tu voto ha sido registrado!')
            return redirect('poll', slug=poll.slug)

        elif action == 'comment':
            author_name = request.POST.get('author_name', '').strip()
            content = request.POST.get('content', '').strip()
            if content:
                Comment.objects.create(
                    poll=poll,
                    author_name=author_name or (request.user.display_name if request.user.is_authenticated else 'Anónimo'),
                    content=content
                )
                messages.success(request, 'Comentario añadido.')
                return redirect('poll', slug=poll.slug)

    # Build summary matrix
    results = {}
    for proposal in proposals:
        yes_count = Answer.objects.filter(proposal=proposal, value='yes').count()
        maybe_count = Answer.objects.filter(proposal=proposal, value='maybe').count()
        no_count = Answer.objects.filter(proposal=proposal, value='no').count()
        results[proposal.id] = {
            'yes': yes_count,
            'maybe': maybe_count,
            'no': no_count,
            'total': yes_count + maybe_count + no_count,
        }

    return render(request, 'polls/show.html', {
        'poll': poll,
        'proposals': proposals,
        'dates_and_proposals': dates_and_proposals,
        'month_groups': month_groups,
        'votes': votes,
        'comments': comments,
        'my_vote': my_vote,
        'results': results,
        'preferred_choices': poll.preferred_choices,
        'can_vote': can_vote,
        'can_view_results': can_view_results,
        'poll_votes_count': poll_votes_count,
        'eligibility_errors': eligibility_errors,
    })


def edit_vote_view(request, slug, vote_id):
    poll = get_object_or_404(Poll, slug=slug)
    vote = get_object_or_404(Vote, id=vote_id, poll=poll)

    # Permission check: can current user edit this vote?
    is_owner = (request.user.is_authenticated and vote.owner_id == request.user.id)
    is_session_voter = (request.session.get(f"vote-{poll.id}") == vote.id)

    if poll.is_closed or poll.edit_vote_mode == 'no':
        messages.error(request, 'Esta encuesta no permite modificar votos.')
        return redirect('poll', slug=poll.slug)

    if poll.edit_vote_mode == 'own' and not (is_owner or is_session_voter):
        messages.error(request, 'No tienes permiso para modificar este voto.')
        return redirect('poll', slug=poll.slug)

    proposals = poll.proposals.all().order_by('id')
    existing_answers = {a.proposal_id: a.value for a in vote.answers.all()}

    if request.method == 'POST':
        author_name = request.POST.get('author_name', '').strip()
        if not is_owner and author_name:
            vote.author_name = author_name
            vote.save()

        for proposal in proposals:
            val = request.POST.get(f"proposal_{proposal.id}")
            answer, _ = Answer.objects.get_or_create(vote=vote, proposal=proposal)
            answer.value = val
            answer.save()

        messages.success(request, 'Tu voto ha sido modificado exitosamente.')
        return redirect('poll', slug=poll.slug)

    return render(request, 'polls/edit_vote.html', {
        'poll': poll,
        'vote': vote,
        'proposals': proposals,
        'existing_answers': existing_answers,
    })


def poll_csv_view(request, slug):
    poll = get_object_or_404(Poll, slug=slug)
    proposals = poll.proposals.all().order_by('id')
    votes = poll.votes.all().order_by('created_at').prefetch_related('answers')

    response = HttpResponse(content_type='text/csv; charset=utf-8')
    filename = f"{poll.slug}.csv"
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    response.write('\ufeff')  # UTF-8 BOM for Microsoft Excel compatibility

    writer = csv.writer(response)
    header = ['Participante'] + [p.label for p in proposals]
    writer.writerow(header)

    human_values = {'yes': 'Sí', 'maybe': 'Tal vez', 'no': 'No'}

    for vote in votes:
        answers_map = {a.proposal_id: a.value for a in vote.answers.all()}
        row = [vote.author_name]
        for p in proposals:
            val = answers_map.get(p.id, '')
            row.append(human_values.get(val, ''))
        writer.writerow(row)

    return response


def authenticate_poll_view(request, slug):
    poll = get_object_or_404(Poll, slug=slug)

    if request.method == 'POST':
        password = request.POST.get('password', '')
        if PollSecurity.verify_password(poll, password):
            PollSecurity.authenticate(poll, request.session)
            return redirect('poll', slug=poll.slug)
        else:
            messages.error(request, 'La contraseña ingresada no es correcta.')

    return render(request, 'polls/authenticate.html', {
        'poll': poll,
    })


def poll_admin_view(request, id, token):
    poll = get_object_or_404(Poll, id=id, admin_token=token)

    if request.method == 'POST':
        if 'close' in request.POST:
            poll.closed_at = timezone.now() if not poll.closed_at else None
            poll.save()
            messages.success(request, 'Estado de la encuesta actualizado.')
        elif 'delete' in request.POST:
            poll.delete()
            messages.success(request, 'Encuesta eliminada.')
            return redirect('home')

    return render(request, 'polls/admin.html', {
        'poll': poll,
    })


def delete_vote_view(request, id, token, vote_id):
    poll = get_object_or_404(Poll, id=id, admin_token=token)
    vote = get_object_or_404(Vote, id=vote_id, poll=poll)

    if request.method == 'POST':
        vote.delete()
        messages.success(request, 'El voto ha sido eliminado.')

    return redirect('poll', slug=poll.slug)


# --- Authentication Views ---

def login_view(request):
    if request.user.is_authenticated:
        return redirect('home')

    returnto = request.GET.get('returnto')
    if returnto and returnto.startswith('/') and not returnto.startswith('//'):
        request.session['login_returnto'] = returnto

    oauth = WikimediaOAuth()
    return render(request, 'login/login.html', {
        'wikimedia_oauth_enabled': oauth.is_configured(),
    })


def login_wikimedia_view(request):
    oauth = WikimediaOAuth()
    if not oauth.is_configured():
        messages.error(request, 'Wikimedia OAuth no está configurado.')
        return redirect('login')

    try:
        auth_url = oauth.build_authorization_url(request.session)
        return redirect(auth_url)
    except Exception as e:
        logger.error(f"Error al iniciar Wikimedia OAuth: {e}")
        messages.error(request, 'No se pudo iniciar la conexión con Wikimedia.')
        return redirect('login')


def login_wikimedia_callback_view(request):
    oauth = WikimediaOAuth()
    if not oauth.is_configured():
        messages.error(request, 'Wikimedia OAuth no está configurado.')
        return redirect('login')

    oauth_token = request.GET.get('oauth_token', '')
    oauth_verifier = request.GET.get('oauth_verifier', '')

    if not oauth.has_valid_callback(request.session, oauth_token, oauth_verifier):
        messages.error(request, 'La autenticación con Wikimedia fue cancelada o el token expiró.')
        return redirect('login')

    try:
        profile = oauth.fetch_profile(request.session, oauth_verifier)
        user = oauth.synchronize_wikimedia_user(profile)
        auth_login(request, user)
        messages.success(request, f"¡Bienvenido, {user.display_name}!")
    except Exception as e:
        logger.error(f"Error al completar Wikimedia OAuth: {e}")
        messages.error(request, f"Error al autenticar con Wikimedia: {e}")
        return redirect('login')

    returnto = request.session.pop('login_returnto', None)
    if returnto and returnto.startswith('/') and not returnto.startswith('//'):
        return redirect(returnto)

    return redirect('home')


def logout_view(request):
    auth_logout(request)
    messages.success(request, 'Has cerrado sesión exitosamente.')
    return redirect('home')


# --- Phase 3 Views: Cohosts, My Section, and Preferences ---

def my_view(request):
    if not request.user.is_authenticated:
        return render(request, 'my/index.html')

    owned_polls = Poll.objects.filter(owner=request.user).order_by('-created_at')
    cohosted_polls = Poll.objects.filter(pollcohost__user=request.user).exclude(owner=request.user).order_by('-created_at')
    user_votes = Vote.objects.filter(owner=request.user).select_related('poll').order_by('-created_at')

    return render(request, 'my/index.html', {
        'owned_polls': owned_polls,
        'cohosted_polls': cohosted_polls,
        'user_votes': user_votes,
    })


def user_search_view(request):
    if not request.user.is_authenticated:
        return JsonResponse([], safe=False)

    q = request.GET.get('q', '').strip()
    if not q:
        return JsonResponse([], safe=False)

    users = User.objects.filter(username__icontains=q)[:20]
    results = [
        {'username': u.username, 'displayName': u.display_name}
        for u in users
    ]
    return JsonResponse(results, safe=False)


def poll_cohosts_view(request, id, token):
    poll = get_object_or_404(Poll, id=id, admin_token=token)

    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        right = request.POST.get('right', 'edit')
        if right not in ('edit', 'full'):
            right = 'edit'

        user = User.objects.filter(username=username).first()
        if not user:
            messages.error(request, f"No se encontró ningún usuario con el nombre '{username}'.")
        elif user.id == poll.owner_id:
            messages.error(request, "El creador principal ya es propietario de la encuesta.")
        else:
            cohost, created = PollCohost.objects.get_or_create(poll=poll, user=user)
            cohost.cohost_right = right
            cohost.save()
            messages.success(request, f"Usuario '{user.display_name}' agregado como co-anfitrión.")
            return redirect('poll cohosts', id=poll.id, token=poll.admin_token)

    cohosts = PollCohost.objects.filter(poll=poll).select_related('user')
    return render(request, 'polls/cohosts.html', {
        'poll': poll,
        'cohosts': cohosts,
    })


def poll_remove_cohost_view(request, id, token, user_id):
    poll = get_object_or_404(Poll, id=id, admin_token=token)
    PollCohost.objects.filter(poll=poll, user_id=user_id).delete()
    messages.success(request, 'Co-anfitrión eliminado.')
    return redirect('poll cohosts', id=poll.id, token=poll.admin_token)


def preferences_view(request):
    from polls.services.translator import SUPPORTED_LOCALES, DEFAULT_LOCALE, set_current_locale, trans

    if request.method == 'POST':
        locale = request.POST.get('locale')
        if locale and locale in SUPPORTED_LOCALES:
            request.session['_locale'] = locale
            set_current_locale(locale)
        messages.success(request, 'Preferencias guardadas.')
        referrer = request.META.get('HTTP_REFERER') or '/'
        response = redirect(referrer)
        if locale:
            response.set_cookie('_locale', locale, max_age=365 * 24 * 3600, samesite='Lax')
        return response

    current_locale = getattr(request, 'locale', None) or request.session.get('_locale', DEFAULT_LOCALE)
    return render(request, 'preferences/edit.html', {
        'current_locale': current_locale,
        'supported_locales': SUPPORTED_LOCALES,
    })
