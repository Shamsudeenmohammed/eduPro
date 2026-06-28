"""
accounts/models.py

Custom User model and Profile extension for eduPro ERP.

Key changes from v1:
- EduProUser.is_active defaults to False → no immediate dashboard access.
- Single `role` field kept for primary classification (ADMIN/TEACHER/STUDENT),
  but academic authority is now carried by StaffRole (M2M via UserStaffRole).
- UserStaffRole records grant fine-grained responsibilities:
    TEACHER, HOD, DEAN, COORDINATOR, ADMISSIONS_OFFICER, …
- A user can hold multiple simultaneous StaffRoles.
- HOD authority is scoped to a specific Department via UserStaffRole.department.
- `is_active = False` by default; admin/admissions must explicitly activate.
"""

from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver



# ─────────────────────────────────────────────────────────────────────────────
# CHOICES
# ─────────────────────────────────────────────────────────────────────────────

class Role(models.TextChoices):
    ADMIN   = "admin",   _("Admin")
    TEACHER = "teacher", _("Teacher")
    STUDENT = "student", _("Student")


class StaffResponsibility(models.TextChoices):
    """
    Fine-grained academic responsibilities that can be stacked on one user.
    Stored in UserStaffRole (one row per responsibility, optionally scoped
    to a Department).
    """
    TEACHER              = "teacher",              _("Teacher")
    HOD                  = "hod",                  _("Head of Department")
    DEAN                 = "dean",                 _("Dean / Faculty Head")
    PROGRAM_COORDINATOR  = "program_coordinator",  _("Program Coordinator")
    ADMISSIONS_OFFICER   = "admissions_officer",   _("Admissions Officer")
    EXAMINATIONS_OFFICER = "examinations_officer", _("Examinations Officer")
    COUNSELOR            = "counselor",            _("Student Counselor")


# ─────────────────────────────────────────────────────────────────────────────
# USER MANAGER
# ─────────────────────────────────────────────────────────────────────────────

class EduProUserManager(BaseUserManager):
    """Custom manager for EduProUser."""

    def _create_user(self, email, password, **extra_fields):
        if not email:
            raise ValueError(_("The Email field must be set."))
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra_fields):
        """
        Public-facing user creation.  is_active=False by default so the
        account must be approved before the user can log in.
        """
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        extra_fields.setdefault("role", Role.STUDENT)
        extra_fields.setdefault("is_active", False)   # ← CHANGED: inactive until approved
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("role", Role.ADMIN)
        extra_fields.setdefault("is_active", True)    # superusers always active

        if extra_fields.get("is_staff") is not True:
            raise ValueError(_("Superuser must have is_staff=True."))
        if extra_fields.get("is_superuser") is not True:
            raise ValueError(_("Superuser must have is_superuser=True."))

        return self._create_user(email, password, **extra_fields)

    def create_approved_user(self, email, password=None, **extra_fields):
        """
        Used internally when admissions approves an application and
        creates the linked EduProUser account.  Always active immediately.
        """
        extra_fields["is_active"] = True
        return self.create_user(email, password, **extra_fields)


# ─────────────────────────────────────────────────────────────────────────────
# USER MODEL
# ─────────────────────────────────────────────────────────────────────────────

