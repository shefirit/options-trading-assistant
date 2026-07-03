# Options Trading Assistant

Your personal helper for options trading. It connects to real market data, helps you read
the market, finds trades that fit your rules, and checks every trade against your SOP before
you enter it in thinkorswim.

It does **not** place trades and does **not** give buy/sell advice. You stay in control - it
just helps you pick well and do it correctly.

---

## What it does

1. **Read the market** - price, VIX (the "fear gauge"), implied volatility, and a big
   **"best fit for today"** recommendation that picks the strategy matching current conditions.
2. **Pick a strategy** - all 8 from your Notion system are built in. Choose your days to
   expiration on a 21-35 day slider; contracts default to 1.
3. **Find setups** - the scanner searches option chains for trades that match your rules
   (short-leg delta under 0.10, your chosen DTE, inside your buying-power limit).
4. **Check any trade** - a green/red checklist confirms a trade passes every SOP rule before
   you place it. Works for trades the scanner found OR trades you built yourself.
5. **Keep a record** - one click logs a trade to your Google Sheet (with a local backup).

Your rules live in plain text in the `config/` folder - change a number there and the whole
app follows. No coding needed.

---

## Data modes (it picks the best one automatically)

- **REAL (works right now):** real market prices, option chains, volatility, and trend from
  Yahoo Finance - about 15 minutes delayed, which is fine for 21-45 day trades. Free, no setup,
  no account. This is what you get as soon as you have internet.
- **LIVE:** once your Schwab app is approved and connected, it upgrades to true real-time data
  from your own account automatically.
- **DEMO:** if you are offline, it falls back to bundled sample data so you can still explore.

The greeks (delta, etc.) your rules depend on are computed with the same Black-Scholes math a
broker uses, so the numbers line up closely with thinkorswim.

## Stocks, not just ETFs and indexes

You can trade quality individual stocks (AAPL, MSFT, NVDA, and more - see `config/settings.yaml`)
for cash secured puts and covered calls. When you pick a stock, the app shows a plain-English
**"Is this a good stock to trade?"** scorecard: fundamentals (company size, valuation, profit
margin, growth) and technicals (trend, momentum, trading volume), each with a simple read and a
green/amber/red flag. Credit spreads stay on cash-settled indexes (SPX, NDX...) to avoid
early-assignment risk.

---

## First-time setup

You only do this once.

### 1. Install

Open a terminal in this folder and run:

```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Start the app

**Just double-click `run_app.bat`.** A black window opens, then your browser opens to the app
at http://localhost:8501 . Keep that black window open while you use the app - closing it stops
the app. To start it again later, double-click `run_app.bat` again.

Tip: for one-click access, right-click `run_app.bat` -> **Send to** -> **Desktop (create
shortcut)**. Rename the shortcut to "Trading Assistant".

**If the browser says it can't connect to localhost:8501:**
- Make sure the black `run_app.bat` window is still open. If it closed, the app stopped - open
  it again.
- Wait ~5-10 seconds after the window appears; the app takes a moment to start.
- If it still fails, close any leftover black windows and double-click `run_app.bat` once more.

(The app runs on your own PC. The window IS the app - as long as it's open, the app stays up.)

### 3. Connect live Schwab data (when you're ready)

1. Go to https://developer.schwab.com and create an app (choose the **Trader API - Individual**).
   - Set the callback URL to `https://127.0.0.1:8182`.
   - Wait for the app status to become **"Ready for Use"** (this can take a few days).
2. Copy `.env.example` to `.env` and paste in your **App Key** and **App Secret**.
3. Run this once to log in (a browser window opens):
   ```
   python -m src.data.schwab_client
   ```
   After that it logs in silently. The app now shows **LIVE data**.

Your keys stay on your PC (the `.env` and `token.json` files are never shared).

### 4. Log trades to your Google Sheet (the easy way - paste one link)

No Google Cloud, no key files. You add a tiny script to your own sheet, then paste one
link into the app. About 5 minutes.

1. Open your Google Sheet.
2. Menu: **Extensions → Apps Script**.
3. Delete anything there and paste the whole script from
   `google_apps_script/LogTrade.gs` in this project.
4. Click **Save**, then **Deploy → New deployment**.
5. Choose type **Web app**, set **Who has access: Anyone**, click **Deploy**, and approve
   the permissions (it is your own script).
6. Copy the **Web app URL** it gives you.
7. In the app sidebar, open **Connect Google Sheet**, paste the link, and click **Save**.
   Click **Test it** to send a sample row you can then delete.

After that, the **Log this trade** button writes straight into your sheet. If the internet or
the sheet is ever unreachable, it quietly saves to a local `trade_log.xlsx` backup instead, so
you never lose a record.

*(Advanced alternative: a Google service-account `google_credentials.json` also works and is
tried automatically if present - see `src/logging_tools/sheets_logger.py`.)*

---

## Everyday use

1. Start the app.
2. Pick a strategy and an underlying (SPX is the usual pick for spreads - but you can choose
   or scan any allowed one).
3. Read the **Market conditions** panel.
4. **Find setups** tab: set your contracts and spread width, press **Scan**, and review the
   candidates. Click one to see its full SOP checklist.
5. **Check my own trade** tab: type in a trade you set up in thinkorswim and get the checklist.
6. Enter the trade yourself in thinkorswim PaperMoney, then log it.

---

## Changing your rules

Everything is in the `config/` folder:

- `config/settings.yaml` - your capital, targets, buying-power limit, and allowed underlyings.
- `config/strategies.yaml` - the rules for each of the 8 strategies (deltas, days to
  expiration, profit target, stop loss). This mirrors your Notion SOP.

Edit a number, save, and refresh the app.

---

## What's covered

| Feature | Credit spreads (PCS / CCS / Iron Condor) | Cash Secured Put | Covered Calls (1-3) & PMCC |
|---|---|---|---|
| Scanner (find setups) | ✅ | ✅ | Checklist only for now |
| SOP checklist | ✅ | ✅ | ✅ |

Covered calls and PMCC depend on your real share position, so for now you check them with the
checklist rather than scanning. Everything else scans automatically.

---

## Running the tests

```
.venv\Scripts\activate
pytest -q
```

The tests prove the rule engine and scanner enforce your SOP correctly, with no live
connection needed.

---

## Safety

- No trades are ever placed. No money is moved. No buy/sell advice is given.
- Your Schwab keys and Google credentials never leave your PC.
- Logging writes to your Google Sheet (or a local `trade_log.xlsx` backup), never to your
  teacher's Hebrew tracker, so that file stays safe.
- You are paper trading to learn the process. Follow the rules, not the P&L.
