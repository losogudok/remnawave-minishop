"""Typed HTTP contracts for the built-in guide media plugin."""

from __future__ import annotations

from datetime import datetime

from pydantic import ConfigDict, Field

from bot.app.web.http_contracts import HttpBodyModel, HttpResponseModel


class GuideMediaImageOut(HttpResponseModel):
    image_id: str
    digest: str
    config_id: str
    platform: str
    client_id: str
    captions: dict[str, str] = Field(default_factory=dict)
    order: int = 0
    content_type: str = "image/webp"
    width: int | None = None
    height: int | None = None
    size_bytes: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None
    src: str


class GuideMediaGalleryOut(HttpResponseModel):
    config_id: str
    platform: str
    client_id: str
    orphaned: bool = False
    images: list[GuideMediaImageOut] = Field(default_factory=list)


class GuideMediaGalleryKeyOut(HttpResponseModel):
    config_id: str
    platform: str
    client_id: str


class GuideMediaCatalogAppOut(HttpResponseModel):
    id: str
    name: str
    explicit_id: bool = False
    ambiguous: bool = False


class GuideMediaCatalogPlatformOut(HttpResponseModel):
    id: str
    label: str
    apps: list[GuideMediaCatalogAppOut] = Field(default_factory=list)


class GuideMediaCatalogConfigOut(HttpResponseModel):
    config_id: str
    source: str
    platforms: list[GuideMediaCatalogPlatformOut] = Field(default_factory=list)
    error: str | None = None
    missing: bool = False


class AdminGuideMediaGalleriesOut(HttpResponseModel):
    catalog: list[GuideMediaCatalogConfigOut] = Field(default_factory=list)
    catalog_available: bool = False
    catalog_error: str | None = None
    orphaned_known: bool = False
    galleries: list[GuideMediaGalleryOut] = Field(default_factory=list)
    orphaned: list[GuideMediaGalleryKeyOut] = Field(default_factory=list)


class AdminGuideMediaUploadBody(HttpBodyModel):
    model_config = ConfigDict(extra="forbid")

    config_id: str = Field(min_length=1, max_length=128)
    platform: str = Field(min_length=1, max_length=64)
    client_id: str = Field(min_length=1, max_length=128)
    captions: dict[str, str] = Field(default_factory=dict)
    order: int | None = Field(default=None, ge=-10000, le=10000)


class AdminGuideMediaUpdateBody(HttpBodyModel):
    model_config = ConfigDict(extra="forbid")

    captions: dict[str, str] | None = None
    order: int | None = Field(default=None, ge=-10000, le=10000)


class AdminGuideMediaReorderBody(HttpBodyModel):
    model_config = ConfigDict(extra="forbid")

    config_id: str = Field(min_length=1, max_length=128)
    platform: str = Field(min_length=1, max_length=64)
    client_id: str = Field(min_length=1, max_length=128)
    image_ids: list[str] = Field(max_length=8)
