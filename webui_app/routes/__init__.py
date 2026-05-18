"""Blueprint registration entry — Plan 2026-05-18-001 Unit 3."""

from __future__ import annotations

from flask import Flask


def register_blueprints(app: Flask) -> None:
    from .main import bp as main_bp
    from .pipeline import bp as pipeline_bp
    from .batch import bp as batch_bp
    from .checkpoint import bp as checkpoint_bp
    from .history import bp as history_bp
    from .drafts import bp as drafts_bp
    from .settings_basic import bp as settings_basic_bp
    from .oauth import bp as oauth_bp
    from .profiles import bp as profiles_bp
    from .sites import bp as sites_bp

    for bp in (main_bp, pipeline_bp, batch_bp, checkpoint_bp,
               history_bp, drafts_bp, settings_basic_bp, oauth_bp,
               profiles_bp, sites_bp):
        app.register_blueprint(bp)
