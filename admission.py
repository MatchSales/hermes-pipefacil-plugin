"""Opt-in profile admission barrier. Existing profiles keep their current behavior."""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import tempfile
import threading


class AdmissionConflict(RuntimeError):
    pass


class ProfileAdmissionControl:
    def __init__(self, home, enabled=False):
        self.enabled = enabled is True
        self.path = Path(home) / ".pipefacil-admission.json"
        self.lock = threading.RLock()
        self.active = 0
        self.version = 0
        self.closed = self.enabled
        if self.enabled and self.path.exists():
            data = json.loads(self.path.read_text())
            if type(data.get("closed")) is not bool or type(data.get("version")) is not int or data["version"] < 0:
                raise ValueError("Invalid managed admission state")
            self.closed, self.version = data["closed"], data["version"]

    def status(self):
        with self.lock:
            return {"managed": self.enabled, "closed": self.closed, "active": self.active,
                    "version": self.version, "drained": self.closed and self.active == 0}

    def enter(self):
        with self.lock:
            if self.closed:
                return False
            self.active += 1
            return True

    def leave(self):
        with self.lock:
            if self.active <= 0:
                raise AdmissionConflict("Unbalanced admission reservation")
            self.active -= 1

    def set_closed(self, closed, expected_version):
        with self.lock:
            if type(closed) is not bool or type(expected_version) is not int:
                raise AdmissionConflict("Invalid admission request")
            if not self.enabled or expected_version != self.version:
                raise AdmissionConflict("Admission version changed or profile is unmanaged")
            if not closed and self.active:
                raise AdmissionConflict("Current turns must finish before resuming")
            value = {"closed": bool(closed), "version": self.version + 1}
            with tempfile.NamedTemporaryFile(mode="w", dir=self.path.parent, delete=False) as handle:
                temporary = Path(handle.name)
                try:
                    json.dump(value, handle)
                    handle.flush()
                    os.fsync(handle.fileno())
                    os.replace(temporary, self.path)
                    # Persist the directory entry as well as the file content.
                    directory = os.open(self.path.parent, os.O_RDONLY)
                    try:
                        os.fsync(directory)
                    finally:
                        os.close(directory)
                finally:
                    temporary.unlink(missing_ok=True)
            self.closed, self.version = value["closed"], value["version"]
            return self.status()

    @contextmanager
    def configuration_scope(self, expected_version):
        """Hold admission while a trusted manager changes configuration of this profile only."""
        with self.lock:
            if not self.enabled or not self.closed or self.active or expected_version != self.version:
                raise AdmissionConflict("Profile must be drained at the expected version")
            yield
