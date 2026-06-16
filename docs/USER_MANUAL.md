# User Manual

## Admin

- **Dashboard:** `/accounts/dashboard/admin/` — KPIs, registrations, activity feed
- **Academics:** Manage institution, faculties, departments, programs, sessions, courses, allocations, enrolments
- **Analytics:** `/analytics/` — platform stats, at-risk students, grade charts
- **Finance:** `/finance/` — fee structures, student fees, payroll
- **Operations:** Announcements, calendar, timetable, support tickets, hostel
- **Feedback:** Review and respond to feedback with sentiment tags
- **Audit logs:** `/core/audit-logs/`
- **Bulk import:** `python manage.py import_students file.csv`

## Teacher

- **Portal:** `/teachers/` — courses, materials, assignments, quizzes, attendance, results
- **LMS:** `/elearning/course/<id>/` — modules and live class links
- **Messages:** `/messages/`

## Student

- **Portal:** `/students/` — courses, assignments, quizzes, results, progress
- **Register courses:** `/students/register/`
- **Transcript PDF:** `/students/transcript/pdf/`
- **Fees:** `/students/fees/`
- **Insights:** `/students/insights/`
- **Support tickets:** `/operations/tickets/`

## Public

- **Home:** `/portal/`
- **Apply:** `/portal/admission/`
- **Contact:** `/portal/contact/`

## Dark / Light Mode

Click the theme toggle in the navigation bar. Preference is saved in browser localStorage.
