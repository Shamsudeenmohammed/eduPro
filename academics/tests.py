"""Tests for the central academic calculation engine and (Phase 5) the
progression & graduation engine."""
from decimal import Decimal

from django.core.management import call_command
from django.test import TestCase

from accounts.models import EduProUser, Role
from academics import services
from academics.models import (
    AcademicSession,
    Course,
    CourseOffering,
    Department,
    Enrolment,
    Faculty,
    GradeBoundary,
    GradingScheme,
    GraduationRecord,
    Institution,
    Level,
    Program,
    ProgressionPolicy,
    Semester,
    StudentProfile,
    StudentStatus,
)
from teachers.models import ResultSheet as TeacherResultSheet
from teachers.models import StudentResult


class AcademicGraphMixin:
    """Build the minimal institution graph needed for result/GPA tests."""

    def build_graph(self):
        institution = Institution.objects.create(
            name="Test University", short_name="TU", is_active=True,
        )
        faculty = Faculty.objects.create(
            institution=institution, code="SCI", name="Science", is_active=True,
        )
        department = Department.objects.create(
            institution=institution, faculty=faculty,
            code="CS", name="Computer Science", is_active=True,
        )
        program = Program.objects.create(
            department=department, code="BS-CS", name="BSc Computer Science",
            is_active=True,
        )
        level = Level.objects.get_or_create(
            program=program, order=1,
            defaults={"name": "100", "is_active": True},
        )[0]

        session = AcademicSession.objects.create(
            name="2025/2026", start_date="2025-09-01", end_date="2026-07-31",
        )
        sem1 = Semester.objects.create(
            session=session, name=Semester.SemesterType.FIRST,
            start_date="2025-09-01", end_date="2026-01-31",
        )
        sem2 = Semester.objects.create(
            session=session, name=Semester.SemesterType.SECOND,
            start_date="2026-02-01", end_date="2026-07-31",
        )
        return institution, department, program, level, sem1, sem2

    def build_student(self, program, level):
        student = EduProUser.objects.create_user(
            email="student@test.edu", password="pw", role=Role.STUDENT,
            first_name="Razak", last_name="Abdullah", is_active=True,
        )
        StudentProfile.objects.create(student=student, program=program, current_level=level)
        return student


class GradeResolutionTests(TestCase, AcademicGraphMixin):
    def setUp(self):
        call_command("seed_grading_scheme")

    def test_top_band(self):
        for score in (90, 95, 100):
            grade, point = services.resolve_grade(score)
            self.assertEqual(grade, "A+", score)
            self.assertEqual(point, Decimal("4.0"))

    def test_boundary_thresholds(self):
        cases = {
            85: "A", 84.99: "A-", 80: "A-", 77: "B+", 73: "B", 70: "B-",
            67: "C+", 63: "C", 60: "C-", 50: "D", 49.99: "F", 0: "F",
        }
        expected_points = {
            "A": Decimal("4.0"), "A-": Decimal("3.7"), "B+": Decimal("3.3"),
            "B": Decimal("3.0"), "B-": Decimal("2.7"), "C+": Decimal("2.3"),
            "C": Decimal("2.0"), "C-": Decimal("1.7"), "D": Decimal("1.0"),
            "F": Decimal("0.0"),
        }
        for score, grade in cases.items():
            resolved_grade, resolved_point = services.resolve_grade(score)
            self.assertEqual(resolved_grade, grade, f"score={score}")
            self.assertEqual(resolved_point, expected_points[grade], f"score={score}")

    def test_falls_back_to_legacy_scale_when_no_config(self):
        GradingScheme.objects.all().delete()
        GradeBoundary.objects.all().delete()

        for score, grade in ((90, "A+"), (87, "A"), (74, "B"), (45, "F"), (0, "F")):
            resolved_grade, resolved_point = services.resolve_grade(score)
            self.assertEqual(resolved_grade, grade, f"score={score}")
            self.assertTrue(resolved_point, f"score={score}")


