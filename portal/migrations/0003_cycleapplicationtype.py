"""
Adds the cycle -> application type join table.

A cycle could only ever name one application type, so an intake that recruits
undergraduates and mature applicants together had no way to say so.

The table is created by `manage.py ensure_portal_schema` (and automatically by
the `post_migrate` hook in `portal.apps`) from the models, which is how the
rest of this engine's schema is built. That hook is idempotent, so having the
`CreateModel` here as well is safe on a database where the table already
exists from the model-built path.
"""

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("portal", "0002_applicationtype_admissionapplication_converted_at_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="CycleApplicationType",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "application_type",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="cycle_links",
                        to="portal.applicationtype",
                        verbose_name="application type",
                    ),
                ),
                (
                    "cycle",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="application_type_links",
                        to="portal.admissioncycle",
                        verbose_name="admission cycle",
                    ),
                ),
            ],
            options={
                "verbose_name": "cycle application type",
                "verbose_name_plural": "cycle application types",
                "ordering": ["application_type__order", "application_type__code"],
            },
        ),
        migrations.AddConstraint(
            model_name="cycleapplicationtype",
            constraint=models.UniqueConstraint(
                fields=("cycle", "application_type"),
                name="uniq_cycle_application_type",
            ),
        ),
    ]
