from django.urls import path
from . import views

urlpatterns = [
    path("plan/", views.plan_trip, name="plan-trip"),
    path("autocomplete/", views.autocomplete, name="autocomplete"),
]