class ComputeTotalTests(TestCase):
    def test_missing_scores_return_none(self):
        self.assertIsNone(services.compute_total(None, None))

    def test_weighted_total(self):
        total = services.compute_total(30, 50, ca_weight=30, exam_weight=70)
        self.assertEqual(total, Decimal("44.00"))

    def test_zero_and_full(self):
        self.assertEqual(services.compute_total(0, 0), Decimal("0.00"))
        self.assertEqual(services.compute_total(100, 100), Decimal("100.00"))

    def test_partial_missing_score_counts_as_zero(self):
        self.assertEqual(services.compute_total(100, None), Decimal("30.00"))


class GpaEngineTests(TestCase, AcademicGraphMixin):
    def setUp(self):
        call_command("seed_grading_scheme")
        (self.institution, self.department, self.program, self.level,
         self.sem1, self.sem2) = self.build_graph()
        self.student = self.build_student(self.program, self.level)
        self.teacher = EduProUser.objects.create_user(
            email="teacher@test.edu", password="pw", role=Role.TEACHER,
            first_name="Mahamadu", last_name="Azindoo", is_active=True,
        )

    def _make_result(self, semester, course_code, credits, grade_point):
        course = Course.objects.create(
            department=self.department, code=course_code,
            title=f"Course {course_code}", credit_units=credits, is_active=True,
        )
        offering = CourseOffering.objects.create(
            course=course, semester=semester, level=self.level,
            level_name="100", venue="LT1", is_active=True,
        )
        offering.departments.add(self.department)
        enrolment = Enrolment.objects.create(
            student=self.student, offering=offering, is_active=True,
        )
        sheet = TeacherResultSheet.objects.create(
            offering=offering, submitted_by=self.teacher, status="approved",
        )
        return StudentResult.objects.create(
            result_sheet=sheet, enrolment=enrolment,
            total_score=Decimal("90.00"), grade="A+", grade_point=grade_point,
        )

    def test_semester_gpa_and_cgpa(self):
        self._make_result(self.sem1, "CS101", credits=3, grade_point=Decimal("4.0"))
        self._make_result(self.sem2, "CS202", credits=2, grade_point=Decimal("0.0"))

        self.assertEqual(services.calculate_semester_gpa(self.student, self.sem1), Decimal("4.00"))
        self.assertEqual(services.calculate_semester_gpa(self.student, self.sem2), Decimal("0.00"))
        self.assertEqual(services.calculate_cgpa(self.student), Decimal("2.40"))

    def test_update_student_gpa_persists_cgpa(self):
        self._make_result(self.sem1, "CS101", credits=3, grade_point=Decimal("4.0"))
        self._make_result(self.sem2, "CS202", credits=2, grade_point=Decimal("0.0"))

        services.update_student_gpa(self.student)
        self.student.academic_profile.refresh_from_db()

        self.assertEqual(self.student.academic_profile.cumulative_gpa, Decimal("2.40"))

    def test_gpa_is_none_without_approved_results(self):
        self.assertIsNone(services.calculate_cgpa(self.student))
        services.update_student_gpa(self.student)
        self.student.academic_profile.refresh_from_db()
        self.assertIsNone(self.student.academic_profile.cumulative_gpa)

    def test_compute_result_routes_through_engine(self):
        course = Course.objects.create(
            department=self.department, code="CS303", title="Databases",
            credit_units=3, is_active=True,
        )
        offering = CourseOffering.objects.create(
            course=course, semester=self.sem1, level=self.level,
            level_name="100", venue="LT2", is_active=True,
        )
        offering.departments.add(self.department)
        enrolment = Enrolment.objects.create(
            student=self.student, offering=offering, is_active=True,
        )
        sheet = TeacherResultSheet.objects.create(
            offering=offering, submitted_by=self.teacher,
            status="open", ca_weight=30, exam_weight=70,
        )
        result = StudentResult.objects.create(
            result_sheet=sheet, enrolment=enrolment,
            ca_score=Decimal("85.00"), exam_score=Decimal("80.00"),
        )
        result.refresh_from_db()

        self.assertEqual(result.total_score, Decimal("81.50"))
        self.assertEqual(result.grade, "A-")
        self.assertEqual(result.grade_point, Decimal("3.7"))


