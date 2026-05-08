#!/usr/bin/env python3
"""
Convert public free proxy lists to Quantumult X / Clash Meta subscription format.

Pipeline:
  1. Fetch raw proxy lists from multiple upstream sources (concurrently).
  2. Parse + validate each line (regex + IPv4/port range checks).
  3. Deduplicate by ip:port across all sources.
  4. Cross-validate each candidate against TWO probe URLs — both must pass.
  5. Sort by average latency, keep the fastest N.
  6. Emit QX list and a curated Clash Meta config (with rule-providers).
"""

import datetime
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

import requests
from requests.exceptions import RequestException

# ---------------------------------------------------------------------------
# Sources — multiple upstreams, deduplicated by ip:port.
# Add or remove sources here; each list is fetched concurrently.
# ---------------------------------------------------------------------------
SOURCES: dict[str, list[str]] = {
    "http": [
        "https://raw.githubusercontent.com/databay-labs/free-proxy-list/master/http.txt",
        "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",
        "https://raw.githubusercontent.com/proxifly/free-proxy-list/main/proxies/protocols/http/data.txt",
    ],
    "socks5": [
        "https://raw.githubusercontent.com/databay-labs/free-proxy-list/master/socks5.txt",
        "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/socks5.txt",
        "https://raw.githubusercontent.com/proxifly/free-proxy-list/main/proxies/protocols/socks5/data.txt",
    ],
}

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")

# ---------------------------------------------------------------------------
# Validation knobs
# ---------------------------------------------------------------------------
MAX_PROXIES = 50          # final cap kept in subscription output
TIMEOUT_SEC = 3           # per-probe timeout
CONCURRENCY = 80          # validation thread pool size
FETCH_TIMEOUT_SEC = 30    # source fetch timeout

# Probe both endpoints — only proxies that succeed on BOTH are kept.
# Reduces false positives from captive portals / partial blockers.
PROBE_URLS = [
    "http://www.gstatic.com/generate_204",
    "http://cp.cloudflare.com/generate_204",
]

# Accepts:
#   "1.2.3.4:8080"
#   "http://1.2.3.4:8080"
#   "socks5://1.2.3.4:1080"
# Rejects malformed lines, IPv6, hostnames (validation upstream is more reliable
# when constrained to numeric IPs).
_PROXY_LINE_RE = re.compile(
    r"^(?:[a-z][a-z0-9+\-.]*://)?"        # optional scheme
    r"((?:\d{1,3}\.){3}\d{1,3})"          # IPv4
    r":(\d{1,5})"                         # port
    r"(?:[/?#].*)?$"                      # optional trailing path/query
)


def parse_proxy_line(line: str) -> Optional[str]:
    """Normalize a raw line to canonical 'ip:port', or None if invalid.

    Filters comments, blank lines, malformed entries, out-of-range octets,
    and out-of-range ports.
    """
    line = line.strip()
    if not line or line.startswith("#"):
        return None

    m = _PROXY_LINE_RE.match(line)
    if not m:
        return None

    ip, port_str = m.group(1), m.group(2)

    # Octet range check (regex allows 999.999.999.999 syntactically)
    octets = ip.split(".")
    if any(not (0 <= int(o) <= 255) for o in octets):
        return None

    # Port range check
    port = int(port_str)
    if not (1 <= port <= 65535):
        return None

    return f"{ip}:{port}"


def fetch(url: str) -> list[str]:
    """Fetch a source URL and return parsed canonical 'ip:port' lines."""
    try:
        r = requests.get(url, timeout=FETCH_TIMEOUT_SEC)
        r.raise_for_status()
    except RequestException as e:
        print(f"  [WARN] fetch failed {url}: {e}")
        return []

    parsed = (parse_proxy_line(ln) for ln in r.text.splitlines())
    return [p for p in parsed if p]


def fetch_all(urls: list[str]) -> set[str]:
    """Fetch multiple sources concurrently; return a deduplicated set."""
    seen: set[str] = set()
    with ThreadPoolExecutor(max_workers=max(1, len(urls))) as pool:
        for items in pool.map(fetch, urls):
            seen.update(items)
    return seen


def _probe(session: requests.Session, proxies: dict, url: str) -> tuple[bool, float]:
    """Single probe through the given proxies. Returns (ok, latency_ms)."""
    try:
        start = time.monotonic()
        r = session.get(
            url,
            proxies=proxies,
            timeout=TIMEOUT_SEC,
            allow_redirects=False,
        )
        elapsed_ms = (time.monotonic() - start) * 1000
        return r.status_code in (200, 204), elapsed_ms
    except Exception:
        # Connection refused, timeout, SOCKS errors, TLS errors — all fail
        return False, float("inf")


