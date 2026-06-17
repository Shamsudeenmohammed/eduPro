# eduPro ERP

**A comprehensive Enterprise Resource Planning system for educational institutions.**

eduPro is a full-featured academic management platform that streamlines the complete student lifecycle — from admissions and enrollment through course registration, grading, and graduation — while providing powerful tools for teachers, administrators, and operations staff.

---

## Table of Contents

1. [System Overview](#system-overview)
2. [Architecture](#architecture)
3. [User Roles & Permissions](#user-roles--permissions)
4. [Admissions Management](#admissions-management)
5. [Student Portal](#student-portal)
6. [Teacher Portal](#teacher-portal)
7. [Admin Console](#admin-console)
8. [Operations & Finance](#operations--finance)
9. [Technical Stack](#technical-stack)
10. [Installation & Deployment](#installation--deployment)
11. [Security Features](#security-features)

---

## System Overview

eduPro ERP is designed for universities, colleges, and other educational institutions that need a unified digital platform to manage:

- **Prospective students** — Online applications, document collection, admission decisions
- **Current students** — Course registration, learning materials, assignments, quizzes, grades, transcripts, fees
- **Teachers** — Course delivery, assessment creation, grading, attendance tracking, result management
- **Administrators** — Academic structure management, user administration, analytics, audit logging
- **Operations** — Timetables, announcements, hostel management, support tickets
- **Finance** — Fee structures, student billing, payment tracking, payroll

The system is built on a modern web architecture and is accessible from any device with a browser. It supports multiple concurrent academic sessions and semesters, flexible grading systems, and role-based access control.

---

## Architecture

```
                    ┌────────────────────────────────────────┐
                    │            Web Browser                  │
                    └────────────────┬───────────────────────┘
                                     │
                    ┌────────────────┴───────────────────────┐
                    │           Nginx (reverse proxy)         │
                    │     Serves static/media, buffers uploads│
                    └────────────────┬───────────────────────┘
                                     │
                    ┌────────────────┴───────────────────────┐
                    │         Gunicorn (WSGI server)          │
                    │           3 worker processes             │
                    └────────────────┬───────────────────────┘
                                     │
                    ┌────────────────┴───────────────────────┐
                    │              Django 5.2                  │
                    │    ┌──────────────────────────────┐     │
                    │    │  Accounts  │  Academics      │     │
                    │    │  Students  │  Teachers       │     │
                    │    │  Portal    │  Operations     │     │
                    │    │  Finance   │  Feedback       │     │
                    │    │  Analytics │  E-learning     │     │
                    │    │  Messaging │  Core / API     │     │
                    │    └──────────────────────────────┘     │
                    └──────┬──────────────┬───────────────────┘
                           │              │
                    ┌──────┴──────┐ ┌────┴─────┐
                    │ PostgreSQL  │ │   Redis   │
                    │ (primary DB)│ │  (cache)  │
                    └─────────────┘ └──────────┘
```

### Application Modules

| Module | Purpose |
|--------|---------|
| **Portal** | Public-facing website, online admission applications, document management |
| **Accounts** | Authentication, user management, role-based access control |
| **Academics** | Programs, courses, semesters, enrollment, grading, transcripts |
| **Students** | Student dashboard, course registration, results, notifications |
| **Teachers** | Course delivery, assignments, quizzes, attendance, grade entry |
| **Operations** | Timetables, announcements, hostel allocation, support tickets |
| **Finance** | Fee structures, student fees, payments, payroll |
| **Feedback** | Student feedback collection with sentiment analysis |
| **Analytics** | Platform-wide analytics and reporting |
| **E-learning** | Learning management system, forums, live classes |
| **Messaging** | Internal user-to-user messaging |
| **Core** | Audit logging, PDF generation, system middleware |
| **API** | REST API for integration with external systems |

---

## User Roles & Permissions

eduPro uses a two-tier role system for flexible access control.

### Primary Roles

Every user is assigned one primary role:

| Role | Description |
|------|-------------|
| **Admin** | Full system access — user management, academic structure, admissions, finance, all modules |
| **Teacher** | Course delivery, assessment, grading — scoped to allocated courses |
| **Student** | Course access, assignments, results, fees — scoped to enrolled courses |

### Staff Responsibilities (Fine-grained)

Users with the Teacher or Admin role can be assigned additional responsibilities:

| Responsibility | Privileges |
|----------------|------------|
| **Teacher** | Default teaching access to allocated courses |
| **Head of Department (HOD)** | Department-level oversight, result sheet approval, teacher management |
| **Dean** | Faculty-level oversight |
| **Program Coordinator** | Program-level curriculum management |
| **Admissions Officer** | Application review, approval/rejection, document requests |
| **Examinations Officer** | Examination management and result oversight |
| **Counselor** | Student support and counseling access |

---

## Admissions Management

The admissions workflow handles the complete journey from application to enrollment.

### Applicant Journey

```
1. APPLY                 2. TRACK              3. RESPOND            4. ENROLL
   ┌─────────┐            ┌─────────┐            ┌─────────┐            ┌─────────┐
   │ Visit   │            │ Log in  │            │ Upload  │            │ Access  │
   │ portal  │───►        │ to check│───►        │requested│───►        │student  │
   │ /apply/ │            │ status  │            │  docs   │            │ portal  │
   └─────────┘            └─────────┘            └─────────┘            └─────────┘
        │                      │                      │                      │
   Submit application     View status in         Upload required         Full student
   Create account with    real-time —             documents via           dashboard with
   email + password       Pending → Reviewing     application             courses, grades,
                          → Approved/Rejected     dashboard               fees, etc.
```

### Key Features

- **Online Application Form** — Comprehensive form with program selection, personal info, academic background, document uploads, and personal statement
- **Real-time Status Tracking** — Applicants log in to view their application status at any time
- **Multi-stage Review Process** — Applications move through Pending → Reviewing → Approved/Rejected
- **Document Requests** — Admissions officers can request additional documents; applicants upload them directly
- **Admission Cycles** — Manage multiple intake periods with configurable dates and application limits
- **Reference Number System** — Each application gets a unique reference for tracking
- **Automated User Provisioning** — Approved applications automatically create active student accounts

### Admissions Officer Tools

- Central dashboard with application statistics
- Filterable application list (by status, cycle, program)
- Detailed application view with all submitted information and documents
- Review workflow with internal notes
- Approval with automatic student account creation
- Rejection with reason (visible to applicant)
- Document request creation and fulfillment tracking

---

## Student Portal

The student portal provides a complete digital experience for enrolled students.

### Dashboard
- Overview of current semester, enrolled courses, and key metrics
- Quick access to recent materials, assignments, and notifications
- Progress indicators and GPA snapshot

### Courses
- View all enrolled courses for the current semester
- Access course materials (lecture notes, videos, past questions)
- View and submit assignments
- Take computer-based tests (quizzes)
- View attendance records

### Academic Records
- **Results** — Semester-by-semester grade view with GPA calculations
- **Progress** — Academic progress tracking across all semesters
- **Transcript** — Download official transcript as PDF
- **Course Registration** — Self-service course registration with admin approval workflow

### Other Features
- **Notifications** — In-platform alerts for new materials, results, announcements
- **Profile Management** — Update personal information and change password
- **Fees** — View fee balances and payment history
- **Support Tickets** — Submit and track IT or administrative support requests
- **Feedback** — Submit course and institutional feedback

---

## Teacher Portal

The teacher portal empowers educators with tools for course delivery and assessment.

### Course Management
- View all allocated courses for the current semester
- Upload and organize lecture materials (documents, images, videos)
- Create and publish assignments with due dates
- Design computer-based tests with multiple question types

### Assessment Tools
- **Assignments** — Create, publish, close; view submissions and grade online
- **Quizzes/CBT** — Multiple question types (multiple choice, multi-select, true/false, short answer); auto-grading for objective questions; timer support
- **Attendance** — Take daily attendance, view attendance sheets and reports

### Grading
- Create result sheets for course offerings
- Enter continuous assessment (CA) and exam scores
- Submit result sheets for HOD approval
- View individual student performance analytics

### HOD Features
- Department-level result sheet review and approval
- Pending action dashboard

---

## Admin Console

The admin console provides complete system management capabilities.

### User Administration
- Create, activate, deactivate user accounts
- Assign staff roles and responsibilities
- Manage pending registration approvals
- Approve course registration requests

### Academic Structure Management
- Configure institutions, faculties, departments
- Define programs, courses, and course offerings
- Manage academic sessions and semesters
- Set up course allocations (teacher assignments)
- Manage enrollment records

### Admissions Administration
- Full application lifecycle management
- Admission cycle configuration
- Review and decision workflow
- Document request management

### System Features
- **Audit Logs** — Immutable audit trail of all admin actions (create, update, delete, approve, reject) with user, timestamp, IP, and change details
- **Analytics** — Platform-wide statistics (user counts, enrollment trends, grade distributions)
- **Finance Dashboard** — Fee structure management, student billing, payment tracking, payroll
- **Operations** — Announcement publishing, calendar management, hostel allocation, support ticket oversight

---

## Operations & Finance

### Operations Module

| Feature | Description |
|---------|-------------|
| **Announcements** | Create and publish institutional announcements (news) |
| **Calendar** | Academic calendar with important dates and events |
| **Timetable** | Class scheduling and timetable management |
| **Hostel** | Hostel and room management, student allocation |
| **Support Tickets** | IT and administrative support request system |

### Finance Module

| Feature | Description |
|---------|-------------|
| **Fee Structures** | Configure tuition and fee schedules by program and level |
| **Student Fees** | View and manage individual student fee accounts |
| **Payments** | Record and track fee payments |
| **Payroll** | Staff salary management and payment records |

---

## Technical Stack

### Backend
- **Framework:** Django 5.2+ (Python 3.12)
- **Database:** PostgreSQL 16 (production) / SQLite (development)
- **Cache:** Redis 7
- **API:** Django REST Framework

### Frontend
- **Server-rendered templates** with Django Template Language
- **Responsive design** — works on desktop, tablet, and mobile
- **Dark theme** admin interface with custom CSS
- **Google Fonts** — DM Serif Display + DM Sans
- **Chart.js** for analytics visualizations

### Infrastructure
- **Web Server:** Nginx (reverse proxy, static/media serving)
- **Application Server:** Gunicorn (WSGI)
- **Containerization:** Docker + Docker Compose
- **Static Files:** Whitenoise (compressed, manifest-based)

### Security
- **Rate Limiting** — IP-based, configurable (default: 120 requests/60s)
- **Audit Logging** — All admin mutations recorded immutably
- **CSRF Protection** — Enabled across all forms
- **Session Security** — HttpOnly cookies, configurable expiry
- **Content Security** — XSS filter, content-type sniffing protection
- **Referrer Policy** — same-origin

---

## Installation & Deployment

### Quick Start (Development)

```bash
# 1. Clone the repository
git clone <repository-url>
cd eduPro

# 2. Create environment file
cp .env.example .env
# Edit .env with your settings (SECRET_KEY, DEBUG=True)

# 3. Install dependencies
pip install -r requirements.txt

# 4. Run database migrations
python manage.py migrate

# 5. Create superuser
python manage.py createsuperuser

# 6. Start development server
python manage.py runserver
```

### Production Deployment (Docker)

```bash
# 1. Configure environment
cp .env.example .env
# Set DEBUG=False, configure DB/EMAIL/REDIS settings

# 2. Build and start services
docker-compose up -d --build

# 3. Run migrations
docker-compose exec web python manage.py migrate

# 4. Create superuser
docker-compose exec web python manage.py createsuperuser

# 5. Collect static files
docker-compose exec web python manage.py collectstatic --noinput
```

The application will be available at `http://localhost` (Nginx on port 80).

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `SECRET_KEY` | Yes | Django secret key (generate with `python -c 'from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())'`) |
| `DEBUG` | Yes | Set to `True` for development, `False` for production |
| `ALLOWED_HOSTS` | Yes | Comma-separated list of allowed hostnames |
| `DB_NAME` | Production | PostgreSQL database name |
| `DB_USER` | Production | PostgreSQL username |
| `DB_PASSWORD` | Production | PostgreSQL password |
| `DB_HOST` | Production | PostgreSQL host |
| `DB_PORT` | Production | PostgreSQL port (default: 5432) |
| `EMAIL_HOST` | Optional | SMTP server hostname |
| `EMAIL_PORT` | Optional | SMTP port |
| `EMAIL_HOST_USER` | Optional | SMTP username |
| `EMAIL_HOST_PASSWORD` | Optional | SMTP password |
| `EMAIL_USE_TLS` | Optional | Enable TLS (default: True) |
| `REDIS_URL` | Production | Redis connection string |
| `TIME_ZONE` | Optional | Timezone (default: UTC) |
| `EDUPRO_SITE_NAME` | Optional | Institution name for branding |

---

## Security Features

### Authentication Security
- Passwords hashed with Django's default PBKDF2 algorithm
- Rate limiting on login attempts (configurable)
- Session management with optional "Remember Me" (14-day expiry) or browser-session only
- Account lockout for inactive accounts

### Audit Trail
Every administrative action is recorded in an immutable audit log:
- Who performed the action
- What action was taken (create, update, delete, approve, reject, login, logout)
- When it happened
- What object was affected
- The IP address and user agent
- The request path
- Any changed values (as JSON)

### Access Control
- Role-based access at the view level (decorators)
- Fine-grained staff responsibilities for shared-role scenarios
- Department-scoped permissions (e.g., HOD can only approve results for their department)
- All API endpoints require authentication

### Data Protection
- CSRF protection on all forms
- XSS filtering enabled
- Content-type sniffing disabled
- Secure referrer policy
- File upload validation by extension
- Configurable file upload size limits

---

## License

eduPro ERP is proprietary software. All rights reserved.

---

*Documentation version 1.0 — June 2026*
