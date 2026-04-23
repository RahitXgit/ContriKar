from django.urls import path
from . import views

urlpatterns = [
    path('', views.login_view, name='login'),
    path('register/', views.register_view, name='register'),
    path('dashboard/', views.dashboard_view, name='dashboard'),
    path('add/', views.add_expense_view, name='add_expense'),
    path('expenses/', views.all_expenses_view, name='all_expenses'),
    path('settle/', views.settle_view, name='settle'),
    path('logout/', views.logout_view, name='logout'),
    path('expense/<uuid:expense_id>/edit/', views.edit_expense_view, name='edit_expense'),
    path('expense/<uuid:expense_id>/delete/', views.delete_expense_view, name='delete_expense'),
]
