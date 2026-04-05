#!/usr/bin/env python3
"""
Convert free proxy list to QX / Clash subscription format.
Validates proxies and keeps only the fastest ones.
"""

import subprocess
import datetime
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

HTTP_URL = "https://raw.githubusercontent.com/databay-labs/free-proxy-list/master/http.txt"
SOCKS5_URL = "https://raw.githubusercontent.com/databay-labs/free-proxy-list/master/socks5.txt"
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")

# Validation settings
MAX_PROXIES = 50
TIMEOUT_SEC = 3
TEST_URL = "http://www.gstatic.com/generate_204"
CONCURRENCY = 80


def fetch(url: str) -> list[str]:
    """Fetch proxy list from URL via curl and return non-empty lines."""
    try:
        result = subprocess.run(
            ["curl", "-sL", "--connect-timeout", "30", url],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode != 0:
            print(f"  [WARN] curl failed for {url}: {result.stderr}")
            return []
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]
    except Exception as e:
        print(f"  [WARN] Failed to fetch {url}: {e}")
        return []


def check_proxy(proxy: str, proxy_type: str) -> tuple[str, str, float] | None:
    """
    Test a single proxy by making a request through it.
    Returns (proxy, type, latency_ms) if valid, None otherwise.
    """
    if proxy_type == "http":
        proxy_flag = "-x"
        proxy_url = f"http://{proxy}"
    else:
        proxy_flag = "--socks5"
        proxy_url = proxy

    try:
        start = time.monotonic()
        result = subprocess.run(
            [
                "curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
                proxy_flag, proxy_url,
                "--connect-timeout", str(TIMEOUT_SEC),
                "--max-time", str(TIMEOUT_SEC),
                TEST_URL,
            ],
            capture_output=True, text=True, timeout=TIMEOUT_SEC + 2,
        )
        elapsed = (time.monotonic() - start) * 1000  # ms

        status = result.stdout.strip()
        if status in ("200", "204") and elapsed < TIMEOUT_SEC * 1000:
            return (proxy, proxy_type, round(elapsed))
    except Exception:
        pass
    return None


def validate_proxies(
    proxies: list[str], proxy_type: str
) -> list[tuple[str, str, float]]:
    """Validate proxies concurrently and return working ones with latency."""
    valid: list[tuple[str, str, float]] = []
    total = len(proxies)

    with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
        futures = {
            pool.submit(check_proxy, p, proxy_type): p for p in proxies
        }
        done_count = 0
        for future in as_completed(futures):
            done_count += 1
            if done_count % 100 == 0 or done_count == total:
                print(f"   [{proxy_type.upper()}] {done_count}/{total} tested, {len(valid)} alive")
            result = future.result()
            if result is not None:
                valid.append(result)

    # Sort by latency (fastest first)
    valid.sort(key=lambda x: x[2])
    return valid


def generate_qx(proxies: list[tuple[str, str, float]]) -> str:
    """Generate Quantumult X proxy list snippet."""
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        f"# Free Proxy List for Quantumult X",
        f"# Updated: {now}",
        f"# Validated: {len(proxies)} nodes (sorted by latency)",
        f"# Timeout: {TIMEOUT_SEC}s | Max: {MAX_PROXIES}",
        "",
    ]

    for i, (proxy, ptype, latency) in enumerate(proxies, 1):
        ip, port = proxy.rsplit(":", 1)
        tag = ptype.upper()
        lines.append(f"{tag}-{i}[{latency}ms] = {ptype}, {ip}, {port}")

    return "\n".join(lines) + "\n"


def generate_clash(proxies: list[tuple[str, str, float]]) -> str:
    """Generate full Clash Meta (Mihomo) config in YAML format."""
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    proxy_names: list[str] = []
    proxy_blocks: list[str] = []

    for i, (proxy, ptype, latency) in enumerate(proxies, 1):
        ip, port = proxy.rsplit(":", 1)
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
# Validated: {len(proxies)} nodes (sorted by latency)

mixed-port: 7890
allow-lan: false
mode: rule
log-level: info

dns:
  enable: true
  enhanced-mode: fake-ip
  nameserver:
    - 223.5.5.5
    - 119.29.29.29

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

    rules_section = """\
rules:
  - GEOIP,CN,DIRECT
  - MATCH,Proxy
"""

    return header + proxies_section + "\n" + groups_section + rules_section


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    print("=> Fetching proxy lists...")
    http_proxies = fetch(HTTP_URL)
    socks5_proxies = fetch(SOCKS5_URL)
    print(f"   HTTP: {len(http_proxies)} | SOCKS5: {len(socks5_proxies)}")

    if not http_proxies and not socks5_proxies:
        print("[ERROR] No proxies fetched, aborting.")
        return

    # Validate all proxies
    print("=> Validating proxies (this may take a while)...")
    valid: list[tuple[str, str, float]] = []

    if http_proxies:
        valid.extend(validate_proxies(http_proxies, "http"))
    if socks5_proxies:
        valid.extend(validate_proxies(socks5_proxies, "socks5"))

    # Sort all by latency, keep top N
    valid.sort(key=lambda x: x[2])
    valid = valid[:MAX_PROXIES]
    print(f"=> Kept {len(valid)} fastest proxies")

    if not valid:
        print("[WARN] No valid proxies found, keeping previous output.")
        return

    # Print top 5 for quick check
    for proxy, ptype, latency in valid[:5]:
        print(f"   #{ptype}: {proxy} ({latency}ms)")

    # Quantumult X
    qx_content = generate_qx(valid)
    qx_path = os.path.join(OUTPUT_DIR, "qx.txt")
    with open(qx_path, "w") as f:
        f.write(qx_content)
    print(f"   => {qx_path}")

    # Clash Meta
    clash_content = generate_clash(valid)
    clash_path = os.path.join(OUTPUT_DIR, "clash.yaml")
    with open(clash_path, "w") as f:
        f.write(clash_content)
    print(f"   => {clash_path}")

    print("=> Done!")


if __name__ == "__main__":
    main()