class SeedGradingSchemeTests(TestCase):
    def test_idempotent(self):
        call_command("seed_grading_scheme")
        first = GradingScheme.objects.get(name="standard")

        boundary = first.boundaries.get(grade="B")
        boundary.grade_point = Decimal("2.5")
        boundary.save()

        call_command("seed_grading_scheme")

        self.assertEqual(GradingScheme.objects.filter(name="standard").count(), 1)
        self.assertEqual(GradeBoundary.objects.filter(scheme=first).count(), 11)
        self.assertEqual(GradingScheme.objects.filter(is_default=True).count(), 1)
        boundary.refresh_from_db()
        self.assertEqual(boundary.grade_point, Decimal("2.5"))


# ─────────────────────────────────────────────────────────────────────────────
# Phase 5 — Progression & Graduation engine tests
# ─────────────────────────────────────────────────────────────────────────────

class ProgressionPolicyTests(TestCase, AcademicGraphMixin):
    def setUp(self):
        (self.institution, self.department, self.program, self.level,
         self.sem1, self.sem2) = self.build_graph()

    def test_no_policy_returns_none(self):
        self.assertIsNone(services.get_progression_policy(self.program))

    def test_global_default_applies_to_any_program(self):
        ProgressionPolicy.objects.create(
            min_cgpa_to_advance=Decimal("1.50"), min_credits_per_year=40,
        )
        policy = services.get_progression_policy(self.program)
        self.assertIsNotNone(policy)
        self.assertIsNone(policy.program)
        self.assertEqual(policy.min_cgpa_to_advance, Decimal("1.50"))

    def test_program_override_beats_global(self):
        ProgressionPolicy.objects.create(
            min_cgpa_to_advance=Decimal("1.00"),
        )
        ProgressionPolicy.objects.create(
            program=self.program, min_cgpa_to_advance=Decimal("2.00"),
        )
        policy = services.get_progression_policy(self.program)
        self.assertEqual(policy.program, self.program)
        self.assertEqual(policy.min_cgpa_to_advance, Decimal("2.00"))

    def test_inactive_program_policy_falls_back_to_global(self):
        ProgressionPolicy.objects.create(
            min_cgpa_to_advance=Decimal("1.00"),
        )
        ProgressionPolicy.objects.create(
            program=self.program, min_cgpa_to_advance=Decimal("2.00"),
            is_active=False,
        )
        policy = services.get_progression_policy(self.program)
        self.assertIsNone(policy.program)
        self.assertEqual(policy.min_cgpa_to_advance, Decimal("1.00"))


