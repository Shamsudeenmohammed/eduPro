"""
academics/urls.py

All URL patterns for the academics app.
Include in main urls.py as:
    path("academics/", include("academics.urls", namespace="academics")),
"""

from django.urls import path

from . import views

app_name = "academics"

urlpatterns = [
    # ── Dashboard ─────────────────────────────────────────────────────────────
    path("", views.academics_dashboard, name="dashboard"),

    # ── Institution ───────────────────────────────────────────────────────────
    path("institutions/",             views.institution_list,   name="institution_list"),
    path("institutions/add/",         views.institution_create, name="institution_create"),
    path("institutions/<int:pk>/edit/", views.institution_edit, name="institution_edit"),

    # ── Faculty ───────────────────────────────────────────────────────────────
    path("faculties/",               views.faculty_list,   name="faculty_list"),
    path("faculties/add/",           views.faculty_create, name="faculty_create"),
    path("faculties/<int:pk>/edit/", views.faculty_edit,   name="faculty_edit"),
    path("faculties/<int:pk>/",          views.faculty_detail, name="faculty_detail"),

    # ── Department ────────────────────────────────────────────────────────────
    path("departments/",               views.department_list,   name="department_list"),
    path("departments/add/",           views.department_create, name="department_create"),
    path("departments/<int:pk>/edit/", views.department_edit,   name="department_edit"),
    path("departments/<int:pk>/",         views.department_detail,  name="department_detail"),

    # ── Program ───────────────────────────────────────────────────────────────
    path("programs/",               views.program_list,   name="program_list"),
    path("programs/add/",           views.program_create, name="program_create"),
    path("programs/<int:pk>/edit/", views.program_edit,   name="program_edit"),
    path("programs/<int:pk>/",         views.program_detail,  name="program_detail"),

    # ── Academic Session ──────────────────────────────────────────────────────
    path("sessions/",      views.session_list,   name="session_list"),
    path("sessions/add/",  views.session_create, name="session_create"),
    path("sessions/<int:pk>/edit/",  views.session_edit,    name="session_edit"),

    # ── Semester ──────────────────────────────────────────────────────────────
    path("semesters/",     views.semester_list,   name="semester_list"),
    path("semesters/add/", views.semester_create, name="semester_create"),
    path("semesters/<int:pk>/edit/",  views.semester_edit,    name="semester_edit"),

    # ── Course ────────────────────────────────────────────────────────────────
    path("courses/",               views.course_list,   name="course_list"),
    path("courses/<int:pk>/",         views.course_detail,  name="course_detail"),
    path("courses/add/",           views.course_create, name="course_create"),
    path("courses/<int:pk>/edit/", views.course_edit,   name="course_edit"),

    # ── Course Offering ───────────────────────────────────────────────────────
    path("offerings/",      views.offering_list,   name="offering_list"),
    path("offerings/add/",  views.offering_create, name="offering_create"),
    path("offerings/<int:pk>/",         views.offering_detail,  name="offering_detail"),
    path("offerings/<int:pk>/edit/",    views.offering_edit,    name="offering_edit"),

    # ── Course Allocation ─────────────────────────────────────────────────────
    path("allocations/",                         views.allocation_list,       name="allocation_list"),
    path("allocations/add/",                     views.allocation_create,     name="allocation_create"),
    path("allocations/bulk/",                       views.allocation_bulk,       name="allocation_bulk"),
    path("allocations/<int:pk>/deactivate/",     views.allocation_deactivate, name="allocation_deactivate"),
    path("allocations/<int:pk>/activate/",       views.allocation_activate,   name="allocation_activate"),

    # ── Enrolment ─────────────────────────────────────────────────────────────
    path("enrolments/",                      views.enrolment_list,       name="enrolment_list"),
    path("enrolments/add/",                  views.enrolment_create,     name="enrolment_create"),
    path("enrolments/bulk/",                 views.enrolment_bulk,       name="enrolment_bulk"),
    path("enrolments/<int:pk>/activate/",    views.enrolment_activate,   name="enrolment_activate"),
    path("enrolments/<int:pk>/deactivate/",  views.enrolment_deactivate, name="enrolment_deactivate"),

    # ── Teacher views ─────────────────────────────────────────────────────────
    path("my-courses/",    views.my_courses,    name="my_courses"),
    path("my-enrolments/", views.my_enrolments, name="my_enrolments"),

    # ── Student Profile ───────────────────────────────────────────────────────
    path("students/<int:user_pk>/profile/", views.student_profile_edit, name="student_profile_edit"),

    # ── Teacher–Department ────────────────────────────────────────────────────
    path("teacher-departments/",      views.teacher_dept_list,   name="teacher_dept_list"),
    path("teacher-departments/add/",  views.teacher_dept_create, name="teacher_dept_create"),

    # ── Result Sheets ─────────────────────────────────────────────────────────
    path("result-sheets/",                            views.result_sheet_list,       name="result_sheet_list"),
    path("result-sheets/admin/",                      views.result_sheet_admin_list, name="result_sheet_admin_list"),
    path("result-sheets/add/",                        views.result_sheet_create,     name="result_sheet_create"),
    path("result-sheets/<int:pk>/",                   views.result_sheet_detail,     name="result_sheet_detail"),
    path("result-sheets/<int:pk>/submit/",            views.result_sheet_submit,     name="result_sheet_submit"),
    path("result-sheets/<int:pk>/hod-approve/",       views.result_sheet_hod_approve, name="result_sheet_hod_approve"),
    path("result-sheets/<int:pk>/finalize/",          views.result_sheet_finalize,   name="result_sheet_finalize"),
    path("result-sheets/<int:pk>/revert/",            views.result_sheet_revert,     name="result_sheet_revert"),
]