class EduProUser(AbstractBaseUser, PermissionsMixin):
    """
    Custom User model using email as the unique identifier.

    Primary role field classifies the user broadly (admin/teacher/student).
    Fine-grained academic responsibilities live in UserStaffRole (below).

    Activation flow:
        Student applicant  → portal.AdmissionApplication (PENDING)
            → Admin approves → create_approved_user() → is_active=True
        Staff/Teacher      → admin creates directly with is_active=True
        Direct signup      → is_active=False, pending admin approval
    """

    email = models.EmailField(_("email address"), unique=True)
    first_name = models.CharField(_("first name"), max_length=150, blank=False)
    last_name  = models.CharField(_("last name"),  max_length=150, blank=False)

    role = models.CharField(
        _("primary role"),
        max_length=20,
        choices=Role.choices,
        default=Role.STUDENT,
    )

    # ── Activation ───────────────────────────────────────────────────────────
    is_active = models.BooleanField(
        _("active"),
        default=False,   # ← CHANGED from True
        help_text=_(
            "Designates whether this user can log in. "
            "Unset after signup; set by admin after approval."
        ),
    )
    is_staff = models.BooleanField(
        _("staff status"),
        default=False,
        help_text=_("Designates whether the user can log into the admin site."),
    )
    date_joined = models.DateTimeField(_("date joined"), default=timezone.now)

    # ── Approval audit ───────────────────────────────────────────────────────
    approved_by = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="approved_users",
        verbose_name=_("approved by"),
    )
    approved_at = models.DateTimeField(_("approved at"), null=True, blank=True)

    objects = EduProUserManager()

    USERNAME_FIELD  = "email"
    REQUIRED_FIELDS = ["first_name", "last_name"]

    class Meta:
        verbose_name        = _("user")
        verbose_name_plural = _("users")
        ordering = ["last_name", "first_name"]

    def __str__(self):
        return f"{self.get_full_name()} <{self.email}>"

    def get_full_name(self):
        return f"{self.first_name} {self.last_name}".strip()

    def get_short_name(self):
        return self.first_name

    # ── Primary-role helpers (backward-compat) ────────────────────────────

    @property
    def is_admin(self):
        return self.role == Role.ADMIN or self.is_superuser

    @property
    def is_teacher(self):
        return self.role == Role.TEACHER

    @property
    def is_student(self):
        return self.role == Role.STUDENT

    @property
    def is_approved_student(self):
        """True when the student has a StudentProfile (i.e. admission approved)."""
        if self.role != Role.STUDENT:
            return False
        from academics.models import StudentProfile
        return StudentProfile.objects.filter(student=self).exists()

    @property
    def is_hod(self):
        from academics.models import Department
        if Department.objects.filter(hod=self).exists():
            return True
        return self.staff_roles.filter(
            responsibility=StaffResponsibility.HOD, is_active=True
        ).exists()

    # ── Multi-responsibility helpers ──────────────────────────────────────

    @property
    def institutional_email(self):
        """Auto-generated institutional email for students."""
        if self.role != Role.STUDENT:
            return None
        domain = getattr(settings, 'INSTITUTION_EMAIL_DOMAIN', 'schoolname.com')
        first_initial = self.first_name[0].lower() if self.first_name else ''
        last_name = self.last_name.lower().replace(' ', '') if self.last_name else ''
        return f"{first_initial}{last_name}.stu@{domain}"

    def set_default_password(self):
        """Set the default password for new students."""
        self.set_password("0123456789")

    def has_responsibility(self, responsibility: str) -> bool:
        """True if the user holds the given StaffResponsibility."""
        return self.staff_roles.filter(
            responsibility=responsibility, is_active=True
        ).exists()

    def is_hod_of(self, department) -> bool:
        """True if user is HOD of the given Department instance."""
        from academics.models import Department
        if Department.objects.filter(pk=department.pk, hod=self).exists():
            return True
        return self.staff_roles.filter(
            responsibility=StaffResponsibility.HOD,
            department=department,
            is_active=True,
        ).exists()

    def get_hod_departments(self):
        """QuerySet of Departments where this user is HOD."""
        from academics.models import Department
        role_dept_ids = self.staff_roles.filter(
            responsibility=StaffResponsibility.HOD,
            is_active=True,
        ).values_list("department_id", flat=True)
        return Department.objects.filter(
            models.Q(pk__in=role_dept_ids) | models.Q(hod=self)
        ).distinct()

    def get_active_responsibilities(self):
        """List of responsibility strings this user holds."""
        return list(
            self.staff_roles.filter(is_active=True)
            .values_list("responsibility", flat=True)
            .distinct()
        )

    def get_dashboard_url(self):
        """Return the correct dashboard URL for this user."""
        from django.urls import reverse
        if self.is_admin:
            return reverse("accounts:dashboard")
        if self.is_teacher:
            return reverse("accounts:teacher_dashboard")
        if self.is_approved_student:
            return reverse("accounts:student_dashboard")
        return reverse("accounts:student_pending")


