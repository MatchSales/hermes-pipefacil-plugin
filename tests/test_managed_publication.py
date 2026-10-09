"""A managed revision changes only session routing, preserving native history and delivery IDs."""
import json

import pytest

from test_gateway_sessions import multiplex  # noqa: F401


def test_publication_preserves_history_and_other_profile(multiplex):
    from gateway.session import SessionSource
    from gateway.config import Platform
    from hermes_constants import set_hermes_home_override, reset_hermes_home_override

    m = multiplex
    adapter = m.adapters["sdr-a"]
    admission = __import__(m.module.__package__ + ".admission", fromlist=["ProfileAdmissionControl"])
    adapter._admission_control = admission.ProfileAdmissionControl(adapter.profile_home, True)
    adapter._hermes_profile_name = "sdr-a"
    adapter.build_source = lambda **kw: SessionSource(platform=Platform("pipefacil"), **kw)
    old = m.entries["sdr-a"]
    db = m.store._db_for_key(old.session_key)
    db.append_message(old.session_id, "user", "preserved conversation")
    marker = {"revision": 7, "contentHash": "a" * 64, "sessionGeneration": 7,
              "model": "example", "provider": "openai"}
    (adapter.profile_home / ".pipefacil-publication.json").write_text(json.dumps(marker))
    adapter._admission_control.active = 1
    with pytest.raises(admission.AdmissionConflict):
        adapter.install_managed_publication(0)
    adapter._admission_control.active = 0
    assert adapter.install_managed_publication(0) == marker
    source = adapter._build_contact_source(chat_id=m.chat_id, contact={"id": "test-contact"}, message_id="message")
    assert source.chat_id == m.chat_id and source.profile == "sdr-a"
    assert source.thread_id == "pf-revision-7"
    token = set_hermes_home_override(str(adapter.profile_home))
    try:
        new = m.store.get_or_create_session(source)
    finally:
        reset_hermes_home_override(token)
    assert new.session_id != old.session_id
    assert db.get_messages(old.session_id)[0]["content"] == "preserved conversation"
    assert m.store.peek_session_id(m.entries["sdr-b"].session_key) == m.entries["sdr-b"].session_id
    assert m.store.get_or_create_session(source).session_id == new.session_id
    # Restart reads the durable marker; unmanaged profiles never opt into this routing.
    publication = __import__(m.module.__package__ + ".publication", fromlist=["read_publication"])
    assert publication.read_publication(adapter.profile_home) == marker
    assert publication.read_publication(m.homes["sdr-b"]) is None


def test_commercial_skill_reading_is_limited_to_published_markdown(multiplex):
    from hermes_constants import set_hermes_home_override, reset_hermes_home_override
    m = multiplex
    home = m.homes["sdr-a"]
    path = home / "skills/pipefacil-commercial/vendas/SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text("Approved commercial skill")
    secret = home / ".env"
    adapter = m.adapters["sdr-a"]
    adapter._managed_publication = {"skills": ["vendas"]}
    token = set_hermes_home_override(str(home))
    try:
        with m.turn("sdr-a"):
            result = m.tools._read_profile_file({"path": str(path.relative_to(home))}, session_id=m.entries["sdr-a"].session_id)
            assert "Approved commercial skill" in result
            assert "not allowed" in m.tools._read_profile_file({"path": str(secret)}, session_id=m.entries["sdr-a"].session_id)
            path.unlink()
            path.symlink_to(secret)
            assert "not allowed" in m.tools._read_profile_file({"path": str(path)}, session_id=m.entries["sdr-a"].session_id)
        assert "skills/pipefacil-commercial/vendas/SKILL.md" in adapter._managed_channel_prompt()
    finally:
        reset_hermes_home_override(token)
