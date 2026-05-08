# Free Proxy Subscription

Auto-validated free proxy list, published in **Quantumult X** and **Clash Meta (Mihomo)** subscription formats.

- 🔄 Auto-updated every **30 minutes** via GitHub Actions
- 🌐 Aggregated from **3 upstream sources** (databay-labs, monosans, proxifly), deduplicated by `ip:port`
- ✅ Cross-validated with **2 probes** (gstatic + cloudflare), only proxies passing both are kept
- 📊 Sorted by average latency, top **50** retained
- 🧹 Subscription files live on the orphan **`data`** branch — `main` history stays clean

## Subscription URLs

Replace `{username}` with your GitHub username.

### Quantumult X

```
https://raw.githubusercontent.com/{username}/free-proxy-sub/data/qx.txt
```

### Clash Meta / Mihomo

```
https://raw.githubusercontent.com/{username}/free-proxy-sub/data/clash.yaml
```

The Clash config ships with curated **rule-providers** (Loyalsoldier upstream):

- 🚫 Ad / tracker block (`reject`)
- 🍎 Apple / iCloud direct
- 🔍 Google services proxy
- 📨 Telegram CIDR proxy
- 🇨🇳 China CIDR direct
- 🤖 OpenAI / Claude / Anthropic explicit proxy

### GitHub Pages mirror (alternative)

```
https://{username}.github.io/free-proxy-sub/qx.txt
https://{username}.github.io/free-proxy-sub/clash.yaml
```

## How it works

```
sources (3x http + 3x socks5)
  ├── fetch (concurrent)
  ├── parse + validate (regex, port range, octet range)
  ├── dedupe by ip:port
  ├── cross-probe (gstatic 204 + cloudflare 204) ─ both must pass
  ├── sort by avg latency, keep top 50
  └── emit qx.txt + clash.yaml → orphan `data` branch (force-push)
```

## Local development

```bash
pip install -r requirements.txt
python convert.py
# outputs land in ./output/
```

## Sources

- [databay-labs/free-proxy-list](https://github.com/databay-labs/free-proxy-list)
- [monosans/proxy-list](https://github.com/monosans/proxy-list)
- [proxifly/free-proxy-list](https://github.com/proxifly/free-proxy-list)
- Clash rules: [Loyalsoldier/clash-rules](https://github.com/Loyalsoldier/clash-rules)
