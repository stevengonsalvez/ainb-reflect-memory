"""Exercise release-time identity migration without touching a remote marketplace."""
from pathlib import Path
import json
import shutil
import subprocess

import pytest

WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/release.yml"


@pytest.mark.skipif(shutil.which("jq") is None, reason="release migration needs jq")
@pytest.mark.parametrize("identity", ["reflect", "plugly-reflect-memory"])
def test_release_migrates_old_and_current_marketplace_entries(identity):
    result = _migrate([identity, "unrelated"])
    assert result.returncode == 0, result.stderr
    doc = json.loads(result.stdout)
    assert doc["plugins"][0] == {
        "name": "plugly-reflect-memory",
        "source": {
            "source": "github", "repo": "stevengonsalvez/plugly-reflect-memory",
            "path": "plugin", "ref": "v5.2.6",
        },
    }
    assert doc["plugins"][1]["name"] == "unrelated"
    assert doc["renames"] == {"reflect": "plugly-reflect-memory"}


@pytest.mark.skipif(shutil.which("jq") is None, reason="release migration needs jq")
@pytest.mark.parametrize("identities", [[], ["reflect", "plugly-reflect-memory"]])
def test_release_rejects_missing_or_duplicate_managed_identity(identities):
    assert _migrate(identities).returncode != 0


def _migrate(identities):
    workflow = WORKFLOW.read_text()
    start = workflow.index("            ([.plugins[]")
    end = workflow.index("\n          ' \"$FILE\"", start)
    program = workflow[start:end]
    doc = {"plugins": [
        {"name": name, "source": {
            "source": "github", "repo": "stevengonsalvez/ainb-reflect-memory",
            "path": "plugin", "ref": "v5.2.5",
        }} for name in identities
    ]}
    return subprocess.run(
        ["jq", "--arg", "name", "plugly-reflect-memory", "--arg", "old", "reflect",
         "--arg", "repo", "stevengonsalvez/plugly-reflect-memory",
         "--arg", "ref", "v5.2.6", program],
        input=json.dumps(doc), text=True, capture_output=True, check=False,
    )
