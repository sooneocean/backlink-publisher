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
    from .llm import bp as llm_bp
    from .oauth import bp as oauth_bp
    from .profiles import bp as profiles_bp
    from .sites import bp as sites_bp
    from .queue import bp as queue_bp
    from .dashboard import bp as dashboard_bp
    from .medium_login import bp as medium_login_bp
    from .bind import bp as bind_bp
    from .token_paste import bp as token_paste_bp
    from .url_verify import bp as url_verify_bp
    from .image_gen import bp as image_gen_bp
    from .seo_viz import bp as seo_viz_bp
    from .equity_ledger import bp as equity_ledger_bp
    from .health import bp as health_bp
    from .channel_bind_save import bp as channel_bind_save_bp
    from .copilot import bp as copilot_bp
    from .schedule import bp as schedule_bp
    from .pr_queue import bp as pr_queue_bp
    from .metrics import bp as metrics_bp

    for bp in (main_bp, pipeline_bp, batch_bp, checkpoint_bp,
               history_bp, drafts_bp, settings_basic_bp, llm_bp, oauth_bp,
               profiles_bp, sites_bp, queue_bp, dashboard_bp,
               medium_login_bp, bind_bp, token_paste_bp, url_verify_bp, image_gen_bp,
               seo_viz_bp, equity_ledger_bp, health_bp, channel_bind_save_bp,
               copilot_bp, schedule_bp, pr_queue_bp, metrics_bp):
        app.register_blueprint(bp)
