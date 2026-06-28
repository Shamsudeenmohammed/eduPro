from django.contrib.auth.backends import ModelBackend
from django.contrib.auth import get_user_model


class EduProAuthBackend(ModelBackend):
    """
    Custom authentication backend for the *dashboard* login.

    Authentication is attempted in this order:
      1. Student ID  (student_number from StudentProfile)
      2. Staff ID    (staff_id from TeacherProfile)
      3. Email       — only for users WITHOUT a StudentProfile

    Once a StudentProfile exists (admission approved), email-based login
    is blocked to enforce separate credentials for the student dashboard.
    """

    def authenticate(self, request, username=None, password=None, **kwargs):
        UserModel = get_user_model()
        if username is None:
            username = kwargs.get(UserModel.USERNAME_FIELD)
        if username is None or password is None:
            return None

        user = self._authenticate_by_student_id(username, password)
        if user:
            return user

        user = self._authenticate_by_staff_id(username, password)
        if user:
            return user

        try:
            user = UserModel.objects.get(email=username)
        except UserModel.DoesNotExist:
            return None

        if not user.check_password(password) or not self.user_can_authenticate(user):
            return None

        # Block email login for users who already have a StudentProfile
        # (they must use their Student ID to access the dashboard).
        if user.role == "student":
            from academics.models import StudentProfile
            exists = StudentProfile.all_objects.filter(student=user).exists()
            if exists:
                return None

        return user

    def _authenticate_by_student_id(self, student_id, password):
        from academics.models import StudentProfile
        try:
            profile = StudentProfile.objects.get(student_number=student_id)
            user = profile.student
            if user.check_password(password) and self.user_can_authenticate(user):
                return user
        except StudentProfile.DoesNotExist:
            pass
        return None

    def _authenticate_by_staff_id(self, staff_id, password):
        from teachers.models import TeacherProfile
        try:
            profile = TeacherProfile.objects.get(staff_id=staff_id)
            user = profile.teacher
            if user.check_password(password) and self.user_can_authenticate(user):
                return user
        except TeacherProfile.DoesNotExist:
            pass
        return None
