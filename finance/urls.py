from django.urls import path
from . import views

app_name = "finance"

urlpatterns = [
    path("", views.fee_dashboard, name="dashboard"),
    path("structures/", views.fee_structure_list, name="fee_structures"),
    path("students/", views.student_fees, name="student_fees"),
    path("students/<int:fee_pk>/pay/", views.record_payment, name="record_payment"),
    path("my-fees/", views.my_fees, name="my_fees"),
    path("payroll/", views.payroll_list, name="payroll"),
    path("my-payroll/", views.my_payroll, name="my_payroll"),
]
