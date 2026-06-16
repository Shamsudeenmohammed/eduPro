from django.urls import path
from . import views

app_name = "students"

urlpatterns = [

    # =========================================================
    # Dashboard & Profile
    # =========================================================
    path("", views.student_dashboard, name="dashboard"),
    path("profile/", views.student_profile, name="profile"),

    # =========================================================
    # Courses
    # =========================================================
    path("courses/", views.my_courses, name="my_courses"),
    path("courses/<int:offering_pk>/", views.course_home, name="course_home"),

    # =========================================================
    # Course Materials
    # =========================================================
    path("courses/<int:offering_pk>/materials/", views.materials_list, name="materials_list"),
    path("materials/<int:pk>/access/", views.material_access, name="material_access"),

    # =========================================================
    # Assignments
    # =========================================================
    path("courses/<int:offering_pk>/assignments/", views.assignment_list, name="assignment_list"),
    path("assignments/<int:pk>/submit/", views.assignment_submit, name="assignment_submit"),

    # =========================================================
    # Quizzes
    # =========================================================
    path("courses/<int:offering_pk>/quizzes/", views.quiz_list, name="quiz_list"),
    path("quizzes/<int:pk>/start/", views.quiz_start, name="quiz_start"),
    path("quizzes/attempt/<int:attempt_pk>/take/", views.quiz_take, name="quiz_take"),
    path("quizzes/attempt/<int:attempt_pk>/result/", views.quiz_result, name="quiz_result"),

    # =========================================================
    # Attendance
    # =========================================================
    path("courses/<int:offering_pk>/attendance/", views.attendance_summary, name="attendance_summary"),

    # =========================================================
    # Results & Academic Progress
    # =========================================================
    path("results/", views.results_list, name="results_list"),
    path("results/<int:offering_pk>/", views.result_detail, name="result_detail"),
    path("progress/", views.academic_progress, name="academic_progress"),
    path("transcript/pdf/", views.transcript_redirect, name="transcript_pdf"),

    # =========================================================
    # Course Registration
    # =========================================================
    path("register/", views.course_registration, name="course_registration"),
    path("register/list/", views.registration_list, name="registration_list"),

    # =========================================================
    # Notifications
    # =========================================================
    path("notifications/", views.notifications, name="notifications"),
    path("notifications/mark-read/", views.mark_all_read, name="mark_all_read"),

    # =========================================================
    # Finance & Insights
    # =========================================================
    path("fees/", views.my_fees_redirect, name="my_fees"),
    path("insights/", views.insights_redirect, name="insights"),

]