# API Documentation

Base URL: `/api/v1/`

## Authentication

- **Session:** Log in via browser, use session cookie
- **Token:** `Authorization: Token <your-token>` (create via Django admin → Authtoken)

## Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/` | API root / discovery |
| GET | `/api/v1/users/` | List users (authenticated) |
| GET | `/api/v1/departments/` | Departments |
| GET | `/api/v1/courses/` | Course catalogue |
| GET | `/api/v1/offerings/` | Course offerings |
| GET | `/api/v1/students/` | Student academic profiles |
| GET | `/api/v1/announcements/` | Announcements |
| GET | `/api/v1/assignments/` | Assignments |
| GET | `/api/v1/results/` | Student results |
| GET/POST | `/api/v1/feedback/` | Feedback (POST auto-classifies sentiment) |
| GET | `/api/v1/stats/` | Platform stats (admin only) |

## Pagination

Default page size: 25. Use `?page=2` for subsequent pages.

## Filtering

Use query params supported by django-filter, e.g. `?role=student`, `?search=john`.
