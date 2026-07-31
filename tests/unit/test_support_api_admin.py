import pytest
from pydantic import ValidationError

from bot.app.web.admin_api_impl.support import AdminTicketPatchPayload, AdminTicketReplyPayload


def test_admin_patch_payload_accepts_closed_status_and_urgent_priority():
    payload = AdminTicketPatchPayload.model_validate(
        {"status": "closed", "priority": "urgent", "category": "billing"}
    )

    assert payload.status == "closed"
    assert payload.priority == "urgent"


def test_admin_reply_payload_supports_internal_note():
    payload = AdminTicketReplyPayload.model_validate({"body": " note ", "is_internal_note": True})

    assert payload.body == "note"
    assert payload.is_internal_note is True
    assert payload.body_format == "text"
    assert payload.buttons == []


def test_admin_reply_payload_accepts_markup_and_buttons():
    payload = AdminTicketReplyPayload.model_validate(
        {
            "body": "<b>hi</b>",
            "body_format": "html",
            "buttons": [{"kind": "promo_webapp", "label": "Take it", "promo_code": "SAVE"}],
        }
    )

    assert payload.body_format == "html"
    assert payload.buttons[0].promo_code == "SAVE"


def test_admin_reply_payload_rejects_an_unknown_body_format():
    with pytest.raises(ValidationError):
        AdminTicketReplyPayload.model_validate({"body": "hi", "body_format": "markdown"})


def test_admin_reply_payload_caps_the_number_of_buttons():
    with pytest.raises(ValidationError):
        AdminTicketReplyPayload.model_validate(
            {
                "body": "hi",
                "buttons": [
                    {"kind": "url", "label": str(index), "url": "https://x.dev"}
                    for index in range(5)
                ],
            }
        )
