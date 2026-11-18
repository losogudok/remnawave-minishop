"""Wire models for sessionless native provider login."""

from typing import Annotated, Literal

from pydantic import ConfigDict, StringConstraints

from bot.app.web.http_contracts import HttpBodyModel, HttpResponseModel

NativeProvider = Literal["google", "telegram"]
ChallengeToken = Annotated[str, StringConstraints(strict=True, pattern=r"^[A-Za-z0-9_-]{43}$")]
IdToken = Annotated[
    str,
    StringConstraints(strict=True, min_length=1, max_length=12000, pattern=r"^\S+$"),
]


class NativeChallengeBody(HttpBodyModel):
    model_config = ConfigDict(extra="forbid")


class NativeTokenBody(NativeChallengeBody):
    request_id: ChallengeToken
    id_token: IdToken


class NativeChallengeOut(HttpResponseModel):
    ok: Literal[True]
    request_id: str
    nonce: str
    expires_in: int


class NativeSessionOut(HttpResponseModel):
    ok: Literal[True]
    token: str
    user_id: int
