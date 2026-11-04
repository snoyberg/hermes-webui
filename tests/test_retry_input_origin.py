"""Execute both credential-recovery contracts without importing server startup."""
import ast
import os
from pathlib import Path
from types import SimpleNamespace

import pytest


STREAMING = Path(__file__).parents[1] / 'api/streaming.py'


def retry_kwargs(route, callable_obj, source):
    tree = ast.parse(STREAMING.read_text())
    helpers = {'_build_run_conversation_kwargs', '_add_supported_run_conversation_kwarg'}
    scope = {'_preserve_legacy_agent_row_identity': lambda *args: None}
    exec(compile(ast.Module(body=[n for n in tree.body if isinstance(n, ast.FunctionDef)
                                 and n.name in helpers], type_ignores=[]), str(STREAMING), 'exec'), scope)
    origin_scope = {}
    exec((STREAMING.parent / 'input_origin.py').read_text(), origin_scope)
    clean = 'Go for it, within that scope.' if source == 'webui' else 'Scheduled job finished.'
    composed = 'completion receipt\n\n' + clean
    kind, metadata = origin_scope['input_origin'](clean, ['completion receipt'], source, agent_input=composed)
    scope.update(agent=SimpleNamespace(run_conversation=callable_obj),
                 _heal_agent=SimpleNamespace(run_conversation=callable_obj),
                 user_message=composed, workspace_system_msg=None, _heal_context_messages=[],
                 _cfg={}, resolved_model=None, resolved_provider=None, resolved_base_url=None,
                 _session_requested_provider='', _heal_conversation_history_revision=None,
                 session_id='synthetic', msg_text=clean, _heal_persist_user_timestamp=None,
                 _active_turn_identity={}, s=SimpleNamespace(messages=[]),
                 _cancelled_journal_turn_owner=lambda messages: False,
                 _sanitize_messages_for_agent=lambda messages, **kw: messages,
                 _origin_kind=kind, _origin_metadata=metadata)
    assignments = [n for n in ast.walk(tree) if isinstance(n, ast.Assign)
                   and isinstance(n.value, ast.Call)
                   and isinstance(n.value.func, ast.Name)
                   and n.value.func.id == '_build_run_conversation_kwargs'
                   and any(isinstance(t, ast.Name) and t.id == route for t in n.targets)]
    assert len(assignments) == 1
    exec(compile(ast.Module(body=assignments, type_ignores=[]), str(STREAMING), 'exec'), scope)
    return scope[route], clean, metadata


@pytest.mark.parametrize('route', ['_heal_kwargs', '_heal_kwargs2'])
@pytest.mark.parametrize('source', ['webui', 'process_wakeup'])
def test_retry_carries_accepted_origin(route, source):
    def native(*, persist_user_display_kind=None, persist_user_display_metadata=None, **kwargs):
        return persist_user_display_kind, persist_user_display_metadata
    kwargs, _, metadata = retry_kwargs(route, native, source)
    kind, actual = native(**kwargs)
    assert actual == metadata
    assert kind == (None if source == 'webui' else 'process_complete')


@pytest.mark.parametrize('route', ['_heal_kwargs', '_heal_kwargs2'])
def test_retry_old_callable_does_not_receive_new_keywords(route):
    def old(*, user_message, system_message, conversation_history, task_id, persist_user_message):
        return persist_user_message
    kwargs, clean, _ = retry_kwargs(route, old, 'webui')
    assert old(**kwargs) == clean


@pytest.mark.parametrize('route', ['_heal_kwargs', '_heal_kwargs2'])
@pytest.mark.parametrize('source', ['webui', 'process_wakeup'])
def test_retry_native_sync_and_sdk_peers(monkeypatch, route, source):
    if not os.environ.get('ORIGIN_NATIVE_RETRY_TESTS'):
        pytest.skip('requires explicit isolated native/provider test environment')
    from test_origin import provider
    import socket
    monkeypatch.setattr(socket.socket, 'connect', lambda *a, **k: pytest.fail('unexpected network attempt'))
    from agent.turn_context import _stage_turn_user_message
    from agent.memory_manager import MemoryManager
    from run_agent import AIAgent
    from honcho import MessageCreateParams
    from honcho.peer import Peer
    from hermes_plugin_honcho.session import HonchoSessionManager
    from unittest.mock import Mock

    kwargs, clean, _ = retry_kwargs(route, AIAgent.run_conversation, source)
    row, _ = _stage_turn_user_message(SimpleNamespace(), kwargs['user_message'], clean, None, None,
                                    kwargs.get('persist_user_display_kind'),
                                    kwargs.get('persist_user_display_metadata'))
    p, session = provider(monkeypatch)
    manager = MemoryManager()
    manager._providers = [p]
    monkeypatch.setattr(manager, '_owner_allows', lambda *a: True)
    monkeypatch.setattr(manager, '_submit_background', lambda fn, **kw: fn())
    manager.queue_prefetch_all = lambda *a, **kw: None
    AIAgent._sync_external_memory_for_turn(
        SimpleNamespace(_memory_manager=manager, session_id='synthetic', _turn_author=None),
        original_user_message=clean, final_response='Assistant report.', interrupted=False, messages=[row])
    m = HonchoSessionManager()
    sdk_session = Mock()
    m._sessions_cache['test'] = sdk_session
    client = Mock(workspace_id='test')
    monkeypatch.setattr(m, '_authed_call', lambda operation, fn: fn())
    monkeypatch.setattr(m, '_get_or_create_peer', lambda name: Peer(name, client))
    monkeypatch.setattr(m, '_author_peer_for_session', lambda s, sid, name: Peer(name, client))
    assert m._flush_session(session)
    payload = sdk_session.add_messages.call_args.args[0]
    assert all(isinstance(message, MessageCreateParams) for message in payload)
    assert any(message.peer_id == 'hermes-machine' and 'completion receipt' in message.content
               for message in payload)
    if source == 'webui':
        assert any(message.peer_id == 'michael' and message.content == clean for message in payload)
    else:
        assert all(message.peer_id != 'michael' for message in payload)
    assert payload[-1].peer_id == 'rocky'
