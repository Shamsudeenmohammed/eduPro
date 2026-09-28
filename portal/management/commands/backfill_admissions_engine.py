"""
``manage.py backfill_admissions_engine``

One-off data migration for the Applications & Admissions engine.

The engine was built without shipping migrations, so an existing installation
still holds legacy application rows. This command fills in the new columns from
what the old workflow already implied. It is deliberately a management command
rather than a migration: schema changes and data changes are then reviewed and
scheduled separately, and this can be re-run safely.

What it fixes, all idempotently (rows already correct are skipped):

* ``application_type_ref``  — link the legacy ``application_type`` code to its
  ``ApplicationType`` row.
* ``decision``              — an ``approved``/``accepted`` application really
  carried an *accepted* decision, and a ``rejected`` one a *rejected* decision.
  Backfilled from status so the two can never disagree.
* ``decision_made_by``      — taken from ``approved_by``/``rejected_by`` so the
  outcome stays attributed to whoever made it.
* ``submitted_at``          — legacy rows were submitted on creation.
* ``cycle.academic_session``— matched on ``academic_year`` so a cycle knows
  which existing session it targets.

Nothing is deleted and no status is *invented*: only the columns the legacy
workflow already determined are filled in. Use ``--dry-run`` first.
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from portal.models import (
    AdmissionApplication,
    AdmissionCycle,
    AdmissionDecision,
    AdmissionStatus,
    ApplicationType,
)


#: Legacy status -> the decision it actually represented.
LEGACY_DECISION = {
    AdmissionStatus.APPROVED: AdmissionDecision.ACCEPTED,
    AdmissionStatus.ACCEPTED: AdmissionDecision.ACCEPTED,
    AdmissionStatus.REJECTED: AdmissionDecision.REJECTED,
}

#: Legacy status -> the modern stage it means, where one is unambiguous.
LEGACY_STATUS = {
    AdmissionStatus.PENDING: AdmissionStatus.SUBMITTED,
    AdmissionStatus.REVIEW: AdmissionStatus.UNDER_REVIEW,
    AdmissionStatus.REVIEWING: AdmissionStatus.UNDER_REVIEW,
    AdmissionStatus.APPROVED: AdmissionStatus.DECISION_PENDING,
}


class Command(BaseCommand):
    help = "Backfill Applications & Admissions engine columns from legacy data."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would change without writing anything.",
        )
        parser.add_argument(
            "--normalize-status",
            action="store_true",
            help=(
                "Also rewrite legacy status values to their modern equivalent. "
                "Off by default because it changes the stored status."
            ),
        )

    @transaction.atomic
    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        stats = {
            "type_ref": 0,
            "decision": 0,
            "decision_by": 0,
            "submitted_at": 0,
            "cycles": 0,
            "status": 0,
        }
        details = []

        # ── 1. Application types must exist before anything can link to them ──
        from portal.services import CycleService

        types, _requirements = CycleService.bootstrap()

        # ── 2. Link application_type_ref ─────────────────────────────────────
        for app in AdmissionApplication.objects.filter(application_type_ref__isnull=True):
            atype = ApplicationType.for_code(app.application_type)
            if atype is None:
                details.append(
                    f"{app.reference_number}: unknown type "
                    f"{app.application_type!r}, left unlinked"
                )
                continue
            app.application_type_ref = atype
            if not dry_run:
                app.save(update_fields=["application_type_ref"])
            stats["type_ref"] += 1

        # ── 3. Decision + attribution from the legacy status ─────────────────
        for app in AdmissionApplication.objects.filter(decision=AdmissionDecision.PENDING):
            decision = LEGACY_DECISION.get(app.status)
            if decision is None:
                continue
            actor = app.approved_by or app.rejected_by or app.reviewed_by
            app.decision = decision
            fields = ["decision"]
            if actor is not None:
                app.decision_made_by = actor
                fields.append("decision_made_by")
            if not app.decision_at:
                app.decision_at = app.approved_at or app.rejected_at or app.created_at
                fields.append("decision_at")
            if not dry_run:
                app.save(update_fields=fields)
            stats["decision"] += 1
            if actor is not None:
                stats["decision_by"] += 1
            details.append(
                f"{app.reference_number}: {app.status} -> decision {decision}"
            )

        # ── 4. submitted_at for anything that was never a draft ─────────────
        legacy = AdmissionApplication.objects.filter(
            submitted_at__isnull=True,
        ).exclude(status=AdmissionStatus.DRAFT)
        for app in legacy:
            app.submitted_at = app.created_at
            if not dry_run:
                app.save(update_fields=["submitted_at"])
            stats["submitted_at"] += 1

        # ── 5. Cycles: resolve the academic session from the year ───────────
        from academics.models import AcademicSession

        for cycle in AdmissionCycle.objects.filter(academic_session__isnull=True):
            if not cycle.academic_year:
                continue
            # AcademicSession is identified by ``name`` (e.g. "2025/2026");
            # it has no separate year column, so match on the name directly.
            session = AcademicSession.objects.filter(
                name__iexact=cycle.academic_year
            ).first() or AcademicSession.objects.filter(
                name__contains=cycle.academic_year
            ).first()
            if session is None:
                details.append(
                    f"cycle {cycle.name!r}: no AcademicSession matches "
                    f"{cycle.academic_year!r}, left unresolved"
                )
                continue
            cycle.academic_session = session
            if not dry_run:
                cycle.save(update_fields=["academic_session"])
            stats["cycles"] += 1
            details.append(f"cycle {cycle.name!r} -> session {session.name!r}")

        # ── 6. Optional status normalisation ────────────────────────────────
        if options["normalize_status"]:
            for app in AdmissionApplication.objects.all().iterator():
                old = app.status
                target = LEGACY_STATUS.get(old)
                if target is None:
                    continue
                app.status = target
                if not dry_run:
                    app.save(update_fields=["status"])
                stats["status"] += 1
                details.append(f"{app.reference_number}: status {old} -> {target}")

        if dry_run:
            transaction.set_rollback(True)

        self.stdout.write("")
        self.stdout.write(
            self.style.WARNING("DRY RUN — no changes written.")
            if dry_run
            else self.style.SUCCESS("Backfill complete.")
        )
        self.stdout.write(f"  application types created : {len(types)}")
        for label, key in (
            ("application_type_ref linked", "type_ref"),
            ("decisions backfilled", "decision"),
            ("  of which attributed", "decision_by"),
            ("submitted_at set", "submitted_at"),
            ("cycles linked to session", "cycles"),
            ("statuses normalized", "status"),
        ):
            self.stdout.write(f"  {label:<28}: {stats[key]}")

        if details:
            self.stdout.write("")
            self.stdout.write("Details:")
            for line in details[:200]:
                self.stdout.write(f"  {line}")
            if len(details) > 200:
                self.stdout.write(f"  ... and {len(details) - 200} more")
