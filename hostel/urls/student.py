"""Student endpoints — under the ``hostel`` namespace."""

from django.urls import path

from hostel.views import (
    hostel_apply,
    hostel_confirm_payment,
    hostel_list,
    hostel_pay_callback,
    hostel_pay_initiate,
    hostel_pay_webhook,
    hostel_renew_allocation,
    hostel_rooms_api,
    hostel_transfer_cancel,
    hostel_transfer_request,
    hostel_vacate,
)

urlpatterns = [
    path("", hostel_list, name="hostel"),
    path("apply/", hostel_apply, name="hostel_apply"),
    path("vacate/", hostel_vacate, name="hostel_vacate"),
    path("confirm-payment/<int:pk>/", hostel_confirm_payment, name="hostel_confirm_payment"),
    path("pay/<int:pk>/initiate/", hostel_pay_initiate, name="hostel_pay_initiate"),
    path("pay/callback/", hostel_pay_callback, name="hostel_pay_callback"),
    path("pay/webhook/", hostel_pay_webhook, name="hostel_pay_webhook"),
    path("renew/", hostel_renew_allocation, name="hostel_renew_allocation"),
    path("transfer/request/", hostel_transfer_request, name="hostel_transfer_request"),
    path("transfer/cancel/<int:pk>/", hostel_transfer_cancel, name="hostel_transfer_cancel"),
    path("api/rooms/", hostel_rooms_api, name="hostel_rooms_api"),
]