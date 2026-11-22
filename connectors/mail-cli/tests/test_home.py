"""Connector home resolution + the explicit legacy migration (docs/home.md ABI).

Every test runs the real CLI in a subprocess with HOME / OWFA_HOME / OWFA_HOME_HELPER
redirected into a temporary directory — the developer's own ~/.config/mail-cli and
~/.owfa are never read or written (the human-provided gcp.json stays exactly where it is).
The substrate helper is the one landed with the framework (OWFA_HOME_HELPER, or the
sibling owfa checkout); without it the helper-dependent tests skip instead of failing on
a machine that only has the connector.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PKG_DIR = Path(__file__).resolve().parents[1]
SCRIPT = PKG_DIR / "mail-cli"
HELPER = Path(os.environ.get("OWFA_HOME_HELPER")
              or os.path.expanduser("~/dev/owfa/.owfa/scripts/owfa_home.py"))
needs_helper = unittest.skipUnless(
    HELPER.is_file(), f"machine substrate helper not found at {HELPER} "
                      f"(set OWFA_HOME_HELPER=/path/to/owfa_home.py)")


class HomeTestCase(unittest.TestCase):
    """Temp HOME + temp OWFA_HOME + a temp cwd outside any workspace."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.home = root / "home"
        self.owfa_home = root / "owfa"
        self.work = root / "work"
        for d in (self.home, self.work):
            d.mkdir(parents=True)
        self.legacy = self.home / ".config" / "mail-cli"
        self.substrate = self.owfa_home / "connectors" / "mail-cli" / "homes" / "default"

    # -- fixtures -----------------------------------------------------

    def make_workspace(self) -> Path:
        """A disposable workspace carrying the connector tree and a planted helper."""
        ws = Path(self._tmp.name) / "ws"
        shutil.copytree(PKG_DIR, ws / "connectors" / "mail-cli",
                        ignore=shutil.ignore_patterns("__pycache__"))
        (ws / ".owfa" / "scripts").mkdir(parents=True)
        shutil.copyfile(HELPER, ws / ".owfa" / "scripts" / "owfa_home.py")
        return ws

    def make_legacy(self) -> Path:
        self.legacy.mkdir(parents=True)
        (self.legacy / "gcp.json").write_text('{"client_id": "fake"}\n', encoding="utf-8")
        (self.legacy / "work.json").write_text('{"provider": "imap"}\n', encoding="utf-8")
        (self.legacy / "policy.json").write_text("{}\n", encoding="utf-8")
        return self.legacy

    def env(self, **extra) -> dict:
        env = {k: v for k, v in os.environ.items()
               if k not in ("OWFA_HOME", "OWFA_HOME_HELPER", "OWFA_ROOT", "MAIL_CLI_HOME")}
        env["HOME"] = str(self.home)
        env["USERPROFILE"] = str(self.home)      # Windows equivalent of HOME
        env["PYTHONPATH"] = str(PKG_DIR)
        env.update({k: str(v) for k, v in extra.items()})
        return env

    def run_cli(self, *args, **extra):
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            cwd=str(self.work), env=self.env(**extra),
            capture_output=True, text=True, timeout=60)

    def run_py(self, code: str, **extra):
        return subprocess.run(
            [sys.executable, "-c", code],
            cwd=str(self.work), env=self.env(**extra),
            capture_output=True, text=True, timeout=60)

    def payload(self, proc) -> dict:
        lines = [ln for ln in proc.stdout.strip().splitlines() if ln.strip()]
        self.assertTrue(lines, f"no JSON on stdout (stderr: {proc.stderr})")
        return json.loads(lines[-1])

    def substrate_env(self) -> dict:
        return {"OWFA_HOME": self.owfa_home, "OWFA_HOME_HELPER": HELPER}


