## Install

Download `redder_1.0.0_all.deb` from the
[latest release](https://github.com/trevthom/redder/releases/latest) and
install it:

```bash
sudo dpkg -i redder_1.0.0_all.deb
```

If `dpkg` reports missing dependencies:

```bash
sudo apt-get -f install
```

The `.deb` also carries a `.sha256` checksum on the release page:

```bash
sha256sum -c redder_1.0.0_all.deb.sha256
```

A `.deb` is not strictly required for a tray app, but it installs the
launcher, app-menu entry, icon, and login autostart together and gives you a
clean removal path.
