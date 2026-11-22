"""mail-cli connector home: where credentials, tokens and policy live on this machine.

Pre-0.4 the connector kept everything in one scattered directory, ``~/.config/mail-cli``.
0.5+ keeps it in the machine substrate — ``$OWFA_HOME/connectors/mail-cli/homes/<id>/``
(``$OWFA_HOME`` defaults to ``~/.owfa``). mail-cli owns a single state directory, so it
uses the home itself and no component subdirectory.

The directory also carries the human-provided GCP OAuth client ``gcp.json`` (used by the
Gmail provider). It is the account owner's own OAuth client: it moves with the directory
when state is migrated, but it is **local-only** — it never enters any repository, and no
documentation or test here reads or copies it.

The layout, the resolution order and the migration rules are owned by the machine helper
``.owfa/scripts/owfa_home.py`` (``docs/home.md``); this module only asks that helper and
never assembles ``~/.owfa/connectors/...`` by hand.

Resolution precedence:

1. ``MAIL_CLI_HOME``             explicit override — use exactly this directory
2. the substrate helper          ``resolve_home("mail-cli")`` (the canonical default)
3. legacy ``~/.config/mail-cli`` only when no helper/workspace is found, or when the
                                 substrate home does not exist yet and the legacy
                                 directory still holds state. This fallback is loud:
                                 one stderr line names the explicit migration command,
                                 because a second silent home is the bug being fixed.

Migration is never a side effect. ``mail-cli home status`` inspects; ``mail-cli home
migrate [--dry-run]`` is the only thing that moves state — it moves (never copies) and
leaves a symlink at the legacy path so old absolute references keep resolving.
"""

# --- canonical connector bootstrap (docs/home.md ABI — keep byte-identical across connectors) ---
import importlib.util
import os
from pathlib import Path

HELPER_ENV = "OWFA_HOME_HELPER"   # explicit path to owfa_home.py


def _candidates():
    out = []
    env = os.environ.get(HELPER_ENV)
    if env:
        out.append(Path(env).expanduser())
    for start in (Path(__file__).resolve(), Path.cwd().resolve()):
        for parent in (start, *start.parents):
            out.append(parent / ".owfa" / "scripts" / "owfa_home.py")
    return out


def load_owfa_home():
    """Import the machine substrate helper, or None when this connector runs outside a workspace."""
    seen = set()
    for helper in _candidates():
        if helper in seen or not helper.is_file():
            continue
        seen.add(helper)
        spec = importlib.util.spec_from_file_location("owfa_home", helper)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    return None


# --- mail-cli specifics ------------------------------------------------------

import sys

CONNECTOR = "mail-cli"
PROG = "mail-cli"
ENV_OVERRIDE = "MAIL_CLI_HOME"          # explicit escape hatch: use exactly this directory
LEGACY_HOME = "~/.config/mail-cli"      # pre-0.4 scattered home — fallback only, never the target
_REQUIRED_HELPER = ("resolve_home", "resolve_root", "migrate_legacy_home", "connector_status")
_noticed = False


class MigrationError(RuntimeError):
    """The explicit migrate command cannot run (missing helper/legacy, conflicting override)."""


def legacy_dir() -> Path:
    """The pre-0.4 scattered home (``~/.config/mail-cli``); resolved on every call, never cached."""
    return Path(os.path.expanduser(LEGACY_HOME))


def helper_path():
    """Path of the substrate helper this process would import, or None."""
    for path in _candidates():
        if path.is_file():
            return path
    return None


def _helper():
    """The helper module when it implements the 0.5.2+ ABI this connector relies on."""
    module = load_owfa_home()
    if module is None or not all(hasattr(module, name) for name in _REQUIRED_HELPER):
        return None
    return module


def _notice(line: str) -> None:
    """One loud stderr line; repeated calls in one process stay quiet."""
    global _noticed
    if _noticed:
        return
    _noticed = True
    print(f"[{PROG} home] {line}", file=sys.stderr)


def _has_state(path: Path) -> bool:
    try:
        return path.is_dir() and any(path.iterdir())
    except OSError:
        return path.exists()


def _substrate_home(module) -> Path:
    return Path(module.resolve_home(CONNECTOR))


def _resolve() -> tuple[Path, str, object]:
    """(state directory, source, helper module) for this process. Creates nothing."""
    override = os.environ.get(ENV_OVERRIDE)
    if override:
        return Path(override).expanduser(), "env-override", None
    module = _helper()
    legacy = legacy_dir()
    if module is None:
        _notice(
            f"machine substrate helper (owfa_home.py) not found — keeping the pre-0.4 home "
            f"{legacy}; the substrate expects $OWFA_HOME/connectors/{CONNECTOR}/homes/<id>/ — "
            f"once the workspace plants the helper run `{PROG} home migrate --dry-run` then "
            f"`{PROG} home migrate`")
        return legacy, "legacy", None
    substrate = _substrate_home(module)
    legacy_is_real = legacy.is_dir() and not legacy.is_symlink()
    if legacy_is_real and not _has_state(substrate):
        _notice(
            f"legacy home {legacy} still holds the state and the substrate home {substrate} "
            f"does not exist yet — never migrate silently; inspect `{PROG} home status`, then "
            f"run `{PROG} home migrate --dry-run` and `{PROG} home migrate` "
            f"(gcp.json, the human-provided OAuth client, moves with the directory)")
        return legacy, "legacy", module
    if legacy_is_real and _has_state(substrate):
        _notice(
            f"both {legacy} and the substrate home {substrate} exist — using the substrate; "
            f"the legacy directory is a leftover to clean up by hand (never auto-deleted)")
    return substrate, "substrate", module


