"""
teachers/urls.py
Include in edupro/urls.py:
    path("teachers/", include("teachers.urls", namespace="teachers")),
"""
from django.urls import path
from . import views

app_name = "teachers"

urlpatterns = [

    # Dashboard & profile
    path("",                            views.teacher_dashboard,      name="dashboard"),
    path("profile/edit/",              views.teacher_profile_edit,   name="profile_edit"),

    # My courses
    path("courses/",                   views.my_courses,             name="my_courses"),
    path("courses/<int:offering_pk>/", views.course_detail,          name="course_detail"),

    # Lecture materials
    path("courses/<int:offering_pk>/materials/",        views.material_list,   name="material_list"),
    path("courses/<int:offering_pk>/materials/add/",    views.material_create, name="material_create"),
    path("materials/<int:pk>/edit/",                    views.material_edit,   name="material_edit"),
    path("materials/<int:pk>/delete/",                  views.material_delete, name="material_delete"),

    # Assignments
    path("courses/<int:offering_pk>/assignments/",       views.assignment_list,           name="assignment_list"),
    path("courses/<int:offering_pk>/assignments/add/",   views.assignment_create,         name="assignment_create"),
    path("assignments/<int:pk>/submissions/",            views.assignment_submissions,    name="assignment_submissions"),
    path("assignments/<int:pk>/publish/",                views.assignment_publish_toggle, name="assignment_publish_toggle"),
    path("submissions/<int:pk>/grade/",                  views.grade_submission,          name="grade_submission"),

    # Quizzes
    path("courses/<int:offering_pk>/quizzes/",       views.quiz_list,            name="quiz_list"),
    path("courses/<int:offering_pk>/quizzes/add/",   views.quiz_create,          name="quiz_create"),
    path("quizzes/<int:pk>/questions/",              views.quiz_questions,       name="quiz_questions"),
    path("quizzes/<int:quiz_pk>/questions/add/",     views.quiz_question_add,    name="quiz_question_add"),
    path("quizzes/<int:pk>/results/",                views.quiz_results,         name="quiz_results"),
    path("quizzes/<int:pk>/publish/",                views.quiz_publish_toggle,  name="quiz_publish_toggle"),
    path("quizzes/attempt/<int:attempt_pk>/grade/",  views.quiz_attempt_detail,  name="quiz_attempt_grade"),

    # Attendance
    path("courses/<int:offering_pk>/attendance/",         views.attendance_list,         name="attendance_list"),
    path("courses/<int:offering_pk>/attendance/take/",    views.attendance_take,         name="attendance_take"),
    path("attendance/<int:pk>/",                          views.attendance_sheet_detail, name="attendance_sheet_detail"),

    # Results
    path("results/",                                   views.result_sheet_list,  name="result_sheet_list"),
    path("courses/<int:offering_pk>/results/setup/",   views.result_sheet_setup, name="result_sheet_setup"),
    path("results/<int:sheet_pk>/entry/",              views.result_entry,       name="result_entry"),
    path("results/<int:sheet_pk>/submit/",             views.result_submit,      name="result_submit"),
    path("results/<int:sheet_pk>/approve/",            views.result_approve,     name="result_approve"),
    path("results/<int:sheet_pk>/reject/",             views.result_reject,      name="result_reject"),
    path("results/<int:sheet_pk>/view/",               views.result_sheet_view,  name="result_sheet_view"),

    # HOD department results
    path("hod/results/",                               views.hod_result_sheets,  name="hod_result_sheets"),

    # Student performance
    path("courses/<int:offering_pk>/students/<int:student_pk>/performance/",
         views.student_performance, name="student_performance"),
    
    path("quizzes/<int:pk>/publish/",views.quiz_publish,name="quiz_publish",),
    path("quizzes/<int:pk>/unpublish/",views.quiz_unpublish,name="quiz_unpublish",),

]
