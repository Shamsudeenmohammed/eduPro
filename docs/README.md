# eduPro Enterprise Academic Platform

## System Overview

eduPro is a full-stack Django school/university management system with role-based access (Admin, Teacher, Student), academic structure management, e-learning, finance, analytics, and a public-facing portal.

## Architecture

```
eduPro/
├── accounts/      # Auth, profiles, user management
├── academics/     # Institution, programs, courses, enrolments
├── teachers/      # Materials, assignments, quizzes, attendance, results
├── students/      # Student portal, registration, notifications
├── portal/        # Public website, contact, admissions
├── operations/    # Timetable, announcements, calendar, hostel, tickets
├── finance/       # Fees and payroll
├── feedback/      # Feedback with sentiment analysis
├── analytics/     # Dashboards, predictive risk, recommendations
├── elearning/     # LMS modules, forums, live classes
├── messaging/     # Internal messaging
├── api/           # REST API (DRF)
└── core/          # Audit logs, PDF utilities, middleware
```

## Quick Start

```bash
python -m venv venv
venv\Scripts\activate        # Windows
pip install -r requirements.txt
copy .env.example .env       # or use existing .env
python manage.py migrate
python manage.py load_sample_data
python manage.py createsuperuser
python manage.py runserver
```

Visit: http://127.0.0.1:8000/

## Default URLs

| Area | URL |
|------|-----|
| Public home | `/portal/` |
| Login | `/accounts/login/` |
| Admin dashboard | `/accounts/dashboard/admin/` |
| Student portal | `/students/` |
| Teacher portal | `/teachers/` |
| Analytics | `/analytics/` |
| API root | `/api/` |

## Database

- **Development:** SQLite (`db.sqlite3`)
- **Production:** PostgreSQL (see `eduPro/settings/production.py`)

## Documentation Index

- [Installation Guide](INSTALLATION.md)
- [Deployment Guide](DEPLOYMENT.md)
- [User Manual](USER_MANUAL.md)
- [API Documentation](API.md)
- [Database Schema](DATABASE_SCHEMA.md)
- [Folder Structure](FOLDER_STRUCTURE.md)

## License

Proprietary — eduPro Enterprise Edition
