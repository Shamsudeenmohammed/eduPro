from django.urls import path
from . import views

app_name = "feedback"

urlpatterns = [
    path("submit/", views.submit_feedback, name="submit"),
    path("dashboard/", views.feedback_dashboard, name="dashboard"),
    path("<int:pk>/respond/", views.feedback_respond, name="respond"),
]
