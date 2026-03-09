"""Language plugin registry."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .base import LanguagePlugin
    from ..config import ToolkitConfig


def _build_registry() -> dict[str, type[LanguagePlugin]]:
    from .r import RPlugin
    from .scala import ScalaPlugin
    from .julia import JuliaPlugin

    return {
        "r": RPlugin,
        "scala": ScalaPlugin,
        "julia": JuliaPlugin,
    }


def get_enabled_plugins(config: ToolkitConfig) -> list[LanguagePlugin]:
    """Return instantiated plugins for all enabled languages."""
    registry = _build_registry()
    plugins: list[LanguagePlugin] = []

    lang_cfgs = {
        "r": config.r,
        "scala": config.scala,
        "julia": config.julia,
    }

    for lang_name, lang_cfg in lang_cfgs.items():
        if getattr(lang_cfg, "enabled", False) and lang_name in registry:
            plugins.append(registry[lang_name]())

    return plugins
