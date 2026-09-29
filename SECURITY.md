# Security Model

What the Agent protects, what it deliberately does not, and where the limits are.

This document describes the design as implemented. Where a guarantee is enforced by
convention rather than by code, or where a known limitation exists, it says so.

## 1. Scope

The Agent runs on one Windows machine, owned by the person running it. It accepts a
credential from Slack, types it into a MetaTrader 4 login form on that machine, and
reports the outcome back to the person who asked.

It is not a multi-tenant service. It does not manage several people's machines, it has
no admin rights over the terminal, and it is not exposed to a network.

## 2. The credential

A credential is the password or one-time code a person types. It is **opaque**: the
Agent does not assume any format, because a broker's real code is letters and digits
and a fixed Demo password is digits, and neither has a documented shape.

**Where it goes**

Slack delivers the slash command → the Agent parses it into a secret field → the login
queue holds it in memory → the Win32 or UIA code writes it into the control → it is
released. That is the whole path.

**Where it never goes**

- the Agent's log, at any level, including exception tracebacks
- `history.jsonl` and the Dashboard
- `report.json`, `report.html` or the sanitised UIA tree
- `settings.json` or `secrets.json`
- any temporary file
- a command line or process argument, so it cannot appear in the process list
- a report filename or a URL

**How that is enforced**

1. **Structural.** No code path formats a credential into a message. Rejections and
   errors carry a fixed reason, never the value.
2. **Registration.** A credential in flight is registered as a live secret for the
   duration of the operation, and every rendered value is scrubbed against the
   registered set.
3. **Pattern.** A key-based scrubber removes anything that looks like
   `otp=`, `password:` and similar, and removes the remainder of a `/mt4` command
   entirely. This is deliberately over-broad: it cannot recognise an arbitrary secret's
   shape, so it does not try to.

**Known limitation.** The by-value scrubber ignores candidates shorter than four
characters, because a very short string occurs by chance in ordinary text and
substituting it would destroy the output. Short credentials therefore rely on layers 1
and 2. Lowering that floor is not a fix; the correct handling is to never place the
value in a message at all, which is what layer 1 guarantees.

The `Detect Win32` inspection follows the same rule from the other direction: it reads
control metadata only and never reads the value of an editable control, so running it
cannot expose a login id, a server, or a credential.

## 3. Web Admin

Bound to **loopback only**. It is not reachable from the network, and the Agent does
not open a public endpoint — Slack uses Socket Mode, an outbound WebSocket.

Access additionally requires a shared admin token, sent in a request header. The
token lives in `secrets.json` with owner-only permissions on Windows.

Every mutating request requires it, including Account creation, token changes and the
test runner.

## 4. Slack access control

Two independent gates.

1. **Allowlist** — a Member ID must be listed or the request is refused.
2. **Bindings** — an allowed Member ID may operate only the Account aliases bound to
   them.

The second gate is **fail-closed**: an allowed person with no binding can operate
nothing. Being added to the allowlist never implies access to every existing Account.

Two further properties:

- **No enumeration.** "Does not exist" and "exists but is not yours" produce
  byte-identical replies. For a group target, the refusal never names the member that
  was missing.
- **No side effects on refusal.** A refused request is rejected before the dedup key is
  taken, before the queue, and before anything is typed into MT4, so it cannot consume
  a one-time code or occupy a login slot.

## 5. Reply privacy

Immediate acknowledgements, completion results, errors and `/mt4 status` are delivered
to the requesting person only: an ephemeral message in a channel, and a normal message
in a bot DM, which is already private. Nothing is posted to a channel for others to
read.

`/mt4 status` reports the caller's own assigned aliases and deliberately omits the
total Account count and the queued and active job counts, which describe other people's
work.

## 6. Boundary this does not draw

Two people in the same workspace can still see **each other's Slack profile** in the
member directory, and Slack decides what else is visible about the App. That is Slack's
surface, not the Agent's.

The Agent guarantees the separation that matters: each person cannot list, address or
observe another person's MT4 Accounts, cannot run a login against them, and cannot read
their results.

## 7. Multiple terminals

Two enabled Accounts may not resolve to the same MT4 instance. The guard compares the
canonicalised terminal path and working directory, case-insensitively, and refuses to
create, update or enable a colliding Account.

Without it, one person's login could silently run against another person's terminal.
A disabled draft may share its source's paths while it is being set up; only enabling
is checked.

## 8. Fail-closed guarantees

The Agent prefers refusing a request to guessing. Concretely, it will not:

- start if a configuration file cannot be validated
- write an Account that fails validation
- type a credential into a control it could not identify unambiguously
- claim a login succeeded without observing the configured authenticated window
- apply a selector proposal while any required field is unresolved
- accept a command-line, target or Group that is not configured for this user
- report a success it did not verify: anything requiring a real machine returns
  `WINDOWS_REAL_TEST_REQUIRED` rather than a fabricated pass

## 9. Network

| Direction | Purpose |
|---|---|
| Outbound | Slack Socket Mode WebSocket; nothing else |
| Local | Web Admin on loopback |

The Agent makes no outbound request to a broker. The credential is typed into a
desktop application, not posted to a web API.

## 10. Files on disk

| File | Contains |
|---|---|
| `secrets.json` | Slack tokens, admin token, dedup key. Owner-only permissions |
| `settings.json` | non-secret configuration: web port, allowlist, bindings, timeouts |
| `accounts.json` | login ids and selectors. **Not** a credential, but still private |
| `history.jsonl` | who asked for what, when, and how it went. No credential |
| `logs/`, `reports/` | sanitised; every rendered value is scrubbed |

All of these live in the data directory and are excluded from version control. See
[.gitignore](.gitignore) for the full list.

## 11. Reporting a vulnerability

Please report privately rather than in a public issue. If this repository has
**Security** → **Report a vulnerability** enabled, use it. Otherwise open an issue
asking for a private channel, or contact the maintainer through the repository owner
listing.

Please do not include a real credential, a real token, a real account number, or a
screenshot of a live session. A description of the behaviour, the version, and the
relevant part of the sanitised report is enough.

## 12. Known limitations

| Limitation | Effect |
|---|---|
| Slack member directory | co-workers can see each other's Slack profile; the Agent cannot and does not try to hide that |
| Web Admin is token-gated, not user-gated | anyone holding the admin token can see all Accounts |
| By-value scrubbing needs 4+ characters | very short credentials rely on the structural guarantee instead |
| A shared Windows account | anyone signed in to that Windows session can see the Agent's window and data directory |
| No protection from a compromised host | the Agent assumes the machine it runs on is not hostile |
