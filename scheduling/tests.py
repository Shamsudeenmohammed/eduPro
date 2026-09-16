"""
scheduling/tests.py — regression tests for the scheduling module.

Run with:
    python manage.py test scheduling.tests -v2

Covers:
  - Overlap / constraint helpers
  - Schedule lifecycle: create, generate, submit → HOD review → publish
  - HOD workflow: rejection reasons, correction-required state, re-submit
  - Conflict detection unique-counting (not pairwise-inflated)
  - Role-based scoping of timetables + calendar (students / lecturers / HODs
    never see unpublished schedules or out-of-scope entries/events)
  - View-level access control (anonymous, student, HOD)
"""

import datetime

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from academics.models import (
    AcademicSession,
    Course,
    CourseAllocation,
    CourseOffering,
    Department,
    Enrolment,
    Faculty,
    Institution,
    Semester,
)

from scheduling.constants import (
    ConflictKind,
    ConflictSeverity,
    EventScope,
    EventType,
    IsoWeekday,
    ScheduleStatus,
    ScheduleType,
    SessionType,
)
from scheduling.engine import constraints
from scheduling.engine.conflict_detector import detect_conflicts
from scheduling.models import (
    AcademicEvent,
    AcademicSchedule,
    Building,
    Room,
    ScheduleHodApproval,
    SchedulingConfig,
    times_overlap,
)
from scheduling import services

User = get_user_model()


def _time(h, m=0):
    return datetime.time(h, m)


def _days(data):
    return [e for day in data["days"] for e in day["entries"]]


def _class_codes(data):
    return {it["title"] for it in data["items"] if it["kind"] == "class"}


def _event_titles(data):
    return {it["title"] for it in data["items"] if it["kind"] == "event"}


class TimeOverlapTest(TestCase):
    def test_overlap(self):
        self.assertTrue(times_overlap(
            _time(9, 0), _time(10, 0), _time(9, 30), _time(10, 30)))
        self.assertFalse(times_overlap(
            _time(9, 0), _time(10, 0), _time(10, 0), _time(11, 0)))
        self.assertTrue(times_overlap(
            _time(9, 0), _time(11, 0), _time(9, 30), _time(10, 30)))


class ConstraintHelpersTest(TestCase):
    def test_violation_dict_shape(self):
        v = constraints._violation(
            ConflictSeverity.HARD, ConflictKind.LECTURER_BUSY, "Lecturer busy")
        self.assertEqual(v["severity"], "hard")
        self.assertEqual(v["kind"], "lecturer_busy")
        self.assertIn("message", v)


