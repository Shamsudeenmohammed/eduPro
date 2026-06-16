from django.urls import path
from . import views

app_name = "analytics"

urlpatterns = [
    path("", views.analytics_dashboard, name="dashboard"),
    path("course/<int:offering_pk>/", views.teacher_analytics, name="teacher_course"),
    path("my-insights/", views.student_recommendations, name="student_insights"),
]
