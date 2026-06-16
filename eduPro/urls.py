"""
eduPro root URL configuration.
"""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.shortcuts import redirect
from django.urls import include, path


def root_redirect(request):
    if request.user.is_authenticated:
        return redirect(request.user.get_dashboard_url())
    return redirect("portal:home")


urlpatterns = [
    path("", root_redirect, name="root"),
    path("admin/", admin.site.urls),

    # Public portal
    path("portal/", include("portal.urls", namespace="portal")),

    # Authentication & accounts
    path("accounts/", include("accounts.urls", namespace="accounts")),

    # Academic modules
    path("academics/", include("academics.urls", namespace="academics")),
    path("teachers/", include("teachers.urls", namespace="teachers")),
    path("students/", include("students.urls", namespace="students")),

    # Enterprise modules
    path("core/", include("core.urls", namespace="core")),
    path("operations/", include("operations.urls", namespace="operations")),
    path("finance/", include("finance.urls", namespace="finance")),
    path("feedback/", include("feedback.urls", namespace="feedback")),
    path("analytics/", include("analytics.urls", namespace="analytics")),
    path("elearning/", include("elearning.urls", namespace="elearning")),
    path("messages/", include("messaging.urls", namespace="messaging")),
    path("api/", include("api.urls", namespace="api")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
