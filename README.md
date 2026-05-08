# Free Proxy Subscription

Auto-validated free proxy list, published in **Quantumult X** and **Clash Meta (Mihomo)** subscription formats.

- 🔄 Auto-updated every **2 hours** via GitHub Actions
- 🌐 Aggregated from **3 upstream sources** (databay-labs, monosans, proxifly)
- ✅ **Strict quality gates**: HTTP+HTTPS probe forwarding × 3 samples each — survivors must hit `<500ms` median, `<200ms` jitter, `≥30KB/s` throughput
- 📊 **Per-source quota**: each source contributes its top 17 fastest survivors (final list ≤ 51 nodes, sorted by latency)
- 🛡️ Diversified — insulated against any single upstream going dark
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
sources (databay-labs + monosans + proxifly)
  ├── fetch each source separately
  ├── parse + validate (regex, port range, octet range)
  ├── cross-source dedup — proxy attributed to first source listing it
  ├── strict gates (fail-fast pipeline):
  │     ├── HTTP probe   × 3 samples   → median <500ms, jitter <200ms
  │     ├── HTTPS probe  × 3 samples   → median <500ms, jitter <200ms
  │     └── 100KB throughput download  → ≥30KB/s
  ├── per-source top-N selection (default 17 each, sorted by latency)
  ├── final cross-source sort by latency
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
