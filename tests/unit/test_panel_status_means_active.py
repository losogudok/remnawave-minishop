"""Panel status -> local ``is_active`` mapping.

``LIMITED`` means the monthly traffic quota is exhausted, not that the
subscription ended. Treating it as inactive used to strand paying users: the
row stopped satisfying ``get_active_subscription_by_user_id`` (so a traffic
top-up could not be credited to it) *and* dropped out of
``traffic_period_tick``'s ``is_active == True`` filter (so the monthly reset
that would have cleared the quota never ran for it). The state was therefore
permanent -- the user could neither wait it out nor pay to fix it.
"""

from pathlib import Path

from bot.services.panel_activity import (
    PANEL_STATUSES_MEANING_ACTIVE,
    panel_status_means_active,
)

BACKEND = Path(__file__).resolve().parents[2] / "backend"


def test_active_and_limited_are_the_only_active_statuses():
    assert sorted(PANEL_STATUSES_MEANING_ACTIVE) == ["ACTIVE", "LIMITED"]


def test_limited_still_counts_as_an_active_entitlement():
    assert panel_status_means_active("LIMITED") is True


def test_active_counts_as_an_active_entitlement():
    assert panel_status_means_active("ACTIVE") is True


def test_ended_and_blocked_statuses_do_not_count_as_active():
    for panel_status in ("EXPIRED", "DISABLED", "INACTIVE", "TRIAL_ENDED"):
        assert panel_status_means_active(panel_status) is False, panel_status


def test_missing_status_does_not_count_as_active():
    for panel_status in (None, "", "   "):
        assert panel_status_means_active(panel_status) is False, repr(panel_status)


def test_status_is_normalized_before_comparison():
    for panel_status in (" limited ", "Limited", "aCtIvE"):
        assert panel_status_means_active(panel_status) is True, repr(panel_status)


def test_panel_status_writers_use_the_shared_mapping():
    """Regression guard for the writers that strand LIMITED subscriptions.

    Both of these modules assign the local ``is_active`` flag from the panel
    status. A bare ``panel_status == "ACTIVE"`` there re-introduces the trap,
    so the comparison must go through ``panel_status_means_active``.
    """
    writers = (
        BACKEND / "bot" / "handlers" / "admin" / "sync_admin_runner.py",
        BACKEND / "bot" / "services" / "subscription_service_impl" / "lifecycle_details.py",
    )
    for writer in writers:
        source = writer.read_text(encoding="utf-8")
        assert 'panel_status == "ACTIVE"' not in source, writer.name
        assert "panel_status_means_active(" in source, writer.name
