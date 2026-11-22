# Upgrade note: connector home -> machine substrate (mail-cli 0.5, package 0.3)

Not a kernel staging declaration (the file name does not match `x-to-y.md` on purpose):
the state being moved lives on the **machine**, outside any workspace, so the move cannot
be verified or performed by the workspace-only migration machinery. This is the human
cutover recipe; the connector exposes it through explicit commands, never as a side effect.

## What changed

| | pre-0.4 / connector <= 0.4 | connector 0.5 |
|---|---|---|
| state directory | `~/.config/mail-cli/` (scattered) | `$OWFA_HOME/connectors/mail-cli/homes/<id>/` (`$OWFA_HOME` defaults to `~/.owfa`) |
| resolution | hardcoded | `.owfa/scripts/owfa_home.py` (`.owfa/docs/home.md` ABI), at run time |
| explicit override | none | `MAIL_CLI_HOME=/path/to/dir` |
| old path | the only path | loud fallback only (one stderr line), plus `home migrate` |

Everything inside the directory moves together: one `<account>.json` token per profile
(graph / gmail / imap), `policy.json` (send gate, absent = deny), and the **human-provided
GCP OAuth client `gcp.json`**.

`gcp.json` is the account owner's own OAuth client: the move carries it unchanged, and it
stays local-only — never in any repository, never copied by the tooling, never printed.
If the legacy directory is migrated, delete nothing by hand: the file lives in the new home
through the symlink.

## Cutover (per machine, once — the machine owner does this)

```bash
mail-cli home status            # source=legacy, state_dir=~/.config/mail-cli, substrate home shown
mail-cli home migrate --dry-run # entries + exact from -> to; changes nothing
mail-cli home migrate           # move (never copy) + bind this workspace root + symlink left behind
mail-cli home status            # source=substrate, legacy_is_symlink=true
mail-cli profiles               # accounts still enumerate from the moved directory
mail-cli auth status --account <name>   # tokens still refresh (the credentials moved with the directory)
```

Equivalent raw helper command (when the connector is not on PATH):

```bash
python3 .owfa/scripts/owfa_home.py migrate mail-cli --legacy ~/.config/mail-cli
```

- The legacy path stays as a **symlink** to the new home so old absolute references keep
  resolving; removing that symlink is a human cleanup act, never the kernel's or the
  connector's.
- Do not migrate twice: the second run reports `already_migrated`.
- `home migrate` refuses when `MAIL_CLI_HOME` is set, when there is no legacy directory,
  or when the target home already holds state — resolve those by hand.
- Multiple workspaces: the first `home migrate` binds the workspace it is run from; bind
  further roots via `mail-cli home migrate --root <ws>` (same machine, one home per
  binding — two workspaces must not drain one token store).
