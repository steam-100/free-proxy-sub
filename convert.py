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
    """Generate Clash proxy config in YAML format."""
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        f"# Free Proxy List for Clash",
        f"# Updated: {now}",
        f"# HTTP: {len(http_proxies)} | SOCKS5: {len(socks5_proxies)}",
        "",
        "proxies:",
    ]

    for i, proxy in enumerate(http_proxies, 1):
        ip, port = proxy.rsplit(":", 1)
        lines.append(f'  - name: "HTTP-{i}"')
        lines.append(f"    type: http")
        lines.append(f"    server: {ip}")
        lines.append(f"    port: {port}")

    for i, proxy in enumerate(socks5_proxies, 1):
        ip, port = proxy.rsplit(":", 1)
        lines.append(f'  - name: "SOCKS5-{i}"')
        lines.append(f"    type: socks5")
        lines.append(f"    server: {ip}")
        lines.append(f"    port: {port}")

    return "\n".join(lines) + "\n"


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
