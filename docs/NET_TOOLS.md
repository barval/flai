# Network diagnostics — FLAI v13.5

FLAI could answer "what does this page say" (`read_page`, the crawler) but not "does
this host answer at all", "what does DNS say", "which ports are open", "how fast is
this link". Those questions are asked from a terminal today; this turns them into chat
tools, behind a permission an administrator grants per user.

## What the tools answer

Seven tools, each a typed request to a separate container:

| Tool | Answers |
|---|---|
| `net_dns` | what a name resolves to, mail and text records, what a specific resolver says |
| `net_whois` | who registered a domain or holds an address block |
| `net_ping` | whether a host answers, and how long it takes |
| `net_trace` | where the packets go and where loss starts |
| `net_ports` | which of the 100 most common ports are open, or which addresses answer in a network |
| `net_http` | what headers and TLS certificate a URL returns |
| `net_speed` | throughput to an iperf3 server |

## The container and its profile

`services/netdiag/` builds an image with `nmap`, `iperf3`, `dig`, `whois`, `curl`,
`ping`, `mtr`, `traceroute` and `nc`, and puts a small Flask app in front of them. That
app is the only interface: it validates every field, runs one program per request with
`shell=False`, and never accepts a command line.

```bash
docker compose -f docker-compose.gpu.yml --profile with-net-tools up -d
./deploy.sh --with-net-tools        # writes the profile and the .env key
```

The container runs with `cap_drop: ALL` and one capability added back — `NET_RAW`,
without which ping, mtr and SYN scanning do not work. It has a read-only root
filesystem, a `tmpfs` for `/tmp`, no volumes, no published port, and its own Docker
network shared with the `web` service only. It is deliberately **not** on the network
that carries PostgreSQL, Redis and the model servers: a permitted user must not be able
to reach an unauthenticated database through Docker's own networking.

## Turning it on

Three keys, all in `.env` and in the admin panel:

| Key | Meaning |
|---|---|
| `NET_TOOLS_ENABLED` | whether the tools are offered at all |
| `NETDIAG_URL` | the sidecar's address (`http://flai-netdiag:8099` in compose) |
| `NETDIAG_API_TOKEN` | the shared bearer token; stored as a secret in the panel |

With `NET_TOOLS_ENABLED=false` the sidecar contributes nothing to the catalogue and can
keep running. `flask doctor` reports it only when the switch is on — a container that is
advertised and broken is exactly the failure that command exists to name.

## The permission

`users.net_tools_enabled`, off by default, a checkbox per user in **Admin → Users**. The
administrator account has no chat, so it never needs the flag.

A user without it who asks for a ping gets a sentence, not silence:

