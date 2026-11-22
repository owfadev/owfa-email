# mail-cli

Connector for the wiki email domain (multi-provider: Microsoft Graph / Gmail API / IMAP), OAuth2 device flow or authorization-code login. Contract: `.owfa/plugins/domains/email/`.

## Discipline

- Read side is read-only: fetching never implicitly marks messages as read (Graph GET / IMAP `BODY.PEEK`), never moves, archives, or deletes
- Credentials, tokens and the send policy stay on the local machine in the connector state dir (0600) — `$OWFA_HOME/connectors/mail-cli/homes/<id>/`, `mail-cli home status` — and never enter any repo; the human-provided GCP OAuth client `gcp.json` lives in that same directory, moves with it, and stays local-only (never in any repo, never copied by the tooling)
- Send policy gate (see below): send-type operations are governed at the connector layer by a manually configured policy

## State and migration (machine substrate)

No state lives in a repository. OAuth tokens (one `<account>.json` per profile), the send
policy (`policy.json`) and the human-provided GCP OAuth client (`gcp.json`) live in the
**connector state dir**, resolved at run time through the machine helper
`.owfa/scripts/owfa_home.py` (ABI: `.owfa/docs/home.md`) rather than assembled by hand:

```
$OWFA_HOME/connectors/mail-cli/homes/<id>/        # default: $OWFA_HOME=~/.owfa, <id>=default
```

Resolution order:

1. `MAIL_CLI_HOME` — explicit override: use exactly this directory (compatibility escape hatch)
2. the substrate helper — `resolve_home("mail-cli")`, the canonical default
3. the pre-0.4 scattered home `~/.config/mail-cli` — only when no helper/workspace is found,
   or when the substrate home does not exist yet and the old directory still holds state.
   This fallback is **loud**: one stderr line names the migration command, and no second
   home is created silently.

```
mail-cli home status                  # read-only: source, state_dir, legacy presence, bindings + hazards
mail-cli home migrate --dry-run       # show the planned move (entries, from -> to); changes nothing
mail-cli home migrate                 # move ~/.config/mail-cli into the substrate, bind this root, leave a symlink
```

`home migrate` moves, never copies (credentials must exist once), carries `gcp.json` with the
directory as-is, leaves a symlink at the legacy path so old absolute references keep
resolving, and refuses when `MAIL_CLI_HOME` is set, when the legacy directory is absent, or
when the target home already holds state. The move is the machine owner's cutover — never
run it on a machine you do not own.

## Usage

`--account` is the plugin-contract account (= `wiki/email/<account>/` directory name = profile name).

```
mail-cli auth start  --account <name> [--provider graph|gmail] [--send]  # device flow (graph) / loopback (gmail); graph by default requests only the Mail.Read read-only scope (the read-only consent surface usually needs no admin approval), --send includes write/send
mail-cli auth setup  --account <name> --user <address> --auth-code <code> [--imap-host H] [--smtp-host H]  # imap (e.g. 163)
mail-cli auth status --account <name>
mail-cli profiles                                 # account list (digest-page traversal entry)
mail-cli home [status|migrate] [--dry-run]        # connector state dir + the explicit legacy migration
mail-cli folders --account <name> [--all]           # folders/labels (imap auto-decodes Chinese UTF-7 folder names)
mail-cli fetch --account <name> [--folder F] [--since D | --days 7] [--from A]
                [--unread] [--headers] [--limit 30] [--next <url>]
mail-cli read --account <name> --id <id> [--text]   # single message body; --text extracts plain text
mail-cli search --account <name> --query <q> [--limit 10]
mail-cli attach ls  --account <name> --id <id>
mail-cli attach get --account <name> --id <id> --att <aid> [--name filename] --dest <dir>
mail-cli draft create --account <name> --to A[,B] [--cc] [--subject S] [--body T | --body-file F]
                       [--html] [--attach path]... [--reply-to <msg-id>]   # replies auto-carry thread headers
mail-cli draft list/show/delete --account <name> --id <draft-id> (list takes no --id)
mail-cli send --account <name> --id <draft-id> [--yes]
```

