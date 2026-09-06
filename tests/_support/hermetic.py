"""One hermetic environment builder for every test that shells out to reflect.

Shared by tests/compat and the recall eval harness so both isolate the same
things the same way: the KB (GLOBAL_LEARNINGS_PATH), reflect's state dir, the
qmd index (XDG_CACHE_HOME) and the model daemon. The embedding model caches
(HF_HOME, SENTENCE_TRANSFORMERS_HOME) are pinned to their real locations
unless already set, otherwise the XDG override forces a 420 MB re-download.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

SYSTEM_DIRS = ("/usr/bin", "/bin", "/usr/sbin", "/sbin")


def minimal_path(*extra_tools: str) -> str:
    """This interpreter's bin dir, the dirs of the named tools, and the system
    dirs. Nothing from the operator's own PATH leaks in unless named."""
    dirs = [str(Path(sys.executable).parent)]
    for tool in extra_tools:
        found = shutil.which(tool)
        if found and str(Path(found).parent) not in dirs:
            dirs.append(str(Path(found).parent))
    for d in SYSTEM_DIRS:
        if d not in dirs:
            dirs.append(d)
    return os.pathsep.join(dirs)


def hermetic_env(
    *,
    kb_dir: Path,
    state_dir: Path,
    cache_home: Path,
    base: dict[str, str] | None = None,
    home: Path | None = None,
    path: str | None = None,
) -> dict[str, str]:
    """Environment for a subprocess that must not touch the operator's data.

    ``base`` is the starting environment (``os.environ`` for the eval
    harness, an empty dict for the compat gate). ``home`` and ``path``
    override HOME and PATH when given.
    """
    env = dict(base or {})
    env["GLOBAL_LEARNINGS_PATH"] = str(kb_dir)
    env["REFLECT_STATE_DIR"] = str(state_dir)
    env["XDG_CACHE_HOME"] = str(cache_home)
    # The compat gate (base={}) gets the daemon off; the eval harness passes
    # os.environ and may opt out with REFLECT_NO_DAEMON=0 so a shared daemon
    # keeps the model loaded across its subprocesses.
    env.setdefault("REFLECT_NO_DAEMON", "1")
    real_home = Path(os.environ.get("HOME", str(Path.home())))
    env.setdefault("HF_HOME", os.environ.get("HF_HOME", str(real_home / ".cache" / "huggingface")))
    env.setdefault(
        "SENTENCE_TRANSFORMERS_HOME",
        os.environ.get(
            "SENTENCE_TRANSFORMERS_HOME",
            str(real_home / ".cache" / "torch" / "sentence_transformers"),
        ),
    )
    env.setdefault("TOKENIZERS_PARALLELISM", "false")
    if home is not None:
        env["HOME"] = str(home)
    if path is not None:
        env["PATH"] = path
    for key in ("LANG", "LC_ALL"):
        env.setdefault(key, "C.UTF-8")
    for key in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "TMPDIR", "HF_HUB_OFFLINE"):
        if key in os.environ:
            env.setdefault(key, os.environ[key])
    return env
