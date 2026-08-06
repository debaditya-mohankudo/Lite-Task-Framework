---
name: stock-tracker
description: Use this skill when asked to "update <ticker> tracker", "run the <ticker> daily check", or when invoked by a daily cron job for a tracked equity position. Generic thesis monitor -- one skill, one config file per stock -- maintaining a running log (price, Wyckoff phase, shareholding, news) in SQLite and writing a daily diff into the Obsidian vault. Requires args=<slug> naming a config in ~/workspace/stock-tracker-mcp/stock_tracker/configs/.
user-invocable: true
---

# Stock Tracker (generic)

Thesis monitor for a tracked equity position. Surfaces only what *changed*
since the last run — not a full re-dump. A quiet day is a valid result.

**One skill, many stocks.** Everything specific to a ticker (yfinance
symbol, DB path, vault note path, news search terms, the thesis baseline,
and which watch-level rule applies) lives in a small JSON config, not in
this file. Adding a new tracked stock is one new config file, not a new
skill.

**Prefer the `wyckoff-analyzer` MCP tools over raw bash/python for the
mechanical steps** where they cover the same ground: `db_init_state(slug)`
replaces Step 0/Step 1's manual config-load + prior-state queries in one
call, and `update_run(slug)` replaces Step 2 (live price fetch + write) and
Step 4/4a/4c (Wyckoff phase, fib levels, and DMA/crossover signal,
including the "only write if meaningfully changed" logic) in one call. They
read configs from this
repo's `stock_tracker/configs/<slug>.json` and write to the same
`db_path` the config declares — same source of truth, just less
boilerplate. Delivery % (Step 2b), news (Step 3), sector context (Step 3b),
shareholding/earnings (Step 5/5b), and the thesis checkpoint/vault write
(Step 6-8) still need the manual/interactive steps below — the MCP tools
don't cover those.

## Invocation

```
/stock-tracker <slug>
```

`<slug>` names a file at `~/workspace/stock-tracker-mcp/stock_tracker/configs/<slug>.json`.
Known slugs as of writing: `thangamayl`, `happiest_minds`. If no slug is
given, ask which config to run, or list `~/workspace/stock-tracker-mcp/stock_tracker/configs/*.json`
and ask the user to pick.

---

## Step 0 — Load config, init DB if needed

```bash
python3 - <<'EOF'
import json, os

slug = "<slug>"  # substitute the invocation arg
cfg_path = os.path.expanduser(f"~/workspace/stock-tracker-mcp/stock_tracker/configs/{slug}.json")
with open(cfg_path) as f:
    cfg = json.load(f)
print(json.dumps(cfg, indent=2))
EOF
```

Hold `cfg` in context for every step below. `cfg["db_path"]` (expanduser it),
`cfg["yf_ticker"]`, `cfg["vault_note"]`, `cfg["news_queries"]`,
`cfg["wyckoff_period"]`, `cfg["thesis_mode"]` (`"distribution_watch"` or
`"accumulation_watch"`), `cfg["watch_level"]` (nullable),
`cfg["invalidation_trigger"]` are all referenced by name below as
`{{cfg.field}}`. `cfg["sector"]` (optional block: `name`, `cyclicality`,
`peer_tickers`, `benchmark_ticker`) and `cfg["accumulation_trigger"]`
(optional, `distribution_watch` configs only) are both nullable/absent-by-default
— see Step 3b and Step 4b respectively for what to do when present.

```bash
mkdir -p ~/project-companion/data
python3 - <<'EOF'
import sqlite3, os

db_path = os.path.expanduser("{{cfg.db_path}}")
db = sqlite3.connect(db_path)
db.executescript("""
CREATE TABLE IF NOT EXISTS price_history (
    date TEXT PRIMARY KEY,
    open REAL, high REAL, low REAL, close REAL, volume INTEGER,
    pe_ratio REAL, market_cap REAL
);
CREATE TABLE IF NOT EXISTS news_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT, headline TEXT, source TEXT, url TEXT,
    category TEXT CHECK(category IN
        ('block_deal','promoter','product','earnings','macro','mna','other')),
    sentiment TEXT CHECK(sentiment IN ('bullish','bearish','neutral')),
    keywords TEXT
);
CREATE TABLE IF NOT EXISTS shareholding (
    quarter TEXT PRIMARY KEY,
    promoter_pct REAL, fii_pct REAL, dii_pct REAL, retail_pct REAL
);
CREATE TABLE IF NOT EXISTS wyckoff_state (
    date TEXT PRIMARY KEY,
    phase TEXT, score INTEGER, confidence INTEGER,
    support_level REAL, resistance_level REAL,
    notes TEXT
);
CREATE TABLE IF NOT EXISTS delivery_data (
    date TEXT PRIMARY KEY,
    qty_traded INTEGER, deliverable_qty INTEGER, delivery_pct REAL,
    avg_10d_delivery_pct REAL, delta_vs_avg_pp REAL,
    notes TEXT
);
CREATE TABLE IF NOT EXISTS fib_levels (
    date TEXT PRIMARY KEY,
    swing_high REAL, swing_low REAL, swing_high_date TEXT, swing_low_date TEXT,
    direction TEXT CHECK(direction IN ('uptrend','downtrend')),
    level_236 REAL, level_382 REAL, level_500 REAL, level_618 REAL, level_786 REAL,
    nearest_level TEXT, nearest_level_price REAL, distance_pct REAL,
    notes TEXT
);
CREATE TABLE IF NOT EXISTS thesis_checkpoints (
    date TEXT PRIMARY KEY,
    bull_case_intact INTEGER,
    watch_items TEXT,
    invalidation_trigger TEXT
);
CREATE TABLE IF NOT EXISTS quarterly_results (
    quarter TEXT PRIMARY KEY,
    report_date TEXT,
    revenue_cr REAL, revenue_est_cr REAL,
    pat_cr REAL, pat_est_cr REAL,
    ebitda_margin_pct REAL, eps REAL,
    yoy_revenue_pct REAL, yoy_pat_pct REAL,
    beat_miss TEXT, notes TEXT
);
CREATE TABLE IF NOT EXISTS analyst_forecasts (
    date TEXT PRIMARY KEY,
    target_price REAL, analyst_count INTEGER,
    buy_count INTEGER, hold_count INTEGER, sell_count INTEGER,
    next_earnings_date TEXT, fy_rev_est_cr REAL, fy_pat_est_cr REAL,
    source TEXT
);
CREATE TABLE IF NOT EXISTS sector_forecasts (
    quarter TEXT PRIMARY KEY,
    outlook TEXT CHECK(outlook IN ('bullish','bearish','neutral','mixed')),
    summary TEXT, sources TEXT
);
CREATE TABLE IF NOT EXISTS dma_levels (
    date TEXT PRIMARY KEY,
    close REAL,
    dma_10 REAL, dma_20 REAL, dma_50 REAL, dma_100 REAL, dma_200 REAL,
    price_vs_20 TEXT CHECK(price_vs_20 IN ('above','below')),
    price_vs_50 TEXT CHECK(price_vs_50 IN ('above','below')),
    price_vs_200 TEXT CHECK(price_vs_200 IN ('above','below')),
    gap_20_50_pct REAL,
    crossover_state TEXT CHECK(crossover_state IN ('golden_cross','death_cross','none')),
    notes TEXT
);
""")
db.commit()
db.close()
print("DB ready")
EOF
```

Schema is identical across every tracked stock — this is what makes one
generic skill possible. Nothing in the schema is ticker-specific.

---

## Step 1 — Read prior state

Same query shape for every config — only `db_path` changes:

