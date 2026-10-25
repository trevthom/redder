# Redder

Redder is a small Cinnamon/XApp system-tray app that controls the X11 GPU
gamma lookup table. Drag its vertical slider to isolate the red channel:
**up** is pure red, **down** is normal color. It can drive green and blue to
exactly zero, which `xrandr --gamma` cannot do.

## Install

The Debian package is built at `dist/redder_1.0.0_all.deb`:

```bash
sudo dpkg -i dist/redder_1.0.0_all.deb
```

If `dpkg` reports missing dependencies:

```bash
sudo apt-get -f install
```

A `.deb` is not strictly required for a tray app, but it installs the
launcher, app-menu entry, icon, and login autostart together and gives you a
clean removal path.

After installation:

- **Redder** appears in the application menu.
- It starts silently in the system tray at Cinnamon login.
- Start it now with `redder`, or silently with `redder --silent`.

## Use

- Left-click the Redder tray icon (a red ring on a dark tile) to open the
  panel.
- Drag the vertical slider all the way **up** for pure red; green and blue
  become zero.
- Drag all the way **down** for the original colors.
- Click the tray icon again or press **Esc** to close the panel.
- **Quit** restores the original color table and exits. To return to normal
  colors without quitting, drag the slider to the bottom.

## Safety

- Redder captures the original table at launch and restores it on quit,
  `SIGINT`/`SIGTERM`, or the next launch after a crash.
- While isolation is active it keeps a private backup at
  `~/.local/state/redder/gamma-backup.json`. The backup is removed as soon as
  normal colors are restored.
- If the display is stuck red, run `redder --reset`. From a source checkout,
  `python3 redder_app.py --reset` writes a standard linear ramp.
- Only one instance runs. A second launch activates the existing tray
  instance.

## Requirements

- Linux with an **X11** desktop session. This is an XRandR app; Wayland is
  not supported.
- Cinnamon (or another XApp status-icon host) with a system tray.
- Python 3.10+ and `python3-gi`.
- `gir1.2-gtk-3.0`, `gir1.2-xapp-1.0`, `libx11-6`, and `libxrandr2`.
  The `.deb` declares these dependencies.
- The app uses every connected XRandR CRTC and restores the exact captured
  table for each one.

## Autostart

The package installs `/etc/xdg/autostart/redder-autostart.desktop`, limited
to `X-Cinnamon`. To disable it, remove that file or untick **Redder** in
Cinnamon's Startup Applications.

## Uninstall

Quit Redder from its panel first so it restores the display, then:

```bash
sudo dpkg -r redder
```

This removes the launcher, autostart entry, icon, and packaged documentation.
The per-user log and recovery file under `~/.local/state/redder/` are not
removed.

## Build

```bash
./build_deb.sh
```

This creates `dist/redder_1.0.0_all.deb` and
`dist/redder_1.0.0_all.deb.sha256` using `dpkg-deb`. No network access is
needed.

## Development

```bash
python3 redder_app.py --silent   # tray only
python3 redder_app.py            # tray plus open panel
python3 redder_app.py --reset    # repair a stuck display
```

The tray app is a single GTK3 process. `gamma_control.py` contains the
X11/XRandR code, and `redder.css` contains the panel styling.

The original browser prototype (`app.py`, `static/`, and `run.sh`) is still
in this folder for reference, but the installed app does not use it.

## Why direct XRandR?

`xrandr --gamma` rejects zero factors, so it cannot make a channel fully
dark. Redder calls `XRRSetCrtcGamma` through `ctypes` and writes the 16-bit
lookup table directly. That is what makes a true zero possible.

## Files

- `redder_app.py` — tray app, slider, status, and restoration.
- `gamma_control.py` — X11/XRandR gamma access and ramp math.
- `redder.css` — panel styling.
- `assets/redder.svg` — app and tray icon.
- `packaging/` — desktop entries, launcher, and maintainer scripts.
- `build_deb.sh` — builds the `.deb`.
