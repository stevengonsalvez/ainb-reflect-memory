"""Regression tests for the reflect serve curation layer.

These pin the defects surfaced in the PR #29 adversarial review: frontmatter
corruption when a value contains ``---``, silent wiping of a malformed compress
queue, archive/restore collisions, and the loopback + CSRF request guard.
"""

from __future__ import annotations

import http.client
import shutil
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from reflect_kb.serve import KnowledgeBase, MutationError, make_handler

FIXTURE = Path(__file__).parent / "e2e" / "fixture-kb"


@pytest.fixture()
def kb(tmp_path: Path) -> KnowledgeBase:
    dest = tmp_path / "kb"
    shutil.copytree(FIXTURE, dest)
    return KnowledgeBase(dest)


def _write_note(kb: KnowledgeBase, name: str, text: str) -> None:
    (kb.repo / "documents" / f"{name}.md").write_text(text)
    kb._invalidate()


def test_confidence_edit_preserves_note_with_dashes_in_value(kb: KnowledgeBase):
    # A frontmatter value containing '---' must not corrupt the note on rewrite.
    _write_note(kb, "dash", (
        '---\n'
        'id: dash\n'
        'title: "cost --- benefit tradeoff"\n'
        'confidence: low\n'
        'tags: [x, y]\n'
        '---\n\n'
        '# Body\n\nA line with --- dashes that must survive.\n'
    ))
    kb.set_confidence("dash", "high")
    after = (kb.repo / "documents" / "dash.md").read_text()
    assert "cost --- benefit tradeoff" in after      # title intact
    assert "A line with --- dashes that must survive." in after  # body intact
    assert after.count("confidence:") == 1
    assert "confidence: high" in after


def test_confidence_edit_appends_when_missing_at_top_level(kb: KnowledgeBase):
    _write_note(kb, "noconf", '---\nid: noconf\ntitle: no confidence\n---\n\nbody\n')
    kb.set_confidence("noconf", "medium")
    assert next(m for m in kb.memories() if m["id"] == "noconf")["confidence"] == "medium"


def test_invalid_confidence_rejected(kb: KnowledgeBase):
    with pytest.raises(MutationError):
        kb.set_confidence("alpha-db-migration-order", "bogus")


def test_malformed_compress_queue_fails_loud_not_silent_wipe(kb: KnowledgeBase):
    (kb.repo / "compress-queue.yaml").write_text("{{ not: valid yaml")
    with pytest.raises(MutationError):
        kb.compress_queue()
    # the malformed file must NOT have been overwritten
    assert (kb.repo / "compress-queue.yaml").read_text().startswith("{{")


def test_archive_then_restore_is_net_neutral(kb: KnowledgeBase):
    n = len(kb.memories())
    kb.archive("orphan-untagged-note")
    assert len(kb.memories()) == n - 1
    assert [a["id"] for a in kb.archived()] == ["orphan-untagged-note"]
    kb.restore("orphan-untagged-note")
    assert len(kb.memories()) == n


def test_restore_refuses_to_clobber_a_live_note(kb: KnowledgeBase):
    kb.archive("orphan-untagged-note")
    # a new live note reoccupies the same filename
    _write_note(kb, "orphan-untagged-note", '---\nid: orphan-untagged-note\ntitle: new\n---\n\nnew\n')
    with pytest.raises(MutationError):
        kb.restore("orphan-untagged-note")


def test_compress_needs_two_live_members(kb: KnowledgeBase):
    with pytest.raises(MutationError):
        kb.queue_compress(["alpha-auth-jwt-fix"])
    with pytest.raises(MutationError):
        kb.queue_compress(["alpha-auth-jwt-fix", "does-not-exist"])


def test_archiving_a_queued_member_dissolves_a_too_small_group(kb: KnowledgeBase):
    kb.queue_compress(["beta-cache-redis-decision", "beta-old-inmemory-cache"])
    kb.archive("beta-old-inmemory-cache")
    assert kb.compress_queue()["groups"] == []


# ---------- HTTP request guard (loopback + CSRF) ----------

@pytest.fixture()
def server(kb: KnowledgeBase):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(kb))
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield port
    httpd.shutdown()


def _request(port, method, path, host=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.putrequest(method, path, skip_host=(host is not None))
    if host is not None:
        conn.putheader("Host", host)
    for k, v in (headers or {}).items():
        conn.putheader(k, v)
    conn.putheader("Content-Length", "0")
    conn.endheaders()
    resp = conn.getresponse()
    resp.read()
    conn.close()
    return resp.status


def test_loopback_host_allowed(server):
    assert _request(server, "GET", "/api/stats", host=f"127.0.0.1:{server}") == 200


def test_non_loopback_host_rejected(server):
    # DNS-rebinding: attacker domain resolving to 127.0.0.1 still sends its Host.
    assert _request(server, "GET", "/api/stats", host="evil.example.com") == 403


def test_post_without_csrf_header_rejected(server):
    status = _request(server, "POST", "/api/memories/orphan-untagged-note/archive",
                      host=f"127.0.0.1:{server}")
    assert status == 403


def test_post_with_csrf_header_allowed(server):
    status = _request(server, "POST", "/api/memories/orphan-untagged-note/archive",
                      host=f"127.0.0.1:{server}", headers={"X-Reflect": "1"})
    assert status == 200
