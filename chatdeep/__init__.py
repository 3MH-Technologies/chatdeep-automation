"""chatdeep — أداة أتمتة شات احترافية لموقع chat-deep.ai.

Developed by 3MH TECHNOLOGIES — https://3mh.pages.dev · Telegram: t.me/j49_c
Licensed under MIT (see LICENSE).

Public API::

    from chatdeep import ChatDeepClient, ChatMessage, Quota

    with ChatDeepClient() as client:
        answer = client.ask("مرحبًا!", mode="fast")
        print(answer)

See ``docs/API.md`` for the reverse-engineered internal API documentation.
"""

from .client import BASE_URL, ChatDeepClient, discover_config
from .bridge import BridgeServer, BridgeTokenProvider, TokenPool
from .errors import (
    ChatDeepError,
    ConsentRequired,
    DependencyError,
    ERROR_CLASSES,
    ERROR_INFO,
    HttpError,
    NonceError,
    QuotaExceeded,
    RateLimited,
    ServicePaused,
    SessionError,
    TurnstileClientError,
    TurnstileError,
    UpstreamError,
    ValidationError,
)
from .images import prepare_image, prepare_images
from .models import (
    ChatAnswer,
    ChatMessage,
    CompareResult,
    Consent,
    Quota,
    ServiceStatus,
    SessionInfo,
    SiteConfig,
    StreamEvent,
)
from .providers import build_token_provider
from .solvers import (
    CapSolverProvider,
    TaskSolverProvider,
    TwoCaptchaProvider,
    YesCaptchaProvider,
    make_solver_provider,
)
from .sse import iter_events
from .turnstile import (
    DEFAULT_SITEKEY,
    ManualTokenProvider,
    PlaywrightTokenProvider,
    TokenProvider,
)

__version__ = "1.1.0"
__all__ = [
    "__version__",
    "BASE_URL",
    "ChatDeepClient",
    "ChatAnswer",
    "ChatMessage",
    "CompareResult",
    "Consent",
    "Quota",
    "ServiceStatus",
    "SessionInfo",
    "SiteConfig",
    "StreamEvent",
    "discover_config",
    "iter_events",
    "prepare_image",
    "prepare_images",
    "TokenProvider",
    "PlaywrightTokenProvider",
    "ManualTokenProvider",
    "TaskSolverProvider",
    "CapSolverProvider",
    "TwoCaptchaProvider",
    "YesCaptchaProvider",
    "make_solver_provider",
    "BridgeServer",
    "BridgeTokenProvider",
    "TokenPool",
    "build_token_provider",
    "DEFAULT_SITEKEY",
    "ChatDeepError",
    "SessionError",
    "NonceError",
    "TurnstileError",
    "TurnstileClientError",
    "ConsentRequired",
    "QuotaExceeded",
    "RateLimited",
    "ServicePaused",
    "ValidationError",
    "UpstreamError",
    "HttpError",
    "DependencyError",
    "ERROR_CLASSES",
    "ERROR_INFO",
]
