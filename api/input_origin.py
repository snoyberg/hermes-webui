"""Server-owned text boundaries for native transcript consumers.

This envelope is metadata, never decoded from user text. Duplicate text rather
than byte offsets allows the native memory egress redactor to scrub all copies.
"""


def input_origin(text, notifications, source, *, agent_input, workspace_context=''):
    """Describe only the final accepted input, after notification rejection."""
    kind = {'process_wakeup': 'process_complete',
            'delegation_wakeup': 'async_delegation_complete'}.get(source)
    segments = [{'kind': 'machine', 'text': notice} for notice in notifications]
    segments.append({'kind': 'machine' if kind else 'human', 'text': text})
    input_text = agent_input
    if isinstance(agent_input, list):
        input_text = '\n'.join(p['text'] for p in agent_input
                               if isinstance(p, dict) and p.get('type') == 'text' and isinstance(p.get('text'), str))
    if workspace_context + '\n\n'.join(s['text'] for s in segments).strip() != input_text:
        return kind, None
    return kind, {'input_origin': {
        'version': 1, 'producer': 'hermes-webui', 'input_text': input_text,
        'context_text': workspace_context, 'sync_text': text, 'segments': segments,
    }}
