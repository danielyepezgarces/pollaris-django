import json
import logging
from django.shortcuts import render, redirect, get_object_or_404
from django.http import HttpResponse, Http404
from django.core.paginator import Paginator
from django.db.models import Count
from django.contrib import messages
from django.contrib.auth import login as auth_login, logout as auth_logout
from django.utils import timezone

from .models import Poll, Proposal, Date, Vote, Answer, Comment, User, PollCohost, generate_hex_id
from .services.wikimedia_oauth import WikimediaOAuth
from .services.poll_security import PollSecurity

logger = logging.getLogger(__name__)


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


def poll_view(request, slug):
    poll = get_object_or_404(Poll, slug=slug)

    # 1. Check full password protection
    if not PollSecurity.is_authenticated(poll, request.session):
        return redirect('authenticate poll', slug=poll.slug)

    proposals = poll.proposals.all().order_by('id')
    votes = poll.votes.all().order_by('created_at').prefetch_related('answers')
    comments = poll.comments.all().order_by('created_at')

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
        'votes': votes,
        'comments': comments,
        'my_vote': my_vote,
        'results': results,
        'can_vote': can_vote,
        'eligibility_errors': eligibility_errors,
    })


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