class _BaseTestCase(TestCase):
    _counter = 0

    @classmethod
    def _next_name(cls):
        cls._counter += 1
        return f"Timetable {cls._counter}"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        cls.inst = Institution.objects.create(
            name="Test University", short_name="TU")
        cls.faculty = Faculty.objects.create(
            institution=cls.inst, name="Faculty of Science", code="FOS")
        cls.dept = Department.objects.create(
            institution=cls.inst, faculty=cls.faculty,
            name="Computer Science", code="CS")
        cls.dept2 = Department.objects.create(
            institution=cls.inst, faculty=cls.faculty,
            name="Information Technology", code="IT")

        cls.admin = User.objects.create_superuser(
            "admin@test.com", "x", role="admin",
            first_name="Admin", last_name="One", is_active=True)
        cls.hod = User.objects.create_user(
            "hod@test.com", "x", role="teacher", is_active=True,
            first_name="Hod", last_name="Cs")
        cls.lecturer = User.objects.create_user(
            "lect@test.com", "x", role="teacher", is_active=True,
            first_name="Lect", last_name="Erer")
        cls.lecturer2 = User.objects.create_user(
            "lect2@test.com", "x", role="teacher", is_active=True,
            first_name="Lect", last_name="Two")
        cls.student = User.objects.create_user(
            "stu@test.com", "x", role="student", is_active=True,
            first_name="Stu", last_name="Dent")

        cls.dept.hod_id = cls.hod.pk
        cls.dept.save(update_fields=["hod"])

        cls.session = AcademicSession.objects.create(
            name="2025/2026",
            start_date=datetime.date(2025, 9, 1),
            end_date=datetime.date(2026, 8, 31),
            is_current=True)
        cls.sem = Semester.objects.create(
            session=cls.session, name="first",
            start_date=datetime.date(2025, 9, 1),
            end_date=datetime.date(2026, 1, 31),
            is_current=True)

        cls.course1 = Course.objects.create(
            department=cls.dept, code="CS101", title="Intro to CS",
            lecture_hours_per_week=2, lab_hours_per_week=0)
        cls.course2 = Course.objects.create(
            department=cls.dept2, code="IT101", title="Intro to IT",
            lecture_hours_per_week=2, lab_hours_per_week=0)
        cls.course3 = Course.objects.create(
            department=cls.dept2, code="MATH101", title="Intro to Math",
            lecture_hours_per_week=2, lab_hours_per_week=0)

        cls.offering1 = CourseOffering.objects.create(
            course=cls.course1, semester=cls.sem, level_name="100",
            max_students=80)
        cls.offering1.departments.add(cls.dept)
        cls.offering2 = CourseOffering.objects.create(
            course=cls.course2, semester=cls.sem, level_name="100",
            max_students=80)
        cls.offering2.departments.add(cls.dept2)
        cls.offering3 = CourseOffering.objects.create(
            course=cls.course3, semester=cls.sem, level_name="100",
            max_students=80)
        cls.offering3.departments.add(cls.dept2)

        CourseAllocation.objects.create(
            offering=cls.offering1, teacher=cls.lecturer)
        CourseAllocation.objects.create(
            offering=cls.offering2, teacher=cls.lecturer)
        CourseAllocation.objects.create(
            offering=cls.offering3, teacher=cls.lecturer)

        Enrolment.objects.create(
            student=cls.student, offering=cls.offering1,
            is_active=True, status="active")

        cls.bldg = Building.objects.create(
            institution=cls.inst, name="Block A", code="A")
        cls.room = Room.objects.create(
            building=cls.bldg, name="Hall A", code="A101",
            room_type="lecture", capacity=120, exam_capacity=120)
        cls.room2 = Room.objects.create(
            building=cls.bldg, name="Hall B", code="B101",
            room_type="lecture", capacity=120, exam_capacity=120)
        cls.room3 = Room.objects.create(
            building=cls.bldg, name="Hall C", code="C101",
            room_type="lecture", capacity=120, exam_capacity=120)

        cls.config = SchedulingConfig.for_institution(cls.inst)

    def make_schedule(self):
        sched = services.create_schedule(
            self._next_name(), self.sem, ScheduleType.CLASS, actor=self.admin)
        services.add_entry(
            sched, self.offering1, IsoWeekday.MONDAY, _time(9), _time(10),
            SessionType.LECTURE, room=self.room, lecturer=self.lecturer,
            actor=self.admin)
        return sched

    def make_published_schedule(self):
        sched = self.make_schedule()
        services.add_entry(
            sched, self.offering2, IsoWeekday.TUESDAY, _time(9), _time(10),
            SessionType.LECTURE, room=self.room2, lecturer=self.lecturer,
            actor=self.admin)
        sched.status = ScheduleStatus.PUBLISHED
        sched.save()
        return sched


