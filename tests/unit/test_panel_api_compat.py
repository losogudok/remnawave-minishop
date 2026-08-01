from bot.services.panel_api_compat import (
    PanelApiCompatibility,
    PanelUserIdMode,
    normalize_panel_user,
    numeric_panel_user_id,
)


def test_metadata_selects_user_identity_contract() -> None:
    legacy = PanelApiCompatibility.from_metadata({"response": {"version": "2.8.1"}})
    current = PanelApiCompatibility.from_metadata({"response": {"version": "v3.0.0"}})

    assert legacy.user_id_mode is PanelUserIdMode.UUID
    assert legacy.version == "2.8.1"
    assert current.user_id_mode is PanelUserIdMode.NUMERIC_ID
    assert current.version == "v3.0.0"


def test_unknown_metadata_does_not_guess_a_generation() -> None:
    compatibility = PanelApiCompatibility.from_metadata({"response": {"version": "development"}})

    assert compatibility.user_id_mode is PanelUserIdMode.UNKNOWN
    assert compatibility.version is None


def test_v3_user_gets_a_legacy_uuid_alias_without_losing_id() -> None:
    source = {"id": 42, "username": "tg_42"}

    normalized = normalize_panel_user(source)

    assert normalized == {"id": 42, "uuid": "42", "username": "tg_42"}
    assert source == {"id": 42, "username": "tg_42"}


def test_v2_uuid_wins_when_response_also_contains_internal_id() -> None:
    normalized = normalize_panel_user({"id": 42, "uuid": "panel-uuid"})

    assert normalized == {"id": 42, "uuid": "panel-uuid"}


def test_numeric_identifier_rejects_ambiguous_values() -> None:
    assert numeric_panel_user_id("42") == 42
    assert numeric_panel_user_id(42) == 42
    assert numeric_panel_user_id(True) is None
    assert numeric_panel_user_id("0") is None
    assert numeric_panel_user_id("panel-uuid") is None