class ProgressionEngineTests(TestCase, AcademicGraphMixin):
    def setUp(self):
        call_command("seed_grading_scheme")
        (self.institution, self.department, self.program, self.level,
         self.sem1, self.sem2) = self.build_graph()
        self.student = self.build_student(self.program, self.level)
        self.teacher = EduProUser.objects.create_user(
            email="teacher@test.edu", password="pw", role=Role.TEACHER,
            first_name="Mahamadu", last_name="Azindoo", is_active=True,
        )

    def _make_result(self, semester, course_code, credits, grade_point, grade="A+"):
        course = Course.objects.create(
            department=self.department, code=course_code,
            title=f"Course {course_code}", credit_units=credits, is_active=True,
        )
        offering = CourseOffering.objects.create(
            course=course, semester=semester, level=self.level,
            level_name="100", venue="LT1", is_active=True,
        )
        offering.departments.add(self.department)
        enrolment = Enrolment.objects.create(
            student=self.student, offering=offering, is_active=True,
        )
        sheet = TeacherResultSheet.objects.create(
            offering=offering, submitted_by=self.teacher, status="approved",
        )
        return StudentResult.objects.create(
            result_sheet=sheet, enrolment=enrolment,
            total_score=Decimal("90.00"), grade=grade, grade_point=grade_point,
        )

    def _move_to_final_level(self):
        final = Level.objects.get(program=self.program, order=4)
        self.student.academic_profile.current_level = final
        self.student.academic_profile.save(update_fields=["current_level", "updated_at"])

    def test_advance(self):
        self._make_result(self.sem1, "CS101", credits=3, grade_point=Decimal("3.0"))
        out = services.evaluate_progression(self.student, self.sem1.session)
        self.assertEqual(out["decision"], "ADVANCE")
        self.assertIsNotNone(out["next_level"])
        self.assertEqual(out["session_gpa"], Decimal("3.00"))
        self.assertEqual(out["pass_ratio"], Decimal("1.00"))

    def test_probation_below_threshold(self):
        self._make_result(self.sem1, "CS101", credits=3, grade_point=Decimal("0.3"))
        out = services.evaluate_progression(self.student, self.sem1.session)
        self.assertEqual(out["decision"], "PROBATION")

    def test_withdrawn_below_withdraw_threshold(self):
        self._make_result(self.sem1, "CS101", credits=3, grade_point=Decimal("0.1"))
        out = services.evaluate_progression(self.student, self.sem1.session)
        self.assertEqual(out["decision"], "WITHDRAWN")

    def test_repeat_when_too_many_failures(self):
        # 3 failures (F) + 2 passes → session GPA 0.8 but 3 fails > max 2
        self._make_result(self.sem1, "CS111", credits=3, grade_point=Decimal("0.0"), grade="F")
        self._make_result(self.sem2, "CS112", credits=3, grade_point=Decimal("0.0"), grade="F")
        self._make_result(self.sem1, "CS113", credits=3, grade_point=Decimal("0.0"), grade="F")
        self._make_result(self.sem1, "CS900", credits=3, grade_point=Decimal("2.0"))
        self._make_result(self.sem2, "CS901", credits=3, grade_point=Decimal("2.0"))
        out = services.evaluate_progression(self.student, self.sem1.session)
        self.assertEqual(out["decision"], "REPEAT")
        self.assertEqual(len(out["failed_courses"]), 3)

    def test_completed_at_final_level(self):
        self._move_to_final_level()
        self._make_result(self.sem1, "CS101", credits=3, grade_point=Decimal("3.0"))
        out = services.evaluate_progression(self.student, self.sem1.session)
        self.assertEqual(out["decision"], "COMPLETED")
        self.assertIsNone(out["next_level"])

    def test_deferred_without_results(self):
        out = services.evaluate_progression(self.student, self.sem1.session)
        self.assertEqual(out["decision"], "DEFERRED")
        self.assertIsNone(out["session_gpa"])


class ProgressionApplyTests(TestCase, AcademicGraphMixin):
    def setUp(self):
        call_command("seed_grading_scheme")
        (self.institution, self.department, self.program, self.level,
         self.sem1, self.sem2) = self.build_graph()
        self.student = self.build_student(self.program, self.level)
        self.teacher = EduProUser.objects.create_user(
            email="teacher@test.edu", password="pw", role=Role.TEACHER,
        )

    def _make_result(self, semester, course_code, grade_point, credits=3):
        course = Course.objects.create(
            department=self.department, code=course_code,
            title=f"Course {course_code}", credit_units=credits, is_active=True,
        )
        offering = CourseOffering.objects.create(
            course=course, semester=semester, level=self.level,
            level_name="100", venue="LT1", is_active=True,
        )
        offering.departments.add(self.department)
        enrolment = Enrolment.objects.create(
            student=self.student, offering=offering, is_active=True,
        )
        sheet = TeacherResultSheet.objects.create(
            offering=offering, submitted_by=self.teacher, status="approved",
        )
        return StudentResult.objects.create(
            result_sheet=sheet, enrolment=enrolment,
            total_score=Decimal("90.00"), grade="A+", grade_point=grade_point,
        )

    def test_applies_status_advances_level_and_credits(self):
        self._make_result(self.sem1, "CS101", grade_point=Decimal("3.0"))
        session = self.sem1.session

        out = services.evaluate_progression(self.student, session)
        status = services.apply_progression(self.student, session, out)

        self.assertEqual(StudentStatus.objects.filter(student=self.student, session=session).count(), 1)
        self.assertEqual(status.decision, "ADVANCE")

        profile = self.student.academic_profile
        profile.refresh_from_db()
        self.assertEqual(profile.current_level, Level.objects.get(program=self.program, order=2))
        self.assertEqual(profile.total_credits_earned, 3)

    def test_reapply_is_idempotent_for_level_and_credits(self):
        self._make_result(self.sem1, "CS101", grade_point=Decimal("3.0"))
        session = self.sem1.session
        out = services.evaluate_progression(self.student, session)

        services.apply_progression(self.student, session, out)
        services.apply_progression(self.student, session, out)

        profile = self.student.academic_profile
        profile.refresh_from_db()
        self.assertEqual(
            StudentStatus.objects.filter(student=self.student, session=session).count(), 1,
        )
        self.assertEqual(profile.current_level, Level.objects.get(program=self.program, order=2))
        self.assertEqual(profile.total_credits_earned, 3)

    def test_run_progression_for_session_dry_run_does_not_persist(self):
        self._make_result(self.sem1, "CS101", grade_point=Decimal("3.0"))
        summary = services.run_progression_for_session(
            self.sem1.session, dry_run=True,
        )
        self.assertEqual(summary.get("ADVANCE"), 1)
        self.assertEqual(StudentStatus.objects.count(), 0)
        self.student.academic_profile.refresh_from_db()
        self.assertEqual(self.student.academic_profile.current_level, self.level)

    def test_run_progression_for_session_applies(self):
        self._make_result(self.sem1, "CS101", grade_point=Decimal("3.0"))
        summary = services.run_progression_for_session(self.sem1.session)
        self.assertEqual(summary.get("ADVANCE"), 1)
        self.assertEqual(StudentStatus.objects.count(), 1)
        self.student.academic_profile.refresh_from_db()
        self.assertEqual(
            self.student.academic_profile.current_level,
            Level.objects.get(program=self.program, order=2),
        )


