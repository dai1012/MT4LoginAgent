# Adapting a New MT4 Build or a New Broker

How to bring a broker, or a different build of the same broker, under the Agent's
control — and how to tell the easy case from the hard one.

All examples are invented. `A` and `B` are Account aliases, `12345678` is a made-up
login id, and `C:\MT4\Broker-A\terminal.exe` is a made-up path.

## 1. Two different jobs

| You want to add | Use | Why |
|---|---|---|
| A **second account at the same broker, same MT4 build** | **Account → Duplicate** | Every selector is broker-and-build specific, so they are all still correct. Copying them is exactly right. |
| A **different broker, or a different MT4 build** | **Win32 Inspector** | The dialog shape, the control ids, the menu wording and the window titles are all specific to that build. Copying them carries wrong values. |

Duplicate is fast but it is **not** a new-broker tool. It copies the terminal path,
the working directory, the window title expression, the UIA selectors and the whole
Win32 configuration, because those are what you would otherwise have to type.

The one thing Duplicate will not do is copy a success title expression that embeds the
source account's login id: that value is cleared for you, with a note, because such an
expression is specific to one account and would be wrong for the new one.

## 2. What the Agent actually depends on

| Category | Fields |
|---|---|
| Finding the process | `terminal_path`, `profile_path` (the process working directory), `process_name` |
| Recognising the login window | `window_title_regex` |
| Recognising the logged-in window | `success_window_title_regex` |
| UIA selectors | `control_ids` for login id, credential, server, save-login and the login button; `control_titles` as an alternative |
| Win32 fallback | `dialog_class`, `anchors`, and six native control ids: `login_id_combo`, `login_id_edit`, `otp`, `server_combo`, `server_edit`, `login_button` |
| Menu command | a small set of known menu captions, used only to open the login dialog |

`profile_path` is the **process working directory**, not the MT4 "Open Data Folder".
It is compared against the running process's current directory to tell two terminals
apart. Filling in the data folder there will not match anything.

## 3. Which of these survive a version change

**Effectively stable.** These only change if you reinstall or move the software.

- `terminal_path`, `profile_path`, `process_name`
- `dialog_class` — `#32770` is a Windows standard dialog class, so any MT4 build
  uses it
- the six Win32 control ids — these are numeric `DlgCtrlID` values, and unlike UIA
  automation ids they do not depend on the language

**Changes with the broker's customisation of the dialog.**

- the Win32 `anchors` — they are visible menu and label strings, so a different
  language or a rebranded build has different text
- `control_titles` — visible captions, language dependent
- the server list entries

**Most likely to break.**

| Field | Why |
|---|---|
| `window_title_regex` | usually contains the broker's brand and product name |
| `success_window_title_regex` | must be anchored, and is easy to write too narrowly |
| UIA `control_ids` | an automation id comes from the UIA provider, which is not stable across builds. Some MT4 builds expose nothing to UIA at all |
| the menu caption list | hard-coded, and a custom build may word the menu differently |

The practical consequence: **if the UIA route worked for a build and stops working
after an update, that is a known and expected failure, not a bug in the Agent.**

## 4. Adapting a new broker, step by step

1. **Install the broker's MT4** and log in once by hand, so its data folder exists.
   Keep it in its own folder, and if you will run a second terminal, install that one
   into a **different folder**.
2. **Create the Account** in Web Admin → Accounts. Fill in the display name, an
   `alias`, the `login_id` and the `server`, plus:
   - `terminal_path` — the full path to `terminal.exe`
   - `profile_path` — the process working directory, **not** the data folder
   Leave every selector empty and leave the Win32 fallback off.
3. **Start that terminal** and leave it on its main window. You do **not** need to
   open the login dialog by hand.
4. **Windows Test → select the Account → `Detect Win32`.**
   The Agent reuses the same window-menu command the real login uses, so it opens the
   login dialog itself. If this broker's menu is worded differently it says so, and
   you can open the dialog by hand and press the button again.
5. **Read the proposal.** For each of the eight fields you get a value and a
   confidence:
   - `HIGH` — the structure resolved uniquely
   - `NEEDS_CONFIRMATION` — it could not be pinned down, and **no value is offered**
   Nothing is guessed. While any required field is not `HIGH`, the Apply button is
   not offered, and the panel lists which fields are missing.
6. **Apply** once the list is empty. This writes **only** the Win32 fallback fields.
   Your alias, login id, server, terminal path and working directory are left
   exactly as they were.
7. **Copy the two suggested titles** into `window_title_regex` and
   `success_window_title_regex` on the Account form. They are suggestions, shown for
   you to copy — the Agent never writes them for you, because these are the fields
   where a wrong value is most expensive.
8. **Verify**: Step 1, Step 2 Detect, then Step 4 Real Login.

The inspection reads control **metadata** only — native ids, class names, and the
captions of inert controls. It never reads the value of an editable field, so running
it cannot expose a login id, a server, or a credential, and it writes nothing at all
until you apply.

## 5. Why the suggested title expressions look like that

Both suggestions are anchored (`^...$`) and escaped, so a title containing
parentheses, brackets or dots is matched literally rather than as a pattern.

The suggestion for the logged-in window **replaces the account's own login id with a
wildcard**. A real broker title looks like:

```
RakutenSecurities-Demo - 12345678 - Rakuten Securities, Inc.
```

and the suggestion becomes an expression in which the `12345678` is generalised. That
keeps the broker and server wording, which is stable, and drops the part that is
specific to one account, so the same expression keeps working for the other accounts
you add later.

## 6. Menu captions

The Agent can open the login dialog by sending the window's own menu command, without
touching the keyboard or the screen coordinates. To do that it has to recognise the
menu item, and it only knows a short list of captions — the standard Japanese and the
standard English wording.

The inspection panel shows you the **actual captions your build ships**, as a
read-only reference. This is the fastest way to find out why auto-open did not work:
if your build's menu says something different, it will be visible in that list.

The known caption list is a constant in the source. It is deliberately **not**
extended from an inspection — a human reads the list, decides, and changes the code.
Report the wording you found if it is missing.

## 7. Two terminals at the same time

MetaTrader's own guidance is to install a separate copy into a separate folder for
each concurrently running terminal. That is also what the Agent needs, because it
tells terminals apart by the executable path and the process working directory.

- install the second terminal into a **different folder**
- give it its **own** `terminal_path` and its **own** `profile_path`
- give it its own Account

If you try to enable a second Account that resolves to the same instance as an already
enabled one, the Agent refuses and explains that each concurrently running Account
needs its own terminal folder and its own working directory. A disabled draft may
share them while you set it up; only enabling is checked.

## 8. When UIA is not available

Some MT4 builds render the login form as a plain Win32 dialog that UI Automation never
exposes. For those, the UIA route cannot resolve anything and no amount of selector
entry will help — this is why the Win32 fallback exists, and why you should reach for
`Detect Win32` instead of typing automation ids by hand.

If the panel reports that it could not find a dialog with both an Edit and a Button,
open the login window by hand and run `Detect Win32` again.

## 9. Troubleshooting

See [Troubleshooting](TROUBLESHOOTING.md).
