# Tempest Inky

A Python application for the Raspberry Pi that displays a weather dashboard on a Pimoroni Inky e-ink display, pulling live data directly from a WeatherFlow Tempest weather station.

The layout is built to be read from a sofa, about 4.5 m away, in a dim room. Type is sized by viewing distance — comfortable reading needs a cap height of roughly distance ÷ 200 — and at that distance only one text element can ever be legible. So the panel carries exactly two things across the room: a number and a shape. Everything else is sized for walk-up.

Colour is fill, never type, and only the forecast bars carry it — absolute temperature and nothing else. Condition is carried by glyphs and has no ink of its own, so a mild week renders almost entirely black and white, and colour appearing across the room always means a cold or hot spell.

![Dashboard preview](dashboard-preview.png)

*Illustrative render. The panel shows your own station's data.*

---

## What's on the display

Two columns split by a rule at x=362, with fixed geometry — nothing moves between refreshes, which is what keeps an e-ink panel from ghosting and means the number you want is always where you last found it.

| Zone | Contents |
|------|----------|
| **NOW** (362 × 480, left) | The condition as a 100 px glyph over the temperature at 172 px in whole degrees, both right-justified to the column edge. Below a rule, three rows on a shared label column: `FEELS`, `TODAY` — Tempest's forecast high and low for the day — and `SO FAR`, the highest and lowest the station has actually measured since local midnight. |
| **METRICS** (168 px) | Wind and the last-updated time on top, rain and humidity below, in a 2×2 grid — always these four, always in this order, each carrying its unit in the label so the value stays as large as the cell allows. A cell with no data shows a dash rather than disappearing. `UPDATED` is when the data was last fetched, so after a failed fetch it shows how old the figures on the panel are. |
| **NEXT** (160 px) | The single highest-priority thing worth knowing — station trouble, lightning, rain starting, gusts or frost — in sentence case, over a sub-line of qualifying figures, over the next four hours. When nothing needs flagging it shows the current condition instead, such as `Partly cloudy`, and keeps its height either way. The exact temperature lives on the sub-line, since the hero rounds. |
| **LATER** (152 px) | Five days: name, condition glyph, a 12 px temperature bar and the high. Bar *length* is relative to that week's own range, so it shows the week's shape; bar *fill* is absolute — blue below 0 °C, green to 9, white to 19, then yellow, orange and red — so it shows the week's level. There is no key: the high is printed beside every bar, so the panel teaches its own scale. |

Times are always absolute. The panel only repaints when the temperature changes, so it can be hours old, and "in 3h 25m" would be wrong for nearly all of that time; `18:00` never is.

---

## Units

The panel shows metric by default. For Fahrenheit and other imperial units, add this to `~/secrets.py`:

```python
UNITS = "imperial"       # or "metric", the default
```

or set the `TEMPEST_UNITS` environment variable, which takes precedence. The next refresh picks it up — switching units changes the temperature as displayed, so it repaints straight away.

| Measure | Metric | Imperial |
|---------|--------|----------|
| Temperature | °C | °F |
| Wind and gusts | km/h | mph |
| Rain | mm, one decimal | inches, two decimals |
| Lightning distance | km | mi |

In imperial mode the forecast is fetched from Tempest in °F, so the highs, lows, hourly temperatures and feels-like match the Tempest app to the degree. A forecast that arrives as a whole °C would otherwise convert to a °F figure up to a degree off Tempest's own. Everything else is read in metric and converted for display, and every threshold stays where it was: gusts flag at 40 km/h (25 mph), lightning within 15 km (9 mi), and frost at 0 °C (32 °F). The forecast bar colours keep their bands too, so a bar is blue below 32 °F, green to 49, white to 67, yellow to 76, orange to 85 and red above that.

At 100 °F and above the hero temperature is three digits wide. It steps down to the largest size that fits its column, about 142 px, and returns to full size once the reading drops below 100.

---

## Hardware

