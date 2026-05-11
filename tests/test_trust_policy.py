from web_scraper.trust_policy import TrustPolicy, classify_target


def test_classify_owner_repo_shorthand():
    t = classify_target("anthropics/claude-cookbooks")
    assert t.kind == "github_repo"
    assert t.owner == "anthropics"
    assert t.repo == "claude-cookbooks"


def test_classify_github_url():
    t = classify_target("https://github.com/browser-use/browser-use/tree/main")
    assert t.kind == "github_repo"
    assert t.owner == "browser-use"
    assert t.repo == "browser-use"


def test_classify_https_non_github():
    t = classify_target("https://docs.polymarket.com/clob")
    assert t.kind == "https_url"
    assert t.host == "docs.polymarket.com"


def test_classify_garbage():
    assert classify_target("").kind == "unknown"
    assert classify_target("just-a-string").kind == "unknown"


def test_trusted_github_owner_is_allowed():
    policy = TrustPolicy()
    decision = policy.classify("anthropics/skills")
    assert decision.allowed
    assert decision.requires_auth_check
    assert "trusted allowlist" in decision.reason


def test_untrusted_github_owner_is_blocked():
    policy = TrustPolicy()
    decision = policy.classify("randomuser/sketchy-repo")
    assert not decision.allowed
    assert not decision.requires_auth_check
    assert "not in trusted allowlist" in decision.reason


def test_trusted_domain_is_allowed():
    policy = TrustPolicy()
    decision = policy.classify("https://docs.polymarket.com/clob")
    assert decision.allowed
    assert decision.requires_auth_check


def test_untrusted_domain_is_blocked():
    policy = TrustPolicy()
    decision = policy.classify("https://random.example.com/blob")
    assert not decision.allowed


def test_yfe404_is_seeded_trusted():
    policy = TrustPolicy()
    decision = policy.classify("yfe404/web-scraper")
    assert decision.allowed, "web-scraper skill author must be in seed allowlist"


def test_add_trusted_owner_then_classify():
    policy = TrustPolicy()
    assert not policy.classify("acmecorp/foo").allowed
    policy.add_trusted_owner("acmecorp")
    assert policy.classify("acmecorp/foo").allowed
