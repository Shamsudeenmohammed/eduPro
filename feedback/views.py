from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Avg, Count
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from accounts.decorators import admin_required
from .forms import FeedbackForm, FeedbackResponseForm
from .models import Feedback, FeedbackCategory, SentimentLabel
from .services import classify_feedback


@login_required
@require_http_methods(["GET", "POST"])
def submit_feedback(request):
    form = FeedbackForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        fb = form.save(commit=False)
        if not fb.is_anonymous:
            fb.user = request.user
        fb.save()
        classify_feedback(fb)
        messages.success(request, "Thank you for your feedback!")
        return redirect("feedback:submit")
    base = "students/base.html" if getattr(request.user, "is_student", False) else "admin_base.html"
    return render(request, "feedback/submit.html", {"form": form, "page_title": "Submit Feedback", "base_template": base})


@login_required
@admin_required
def feedback_dashboard(request):
    qs = Feedback.objects.order_by("-created_at")
    stats = {
        "total": qs.count(),
        "positive": qs.filter(sentiment=SentimentLabel.POSITIVE).count(),
        "negative": qs.filter(sentiment=SentimentLabel.NEGATIVE).count(),
        "neutral": qs.filter(sentiment=SentimentLabel.NEUTRAL).count(),
        "avg_rating": qs.filter(rating__isnull=False).aggregate(a=Avg("rating"))["a"],
        "by_category": list(
            qs.values("category").annotate(count=Count("id")).order_by("-count")
        ),
    }
    paginator = Paginator(qs, 25)
    return render(request, "feedback/dashboard.html", {
        "stats": stats,
        "page_obj": paginator.get_page(request.GET.get("page")),
        "categories": FeedbackCategory.choices,
        "page_title": "Feedback & Sentiment Analysis",
    })


@login_required
@admin_required
@require_http_methods(["GET", "POST"])
def feedback_respond(request, pk):
    fb = Feedback.objects.get(pk=pk)
    form = FeedbackResponseForm(request.POST or None, instance=fb)
    if request.method == "POST" and form.is_valid():
        obj = form.save(commit=False)
        obj.is_reviewed = True
        obj.save()
        messages.success(request, "Response saved.")
        return redirect("feedback:dashboard")
    return render(request, "feedback/respond.html", {"feedback": fb, "form": form, "page_title": "Respond to Feedback"})