class ResolutionTests(HomeTestCase):
    @needs_helper
    def test_substrate_is_the_default(self):
        proc = self.run_py("from mailcli import home; print(home.state_dir())",
                           **self.substrate_env())
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(Path(proc.stdout.strip()), self.substrate)
        self.assertNotIn("legacy home", proc.stderr)          # canonical path is quiet
        self.assertFalse(self.legacy.exists())                # nothing created in the old home
        self.assertFalse(self.substrate.exists())             # resolution creates nothing either

    @needs_helper
    def test_walk_up_finds_the_planted_workspace_helper(self):
        """The primary ABI path: no OWFA_HOME_HELPER, helper planted in the workspace tree."""
        ws = self.make_workspace()
        neutral = Path(self._tmp.name) / "neutral"
        neutral.mkdir()
        proc = subprocess.run(
            [sys.executable, str(ws / "connectors" / "mail-cli" / "mail-cli"), "home", "status"],
            cwd=str(neutral), env=self.env(OWFA_HOME=self.owfa_home),
            capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = self.payload(proc)
        self.assertEqual(out["source"], "substrate")
        self.assertEqual(Path(out["state_dir"]), self.substrate)
        self.assertEqual(Path(out["helper"]), ws / ".owfa" / "scripts" / "owfa_home.py")
        self.assertNotIn("not found", proc.stderr)

    @needs_helper
    def test_env_override_wins_over_substrate_and_legacy(self):
        explicit = self.work / "explicit-state"
        self.make_legacy()
        proc = self.run_py("from mailcli import home; print(home.state_dir())",
                           **self.substrate_env(), MAIL_CLI_HOME=explicit)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(Path(proc.stdout.strip()), explicit)
        self.assertNotIn("legacy home", proc.stderr)

    def test_legacy_fallback_without_helper_is_loud(self):
        self.make_legacy()
        proc = self.run_py("from mailcli import home; print(home.state_dir())")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(Path(proc.stdout.strip()), self.legacy)
        self.assertIn("not found", proc.stderr)
        self.assertIn("home migrate", proc.stderr)            # the migration command is named
        self.assertTrue((self.legacy / "gcp.json").is_file())

    @needs_helper
    def test_helper_but_unmigrated_legacy_keeps_legacy_loudly(self):
        """A helper alone must not silently strand the old state or create a second home."""
        self.make_legacy()
        proc = self.run_py("from mailcli import home; print(home.state_dir())",
                           **self.substrate_env())
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(Path(proc.stdout.strip()), self.legacy)
        self.assertIn("never migrate silently", proc.stderr)

    @needs_helper
    def test_common_cfg_dir_follows_the_substrate(self):
        proc = self.run_py("from mailcli import common; print(common.cfg_dir())",
                           **self.substrate_env())
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(Path(proc.stdout.strip()), self.substrate)

    @needs_helper
    def test_status_reports_source_legacy_and_substrate_view(self):
        self.make_legacy()
        out = self.payload(self.run_cli("home", "status", **self.substrate_env()))
        self.assertTrue(out["ok"])
        self.assertEqual(out["source"], "legacy")
        self.assertEqual(Path(out["state_dir"]), self.legacy)
        self.assertEqual(Path(out["legacy_home"]), self.legacy)
        self.assertTrue(out["legacy_present"])
        self.assertFalse(out["legacy_is_symlink"])
        self.assertEqual(Path(out["helper"]), HELPER)
        self.assertTrue(out["migration"]["helper_available"])
        self.assertEqual(Path(out["substrate"]["home"]), self.substrate)
        self.assertEqual(out["substrate"]["exclusivity_violations"], [])
        self.assertEqual(out["substrate"]["stale_bindings"], [])

    @needs_helper
    def test_existing_substrate_home_wins_over_a_leftover_legacy_dir(self):
        self.make_legacy()
        self.substrate.mkdir(parents=True)
        (self.substrate / "work.json").write_text("{}", encoding="utf-8")
        proc = self.run_py("from mailcli import home; print(home.state_dir())",
                           **self.substrate_env())
        self.assertEqual(Path(proc.stdout.strip()), self.substrate)
        self.assertIn("leftover", proc.stderr)                # reported, never auto-deleted

    def test_status_without_helper_names_the_gap(self):
        self.make_legacy()
        out = self.payload(self.run_cli("home", "status"))
        self.assertEqual(out["source"], "legacy")
        self.assertIsNone(out["helper"])
        self.assertIsNone(out["substrate"])
        self.assertFalse(out["migration"]["helper_available"])

    @needs_helper
    def test_status_is_read_only(self):
        self.make_legacy()
        self.run_cli("home", "status", **self.substrate_env())
        self.assertFalse(self.substrate.exists())
        self.assertFalse((self.owfa_home / "connectors" / "mail-cli" / "bindings.json").exists())

    @needs_helper
    def test_bootstrap_loader_imports_the_helper(self):
        proc = self.run_py(
            "from mailcli import home; m = home.load_owfa_home(); "
            "print(bool(m and hasattr(m, 'resolve_home') and hasattr(m, 'migrate_legacy_home')))",
            **self.substrate_env())
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), "True")


