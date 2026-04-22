"""Root URL configuration."""
from django.urls import path, include

urlpatterns = [
    path('', include('expenses.urls')),
]