def check_proxy(proxy: str, proxy_type: str) -> Optional[tuple[str, str, float]]:
    """Cross-validate a proxy against all PROBE_URLS.

    Returns (proxy, type, avg_latency_ms) only if EVERY probe succeeds.
    """
    if proxy_type == "http":
        proxy_url = f"http://{proxy}"
    else:
        # 'socks5' (vs 'socks5h') resolves DNS locally — faster for validation
        proxy_url = f"socks5://{proxy}"
    proxies = {"http": proxy_url, "https": proxy_url}

    latencies: list[float] = []
    with requests.Session() as s:
        for probe_url in PROBE_URLS:
            ok, ms = _probe(s, proxies, probe_url)
            if not ok:
                return None
            latencies.append(ms)

    avg_ms = round(sum(latencies) / len(latencies))
    return (proxy, proxy_type, avg_ms)


def validate_proxies(
    proxies: list[str], proxy_type: str
) -> list[tuple[str, str, float]]:
    """Validate a list of candidates concurrently; return survivors."""
    valid: list[tuple[str, str, float]] = []
    total = len(proxies)

    with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
        futures = {pool.submit(check_proxy, p, proxy_type): p for p in proxies}
        done = 0
        for future in as_completed(futures):
            done += 1
            if done % 100 == 0 or done == total:
                print(
                    f"   [{proxy_type.upper()}] {done}/{total} tested, "
                    f"{len(valid)} alive"
                )
            r = future.result()
            if r is not None:
                valid.append(r)

    valid.sort(key=lambda x: x[2])
    return valid