class MigrateTests(HomeTestCase):
    @needs_helper
    def test_dry_run_prints_the_move_and_changes_nothing(self):
        self.make_legacy()
        out = self.payload(self.run_cli("home", "migrate", "--dry-run", **self.substrate_env()))
        self.assertTrue(out["ok"])
        self.assertTrue(out["dry_run"])
        self.assertEqual(Path(out["from"]), self.legacy)
        self.assertEqual(Path(out["to"]), self.substrate)
        self.assertEqual(out["entries"], ["gcp.json", "policy.json", "work.json"])
        # nothing moved, nothing created
        self.assertFalse(self.legacy.is_symlink())
        self.assertTrue((self.legacy / "gcp.json").is_file())
        self.assertFalse(self.substrate.exists())
        self.assertFalse((self.owfa_home / "connectors" / "mail-cli" / "bindings.json").exists())

    @needs_helper
    def test_real_migrate_moves_links_and_binds(self):
        self.make_legacy()
        proc = self.run_cli("home", "migrate", **self.substrate_env())
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = self.payload(proc)
        self.assertTrue(out["migrated"])
        self.assertTrue(out["legacy_symlink"])
        self.assertEqual(Path(out["to"]), self.substrate)
        # moved, never copied: the human-provided OAuth client rides along, exactly once
        self.assertTrue((self.substrate / "gcp.json").is_file())
        self.assertTrue((self.substrate / "work.json").is_file())
        self.assertTrue(self.legacy.is_symlink())
        self.assertEqual(self.legacy.resolve(), self.substrate.resolve())
        bindings = json.loads((self.owfa_home / "connectors" / "mail-cli" / "bindings.json")
                              .read_text(encoding="utf-8"))["bindings"]
        self.assertEqual(len(bindings), 1)
        self.assertEqual(bindings[0]["id"], "default")
        self.assertEqual(Path(bindings[0]["root"]), self.work.resolve())
        resolved = self.run_py("from mailcli import home; print(home.state_dir())",
                               **self.substrate_env())
        self.assertEqual(Path(resolved.stdout.strip()), self.substrate)
        self.assertNotIn("legacy home", resolved.stderr)

    @needs_helper
    def test_profiles_enumerates_the_migrated_store(self):
        self.make_legacy()
        self.run_cli("home", "migrate", **self.substrate_env())
        out = self.payload(self.run_cli("profiles", **self.substrate_env()))
        self.assertTrue(out["ok"])
        names = [p["account"] for p in out["profiles"]]
        self.assertEqual(names, ["work"])              # gcp.json / policy.json are not accounts

    @needs_helper
    def test_second_migrate_is_a_no_op(self):
        self.make_legacy()
        self.run_cli("home", "migrate", **self.substrate_env())
        out = self.payload(self.run_cli("home", "migrate", **self.substrate_env()))
        self.assertTrue(out["already_migrated"])
        self.assertEqual(Path(out["to"]), self.substrate)

    def test_migrate_without_helper_fails_closed(self):
        self.make_legacy()
        proc = self.run_cli("home", "migrate")
        self.assertEqual(proc.returncode, 1)
        out = self.payload(proc)
        self.assertFalse(out["ok"])
        self.assertIn("helper", out["error"])
        self.assertFalse(self.legacy.is_symlink())            # the state never moved

    @needs_helper
    def test_migrate_without_legacy_errors(self):
        proc = self.run_cli("home", "migrate", **self.substrate_env())
        self.assertEqual(proc.returncode, 1)
        self.assertIn("nothing to migrate", self.payload(proc)["error"])

    @needs_helper
    def test_migrate_refuses_an_explicit_override(self):
        self.make_legacy()
        proc = self.run_cli("home", "migrate", **self.substrate_env(),
                            MAIL_CLI_HOME=self.work / "pinned")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("MAIL_CLI_HOME", self.payload(proc)["error"])
        self.assertFalse(self.legacy.is_symlink())

    @needs_helper
    def test_migrate_refuses_a_non_empty_target(self):
        self.make_legacy()
        self.substrate.mkdir(parents=True)
        (self.substrate / "keepme").write_text("x", encoding="utf-8")
        proc = self.run_cli("home", "migrate", **self.substrate_env())
        self.assertEqual(proc.returncode, 1)
        self.assertIn("already exists", self.payload(proc)["error"])
        self.assertFalse(self.legacy.is_symlink())


if __name__ == "__main__":
    unittest.main()
