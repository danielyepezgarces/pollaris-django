from django.urls import path
from . import views

urlpatterns = [
    path('', views.home_view, name='home'),
    path('polls/choose', views.choose_poll_type_view, name='choose poll type'),
    path('polls/new', views.new_poll_view, name='new poll'),
    path('polls/public', views.public_polls_view, name='public polls'),
    path('polls/<str:slug>', views.poll_view, name='poll'),
    path('polls/<str:id>/<str:token>/admin', views.poll_admin_view, name='poll admin'),
]
