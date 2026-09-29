# MT4 Remote Login Agent

A local, source-auditable Windows agent that logs into MetaTrader 4 from a Slack
slash command.

Someone in your Slack workspace types:

```text
/mt4 A 123456
```

`A` is an **Account alias** you chose in the local Web Admin — not a real login id.
`123456` is the credential for this one attempt. The agent finds the matching MT4
terminal on the machine it runs on, fills in the saved login id and server together
with that credential, and replies to the person who asked.

The agent runs on your own Windows machine. It needs no public server, no public
inbound port and no database.

> **Honest status.** This is a real program with a real test suite, and the MT4
> automation has been exercised on a real Windows machine with a real broker
> terminal. Not everything has been: the full Slack end-to-end path, group login, and
> some of the newer adapters have not been confirmed on hardware. Anything that needs
> hardware says so, and reports `WINDOWS_REAL_TEST_REQUIRED` instead of claiming a
> pass. See [Known limitations](#known-limitations).

## Contents

- [What it can do](#what-it-can-do)
- [Quick start](#quick-start)
- [Commands](#commands)
- [How the credential is handled](#how-the-credential-is-handled)
- [Who can do what](#who-can-do-what)
- [Running two terminals at once](#running-two-terminals-at-once)
- [Adding a new broker or build](#adding-a-new-broker-or-build)
- [Windows Test](#windows-test)
- [Documentation](#documentation)
- [Known limitations](#known-limitations)
- [Development](#development)
- [Data and files](#data-and-files)
- [Stopping, updating, uninstalling](#stopping-updating-uninstalling)
- [Screenshots](#screenshots)

## What it can do

| Capability | Notes |
|---|---|
| Slack slash command → local MT4 login | Socket Mode, so no inbound port |
| **Multiple accounts**, one person or several | Each gets its own alias, its own terminal install folder, its own working directory |
| **Account Duplicate** | Copy a configured Account for a second account at the same broker and build |
| **Automatic login-dialog opening** | Drives the terminal's own menu command; no coordinates, no blind keypresses |
| **UIA first, Win32 fallback** | UI Automation where the build exposes it; native Win32 control ids where it does not |
| **Win32 Inspector** | Read a new broker's dialog and propose its configuration, instead of hand-copying control ids |
| **Windows Test** | A guided, phase-by-phase acceptance run that refuses to claim what it did not verify |
| **History** | Who asked for what, when, and how it went — with no credential in it |
| **Per-user Account bindings** | In one workspace, each person operates only the aliases bound to them |
| **Private results** | Completion messages are delivered to the requester, never to the channel |

## Quick start

1. **Copy the project folder** to your Windows machine.
2. **Install Python 3.12** (recommended) or 3.11, with the Python Launcher `py.exe`.
3. Run [`install.bat`](install.bat). It installs the dependencies and runs a
   self-check.
4. Run [`start.bat`](start.bat) and note the **local admin token** it prints.
5. Open `http://127.0.0.1:8765` and enter the token. If the port is taken, the console
   tells you and you can change it in `settings.json` or pass `--port`.
6. In **Accounts**, add an account. Give it an `alias` such as `A` — that is the name
   used in Slack, not the real login id. Fill in the terminal path and the process
   working directory.
7. In **Slack**, create an app from
   [`slack-app-manifest.yaml`](slack-app-manifest.yaml), then paste the `xapp-`
   app-level token, the `xoxb-` bot token and your Slack Member ID, and save.
8. In Slack, run:

   ```text
   /mt4 A 123456
   /mt4 status
   ```

For the Slack side in detail, including the App Home Messages tab, see
**[docs/SLACK-SETUP.md](docs/SLACK-SETUP.md)**.

To connect a second broker or a different MT4 build, see
**[docs/NEW-MT4-ADAPTER.md](docs/NEW-MT4-ADAPTER.md)**.

## Commands

```text
/mt4 <alias-or-group> <credential>
/mt4 status
```

- `<alias-or-group>` is an **Account alias** (or a Group name). It is the identifier,
  never the login id.
- `<credential>` is the **password or one-time code only**. Do not include the login
  id — the agent already knows it from the Account.

The credential is opaque. It may contain spaces and symbols; the agent treats
everything after the first token as the credential, so a password with spaces works.

```text
/mt4 A hunter2correct-horse
/mt4 B 483920
```

`/mt4 status` reports the caller's own assigned Accounts, and nothing about anybody
else's.

## How the credential is handled

A credential passes from Slack into memory, into the login form, and nowhere else.

It is **not** written to the log at any level, not to `history.jsonl`, not to
`report.json` or `report.html`, not to `settings.json` or `secrets.json`, not to a
temporary file, and not to a command line where the process list could show it.

That rests on three things: no code path ever formats a credential into a message; the
credential is registered as a live secret for the duration of the operation and every
rendered value is scrubbed against it; and a key-based scrubber removes anything that
looks like a credential from log and report text. The full threat model, including a
known limitation for very short credentials, is in
**[SECURITY.md](SECURITY.md)**.

The Web Admin is bound to **loopback only** and every mutating request needs the admin
token.

## Who can do what

Two independent gates, both configured in Web Admin → **Slack**:

1. **Allowed Slack User IDs** — who may talk to the agent at all.
2. **Account bindings** — which Account aliases each of those people may operate.

The second is **fail-closed**: an allowed person with no binding can operate
**nothing**. Being added to the allowlist never grants access to every existing
Account, so onboarding a colleague cannot silently hand them your trading accounts.

A worked example — two people, one workspace:

```
U_A -> [A]
U_B -> [B]
```

Asking for an alias you do not own, and asking for an alias that does not exist,
produce the **same reply**. Otherwise the reply itself would let anyone discover which
aliases exist. A refused request is rejected before the queue, before MT4, and before
any credential is typed, so it cannot consume a one-time code.

Completion results and errors are delivered to the requester only — ephemeral in a
channel, a plain message in a DM.

> Two people in the same workspace can still see **each other's Slack profile** in the
> member directory. That is Slack's surface. What the agent guarantees is that each
> person cannot list, address or observe another person's MT4 Accounts, cannot log in
> against them, and cannot read their results.

## Running two terminals at once

MetaTrader's own guidance is to install a separate copy into a separate folder for
each concurrently running terminal, and that is also what the agent needs: it tells
terminals apart by executable path and process working directory.

- give each concurrently running terminal its **own install folder**
- give each Account its own `terminal_path` and its own `profile_path`
  (`profile_path` is the **process working directory**, not the MT4 data folder)

Two enabled Accounts may not resolve to the same terminal. If you try, the agent
refuses and explains why, rather than letting one person's login collide with
another's. A disabled draft may share its source's paths while you set it up.

## Adding a new broker or build

**Same broker, same build, second account** → use **Account → Duplicate**. Every
selector is still correct, and it copies the Win32 configuration, which is the tedious
part.

**Different broker, or a different build** → use **Win32 Inspector**:

```
New Account  →  start that MT4  →  Windows Test: Detect Win32
             →  review the proposal  →  Apply  →  Real Login test
```

`Detect Win32` reads the target's own login dialog, opens it through the terminal's
menu if needed, and proposes a configuration with a confidence per field. A field that
cannot be resolved uniquely is reported as unconfirmed **with no value offered** —
nothing is guessed — and Apply stays unavailable until every required field is
confirmed. It reads control metadata only, never the value of an editable field, and it
writes nothing until you apply.

Details: **[docs/NEW-MT4-ADAPTER.md](docs/NEW-MT4-ADAPTER.md)**.

## Windows Test

A guided acceptance run in the Web Admin, one step at a time. It refuses to claim
success it did not observe: a login is only reported successful when a window matching
your anchored success-title expression is actually seen.

Step cards mean:

| Card | Meaning |
|---|---|
| `FAIL` | something blocking failed |
| `WARN` | a real warning or degradation; the flow could still continue |
| `PASS` / `READY` | the automatic checks passed |
| `MANUAL` rows | a decision for you to make — shown as a count, never as a failure |

Optional destructive checks (system time, UAC, killing a worker, two instances) are
marked manual and never run unattended.

A full walkthrough, from a blank machine to acceptance, is in
**[docs/WINDOWS-TEST-GUIDE.md](docs/WINDOWS-TEST-GUIDE.md)**.

## Documentation

| Document | Read it for |
|---|---|
| **[docs/WINDOWS-TEST-GUIDE.md](docs/WINDOWS-TEST-GUIDE.md)** | From a blank Windows machine to an accepted run, step by step |
| **[docs/SLACK-SETUP.md](docs/SLACK-SETUP.md)** | The Slack app, both tokens, allowlist, per-user bindings, reply privacy |
| **[docs/NEW-MT4-ADAPTER.md](docs/NEW-MT4-ADAPTER.md)** | A new broker or build; which fields are stable; the Win32 Inspector |
| **[docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)** | Symptoms and what they actually mean |
| **[SECURITY.md](SECURITY.md)** | Threat model, credential handling, and the limits of the guarantees |
| **[docs/WINDOWS-MT4-TEST.md](docs/WINDOWS-MT4-TEST.md)** | A shorter checklist of what the acceptance run must cover |
| **[docs/IMPLEMENTATION-REPORT.md](docs/IMPLEMENTATION-REPORT.md)** | What was built, and its verification status |

## Known limitations

Stated plainly, because a public project that oversells itself is worse than one that
does not.

| Limitation | Effect |
|---|---|
| Windows-only automation | On other platforms the agent runs an explicitly marked mock; it never silently pretends to be a real login |
| Slack member directory | Co-workers can still see each other's Slack profile. The agent's separation does not extend to Slack's own UI |
| UIA is not available for every build | Some MT4 builds expose no login form to UI Automation at all. The Win32 fallback exists for that, and is opt-in per Account |
| Auto-open depends on menu wording | The agent recognises a small set of standard menu captions. A custom build may need the dialog opened by hand — everything else works |
| Every new broker needs inspecting | Selectors are build-specific. `Detect Win32` proposes them; a human confirms |
| 64-bit Python driving a 32-bit terminal | The automation library prints a warning. It is a warning, not a failure, and the native Win32 route's operations are not bitness sensitive |
| Group is advanced and optional | A group target requires every member Account bound to the caller |
| Not confirmed on hardware | The full Slack end-to-end path, group login, and some adapters are unit-tested only. They report `WINDOWS_REAL_TEST_REQUIRED` rather than a fabricated pass |
| A shared Windows account | Anyone signed in to that Windows session can see the agent's window and data directory |

## Development

```bash
python -m venv .venv
.venv/bin/pip install -e ".[dev]"     # Windows: .venv\Scripts\pip install -e ".[dev]"

.venv/bin/python -m pytest -q         # the whole suite
.venv/bin/ruff check .               # lint
node --check src/app/web/static/app.js   # the one static JS file
```

Requires Python 3.11 or later; 3.12 is what the Windows installer prefers and what the
CI matrix runs.

The suite is fast and runs anywhere, but most of it exercises the portable policy
rather than the automation. Anything that needs a real Windows machine and a real
terminal is gated on `sys.platform` and reports itself as not run off Windows.

## Data and files

Everything lives in one data directory (the installer points it at your local app
data folder by default), never inside the repository.

| File | Contains |
|---|---|
| `secrets.json` | Slack tokens, the Web Admin token, the dedup key. Owner-only |
| `settings.json` | Non-secret configuration: port, allowlist, bindings, timeouts |
| `accounts.json` | Login ids and selectors. Not a credential, but still private |
| `groups.json` | Group membership and order |
| `history.jsonl` | Who asked for what, when, and the result. No credential |
| `logs/`, `reports/` | Sanitised |

The repository excludes all of it, plus caches, virtual environments and build output.
See [.gitignore](.gitignore).

## Stopping, updating, uninstalling

**Stop** — press `Ctrl+C` in the `start.bat` window. Only one agent runs at a time;
a lock file in the data directory enforces that.

**Update** — stop the agent, replace the code, then run [`install.bat`](install.bat)
again. Keep the data directory: it holds your tokens, accounts and history. The
[`test-windows.bat`](test-windows.bat) helper runs the whole acceptance sequence for
you.

**Uninstall** — stop the agent and delete the project folder and the data directory.
The data directory is the only thing that is not recreated by a fresh install.

## Screenshots

Sanitised screenshots will be added here before the repository is made public. Until
then this section is intentionally empty rather than containing broken links.

## Licence

MIT. See [LICENSE](LICENSE).
