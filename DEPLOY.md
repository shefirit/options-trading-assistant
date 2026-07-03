# Put the app online (free, always-on) - step by step

This hosts your app at a permanent web address on **Streamlit Community Cloud** (free).
No black window, no launching - just open the link from any device.

Everything here is click-by-click. You do NOT need to type any code.

---

## What you get, and one honest note

- A permanent link like `https://your-app.streamlit.app`, always available.
- Real market data (Yahoo, ~15 minutes delayed) works exactly like it does now.
- Logging to your Google Sheet works (you'll paste your link into "Secrets" once).
- On your phone: open the link, then **Share -> Add to Home Screen** for an app icon.

**Note:** live Schwab data stays on your PC (its login only works locally). The hosted app
uses the Yahoo delayed data. When you go live with real money later, run the local copy
(`run_app.bat`) for that. The hosted one is perfect for research and checking trades.

---

## Step 1 - Make a free GitHub account

1. Go to **https://github.com** and click **Sign up**. Follow the prompts (email, password).
   That's it - GitHub is where your app's files live so the host can read them.

## Step 2 - Install GitHub Desktop (the easy, no-typing way)

1. Go to **https://desktop.github.com** and download **GitHub Desktop**. Install it.
2. Open it and **sign in** with the GitHub account you just made.

## Step 3 - Publish your app to GitHub (private)

1. In GitHub Desktop: **File -> Add local repository**.
2. Click **Choose...** and pick this folder:
   `D:\Claude Code Projects\options-trading-assistant`
3. Click **Add repository**.
4. Click the blue **Publish repository** button (top right).
5. **Important:** leave **"Keep this code private"** CHECKED. Click **Publish repository**.

Your code is now safely on GitHub (private - only you can see it). Your personal files
(.env, your Google Sheet link, logs) were automatically left out.

## Step 4 - Deploy it on Streamlit Cloud

1. Go to **https://share.streamlit.io** and click **Sign in with GitHub** (approve access).
2. Click **Create app** (or "New app").
3. Fill in:
   - **Repository:** `options-trading-assistant` (the one you just published)
   - **Branch:** `main`
   - **Main file path:** `app.py`
4. Click **Deploy**. Wait 2-4 minutes while it builds. Your app opens at its new link.

## Step 5 - Turn on Google Sheet logging (one paste)

1. In Streamlit Cloud, open your app -> **Settings** (or the "..." menu) -> **Secrets**.
2. Paste this line, replacing the link with YOUR Apps Script link (the one ending in `/exec`):
   ```
   google_sheet_webhook = "https://script.google.com/macros/s/XXXXXXXX/exec"
   ```
3. Click **Save**. The app restarts, and "Log this trade" now writes to your sheet.

## Step 6 - Keep it private to just you

1. In Streamlit Cloud: app -> **Settings** -> **Sharing**.
2. Set it so only invited people can view, and **add your own email**.
   Now only you can open the link.

## Step 7 (optional) - App icon on your phone

1. On your phone, open the app link in your browser.
2. Tap **Share** -> **Add to Home Screen**. You now have an app icon that opens it full-screen.

---

## Updating the app later

When the app is changed, open **GitHub Desktop**, click **Commit to main**, then
**Push origin**. Streamlit Cloud updates your live app automatically within a minute.
