"""ASGI config for eduPro.

Defaults to the production settings; DJANGO_SETTINGS_MODULE overrides it.
"""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "eduPro.settings.production")

application = get_asgi_application()
