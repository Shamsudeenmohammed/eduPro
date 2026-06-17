"""
academics/signals.py

Post-save signal: formerly auto-created a StudentProfile when a new
EduProUser with role='student' was created.  This is now **removed**
because:

1. It caused UNIQUE constraint failures on student_number (blank + unique).
2. It made is_approved_student return True before admission was approved.
3. StudentProfile is now created explicitly in AdmissionApplication.approve()
   and in academics/views.py profile editing, where a proper student_number
   can be assigned.

The ready() import in apps.py is kept for future signals.
"""
