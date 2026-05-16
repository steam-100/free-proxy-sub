#!/usr/bin/env python3
"""
Convert public free proxy lists to Quantumult X / Clash Meta subscription format.

Pipeline:
  1. Fetch raw proxy lists from each upstream source.
  2. Parse + validate each line (regex + IPv4/port range checks).
  3. Cross-source dedup: a proxy is attributed to the FIRST source that lists it
     (in SOURCES dict order). No double-counting overlap.
  4. Cross-validate each candidate against TWO probe URLs — both must pass.
  5. Per-source top-N selection: each source contributes up to PER_SOURCE_QUOTA
     of its fastest survivors. Diversity > raw speed; insulates against any
     single upstream going dark.
  6. Final list sorted by latency, emitted as QX list + curated Clash Meta config.
"""

import base64
import datetime
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

import requests
from requests.exceptions import RequestException

# ---------------------------------------------------------------------------
# Sources — grouped by upstream provider so we can quota per-source after
# validation. Order matters: when the same ip:port appears in multiple
# sources, attribution goes to the first one listed here.
# ---------------------------------------------------------------------------
SOURCES: dict[str, dict[str, str]] = {
    "databay-labs": {
        "http":   "https://raw.githubusercontent.com/databay-labs/free-proxy-list/master/http.txt",
        "socks5": "https://raw.githubusercontent.com/databay-labs/free-proxy-list/master/socks5.txt",
    },
    "monosans": {
        "http":   "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",
        "socks5": "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/socks5.txt",
    },
    "proxifly": {
        "http":   "https://raw.githubusercontent.com/proxifly/free-proxy-list/main/proxies/protocols/http/data.txt",
        "socks5": "https://raw.githubusercontent.com/proxifly/free-proxy-list/main/proxies/protocols/socks5/data.txt",
    },
}

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")

# ---------------------------------------------------------------------------
# V2Ray subscription sources — pre-validated by upstream, pulled as base64.
# These provide ss/vmess/trojan/vless URIs for Shadowrocket output.
# ---------------------------------------------------------------------------
V2RAY_SUB_URLS: list[str] = [
    "https://raw.githubusercontent.com/mahdibland/V2RayAggregator/master/sub/sub_merge_base64.txt",
]

_V2RAY_SCHEMES = ("ss://", "vmess://", "trojan://", "vless://", "hysteria2://", "hy2://")

# ---------------------------------------------------------------------------
# Validation knobs
# ---------------------------------------------------------------------------
PER_SOURCE_QUOTA = 17           # cap per source
TIMEOUT_SEC = 3                 # per-probe timeout
CONCURRENCY = 80                # validation thread pool size
FETCH_TIMEOUT_SEC = 30          # source fetch timeout

# Strict quality gates — ALL must pass for a proxy to survive validation.
MAX_LATENCY_MS = 800            # median latency hard cap (per probe URL)
MAX_JITTER_MS = 400             # max - min across samples (stability check)
MIN_THROUGHPUT_KBPS = 15        # 100KB download throughput floor
PROBE_SAMPLES = 3               # samples per endpoint (for median + jitter)

# Probe both HTTP and HTTPS — many free proxies forward http but not https.
# Each endpoint is sampled PROBE_SAMPLES times for jitter measurement.
PROBE_URLS = [
    "http://www.gstatic.com/generate_204",        # plain HTTP forwarding
    "https://cp.cloudflare.com/generate_204",     # HTTPS forwarding + diff endpoint
]

# Throughput probe: download exactly 100KB via cloudflare's speedtest API.
# Filters proxies that ping low but transfer at dial-up speeds.
THROUGHPUT_URL = "https://speed.cloudflare.com/__down?bytes=102400"
THROUGHPUT_BYTES = 100 * 1024
THROUGHPUT_TIMEOUT_SEC = 8      # generous — let slow proxies fail honestly

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


def fetch_v2ray_sub(url: str) -> list[str]:
    """Fetch a base64-encoded V2Ray subscription and return decoded URI lines.

    Trusted upstream — no local probe validation (requests can't speak ss/vmess).
    """
    try:
        r = requests.get(url, timeout=FETCH_TIMEOUT_SEC)
        r.raise_for_status()
    except RequestException as e:
        print(f"  [WARN] v2ray sub fetch failed {url}: {e}")
        return []

    try:
        decoded = base64.b64decode(r.text.strip()).decode("utf-8", errors="ignore")
    except Exception as e:
        print(f"  [WARN] v2ray sub decode failed {url}: {e}")
        return []

    uris: list[str] = []
    for line in decoded.splitlines():
        line = line.strip()
        if line and any(line.startswith(s) for s in _V2RAY_SCHEMES):
            uris.append(line)
    return uris


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


