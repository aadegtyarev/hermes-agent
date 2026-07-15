"""curl + Jina Reader web-extract backend — user plugin.

Free ``web_extract`` provider with zero local browser footprint. The
two-tier strategy (raw HTML + lxml → Jina Reader remote render) lives in
:mod:`provider`. Enable by adding ``web-curl-jina`` to ``plugins.enabled``
and setting ``web.extract_backend: curl-jina`` in config.yaml.
"""

from __future__ import annotations

import logging

from .provider import CurlJinaWebSearchProvider

logger = logging.getLogger(__name__)


def register(ctx) -> None:
    """Register the curl+Jina extract provider with the plugin context."""
    ctx.register_web_search_provider(CurlJinaWebSearchProvider())
    logger.info("Registered web extract provider: curl-jina")