# ---------------------------------------------------------------------------
# Output: Quantumult X
# ---------------------------------------------------------------------------
def generate_qx(proxies: list[tuple[str, str, float]]) -> str:
    """Generate Quantumult X proxy list snippet."""
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "# Free Proxy List for Quantumult X",
        f"# Updated: {now}",
        f"# Validated: {len(proxies)} nodes (sorted by avg latency, 2-probe)",
        f"# Timeout: {TIMEOUT_SEC}s | Max: {MAX_PROXIES}",
        "",
    ]
    for i, (proxy, ptype, latency) in enumerate(proxies, 1):
        ip, port = proxy.split(":", 1)
        tag = ptype.upper()
        lines.append(f"{tag}-{i}[{latency}ms] = {ptype}, {ip}, {port}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Output: Clash Meta (Mihomo)
# ---------------------------------------------------------------------------
# Loyalsoldier rule providers — community-maintained, weekly updated.
# Covers ad-block (reject), Apple/iCloud direct, Google/proxy/direct domain
# lists, Telegram CIDR, China CIDR, and common app bundle IDs.
_RULE_PROVIDERS = """\
rule-providers:
  reject:
    type: http
    behavior: domain
    url: "https://cdn.jsdelivr.net/gh/Loyalsoldier/clash-rules@release/reject.txt"
    path: ./ruleset/reject.yaml
    interval: 86400
  apple:
    type: http
    behavior: domain
    url: "https://cdn.jsdelivr.net/gh/Loyalsoldier/clash-rules@release/apple.txt"
    path: ./ruleset/apple.yaml
    interval: 86400
  icloud:
    type: http
    behavior: domain
    url: "https://cdn.jsdelivr.net/gh/Loyalsoldier/clash-rules@release/icloud.txt"
    path: ./ruleset/icloud.yaml
    interval: 86400
  google:
    type: http
    behavior: domain
    url: "https://cdn.jsdelivr.net/gh/Loyalsoldier/clash-rules@release/google.txt"
    path: ./ruleset/google.yaml
    interval: 86400
  proxy:
    type: http
    behavior: domain
    url: "https://cdn.jsdelivr.net/gh/Loyalsoldier/clash-rules@release/proxy.txt"
    path: ./ruleset/proxy.yaml
    interval: 86400
  direct:
    type: http
    behavior: domain
    url: "https://cdn.jsdelivr.net/gh/Loyalsoldier/clash-rules@release/direct.txt"
    path: ./ruleset/direct.yaml
    interval: 86400
  private:
    type: http
    behavior: domain
    url: "https://cdn.jsdelivr.net/gh/Loyalsoldier/clash-rules@release/private.txt"
    path: ./ruleset/private.yaml
    interval: 86400
  telegramcidr:
    type: http
    behavior: ipcidr
    url: "https://cdn.jsdelivr.net/gh/Loyalsoldier/clash-rules@release/telegramcidr.txt"
    path: ./ruleset/telegramcidr.yaml
    interval: 86400
  cncidr:
    type: http
    behavior: ipcidr
    url: "https://cdn.jsdelivr.net/gh/Loyalsoldier/clash-rules@release/cncidr.txt"
    path: ./ruleset/cncidr.yaml
    interval: 86400
  applications:
    type: http
    behavior: classical
    url: "https://cdn.jsdelivr.net/gh/Loyalsoldier/clash-rules@release/applications.txt"
    path: ./ruleset/applications.yaml
    interval: 86400
"""

# Order matters — first match wins.
_RULES_SECTION = """\
rules:
  # AI services (top priority — domain-explicit)
  - DOMAIN-SUFFIX,openai.com,Proxy
  - DOMAIN-SUFFIX,chatgpt.com,Proxy
  - DOMAIN-SUFFIX,anthropic.com,Proxy
  - DOMAIN-SUFFIX,claude.ai,Proxy

  # Local apps and private network — direct
  - RULE-SET,applications,DIRECT
  - RULE-SET,private,DIRECT

  # Ad / tracker block
  - RULE-SET,reject,REJECT

  # Apple ecosystem — direct (avoid breaking iCloud / push)
  - RULE-SET,icloud,DIRECT
  - RULE-SET,apple,DIRECT

  # Western services — proxy
  - RULE-SET,google,Proxy
  - RULE-SET,proxy,Proxy

  # Curated direct domains
  - RULE-SET,direct,DIRECT

  # IP rules (no-resolve avoids unnecessary DNS lookups)
  - RULE-SET,telegramcidr,Proxy,no-resolve
  - RULE-SET,cncidr,DIRECT,no-resolve
  - GEOIP,LAN,DIRECT,no-resolve
  - GEOIP,CN,DIRECT,no-resolve

  # Fallback
  - MATCH,Proxy
"""


def generate_clash(proxies: list[tuple[str, str, float]]) -> str:
    """Generate full Clash Meta (Mihomo) config with curated rule-providers."""
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    proxy_names: list[str] = []
    proxy_blocks: list[str] = []
    for i, (proxy, ptype, latency) in enumerate(proxies, 1):
        ip, port = proxy.split(":", 1)
        name = f"{ptype.upper()}-{i}"
        proxy_names.append(name)
        proxy_blocks.append(
            f'  - name: "{name}"\n'
            f"    type: {ptype}\n"
            f"    server: {ip}\n"
            f"    port: {port}"
        )
    names_yaml = "\n".join(f'      - "{n}"' for n in proxy_names)

    header = f"""\
# Clash Meta (Mihomo) Config - Free Proxy List
# Updated: {now}
# Validated: {len(proxies)} nodes (sorted by avg latency, 2-probe)

mixed-port: 7890
allow-lan: false
mode: rule
log-level: info

dns:
  enable: true
  enhanced-mode: fake-ip
  fake-ip-filter:
    - "*.lan"
    - "*.local"
    - "localhost.ptlogin2.qq.com"
  nameserver:
    - 223.5.5.5
    - 119.29.29.29
  fallback:
    - 1.1.1.1
    - 8.8.8.8
  fallback-filter:
    geoip: true
    geoip-code: CN

"""
    proxies_section = "proxies:\n" + "\n".join(proxy_blocks) + "\n"

    groups_section = f"""\
proxy-groups:
  - name: "Proxy"
    type: select
    proxies:
      - "Auto"
      - "DIRECT"
{names_yaml}

  - name: "Auto"
    type: url-test
    url: http://www.gstatic.com/generate_204
    interval: 300
    tolerance: 100
    proxies:
{names_yaml}

"""

    return (
        header
        + proxies_section
        + "\n"
        + groups_section
        + "\n"
        + _RULE_PROVIDERS
        + "\n"
        + _RULES_SECTION
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    print("=> Fetching proxy lists from multiple sources...")
    http_set = fetch_all(SOURCES["http"])
    socks5_set = fetch_all(SOURCES["socks5"])
    print(f"   HTTP: {len(http_set)} unique | SOCKS5: {len(socks5_set)} unique")

    if not http_set and not socks5_set:
        print("[ERROR] No proxies fetched, aborting.")
        return

    print("=> Validating proxies (2-probe cross-validation)...")
    valid: list[tuple[str, str, float]] = []
    if http_set:
        valid.extend(validate_proxies(sorted(http_set), "http"))
    if socks5_set:
        valid.extend(validate_proxies(sorted(socks5_set), "socks5"))

    valid.sort(key=lambda x: x[2])
    valid = valid[:MAX_PROXIES]
    print(f"=> Kept {len(valid)} fastest proxies")

    if not valid:
        print("[WARN] No valid proxies found, keeping previous output.")
        return

    # Quick sanity print
    for proxy, ptype, latency in valid[:5]:
        print(f"   #{ptype}: {proxy} ({latency}ms)")

    qx_path = os.path.join(OUTPUT_DIR, "qx.txt")
    with open(qx_path, "w") as f:
        f.write(generate_qx(valid))
    print(f"   => {qx_path}")

    clash_path = os.path.join(OUTPUT_DIR, "clash.yaml")
    with open(clash_path, "w") as f:
        f.write(generate_clash(valid))
    print(f"   => {clash_path}")

    print("=> Done!")


if __name__ == "__main__":
    main()
