"""
Tests for which application types an applicant may actually choose.

Two independent limits apply, and both are enforced:

* the **cycle** — if it names the types it accepts, only those are offered, so
  an applicant cannot post a type the intake is not running and be believed;
* the **programme** — a programme only offers the types it actually runs, so a
  diploma programme is not offered postgraduate and the reverse.

The dropdown is narrowed for the applicant's benefit; `clean()` re-checks the
pair server-side so a crafted POST cannot get past it.
"""

from django.test import TestCase

from portal.forms import AdmissionApplicationForm
from portal.models import (
    AdmissionCycle,
    ApplicationType,
    ProgramApplicationType,
    program_available_types,
)
from portal.services import ApplicationTypeCatalog

from .factories import (
    AdmissionsSchemaTestCase,
    make_applicant,
    make_cycle,
    make_programme,
    make_session,
    seed_application_types,
)


def _programme(program_type):
    """An active programme of the given program_type."""
    return make_programme(code=f"T-{program_type[:3].upper()}",
                          program_type=program_type)


class ProgramAvailableTypeTests(AdmissionsSchemaTestCase):
    """A programme's own program_type is the default answer."""

    def setUp(self):
        self.types = {t.code: t for t in seed_application_types()}

    def test_a_programme_only_offers_its_own_type(self):
        program = _programme("diploma")
        self.assertEqual(
            [t.code for t in program_available_types(program)], ["diploma"]
        )
        self.assertNotIn("postgraduate", [t.code for t in program_available_types(program)])

    def test_postgraduate_programme_does_not_offer_diploma(self):
        program = _programme("postgraduate")
        self.assertEqual(
            [t.code for t in program_available_types(program)], ["postgraduate"]
        )

    def test_doctorate_and_professional_map_onto_real_types(self):
        """Those program_type values have no ApplicationTypeCode of their own."""
        self.assertEqual(
            [t.code for t in program_available_types(_programme("doctorate"))],
            ["postgraduate"],
        )
        self.assertEqual(
            [t.code for t in program_available_types(_programme("professional"))],
            ["diploma"],
        )

    def test_explicit_override_replaces_the_default(self):
        program = _programme("undergraduate")
        ProgramApplicationType.objects.create(
            program=program, application_type=self.types["mature"]
        )
        self.assertEqual(
            [t.code for t in program_available_types(program)], ["mature"]
        )
        ProgramApplicationType.objects.filter(program=program).delete()
        # Removing the override goes back to the programme's own type.
        self.assertEqual(
            [t.code for t in program_available_types(program)], ["undergraduate"]
        )

    def test_several_overrides_widen_the_list(self):
        program = _programme("undergraduate")
        for code in ("mature", "transfer"):
            ProgramApplicationType.objects.create(
                program=program, application_type=self.types[code]
            )
        self.assertEqual(
            [t.code for t in program_available_types(program)],
            ["mature", "transfer"],
        )

    def test_inactive_override_is_ignored(self):
        program = _programme("undergraduate")
        ProgramApplicationType.objects.create(
            program=program, application_type=self.types["mature"], is_active=False
        )
        self.assertEqual(
            [t.code for t in program_available_types(program)], ["undergraduate"]
        )

    def test_missing_catalogue_falls_back_to_every_active_type(self):
        """Nothing disappears just because seeding has not been run."""
        program = _programme("undergraduate")
        self.assertEqual(
            [t.code for t in program_available_types(program)], ["undergraduate"]
        )
        self.types["undergraduate"].delete()
        self.assertEqual(
            len(program_available_types(program)),
            ApplicationType.objects.filter(is_active=True).count(),
        )

    def test_program_method_delegates(self):
        program = _programme("certificate")
        self.assertEqual(
            [t.code for t in program.available_application_types()], ["certificate"]
        )


