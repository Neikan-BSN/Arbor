"""Typed configuration for tandem role orchestration."""

from __future__ import annotations

from typing import ClassVar

from pydantic import Field

from ..core.config_schema import (
    SHARED_FLAT,
    ContextConfig,
    LLMConfig,
    ProxyModel,
    RolesConfig,
    TimeoutConfig,
)


class TandemConfig(ProxyModel):
    """Per-run tandem configuration.

    The tandem path owns role bindings separately from coordinator/executor
    config so role flags never leak into the shared flat proxy surface.
    """

    PROXY: ClassVar[dict[str, tuple[str, str]]] = SHARED_FLAT

    llm: LLMConfig = Field(default_factory=LLMConfig)
    timeout: TimeoutConfig = Field(default_factory=TimeoutConfig)
    context: ContextConfig = Field(default_factory=ContextConfig)
    roles: RolesConfig = Field(default_factory=RolesConfig)

    cwd: str = "."
    task: str = ""
