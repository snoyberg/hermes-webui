"""Native update adapter: disposable transports only, never a live owner."""
from unittest.mock import Mock

import pytest


def test_public_agent_apply_delegates_native_without_generic_restart(monkeypatch):
    import api.updates as updates
    from api import native_agent_update as native
    accepted = {'ok': True, 'status': 'accepted', 'action_id': 'a'*32, 'owner': 'owner'}
    monkeypatch.setattr(native, 'submit', Mock(return_value=accepted))
    monkeypatch.setattr(updates, '_schedule_restart', Mock(side_effect=AssertionError('premature restart')))
    assert updates.apply_update('agent') == accepted
    updates._schedule_restart.assert_not_called()


def test_explicit_check_uses_native_and_cached_read_is_inert(monkeypatch):
    import api.updates as updates
    from api import native_agent_update as native
    monkeypatch.setattr(updates, '_update_cache', {'agent': None, 'webui': None, 'checked_at': 0})
    offer = {'name': 'agent', 'native': True, 'behind': 3, 'can_apply': True, 'owner': 'owner'}
    monkeypatch.setattr(native, '_identity', lambda: 'owner')
    monkeypatch.setattr(native, 'check', Mock(return_value=offer))
    monkeypatch.setattr(updates, '_check_repo', lambda *args: {'name': 'webui', 'behind': 0})
    assert updates.check_for_updates(force=True)['agent'] == offer
    native.check.assert_called_once_with(force=True)
    assert updates.cached_update_status()['agent'] == offer
    native.check.assert_called_once_with(force=True)


def test_cached_identity_is_available_before_any_native_check(monkeypatch):
    import api.updates as updates
    from api import native_agent_update as native
    monkeypatch.setattr(updates, '_update_cache', {'agent': None, 'checked_at': 0})
    monkeypatch.setattr(native, '_identity', lambda: 'current-installation-profile')
    request = Mock(side_effect=AssertionError('cached GET must not contact native'))
    monkeypatch.setattr(native, '_request', request)
    assert updates.cached_update_status()['agent']['owner'] == 'current-installation-profile'
    request.assert_not_called()


def test_submit_is_accepted_not_completed(monkeypatch):
    from api import native_agent_update as native
    monkeypatch.setattr(native, '_request', Mock(return_value={
        'ok': True, 'name': 'hermes-update', 'action_id': 'a' * 32, 'pid': 42,
    }))
    monkeypatch.setattr(native, '_identity', lambda: 'installation-profile')
    result = native.submit()
    assert result == {'ok': True, 'status': 'accepted', 'action_id': 'a' * 32,
                      'owner': 'installation-profile', 'already_running': False}
    native._request.assert_called_once_with('submit')


@pytest.mark.parametrize('receipt,status', [
    ({'action_id': 'a'*32, 'outcome': 'success', 'followups': []}, 'success'),
    ({'action_id': 'a'*32, 'outcome': 'partial', 'followups': []}, 'partial'),
    ({'action_id': 'a'*32, 'outcome': 'failed'}, 'failed'),
    ({'action_id': 'a'*32, 'outcome': 'refused'}, 'refused'),
    ({'action_id': 'b'*32, 'outcome': 'success'}, 'unknown'),
])
def test_only_matching_receipt_certifies_outcome(monkeypatch, receipt, status):
    from api import native_agent_update as native
    monkeypatch.setattr(native, '_identity', lambda: 'installation-profile')
    monkeypatch.setattr(native, '_request', lambda op: {
        'running': False, 'exit_code': 0, 'receipt': receipt, 'lines': [],
    })
    result = native.status('a'*32, 'installation-profile')
    assert result['status'] == status
    assert result['action_id'] == 'a'*32
    assert not result.get('webui_recovered')


def test_running_needs_owned_native_log_header(monkeypatch):
    from api import native_agent_update as native
    monkeypatch.setattr(native, '_identity', lambda: 'installation-profile')
    response = {'running': True, 'pid': 42, 'lines': [
        '=== hermes-update started 2026-11-02 17:00:00 ' + 'a'*32 + ' ===']}
    monkeypatch.setattr(native, '_request', lambda op: response)
    assert native.status('a'*32, 'installation-profile')['status'] == 'running'
    response['lines'].append('=== hermes-update started malformed newer header ===')
    assert native.status('a'*32, 'installation-profile')['status'] == 'unknown'
    response['lines'] = []
    assert native.status('a'*32, 'installation-profile')['status'] == 'unknown'
    response['lines'] = ['=== hermes-update started 2026-11-02 17:00:00 ' + 'b'*32 + ' ===']
    assert native.status('a'*32, 'installation-profile')['status'] == 'superseded'


def test_owner_mismatch_never_contacts_native(monkeypatch):
    from api import native_agent_update as native
    monkeypatch.setattr(native, '_identity', lambda: 'other')
    request = Mock(side_effect=AssertionError('must not contact'))
    monkeypatch.setattr(native, '_request', request)
    assert native.status('a'*32, 'original')['status'] == 'unknown'
    request.assert_not_called()
