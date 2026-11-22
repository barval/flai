---
name: net-diagnostics
description: Diagnose a network — DNS, whois, ping, traceroute, open ports, HTTP/TLS and link speed. Requires the diagnostics service and a permission granted by an administrator.
allowed-tools: net_dns, net_whois, net_ping, net_trace, net_ports, net_http, net_speed
---
# Network diagnostics

One host per call. Never invent output, and a timeout is not the same as "the host is down".

1. Resolve the name first with `net_dns`. If it does not resolve, say so and stop: ping and
   ports against an unresolved name prove nothing.
2. Check reachability with `net_ping`. No reply can mean blocked ICMP rather than a dead host,
   so report what was measured instead of concluding.
3. If ping fails, run `net_trace` once to show where the path stops. Do not repeat it with
   more hops.
4. Check the service with `net_ports` only against a host that answered, and only the ports the
   question is about. A closed port is not a broken service.
5. Use `net_http` when the question is about a web service: status, redirect and certificate in
   one call.
6. Use `net_whois` for ownership or registration of a domain, not for connectivity.
7. Use `net_speed` only when the user asked to measure throughput. It needs an iperf3 server on
   the other side; a failed connection is the answer, not a measurement.

What counts as proof: a DNS answer, an ICMP reply, a completed TCP connection, an HTTP status
line, a certificate subject. What does not: silence, a timeout, or a plausible guess.

A `/24` is discovery only. Never scan a range for open ports, never check loopback or
link-local addresses, and never run a check against a host the user did not name.

If a call answers that network tools are not available, say that once in the user's language
and stop. Do not substitute a web search for a measurement, and do not describe what the result
would have been.
