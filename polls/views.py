import json
from django.shortcuts import render, redirect, get_object_or_404
from django.http import HttpResponse, Http404
from django.core.paginator import Paginator
from django.db.models import Count
from django.contrib import messages
from django.utils import timezone
from .models import Poll, Proposal, Date, Vote, Answer, Comment, User, PollCohost, generate_hex_id


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
            author_name=author_name or (request.user.username if request.user.is_authenticated else 'Anónimo'),
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
            # Fallback to lines in a textarea
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
    proposals = poll.proposals.all().order_by('id')
    votes = poll.votes.all().order_by('created_at').prefetch_related('answers')
    comments = poll.comments.all().order_by('created_at')

    # Current user's vote
    my_vote = None
    if request.user.is_authenticated:
        my_vote = Vote.objects.filter(poll=poll, owner=request.user).first()
    else:
        vote_id = request.session.get(f"vote-{poll.id}")
        if vote_id:
            my_vote = Vote.objects.filter(id=vote_id, poll=poll).first()

    if request.method == 'POST':
        action = request.POST.get('action', 'vote')

        if action == 'vote' and not poll.is_closed:
            author_name = request.POST.get('author_name', '').strip()
            if not author_name and request.user.is_authenticated:
                author_name = request.user.display_name

            if not author_name:
                author_name = 'Participante'

            # Create or update vote
            vote = my_vote or Vote(poll=poll)
            vote.author_name = author_name
            if request.user.is_authenticated:
                vote.owner = request.user
            vote.save()

            if not request.user.is_authenticated:
                request.session[f"vote-{poll.id}"] = vote.id

            # Save answers
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
