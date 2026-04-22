from django.urls import path
from . import views

urlpatterns = [
    path('', views.login_view, name='login'),
    path('register/', views.register_view, name='register'),
    path('dashboard/', views.dashboard_view, name='dashboard'),
    path('add/', views.add_expense_view, name='add_expense'),
    path('settle/', views.settle_view, name='settle'),
    path('logout/', views.logout_view, name='logout'),
]
