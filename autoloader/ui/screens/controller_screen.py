"""Textual screen base with a typed reference to the application's facade."""
from __future__ import annotations

from typing import TYPE_CHECKING, cast

from textual.screen import Screen

if TYPE_CHECKING:
    from autoloader.app import AutoLoaderApp


class ControllerScreen(Screen[None]):
    """Base for screens that consume the shared application controller."""

    @property
    def controller_app(self) -> AutoLoaderApp:
        return cast("AutoLoaderApp", self.app)