def _probe_samples(
    session: requests.Session, proxies: dict, url: str
) -> tuple[bool, float, float]:
    """Probe an endpoint PROBE_SAMPLES times. Fast-fail on any error.

    Returns (ok, median_ms, jitter_ms). jitter = max - min across samples,
    catches proxies that look fine on one ping but actually flap.
    """
    samples: list[float] = []
    for _ in range(PROBE_SAMPLES):
        ok, ms = _probe(session, proxies, url)
        if not ok:
            return False, float("inf"), float("inf")
        samples.append(ms)
    samples.sort()
    median = samples[len(samples) // 2]
    jitter = samples[-1] - samples[0]
    return True, median, jitter


def _measure_throughput(session: requests.Session, proxies: dict) -> float:
    """Download THROUGHPUT_BYTES through the proxy. Return KB/s (0 on fail).

    Truncated responses (e.g. proxy returning a tiny error page instead of
    the speedtest payload) are rejected — many free proxies fake success.
    """
    try:
        start = time.monotonic()
        r = session.get(
            THROUGHPUT_URL,
            proxies=proxies,
            timeout=THROUGHPUT_TIMEOUT_SEC,
            stream=True,
            allow_redirects=True,
        )
        if r.status_code != 200:
            return 0.0
        downloaded = 0
        for chunk in r.iter_content(chunk_size=8192):
            downloaded += len(chunk)
            if downloaded >= THROUGHPUT_BYTES:
                break
        elapsed = time.monotonic() - start
    except Exception:
        return 0.0

    # Guard against early-close / wrong-sized payloads
    if elapsed <= 0 or downloaded < THROUGHPUT_BYTES * 0.8:
        return 0.0
    return (downloaded / 1024) / elapsed


def check_proxy(proxy: str, proxy_type: str) -> Optional[tuple[str, str, float]]:
    """Strict validation pipeline.

    A proxy survives only if ALL conditions hold:
      - PROBE_SAMPLES consecutive successes on EVERY PROBE_URL
      - Median latency < MAX_LATENCY_MS on every probe
      - Jitter (max-min) < MAX_JITTER_MS on every probe
      - 100KB download throughput >= MIN_THROUGHPUT_KBPS

    Cheap checks run first — bad proxies fail fast without paying for the
    throughput download. Returns (proxy, type, avg_median_ms) on success.
    """
    if proxy_type == "http":
        proxy_url = f"http://{proxy}"
    else:
        # 'socks5' (vs 'socks5h') resolves DNS locally — faster for validation
        proxy_url = f"socks5://{proxy}"
    proxies = {"http": proxy_url, "https": proxy_url}

    medians: list[float] = []
    with requests.Session() as s:
        # Stage 1: latency + jitter on each probe URL
        for probe_url in PROBE_URLS:
            ok, median, jitter = _probe_samples(s, proxies, probe_url)
            if not ok or median > MAX_LATENCY_MS or jitter > MAX_JITTER_MS:
                return None
            medians.append(median)

        # Stage 2: throughput (most expensive — gated by latency stage)
        if _measure_throughput(s, proxies) < MIN_THROUGHPUT_KBPS:
            return None

    avg_ms = round(sum(medians) / len(medians))
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
        f"# Validated: {len(proxies)} nodes ({len(PROBE_URLS)} probes × {PROBE_SAMPLES} samples)",
        f"# Gates: <{MAX_LATENCY_MS}ms median | <{MAX_JITTER_MS}ms jitter | >={MIN_THROUGHPUT_KBPS}KB/s",
        f"# Strategy: per-source top {PER_SOURCE_QUOTA} from {len(SOURCES)} sources",
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
# Validated: {len(proxies)} nodes ({len(PROBE_URLS)} probes × {PROBE_SAMPLES} samples)
# Gates: <{MAX_LATENCY_MS}ms median | <{MAX_JITTER_MS}ms jitter | >={MIN_THROUGHPUT_KBPS}KB/s
# Strategy: per-source top {PER_SOURCE_QUOTA} from {len(SOURCES)} sources

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
# Output: Shadowrocket (base64-encoded URI list)
# ---------------------------------------------------------------------------
def generate_shadowrocket(
    proxies: list[tuple[str, str, float]],
    v2ray_uris: list[str],
) -> str:
    """Generate Shadowrocket subscription (base64-encoded URI list).

    Combines locally-validated http/socks5 proxies with upstream V2Ray URIs
    (ss/vmess/trojan/vless) into one subscription.
    """
    from urllib.parse import quote

    uris: list[str] = []

    # Validated http/socks5 proxies → URI form
    for i, (proxy, ptype, latency) in enumerate(proxies, 1):
        ip, port = proxy.split(":", 1)
        name = quote(f"{ptype.upper()}-{i} [{latency}ms]")
        if ptype == "socks5":
            uris.append(f"socks5://{ip}:{port}#{name}")
        else:
            # Shadowrocket accepts http:// scheme for plain HTTP proxies
            uris.append(f"http://{ip}:{port}#{name}")

    # V2Ray URIs (ss/vmess/trojan/vless — pre-validated by upstream)
    uris.extend(v2ray_uris)

    content = "\n".join(uris) + "\n"
    return base64.b64encode(content.encode("utf-8")).decode("utf-8")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # ---- Fetch + cross-source dedup ----
    # A proxy is attributed to the FIRST source (in SOURCES dict order) that
    # lists it. This makes per-source counts reflect "unique contributions"
    # and prevents the same node from being validated twice.
    print("=> Fetching proxy lists from multiple sources...")
    seen: set[tuple[str, str]] = set()
    by_source: dict[str, dict[str, list[str]]] = {}
    for source_name, urls in SOURCES.items():
        by_source[source_name] = {}
        for ptype, url in urls.items():
            unique: list[str] = []
            for p in fetch(url):
                key = (p, ptype)
                if key not in seen:
                    seen.add(key)
                    unique.append(p)
            by_source[source_name][ptype] = unique
            print(f"   {source_name:>15} {ptype:>6}: {len(unique):>4} unique")

    if not seen:
        print("[ERROR] No proxies fetched, aborting.")
        return

    # ---- Validate per-source, then per-source top-N ----
    # Each source contributes up to PER_SOURCE_QUOTA fastest survivors.
    # If a source has fewer survivors, it simply contributes fewer.
    print("=> Validating proxies (2-probe cross-validation)...")
    final: list[tuple[str, str, float]] = []
    for source_name, types_dict in by_source.items():
        per_source_valid: list[tuple[str, str, float]] = []
        for ptype, candidates in types_dict.items():
            if not candidates:
                continue
            per_source_valid.extend(validate_proxies(candidates, ptype))
        per_source_valid.sort(key=lambda x: x[2])
        kept = per_source_valid[:PER_SOURCE_QUOTA]
        print(
            f"   {source_name:>15}: kept {len(kept):>2} "
            f"of {len(per_source_valid):>3} valid (quota {PER_SOURCE_QUOTA})"
        )
        final.extend(kept)

    # Final cross-source sort by latency for the output.
    final.sort(key=lambda x: x[2])
    print(f"=> Final list: {len(final)} proxies")

    if not final:
        print("[WARN] No valid http/socks5 proxies found, skipping qx/clash output.")
    else:
        # Quick sanity print
        for proxy, ptype, latency in final[:5]:
            print(f"   #{ptype}: {proxy} ({latency}ms)")

        qx_path = os.path.join(OUTPUT_DIR, "qx.txt")
        with open(qx_path, "w") as f:
            f.write(generate_qx(final))
        print(f"   => {qx_path}")

        clash_path = os.path.join(OUTPUT_DIR, "clash.yaml")
        with open(clash_path, "w") as f:
            f.write(generate_clash(final))
        print(f"   => {clash_path}")

    # ---- Fetch V2Ray subscriptions (ss/vmess/trojan/vless) ----
    print("=> Fetching V2Ray subscriptions (trusted upstream)...")
    v2ray_uris: list[str] = []
    for url in V2RAY_SUB_URLS:
        uris = fetch_v2ray_sub(url)
        print(f"   {url.split('/')[-2]:>20}: {len(uris)} URIs")
        v2ray_uris.extend(uris)

    total_rocket = len(final) + len(v2ray_uris)
    if total_rocket == 0:
        print("[WARN] No nodes at all (http/socks5 + v2ray), keeping previous output.")
        return

    rocket_path = os.path.join(OUTPUT_DIR, "shadowrocket.txt")
    with open(rocket_path, "w") as f:
        f.write(generate_shadowrocket(final, v2ray_uris))
    print(f"   => {rocket_path} ({total_rocket} nodes: {len(final)} validated + {len(v2ray_uris)} v2ray)")

    print("=> Done!")


if __name__ == "__main__":
    main()
