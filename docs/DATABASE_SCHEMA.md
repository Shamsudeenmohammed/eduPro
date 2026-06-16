# Database Schema

## Core Hierarchy

```
Institution
  └── Faculty
        └── Department
              └── Program
                    └── Level
                          └── Course (catalogue)
                                └── CourseOffering (× Semester × Level)
                                      ├── CourseAllocation (Teacher)
                                      ├── Enrolment (Student)
                                      ├── Assignment / Quiz / Attendance
                                      └── ResultSheet → StudentResult
```

## Key Models by App

### accounts
- `EduProUser` — email login, role (admin/teacher/student)
- `UserProfile` — avatar, phone, bio

### academics
- `AcademicSession`, `Semester`, `StudentProfile`, `Enrolment`

### teachers
- `TeacherProfile`, `LectureMaterial`, `Assignment`, `Quiz`, `AttendanceSheet`, `ResultSheet`, `StudentResult`

### students
- `StudentNotification`, `CourseRegistrationRequest`, `MaterialDownloadLog`

### operations
- `TimetableSlot`, `Announcement`, `CalendarEvent`, `Hostel`, `HostelAllocation`, `SupportTicket`

### finance
- `FeeStructure`, `StudentFee`, `FeePayment`, `PayrollRecord`

### feedback
- `Feedback` — with `sentiment`, `sentiment_score`, `keywords`

### elearning
- `LMSModule`, `LearningResource`, `Forum`, `ForumPost`, `LiveClassSession`

### messaging
- `Conversation`, `Message`

### core
- `AuditLog`

## GPA

- **Grade points:** Defined in `teachers.models.GRADE_POINTS`
- **CGPA:** `sum(grade_point × credits) / sum(credits)` via `core.utils.calculate_cgpa`
