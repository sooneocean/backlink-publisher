"""WebUI Flask app factory — Plan 2026-05-18-001 Unit 3.

``create_app()`` returns the configured Flask app with all blueprints
registered, scheduler started (when not in test mode), and pending
draft jobs restored from the queue.
"""

from __future__ import annotations

import os
import uuid
from datetime import timedelta
from pathlib import Path

from flask import Flask


def create_app(*, start_scheduler: bool | None = None) -> Flask:
    """Build the Flask app.

    Args:
        start_scheduler: When True, start APScheduler and restore pending
            draft jobs. When None (default), starts only when not running
            under pytest (detected via PYTEST_CURRENT_TEST env var).
    """
    template_dir = Path(__file__).parent / "templates"
    app = Flask(__name__, template_folder=str(template_dir))
    app.secret_key = os.environ.get(
        'SECRET_KEY', 'backlink-publisher-secret-' + str(uuid.uuid4()),
    )
    app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(minutes=15)
    app.config['SESSION_COOKIE_SECURE'] = True
    app.config['SESSION_COOKIE_HTTPONLY'] = True
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

    # Share the publish-path markdown→HTML renderer with Jinja so preview
    # visual matches the published article (Plan 2026-05-19-007 Unit 2).
    from backlink_publisher._util.markdown import render_to_html
    app.jinja_env.filters['render_markdown'] = render_to_html

    # Register all blueprints
    from .routes import register_blueprints
    register_blueprints(app)

    # Inject the live registered platforms into every template render.
    # Plan 2026-05-19-002 U2 / R6: WebUI is reverse-driven by the publisher
    # registry — register("X", XAdapter) is now sufficient to make X
    # appear in the publish-form select, the history filter-chip row,
    # the JS counter dict, and norm_platform routing without any HTML edit.
    # ``s.title()`` is the v1 display-name source (no _display_name_map dict
    # per scope-guardian F5); i18n migration is a Deferred follow-up.
    @app.context_processor
    def inject_platforms():
        # Importing adapters at first request populates the registry
        # side-effect — same idiom as plan_backlinks.py / publish_backlinks.py.
        import backlink_publisher.publishing.adapters  # noqa: F401
        from backlink_publisher.publishing.registry import registered_platforms

        return {
            "platforms": [
                {"slug": s, "display_name": s.title()}
                for s in registered_platforms()
            ]
        }

    # Plan 2026-05-20-002 Unit 5 — register csrf_token() Jinja global so
    # the homepage <meta name="csrf-token"> tag can read the per-session
    # token for the new /url-verify POST endpoint. Calling
    # _ensure_csrf_token() is idempotent within a request.
    @app.context_processor
    def inject_csrf_token():
        # Return the STRING value so templates can use ``{{ csrf_token }}``
        # uniformly. Previously this returned the function — templates
        # were split between ``{{ csrf_token }}`` and ``{{ csrf_token() }}``
        # and per-route ``_settings_context`` re-bound to a string, so
        # ``{{ csrf_token() }}`` exploded under /settings. The try/except
        # handles template-only renders that some unit tests do outside
        # of a real request context (session is unavailable there).
        from .helpers import _ensure_csrf_token
        try:
            return {"csrf_token": _ensure_csrf_token()}
        except RuntimeError:
            return {"csrf_token": ""}

    # Global CSRF enforcement. SameSite=Lax + loopback already block most
    # cross-site POST, but operators who flip BACKLINK_PUBLISHER_ALLOW_NETWORK
    # to bind off-loopback lose Lax's effective protection. Defence-in-depth
    # so every state-mutating verb checks a token rather than trusting that
    # 12 of 16 blueprints remembered to call _check_csrf_or_abort inline.
    #
    # Tests can opt out via ``app.config['CSRF_ENABLED'] = False`` or the
    # legacy ``WTF_CSRF_ENABLED = False`` (many existing tests already set
    # that flag defensively — both are honored).
    app.config.setdefault('CSRF_ENABLED', True)

    @app.before_request
    def _global_csrf_guard():
        from flask import request as _req
        if _req.method not in ('POST', 'PUT', 'PATCH', 'DELETE'):
            return
        if app.config.get('CSRF_ENABLED', True) is False:
            return
        if app.config.get('WTF_CSRF_ENABLED', True) is False:
            return
        # OAuth callbacks arrive via 302 from Google with their own HMAC-signed
        # state param verified inside the handler; CSRF token can't survive
        # the cross-origin redirect.
        if _req.endpoint and _req.endpoint.endswith('oauth_callback'):
            return
        from .helpers import _check_csrf_or_abort
        _check_csrf_or_abort()

    # Start scheduler unless under pytest (tests don't need background jobs)
    if start_scheduler is None:
        start_scheduler = 'PYTEST_CURRENT_TEST' not in os.environ

    if start_scheduler:
        from .scheduler import _restore_scheduled_jobs, _scheduler
        if not _scheduler.running:
            _scheduler.start()
        _restore_scheduled_jobs()

        # Plan 2026-05-19-001 Unit 4: real-runtime startup hooks. Gated by
        # ``start_scheduler`` so pytest never fires them. Wrapped because a
        # disk read failure must not crash ``create_app``.
        import logging
        _log = logging.getLogger(__name__)
        try:
            from webui_store.channel_status import reconcile_on_load
            reconcile_on_load()
        except Exception as exc:  # noqa: BLE001 — startup must not crash
            _log.warning("channel_status.reconcile_on_load failed: %s", exc)
        try:
            from .services.bind_job import reap_orphans
            reap_orphans()
        except Exception as exc:  # noqa: BLE001 — startup must not crash
            _log.warning("bind_job.reap_orphans failed: %s", exc)

    return app
