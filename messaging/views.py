from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from accounts.models import EduProUser
from .forms import ComposeMessageForm
from .models import Conversation, Message


@login_required
def inbox(request):
    received = Message.objects.filter(recipient=request.user).select_related(
        "sender", "conversation"
    ).order_by("-created_at")[:50]
    sent = Message.objects.filter(sender=request.user).select_related(
        "recipient"
    ).order_by("-created_at")[:20]
    return render(request, "messaging/inbox.html", {
        "received": received,
        "sent": sent,
        "page_title": "Messages",
    })


@login_required
@require_http_methods(["GET", "POST"])
def compose(request):
    form = ComposeMessageForm(request.POST or None, user=request.user)
    if request.method == "POST" and form.is_valid():
        recipient = form.cleaned_data["recipient"]
        conv = Conversation.objects.create(subject=form.cleaned_data.get("subject", ""))
        conv.participants.add(request.user, recipient)
        Message.objects.create(
            conversation=conv,
            sender=request.user,
            recipient=recipient,
            body=form.cleaned_data["body"],
        )
        messages.success(request, "Message sent.")
        return redirect("messaging:inbox")
    return render(request, "messaging/compose.html", {"form": form, "page_title": "Compose Message"})


@login_required
def read_message(request, pk):
    msg = get_object_or_404(Message, pk=pk, recipient=request.user)
    if not msg.is_read:
        msg.is_read = True
        msg.read_at = timezone.now()
        msg.save(update_fields=["is_read", "read_at"])
    return render(request, "messaging/read.html", {"message": msg, "page_title": "Message"})