# ─────────────────────────────────────────────────────────────────────────────
# STAFF ROLE (multi-responsibility bridge)
# ─────────────────────────────────────────────────────────────────────────────

class UserStaffRole(models.Model):
    """
    Grants a specific academic responsibility to a user, optionally scoped
    to a Department.

    Examples:
        user=Alice, responsibility=TEACHER,  department=None     → generic teacher
        user=Alice, responsibility=HOD,      department=CS dept  → HOD of CS
        user=Bob,   responsibility=TEACHER,  department=None
        user=Bob,   responsibility=HOD,      department=EE dept  → Teacher + HOD
        user=Carol, responsibility=ADMISSIONS_OFFICER, department=None
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="staff_roles",
        verbose_name=_("user"),
    )
    responsibility = models.CharField(
        _("responsibility"),
        max_length=30,
        choices=StaffResponsibility.choices,
    )
    department = models.ForeignKey(
        "academics.Department",
        on_delete=models.CASCADE,
        null=True, blank=True,
        related_name="staff_role_assignments",
        verbose_name=_("scoped department"),
        help_text=_("Required for HOD; optional for others to scope authority."),
    )
    granted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="roles_granted",
        verbose_name=_("granted by"),
    )
    granted_at = models.DateTimeField(_("granted at"), default=timezone.now)
    is_active  = models.BooleanField(_("active"), default=True)

    class Meta:
        verbose_name        = _("staff role")
        verbose_name_plural = _("staff roles")
        ordering = ["user__last_name", "responsibility"]
        unique_together = [("user", "responsibility", "department")]

    def __str__(self):
        dept_suffix = f" [{self.department.code}]" if self.department else ""
        return (
            f"{self.user.get_full_name()} — "
            f"{self.get_responsibility_display()}{dept_suffix}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# USER PROFILE
# ─────────────────────────────────────────────────────────────────────────────

class UserProfile(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="profile",
        verbose_name=_("user"),
    )
    bio           = models.TextField(_("bio"), blank=True, max_length=500)
    phone         = models.CharField(_("phone number"), max_length=30, blank=True)
    avatar        = models.ImageField(
        _("avatar"), upload_to="avatars/%Y/%m/", blank=True, null=True,
    )
    department    = models.CharField(_("department / class"), max_length=100, blank=True)
    date_of_birth = models.DateField(_("date of birth"), null=True, blank=True)
    updated_at    = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name        = _("user profile")
        verbose_name_plural = _("user profiles")
        constraints = [
            models.UniqueConstraint(fields=["user"], name="unique_user_profile")
        ]

    def __str__(self):
        if self.user:
            return f"Profile of {self.user.get_full_name() or self.user.email}"
        return "Unassigned Profile"

    def get_avatar_url(self):
        if self.avatar and hasattr(self.avatar, "url"):
            return self.avatar.url
        return "/static/accounts/img/default_avatar.svg"


# ─────────────────────────────────────────────────────────────────────────────
# SIGNALS
# ─────────────────────────────────────────────────────────────────────────────

@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def create_user_profile(sender, instance, created, **kwargs):
    """Automatically builds a UserProfile row whenever a new EduProUser is created."""
    if created:
        UserProfile.objects.get_or_create(user=instance)


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def save_user_profile(sender, instance, **kwargs):
    """Saves the profile whenever the user object updates."""
    if hasattr(instance, "profile"):
        instance.profile.save()
