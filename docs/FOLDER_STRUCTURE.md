# Folder Structure

```
eduPro/
├── manage.py
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── .env                    # Environment variables (do not commit secrets)
├── db.sqlite3              # Dev database
├── docs/                   # Documentation
├── deploy/                 # Nginx config
├── sample_data/            # Sample CSV for import
├── static/                 # Source static files (CSS, JS)
├── staticfiles/            # Collected static (production)
├── media/                  # User uploads
├── templates/              # Global templates
├── eduPro/                 # Project config
│   ├── settings/
│   │   ├── base.py
│   │   ├── development.py
│   │   └── production.py
│   ├── urls.py
│   └── wsgi.py
├── accounts/
├── academics/
├── teachers/
├── students/
├── portal/
├── operations/
├── finance/
├── feedback/
├── analytics/
├── elearning/
├── messaging/
├── api/
└── core/
```

Each app typically contains: `models.py`, `views.py`, `urls.py`, `forms.py`, `admin.py`, `migrations/`, `templates/<app>/`.
