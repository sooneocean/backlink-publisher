"""SSL context helper with environment-gated insecure verification."""

from __future__ import annotations

import os
import ssl


def get_ssl_context() -> ssl.SSLContext:
    if os.environ.get("BACKLINK_PUBLISHER_ALLOW_INSECURE_SSL") == "1":
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    return ssl.create_default_context()