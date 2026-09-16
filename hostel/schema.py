"""Cheap schema-feature detection.

The hostel module CANNOT ship a migration in some environments, and the live
database may temporarily lag the models. These helpers let views degrade
gracefully (hide amenity/fee-scope sections) instead of 500ing until the
reported SQL is applied.

Results are cached per process.
"""

from django.db import connection

_cache = {}


def _columns(table):
    if table not in _cache:
        try:
            with connection.cursor() as cursor:
                names = {
                    col.name
                    for col in connection.introspection.get_table_description(cursor, table)
                }
        except Exception:
            names = set()
        _cache[table] = names
    return _cache[table]


def table_exists(table):
    return bool(_columns(table))


def column_exists(table, column):
    return column in _columns(table)