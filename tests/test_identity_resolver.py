"""Tests for the Slack ↔ external-system identity resolver and store."""

import pytest

from src.core.context import RequestContext
from src.identity.resolver import IdentityResolutionError, IdentityResolver
from src.identity.store import IdentityStore


@pytest.fixture()
def store(tmp_path):
    return IdentityStore(path=tmp_path / "platform.db")


def _context(user_id="U1", email=None):
    return RequestContext.from_slack(channel_id="C1", user_id=user_id, email=email)


def test_store_upsert_merges_fields(store):
    store.upsert("U1", email="jane@company.com")
    store.upsert("U1", snipeit_user_id=9)
    record = store.get("U1")
    assert record.email == "jane@company.com"
    assert record.snipeit_user_id == "9"
    assert record.taskwondo_user_id is None


def test_resolve_links_snipeit_by_email_and_caches(store):
    calls = []

    def snipeit_lookup(email):
        calls.append(email)
        return {"id": 9, "name": "Jane Smith"}

    resolver = IdentityResolver(store, snipeit_lookup=snipeit_lookup)
    context = _context(email="jane@company.com")

    identity = resolver.resolve(context, want_snipeit=True)
    assert identity.snipeit_user_id == "9"
    assert identity.display_name == "Jane Smith"
    assert identity.stamp() == "Requested by Jane Smith (Slack U1)"

    # Second resolve must hit the cache, not the lookup again.
    resolver2 = IdentityResolver(store, snipeit_lookup=snipeit_lookup)
    again = resolver2.resolve(_context(), want_snipeit=True)
    assert again.snipeit_user_id == "9"
    assert calls == ["jane@company.com"]


def test_resolve_taskwondo_independent_of_snipeit(store):
    resolver = IdentityResolver(
        store,
        taskwondo_lookup=lambda email: {"id": "user_9", "name": "Jane"},
    )
    identity = resolver.resolve(_context(email="jane@company.com"), want_taskwondo=True)
    assert identity.taskwondo_user_id == "user_9"


def test_resolve_raises_when_email_unknown(store):
    resolver = IdentityResolver(store, snipeit_lookup=lambda email: None)
    with pytest.raises(IdentityResolutionError) as exc:
        resolver.resolve(_context(), want_snipeit=True)
    assert "link me" in str(exc.value)


def test_resolve_raises_when_external_account_missing(store):
    store.upsert("U1", email="jane@company.com")
    resolver = IdentityResolver(store, snipeit_lookup=lambda email: None)
    with pytest.raises(IdentityResolutionError) as exc:
        resolver.resolve(_context(), want_snipeit=True)
    assert "jane@company.com" in str(exc.value)


def test_auto_link_disabled_prevents_lookup(store, monkeypatch):
    from src.core.config import settings

    monkeypatch.setattr(settings, "identity_auto_link_by_email", False)
    called = []
    resolver = IdentityResolver(store, snipeit_lookup=lambda email: called.append(email))
    with pytest.raises(IdentityResolutionError):
        resolver.resolve(_context(email="jane@company.com"), want_snipeit=True)
    assert called == []


def test_register_persists_email(store):
    resolver = IdentityResolver(store)
    identity = resolver.register("U1", email="jane@company.com", display_name="Jane")
    assert identity.email == "jane@company.com"
    assert store.get("U1").email == "jane@company.com"