class WorkflowTest(_BaseTestCase):
    def test_create_makes_draft(self):
        sched = services.create_schedule(
            self._next_name(), self.sem, ScheduleType.CLASS, actor=self.admin)
        self.assertEqual(sched.status, ScheduleStatus.DRAFT)
        self.assertEqual(sched.entries.count(), 0)

    def test_generate_places_sessions(self):
        sched = services.create_schedule(
            self._next_name(), self.sem, ScheduleType.CLASS, actor=self.admin)
        result = services.generate_schedule(sched, self.admin)
        self.assertIsInstance(result["unplaced"], int)
        self.assertIsInstance(result["total"], int)
        self.assertGreater(sched.entries.count(), 0)

    def test_submit_moves_to_under_review_and_clears_prior_approvals(self):
        sched = self.make_schedule()
        ScheduleHodApproval.objects.create(
            schedule=sched, department=self.dept, hod=self.hod, approved=True)
        services.submit_for_review(sched, self.admin)
        sched.refresh_from_db()
        self.assertEqual(sched.status, ScheduleStatus.UNDER_REVIEW)
        self.assertEqual(sched.hod_approvals.count(), 0)

    def test_submit_on_wrong_status_rejected(self):
        sched = self.make_schedule()
        sched.status = ScheduleStatus.PUBLISHED
        sched.save()
        with self.assertRaises(ValueError):
            services.submit_for_review(sched, self.admin)

    def test_hod_reject_requires_comment(self):
        sched = self.make_schedule()
        services.submit_for_review(sched, self.admin)
        with self.assertRaises(ValueError):
            services.hod_approve(sched, self.hod, self.dept, False)
        sched.refresh_from_db()
        self.assertEqual(sched.status, ScheduleStatus.UNDER_REVIEW)

    def test_hod_reject_sets_correction_required_then_keeps_editable(self):
        sched = self.make_schedule()
        services.submit_for_review(sched, self.admin)
        services.hod_approve(
            sched, self.hod, self.dept, False, comment="Room double-booked")
        sched.refresh_from_db()
        self.assertEqual(sched.status, ScheduleStatus.CORRECTION_REQUIRED)
        self.assertFalse(sched.is_locked)
        # resubmission after rework is allowed again
        services.submit_for_review(sched, self.admin)
        sched.refresh_from_db()
        self.assertEqual(sched.status, ScheduleStatus.UNDER_REVIEW)

    def test_cross_department_hod_cannot_approve_others(self):
        sched = self.make_schedule()
        self.offering1.departments.add(self.dept2)
        services.submit_for_review(sched, self.admin)
        # hod is not HOD of dept2
        hod2 = User.objects.create_user(
            "hod2@test.com", "x", role="teacher", is_active=True)
        self.dept2.hod_id = hod2.pk
        self.dept2.save(update_fields=["hod"])
        with self.assertRaises(ValueError):
            services.hod_approve(sched, hod2, self.dept, True)

    def test_all_owning_hods_must_approve(self):
        self.offering1.departments.add(self.dept2)
        sched = self.make_schedule()
        services.submit_for_review(sched, self.admin)
        services.hod_approve(sched, self.hod, self.dept, True)
        sched.refresh_from_db()
        self.assertEqual(sched.status, ScheduleStatus.UNDER_REVIEW)
        hod2 = User.objects.create_user(
            "hod2b@test.com", "x", role="teacher", is_active=True)
        self.dept2.hod_id = hod2.pk
        self.dept2.save(update_fields=["hod"])
        services.hod_approve(sched, hod2, self.dept2, True)
        sched.refresh_from_db()
        self.assertEqual(sched.status, ScheduleStatus.HOD_APPROVED)

    def test_single_department_approval_auto_approves(self):
        sched = self.make_schedule()
        services.submit_for_review(sched, self.admin)
        services.hod_approve(sched, self.hod, self.dept, True)
        sched.refresh_from_db()
        self.assertEqual(sched.status, ScheduleStatus.HOD_APPROVED)

    def test_publish_guard_blocks_unplaced(self):
        sched = self.make_schedule()
        sched.unplaced = 2
        sched.save(update_fields=["unplaced"])
        with self.assertRaises(ValueError):
            services.publish_schedule(sched, self.admin)

    def test_publish_guard_blocks_hard_conflicts(self):
        sched = self.make_schedule()
        services.add_entry(
            sched, self.offering2, IsoWeekday.MONDAY, _time(9), _time(10),
            SessionType.LECTURE, room=self.room, lecturer=self.lecturer,
            actor=self.admin)
        with self.assertRaises(ValueError):
            services.publish_schedule(sched, self.admin)

    def test_publish_clean_schedule(self):
        sched = self.make_schedule()
        services.publish_schedule(sched, self.admin)
        sched.refresh_from_db()
        self.assertEqual(sched.status, ScheduleStatus.PUBLISHED)
        self.assertTrue(sched.is_current)
        self.assertTrue(sched.is_published)

    def test_unpublish_withdraws(self):
        sched = self.make_schedule()
        services.publish_schedule(sched, self.admin)
        services.unpublish_schedule(sched, self.admin)
        sched.refresh_from_db()
        self.assertEqual(sched.status, ScheduleStatus.APPROVED)
        self.assertFalse(sched.is_published)


