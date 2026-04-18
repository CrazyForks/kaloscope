import os
import ssl
from typing import cast

import sanic.mixins.startup as startup
from sanic import Sanic
from sanic.constants import LocalCertCreator
from sanic.http.tls.creators import CertCreator
from sanic.worker.manager import WorkerManager

# patch the WorkerManager to allow longer startup time
WorkerManager.THRESHOLD = 600  # type: ignore


def _patched_get_ssl_context(app: Sanic, ssl: ssl.SSLContext | None) -> ssl.SSLContext:
    """Patched version of get_ssl_context that allows self-signed TLS in PROD mode.

    Args:
        app: The Sanic application instance.
        ssl: An optional SSLContext. If provided, it will be used directly.

    Returns:
        The SSLContext to be used for TLS connections.
    """
    if ssl:
        return ssl

    creator = CertCreator.select(
        app,
        cast(LocalCertCreator, app.config.LOCAL_CERT_CREATOR),
        app.config.LOCAL_TLS_KEY,
        app.config.LOCAL_TLS_CERT,
    )
    # prefer TLS_HOSTNAME env variable over app config
    hostname = os.environ.get("TLS_HOSTNAME") or app.config.LOCALHOST
    context = creator.generate_cert(hostname)
    return context


startup.get_ssl_context = _patched_get_ssl_context  # type: ignore