class GraduationEngineTests(TestCase, AcademicGraphMixin):
    def setUp(self):
        call_command("seed_grading_scheme")
        (self.institution, self.department, self.program, self.level,
         self.sem1, self.sem2) = self.build_graph()
        self.student = self.build_student(self.program, self.level)
        self.teacher = EduProUser.objects.create_user(
            email="teacher@test.edu", password="pw", role=Role.TEACHER,
        )

    def _make_result(self, semester, course_code, grade_point, grade="A+"):
        course = Course.objects.create(
            department=self.department, code=course_code,
            title=f"Course {course_code}", credit_units=3, is_active=True,
        )
        offering = CourseOffering.objects.create(
            course=course, semester=semester, level=self.level,
            level_name="100", venue="LT1", is_active=True,
        )
        offering.departments.add(self.department)
        enrolment = Enrolment.objects.create(
            student=self.student, offering=offering, is_active=True,
        )
        sheet = TeacherResultSheet.objects.create(
            offering=offering, submitted_by=self.teacher, status="approved",
        )
        return StudentResult.objects.create(
            result_sheet=sheet, enrolment=enrolment,
            total_score=Decimal("90.00"), grade=grade, grade_point=grade_point,
        )

    def _move_to_final_level(self):
        final = Level.objects.get(program=self.program, order=4)
        self.student.academic_profile.current_level = final
        self.student.academic_profile.save(update_fields=["current_level", "updated_at"])

    def test_eligible_at_final_level(self):
        self._move_to_final_level()
        self._make_result(self.sem1, "CS101", grade_point=Decimal("3.0"))
        result = services.evaluate_graduation(self.student)
        self.assertTrue(result["eligible"])
        self.assertEqual(result["cgpa"], Decimal("3.00"))
        self.assertEqual(result["award_class"], "second_upper")

    def test_not_eligible_before_final_level(self):
        self._make_result(self.sem1, "CS101", grade_point=Decimal("3.0"))
        result = services.evaluate_graduation(self.student)
        self.assertFalse(result["eligible"])
        self.assertTrue(any("final level" in r for r in result["reasons"]))

    def test_outstanding_failure_blocks_graduation(self):
        self._move_to_final_level()
        self._make_result(self.sem1, "CS101", grade_point=Decimal("0.0"), grade="F")
        result = services.evaluate_graduation(self.student)
        self.assertFalse(result["eligible"])
        self.assertTrue(any("outstanding" in r for r in result["reasons"]))

    def test_low_cgpa_blocks_graduation(self):
        self._move_to_final_level()
        self._make_result(self.sem1, "CS101", grade_point=Decimal("0.3"))
        result = services.evaluate_graduation(self.student)
        self.assertFalse(result["eligible"])
        self.assertEqual(result["cgpa"], Decimal("0.30"))
        self.assertTrue(any("1.00" in r for r in result["reasons"]))

    def test_award_class_mapping(self):
        self.assertEqual(services._award_class(Decimal("3.70")), "first")
        self.assertEqual(services._award_class(Decimal("3.00")), "second_upper")
        self.assertEqual(services._award_class(Decimal("2.60")), "second_lower")
        self.assertEqual(services._award_class(Decimal("2.10")), "third")
        self.assertEqual(services._award_class(Decimal("1.50")), "pass")
        self.assertEqual(services._award_class(Decimal("0.50")), "not_awarded")
        self.assertEqual(services._award_class(None), "")

    def test_create_graduation_record_confirms_eligibility(self):
        self._move_to_final_level()
        self._make_result(self.sem1, "CS101", grade_point=Decimal("3.0"))
        record = services.create_graduation_record(self.student, self.sem1.session)
        self.assertIsNotNone(record)
        self.assertTrue(record.eligible)
        self.assertEqual(record.status, "confirmed")
        self.assertEqual(record.award_class, "second_upper")
        self.assertEqual(record.cgpa, Decimal("3.00"))


