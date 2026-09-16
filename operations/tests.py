"""
operations/tests.py — Moved.

All hostel functionality (models, services, forms, views, admin, templates
and the full test suite) now lives in the dedicated `hostel` application.
The 64 tests previously defined here were ported to
`hostel/tests/test_hostel.py` and now run against the `hostel` models.
This file is intentionally empty of tests so Django's test runner finds
nothing here.
"""