from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver
from django.contrib.auth import get_user_model
from .models import Role, StaffResponsibility, UserProfile, UserStaffRole

User = get_user_model()


@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    if created:
        UserProfile.objects.get_or_create(user=instance)


@receiver(post_save, sender=UserStaffRole)
@receiver(post_delete, sender=UserStaffRole)
def sync_hod_staff_status(sender, instance, **kwargs):
    """Ensure HODs have is_staff=True and non-HOD teachers don't."""
    user = instance.user
    if user.role != Role.TEACHER:
        return
    has_hod = user.staff_roles.filter(
        responsibility=StaffResponsibility.HOD, is_active=True
    ).exists()
    if has_hod != user.is_staff:
        user.is_staff = has_hod
        user.save(update_fields=["is_staff"])