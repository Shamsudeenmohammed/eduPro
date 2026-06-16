from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from accounts.decorators import teacher_required
from academics.models import CourseOffering, Enrolment
from .forms import ForumPostForm, LMSModuleForm, LiveClassForm
from .models import Forum, ForumPost, LMSModule, LearningResource, LiveClassSession


def _can_access_offering(user, offering):
    if getattr(user, "is_admin", False):
        return True
    if getattr(user, "is_teacher", False):
        from academics.models import CourseAllocation
        return CourseAllocation.objects.filter(teacher=user, offering=offering, is_active=True).exists()
    if getattr(user, "is_student", False):
        return Enrolment.objects.filter(student=user, offering=offering, is_active=True).exists()
    return False


@login_required
def lms_course(request, offering_pk):
    offering = get_object_or_404(CourseOffering, pk=offering_pk)
    if not _can_access_offering(request.user, offering):
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied
    modules = LMSModule.objects.filter(offering=offering, is_published=True).prefetch_related("resources")
    live_sessions = LiveClassSession.objects.filter(offering=offering, is_active=True)[:5]
    forum, _ = Forum.objects.get_or_create(offering=offering)
    return render(request, "elearning/course_lms.html", {
        "offering": offering,
        "modules": modules,
        "live_sessions": live_sessions,
        "forum": forum,
        "page_title": f"LMS — {offering.course.code}",
    })


@login_required
def forum_view(request, offering_pk):
    offering = get_object_or_404(CourseOffering, pk=offering_pk)
    if not _can_access_offering(request.user, offering):
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied
    forum, _ = Forum.objects.get_or_create(offering=offering)
    posts = forum.posts.filter(parent__isnull=True).select_related("author")[:30]
    form = ForumPostForm()
    if request.method == "POST":
        form = ForumPostForm(request.POST)
        if form.is_valid():
            post = form.save(commit=False)
            post.forum = forum
            post.author = request.user
            post.save()
            messages.success(request, "Post published.")
            return redirect("elearning:forum", offering_pk=offering_pk)
    return render(request, "elearning/forum.html", {
        "offering": offering, "forum": forum, "posts": posts, "form": form,
        "page_title": f"Forum — {offering.course.code}",
    })


@login_required
@teacher_required
def lms_manage(request, offering_pk):
    offering = get_object_or_404(CourseOffering, pk=offering_pk)
    form = LMSModuleForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        mod = form.save(commit=False)
        mod.offering = offering
        mod.save()
        messages.success(request, "Module added.")
        return redirect("elearning:lms_manage", offering_pk=offering_pk)
    modules = LMSModule.objects.filter(offering=offering)
    live_form = LiveClassForm(request.POST or None, prefix="live")
    if request.method == "POST" and request.POST.get("live-submit"):
        live_form = LiveClassForm(request.POST, prefix="live")
        if live_form.is_valid():
            session = live_form.save(commit=False)
            session.offering = offering
            session.host = request.user
            session.save()
            messages.success(request, "Live session scheduled.")
            return redirect("elearning:lms_manage", offering_pk=offering_pk)
    return render(request, "elearning/lms_manage.html", {
        "offering": offering, "modules": modules, "form": form, "live_form": live_form,
        "page_title": f"Manage LMS — {offering.course.code}",
    })
