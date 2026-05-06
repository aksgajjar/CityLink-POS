# CityLink-POS

Production POS for CityLink Convenience (BC, Canada). Touch-first cashier UI
+ admin panel. Offline-first SQLite. PyQt6 desktop app.

## How tester updates the app

1. Open the **CityLink-POS** folder in Finder.
2. Double-click **`Update CityLink.command`**.
3. Terminal opens automatically and:
   - downloads the latest changes from GitHub,
   - updates Python dependencies,
   - launches the app.

That's it. No commands to type.

If the laptop is offline, the app still opens with the version already
installed on the machine. The local sales database (`data/store.db`) is
never touched by the updater — daily transactions stay safe.

If something goes wrong, error details are written to `errors.log` in
the project folder.

## Developer quick start

```bash
git clone https://github.com/aksgajjar/CityLink-POS.git
cd CityLink-POS
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python main.py
```

Default factory PINs on a fresh install: **Admin 1234**, **Cashier 9999**.
