"""
Tests for the multi-application-type selector on the admission cycle form.

The cycle edit screen used to offer a single <select> over
``AdmissionCycle.application_type``, which could both render empty (no
``ApplicationType`` rows seeded) and only ever hold one value. These cover the
replacement: a searchable, multi-select backed by a join table, with the
single-value FK kept in step so existing admin filters still work.
"""

from django.test import TestCase

from portal.forms import AdmissionCycleForm
from portal.models import AdmissionCycle, ApplicationType, CycleApplicationType

from .factories import AdmissionsSchemaTestCase, make_cycle, make_session, seed_application_types


class ApplicationTypeSelectorTests(AdmissionsSchemaTestCase):
    def setUp(self):
        self.types = {t.code: t for t in seed_application_types()}

    def _post(self, codes, **overrides):
        cycle = overrides.pop("cycle", None) or make_cycle(name="selector target")
        data = {
            "name": cycle.name,
            "academic_year": cycle.academic_year,
            # make_cycle builds its dates from the session, so they may still be
            # the raw "YYYY-MM-DD" strings; the form wants that format anyway.
            "start_date": str(cycle.start_date)[:10],
            "end_date": str(cycle.end_date)[:10],
            "academic_session": str(cycle.academic_session_id or ""),
            "status": cycle.status,
            "max_applications": "0",
            "application_types": [str(self.types[c].pk) for c in codes],
        }
        data.update(overrides)
        return AdmissionCycleForm(data, instance=cycle)

    # ── the option list ─────────────────────────────────────────────────────

    def test_every_application_type_is_offered(self):
        form = AdmissionCycleForm()
        offered = {t.pk for t in form.fields["application_types"].queryset}
        self.assertEqual(offered, set(ApplicationType.objects.values_list("pk", flat=True)))
        self.assertGreaterEqual(len(offered), 9)
        for code in ("undergraduate", "postgraduate", "diploma", "mature"):
            self.assertIn(code, {t.code for t in ApplicationType.objects.all()})

    def test_inactive_types_are_not_offered(self):
        self.types["exchange"].is_active = False
        self.types["exchange"].save()
        form = AdmissionCycleForm()
        codes = {t.code for t in form.fields["application_types"].queryset}
        self.assertNotIn("exchange", codes)
        self.assertIn("undergraduate", codes)

    def test_widget_opts_into_selector2_and_renders_code_per_option(self):
        html = str(AdmissionCycleForm()["application_types"])
        self.assertIn('data-selector2="1"', html)
        self.assertIn("multiple", html)
        self.assertEqual(html.count("<option"), ApplicationType.objects.count())
        for atype in ApplicationType.objects.all():
            # selector2 searches and labels on these, so both must be present.
            self.assertIn(f'data-note="{atype.code}"', html)
            self.assertIn(f'data-search="{atype.code}', html)

    # ── saving more than one ────────────────────────────────────────────────

    def test_several_types_are_stored_as_separate_rows(self):
        form = self._post(["undergraduate", "postgraduate", "mature"])
        self.assertTrue(form.is_valid(), form.errors)
        cycle = form.save()

        self.assertEqual(
            sorted(t.code for t in cycle.accepted_types()),
            ["mature", "postgraduate", "undergraduate"],
        )
        self.assertEqual(
            CycleApplicationType.objects.filter(cycle=cycle).count(), 3
        )
        self.assertTrue(cycle.accepts_type(self.types["postgraduate"]))
        self.assertFalse(cycle.accepts_type(self.types["diploma"]))

    def test_primary_type_is_derived_from_the_chosen_set(self):
        form = self._post(["mature", "undergraduate"])
        self.assertTrue(form.is_valid(), form.errors)
        cycle = form.save()

        # Deterministic: always the earliest in ApplicationType.order, never
        # the order the boxes happened to be ticked in.
        self.assertEqual(cycle.application_type, self.types["undergraduate"])
        self.assertEqual(cycle.primary_application_type, self.types["undergraduate"])

    def test_reopening_the_form_returns_every_saved_type(self):
        form = self._post(["undergraduate", "international"])
        self.assertTrue(form.is_valid(), form.errors)
        cycle = form.save()

        reopened = AdmissionCycleForm(instance=AdmissionCycle.objects.get(pk=cycle.pk))
        self.assertEqual(
            sorted(int(pk) for pk in reopened.fields["application_types"].initial),
            sorted([self.types["undergraduate"].pk, self.types["international"].pk]),
        )
        self.assertEqual(
            str(reopened["application_types"]).count("selected"), 2
        )

    def test_saving_again_replaces_rather_than_appends(self):
        first = self._post(["undergraduate", "postgraduate"])
        self.assertTrue(first.is_valid(), first.errors)
        cycle = first.save()

        second = self._post(["mature"], cycle=cycle)
        self.assertTrue(second.is_valid(), second.errors)
        cycle = second.save()

        self.assertEqual([t.code for t in cycle.accepted_types()], ["mature"])
        self.assertEqual(CycleApplicationType.objects.filter(cycle=cycle).count(), 1)
        self.assertEqual(cycle.application_type, self.types["mature"])

    def test_duplicate_codes_in_the_post_do_not_create_duplicate_rows(self):
        cycle = make_cycle(name="dupe target")
        form = AdmissionCycleForm({
            "name": cycle.name,
            "academic_year": cycle.academic_year,
            "start_date": str(cycle.start_date)[:10],
            "end_date": str(cycle.end_date)[:10],
            "status": cycle.status,
            "max_applications": "0",
            "application_types": [str(self.types["undergraduate"].pk)] * 3,
        }, instance=cycle)
        self.assertTrue(form.is_valid(), form.errors)
        cycle = form.save()
        self.assertEqual(CycleApplicationType.objects.filter(cycle=cycle).count(), 1)

    def test_no_selection_means_any_type(self):
        form = self._post([])
        self.assertTrue(form.is_valid(), form.errors)
        cycle = form.save()

        self.assertEqual(cycle.accepted_types(), [])
        self.assertIsNone(cycle.application_type_id)
        self.assertEqual(CycleApplicationType.objects.filter(cycle=cycle).count(), 0)
        # Nothing configured -> every type is accepted.
        self.assertTrue(cycle.accepts_type(self.types["diploma"]))

    def test_inactive_type_cannot_be_smuggled_in(self):
        self.types["exchange"].is_active = False
        self.types["exchange"].save()
        form = self._post(["undergraduate", "exchange"])
        self.assertFalse(form.is_valid())
        self.assertIn("application_types", form.errors)

    # ── reading back what older rows mean ───────────────────────────────────

    def test_legacy_single_type_row_still_reads_as_itself(self):
        """A cycle saved before the join table existed keeps its meaning."""
        cycle = make_cycle(name="legacy typed cycle",
                           application_type=self.types["postgraduate"])
        self.assertEqual([t.code for t in cycle.accepted_types()], ["postgraduate"])
        self.assertTrue(cycle.accepts_type(self.types["postgraduate"]))
        self.assertFalse(cycle.accepts_type(self.types["diploma"]))

    def test_unrestricted_cycle_accepts_everything(self):
        cycle = make_cycle(name="unrestricted cycle")
        self.assertEqual(cycle.accepted_types(), [])
        for atype in ApplicationType.objects.all():
            self.assertTrue(cycle.accepts_type(atype))

    def test_set_of_types_restricts_accepts_type(self):
        cycle = make_cycle(name="restricted cycle")
        cycle.set_accepted_types([self.types["undergraduate"], self.types["mature"]])
        self.assertTrue(cycle.accepts_type(self.types["mature"]))
        self.assertFalse(cycle.accepts_type(self.types["exchange"]))
