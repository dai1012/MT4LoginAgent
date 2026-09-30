# MT4 Remote Login Agent

[简体中文](README.md) | [日本語](README.ja.md) | [English](README.en.md)

A source-auditable MetaTrader 4 remote login agent that runs on **your own Windows
computer**.

A colleague sends one command in Slack:

```text
/mt4 A 123456
```

`A` is the **Account alias** you configured in the local Web Admin, not a real login
id. `123456` is the **credential** for this one attempt (a password or a one-time
code). The agent finds the matching MT4 terminal on the machine it runs on, fills in
the saved Login ID and Server together with that credential, and **replies with the
result to the requester only**.

The agent runs on your machine. It needs no public server, no inbound port and no
database.

> ### ⚠️ Read this first
>
> **This project is not an official Rakuten product, and has no affiliation,
> sponsorship or endorsement from Rakuten.** "Rakuten MT4" appears here only as an
> **example** of a broker and MT4 build that has been verified on real hardware. All
> broker-specific configuration — dialog control ids, menu captions, window titles — is
> broker dependent. See [Adapting a new broker](#9-adapting-a-new-broker).
>
> **Honest verification status.** The core MT4 automation — the Win32 login dialog
> end-to-end, multi-terminal isolation, success detection, and automatic dialog
> opening — **has been verified on real Windows with a real broker terminal**. What
> has **not** been verified on hardware: the full Slack end-to-end round trip, group
> login, and the newer Win32 Inspector. Anything that needs real hardware and has not
> had it reports `WINDOWS_REAL_TEST_REQUIRED` and **never fabricates a pass**. The
> item-by-item boundary is in [Known limitations](#11-known-limitations).

## Contents

- [What it can do](#2-what-it-can-do)
- [Quick start](#3-quick-start)
- [Slack commands and access control](#4-slack-commands-and-access-control)
- [Multiple accounts and terminals](#5-multiple-accounts-and-terminals)
- [Windows Test](#6-windows-test)
- [Win32 Inspector](#7-win32-inspector)
- [Adapting a new broker](#9-adapting-a-new-broker)
- [Security model](#10-security-model)
- [Known limitations](#11-known-limitations)
- [Documentation](#12-documentation)
- [Screenshots](#13-screenshots)
- [Development](#14-development)
- [Data and files](#15-data-and-files)
- [Stopping, updating, uninstalling](#16-stopping-updating-uninstalling)
- [Licence](#17-licence)

## 1. Project scope

**The problem it solves**: a one-time MT4 code is short lived, so a login cannot be
completed while you are away from the machine. Slack delivers "Account alias +
credential" to the agent, and the agent types it into **your own** MT4 login form and
reports the outcome.

**What it is not**:

- not a trading tool — it places no orders, touches no positions, reads no balance
- not a cloud service — no server side, no accounts system
- not an official client of any broker

**Boundary**: the agent does exactly one "fill in the form and click login" on your
machine, and leaves everything else to MT4.

## 2. What it can do

| Capability | Notes |
|---|---|
| Slack command triggers a local MT4 login | Socket Mode, so **no inbound port** |
| **Multiple accounts**, one person or several | Own alias, own terminal install folder, own working directory each |
| **Account Duplicate** | Copy every configured field for a second account at the same broker and build |
| **Automatic login-dialog opening** | Drives the terminal's **own menu command**; no coordinates, no blind keypresses |
| **UIA first, Win32 fallback** | UIA where the build exposes it; native Win32 control ids where it does not |
| **Win32 Inspector** | Reads the target's login dialog and **suggests** a configuration, instead of hand-copying control ids |
| **Windows Test** | A guided, phase-by-phase acceptance run that refuses to claim what it did not verify |
| **History** | Who asked for what, when, and the result — **with no credential in it** |
| **Per-user Account bindings** | In one workspace, each person operates only the aliases bound to them |
| **Private results** | Completion results are delivered to the requester, **never to the channel** |

## 3. Quick start

1. **Copy the project folder** to your Windows machine.
2. **Install Python 3.12** (recommended) or 3.11, with the Python Launcher `py.exe`.
3. Run [`install.bat`](install.bat). It installs the dependencies and self-checks.
4. Run [`start.bat`](start.bat) and note the **local admin token** it prints.
5. Open `http://127.0.0.1:8765` and enter the token. If the port is taken the console
   says so; change it in `settings.json` or pass `--port`.
6. In **Accounts**, add an account. Give it an `alias` such as `A` — that is the name
   used in Slack, not the real login id. Fill in the terminal path and the process
   working directory.
7. In the **Slack** page, create an app from
   [`slack-app-manifest.yaml`](slack-app-manifest.yaml), then paste the `xapp-`
   app-level token, the `xoxb-` bot token and your Slack Member ID, and save.
8. In Slack, send:

   ```text
   /mt4 A 123456
   /mt4 status
   ```

The full Slack side, including the App Home Messages tab and the difference between
the two tokens, is in **[docs/SLACK-SETUP.md](docs/SLACK-SETUP.md)**.

## 4. Slack commands and access control

```text
/mt4 <alias-or-group> <credential>
/mt4 status
```

- `<alias-or-group>` is an **Account alias** (or a Group name). It is the identifier,
  and it is **never the login id**.
- `<credential>` carries the **password or one-time code only. Do not include the
  login id** — the agent already knows it from the Account.

The credential is **opaque**: it may contain spaces and symbols. The agent treats
everything after the first token as the credential, so a password with spaces works.

```text
/mt4 A hunter2correct-horse
/mt4 B 483920
```

### Two independent gates

1. **Allowed Slack User IDs** — who may use the agent at all.
2. **Account bindings** — which Account aliases each of those people ticked.

The second is **fail-closed**: being in the allowlist with **no binding** means being
able to operate **nothing**. Adding someone to the allowlist never implies access to
every existing account, so onboarding a colleague cannot silently hand them your
trading accounts.

A two-person example:

```
U_A -> [A]
U_B -> [B]
```

**An alias you may not use, and an alias that does not exist, return exactly the same
reply.** Otherwise the reply alone would let anyone discover which aliases exist. A
refused request is rejected before the queue, before MT4, and before any credential is
typed, so it cannot consume a one-time code or occupy a login slot.

| Message | In a channel | In a DM | Who can see it |
|---|---|---|---|
| Immediate ack | requester only | requester only | nobody else |
| Completion result | requester only (ephemeral) | requester only | nobody else |
| `/mt4 status` | requester only | requester only | nobody else |

`/mt4 status` reports **only the caller's own assigned aliases**. It does not report
the global account count, the queue depth, or the number of running jobs — those
numbers describe other people's work.

> Two people in the same workspace can still see **each other's Slack profile** in the
> member directory. That is Slack's own behaviour and the agent neither can nor tries
> to hide it. What the agent guarantees is that they cannot see each other's MT4
> Accounts, cannot start a login against them, and cannot read each other's results.

## 5. Multiple accounts and terminals

MetaTrader's own guidance is to install a separate copy into a separate folder for
each concurrently running terminal. The agent depends on the same thing: it tells
terminals apart by `terminal_path` plus the **process working directory**.

- every concurrently running terminal has its own **install folder**
- every Account has its own `terminal_path` and its own `profile_path`
  (`profile_path` is the **process working directory**, **not** the MT4 Open Data
  Folder)

**Instance collision guard**: two enabled Accounts may not resolve to the same
terminal instance. Trying to enable one is refused with an explanation, rather than
letting your login collide with someone else's. A disabled draft may share its
source's paths while you set it up.

## 6. Windows Test

A guided, step-by-step acceptance run in the Web Admin. It **refuses to claim things
it did not observe**: a login is only reported successful once a window matching your
anchored success expression is actually seen.

Step card meanings:

| Card | Meaning |
|---|---|
| `FAIL` | something blocking failed |
| `WARN` | a real warning or degradation, and the flow could still continue |
| `PASS` / `READY` | the automatic checks passed |
| `MANUAL` rows | a decision for you to make (a destructive test, or a confirmation only you can do) |

**`MANUAL` never turns a card amber.** Such items appear only as a count in the
secondary text (`passed · 2 manual checks remaining`). **To see what a step actually
did, read the table, not the card colour.**

Destructive tests (changing the system time, provoking a UAC prompt, killing a worker,
two instances) are marked optional and **never run unattended**.

A full walkthrough, from a blank machine to acceptance, is in
**[docs/WINDOWS-TEST-GUIDE.md](docs/WINDOWS-TEST-GUIDE.md)**.

## 7. Win32 Inspector

**Use it for**: a new broker, or a new MT4 build from the same broker.
**Do not use it for**: a second account at the same broker and build — use
**Account Duplicate**, which is faster.

```
New Account  →  start that MT4  →  Windows Test: Detect Win32
             →  review the proposal  →  Apply  →  copy the two title suggestions
             →  Real Login test
```

| Property | Notes |
|---|---|
| Metadata only | Reads control ids, class names and inert-control text; **never reads an Edit's value**, so it cannot leak a login id, a server or a credential |
| Never saves by itself | Only writes when you press Apply |
| Never guesses | A field that cannot be resolved uniquely is reported as `NEEDS_CONFIRMATION` **with no value**; while any required field is unconfirmed, Apply does not appear |
| Writes only the Win32 fallback | `alias` / `login_id` / `server` / `terminal_path` / `profile_path` are never touched |
| Reveals the real menu captions | The panel shows the captions this build actually ships, which is the fastest way to diagnose an auto-open failure |

Details: [docs/NEW-MT4-ADAPTER.md](docs/NEW-MT4-ADAPTER.md).

## 8. Which fields survive a version change

| Stable | Why |
|---|---|
| `terminal_path`, `profile_path`, `process_name` | only change when the software is reinstalled or moved |
| `dialog_class` = `#32770` | a standard Windows dialog class, used by any MT4 build |
| the six Win32 control ids | numeric `DlgCtrlID` values, which **do not change with the UI language** |

| Likely to break | Why |
|---|---|
| `window_title_regex` | usually contains the broker's brand and product name |
| `success_window_title_regex` | must be anchored, and is easy to write too narrowly |
| UIA `control_ids` | determined by the UIA provider and not guaranteed across builds; **some builds expose no UIA at all** |
| Win32 `anchors` | these are visible strings, so they change with language and branding |
| the menu caption list | a code constant; a customised build may word it differently |

**Practical consequence**: if the UIA route worked on a build and stops after an
update, that is a **known and expected failure**, not a bug in the agent.

## 9. Adapting a new broker

```
Install the broker's MT4 and log in once by hand  →  create the Account
  (leave the selectors empty)  →  start that terminal  →  Detect Win32
  →  review  →  Apply  →  copy the two title suggestions  →  Step 4 Real Login
```

**When UIA is completely unavailable, do not type automation ids by hand** — UIA
cannot see that build's login form at all, so nothing you type will match. Use the
Win32 Inspector.

The full procedure, the field stability table and the failure modes are in
**[docs/NEW-MT4-ADAPTER.md](docs/NEW-MT4-ADAPTER.md)**.

## 10. Security model

A credential travels from Slack into memory, into the login form, and **no further**.

It does **not** appear in: the log at any level, History (`history.jsonl`), the
reports (`report.json` / `report.html` / the sanitised UIA tree), configuration files,
temporary files, or command line arguments (so it cannot show up in the process
list).

Three mechanisms enforce that:

1. **Structural** — no code path ever formats a credential into a message; errors
   carry a fixed reason.
2. **Registration** — the credential is registered as a live secret for the duration
   of the operation, and every rendered value is scrubbed against it.
3. **Pattern** — a key-based scrubber removes fragments like `otp=` and `password:`,
   and removes the remainder of a `/mt4` command entirely.

The Web Admin is bound to **loopback only**, and every mutating request needs the
admin token.

**A limitation, stated honestly**: the by-value scrubber ignores candidates shorter
than four characters, because a very short string occurs by chance in ordinary text
and substituting it would corrupt the output. Short credentials therefore rely on the
first two layers, and lowering that floor is not the correct fix — the correct
handling is to never place the value in a message at all, which is exactly what the
first layer guarantees.

The full threat model is in **[SECURITY.md](SECURITY.md)**.

## 11. Known limitations

Listed plainly, because a public project that oversells itself is worse than one that
does not.

| Limitation | Effect |
|---|---|
| **Windows-only automation** | Other platforms run an explicitly marked mock and **never silently pretend to be a real login** |
| **The Slack member directory** | Co-workers can still see each other's Slack profile; the agent's isolation does not cover Slack's own interface |
| **Not every build exposes UIA** | Some MT4 builds expose no login form to UI Automation at all, which is why the Win32 fallback exists and why it is opt-in per Account |
| **Auto-open depends on menu wording** | Only a small set of standard menu captions is recognised; a customised build may need the dialog opened by hand, and nothing else is affected |
| **Every new broker needs inspecting** | Selectors are build specific; `Detect Win32` proposes them and a human confirms |
| **64-bit Python driving a 32-bit terminal** | The automation library prints a warning. It is a warning, not a failure, and the native Win32 route's operations are not bitness sensitive |
| **Group is advanced and optional** | A group target requires every member Account to be bound to the caller |
| **Some parts are not verified on hardware** | The full Slack end-to-end path, group login and some newer adapters are unit-tested only, and they report `WINDOWS_REAL_TEST_REQUIRED` rather than a fabricated pass |
| **A shared Windows account** | Anyone signed in to that Windows session can see the agent's window and data directory |
| **Not an official broker product** | The broker name appears only as a verified example |

## 12. Documentation

| Document | Read it for |
|---|---|
| [docs/WINDOWS-TEST-GUIDE.md](docs/WINDOWS-TEST-GUIDE.md) | From a blank Windows machine to an accepted run, step by step |
| [docs/SLACK-SETUP.md](docs/SLACK-SETUP.md) | The Slack app, the two tokens, the allowlist, per-user bindings, reply privacy |
| [docs/NEW-MT4-ADAPTER.md](docs/NEW-MT4-ADAPTER.md) | A new broker or build, field stability, the Win32 Inspector |
| [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | Symptoms and what they actually mean |
| [SECURITY.md](SECURITY.md) | Threat model, credential handling, the limits of the guarantees |
| [docs/WINDOWS-MT4-TEST.md](docs/WINDOWS-MT4-TEST.md) | A shorter checklist of what the acceptance run must cover |
| [docs/IMPLEMENTATION-REPORT.md](docs/IMPLEMENTATION-REPORT.md) | What was built, and its verification status |

## 13. Screenshots

> The four sanitised screenshots below will be added in a later commit. They render
> once the files are in place.

### Dashboard

![Dashboard showing Agent health, recent login records and queue state](docs/images/dashboard_overview.webp)

The Dashboard summarises Agent health and recent logins, and **contains no credential**.

### Accounts

![Accounts list showing each Account's alias, broker server and enabled state](docs/images/accounts_list.webp)

One row per Account, with the alias, server, enabled state and multi-instance
isolation information.

### Windows Test

![Windows Test page with five Step cards above a per-item result table](docs/images/windows_test_overview.webp)

Note how to read it: **the cards are the summary, the table is the truth per item.**
`MANUAL` items do not turn a card amber.

### A private reply in Slack

![A completion result in Slack visible only to the requester and absent from the channel](docs/images/slack_private.webp)

The completion result is ephemeral, so nobody else in the channel reads your login
outcome.

More interface screenshots are in [docs/SLACK-SETUP.md](docs/SLACK-SETUP.md) and
[docs/NEW-MT4-ADAPTER.md](docs/NEW-MT4-ADAPTER.md).

## 14. Development

```bash
python -m venv .venv
.venv/bin/pip install -e ".[dev]"     # Windows: .venv\Scripts\pip install -e ".[dev]"

.venv/bin/python -m pytest -q         # the whole suite
.venv/bin/ruff check .               # lint
node --check src/app/web/static/app.js   # the one static JS file
```

Python 3.11 or later is required; the Windows installer prefers 3.12 and the CI matrix
runs 3.11 and 3.12.

The suite is fast and runs anywhere, but most of it exercises the **platform
independent policy** rather than the automation. Anything needing a real Windows
machine and a real terminal is gated on `sys.platform` and reports itself as not run
off Windows.

## 15. Data and files

Everything lives in **one data directory** (the installer points it at your local app
data folder by default), **never inside the repository**.

| File | Contains |
|---|---|
| `secrets.json` | Slack tokens, the Web Admin token, the dedup key. Owner-only |
| `settings.json` | Non-secret configuration: port, allowlist, bindings, timeouts |
| `accounts.json` | Login ids and selectors. **Not a credential**, but still private |
| `groups.json` | Group membership and order |
| `history.jsonl` | Who asked for what, when, and the result. **No credential** |
| `logs/`, `reports/` | Sanitised |

The repository excludes all of that, plus caches, virtual environments and build
output. See [.gitignore](.gitignore).

## 16. Stopping, updating, uninstalling

**Stop** — press `Ctrl+C` in the `start.bat` window. Only one agent runs at a time,
enforced by a lock file in the data directory.

**Update** — stop the agent, replace the code, then run [`install.bat`](install.bat)
again. **Keep the data directory**: it holds your tokens, accounts and history.

**Uninstall** — stop the agent and delete the project folder and the data directory.
The data directory is the only thing a fresh install cannot recreate.

## 17. Licence

MIT. See [LICENSE](LICENSE).