class ConflictCountTest(_BaseTestCase):
    def test_identical_entries_count_conflict_groups_once(self):
        """Two identical offerings at same time → room + lecturer + cohort = 3."""
        sched = self.make_schedule()
        services.add_entry(
            sched, self.offering1, IsoWeekday.MONDAY, _time(9), _time(10),
            SessionType.LECTURE, room=self.room, lecturer=self.lecturer,
            actor=self.admin)
        stats = detect_conflicts(sched, self.config)
        sched.refresh_from_db()
        # Old pairwise-inflated code would count 6 (3 kinds × 2 directions).
        self.assertEqual(stats["hard_conflicts"], 3)
        self.assertEqual(sched.hard_conflicts, 3)

    def test_lecturer_busy_pairwise_groups_not_inflated(self):
        """3 entries same slot/lecturer, distinct rooms + cohorts → 3 pair-groups."""
        sched = self.make_schedule()
        # Distinct cohort so MATH101 does not clash with IT101 (they share dept2).
        self.offering3.level_name = "200"
        self.offering3.save(update_fields=["level_name"])
        services.add_entry(
            sched, self.offering2, IsoWeekday.MONDAY, _time(9), _time(10),
            SessionType.LECTURE, room=self.room2, lecturer=self.lecturer,
            actor=self.admin)
        services.add_entry(
            sched, self.offering3, IsoWeekday.MONDAY, _time(9), _time(10),
            SessionType.LECTURE, room=self.room3, lecturer=self.lecturer,
            actor=self.admin)
        stats = detect_conflicts(sched, self.config)
        sched.refresh_from_db()
        # C(3,2)=3 distinct lecturer-busy pair-groups; old code counted 6.
        self.assertEqual(stats["hard_conflicts"], 3)


class ScopingTest(_BaseTestCase):
    def test_student_timetable_only_their_offerings(self):
        sched = self.make_published_schedule()
        data = services.timetable_for_user(self.student)
        self.assertEqual(data["schedule"].pk, sched.pk)
        self.assertEqual({e.course.code for e in _days(data)}, {"CS101"})

    def test_lecturer_timetable_only_published_and_theirs(self):
        sched = self.make_published_schedule()
        data = services.timetable_for_user(self.lecturer)
        self.assertEqual(data["schedule"].pk, sched.pk)
        self.assertEqual({e.course.code for e in _days(data)},
                         {"CS101", "IT101"})

    def test_admin_timetable_sees_everything(self):
        self.make_published_schedule()
        data = services.timetable_for_user(self.admin)
        self.assertEqual({e.course.code for e in _days(data)},
                         {"CS101", "IT101"})

    def test_student_sees_no_timetable_when_schedule_not_published(self):
        # DRAFT schedule isn't visible to students.
        sched = self.make_schedule()
        sched.status = ScheduleStatus.GENERATED
        sched.save(update_fields=["status"])
        data = services.timetable_for_user(self.student)
        self.assertIsNone(data["schedule"])

    def test_calendar_student_sees_only_own_classes_and_public_scoped_events(self):
        sched = self.make_published_schedule()
        AcademicEvent.objects.create(
            institution=self.inst, title="CS Open Day",
            event_type=EventType.GENERAL, scope=EventScope.DEPARTMENT,
            department=self.dept, start_date=self.sem.start_date,
            end_date=self.sem.start_date, is_public=True)
        AcademicEvent.objects.create(
            institution=self.inst, title="IT Retreat",
            event_type=EventType.GENERAL, scope=EventScope.OFFERING,
            offering=self.offering2, start_date=self.sem.start_date,
            end_date=self.sem.start_date, is_public=True)
        data = services.calendar_items(
            self.student, semester=self.sem,
            start=self.sem.start_date, end=self.sem.end_date)
        self.assertEqual(_class_codes(data), {"CS101"})
        self.assertEqual(_event_titles(data), {"CS Open Day"})

    def test_calendar_hod_sees_department_timetable_only(self):
        sched = self.make_published_schedule()
        data = services.calendar_items(
            self.hod, semester=self.sem,
            start=self.sem.start_date, end=self.sem.end_date)
        self.assertEqual(_class_codes(data), {"CS101"})

    def test_calendar_admin_sees_all_offerings(self):
        sched = self.make_published_schedule()
        data = services.calendar_items(
            self.admin, semester=self.sem,
            start=self.sem.start_date, end=self.sem.end_date)
        self.assertEqual(_class_codes(data), {"CS101", "IT101"})

    def test_calendar_filters_course(self):
        sched = self.make_published_schedule()
        data = services.calendar_items(
            self.admin, semester=self.sem,
            filters={"course": self.course1.pk},
            start=self.sem.start_date, end=self.sem.end_date)
        self.assertEqual(_class_codes(data), {"CS101"})

    def test_calendar_filters_room(self):
        sched = self.make_published_schedule()
        data = services.calendar_items(
            self.admin, semester=self.sem,
            filters={"room": self.room2.pk},
            start=self.sem.start_date, end=self.sem.end_date)
        self.assertEqual(_class_codes(data), {"IT101"})

    def test_update_entry_notifies_student_and_lecturer(self):
        from messaging.models import Message
        from students.models import StudentNotification

        # Rejections leave a schedule in CORRECTION_REQUIRED — still editable
        # (published schedules are locked), and rework must alert students.
        sched = self.make_schedule()
        services.submit_for_review(sched, self.admin)
        services.hod_approve(
            sched, self.hod, self.dept, False,
            comment="Move the lecture to another day.")
        sched.refresh_from_db()
        self.assertEqual(sched.status, ScheduleStatus.CORRECTION_REQUIRED)
        entry = sched.entries.filter(offering=self.offering1).first()
        services.update_entry(
            sched, entry.pk, self.admin, day=IsoWeekday.WEDNESDAY)
        self.assertTrue(StudentNotification.objects.filter(
            student=self.student).exists())
        self.assertTrue(Message.objects.filter(recipient=self.lecturer)
                        .exists())

    def test_update_entry_rejected_when_schedule_locked(self):
        sched = self.make_published_schedule()
        entry = sched.entries.filter(offering=self.offering1).first()
        with self.assertRaises(ValueError):
            services.update_entry(
                sched, entry.pk, self.admin, day=IsoWeekday.WEDNESDAY)


