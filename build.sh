#!/usr/bin/env bash
set -o errexit
pip install -r requirements.txt && python manage.py migrate && python manage.py collectstatic --noinput && python manage.py createsruperuser --username $ADMIN_USERNAME --email $ADMIN_EMAIL --password $ADMIN_PASSWORD --noinput