# DirectorLink · Samsung Refrigerator for Control4

Free, open-source Control4 driver for Samsung Wi-Fi refrigerators. Part of [DirectorLink Drivers](https://directorlink.io/drivers).

It works through the Samsung SmartThings cloud: no extra hardware and no subscription.

Free. No subscription, no license key, no account with us.

Made by [DirectorLink](https://directorlink.io), the open-source management layer for Control4 homes. Works on its own: DirectorLink is not required.

| Feature | Control4 app | Programming |
|---|---|---|
| **Samsung Refrigerator** status | tile: green OK, amber door open, red problem; tap to refresh | – |
| **Power Cool** (fast cooling) | tile, tap to toggle | On / Off / Toggle, events, conditional |
| **Power Freeze** (fast freezing) | tile, tap to toggle | On / Off / Toggle, events, conditional |
| **Sabbath Mode** | tile, tap to toggle | On / Off / Toggle, events, conditional |
| **Ice Maker** | tile, tap to toggle | On / Off / Toggle, events, conditional |
| Fridge and freezer **setpoints** | – | set commands, Composer dropdown, variables |
| **Temperatures**, **doors**, FlexZone mode | – | Door Opened / Closed / **Left Open** events, variables |
| **Water filter** | – | Needs Replacement event, Reset command |
| **Power**, energy, online status | – | Offline / Online events, variables |

The driver checks which features each refrigerator supports. Anything your model doesn't have shows as
*Not available*, and its tile is greyed out.

## Requirements

- **Control4:** OS 3.3 or newer, with internet access on the controller.
- **Refrigerator:** a Samsung refrigerator on Wi-Fi, added to the SmartThings app.
- **For setup:** the login of the Samsung account the refrigerator is registered to, and about 10 minutes.
- **Tested on:**
  - Live: an RF85T901335ML (SmartThings model `TP2X_REF_20K`) on a CORE-1 with OS 4.2.1.
  - Offline: TP2X, TP1X, Family Hub, one-door and older dongle-based models.

## Installation

1. Download **`DirectorLink-Samsung-Refrigerator.c4z`** from the [latest release](../../releases/latest),
   or from https://directorlink.io/drivers/samsung-refrigerator. Each release also lists `SHA256SUMS.txt`.
2. In Composer Pro: **Driver → Add or Update Driver or Agent**, then select the file.
3. On the Search tab, tick **Local**, search for **DirectorLink**, and add **DirectorLink · Samsung Refrigerator** to the room.

> **Keep the file name exactly `DirectorLink-Samsung-Refrigerator.c4z`.** Composer identifies a driver by its file name.
> A browser saves a second download as `DirectorLink-Samsung-Refrigerator (1).c4z`, which Composer installs as a
> *separate* driver instead of updating this one. Delete older downloads first.

## Setup

The same steps appear on the driver's **Documentation** tab in Composer.

### Step 1: Get the right SmartThings token

The driver needs a **Personal Access Token** **once**, to create its own secure login (OAuth).

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
4. **Within a few minutes**, paste it into the **Authorization Code** property → **Set**.
   Pasted it into **OAuth Redirect URI** or **Personal Access Token** by mistake? The driver notices,
   puts that field back, and signs in anyway.

**Authorization Status** should now say *Authorized (OAuth) … renews automatically*. You're done: the
driver renews its own access from now on, and the Personal Access Token is no longer used.

### Step 4: Select the refrigerator and show the tiles

- With one refrigerator, it's selected automatically. Otherwise, choose it in **Select Refrigerator**.
- **Supported Features** lists what your model offers.
- The driver appears in the room as **Samsung Refrigerator**, with four feature tiles next to it.
- In the room's Navigator settings, make the tiles you want visible (**Samsung Refrigerator**, **Power Cool**,
  **Power Freeze**, **Sabbath Mode**, **Ice Maker**). Hide the ones your model doesn't support.
- Optional: bind keypad buttons to the *Keypad Button: …* connections. Their LEDs follow the state.

## Programming examples

- **Shabbat:** In the Scheduler, Friday at sunset − 30 min runs *Set Feature → Sabbath Mode → On*;
  Saturday at sunset + 60 min runs *Sabbath Mode → Off*. Add a notification on **Command Failed**.
- **Big grocery run:** a keypad button runs *Power Cool → On*.
- **Door alarm:** on **Door Left Open** (default 5 minutes), send a push notification.

Commands go through Samsung's cloud. The driver re-reads the refrigerator after each command and shows
it as done only when the refrigerator confirms, typically in about 4 seconds. If it doesn't confirm, the
driver fires **Command Failed**.

## Updating

Download the new `DirectorLink-Samsung-Refrigerator.c4z` from the [latest release](../../releases/latest).
Mind the file name, as described in Installation. Then run **Driver → Add or Update Driver or Agent** in
Composer Pro. Your settings and sign-in are kept. To go back to an older version, install the `.c4z` from an
older release the same way.

## Troubleshooting

| What you see | What to do |
|---|---|
| `Create app failed: Forbidden ...` | The token is missing scopes. Make a new one with **all Devices, Locations and Apps** boxes ticked. |
| `Create app failed: Not authorized ...` | The token is older than 24 hours. Make a new one. |
| `OAuth Client ID is already set` | The app already exists. Use it, or clear both OAuth fields to create a new one. |
| `No refrigerators found in this SmartThings account` | You signed in with a different Samsung account than the one the refrigerator is registered to. |
| `Authorization failed: ...` | The code expired or was already used. Click **2. Show Authorization URL** and sign in again. |
| `Re-authorization required` | SmartThings no longer accepts the saved login, for example after about 30 days with the controller offline, or after access was removed in the SmartThings app. Repeat Step 3 only. |
| A feature shows *Not available* | Your model or its firmware doesn't offer it in SmartThings. Check for a refrigerator update in the SmartThings app. |
| `... was not confirmed by the refrigerator` | SmartThings accepted the command but the refrigerator didn't change. Check that it's online in the SmartThings app. |
| `Rate limited by SmartThings` | SmartThings allows about 12 reads a minute per device. Don't set the poll interval very short, and don't send many commands at once. |
| Lost the Client ID or Secret | Clear both OAuth fields, make a new token, and repeat Steps 2 and 3. |

The Personal Access Token is needed only once. After Step 3 the driver renews its own access.
There's no local (no-cloud) option: current Samsung refrigerators need device certificates for local control.
So the driver uses the official SmartThings cloud API, the same one Home Assistant uses.

## Privacy

The driver talks only to the Samsung cloud. It sends nothing to DirectorLink and collects no usage data.

The sign-in in Step 3 happens in your browser, which lands on httpbin.org to show you the one-time code.
SmartThings tokens are kept in Control4's encrypted driver storage and are never written to the log.

## Support

Community-supported, best effort. Report problems in [Issues](../../issues).

Include the Composer properties *Driver Version*, *Model*, *Supported Features* and *Driver Status*, plus the
Lua output with Debug Mode on. Never post tokens or the Client Secret. For security problems, see [SECURITY.md](SECURITY.md).

## Building from source

```
src/driver.xml               driver definition (5 uibutton proxies, properties, actions, commands, events)
src/driver.lua               driver logic (OAuth, SmartThings API, feature detection, UI, programming)
src/www/documentation.html   Composer Documentation tab
src/www/icons/<tile>/        tile icons per state + Composer images, generated by scripts/make_icons.py
scripts/gen_ui_xml.py        regenerates driver.xml's proxies, Navigator icons and connections from one table
scripts/build.py             stamp VERSION, validate and package -> dist/DirectorLink-Samsung-Refrigerator.c4z
scripts/check_repo.py        fail if secrets or local configuration would be committed
tests/test_driver.py         offline tests: real driver.lua vs mocked Control4 + fake SmartThings cloud
tests/live_test.py           live tests against a real refrigerator (reverts every change)
tests/fixtures/              Home Assistant SmartThings fixtures (Apache-2.0, see NOTICE)
```

```
pip install -r requirements-dev.txt
python tests/test_driver.py          # offline tests under Lua 5.1 and LuaJIT 2.1
python scripts/check_repo.py
python scripts/build.py              # -> dist/DirectorLink-Samsung-Refrigerator.c4z

set ST_TOKEN=<personal access token> # live tests operate your refrigerator, then revert every change
python tests/live_test.py features
```

**Releasing:** `VERSION` (`MAJOR.MINOR.PATCH`) is the only place the version is set. The build stamps it into the
package: `driver.xml` `<version>` = MAJOR×10000 + MINOR×100 + PATCH, and `DRIVER_VERSION` in `driver.lua`.
Add `docs/releases/v<VERSION>.md` and push to `main`. The release workflow runs the tests, builds, and
publishes the release with the `.c4z` and `SHA256SUMS.txt`. Without the notes file nothing is published,
and an existing release is never replaced.

Don't zip with PowerShell 5.1's `Compress-Archive`: it writes backslash paths that break the icons.
Never change the `.c4z` file name, proxy binding ids, variable order, event and command ids, or property names
once released, because installed projects and dealers' programming depend on them.

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
Auth: an OAuth2 API_ONLY app with scopes `r:devices:* x:devices:*`. Access tokens last 24 hours.
Refresh tokens rotate on every use, so each new one is saved immediately and refreshes run one at a time.

## License and trademarks

See [LICENSE](LICENSE) and [NOTICE](NOTICE).

Apache License 2.0. Copyright 2026 DirectorLink. Not affiliated with or endorsed by Samsung, Control4 or Snap One. Samsung, Control4 and related names are trademarks of their respective owners.
