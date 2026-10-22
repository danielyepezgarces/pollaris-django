from django.contrib import admin
from .models import User, Poll, Proposal, Date, Vote, Answer, Comment, PollCohost


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = ('username', 'real_name', 'email', 'wikimedia_id', 'created_at')
    search_fields = ('username', 'real_name', 'email', 'wikimedia_id')
    list_filter = ('created_at',)


class ProposalInline(admin.TabularInline):
    model = Proposal
    extra = 1


class DateInline(admin.TabularInline):
    model = Date
    extra = 1


@admin.register(Poll)
class PollAdmin(admin.ModelAdmin):
    list_display = ('id', 'title', 'type', 'author_name', 'created_at', 'closed_at')
    search_fields = ('id', 'title', 'author_name', 'author_email', 'slug')
    list_filter = ('type', 'created_at', 'closed_at', 'locale')
    inlines = [DateInline, ProposalInline]


@admin.register(Vote)
class VoteAdmin(admin.ModelAdmin):
    list_display = ('id', 'poll', 'author_name', 'owner', 'created_at')
    search_fields = ('id', 'author_name', 'poll__title')
    list_filter = ('created_at',)


@admin.register(Answer)
class AnswerAdmin(admin.ModelAdmin):
    list_display = ('id', 'vote', 'proposal', 'value', 'created_at')
    list_filter = ('value', 'created_at')


@admin.register(Comment)
class CommentAdmin(admin.ModelAdmin):
    list_display = ('id', 'poll', 'author_name', 'created_at')
    search_fields = ('author_name', 'content')
    list_filter = ('created_at',)


class PollCohostInline(admin.TabularInline):
    model = PollCohost
    extra = 1
