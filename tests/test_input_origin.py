"""Native metadata is generated only from final server-owned accepted input."""
import ast
from pathlib import Path

import pytest
from api.input_origin import input_origin


@pytest.mark.parametrize('source,kind', [('webui',None),('process_wakeup','process_complete'),('delegation_wakeup','async_delegation_complete')])
def test_final_projection(source, kind):
    actual, meta = input_origin('Go ahead.', ['accepted'], source, agent_input='accepted\n\nGo ahead.')
    assert actual == kind
    assert meta['input_origin']['segments'] == [
        {'kind':'machine','text':'accepted'},
        {'kind':'human' if kind is None else 'machine','text':'Go ahead.'}]


def test_rejected_input_cannot_bind_old_envelope():
    assert input_origin('Check.', ['rejected'], 'webui', agent_input='Check.')[1] is None


def test_actual_streaming_final_bridge_signature_gate():
    tree = ast.parse((Path(__file__).parents[1] / 'api/streaming.py').read_text())
    block = None
    for node in ast.walk(tree):
        for field in ('body','orelse','finalbody'):
            nodes = getattr(node, field, [])
            if not isinstance(nodes, list):
                continue
            for i, child in enumerate(nodes):
                if isinstance(child, ast.ImportFrom) and child.module == 'api.input_origin':
                    block = nodes[i:i+3]
    assert block is not None
    from api.streaming import _add_supported_run_conversation_kwarg
    def native(*, persist_user_display_kind=None, persist_user_display_metadata=None):
        pass
    kwargs = {}
    scope = dict(msg_text='Go ahead.', _process_notifications=['accepted'], _turn_pending_source='webui',
                 user_message='Workspace\naccepted\n\nGo ahead.', workspace_ctx='Workspace\n',
                 agent=type('Agent', (), {'run_conversation':staticmethod(native)})(),
                 _run_conversation_kwargs=kwargs,
                 _add_supported_run_conversation_kwarg=_add_supported_run_conversation_kwarg)
    exec(compile(ast.Module(body=block, type_ignores=[]), '<actual-final-streaming-bridge>', 'exec'), scope)
    assert kwargs['persist_user_display_metadata']['input_origin']['segments'][0]['text'] == 'accepted'
    assert kwargs['persist_user_display_kind'] is None
