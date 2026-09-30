# Slack Setup

How to connect the Agent to a Slack workspace, and exactly what each person in that
workspace can see and do.

Everything below uses invented values. `U012ABCDEF` and `U0987654321` are example
Member IDs, and no real account number, token or personal path appears anywhere in
this document.

## 1. What you are building

One Slack App, installed in one workspace, connected to one Agent running on your own
Windows machine over **Socket Mode**. No public HTTP endpoint is needed: the Agent
opens an outbound WebSocket, so it can stay behind a normal home network and NAT.

People send a slash command, the Agent fills a credential into the MT4 login form on
your machine, and replies to the person who asked.

## 2. Create the App from the manifest

The repository ships [`slack-app-manifest.yaml`](../slack-app-manifest.yaml). Create a
new app from a manifest at <https://api.slack.com/apps/manifest/new>.

The manifest asks for:

| Setting | Why |
|---|---|
| Bot token scopes: `commands`, `chat:write` | register the slash command, post a reply |
| Socket Mode | outbound WebSocket, no public URL |
| Slash command `/mt4` | the command itself |
| App Home → Messages tab | lets people type the command without the bot being in a channel |
| `org_deploy_enabled: false` | the App stays inside this workspace |

There is deliberately **no** `im:history` and no message-event scope: the Agent reads
slash commands, not conversation content.

### Changing the manifest later

A manifest change does **not** reach an already installed App on its own. If the
Messages tab still shows "messaging this app is turned off", open
<https://api.slack.com/apps> → your App → **App Home** → tick **Messages Tab** →
**Save Changes**. If you changed the whole manifest, use **Update Manifest → Apply to
Workspace** instead.

## 3. The two tokens

With Socket Mode enabled you need two tokens, and they are different things.

| Token | Prefix | Where to get it | What it is |
|---|---|---|---|
| App-level token | `xapp-` | **Settings → Socket Mode → Generate Token** | opens the WebSocket. Scoped to your App, not to a user. |
| Bot token | `xoxb-` | **OAuth & Permissions → Install to workspace** | acts as the bot user inside the workspace |

Both go into Web Admin → **Slack**. They are stored in your local `secrets.json` and
are never written to the repository, a log, or a report.

## 4. Decide who may use the Agent

Two independent lists, and this distinction matters.

**Allowed Slack User IDs** — who is allowed to talk to the Agent at all. Get a Member
ID from your Slack profile: click your avatar → `...` → **Copy member ID**.

**Account bindings** — which MT4 Account aliases each of those people may operate.

The second list is **fail-closed**. A person who is allowed but has no binding can
operate **nothing**. Being added to the allowlist never grants access to every
existing Account, so onboarding a colleague cannot silently hand them your trading
accounts. A person with an empty binding list sees `No accounts assigned.` from
`/mt4 status`.

> Sanitised screenshot will be added in a later commit.

![Web Admin Slack settings showing the Allowed Slack User ID field and the per-user Account bindings checkboxes](docs/images/slack_settings.webp)

In Web Admin → **Slack**, each allowed Member ID gets a row of Account alias
checkboxes drawn from the accounts you have already created:

```
Allowed Slack User IDs      [ U012ABCDEF ] [ U0987654321 ]

Account bindings
  U012ABCDEF   [x] A
  U0987654321   [x] B
```

Save. A binding that names an alias which does not exist is rejected with an error
naming that alias, so a stale grant is never stored.

## 5. Sending a command

```
/mt4 <alias-or-group> <credential>
/mt4 status
```

- `<alias-or-group>` is the **Account alias** you chose in Web Admin. It is also the
  name you pass to the Agent elsewhere; it is not a login id.
- `<credential>` is only the password or the one-time code. **Never include the
  login id** — the Agent already knows it from the Account.

The alias may contain internal spaces when it is part of a credential, so the Agent
treats everything after the first token as the credential.

Examples:

```
/mt4 A hunter2correct-horse
/mt4 B 483920
/mt4 status
```

## 6. Where replies go, and who sees them

| Message | Channel | DM | Who can see it |
|---|---|---|---|
| Immediate acknowledgement | the requester only | the requester only | nobody else |
| Completion result | the requester only | the requester only | nobody else |
| `/mt4 status` | the requester only | the requester only | nobody else |
| Rejection for a target you may not use | the requester only | the requester only | nobody else |

Completion results are **not** posted into the channel. In a channel the Agent posts
an ephemeral message; in a DM it posts a normal message in that DM, which is already
private. Either way the other members of the channel never read your login outcome.

Errors follow the same rule. A rejected or failed login is reported to the requester
and nowhere else.

## 7. Access control between people

Each person operates only their own bindings.

```
U012ABCDEF  ->  A
U0987654321  ->  B
```

If `U012ABCDEF` sends `/mt4 B ...`, the reply is:

```
Target unavailable. 该目标不存在或未分配给你；
请在 Web Admin → Slack → Account bindings 中为你的 User ID 绑定 alias。
```

That is **deliberately the same text** as when the target does not exist at all. If
the two cases differed, the reply itself would let anyone discover which aliases
exist by trying them. Never report which member of a group was missing either.

A request you are not allowed to make stops before the queue, before MT4, and before
any credential is typed anywhere, so it cannot consume a one-time code or occupy a
login slot.

For a group target, **every** member Account must be bound to you. A partially bound
group is refused with the same generic message.

## 8. Status privacy

```
/mt4 status
```

```
Rakuten MT4 Remote Login Agent V1
Agent: running
Slack: Connected
Platform: Windows
Automation: uia
Accounts: A
```

The status reports only the **caller's** assigned aliases. It deliberately does not
report the total number of Accounts, the number of queued jobs, or the number of
active jobs, because those describe other people's work. With no binding it replies
`Accounts: No accounts assigned.`

## 9. The boundary this does not draw

Two people in the same workspace can still see **each other's Slack profile** in the
member directory, and Slack itself decides who else can read the App's presence. That
is outside the Agent.

What the Agent does guarantee: each person cannot list, address, or observe the
other's MT4 Accounts, cannot run a login against them, and cannot read their login
results. The Agents, the Accounts and the results stay separated; the Slack member
directory does not.

## 10. If something is not working

See [Troubleshooting](TROUBLESHOOTING.md) for the Messages tab, the allowlist, binding
rejections, and why the Messages tab may still read "turned off".
