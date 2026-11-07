"""Ordered, localized screenshot galleries owned by the built-in guide plugin."""

from sqlalchemy import JSON, Column, DateTime, Index, Integer, String
from sqlalchemy.sql import func

from db.base import Base


class GuideMediaImage(Base):
    __tablename__ = "ext_guide_media_images"
    __table_args__ = (
        Index(
            "ix_ext_guide_media_gallery",
            "config_id",
            "platform",
            "client_id",
            "sort_order",
        ),
    )

    image_id = Column(String(32), primary_key=True)
    digest = Column(String(64), nullable=False)
    config_id = Column(String(128), nullable=False)
    platform = Column(String(64), nullable=False)
    client_id = Column(String(128), nullable=False)
    captions = Column(JSON, nullable=False, default=dict)
    sort_order = Column(Integer, nullable=False, default=0)
    content_type = Column(String(32), nullable=False, default="image/webp")
    width = Column(Integer, nullable=True)
    height = Column(Integer, nullable=True)
    size_bytes = Column(Integer, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
