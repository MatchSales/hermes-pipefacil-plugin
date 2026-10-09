"""Managed profiles fail closed; unmanaged profiles preserve their admission behavior."""
import json

import pytest

from test_gateway_sessions import _modules


def control_module():
    module = _modules()[0]
    return __import__(module.__package__ + ".admission", fromlist=["*"])


def test_opt_in_defaults_and_unmanaged_behavior(tmp_path):
    module = control_module()
    unmanaged = module.ProfileAdmissionControl(tmp_path)
    assert unmanaged.enter()
    unmanaged.leave()
    with pytest.raises(module.AdmissionConflict):
        unmanaged.set_closed(True, 0)
    managed = module.ProfileAdmissionControl(tmp_path, True)
    assert not managed.enter()
    assert managed.status()["drained"]


def test_close_drains_active_turn_and_requires_current_version(tmp_path):
    module = control_module()
    control = module.ProfileAdmissionControl(tmp_path, True)
    control.set_closed(False, 0)
    assert control.enter()
    control.set_closed(True, 1)
    assert not control.enter()
    with pytest.raises(module.AdmissionConflict):
        control.set_closed(False, 2)
    control.leave()
    with control.configuration_scope(2):
        assert control.status()["drained"]
    with pytest.raises(module.AdmissionConflict):
        control.set_closed(False, 1)
    assert module.ProfileAdmissionControl(tmp_path, True).status()["closed"]
    assert (control.path.stat().st_mode & 0o777) == 0o600
    control.set_closed(False, 2)
    assert control.enter()
    control.leave()


@pytest.mark.parametrize("data", [{"closed": "false", "version": 0}, {"closed": False, "version": True},
                                 {"closed": False, "version": -1}])
def test_corrupt_state_refuses_to_start(tmp_path, data):
    module = control_module()
    (tmp_path / ".pipefacil-admission.json").write_text(json.dumps(data))
    with pytest.raises(ValueError):
        module.ProfileAdmissionControl(tmp_path, True)


def test_reservation_is_released_on_cancellation_path(tmp_path):
    module = control_module()
    control = module.ProfileAdmissionControl(tmp_path, True)
    control.set_closed(False, 0)
    assert control.enter()
    try:
        raise InterruptedError("test cancellation")
    except InterruptedError:
        pass
    finally:
        control.leave()
    control.set_closed(True, 1)
    assert control.status()["drained"]
    with pytest.raises(module.AdmissionConflict):
        control.leave()
    assert control.status()["active"] == 0