Output is always single-line JSON. graph/gmail tokens auto-renew on expiry.

## Send policy (manually configured; the connector only reads it)

`policy.json` in the connector state dir (`mail-cli home status`), hand-edited by a human, **absent = deny everything**:

```json
{"<account>": {"send": "deny|confirm|auto", "auto_allow": ["whitelisted addresses"]}}
```

- `deny`: send is always refused (drafts can still be created)
- `confirm`: TTY enter-to-confirm; non-TTY requires `--yes` (= the user has already explicitly agreed in conversation; the agent only executes the consent already given)
- `auto`: pass through directly; with `auto_allow` set, only whitelisted recipients pass, everything else falls back to confirm

## Provider routes and measured pitfalls

| provider | auth | route | known pitfalls |
|---|---|---|---|
| graph (Microsoft/school) | device flow | Graph API v1.0 | `internetMessageHeaders` can only be `$select`ed, not `$expand`ed; school tenants may need admin approval for write permissions |
| gmail | loopback (ssh -L tunnel to receive the callback) | Gmail API v1 | **device flow does not grant Gmail scopes** (invalid_scope, even for TV clients); requires a desktop client + a temporary local HTTP service |
| imap (163 etc.) | authorization code (auth setup) | IMAP4_SSL + SMTP_SSL, standard library | **163 requires sending IMAP ID to self-identify, otherwise select reports Unsafe Login**; Chinese folder names are modified UTF-7 (already decoded); Chinese search terms fall back to client-side filtering (imaplib parameters are ascii-only) |

## Dependencies

None (Python 3 standard library). graph borrows the public Microsoft Graph PowerShell client; gmail needs your own GCP OAuth client (`gcp.json`).

## Changelog

The connector ships no version constant; from this release its line follows the package
release (`email` 0.3). Entries 0.4 and older carry the connector's earlier own numbering.

- 0.3 2026-11-22: connector-home substrate — tokens/policy/GCP client resolve through the machine helper `.owfa/scripts/owfa_home.py` into `$OWFA_HOME/connectors/mail-cli/homes/<id>/` instead of the scattered `~/.config/mail-cli`; new `home status` + `home migrate [--dry-run]` (explicit, move-never-copy, binds the root, leaves the legacy symlink); `MAIL_CLI_HOME` explicit override; the legacy home stays a loud fallback with a stderr migration notice; gcp.json keeps riding along and stays local-only; 19 new tests run the CLI against temp HOME/OWFA_HOME (the real mailbox state is never touched), including the primary walk-up path with a helper planted inside a disposable workspace; declared `compat: ">= 0.5.2"` (the helper ABI this rides on)
- 0.4 2026-10-05: graph least privilege — default scope narrowed to `Mail.Read offline_access` (reading mail no longer triggers the write/send consent surface and the admin approval that follows), `auth start --send` explicitly requests the broad scope; token records the granted scope (refresh preserves the surface), draft create/delete and send are rejected early under read-only credentials (token_read_only)
- 0.3 2026-10-05: multi-provider (graph/gmail/imap); send policy gate policy.json (deny/confirm/auto + whitelist); full draft suite and send (graph createReply / gmail threadId / imap In-Reply-To thread headers); 163 IMAP ID and UTF-7 folder decoding; gmail loopback auth flow
- 0.2 2026-10-05: multi-account command surface — `--account` aligned with the plugin contract (was `--profile`); added profiles / folders / attach ls·get; fetch gained `--folder/--since/--from/--unread/--headers` and pagination; read gained `--text` plain-text extraction and message-id/references; errors became JSON output
- 0.1 2026-10-05: initial setup — auth / fetch / read / search, single-line JSON, auto-renewal