```python
import sqlite3, os, json
db = sqlite3.connect(os.path.expanduser("{{cfg.db_path}}"))
db.row_factory = sqlite3.Row

prior_price        = dict(db.execute("SELECT * FROM price_history      ORDER BY date DESC LIMIT 1").fetchone() or {})
prior_wyckoff       = dict(db.execute("SELECT * FROM wyckoff_state       ORDER BY date DESC LIMIT 1").fetchone() or {})
prior_fib           = dict(db.execute("SELECT * FROM fib_levels          ORDER BY date DESC LIMIT 1").fetchone() or {})
prior_dma           = dict(db.execute("SELECT * FROM dma_levels          ORDER BY date DESC LIMIT 1").fetchone() or {})
prior_delivery      = dict(db.execute("SELECT * FROM delivery_data       ORDER BY date DESC LIMIT 1").fetchone() or {})
prior_shareholding  = dict(db.execute("SELECT * FROM shareholding     ORDER BY quarter DESC LIMIT 1").fetchone() or {})
prior_news_date     = (db.execute("SELECT MAX(date) FROM news_events").fetchone() or (None,))[0]
prior_checkpoint    = dict(db.execute("SELECT * FROM thesis_checkpoints ORDER BY date DESC LIMIT 1").fetchone() or {})
prior_quarterly     = dict(db.execute("SELECT * FROM quarterly_results ORDER BY quarter DESC LIMIT 1").fetchone() or {})
prior_forecast      = dict(db.execute("SELECT * FROM analyst_forecasts ORDER BY date DESC LIMIT 1").fetchone() or {})
prior_sector_forecast = dict(db.execute("SELECT * FROM sector_forecasts ORDER BY quarter DESC LIMIT 1").fetchone() or {})
db.close()

print(json.dumps({
    "prior_price": prior_price, "prior_wyckoff": prior_wyckoff, "prior_fib": prior_fib,
    "prior_dma": prior_dma,
    "prior_delivery": prior_delivery,
    "prior_shareholding": prior_shareholding, "prior_news_date": prior_news_date,
    "prior_checkpoint": prior_checkpoint, "prior_quarterly": prior_quarterly,
    "prior_forecast": prior_forecast, "prior_sector_forecast": prior_sector_forecast,
}, indent=2, default=str))
```

Hold this baseline in context — you'll diff against it in Step 6.

---

## Step 2 — Fetch live price

**Call `mcp__wyckoff-analyzer__update_run(slug)` for this step** — it fetches
and writes `price_history` (unconditionally) as part of its single call, along
with Steps 4/4a/4c below. Only fall back to the manual script if the MCP tool
errors or is unavailable.

```bash
cd ~/workspace/claude_for_mac_local && uv run python - <<'EOF'
import yfinance as yf, json, sqlite3, os
from datetime import date

ticker = yf.Ticker("{{cfg.yf_ticker}}")
hist = ticker.history(period="5d")
if hist.empty:
    print(json.dumps({"error": "no data"}))
else:
    row = hist.iloc[-1]
    info = {}
    try:
        info = ticker.info
    except Exception:
        pass
    today = date.today().isoformat()
    data = {
        "date": today,
        "open": round(float(row["Open"]), 2),
        "high": round(float(row["High"]), 2),
        "low":  round(float(row["Low"]), 2),
        "close": round(float(row["Close"]), 2),
        "volume": int(row["Volume"]),
        "pe_ratio":   info.get("trailingPE"),
        "market_cap": info.get("marketCap"),
    }
    db = sqlite3.connect(os.path.expanduser("{{cfg.db_path}}"))
    db.execute("""
        INSERT OR REPLACE INTO price_history
        (date,open,high,low,close,volume,pe_ratio,market_cap)
        VALUES (:date,:open,:high,:low,:close,:volume,:pe_ratio,:market_cap)
    """, data)
    db.commit(); db.close()
    print(json.dumps(data, indent=2))
EOF
```

If the fetch errors (e.g. wrong Yahoo symbol), stop and surface it rather
than guessing a corrected ticker — see `thangamayl.json`'s note that
`THANGAMAYIL.NS` 404s and `THANGAMAYL.NS` is correct; a wrong symbol should
be fixed in the config, not patched around at runtime.

Compute day-over-day change vs `prior_price.close`.

---

## Step 2b — Delivery % (NSE-listed configs only)

NSE-specific — skip entirely for a config whose `yf_ticker` is BSE-only
(`.BO` suffix) with no NSE listing; there is no equivalent BSE file this
step is written against.

Delivery % answers a different question than volume alone: what fraction
of traded quantity actually settled into demat accounts (real ownership
change) versus intraday/speculative churn that nets out same-day. A
Wyckoff accumulation read (Step 4) infers demand from volume + price
behavior; delivery % is a direct, independent measurement of how much of
that volume was genuine buying-to-hold — it either corroborates the
Wyckoff inference or exposes it as volume without real accumulation behind
it.

Source: NSE's daily "Security Wise Delivery Position" archive, published
after market close, no session/auth headers required:

```
https://archives.nseindia.com/archives/equities/mto/MTO_DDMMYYYY.DAT
```

Derive the NSE symbol from `cfg["yf_ticker"]` by stripping the `.NS`
suffix (e.g. `HDFCBANK.NS` → `HDFCBANK`) — this is the exact string to
match in column 3 of the file, not the display name.

```bash
python3 - <<'EOF'
import urllib.request, sqlite3, os, json
from datetime import date

symbol = "{{cfg.yf_ticker}}".replace(".NS", "")
today = date.today()
url = f"https://archives.nseindia.com/archives/equities/mto/MTO_{today.strftime('%d%m%Y')}.DAT"

req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
try:
    text = urllib.request.urlopen(req, timeout=10).read().decode()
except Exception as e:
    print(json.dumps({"error": f"fetch failed: {e}"})); raise SystemExit

row = None
for line in text.splitlines():
    fields = line.split(",")
    if len(fields) >= 7 and fields[0] == "20" and fields[2] == symbol:
        row = fields
        break

if row is None:
    # No row for today (holiday/no file yet, or symbol mismatch) -- do not
    # guess or backfill; report and move on, same discipline as Step 2's
    # "stop and surface it" rule for a bad yfinance ticker.
    print(json.dumps({"error": f"no delivery row found for {symbol} on {today.isoformat()}"}))
    raise SystemExit

# Qty fields can carry thousand-separator commas in this legacy NSE format
# for some symbols -- reconstruct from the right: last field is %, second
# -to-last is deliverable qty, everything from field[3] up to (len-2) is
# the traded-quantity fragments to rejoin.
delivery_pct = float(row[-1])
deliverable_qty = int(row[-2].replace(",", ""))
qty_traded = int("".join(row[4:-2]).replace(",", ""))

db = sqlite3.connect(os.path.expanduser("{{cfg.db_path}}"))
recent = db.execute(
    "SELECT delivery_pct FROM delivery_data ORDER BY date DESC LIMIT 10"
).fetchall()
avg_10d = round(sum(r[0] for r in recent) / len(recent), 2) if recent else None
delta_vs_avg = round(delivery_pct - avg_10d, 2) if avg_10d is not None else None

out = {
    "date": today.isoformat(),
    "qty_traded": qty_traded, "deliverable_qty": deliverable_qty,
    "delivery_pct": delivery_pct, "avg_10d_delivery_pct": avg_10d,
    "delta_vs_avg_pp": delta_vs_avg,
}
db.execute("""
    INSERT OR REPLACE INTO delivery_data
    (date, qty_traded, deliverable_qty, delivery_pct, avg_10d_delivery_pct, delta_vs_avg_pp, notes)
    VALUES (?, ?, ?, ?, ?, ?, ?)
""", (out["date"], qty_traded, deliverable_qty, delivery_pct, avg_10d, delta_vs_avg, None))
db.commit(); db.close()
print(json.dumps(out, indent=2))
EOF
```