class ProgressionCommandTests(TestCase, AcademicGraphMixin):
    """Lightweight checks that the management commands wire through correctly."""

    def setUp(self):
        call_command("seed_grading_scheme")
        (self.institution, self.department, self.program, self.level,
         self.sem1, self.sem2) = self.build_graph()
        self.student = self.build_student(self.program, self.level)
        self.teacher = EduProUser.objects.create_user(
            email="teacher@test.edu", password="pw", role=Role.TEACHER,
        )

    def _approved_result(self, semester, course_code, grade_point):
        course = Course.objects.create(
            department=self.department, code=course_code,
            title=f"Course {course_code}", credit_units=3, is_active=True,
        )
        offering = CourseOffering.objects.create(
            course=course, semester=semester, level=self.level,
            level_name="100", venue="LT1", is_active=True,
        )
        offering.departments.add(self.department)
        enrolment = Enrolment.objects.create(
            student=self.student, offering=offering, is_active=True,
        )
        sheet = TeacherResultSheet.objects.create(
            offering=offering, submitted_by=self.teacher, status="approved",
        )
        return StudentResult.objects.create(
            result_sheet=sheet, enrolment=enrolment,
            total_score=Decimal("90.00"), grade="A+", grade_point=grade_point,
        )

    def test_run_progression_command_dry_run_persists_nothing(self):
        self._approved_result(self.sem1, "CS101", Decimal("3.0"))
        call_command("run_progression", session=self.sem1.session.name, dry_run=True)
        self.assertEqual(StudentStatus.objects.count(), 0)
        self.student.academic_profile.refresh_from_db()
        self.assertEqual(self.student.academic_profile.current_level, self.level)

    def test_run_progression_command_applies(self):
        self._approved_result(self.sem1, "CS101", Decimal("3.0"))
        call_command("run_progression", session=self.sem1.session.name)
        self.assertEqual(StudentStatus.objects.count(), 1)
        self.student.academic_profile.refresh_from_db()
        self.assertEqual(
            self.student.academic_profile.current_level,
            Level.objects.get(program=self.program, order=2),
        )

    def test_process_graduation_command_creates_confirmed_record(self):
        final = Level.objects.get(program=self.program, order=4)
        self.student.academic_profile.current_level = final
        self.student.academic_profile.save(update_fields=["current_level", "updated_at"])
        self._approved_result(self.sem1, "CS101", Decimal("3.0"))

        call_command("process_graduation", session=self.sem1.session.name)

        record = GraduationRecord.objects.get(
            student=self.student, program=self.program, session=self.sem1.session,
        )
        self.assertTrue(record.eligible)
        self.assertEqual(record.status, "confirmed")
        self.assertEqual(record.award_class, "second_upper")