class ViewAccessTest(_BaseTestCase):
    def _client(self, user):
        c = Client()
        c.force_login(user)
        return c

    def test_anonymous_redirects_to_login(self):
        res = Client().get(reverse("scheduling:calendar"))
        self.assertEqual(res.status_code, 302)
        self.assertIn("/accounts/login/", res.url)

    def test_student_calendar_and_timetable_ok(self):
        c = self._client(self.student)
        self.assertEqual(c.get(reverse("scheduling:calendar")).status_code, 200)
        self.assertEqual(
            c.get(reverse("scheduling:my_timetable")).status_code, 200)

    def test_admin_dashboard_and_calendar_ok(self):
        c = self._client(self.admin)
        self.assertEqual(c.get(reverse("scheduling:dashboard")).status_code, 200)
        self.assertEqual(c.get(reverse("scheduling:calendar")).status_code, 200)

    def test_student_blocked_from_admin_pages(self):
        c = self._client(self.student)
        for name in ("event_list", "room_list", "slot_list"):
            res = c.get(reverse(f"scheduling:{name}"))
            self.assertIn(res.status_code, (302, 403))

    def test_student_blocked_from_hod_pages(self):
        c = self._client(self.student)
        for name in ("hod_approvals", "hod_dashboard"):
            res = c.get(reverse(f"scheduling:{name}"))
            self.assertIn(res.status_code, (302, 403))

    def test_hod_opens_approval_pages(self):
        c = self._client(self.hod)
        self.assertEqual(
            c.get(reverse("scheduling:hod_approvals")).status_code, 200)
        self.assertEqual(
            c.get(reverse("scheduling:hod_dashboard")).status_code, 200)

    def test_non_hod_teacher_blocked_from_hod_pages(self):
        teacher = User.objects.create_user(
            "plain@test.com", "x", role="teacher", is_active=True)
        c = self._client(teacher)
        res = c.get(reverse("scheduling:hod_approvals"))
        self.assertIn(res.status_code, (302, 403))

    def test_event_fragment_scoped(self):
        ev = AcademicEvent.objects.create(
            institution=self.inst, title="Public",
            event_type=EventType.GENERAL, scope=EventScope.ALL,
            start_date=self.sem.start_date, is_public=True)
        secret = AcademicEvent.objects.create(
            institution=self.inst, title="Secret",
            event_type=EventType.GENERAL, scope=EventScope.DEPARTMENT,
            department=self.dept, start_date=self.sem.start_date,
            is_public=False)
        student = self._client(self.student)
        self.assertEqual(student.get(reverse(
            "scheduling:calendar_event_detail", args=[ev.pk])).status_code, 200)
        self.assertEqual(student.get(reverse(
            "scheduling:calendar_event_detail",
            args=[secret.pk])).status_code, 404)
        admin = self._client(self.admin)
        self.assertEqual(admin.get(reverse(
            "scheduling:calendar_event_detail",
            args=[secret.pk])).status_code, 200)