**Safari fallback for today's number.** If the archive fetch above errors
(no row yet for today — the MTO file sometimes isn't published until well
after close, and yfinance's own cache can lag behind it too), fall back to
reading today's delivery % directly off NSE's live quote page via Safari
rather than giving up on the day's reading entirely:

```
mcp__local-mac__safari__open(url="https://www.nseindia.com/get-quotes/equity?symbol=<SYMBOL>")
mcp__local-mac__safari__read(mode="text")
```

Look for `% of Deliverable / Traded Quantity` in the page text (near
`Traded Volume`, `Traded Value`, `Total Market Cap`) — this is the same
figure as the archive file's last column, just sourced from NSE's own live
page instead of the static `.DAT` file, and it updates as soon as the
session closes rather than waiting for the archive file to publish. Confirm
the `As on <DATE>` timestamp in the page text matches the date being
recorded before writing it to `delivery_data` — do not assume it's today's
close without checking, since the page can still be showing the prior
session if fetched before market open.

**Historical backfill via the same NSE UI.** NSE's "Security-wise Archives"
report (`nseindia.com/report-detail/eq_security`) gives a real historical
delivery-% series (with `DATE`, `DELIVERABLE QTY`, `% DLY QT TO TRADED QTY`
columns and 1D/1W/1M/3M/6M/1Y/5Y/Custom range buttons) — useful for
backfilling `delivery_data` for a config that's new to this tracker, rather
than only accumulating one day at a time. The backing API 404s/503s when
hit directly (even same-origin via `fetch()` from JS) — Akamai bot
protection — so drive the actual page UI instead:

```
mcp__local-mac__safari__navigate(url="https://www.nseindia.com/report-detail/eq_security")
```

Type the symbol into the `#hsa-symbol` autocomplete field via a native
property setter (a plain `.value = ...` assignment doesn't fire React's
change handler) and dispatch an `input` event, then click one of the
date-range buttons (`1D`/`1W`/`1M`/etc. — its `textContent` matches the
button):

```js
const el = document.getElementById('hsa-symbol');
const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
setter.call(el, '<SYMBOL>');
el.dispatchEvent(new Event('input', {bubbles: true}));
// wait ~1s for the autocomplete suggestion to populate/resolve, then:
Array.from(document.querySelectorAll('button')).find(b => b.textContent.trim() === '1M').click();
```

**Give the table time to render before reading it** — the first `read`
right after clicking can still show the pre-render page; wait a beat (or
re-read once) rather than concluding the fetch failed. Once rendered,
`safari__read(mode="text")` returns the table as plain tab-separated rows —
parse `SYMBOL`, `DATE`, `DELIVERABLE QTY`, and `% DLY QT TO TRADED QTY`
directly out of that text; no HTML parsing needed. This is the preferred
path for a first-time backfill; the daily MTO archive file remains the
right tool for the ongoing one-row-per-run cadence once history exists.

Unlike Wyckoff/fib (only rewritten when meaningfully changed), **write a
`delivery_data` row every run** — it's a single new daily data point, not a
recomputed state, so there's no "no-op write" to avoid; the row for today
either exists or doesn't yet.

The first ~10 runs for a new config will have `avg_10d_delivery_pct = None`
(not enough history) — that's expected, not an error; don't fabricate a
baseline average before there's real data to average.

**Reading it alongside Wyckoff + fib:**
- Delivery % meaningfully **above** the trailing 10-day average (`delta_vs_avg_pp`
  large positive, e.g. >10pp) *while* price is near a Wyckoff support level or
  a fib retracement level → corroborates accumulation: real demand at that
  price, not just volume.
- Price rallying (e.g. through a Wyckoff resistance level or a fib level)
  on delivery % **below** the trailing average → weak/speculative
  confirmation — volume was there but not real accumulation; treat any
  "breakout" read on that day skeptically.
- Delivery % alone, with no Wyckoff/fib level nearby, is just background
  context — same rule as sector RS in Step 3b: it calibrates confidence in
  the existing structural signals, it doesn't get its own standalone
  breakout/breakdown call.

**Sector-peer relative-strength check — do this before calling a delivery-%
trend stock-specific.** A rising (or falling) delivery-% trend over several
weeks can look like a stock-specific signal when it's actually the whole
sector re-rating together, the same trap the macro-benchmark check in Step
5 guards against for shareholding data. Before reading a multi-session
delivery-% trend (not a single day's reading — that's the Wyckoff/fib
confluence check above) as evidence of something happening at this stock
specifically, pull the same window's delivery % for at least one
`cfg["sector"]["peer_tickers"]` entry via the same NSE archive/Safari method
(Step 2b's primary + fallback sources both work — the peer just needs its
own symbol substituted) and compare the trend, not just the day's number:
- If a peer shows a comparable rise/fall over the same window, read the
  trend as sector-wide — do not claim it as a stock-specific accumulation/
  distribution tell, even if the raw numbers (elevated delivery %, `ALERT`-
  crossing `delta_vs_avg_pp`) look compelling in isolation.
- Only treat a delivery-% trend as stock-specific when it diverges
  meaningfully from the peer's trend over the same window — e.g. this
  stock's delivery % climbing while a peer's stays flat or falls.
- Skip this check entirely if `cfg` has no `sector` block (same skip
  condition as Step 3b) — there's no peer to compare against.
- Example precedent (2026-08-04/05, TCS vs Infosys): TCS's delivery %
  rose ~6pp from early-July (SC period) to late-July/August (rally toward
  the fib/Wyckoff confluence zone). Read in isolation this looked like
  TCS-specific accumulating demand. Checking INFY over the identical window
  showed a nearly identical ~4.3pp rise — the same IT-sector-wide delivery
  uptrend, not something distinguishing TCS. Combined with the price-side
  finding (TCS's price recovery since its SC was in line with sector peers,
  not an outlier), the correct read was "TCS participating normally in a
  sector-wide base-building move," not "TCS-specific accumulation signal" —
  despite every individual number looking supportive before the peer check.

This mirrors Step 5's macro-benchmark discipline exactly: a signal computed
from a single stock's own data is never enough on its own to call something
stock-specific — it always needs a same-window peer or market-wide
reference before that claim is safe to make.

---

## Step 3 — Fetch news (last 24–48h)

Run every query in `cfg["news_queries"]` in parallel via web search.

For each result found:
1. Skip if published before `prior_news_date` (or older than 48h if no prior date).
2. Run the dedup check below before inserting — do NOT rely on `INSERT OR IGNORE`.
3. Classify `category`: `block_deal` / `promoter` / `product` / `earnings` / `macro` / `mna` / `other`
4. Classify `sentiment`: `bullish` / `bearish` / `neutral`
5. Insert only if dedup passes:

```python
import sqlite3, os, re

_STOPWORDS = {
    "that", "with", "from", "this", "have", "been", "will", "into", "also",
    "their", "which", "about", "after", "before", "other", "over", "under",
    "more", "some",
}
# Add the ticker's own name-words to the stopword set so headlines don't
# dedupe purely on "of course this article is about the company we're
# tracking" — extend, don't replace:
_STOPWORDS |= {w.lower() for w in cfg["display_name"].split()}

def extract_keywords(text: str) -> str:
    tokens = sorted(set(
        w for w in re.findall(r"[a-z]{4,}", text.lower())
        if w not in _STOPWORDS
    ))
    return " ".join(tokens)

def is_duplicate(db, date: str, headline: str, url: str, keywords: str) -> bool:
    """Return True if an existing row covers the same story."""
    if url and db.execute("SELECT 1 FROM news_events WHERE url=?", (url,)).fetchone():
        return True
    if db.execute("SELECT 1 FROM news_events WHERE headline=?", (headline,)).fetchone():
        return True
    new_kws = set(keywords.split())
    if not new_kws:
        return False
    existing = db.execute(
        "SELECT keywords FROM news_events WHERE date=? AND keywords IS NOT NULL", (date,)
    ).fetchall()
    for (kw_str,) in existing:
        shared = new_kws & set(kw_str.split())
        if len(shared) / len(new_kws) > 0.5:
            return True
    return False

db = sqlite3.connect(os.path.expanduser("{{cfg.db_path}}"))
for new_item in new_news:
    kw = extract_keywords(new_item["headline"])
    if not is_duplicate(db, new_item["date"], new_item["headline"], new_item.get("url", ""), kw):
        db.execute("""
            INSERT INTO news_events (date, headline, source, url, category, sentiment, keywords)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (new_item["date"], new_item["headline"], new_item["source"],
              new_item.get("url"), new_item["category"], new_item["sentiment"], kw))
db.commit(); db.close()
```

---

## Step 3b — Sector & relative-strength context (skip if `cfg` has no `sector` block)

A single stock's Wyckoff read can't tell you whether a move is *stock-specific*,
*sector-wide*, or just *the cap-tier it trades in moving as a group* — a
cyclical small-cap grinding down 20% means something different if its sector
peers are down 25% and small-caps broadly are down 20% (both, mostly
market-structure, weight the technical signal less) versus flat on both
(idiosyncratic, weight it more). This step is deliberately **multidimensional**
— sector index AND cap-tier index where both apply — not a single benchmark.
It exists to add that context, not to replace the Wyckoff read.

Configs with a `sector` block look like:

```json
"sector": {
    "name": "IT Services",
    "cyclicality": "cyclical",
    "peer_tickers": ["INFY.NS", "HCLTECH.NS", "WIPRO.NS", "TECHM.NS"],
    "benchmark_ticker": "^CNXIT",
    "cap_tier": "large",
    "cap_tier_benchmark_ticker": null
}
```

`cyclicality` is `"cyclical"` or `"defensive"` — informative context for how
much weight to put on a drawdown (a cyclical sector's decline is more often
sector rotation than company-specific distress; a defensive sector's
decline is more often idiosyncratic).

`benchmark_ticker` should be an actual **sector index** where one exists on
yfinance, not a single bellwether stock — a single company (even a large,
liquid one) carries its own idiosyncratic drivers that have nothing to do
with the tracked stock's sector, so it answers a fuzzier question than a
real index does.

`cap_tier` (`"large"` / `"mid"` / `"small"`) and `cap_tier_benchmark_ticker`
add the second dimension — is the *market-cap bucket* this stock trades in
moving together, independent of sector? Classify by actual market cap
(`yf.Ticker(t).info["marketCap"]`), not by reputation — roughly large ≈ top
100 by market cap, mid ≈ 101–250, small ≈ everything below (cutoffs move
over time, so check current AMFI/SEBI classification if precision matters).
Set `cap_tier_benchmark_ticker` to `null` for a large-cap stock — its sector
index is already large-cap-weighted, so a separate cap-tier check is
redundant.

Verify data availability before picking any index ticker — plausible-looking
names frequently 404 or return almost no history on yfinance, and the
correct working ticker format is not always the obvious one:

```bash
uv run python3 -c "
import yfinance as yf
tk = yf.Ticker('<candidate ticker>')
df = yf.download('<candidate ticker>', period='1y', interval='1wk', progress=False, auto_adjust=True)
print(df.shape, '|', tk.info.get('longName'))  # want ~50 weekly bars; a handful means don't use it
# ALSO confirm longName actually matches what you think the ticker is --
# a ticker can return data under a name you didn't expect (e.g. ^NSMIDCP
# returns 'NIFTY NEXT 50', not a midcap index, despite the ticker name).
"
```

Known-good NSE index tickers (verified 2026-08-03, all ~52 weekly bars/1y):
- Sector: `^CNXIT` (Nifty IT), `^CNXCONSUM` (Nifty Consumption — closest
  available proxy when no dedicated sector index exists, e.g.
  jewellery/gems), `^CNXAUTO` (Nifty Auto), `^CNXFMCG` (Nifty FMCG)
- Cap-tier: `NIFTYMIDCAP150.NS` (Nifty Midcap 150), `NIFTYSMLCAP250.NS`
  (Nifty Smallcap 250)
- Broad-market fallback: `^NSEI` (Nifty50)

Confirmed **unusable** despite plausible names — don't retry these without
re-verifying: `NIFTY_CONSR_DURBL.NS`, `NIFTY_MIDCAP_100.NS`-style
underscore variants generally 404, `^CNXSC`/`NIFTYSMALLCAP100.NS`/most other
small-cap-index guesses. `^NSMIDCP` resolves but is actually "NIFTY NEXT
50," not a midcap index — a naming trap, confirmed via `tk.info["longName"]`.

```bash
cd ~/workspace/claude_for_mac_local && uv run python - <<'EOF'
import yfinance as yf

tickers = ["{{cfg.yf_ticker}}"] + {{cfg.sector.peer_tickers}} + ["{{cfg.sector.benchmark_ticker}}"]
if {{cfg.sector.cap_tier_benchmark_ticker}}:
    tickers.append({{cfg.sector.cap_tier_benchmark_ticker}})
df = yf.download(tickers, period="6mo", interval="1wk", progress=False, auto_adjust=True)["Close"].dropna()

pct_6mo = ((df.iloc[-1] / df.iloc[0]) - 1) * 100
pct_3mo = ((df.iloc[-1] / df.iloc[-13]) - 1) * 100 if len(df) > 13 else None

print("6-month % change:")
print(pct_6mo.round(1).to_string())
if pct_3mo is not None:
    print("\n3-month % change:")
    print(pct_3mo.round(1).to_string())
EOF
```

Read the result multidimensionally: is `cfg["yf_ticker"]`'s move in line
with its sector peers/index? In line with its cap-tier index? Both,
neither, or one but not the other each tell a different story — e.g. "down
with the sector but *not* down with small-caps broadly" points more at a
sector-specific issue than a market-structure one, and vice versa.
Summarize in one line for the run's output and for the thesis checkpoint
notes (Step 7) — e.g. *"Peers down 15-22% over 6mo, this stock down 45% —
meaningfully underperforming its own sector, not just cyclical rotation"* or
*"Sector-wide decline (peers -18% to -30%), this stock's -25% is in line —
read the Wyckoff signal as sector-driven, not company-specific."*

This is read-only context — it does not get its own DB table or its own
ALERT/WATCH rule in Step 6. It exists to calibrate how much weight the
existing flags deserve, not to add new ones.

---

## Step 3c — Sector forecast for next quarter (skip if `cfg` has no `sector` block)

Quarterly cadence, same skip condition as Step 3b (no `sector` block, no
signal — there's no sector to forecast). Unlike Step 3b (which reads *where
the sector has been* over the last 3–6 months), this reads *what analysts/
brokerages expect the sector to do next quarter* — forward-looking
commentary, not trailing price action. It's the sector-level counterpart to
Step 5b-ii's stock-level analyst consensus: same idea, one level up.

Check `prior_sector_forecast.quarter`. If it matches the current quarter,
**skip** — no need to re-search for commentary already logged this quarter.

Web-search for sector/brokerage outlook commentary using `cfg["sector"]["name"]`,
e.g. `"<sector name> sector outlook Q<next-quarter> brokerage"` /
`"<sector name> stocks next quarter outlook analyst"`. Look specifically for
forward-looking statements (brokerage notes, sector outlook pieces) — not
routine news headlines already covered by Step 3, and not backward-looking
recaps of the quarter just ended.

From what's found, classify a single `outlook` for the sector next quarter:
- `bullish` — multiple sources expect the sector to outperform / see tailwinds
- `bearish` — multiple sources expect the sector to underperform / see headwinds
- `neutral` — sources expect flat/range-bound, or no strong view either way
- `mixed` — sources genuinely disagree (some bullish, some bearish) — do not
  force this into bullish or neutral just to pick one; `mixed` is itself the
  honest read when brokerages are split

Write a one-line `summary` capturing the actual reasoning (e.g. "Brokerages
flag margin tailwinds from rupee depreciation and a pickup in US/EU
discretionary IT spend for Q3FY27; 3 of 4 sources bullish, one flags demand
uncertainty") — not just the label. `sources` is a comma-separated list of
publication names (not URLs — same spirit as `analyst_forecasts.source`).

**Do not fabricate an outlook if the search turns up nothing usable** — if
there's no real forward-looking sector commentary to find, leave the quarter
unlogged and say so plainly in output, same discipline as every other "don't
invent it" rule in this skill. A single quarter with no sector_forecasts row
is a valid, honest result.

```python
import sqlite3, os
db = sqlite3.connect(os.path.expanduser("{{cfg.db_path}}"))
db.execute("""
    INSERT OR REPLACE INTO sector_forecasts (quarter, outlook, summary, sources)
    VALUES (?, ?, ?, ?)
""", (quarter, outlook, summary, sources))
db.commit(); db.close()
```

**Reading it alongside the stock's own thesis.** This is sector-level, not
stock-specific — same discipline as Step 3b: it calibrates confidence, it
doesn't override the stock's own Wyckoff/fib/delivery/shareholding reads. A
`bearish` sector outlook doesn't make this stock's `accumulation_watch`
thesis wrong on its own, but it's a headwind worth naming in the thesis
checkpoint's `watch_items` (Step 7); a `bullish` sector outlook lining up
with an already-bullish stock-level read is corroboration worth naming, not
a new standalone trigger.

---

## Step 4 — Wyckoff phase update

**Call `mcp__wyckoff-analyzer__update_run(slug)` for this step** (same call
as Step 2 — it covers price, Wyckoff, fib, and DMA in one shot; do not call
it multiple times per run). It runs the analyzer below and writes
`wyckoff_state` only when phase/score/support/resistance changed, per the
same rule stated below. Only fall back to the manual script if the MCP tool
errors or is unavailable.

`~/workspace/wyckoff_anlayzer/wyckoff_accumulation_analyzer.py` is
ticker-agnostic — `analyze(ticker, period=...)` takes any NSE/BSE symbol,
which is what makes this step config-driven too:

```bash
cd ~/workspace/wyckoff_anlayzer && uv run python - <<'EOF'
import sys, json, sqlite3, os
sys.path.insert(0, ".")
from wyckoff_accumulation_analyzer import analyze
from datetime import date

result = analyze("{{cfg.yf_ticker}}", period="{{cfg.wyckoff_period}}")

phase      = result["phase"]
phase_desc = result["phase_desc"]
confidence = result["confidence"]
score      = result["score"]
events     = result["events"]

PHASE_LABELS = {
    "!":  "Structure-Violated",
    "C?": "Accumulation-C-Unconfirmed",
    "?":  "No-Pattern",
}
phase_label = PHASE_LABELS.get(phase, f"Accumulation-{phase}")

sc = events.get("sc")
ar = events.get("ar")
support    = round(sc["price_low"],  2) if sc else None
resistance = round(ar["price_high"], 2) if ar else None

note_parts = []
if events.get("spring"):
    s = events["spring"]
    note_parts.append(f"Spring {s['date'].date()} price={s['price_low']:.2f}")
if events.get("sos"):
    s = events["sos"]
    note_parts.append(f"SOS {s['date'].date()} close={s['price_close']:.2f}")
if events.get("lps"):
    note_parts.append(f"{len(events['lps'])} LPS detected")
if events.get("buec"):
    note_parts.append(f"BUEC {events['buec']['date'].date()}")
notes = "; ".join(note_parts) if note_parts else "No Spring/SOS/BUEC yet"

today = date.today().isoformat()
out = {
    "date": today, "phase": phase_label, "phase_desc": phase_desc,
    "score": score, "confidence": confidence,
    "support": support, "resistance": resistance, "notes": notes,
    "report": result["report"],
}
print(json.dumps(out, indent=2))
EOF
```

Write to DB **only if** phase, score (±10), or support/resistance (±2%)
changed from `prior_wyckoff`:

```python
db = sqlite3.connect(os.path.expanduser("{{cfg.db_path}}"))
db.execute("""
    INSERT OR REPLACE INTO wyckoff_state
    (date, phase, score, confidence, support_level, resistance_level, notes)
    VALUES (?, ?, ?, ?, ?, ?, ?)
""", (today, phase_label, score, confidence, support, resistance, notes))
db.commit(); db.close()
```

**`cfg["thesis_mode"]` matters here.** The analyzer is built to detect
*accumulation* structures (SC → AR → ST → Spring → SOS → LPS). For a config
with `"thesis_mode": "accumulation_watch"` (e.g. `happiest_minds`), its
output vocabulary applies directly. For `"thesis_mode": "distribution_watch"`
(e.g. `thangamayl`), the live risk is the opposite of what the analyzer
names — don't force its accumulation labels onto a distribution read.
Instead supplement this step with the config's own watch rule: has price
closed below `cfg["watch_level"]` (see `cfg["watch_level_label"]` for what
that level *is*) on volume above the recent average? That, not the
analyzer's phase label, is what `distribution_watch` configs are actually
tracking. `cfg["invalidation_trigger"]` states this precisely per config —
read it rather than re-deriving the rule each run.

---

## Step 4a — Fibonacci retracement levels

**Covered by the same `mcp__wyckoff-analyzer__update_run(slug)` call as Step
2/4/4c** — writes `fib_levels` only when the swing high/low or nearest-level
distance actually moved, per the rule below. Only fall back to the manual
script if the MCP tool errors or is unavailable.

Ticker-agnostic, runs for every config regardless of `thesis_mode`. Identifies
the most recent significant swing (high→low for an uptrend read, or
low→high for a downtrend read) over `cfg["wyckoff_period"]` and derives the
standard retracement levels, then checks whether the current close sits near
one of them — a fib level lining up with a Wyckoff support/resistance read
(Step 4) is a stronger confluence signal than either alone; a level with no
such overlap is weaker context, not a standalone trigger.

```bash
cd ~/workspace/wyckoff_anlayzer && uv run python - <<'EOF'
import sys, json, sqlite3, os
import yfinance as yf
from datetime import date

hist = yf.download("{{cfg.yf_ticker}}", period="{{cfg.wyckoff_period}}", interval="1d", progress=False, auto_adjust=True)
if hist.empty:
    print(json.dumps({"error": "no data"})); sys.exit()

close = float(hist["Close"].iloc[-1])
swing_high_idx = hist["High"].idxmax()
swing_low_idx  = hist["Low"].idxmin()
swing_high = float(hist.loc[swing_high_idx, "High"])
swing_low  = float(hist.loc[swing_low_idx,  "Low"])

# Direction = which swing point came first; retracement runs from the
# earlier extreme toward the later one.
direction = "uptrend" if swing_low_idx < swing_high_idx else "downtrend"
span = swing_high - swing_low

def level(pct):
    return round(swing_high - span * pct, 2) if direction == "uptrend" else round(swing_low + span * pct, 2)

levels = {r: level(r) for r in (0.236, 0.382, 0.5, 0.618, 0.786)}

nearest_label, nearest_price = min(levels.items(), key=lambda kv: abs(kv[1] - close))
distance_pct = round(abs(nearest_price - close) / close * 100, 2)

out = {
    "date": date.today().isoformat(),
    "swing_high": round(swing_high, 2), "swing_low": round(swing_low, 2),
    "swing_high_date": str(swing_high_idx.date()), "swing_low_date": str(swing_low_idx.date()),
    "direction": direction,
    "level_236": levels[0.236], "level_382": levels[0.382], "level_500": levels[0.5],
    "level_618": levels[0.618], "level_786": levels[0.786],
    "nearest_level": f"{nearest_label:.3f}", "nearest_level_price": nearest_price,
    "distance_pct": distance_pct,
    "close": round(close, 2),
}
print(json.dumps(out, indent=2))
EOF
```

Write to DB **only if** the swing high/low changed (a new extreme printed)
or `nearest_level`/`distance_pct` moved meaningfully (±0.5pp) from
`prior_fib` — same "only log what changed" discipline as Step 4:

```python
db = sqlite3.connect(os.path.expanduser("{{cfg.db_path}}"))
db.execute("""
    INSERT OR REPLACE INTO fib_levels
    (date, swing_high, swing_low, swing_high_date, swing_low_date, direction,
     level_236, level_382, level_500, level_618, level_786,
     nearest_level, nearest_level_price, distance_pct, notes)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
""", (today, swing_high, swing_low, swing_high_date, swing_low_date, direction,
      level_236, level_382, level_500, level_618, level_786,
      nearest_level, nearest_level_price, distance_pct, notes))
db.commit(); db.close()
```

A "confluence" note is worth one line in `notes` when a fib level sits
within ~1% of the Wyckoff `support_level`/`resistance_level` from Step 4 —
e.g. `"61.8% retracement (₹412) confluent with Wyckoff support (₹408)"`. Do
not invent confluence when the levels are actually far apart.

---

## Step 4b — Accumulation-trigger sequence check (`distribution_watch` configs only)

Skip this step entirely if `cfg` has no `accumulation_trigger` key, or if
`cfg["thesis_mode"]` is `"accumulation_watch"` — that mode already has its
own trigger vocabulary via the analyzer's Spring/SOS/LPS events (Step 4).

A `distribution_watch` config's `accumulation_trigger` field names the
*opposite*-direction signal — the sequence that would mean the distribution
thesis is wrong and an entry point is instead forming. It exists precisely
because the Wyckoff analyzer (Step 4) can't detect this: it's built to spot
accumulation structures forming from a downtrend, not to notice one forming
*while a distribution_watch thesis is still active*. This step is manual
inspection, not a script — read the last several weeks of price/volume
(same fetch as Step 2, extended) against `cfg["accumulation_trigger"]
["sequence_required"]`, in order, starting from whichever step
`cfg["accumulation_trigger"]["status"]` says is next:

- `"not_yet_applicable"` → check only for step 1 (has a floor formed — price
  stopped making new lows for multiple weeks, with volume contracting vs.
  the panic/climax volume that's presumably still the most recent extreme).
- `"range_forming"` → check for the next unconfirmed step in
  `sequence_required` (ST, then Spring, then SOS, then LPS) — advance one
  step per run at most; do not skip ahead because a rally looks promising.
  A step only counts once it has actually printed, not while it's still in
  progress (e.g. a rally attempt is not an SOS until it closes above the
  range resistance on the volume the definition calls for).

**If a step advances:** update the config file itself —
`cfg["accumulation_trigger"]["status"]` and append a dated note recording
what was observed (price, volume, which step). This is a config edit, not a
DB write; the config is small enough to be the source of truth for this
field, and rewriting it keeps the next run's starting point correct without
needing a new table.

**If the sequence reaches a confirmed LPS:** that's the point the
`distribution_watch` thesis has been proven wrong by price action. Flag
this prominently (see Step 6) and raise to the user whether
`cfg["thesis_mode"]` should flip to `"accumulation_watch"` and
`cfg["watch_level"]`/`cfg["invalidation_trigger"]` should be rewritten —
don't flip the mode unilaterally, this is a real change in what the tracker
is watching for and the human should confirm it.

**Do not fabricate or backfill a step.** If the last several weeks show
nothing resembling the next step in the sequence, leave `status` unchanged
and say so plainly in the run's output — matching the config's own
`update_instructions`: only log what has actually printed on the chart.

---

## Step 4c — DMA (moving average) signal

**Covered by the same `mcp__wyckoff-analyzer__update_run(slug)` call as Step
2/4/4a** — computes DMAs and writes `dma_levels` only when a real crossover
happened or the 20/50 gap moved ≥1pp, per the rule below, and skips writing
(returning an error in its `dma` sub-result) if there's not even enough
history for a 20DMA. Only fall back to the manual script if the MCP tool
errors or is unavailable.

Ticker-agnostic, runs for every config regardless of `thesis_mode` — same
category of context as Step 4a's fib levels: a second, independent
technical read to corroborate or contradict the Wyckoff phase, not a
standalone buy/sell trigger on its own.

**Check there's enough history before computing anything.** A 200DMA is
meaningless (and `pandas.rolling(200)` silently returns `NaN`, which is
easy to mistake for "no signal" instead of "not enough data") on a config
whose `wyckoff_period` is shorter than 200 trading days, or on a recently
listed stock. Count the fetched daily bars first; if there are fewer than
20 (i.e. can't even fill the 20DMA, the shorter side of the crossover pair
this step cares about), skip the whole step and say so plainly in output —
do not report `price_vs_20`/`gap_20_50_pct` as `None` silently. Windows
that can't be filled from history (e.g. 200DMA on 100 days of data) get
`None` and get reported as `None`, not fabricated or zero-filled.

Uses `pandas` the same way Step 4a does — `yf.download(...)` into a
DataFrame, then `.rolling(window).mean()` on the close series (widened
with today's live close if it's fresher than the cached history's last
row, same trick Step 4a uses for the swing high/low):

```bash
cd ~/workspace/wyckoff_anlayzer && uv run python - <<'EOF'
import sys, json, sqlite3, os
import pandas as pd
import yfinance as yf
from datetime import date

WINDOWS = (10, 20, 50, 100, 200)

hist = yf.download("{{cfg.yf_ticker}}", period="{{cfg.wyckoff_period}}", interval="1d", progress=False, auto_adjust=True)
if hist.empty:
    print(json.dumps({"error": "no data"})); sys.exit()

closes = hist["Close"]
n = len(closes)
close = float(closes.iloc[-1])

if n < 20:
    print(json.dumps({"error": f"insufficient history for even 20DMA ({n} bars available)"})); sys.exit()

dmas = {}
for w in WINDOWS:
    if n < w:
        dmas[w] = None
        continue
    val = closes.rolling(w).mean().iloc[-1]
    dmas[w] = round(float(val), 2) if pd.notna(val) else None

def vs(level):
    return None if level is None else ("above" if close > level else "below")

gap_20_50_pct = (
    round((dmas[20] - dmas[50]) / dmas[50] * 100, 2)
    if dmas[20] is not None and dmas[50] is not None else None
)

out = {
    "date": date.today().isoformat(),
    "close": round(close, 2),
    "dma_10": dmas[10], "dma_20": dmas[20], "dma_50": dmas[50],
    "dma_100": dmas[100], "dma_200": dmas[200],
    "price_vs_20": vs(dmas[20]), "price_vs_50": vs(dmas[50]), "price_vs_200": vs(dmas[200]),
    "gap_20_50_pct": gap_20_50_pct,
}
print(json.dumps(out, indent=2))
EOF
```

**Crossover detection.** Compare this run's `gap_20_50_pct` against
`prior_dma.gap_20_50_pct`: a sign flip (prior ≤0, now >0) is a
`golden_cross` (20DMA crossed above 50DMA — bullish); the reverse (prior
≥0, now <0) is a `death_cross` (bearish). No sign flip → `crossover_state`
is `"none"` — the gap widening or narrowing without actually crossing is
still worth noting in `notes` (e.g. "20/50 gap narrowed from 8.2% to 3.5%
over the past week — approaching a death cross") but is not itself a
crossover event.

Write to DB **only if** `crossover_state` is not `"none"` (a real cross
just happened — always worth logging) **or** `gap_20_50_pct` moved ≥1pp
from `prior_dma` — same "only log what changed" discipline as Step 4a:

```python
db = sqlite3.connect(os.path.expanduser("{{cfg.db_path}}"))
db.execute("""
    INSERT OR REPLACE INTO dma_levels
    (date, close, dma_10, dma_20, dma_50, dma_100, dma_200,
     price_vs_20, price_vs_50, price_vs_200, gap_20_50_pct, crossover_state, notes)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
""", (today, close, dma_10, dma_20, dma_50, dma_100, dma_200,
      price_vs_20, price_vs_50, price_vs_200, gap_20_50_pct, crossover_state, notes))
db.commit(); db.close()
```

**Reading it alongside Wyckoff/fib.** The short/medium-term averages
(10/20/50DMA) and the long-term ones (100/200DMA) can disagree — e.g.
price below the 10/20/50DMA band but still above the 200DMA just means a
long, large prior markup hasn't fully unwound yet in the slow averages;
it's not a contradiction, it's the slow averages lagging. Read
`price_vs_200` as the long-term trend context and the 20/50 relationship
as the thing that actually moves on a session-to-session basis. A
`golden_cross`/`death_cross` that lines up with a Wyckoff Spring/SOS or a
fib confluence (Step 4a) is a stronger combined signal than any one of the
three alone; one with no such overlap is still worth flagging (see Step 6)
but weaker context on its own.

---

## Step 5 — Shareholding (quarterly cadence only)

Check `prior_shareholding.quarter`. If it's the current quarter, **skip** —
no new filing available yet.

Only fetch if a new quarter has started since the last logged entry:
- Source: BSE shareholding pattern filing, scrip code `cfg["bse_scrip_code"]`
  (if `null`, look it up via BSE company search first — do not guess one)
- Extract: promoter %, FII %, DII %, retail %

```python
db.execute("""
    INSERT OR IGNORE INTO shareholding (quarter, promoter_pct, fii_pct, dii_pct, retail_pct)
    VALUES (?, ?, ?, ?, ?)
""", (quarter, promoter_pct, fii_pct, dii_pct, retail_pct))
```

### Step 5's trend read — institutional confirmation/disconfirmation

Whenever this step actually fetches a new quarter (i.e. did not skip above),
read the new filing as a signal, not just a logged fact — this is the
shareholding-trend counterpart to Step 4a (fib) and Step 2b (delivery %):
disclosed ownership change is a third, independent data domain from price
action, so it either corroborates or contradicts what Wyckoff/fib/delivery
are currently reading.

```python
fii_delta      = round(fii_pct      - prior_shareholding.get("fii_pct", fii_pct),      2) if prior_shareholding else None
dii_delta      = round(dii_pct      - prior_shareholding.get("dii_pct", dii_pct),      2) if prior_shareholding else None
inst_delta     = round((fii_delta or 0) + (dii_delta or 0), 2) if prior_shareholding else None
promoter_delta = round(promoter_pct - prior_shareholding.get("promoter_pct", promoter_pct), 2) if prior_shareholding else None
```

`inst_delta` (combined FII+DII, quarter-over-quarter, in percentage points)
is the number to read against the *current* Wyckoff phase / fib position —
not the phase at the time the prior quarter's filing was recorded, since
the technical read may have moved on since then:

- **`inst_delta` > +1pp** while the current Wyckoff phase is an accumulation
  read (Phase B/C, Spring, or price sitting near a Wyckoff/fib support
  confluence) → institutions are actually adding into the same zone the
  technical structure calls accumulation — real corroboration, a stronger
  claim than delivery % or fib can make alone since it's disclosed
  ownership, not inferred flow.
- **`inst_delta` < −1pp** while the current Wyckoff phase still reads
  accumulation, or price is holding above a Wyckoff/fib support → a
  contrary signal worth surfacing even when `promoter_pct` itself didn't
  move (the existing "promoter % changed" ALERT rule doesn't catch this —
  FII/DII can walk out quietly while promoter holding sits still).
- Do not claim corroboration or contradiction unless the *current* Wyckoff
  phase / price-vs-level state is checked at the time of this read, not
  assumed from the last time Step 4/4a ran.

**Macro-benchmark check — do this before applying the flag rules below.**
A stock-level FII/DII delta can look like a stock-specific signal when it's
actually market-wide rotation. Before reading `inst_delta` as
corroboration/contradiction, check the vault's `Monthly/FII_DII_YYYY-MM.md`
notes (already maintained by `market-intel-fii-dii-check`) for the same
window and compare direction and rough scale:
- If the market-wide monthly FII net is sharply negative (broad exodus) and
  DII net is sharply positive (broad absorption) over the same quarter,
  and the stock's own FII-down/DII-up move is *directionally consistent* with
  that market-wide flow, read it as macro-driven — do not flag `WATCH` for
  "quiet institutional exit" even if `inst_delta` crosses the −1pp threshold,
  since every stock in the market is seeing similar rotation, not this one
  specifically.
- Only treat `inst_delta` as a stock-specific signal (positive or negative)
  when it moves *against* or *further than* what the market-wide flow for
  that window would explain — e.g. FII continuing to sell a stock while
  market-wide FII flow has already turned net positive again is a real
  divergence worth flagging; matching a broad exodus everyone is seeing is not.
- Example precedent (2026-08-04, HDFC Bank): FII fell 44.1%→41.8% (Q4FY26→Q1FY27,
  i.e. Mar 2026→Jun 2026 quarter-end), DII rose 40.3%→41.9%, combined
  `inst_delta` −0.7pp. Vault shows market-wide FII net −₹1,11,377Cr / DII net
  +₹1,28,065Cr in March 2026 alone (post 2026-02-28 Iran-Israel war shock,
  a market-wide risk-off FII exodus, not HDFC-specific) — same direction, and
  HDFC's own move is far smaller in relative terms than the market-wide
  rotation. Correctly read as macro-driven, not a stock-specific institutional
  retreat, regardless of what `inst_delta`'s raw number would suggest in isolation.

This context is inherently a quarter stale relative to the day's Wyckoff/fib/
delivery reads — label it as such in output (Step 8 / interactive output),
don't present it as same-day-fresh.

---

## Step 5b — Quarterly results and analyst forecasts

### 5b-i: Quarterly results

Check `prior_quarterly.quarter`. If it matches the latest published quarter, **skip**.

Source order: (1) BSE result filing, scrip code `cfg["bse_scrip_code"]`;
(2) Moneycontrol / Tickertape quarterly financials.

```python
import sqlite3, os
db = sqlite3.connect(os.path.expanduser("{{cfg.db_path}}"))
db.execute("""
    INSERT OR REPLACE INTO quarterly_results
    (quarter, report_date, revenue_cr, revenue_est_cr, pat_cr, pat_est_cr,
     ebitda_margin_pct, eps, yoy_revenue_pct, yoy_pat_pct, beat_miss, notes)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
""", (quarter, report_date, revenue_cr, revenue_est_cr, pat_cr, pat_est_cr,
      ebitda_margin_pct, eps, yoy_revenue_pct, yoy_pat_pct, beat_miss, notes))
db.commit(); db.close()
```

**beat_miss:** `"beat"` if PAT > estimate by >3%, `"miss"` if < by >3%, else `"inline"`.

### 5b-ii: Analyst consensus (weekly cadence)

Only update if `prior_forecast.date` is more than 7 days old. Source:
Tickertape / Moneycontrol broker recommendations for `cfg["yf_ticker"]`.

```python
db = sqlite3.connect(os.path.expanduser("{{cfg.db_path}}"))
db.execute("""
    INSERT OR REPLACE INTO analyst_forecasts
    (date, target_price, analyst_count, buy_count, hold_count, sell_count,
     next_earnings_date, fy_rev_est_cr, fy_pat_est_cr, source)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
""", (today, target_price, analyst_count, buy_count, hold_count, sell_count,
      next_earnings_date, fy_rev_est_cr, fy_pat_est_cr, source))
db.commit(); db.close()
```

---

## Step 6 — Diff and flag

Shared rules, apply to every config:

| Condition | Flag level |
|-----------|------------|
| Price moved >3% day-over-day | `ALERT` |
| New `block_deal` news event | `ALERT` — always |
| Wyckoff phase changed | `ALERT` — always |
| Phase became `Structure-Violated` | `ALERT` — always |
| Promoter % changed vs prior quarter | `ALERT` — always |
| New quarterly result logged (`beat_miss` = `miss` or `beat`) | `ALERT` — always |
| Analyst target price changed >5% vs prior | `ALERT` |
| Volume spike >2× 20-day avg | `WATCH` |
| New bearish news event | `WATCH` |
| `beat_miss` = `inline` | `WATCH` |
| Close within 1% of a fib level (`distance_pct` ≤ 1) that is also within ~1% of Wyckoff support/resistance | `ALERT` — confluence |
| Close within 1% of a fib level with no Wyckoff confluence | `WATCH` |
| Swing high/low reset (new extreme printed, levels recalculated) | `WATCH` |
| Delivery % ≥10pp above 10-day avg (`delta_vs_avg_pp`) while price is within 1% of a Wyckoff support/resistance or fib level | `ALERT` — corroborated demand at that level |
| Price closes through a Wyckoff resistance level or a fib level on delivery % below the 10-day avg | `WATCH` — unconfirmed / speculative move, not corroborated by real accumulation |
| New quarter's `inst_delta` (FII+DII combined) > +1pp while current Wyckoff phase reads accumulation, **and** the move isn't just tracking a market-wide FII/DII rotation (see Step 5's macro-benchmark check) | `ALERT` — institutional corroboration |
| New quarter's `inst_delta` < −1pp while current Wyckoff phase reads accumulation or price holds above Wyckoff/fib support, **and** the move isn't just tracking a market-wide FII/DII rotation | `WATCH` — quiet institutional exit despite bullish technical read; promoter % alone may not have moved |
| `crossover_state` = `golden_cross` or `death_cross` | `ALERT` — always; stronger still (note as such) if it lines up with a Wyckoff Spring/SOS or fib confluence the same day |
| `gap_20_50_pct` moved ≥1pp toward zero (crossover hasn't happened yet, but the gap is closing) | `WATCH` — approaching cross, not yet confirmed |
| New `sector_forecasts` row logged with `outlook` = `bearish`, current Wyckoff phase reads accumulation or thesis is `accumulation_watch` | `WATCH` — sector headwind against a bullish stock-level read |
| New `sector_forecasts` row logged with `outlook` = `bullish`, current Wyckoff phase reads accumulation or thesis is `accumulation_watch` | not a standalone flag — note as corroboration in `watch_items` (Step 7), same "context, not trigger" treatment as Step 3b |

Mode-specific rules, keyed by `cfg["thesis_mode"]`:

| Mode | Condition | Flag level |
|------|-----------|------------|
| `accumulation_watch` | `Spring` or `SOS` or `UTAD` or `SOW` in wyckoff notes | `ALERT` — always |
| `accumulation_watch` | Phase is `Accumulation-C-Unconfirmed` (possible spring, unrecovered) | `WATCH` — not `ALERT`; resolves within a few bars, alerting on entry *and* exit doubles the noise |
| `distribution_watch` | Weekly close below `cfg["watch_level"]` on volume > 2× 20-week avg | `ALERT` — Sign-of-Weakness confirmation |
| `distribution_watch` | Daily close below `cfg["watch_level"]` (not yet a weekly close) | `WATCH` — first warning, not confirmation |
| `distribution_watch` with `accumulation_trigger` | Step 4b advanced the sequence (any step) | `ALERT` — the distribution thesis is being contradicted by price action, always worth surfacing regardless of which step |
| `distribution_watch` with `accumulation_trigger` | Step 4b's sequence reached a confirmed LPS | `ALERT` — always, and explicitly ask the user whether to flip `thesis_mode` (see Step 4b) |

If no flags triggered → output: **"No material change today."**

---

## Step 7 — Update thesis checkpoint

- **bull_case_intact = 1** if: no invalidation trigger hit, no adverse promoter news, and (mode-specific) Wyckoff not in Distribution/Markdown (`accumulation_watch`) or price holds above `cfg["watch_level"]` (`distribution_watch`)
- **bull_case_intact = 0** if: `cfg["invalidation_trigger"]`'s condition is met, or major adverse promoter/business news

For a `distribution_watch` config with an `accumulation_trigger` field, "bull
case" here means the *distribution* thesis, not a buy signal — a confirmed
LPS (Step 4b) doesn't flip `bull_case_intact` to 1 on its own; it means the
tracker's current framing has been invalidated by price action, which is a
different, more significant event than routine day-to-day noise. Note it in
`watch_items` explicitly (e.g. `"accumulation_trigger reached LPS — thesis_mode
reassessment needed, see 2026-XX-XX checkpoint"`) rather than silently
mapping it onto the existing bull_case_intact field.

Set `watch_items` to the next concrete thing to monitor. Set
`invalidation_trigger` to `cfg["invalidation_trigger"]` (or a re-derived
version of it if the config's static level has since been superseded by a
new Wyckoff support/resistance read — update the config file too if so,
don't just let the DB and config drift apart).

```python
db = sqlite3.connect(os.path.expanduser("{{cfg.db_path}}"))
db.execute("""
    INSERT OR REPLACE INTO thesis_checkpoints
    (date, bull_case_intact, watch_items, invalidation_trigger)
    VALUES (?, ?, ?, ?)
""", (today, bull_case_intact, watch_items, invalidation_trigger))
db.commit(); db.close()
```

---

## Step 8 — Write vault daily note

```python
mcp__local-mac__vault__append(
    path="{{cfg.vault_note}}",
    content=f"""
## {today}

**Price:** ₹{close} ({pct_change:+.1f}%)  Volume: {volume:,}
**Wyckoff:** {phase}  |  Support: ₹{support}  Resistance: ₹{resistance}
**Fib:** {nearest_level} ₹{nearest_level_price} ({distance_pct:+.1f}% away, {direction})
**Delivery:** {delivery_pct}% ({delta_vs_avg_pp:+.1f}pp vs 10d avg)
**Shareholding trend (as of {shareholding_quarter}, may be stale vs today):** FII+DII {inst_delta:+.1f}pp QoQ
**Sector forecast (as of {sector_forecast_quarter}, may be stale vs today):** {sector_outlook} — {sector_forecast_summary}
**Thesis:** {"✅ Intact" if bull_case_intact else "⚠️ Under review"}
**Watch:** {watch_items}

{flags_section}
"""
)
```

Use `vault__write` instead of `vault__append` the first time a config's
note doesn't exist yet — append fails against a nonexistent note.

If no flags, omit the `{flags_section}` line entirely. Omit the **Sector
forecast** line entirely for configs with no `sector` block, or when Step 3c
skipped this run with no new quarter logged yet (do not repeat a stale
quarter's line just to keep the note's shape consistent — same discipline as
the shareholding-trend line).

---

## Output (interactive runs)

```
{{cfg.display_name}} ({{cfg.yf_ticker}}) — <today>

Price: ₹XXX (+X.X%)  Vol: X,XX,XXX

[ALERT] <only flagged items — omit section if none>

Wyckoff: <phase>  |  Support ₹XXX  Resistance ₹XXX
Fib: <nearest_level> ₹XXX (X.X% away, <direction>)
DMA: 20D ₹XXX (<above|below>)  50D ₹XXX (<above|below>)  200D ₹XXX (<above|below>)  |  20/50 gap X.X%  <crossover_state>
Delivery: XX.X% (X.Xpp vs 10d avg)
Shareholding (<quarter>, may be stale vs today): FII+DII X.Xpp QoQ
Sector forecast (<quarter>, may be stale vs today): <bullish|bearish|neutral|mixed> — <summary>

Earnings (<quarter>): Rev ₹XXXCr (+X% YoY)  PAT ₹XXCr (+X% YoY)  [BEAT/MISS/INLINE vs est]
Next result: ~<next_earnings_date>
Analyst consensus: ₹XXX target  X buy / X hold / X sell  (N analysts)

Next watch: <watch_items>
Thesis: Intact | Under review  |  Invalidation: <cfg.invalidation_trigger>
```

Nothing else. Don't restate the full thesis — that's what the DB and vault
history are for.

---

## Adding a new tracked stock

Write `~/workspace/stock-tracker-mcp/stock_tracker/configs/<slug>.json` with the fields
shown in `thangamayl.json` (`distribution_watch` example),
`happiest_minds.json` (`accumulation_watch` example), or `tcs.json`
(`accumulation_watch`, early-stage, large-cap with a `sector` block). No new
skill file, no code changes — `/stock-tracker <slug>` picks it up.

Do the actual price/Wyckoff read first (same as any of these three were
built) — don't scaffold a config with a guessed `thesis_mode`,
`watch_level`, or `baseline_notes` before looking at the chart. An empty
mechanical scaffold (ticker, `db_path`, `vault_note`, `news_queries`) is
fine to create ahead of the analysis; the thesis-specific fields are not.

`sector` is optional and stock-cap-agnostic — it's just as valid for a
small-cap as a large-cap, since sector/cyclicality context matters
regardless of size. Add it whenever there's a genuinely comparable set of
peers and a steady benchmark worth diffing against (see Step 3b); skip it
for a stock with no clean sector peers to compare to.

## Hard rules

- Do not re-fetch shareholding if the quarter hasn't changed.
- Do not flag ordinary price noise (<3% moves) as alerts.
- Do not issue buy/sell recommendations.
- Do not insert duplicate news rows — check URL and headline against prior state before inserting.
- Do not write a new `wyckoff_state` row if nothing meaningful changed.
- Do not write a new `fib_levels` row unless the swing high/low or nearest-level distance actually moved; do not claim confluence with Wyckoff support/resistance unless the levels are genuinely within ~1% of each other.
- Do not compute a DMA window the fetched history can't support (e.g. a 200DMA on 100 days of data) — report it as `None`, don't fabricate or zero-fill it. If there isn't even enough history for the 20DMA, skip Step 4c entirely and say so.
- Do not write a new `dma_levels` row unless a real 20/50 crossover just happened or the 20/50 gap moved ≥1pp from the prior row.
- Do not fetch delivery % for a BSE-only config (no `.NS` listing) — the NSE archive has no equivalent for BSE-only symbols.
- Do not fabricate `avg_10d_delivery_pct` before at least one real prior row exists — leave it `None` and say so.
- Do not call a rally "corroborated by delivery" unless `delta_vs_avg_pp` is genuinely elevated (≥10pp) AND price is actually within 1% of a Wyckoff or fib level that day — a delivery-% uptick with no nearby structural level is just background context, not a signal.
- Do not claim shareholding-trend corroboration/contradiction against a stale Wyckoff phase — check the *current* phase at the time of the read, not whatever phase was last logged when the prior quarter's filing came in.
- Do not present the shareholding-trend line as same-day-fresh — it updates quarterly and should always carry its filing quarter in output.
- Do not call a multi-session delivery-% trend stock-specific without checking at least one sector peer over the same window (see Step 2b's sector-peer relative-strength check) — a sector-wide delivery-% re-rating can look identical to a stock-specific one until checked.
- Do not force `accumulation_watch` vocabulary onto a `distribution_watch`
  config's read, or vice versa — check `cfg["thesis_mode"]` first.
- Do not let a config's `watch_level`/`invalidation_trigger` silently drift
  from what the DB's `thesis_checkpoints` history actually says — if the
  thesis level moves, update the config file, don't just log a different
  number to the DB.
- Do not advance `accumulation_trigger.status` past what has actually
  printed on the chart, and do not skip steps in `sequence_required` — a
  promising-looking rally is not an SOS until it closes above range
  resistance on the volume the step calls for.
- Do not flip `cfg["thesis_mode"]` unilaterally, even if `accumulation_trigger`
  reaches a confirmed LPS — flag it and let the user decide (Step 4b).
- Do not fetch or log a sector forecast for a config with no `sector` block —
  same skip condition as Step 3b.
- Do not re-search for sector forecast commentary if the current quarter
  already has a `sector_forecasts` row — quarterly cadence, same discipline
  as Step 5's shareholding fetch.
- Do not fabricate a sector `outlook` when the search turns up no real
  forward-looking commentary — leave the quarter unlogged and say so, rather
  than forcing a `neutral` guess.
- Do not treat a `bullish`/`bearish` sector forecast as a stock-specific
  trigger on its own — it calibrates the existing stock-level flags (Step 6),
  it does not replace them.
