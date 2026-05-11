from web_scraper.authenticator import (
    AuthenticationError,
    AuthVerdict,
    GitHubAuthenticator,
)
from web_scraper.trust_policy import (
    ScrapeDecision,
    ScrapeTarget,
    TrustPolicy,
    classify_target,
)

__all__ = [
    "AuthVerdict",
    "AuthenticationError",
    "GitHubAuthenticator",
    "ScrapeDecision",
    "ScrapeTarget",
    "TrustPolicy",
    "classify_target",
]
