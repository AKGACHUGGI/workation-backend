from django.urls import path

from search import views

urlpatterns = [
    path("search/nl", views.search_nl, name="search-nl"),
]
