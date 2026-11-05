"""ACP session/load and session/resume change only the workspace (#149013).

A load in a process that does not own the session restored its history once;
re-writing the transcript from that snapshot on every load deletes rows another
process appended in between.
"""
from types import SimpleNamespace

from acp_adapter.session import SessionManager
from hermes_state import SessionDB


def _manager(db):
    return SessionManager(db=db, agent_factory=lambda: SimpleNamespace(model="fixture"))


def _contents(db, session_id):
    return [m.get("content") for m in db.get_messages_as_conversation(session_id)]


def test_load_and_resume_keep_rows_written_by_another_process(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    origin = _manager(db)
    state = origin.create_session(cwd=str(workspace))
    state.history.extend([{"role": "user", "content": "first"}, {"role": "assistant", "content": "reply"}])
    origin.save_session(state.session_id)

    loader = _manager(db)
    assert loader.update_cwd(state.session_id, str(workspace)) is not None
    db.append_message(state.session_id, role="user", content="written elsewhere")
    db.append_message(state.session_id, role="assistant", content="answered elsewhere")

    assert loader.update_cwd(state.session_id, str(workspace)) is not None

    assert _contents(db, state.session_id) == ["first", "reply", "written elsewhere", "answered elsewhere"]
    db.close()
