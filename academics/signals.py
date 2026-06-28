from django.db.models.signals import post_save
from django.dispatch import receiver


@receiver(post_save, sender="academics.Program")
def auto_create_program_levels(sender, instance, created, **kwargs):
    """Auto-create levels 100, 200, 300, 400 when a new Program is created."""
    if not created:
        return
    from .models import Level

    defaults = {"is_active": True}
    levels = [
        {"name": "100", "order": 1},
        {"name": "200", "order": 2},
        {"name": "300", "order": 3},
        {"name": "400", "order": 4},
    ]
    for lv in levels:
        Level.objects.get_or_create(
            program=instance,
            order=lv["order"],
            defaults={"name": lv["name"], **defaults},
        )
