"""Hostel policy service — central access to the configurable policy layer."""

from datetime import timedelta

from django.utils import timezone

from hostel.models import HostelPolicy


class HostelPolicyService:
    DEFAULT_EXPIRY_HOURS = 48

    @staticmethod
    def get_policy():
        return HostelPolicy.get_active()

    @classmethod
    def reservation_expiry(cls):
        policy = cls.get_policy()
        hours = policy.reservation_expiry_hours if policy else cls.DEFAULT_EXPIRY_HOURS
        return timedelta(hours=hours)

    @classmethod
    def _window_open(cls, start, end):
        policy = cls.get_policy()
        today = timezone.localdate()
        if policy:
            start = start or getattr(policy, "application_open", None)
            end = end or getattr(policy, "application_close", None)
        if start and today < start:
            return False
        if end and today > end:
            return False
        return True

    @classmethod
    def application_window_open(cls):
        policy = cls.get_policy()
        start = policy.application_open if policy else None
        end = policy.application_close if policy else None
        return cls._window_open(start, end)

    @classmethod
    def allocation_window_open(cls):
        policy = cls.get_policy()
        start = policy.allocation_open if policy else None
        end = policy.allocation_close if policy else None
        return cls._window_open(start, end)