| Component | Details |
|-----------|---------|
| Pi | Raspberry Pi Zero 2 W (or any Pi with 40-pin header) |
| Display | Pimoroni Inky Impression 7.3" (800×480, 7-colour) |
| OS | Raspberry Pi OS Lite — Bookworm (64-bit recommended) |

---

## Pre-install: Setting up a fresh headless Pi

These steps are done on your laptop/desktop before you touch the Pi.

### 1. Flash the SD card

Use the [Raspberry Pi Imager](https://www.raspberrypi.com/software/). Choose:
- **OS**: Raspberry Pi OS Lite (64-bit) — Bookworm
- **Storage**: your SD card (16 GB minimum; use a reputable brand — SanDisk or Samsung)

Before writing, click the **gear icon** (Advanced Options) and configure:
- ✅ Enable SSH (password authentication)
- ✅ Set username and password
- ✅ Configure WiFi (SSID + password)
- ✅ Set locale / timezone

Write the image to the SD card.

### 2. First boot

Insert the card into the Pi, attach the Inky display, and power on. Wait ~60 seconds, then SSH in:

```bash
ssh yourname@raspberrypi.local
# or use the IP address shown in your router's device list
```

### 3. Update the system

```bash
sudo apt update && sudo apt full-upgrade -y
sudo reboot
```

Wait for the Pi to reboot, then SSH back in before continuing.

> **Note:** Always update before installing. It avoids broken package dependencies and ensures you have the latest kernel/firmware for the Inky SPI driver.

---

## Installation

### 1. Install git (not pre-installed on Pi OS Lite)

```bash
sudo apt install -y git
```

### 2. Clone the repository

```bash
git clone https://github.com/moorew/tempest-inky.git
cd tempest-inky
```

### 3. Get your Tempest credentials

You need:
- **Station ID** — visible in the WeatherFlow app under *Settings → Stations*
- **Personal Use Token** — generate one at [tempestwx.com/account/tokens](https://tempestwx.com/account/tokens)

### 4. Run the installer

```bash
bash install.sh
```

The installer will:
- Install system dependencies
- Enable SPI and I2C hardware interfaces
- Ensure your user is in the `spi`, `i2c`, and `gpio` groups
- Apply the Bookworm SPI chip-select overlay fix
- Create a self-contained Python virtual environment
- Prompt you for your Station ID and API token, saved as `~/secrets.py`
- Install a **systemd timer** that refreshes the display on an adaptive schedule

### 5. Reboot

```bash
sudo reboot
```

The display will update automatically ~2 minutes after boot. After that it checks the weather every 15 minutes — more often when rain is likely within the hour, less often overnight — and repaints only when the temperature it shows has changed.

---

## How it works

| Feature | Detail |
|---------|--------|
| **Refresh schedule** | Adaptive: every 15 min normally, 10 min when rain is likely in the next hour, 60 min overnight (22:00–06:00). The timer ticks every 5 min and `main.py` decides whether a tick is due. |
| **Repaints** | Only when the current temperature, as displayed, has changed — `21.8°` to `21.9°` is still `22°` and leaves the panel alone. Everything else on the panel updates at the next repaint. `--force` always repaints |
| **Failed fetch** | Leaves the panel exactly as it is and retries on the next 5-minute tick. The `UPDATED` cell shows when the figures on the panel were fetched |
| **Measured high and low** | Each poll also reads the station's minute-by-minute history since the previous poll — the first poll of the day reads from midnight — and keeps a running high and low in `~/.tempest-last.json`, reset at local midnight. If the history can't be read, the range so far is kept and the gap is filled in by the next poll that succeeds, including hours when the Pi was switched off |
| **Virtual environment** | Self-contained; immune to system Python upgrades |
| **Network resilience** | Waits up to 2 minutes for WiFi before fetching |
| **API retry** | Up to 3 attempts with exponential backoff on failure |
| **Logging** | systemd journal (RAM-backed; no extra SD card wear) |
| **Hang protection** | systemd kills the process after 5 minutes |
| **Type** | Jost 300/400/600 and Weather Icons, both SIL OFL and committed to `assets/`. Figures are tabular, so values do not shuffle sideways between refreshes |

---

## Managing the service

```bash
# Check whether the timer is running
systemctl status tempest-inky.timer

# See recent logs (most useful for debugging)
journalctl -u tempest-inky.service -n 50

# Run one tick now (skips if a refresh isn't due yet; check the logs)
sudo systemctl start tempest-inky.service

# Force an immediate repaint, ignoring the schedule and the temperature check
cd ~/tempest-inky && venv/bin/python3 main.py --force

# Stop automatic refreshes
sudo systemctl stop tempest-inky.timer

# Re-enable after stopping
sudo systemctl start tempest-inky.timer
```

---

## Troubleshooting

### Display never updates after install

1. Check that you rebooted after installation.
2. Check logs: `journalctl -u tempest-inky.service -n 50`
3. Try a manual run to see errors directly. `--force` matters here: without it a run that is not due yet exits without touching the panel.
   ```bash
   cd ~/tempest-inky
   venv/bin/python3 main.py --force
   ```

### "No module named inky" or import errors

The virtual environment may be corrupted (common after a system Python upgrade). Re-run the installer — it rebuilds the venv cleanly:

```bash
cd ~/tempest-inky
bash install.sh
```

### Display shows "DATA FETCH ERROR"

This appears only when a fetch fails **and** the panel has never shown any weather — once it has, a failed fetch leaves the last paint in place. Your API credentials may be wrong, or the Pi has no internet. Check:
```bash
# Test connectivity
curl -s "https://swd.weatherflow.com/swd/rest/observations/station/YOUR_ID?token=YOUR_TOKEN" | python3 -m json.tool | head -20

# Check secrets file
cat ~/secrets.py
```

### SPI / display not detected

Verify SPI is enabled:
```bash
ls /dev/spidev*   # should show /dev/spidev0.0
```

If missing, run `sudo raspi-config` → Interface Options → SPI → Enable, then reboot.

### Works fine but stops after a few months

The most common cause is SD card corruption from power loss, or sectors wearing out on a low-quality card. Recommendations:
- Use a **name-brand SD card** (Samsung PRO Endurance or SanDisk High Endurance are designed for constant-write workloads like this)
- **Avoid powering off the Pi by unplugging** — use `sudo shutdown -h now` first whenever possible
- If it stops working, re-run `bash install.sh` before reinstalling the OS — it rebuilds the venv and re-registers the service, which fixes most software-level failures

---

## Manual run (without the timer)

```bash
cd ~/tempest-inky
venv/bin/python3 main.py --force
```

`--force` renders immediately, ignoring the adaptive schedule and repainting even if the temperature has not changed. Without it, a run that is not yet due exits straight away without fetching, and a run whose temperature matches the panel fetches but does not repaint.

```bash
venv/bin/python3 main.py --preview /tmp/panel.png
```

`--preview` writes the render to a PNG instead of pushing it to the panel, so the layout can be checked with no display attached. It ignores the refresh schedule and leaves it untouched.

```bash
venv/bin/python3 main.py --scenario storm --preview /tmp/storm.png
```

`--scenario` renders from canned data instead of fetching — `quiet`, `storm`, `snow`, `rain` and `heat` — which is how to check the NEXT headlines, the temperature bands and the four-hour strip without waiting for the weather. It touches neither the network nor the panel.

---

## Updating

Pull the latest code and re-run the installer. It is safe to run as many times as needed — it rebuilds the venv only if the Python version has changed, updates all packages, and refreshes the systemd unit files:

```bash
cd ~/tempest-inky
git pull
bash install.sh
```

No reboot is required for code-only updates. The timer picks up the new `main.py` on its next tick. If the systemd unit files changed (e.g. after upgrading from the old cron-based setup), reboot once after the first run of the new installer.

To check that the update took effect:

```bash
journalctl -u tempest-inky.service -n 20
```