class CatalogTests(AdmissionsSchemaTestCase):
    def setUp(self):
        self.types = {t.code: t for t in seed_application_types()}

    def test_unrestricted_cycle_offers_every_active_type(self):
        cycle = make_cycle(name="open cycle")
        self.assertEqual(
            [t.code for t in ApplicationTypeCatalog.for_cycle(cycle)],
            list(ApplicationType.objects.filter(is_active=True)
                 .order_by("order", "code").values_list("code", flat=True)),
        )

    def test_restricted_cycle_offers_only_its_own(self):
        cycle = make_cycle(name="diploma intake")
        cycle.set_accepted_types([self.types["diploma"], self.types["certificate"]])
        self.assertEqual(
            [t.code for t in ApplicationTypeCatalog.for_cycle(cycle)],
            ["diploma", "certificate"],
        )

    def test_inactive_type_is_not_offered_even_if_configured(self):
        cycle = make_cycle(name="has inactive type")
        self.types["exchange"].is_active = False
        self.types["exchange"].save()
        cycle.set_accepted_types([self.types["undergraduate"], self.types["exchange"]])
        self.assertEqual(
            [t.code for t in ApplicationTypeCatalog.for_cycle(cycle)], ["undergraduate"]
        )

    def test_both_limits_intersect(self):
        cycle = make_cycle(name="two limit cycle")
        cycle.set_accepted_types([self.types["diploma"], self.types["undergraduate"]])
        undergraduate = _programme("undergraduate")
        self.assertEqual(
            [t.code for t in ApplicationTypeCatalog.available(cycle, undergraduate)],
            ["undergraduate"],
        )
        self.assertEqual(
            sorted(t.code for t in ApplicationTypeCatalog.available(cycle=cycle)),
            ["diploma", "undergraduate"],
        )

    def test_disjoint_limits_offer_nothing(self):
        cycle = make_cycle(name="diploma only cycle")
        cycle.set_accepted_types([self.types["diploma"]])
        postgraduate = _programme("postgraduate")
        self.assertEqual(
            ApplicationTypeCatalog.available(cycle, postgraduate), []
        )
        self.assertFalse(
            ApplicationTypeCatalog.is_valid_pair(cycle, postgraduate, "diploma")
        )


class ApplyFormTypeScopingTests(AdmissionsSchemaTestCase):
    """The public apply page must not offer what it will not accept."""

    def setUp(self):
        self.types = {t.code: t for t in seed_application_types()}
        self.cycle = make_cycle(name="public intake", active=True)
        self.cycle.set_accepted_types(
            [self.types["undergraduate"], self.types["mature"]]
        )

    def _post(self, program, code, **extra):
        data = {
            "first_name": "Ada", "last_name": "L",
            "email": extra.pop("email", "applicant@probe.test"),
            "date_of_birth": "2000-01-01",
            "program_applied": str(program.pk) if program else "",
            "application_type": code,
            "password1": "0123456789", "password2": "0123456789",
        }
        data.update(extra)
        return AdmissionApplicationForm(cycle=self.cycle, data=data)

    def test_dropdown_offers_only_the_cycles_types(self):
        form = AdmissionApplicationForm(cycle=self.cycle)
        self.assertEqual(
            sorted(form.available_type_codes), ["mature", "undergraduate"]
        )
        self.assertEqual(str(form["application_type"]).count("<option"), 2)

    def test_dropdown_offers_everything_when_the_cycle_is_unrestricted(self):
        open_cycle = make_cycle(name="unrestricted intake")
        form = AdmissionApplicationForm(cycle=open_cycle)
        self.assertEqual(
            len(form.available_type_codes),
            ApplicationType.objects.filter(is_active=True).count(),
        )

    def test_posting_a_type_the_cycle_excluded_is_refused(self):
        program = _programme("undergraduate")
        form = self._post(program, "postgraduate")
        self.assertFalse(form.is_valid())
        self.assertIn("application_type", form.errors)

    def test_posting_a_type_the_programme_does_not_run_is_refused(self):
        undergraduate = _programme("undergraduate")
        # Mature is on by the cycle, but this programme does not run it.
        form = self._post(undergraduate, "mature")
        self.assertFalse(form.is_valid())
        self.assertIn("It accepts", form.errors["application_type"][0])

    def test_a_valid_pair_is_accepted(self):
        program = _programme("undergraduate")
        form = self._post(program, "undergraduate")
        self.assertTrue(form.is_valid(), form.errors)

    def test_no_programme_still_uses_the_cycle_limit(self):
        form = self._post(None, "mature")
        self.assertTrue(form.is_valid(), form.errors)

    def test_page_gets_the_data_it_needs_to_narrow_client_side(self):
        form = AdmissionApplicationForm(cycle=self.cycle)
        attrs = form.fields["application_type"].widget.attrs
        self.assertIn("data-types-by-program", attrs)
        self.assertIn("data-types-all", attrs)
        self.assertEqual(
            sorted(__import__("json").loads(attrs["data-types-all"])),
            ["mature", "undergraduate"],
        )

    def test_catalogue_is_used_for_the_server_side_check(self):
        """The check must not be bypassable by narrowing the widget alone."""
        undergraduate = _programme("undergraduate")
        self.assertTrue(
            ApplicationTypeCatalog.is_valid_pair(
                self.cycle, undergraduate, "undergraduate"
            )
        )
        self.assertFalse(
            ApplicationTypeCatalog.is_valid_pair(
                self.cycle, undergraduate, "postgraduate"
            )
        )
