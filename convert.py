#!/usr/bin/env python3
"""
Convert free proxy list to QX / Clash subscription format.
Runs locally or via GitHub Actions.
"""

import subprocess
import datetime
import os

HTTP_URL = "https://raw.githubusercontent.com/databay-labs/free-proxy-list/master/http.txt"
SOCKS5_URL = "https://raw.githubusercontent.com/databay-labs/free-proxy-list/master/socks5.txt"
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")


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


def generate_qx(http_proxies: list[str], socks5_proxies: list[str]) -> str:
    """Generate Quantumult X proxy list snippet."""
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        f"# Free Proxy List for Quantumult X",
        f"# Updated: {now}",
        f"# HTTP: {len(http_proxies)} | SOCKS5: {len(socks5_proxies)}",
        "",
    ]

    for i, proxy in enumerate(http_proxies, 1):
        ip, port = proxy.rsplit(":", 1)
        lines.append(f"HTTP-{i} = http, {ip}, {port}")

    for i, proxy in enumerate(socks5_proxies, 1):
        ip, port = proxy.rsplit(":", 1)
        lines.append(f"SOCKS5-{i} = socks5, {ip}, {port}")

    return "\n".join(lines) + "\n"


def generate_clash(http_proxies: list[str], socks5_proxies: list[str]) -> str:
    """Generate full Clash Meta (Mihomo) config in YAML format."""
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # Collect all proxy names for proxy groups
    proxy_names: list[str] = []
    proxy_blocks: list[str] = []

    for i, proxy in enumerate(http_proxies, 1):
        ip, port = proxy.rsplit(":", 1)
        name = f"HTTP-{i}"
        proxy_names.append(name)
        proxy_blocks.append(
            f'  - name: "{name}"\n'
            f"    type: http\n"
            f"    server: {ip}\n"
            f"    port: {port}"
        )

    for i, proxy in enumerate(socks5_proxies, 1):
        ip, port = proxy.rsplit(":", 1)
        name = f"SOCKS5-{i}"
        proxy_names.append(name)
        proxy_blocks.append(
            f'  - name: "{name}"\n'
            f"    type: socks5\n"
            f"    server: {ip}\n"
            f"    port: {port}"
        )

    # Build proxy name list for groups
    names_yaml = "\n".join(f'      - "{n}"' for n in proxy_names)

    header = f"""\
# Clash Meta (Mihomo) Config - Free Proxy List
# Updated: {now}
# HTTP: {len(http_proxies)} | SOCKS5: {len(socks5_proxies)}

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

    # Quantumult X
    qx_content = generate_qx(http_proxies, socks5_proxies)
    qx_path = os.path.join(OUTPUT_DIR, "qx.txt")
    with open(qx_path, "w") as f:
        f.write(qx_content)
    print(f"   => {qx_path}")

    # Clash
    clash_content = generate_clash(http_proxies, socks5_proxies)
    clash_path = os.path.join(OUTPUT_DIR, "clash.yaml")
    with open(clash_path, "w") as f:
        f.write(clash_content)
    print(f"   => {clash_path}")

    print("=> Done!")


if __name__ == "__main__":
    main()
