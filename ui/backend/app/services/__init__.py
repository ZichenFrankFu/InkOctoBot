"""Domain services shared by routers.

The router files in ``ui/backend/app/routers/`` should be thin HTTP
adapters; anything that's reusable across routers, called from CLI, or
needs to be testable without spinning up FastAPI lives here.
"""
from .project_paths import get_db_path
from .style_preferences import load_user_style_preferences
from .model_router_factory import (
    SimpleRouter,
    build_router,
    get_user_settings,
    make_provider_instance,
)
from . import usage_tracker

__all__ = [
    "get_db_path",
    "load_user_style_preferences",
    "SimpleRouter",
    "build_router",
    "get_user_settings",
    "make_provider_instance",
    "usage_tracker",
]
