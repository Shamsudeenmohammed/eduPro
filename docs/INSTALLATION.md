# Installation Guide

## Requirements

- Python 3.10+
- pip
- Optional: PostgreSQL 14+, Redis 7+ (production)

## Steps

1. Clone or extract the project to your machine.
2. Create a virtual environment and activate it.
3. Install dependencies: `pip install -r requirements.txt`
4. Copy `.env` and set `SECRET_KEY`, `DEBUG=True` for local dev.
5. Run migrations: `python manage.py migrate`
6. Load sample data: `python manage.py load_sample_data`
7. Create admin user: `python manage.py createsuperuser`
8. Run server: `python manage.py runserver`

## Bulk Student Import

```bash
python manage.py import_students sample_data/students_import_sample.csv
```

## Export Students

```bash
python manage.py export_students -o students_export.csv
```

## TextBlob (Sentiment)

After install, download corpora once:

```bash
python -m textblob.download_corpora
```