def state_dir() -> Path:
    """The directory this process reads/writes — resolved at run time, never built by hand."""
    return _resolve()[0]


def _migration_commands() -> dict:
    return {
        "dry_run": f"{PROG} home migrate --dry-run",
        "command": f"{PROG} home migrate",
    }


def status() -> dict:
    """Read-only view for ``mail-cli home status`` (safe anytime; creates nothing)."""
    path, source, module = _resolve()
    legacy = legacy_dir()
    out = {
        "ok": True,
        "connector": CONNECTOR,
        "source": source,
        "state_dir": str(path),
        "override_env": ENV_OVERRIDE,
        "override_value": os.environ.get(ENV_OVERRIDE) or None,
        "helper": str(helper_path()) if helper_path() else None,
        "legacy_home": str(legacy),
        "legacy_present": legacy.is_dir(),
        "legacy_is_symlink": legacy.is_symlink(),
        "migration": {**_migration_commands(), "helper_available": module is not None},
        "substrate": None,
    }
    if module is not None:
        try:
            substrate = module.connector_status(CONNECTOR)
        except Exception as exc:  # a broken helper must not turn a read-only status into a crash
            substrate = {"error": str(exc)}
        out["substrate"] = substrate
    return out


def _predict_target(module, root: str) -> Path:
    """What migrate_legacy_home would bind for this root (without mutating anything)."""
    bindings = module.load_bindings(CONNECTOR)
    norm = os.path.normpath(root)
    for binding in bindings:
        if os.path.normpath(binding.get("root", "")) == norm:
            return Path(binding["home"])
    if not bindings:
        return Path(module.home_dir(CONNECTOR, "default"))
    # a second (or later) binding gets a slug id allocated at migrate time
    return Path(module.homes_root(CONNECTOR)) / "<new-binding-id>"


def migrate(dry_run: bool = False, root: str = "") -> dict:
    """Explicit legacy -> substrate move. The ONLY code path that moves mail-cli state."""
    module = _helper()
    legacy = legacy_dir()
    if os.environ.get(ENV_OVERRIDE):
        raise MigrationError(
            f"{ENV_OVERRIDE} is set — an explicit override is honoured exactly, so there is "
            f"nothing to migrate; unset it first")
    if module is None:
        raise MigrationError(
            "machine substrate helper (owfa_home.py 0.5.2+) not found — this connector cannot "
            "perform the move without it; install/upgrade the workspace kernel to plant "
            ".owfa/scripts/owfa_home.py (or point OWFA_HOME_HELPER at it)")
    if legacy.is_symlink():
        return {
            "ok": True,
            "dry_run": dry_run,
            "already_migrated": True,
            "connector": CONNECTOR,
            "from": str(legacy),
            "to": str(legacy.resolve()),
            "note": "the legacy path is already a symlink into the substrate; nothing to move",
        }
    if not legacy.exists():
        raise MigrationError(f"no legacy home at {legacy} — nothing to migrate")

    # bind the workspace root, not whatever subdirectory the shell happens to be in:
    # the helper's own resolution (explicit --root > OWFA_ROOT > cwd reverse-lookup) first
    root = str(Path(root or module.resolve_root(CONNECTOR) or os.getcwd()).expanduser().resolve())
    target = _predict_target(module, root)
    try:
        entries = sorted(p.name for p in legacy.iterdir())
    except OSError as exc:
        raise MigrationError(f"cannot read the legacy home {legacy}: {exc}")
    if target.is_dir() and any(target.iterdir()):
        raise MigrationError(
            f"refusing to migrate: the target home {target} already exists and is not empty — "
            f"move its contents away or pick another workspace root first")

    if dry_run:
        out = {
            "ok": True,
            "dry_run": True,
            "connector": CONNECTOR,
            "from": str(legacy),
            "to": str(target),
            "root": root,
            "entries": entries,
            "changes": "would move (never copy) the legacy directory into the substrate and "
                       "leave a symlink at the legacy path; nothing was changed",
            "command": _migration_commands()["command"],
        }
        if "<new-binding-id>" in str(target):
            out["note"] = ("this root has no binding yet — a new homes/<id> is allocated at "
                           "migrate time; `to` is shown as a pattern")
        return out

    try:
        entry = module.migrate_legacy_home(CONNECTOR, str(legacy), root=root)
    except OSError as exc:
        raise MigrationError(f"the move failed ({exc}); the legacy directory is still in place — "
                             f"check permissions and that both paths are on one volume")
    path, source, _ = _resolve()
    return {
        "ok": True,
        "dry_run": False,
        "migrated": True,
        "connector": CONNECTOR,
        "from": str(legacy),
        "to": entry.get("home"),
        "binding": entry,
        "legacy_symlink": legacy.is_symlink(),
        "state_dir": str(path),
        "source": source,
        "note": "state was moved into the substrate and the legacy path left as a symlink "
                "(the symlink is a human cleanup item, never removed by the connector)",
    }
