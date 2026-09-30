# Troubleshooting

Symptoms, what they actually mean, and what to do. Every message quoted here is one
the Agent produces; none contains an account number, a token or a credential.

## Contents

1. [Windows Test: the Step card is amber but nothing is wrong](#1-windows-test-the-step-card-is-amber-but-nothing-is-wrong)
2. [A Step shows MANUAL and you do not know what to do](#2-a-step-shows-manual-and-you-do-not-know-what-to-do)
3. [MT4_PROCESS_RESOLUTION did not match](#3-mt4_process_resolution-did-not-match)
4. [UIA cannot see the login dialog](#4-uia-cannot-see-the-login-dialog)
5. [The login dialog will not open by itself](#5-the-login-dialog-will-not-open-by-itself)
6. [Detect Win32 cannot find a dialog](#6-detect-win32-cannot-find-a-dialog)
7. [Detect Win32 reports NEEDS_CONFIRMATION](#7-detect-win32-reports-needs_confirmation)
8. [MT4 instance collision when enabling an Account](#8-mt4-instance-collision-when-enabling-an-account)
9. [ui_verification_unverified after a login that worked](#9-ui_verification_unverified-after-a-login-that-worked)
10. [The Agent warns about 32-bit and 64-bit Python](#10-the-agent-warns-about-32-bit-and-64-bit-python)
10b. [Detect Win32 reports several candidate windows](#10b-detect-win32-reports-several-candidate-windows)
11. [The Slack command is rejected](#11-the-slack-command-is-rejected)
12. [The App Home Messages tab says messaging is turned off](#12-the-app-home-messages-tab-says-messaging-is-turned-off)
13. [The History page looks empty](#13-the-history-page-looks-empty)
14. [A credential is rejected](#14-a-credential-is-rejected)
15. [A one-time code is refused as stale](#15-a-one-time-code-is-refused-as-stale)
16. [A group cannot run](#16-a-group-cannot-run)
17. [Web Admin will not start](#17-web-admin-will-not-start)

---

## 1. Windows Test: the Step card is amber but nothing is wrong

First check the table, not the card. Each row keeps its own verdict; the card is a
summary.

| Card | Meaning |
|---|---|
| `FAIL` | something blocking failed |
| `WARN` | a real warning or a degradation, and the flow could still continue |
| `PASS` / `READY` | the automatic checks passed |

If a row says `MANUAL`, the step is not broken. A manual item is a decision the human
still has to make — a destructive probe, or a confirmation that can only be done by
hand. Those are counted in the secondary text as `N manual confirmation remaining`
rather than being painted as a problem.

## 2. A Step shows MANUAL and you do not know what to do

Read the row's `suggested_action`. The usual ones:

- **Phase 1 destructive checks** — changing the system time, provoking a UAC prompt,
  killing a worker, running two instances. These are optional and deliberately never
  run unattended. Skip them unless you are specifically testing recovery.
- **`SLACK_COMMAND_PATH`** — go to Slack and run `/mt4 status` yourself, once, to
  confirm the command reaches the Agent. Nothing else satisfies this check.

## 3. MT4_PROCESS_RESOLUTION did not match

The Agent found the executable but not the right **process**.

`profile_path` is the **process working directory** — the directory the process was
started in, usually the install folder. It is compared against the running process's
current directory in order to tell two terminals apart. It is **not** the MT4 "Open
Data Folder".

- fill it with the terminal's own install folder, for example
  `C:\MT4\Broker-A`
- do not fill in anything under `%APPDATA%\MetaQuotes\Terminal\...`

To see what the running process actually reports, in PowerShell:

```powershell
Get-Process terminal | Select-Object Id, Path
```

If you have two terminals running, the instance guard will refuse to enable a second
Account that points at the same folder and working directory — see
[section 8](#8-mt4-instance-collision-when-enabling-an-account).

## 4. UIA cannot see the login dialog

This is expected for some MT4 builds. A login form rendered as a plain Win32 dialog is
not exposed to UI Automation at all, so no automation id can be typed in that would
ever match. Filling in UIA selectors for such a build wastes time.

Use the Win32 route instead: enable the Win32 dialog fallback on the Account, or run
`Detect Win32` and apply the proposal. See
[Adapting a New MT4 Build or a New Broker](NEW-MT4-ADAPTER.md).

A WARN on the UIA rows does **not** stop Step 2 from passing when the Win32 route
resolved — the warning stays visible in the table, but the step reflects the route
that will actually drive MT4.

## 5. The login dialog will not open by itself

The Agent opens the login dialog by sending the terminal's own menu command, which
avoids both screen coordinates and blind keypresses. It only recognises a small set of
standard menu captions.

If it does not open, the wording of that build's menu is different. Either open the
login dialog by hand — every later step works fine that way — or find the real
caption: run `Detect Win32` and read the **menu captions** shown in the panel, which
are read from the target itself.

The known caption list is a constant in the source and is never extended
automatically.

## 6. Detect Win32 cannot find a dialog

The panel reports one of:

- `no window with both an Edit and a Button was found` — the dialog is not open. Open
  the login window by hand and press the button again.
- `expected exactly one MT4 process, found N` — more than one terminal instance
  resolved to the same process. Give each Account its own install folder and working
  directory, or make sure only one of the relevant terminals is running while you
  inspect.
- `N login-shaped windows matched; refusing to choose` — more than one window looked
  like a login form. Close the extra one and run it again.

## 7. Detect Win32 reports NEEDS_CONFIRMATION

A field the Agent could not resolve **uniquely**, so it offers no value. Nothing is
guessed, and the Apply button is not available while any required field is unresolved.
This is the intended behaviour: a confidently wrong control id is worse than none.

Common causes:

- **only one ComboBox** — the login id and server fields cannot be told apart
- **two or more unowned Edit boxes** — the credential box is ambiguous
- **several buttons and none of them reads as a login action**

Resolve them by hand, or compare against another MT4 build of the same broker: those
ids are stable across a rebuild, so you can copy them from a working Account.

## 8. MT4 instance collision when enabling an Account

```
MT4 instance collision: account 'B' resolves to the same terminal instance as
enabled account 'A' (id '...'). Each concurrently running MT4 Account needs its own
terminal installation folder and its own process working directory (cwd), per
MetaTrader multi-instance guidance; install a separate terminal copy and configure a
separate cwd for this Account before enabling it.
```

Two enabled Accounts would drive the same terminal, so the Agent refuses rather than
letting one person's login collide with the other's.

To fix it, install a second terminal into a **different folder**, give the new Account
that folder as `terminal_path` and that terminal's own working directory as
`profile_path`, then enable it. A draft Account may be left disabled and sharing the
source's paths while you set it up; only enabling is checked.

## 9. ui_verification_unverified after a login that worked

The Agent submits the credential, the login dialog closes, MT4 is visibly logged in —
and the History still records a failure with `ui_verification_unverified`.

That means the Agent could not **prove** the login succeeded, not that the login
failed. It looks for the window your `success_window_title_regex` matches and requires
one of two things: that such a window newly appeared, or that its title changed. If the
main window already carried the logged-in title **before** the attempt — because a
previous session was left open — neither happened, and the Agent refuses to claim
success it did not observe.

Fix the expression rather than the login:

- make it describe the stable broker and server wording, not the account:
  `^Rakuten.*Demo - .* - Rakuten Securities, Inc\.$`
- do not put the account's own id in it
- keep it anchored

`Detect Win32` proposes an anchored expression for you, already generalised away from
the account's id; it is shown for copying, never written for you.

## 10. The Agent warns about 32-bit and 64-bit Python

```
32-bit application should be automated using 32-bit Python (you use 64-bit Python)
```

MT4 is a 32-bit program and your Agent's Python is 64-bit. The message comes from the
automation library, and it is a warning, not an error: the request still succeeds.

For the native Win32 route, the operations involved are message-based Win32 calls,
which are not bitness sensitive, and reading window identities, class names, control
ids and text across the two bitnesses has already been exercised on a real 32-bit MT4.
You do not need a 32-bit Python to use the Win32 fallback.

**Treat it as a non-blocking notice once the Win32 fallback has logged in on your
machine.** A real 32-bit MT4 was driven through this route successfully with a 64-bit
interpreter, so the warning alone is never a reason to switch. Login verification also
observes the terminal through the same native enumeration the login form is written
with, so a successful login is not missed because of this warning.

The one thing to keep in mind: forcing a 32-bit interpreter for the whole Agent would
be the wrong trade, because it would break brokers whose terminals are 64-bit.

## 10b. Detect Win32 reports several candidate windows

```
2 login-shaped windows matched in this MT4 process; refusing to choose.
```

The inspector found more than one top-level window of **that one MT4 process** that has
both a text field and a button, so it refused to guess which one is the login form.
This is deliberate: a wrong control id is worse than no suggestion.

**This never affects Real Login for an Account that is already configured.** The
inspector exists to help you adapt a *new* broker or a *new* MT4 build; a configured
Account with `MT4_DISCOVERY_READY` at PASS does not need it.

What to do: close the unrelated windows in that terminal — typically an **order
dialog**, a **new account** dialog, or any other window with an input field — and run
**Detect Win32** again. If instead the message says no window was found, the login
dialog did not open: open it by hand and run it again.

## 11. The Slack command is rejected

| Reply | Meaning |
|---|---|
| `未授权：你的 Slack User ID 是 U…` | that Member ID is not in **Allowed Slack User IDs** |
| `Target unavailable. 该目标不存在或未分配给你…` | the target does not exist, **or** exists but is not bound to you. The two are deliberately indistinguishable so the reply cannot be used to discover which aliases exist |
| `目标账号已有登录任务或队列已满…` | that Account is already logging in, or the queue is full |
| `检测到重复的 Slack 请求…` | the same request arrived twice; the second is ignored so a login is not run twice |
| `未授权：Slack 请求没有可识别的 User ID` | the request had no sender identity |

`Target unavailable` is also what an allowed-but-unbound person gets, including a
person with an empty binding list. To give someone access, bind an Account alias to
their Member ID in Web Admin → **Slack**.

## 12. The App Home Messages tab says messaging is turned off

The manifest enables a writable Messages tab, but a manifest change does not reach an
already installed App on its own.

1. <https://api.slack.com/apps> → your App → **App Home** → tick **Messages Tab** →
   **Save Changes**, or
2. re-apply the whole manifest: **Update Manifest → Apply to Workspace**

You can always fall back to a DM or a channel; both work regardless.

## 13. The History page looks empty

The History table is loaded when the page loads and is refreshed when you switch to
the tab, or when a login finishes. If a login completed in another tab, or from Slack
rather than the page, the table can be showing an older snapshot — press **刷新**, or
reload the page.

The same data backs the Dashboard's "recent logins" block, so if the Dashboard shows
your entry, the record is definitely stored.

## 14. A credential is rejected

The credential is treated as an **opaque secret**: a password or a one-time code, with
no assumed format.

| Rejected | Why |
|---|---|
| empty, or only whitespace | there is nothing to submit |
| longer than 128 characters | a size limit that bounds how much a mistyped value can enter a log line |
| containing a newline, carriage return, NUL or another control character | those can forge a log line or truncate a string downstream |

Symbols, spaces and non-ASCII characters are all fine, so a password such as
`aB3!xY 9#z` is accepted, and a one-time code that is letters and digits is accepted.
**Do not include the login id** — the Agent already knows it from the Account.

## 15. A one-time code is refused as stale

The Agent applies a cutoff of 30 to 300 seconds (120 by default) to a one-time code
measured from when it arrived in Slack. That is the Agent's own limit on accepting an
old request; it is not a statement about how long the broker considers the code valid.

Longer than that, the request is refused rather than typing a stale code into a login
form. Request a fresh code and send it again.

## 16. A group cannot run

Groups are an advanced, optional feature. A group target requires **every** member
Account to be bound to the person running the command, and the group must be enabled
and non-empty.

A partially bound group is refused with the same generic `Target unavailable` text, and
the reply never says which member was missing.

## 17. Web Admin will not start

- **wrong port** — the port comes from `settings.json`; check it before assuming the
  Agent is down
- **a setting will not validate** — a stored value written under an older rule may
  exceed the current limit. The Agent refuses to start rather than silently widening
  it, and the log line names the setting
- **another instance holds the lock** — only one Agent runs at a time; the lock file
  lives in your data directory
