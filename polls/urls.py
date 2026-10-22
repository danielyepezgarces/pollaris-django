from django.urls import path
from . import views

urlpatterns = [
    path('', views.home_view, name='home'),
    path('polls/choose', views.choose_poll_type_view, name='choose poll type'),
    path('polls/new', views.new_poll_view, name='new poll'),
    path('polls/public', views.public_polls_view, name='public polls'),

    # Poll view and CSV export
    path('polls/<str:slug>.csv', views.poll_csv_view, name='poll csv'),
    path('polls/<str:slug>/authenticate', views.authenticate_poll_view, name='authenticate poll'),
    path('polls/<str:slug>', views.poll_view, name='poll'),

    # Votes modification
    path('polls/<str:slug>/votes/<str:vote_id>/edit', views.edit_vote_view, name='edit vote'),
    path('polls/<str:id>/<str:token>/votes/<str:vote_id>/deletion', views.delete_vote_view, name='delete vote'),

    # Wizard & Administration routes
    path('polls/<str:id>/<str:token>/proposals', views.edit_poll_proposals_view, name='edit poll proposals'),
    path('polls/<str:id>/<str:token>/dates', views.edit_poll_dates_view, name='edit poll dates'),
    path('polls/<str:id>/<str:token>/settings', views.edit_poll_settings_view, name='edit poll settings'),
    path('polls/<str:id>/<str:token>/summary', views.poll_summary_view, name='poll summary'),
    path('polls/<str:id>/<str:token>/complete', views.complete_poll_view, name='complete poll'),
    path('polls/<str:id>/<str:token>/edits/titles', views.edit_poll_titles_view, name='edit poll titles'),
    path('polls/<str:id>/<str:token>/edits/descriptions', views.edit_poll_descriptions_view, name='edit poll descriptions'),
    path('polls/<str:id>/<str:token>/cohosts', views.poll_cohosts_view, name='poll cohosts'),
    path('polls/<str:id>/<str:token>/cohosts/<int:user_id>/remove', views.poll_remove_cohost_view, name='poll remove cohost'),
    path('polls/<str:id>/<str:token>/admin', views.poll_admin_view, name='poll admin'),

    # Personal area, User search, Preferences
    path('my', views.my_view, name='my'),
    path('users/search', views.user_search_view, name='users search'),
    path('preferences', views.preferences_view, name='edit preferences'),

    # Authentication routes
    path('login', views.login_view, name='login'),
    path('login/wikimedia', views.login_wikimedia_view, name='login wikimedia'),
    path('login/wikimedia/callback', views.login_wikimedia_callback_view, name='login wikimedia callback'),
    path('logout', views.logout_view, name='logout'),
]
