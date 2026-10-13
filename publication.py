"""Private publication marker; session routing changes without deleting conversations."""

import json
import re
from pathlib import Path


def read_publication(home):
    path = Path(home) / ".pipefacil-publication.json"
    if not path.exists():
        return None
    if path.is_symlink():
        raise ValueError("Invalid publication marker")
    value = json.loads(path.read_text())
    if (type(value.get("revision")) is not int or value["revision"] < 1
            or not re.fullmatch(r"[a-f0-9]{64}", str(value.get("contentHash", "")))):
        raise ValueError("Invalid publication marker")
    skills = value.get("skills", [])
    if not isinstance(skills, list) or any(not isinstance(name, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,79}", name) for name in skills):
        raise ValueError("Invalid publication skills")
    return value


def skill_paths(publication):
    return ["skills/pipefacil-commercial/" + name + "/SKILL.md" for name in (publication or {}).get("skills", [])]


def skill_prompt(publication):
    paths = skill_paths(publication)
    if not paths:
        return ""
    return ("\n\nSkills comerciais publicadas para este agente. Consulte quando pertinente com "
            "pipefacil_read_profile_file(path). Estes documentos não concedem novas ferramentas:\n" + "\n".join(paths))
