# netdiag — the network diagnostics sidecar

The container behind the chat tools `net_dns`, `net_whois`, `net_ping`,
`net_trace`, `net_ports`, `net_http` and `net_speed`. It answers one typed
endpoint per tool and never accepts a command line, so the chat model cannot reach a
shell through it.

```bash
docker compose -f docker-compose.gpu.yml --profile with-net-tools up -d
```

## Contract

`Authorization: Bearer $NETDIAG_API_TOKEN` on every request, including `/health`.

| Method | Path | Arguments |
|---|---|---|
| GET | `/health` | — returns `{"ok": true, "tools": [...]}` |
| POST | `/dns` | `name`, `type` (A/AAAA/MX/TXT/NS/SOA/CNAME/PTR), `server` |
| POST | `/whois` | `target` |
| POST | `/ping` | `host`, `count` (1–5) |
| POST | `/trace` | `host`, `hops` (1–15) |
| POST | `/ports` | `host` (one host or ≤ /24), `ports` (1–1000, default 100), `service_version` |
| POST | `/http` | `url` (http/https) |
| POST | `/speed` | `host`, `port` (1–65535), `seconds` (1–10) |

Every call also takes `timeout`, clamped to the tool's own ceiling. A refusal is a
`⚠️ ` sentence in `output`, not a stack trace — the caller shows it to the model.

## Limits

- One host per call. A `/24` (or narrower) runs **discovery only** (`nmap -sn`): a
  port scan of 256 addresses is minutes of work and does not belong inside a chat turn.
- A port scan is `-sT --top-ports 100 -T4`; `-O` and `-sS` are never used.
- Always refused, whatever the caller's permission: loopback and link-local
  (`127.0.0.0/8`, `::1`, `169.254.0.0/16`, `fe80::/10`) — the last is where cloud
  metadata endpoints live.

## Isolation

The container joins its own compose network alongside the `web` service only. It is
not on the network that carries PostgreSQL, Redis and the model servers, so a
permitted user cannot reach an unauthenticated database through Docker's own
networking. `cap_drop: ALL` plus `NET_RAW` (ping, mtr, SYN scanning), read-only root
filesystem, a `tmpfs` for `/tmp`, no volumes, no published port.
