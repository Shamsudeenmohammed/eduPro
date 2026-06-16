# Deployment Guide

## Docker (Recommended)

```bash
docker-compose up -d --build
docker-compose exec web python manage.py migrate
docker-compose exec web python manage.py createsuperuser
docker-compose exec web python manage.py collectstatic --noinput
```

Access via http://localhost (nginx) or http://localhost:8000 (gunicorn direct).

## Manual Production

1. Set `DJANGO_SETTINGS_MODULE=eduPro.settings.production`
2. Configure PostgreSQL env vars: `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT`
3. Set `SECRET_KEY`, `ALLOWED_HOSTS`, `DEBUG=False`
4. Run `python manage.py collectstatic --noinput`
5. Run `python manage.py migrate`
6. Start Gunicorn: `gunicorn eduPro.wsgi:application --bind 0.0.0.0:8000`
7. Configure Nginx using `deploy/nginx.conf` as reference

## Environment Variables

| Variable | Description |
|----------|-------------|
| SECRET_KEY | Django secret key |
| DEBUG | True/False |
| ALLOWED_HOSTS | Comma-separated hosts |
| DB_NAME, DB_USER, DB_PASSWORD | PostgreSQL |
| REDIS_URL | Cache backend |
| EMAIL_* | SMTP settings |

## Security Checklist

- [ ] Change SECRET_KEY
- [ ] DEBUG=False
- [ ] HTTPS enabled (SECURE_SSL_REDIRECT)
- [ ] Strong database passwords
- [ ] Regular backups of DB and media/