> ⚠️ Network tools are not available to you.  (localized into the user's language)

That is why the tools are advertised to everyone who can chat: a hidden tool cannot
explain itself, and the model would answer the question from its own knowledge instead.

The check runs when the tool is called, not when the catalogue is built, so a flag that
was just switched on takes effect on the next message.

## How a request reaches a tool

The router decides, and it has to be told that a network check is ordinary chat rather than
a lookup. Measured on the live instance before the fix: «measure the speed to `<host>`» went
to web search with the kernel on (the intent won, and the search path offers no tools) and to
`[-REASONING-]` with it off (the marker router read the request as a complex task). In both
cases the model answered «I have no access to network tools», which was true of the request
it received.

Two rules close it, and both are injected only while the sidecar answers `/health`:

- **kernel** (`KERNEL_ENABLED=1`) — a network question *and a request to measure or check
  one* is intent `none` plus the matching tool. Measured after the change: «measure the
  speed», «check the ping», «who owns the domain», «which ports are open» → `none` with
  `net_speed` / `net_ping` / `net_whois` / `net_ports`, while the weather still goes to
  search.
- **marker router** (`KERNEL_ENABLED=0`, the default) — category 12, `[-NET-]`. Additions to
  the prose of categories 6 and 8 were measured and did not move the decision; a category of
  its own does, in both languages and on repeated runs. `[-NET-]` maps to ordinary chat and
  keeps the **whole** request as the query, because the chat model chooses the tool from the
  user's words — the target alone says nothing about what to do with it.

`net_speed` is the one tool whose output is only a measurement: a non-zero exit means no
measurement happened and the result carries `⚠️ `, whereas `ping`'s exit code 1 *is* the
answer («100 % packet loss»). A failed connection attempt is bounded at five seconds, so a
host without an iperf3 server answers in five instead of holding the request open.

## The tools, and their limits

One host per call. The one exception is `net_ports` with a network, which runs **host
discovery only** (`nmap -sn`): 256 addresses with a port scan is minutes of work and
does not belong inside a chat turn. Port scanning and service detection are single-host
operations, `-O` and `-sS` are never used.

| Tool | Ceiling |
|---|---|
| `net_dns` | 10 s |
| `net_ping` | 15 s |
| `net_whois` | 15 s |
| `net_http` | 20 s |
| `net_speed` | 20 s |
| `net_trace` | 20 s |
| `net_ports` | 30 s |

The ceiling is enforced twice — by the application's request timeout and by the
container itself — and a call that exceeds it returns a sentence, not an exception.
Every call publishes a stream stage («📡 Scanning ports…» in the user's language), so a
thirty-second trace is not a silent chat. Results go through the same 4 000-character bound as every other
tool, so a scan cannot flood the context window.

## What is always blocked

Whatever the permission says, these never leave the application:

- `127.0.0.0/8` and `::1` — loopback;
- `169.254.0.0/16` and `fe80::/10` — link-local, which is where cloud metadata
  endpoints live.

Everything else is reachable, including private ranges: diagnosing the local network is
the point of the feature. That is the honest trade this section exists to state — a
permitted user can scan the LAN.

## The rate limit

10 calls per minute and 60 per hour, per user, in Redis. A refused call does not count
against the budget, so the answer "you have used ten" stays true. `nmap` is the reason:
its cost grows with repetition.

## The audit trail

Every call writes one line to Redis — time, login, tool, target, result — and the list
keeps 500 entries for 24 hours. Two ways to read it:

```bash
docker exec flai-web flask net-audit --limit 50
```

and the **Network calls** table in **Admin → Monitoring**.

The trail is best effort by design: a Redis flush loses the day's record, and nothing
survives past 24 hours. It answers "who scanned those addresses yesterday", not "audit
this installation".

## Honest limits

- A permitted user can reach the local network. The permission is the gate, not an
  address list.
- A `/24` is discovery only; a port scan of a network is not available.
- `net_speed` needs a counterpart: an iperf3 server on the other side. The container is
  a client only, so measuring against a public iperf3 host or your own server is up to
  you.
- `net_whois` depends on the whois daemons' own rate limits, which are outside FLAI's
  control.
- The sidecar is architecture-independent but its Dockerfile is only exercised on
  x86-64; an unusual host may need the package list adjusted.

## Troubleshooting

| Symptom | Cause |
|---|---|
| The tools never appear | `NET_TOOLS_ENABLED=false`, or the sidecar is not answering: `flask doctor` says which |
| Network tools are not available to you | the user has no flag in Admin → Users |
| The network diagnostics service is not running | the flag is on but `/health` failed — check the container |
| A scan returns nothing | nmap needs `NET_RAW`; `cap_add` is in both compose files |
| A target is refused as loopback | it is `127.0.0.0/8`, `::1`, `169.254.0.0/16` or `fe80::/10` |
| The tools vanish after enabling | `NETDIAG_API_TOKEN` is empty: the sidecar fails closed, and `--with-net-tools` generates one |

## Related

- [CONFIGURATION.md](CONFIGURATION.md) — the three keys among all the others
- [MONITORING.md](MONITORING.md) — where the audit table lives
- [SKILLS.md](SKILLS.md) — a different kind of on-demand instruction
