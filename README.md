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

### jsdelivr CDN mirror (recommended for China)

If `raw.githubusercontent.com` triggers SSL errors in your client (common GFW symptom), use jsdelivr — it's faster and rarely blocked:

```
https://cdn.jsdelivr.net/gh/{username}/free-proxy-sub@data/qx.txt
https://cdn.jsdelivr.net/gh/{username}/free-proxy-sub@data/clash.yaml
```

Cache TTL is up to 12 hours; append `?cache=invalidate` to force-refresh.

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

## Notes for forkers

If you fork this and run your own subscription, please be considerate of the upstream sources — they hand out these lists for free. For high-volume or popular deployments:

- **Mirror upstream lists** into your own repo and pull from there instead of re-fetching the originals every run
- **Self-host the Loyalsoldier rule files** via your own CDN (the jsdelivr URLs are great for personal use, but a popular fork can dent the cache hit rate)
- **Stretch the cron interval** further if your users tolerate older data — proxies don't actually rotate every 2 hours

And star the upstream projects below — they're the ones doing the hard work.

## Acknowledgements

- [databay-labs/free-proxy-list](https://github.com/databay-labs/free-proxy-list) — strict-SSL HTTP/SOCKS5 lists, refreshed every ~5 min
- [monosans/proxy-list](https://github.com/monosans/proxy-list) — geolocation-tagged proxy lists, hourly refresh
- [proxifly/free-proxy-list](https://github.com/proxifly/free-proxy-list) — high-volume HTTP/SOCKS lists, ~5 min refresh
- [Loyalsoldier/clash-rules](https://github.com/Loyalsoldier/clash-rules) — community-maintained Clash rule providers

## License

[MIT](LICENSE) © steam-100
