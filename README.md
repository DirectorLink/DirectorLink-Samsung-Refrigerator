# Samsung Refrigerator for Control4, by DirectorLink

**A free, open-source Control4 driver for Samsung Wi-Fi refrigerators**, from the makers of
[DirectorLink](https://github.com/IsraelCIL/DirectorLink), the open-source management layer for Control4 homes.

It controls and monitors the refrigerator through the Samsung SmartThings cloud. It needs no extra
hardware and no subscription.

| Feature | Control4 app | Programming |
|---|---|---|
| **Power Cool** (fast cooling) | tile, tap to toggle | On / Off / Toggle, events, conditional |
| **Power Freeze** (fast freezing) | tile, tap to toggle | On / Off / Toggle, events, conditional |
| **Sabbath Mode** | tile, tap to toggle | On / Off / Toggle, events, conditional |
| **Ice Maker** | tile, tap to toggle | On / Off / Toggle, events, conditional |
| Fridge and freezer **setpoints** | – | set commands, Composer dropdown, variables |
| **Temperatures**, **doors**, FlexZone mode | – | Door Opened / Closed / **Left Open** events, variables |
| **Water filter** | – | Needs Replacement event, Reset command |
| **Power**, energy, online status | – | Offline / Online events, variables |

The driver detects each feature per refrigerator. Anything your model doesn't support shows as
*Not available*, and its tile is greyed out.

Tested live on an RF85T901335ML (SmartThings model `TP2X_REF_20K`). The offline test suite covers
the TP2X, TP1X, Family Hub, one-door and older dongle-based models.

---

## Installation

You add it like any Control4 driver, with **Composer Pro**.

1. Download **`DirectorLink-Samsung-Refrigerator.c4z`** from the
   [latest release](https://github.com/IsraelCIL/DirectorLink-Samsung-Refrigerator/releases/latest).
   Each release also lists `SHA256SUMS.txt`.
2. In Composer Pro: **Driver → Add or Update Driver or Agent**, then select the file.
3. On the Search tab, tick **Local**, search for **Samsung Refrigerator (DirectorLink)**, and add it to the kitchen.
4. Follow the setup below. The same steps appear on the driver's **Documentation** tab in Composer.

Requirements:
- The refrigerator is on Wi-Fi and added to the SmartThings app.
- The controller has internet access.
- Control4 OS 3.3 or newer (tested on OS 4.2.1).

> **Keep the file name exactly `DirectorLink-Samsung-Refrigerator.c4z`.** Composer identifies the driver by its file name.
> A browser saves a second download as `DirectorLink-Samsung-Refrigerator (1).c4z`, which Composer installs as a
> *separate* driver instead of updating, and the tile icons break. Delete older downloads first.

---

## Setup

### Step 1: Get the right SmartThings token

The driver needs a **Personal Access Token (PAT)** **once**, to create its own secure login (OAuth).

1. On a computer, open **https://account.smartthings.com/tokens**.
2. Sign in with the **same Samsung account the refrigerator is registered to** in the SmartThings app.
3. Click **Generate new token** and give it any name, for example `Control4`.
4. Under **Authorized Scopes**, **tick every checkbox in these three sections:**
   - **Devices**: all boxes
   - **Locations**: all boxes
   - **Apps**: all boxes

   If you're unsure, tick every box on the page. The token is used once and expires by itself after 24 hours.
5. Click **Generate token** and copy it. It looks like `1a2b3c4d-1111-2222-3333-444455556666`
   and is shown **only once**.
6. In Composer, select the driver and paste the token into **Personal Access Token** → **Set**.

**Getting the token wrong is the most common setup problem:**

| Mistake | What the driver shows |
|---|---|
| No scopes ticked at all | `Create app failed: Forbidden ...` |
| Only **Devices** ticked (Apps missing) | `Create app failed: Forbidden ... the token needs ALL Apps scopes` |
| Token older than 24 hours | `Create app failed: Not authorized ...`. Generate a new token |
| Signed in with a different Samsung account | `No refrigerators found in this SmartThings account` |

### Step 2: Create the OAuth app (this is where the Client ID comes from)

On the driver's **Actions** tab click **1. Create OAuth App (uses Personal Access Token)**.
**OAuth Client ID** and **OAuth Client Secret** fill in automatically.

> You don't need to look up a Client ID anywhere: the driver creates it. SmartThings never shows the
> secret again, so **save both values**. If you reinstall the driver, paste the saved Client ID and
> Secret instead of creating another app.

### Step 3: Sign in once

1. Click **2. Show Authorization URL**. The link appears on the **Lua** tab and in the **Authorization URL** property.
2. Open it in a browser, sign in with the same Samsung account, choose the location, and click **Authorize**.
3. The browser lands on `https://httpbin.org/get?code=...`. Copy the **whole address** from the address bar.
4. **Within about a minute**, paste it into the **Authorization Code** property → **Set**.

**Authorization Status** should now say *Authorized (OAuth) … renews automatically*. You're done: the
driver renews its own access from now on, and the Personal Access Token is no longer used.

### Step 4: Select the refrigerator and show the tiles

- With one refrigerator, it's selected automatically. Otherwise, choose it in **Select Refrigerator**.
- **Supported Features** lists what your model offers.
- In the room's Navigator settings, make the tiles you want visible (**Power Cool**, **Power Freeze**,
  **Sabbath Mode**, **Ice Maker**). Hide the ones your model doesn't support.
- Optional: bind keypad buttons to the *Keypad Button: …* connections. Their LEDs follow the state.

---

## Programming examples

- **Shabbat:** In the Scheduler, Friday at sunset − 30 min runs *Set Feature → Sabbath Mode → On*;
  Saturday at sunset + 60 min runs *Sabbath Mode → Off*. Add a notification on **Command Failed**.
- **Big grocery run:** a keypad button runs *Power Cool → On*.
- **Door alarm:** on **Door Left Open** (default 5 minutes), send a push notification.

Commands go through Samsung's cloud. The driver re-reads the refrigerator after each command and shows
it as done only when the refrigerator confirms, typically in about 4 seconds. If it doesn't confirm, the
driver fires **Command Failed**.

---

## Updating

Download the new release's `DirectorLink-Samsung-Refrigerator.c4z`. Mind the file name, as described in
Installation. Then run **Driver → Add or Update Driver** in Composer Pro. Your settings and sign-in are kept.
To go back to an older version, install the `.c4z` from an older release the same way.

---

## FAQ

**Where do I find the Client ID?** You don't need to look it up: the driver creates it in Step 2.
If you lost it, clear the two OAuth fields and run Step 2 again with a new token, then Step 3.

**Do I need to repeat this every 24 hours?** No. Only the Personal Access Token expires after 24 hours,
and it's needed only once. After Step 3, the driver renews its OAuth access automatically.

**When do I need to authorize again?** If the controller is offline for about 30 days, or if access is
removed in the SmartThings app. Repeat Step 3 only.

**A feature shows "Not available".** Your model or firmware doesn't expose it to SmartThings.
For example, some Family Hub units have Sabbath mode disabled in SmartThings.

**Is there a local (no-cloud) option?** Not for current Samsung refrigerators. Their local protocol needs
device certificates, so this driver uses the official SmartThings cloud API, the same one Home Assistant uses.

**Something doesn't work.** [Open an issue](https://github.com/IsraelCIL/DirectorLink-Samsung-Refrigerator/issues/new/choose).
Include the Composer properties *Driver Version*, *Model*, *Supported Features* and *Driver Status*, plus the Lua
output with Debug Mode on. Never post tokens or the Client Secret. For security problems, see [SECURITY.md](SECURITY.md).

---

## For developers

```
src/driver.xml               driver definition (4 uibutton proxies, properties, actions, commands, events)
src/driver.lua               driver logic (OAuth, SmartThings API, feature detection, UI, programming)
src/www/documentation.html   Composer Documentation tab
src/www/icons/<feature>/     tile icons per state, generated by scripts/make_icons.py
scripts/build.py             stamp VERSION, validate and package -> dist/DirectorLink-Samsung-Refrigerator.c4z
scripts/check_repo.py        fail if secrets or local configuration would be committed
tests/test_driver.py         offline tests: real driver.lua vs mocked Control4 + fake SmartThings cloud
tests/live_test.py           live tests against a real refrigerator (reverts every change)
tests/fixtures/              Home Assistant SmartThings fixtures (Apache-2.0, see NOTICE)
```

```
pip install -r requirements-dev.txt
python tests/test_driver.py          # 25 scenarios x (Lua 5.1, LuaJIT 2.1)
python scripts/check_repo.py
python scripts/build.py              # -> dist/DirectorLink-Samsung-Refrigerator.c4z

set ST_TOKEN=<personal access token> # live tests operate your refrigerator, then revert every change
python tests/live_test.py features
```

**Releasing:** `VERSION` (`MAJOR.MINOR.PATCH`) is the only version to edit. The build stamps it into the
package: `driver.xml` `<version>` = MAJOR×10000 + MINOR×100 + PATCH, and `DRIVER_VERSION` in `driver.lua`.
Add `docs/releases/v<VERSION>.md` and push to `main`. The release workflow runs the tests, builds, and
publishes the GitHub Release with the `.c4z` and `SHA256SUMS.txt`. Without the notes file nothing is
published, and an existing release is never replaced.

Don't zip with PowerShell 5.1's `Compress-Archive`: it writes backslash paths that break the icons.
Never change proxies or connections in `driver.xml` once a version is released; installed projects depend on them.

### SmartThings API used (same as Home Assistant's `smartthings` integration)

| Feature | Component | Capability → command |
|---|---|---|
| Power Cool | `main` | `samsungce.powerCool` → `activate` / `deactivate` (fallback `refrigeration.setRapidCooling("on"/"off")`) |
| Power Freeze | `main` | `samsungce.powerFreeze` → `activate` / `deactivate` (fallback `refrigeration.setRapidFreezing`) |
| Sabbath | `main` | `samsungce.sabbathMode` → `on` / `off` |
| Ice maker | `icemaker` | `switch` → `on` / `off` |
| Setpoints | `cooler` / `onedoor`, `freezer` | `thermostatCoolingSetpoint` → `setCoolingSetpoint(int)` |
| Water filter | `main` | `custom.waterFilter` → `resetWaterFilter` |

A feature counts as unsupported when its capability is missing, listed in that component's
`custom.disabledCapabilities`, or its component is listed in `main.custom.disabledComponents`.
Auth: OAuth2 API_ONLY app with scopes `r:devices:* x:devices:*`. Access tokens last 24 hours;
refresh tokens rotate on every use, so each new one is persisted immediately and refreshes are serialized.

---

Licensed under the [Apache License 2.0](LICENSE). Copyright 2026 DirectorLink.
Not affiliated with or endorsed by Samsung, SmartThings, Control4 or Snap One (see [NOTICE](NOTICE)).
