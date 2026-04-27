# Deploying the meshcore stack on a Raspberry Pi

This walks through getting the gateway, RPC service, and TAK bridge running
on a Pi 4 with Ubuntu Server 22.04+ (or Raspberry Pi OS bookworm+). Targets
the field-deployable Home Node setup: USB-attached RAK4631, optional
GPS via gpsd, mosquitto on localhost, off-grid power.

If you want to skip the prose and just run something, jump to
[Quick install](#quick-install).

---

## What you're deploying

```
  ┌─────────────────────────────────────────────────────────────────┐
  │  Raspberry Pi 4, Ubuntu Server                                  │
  │                                                                 │
  │   ┌──────────────┐                                              │
  │   │ mosquitto    │ ← MQTT broker, localhost:1883                │
  │   │  .service    │                                              │
  │   └──┬───────────┘                                              │
  │      │                                                          │
  │   ┌──┴──────────────────┐  ┌───────────────────┐                │
  │   │ meshcore-mqtt       │  │ meshcore-rpc-     │                │
  │   │  .service           │  │  services.service │                │
  │   │  (gateway)          │  │  (RPC + state)    │                │
  │   └──┬──────────────────┘  └─────────┬─────────┘                │
  │      │                                │                          │
  │      │ /dev/meshcore-gateway          │                          │
  │      ▼                                ▼                          │
  │  USB → RAK4631                   /var/lib/meshcore-rpc-services/ │
  │                                       state.sqlite3              │
  │                                                                 │
  │   ┌─────────────────────┐                                       │
  │   │ meshcore-tak-bridge │ ──TCP──▶ TAK Server (LAN, e.g.        │
  │   │  .service           │          192.168.1.50:8087)            │
  │   └─────────────────────┘                                       │
  │                                                                 │
  │   gpsd.service (optional) ──▶ localhost:2947 ──▶ rpc service    │
  │   (Pi has its own GPS)                                          │
  └─────────────────────────────────────────────────────────────────┘
```

Four systemd units. All start at boot, all restart on failure, all log to
journalctl. Each has a single config file in `/etc/`. State lives under
`/var/lib/meshcore-rpc-services/`.

---

## Prerequisites

**Hardware:**

- Raspberry Pi 4 (2GB+ is plenty)
- microSD card (16GB+)
- RAK4631 + RAK19007 connected via USB
- Optional: RAK12501 GNSS module connected to the Pi (not the field node)
- **Power supply:** official Pi 4 USB-C adapter (5.1V/≥3A) or equivalent.
  Thin USB cables or cheap adapters cause undervoltage throttling that
  degrades serial reliability and slows the CPU. Use ≥20 AWG cable and
  check for throttle events after first boot:
  ```bash
  vcgencmd get_throttled
  # healthy: throttled=0x0
  # anything else: replace the supply or cable before deploying
  ```
  The `mc/svc/health` MQTT topic includes a `power_ok` field (true/false)
  when running on a Pi, so you can monitor this remotely.

**OS:** Ubuntu Server 22.04 LTS or Raspberry Pi OS bookworm. Both have
Python 3.10+ and a recent enough mosquitto package. Older OSes may work
but aren't tested.

**Network:** during install you need internet for `apt` and `pip`. After
install, the stack runs entirely offline; nothing reaches outside the Pi
unless you configure the TAK bridge to a remote endpoint.

---

## Quick install

If you have both repos checked out on the Pi:

```bash
cd ~/meshcore-rpc-services
sudo bash deploy/install.sh ~/meshcore-mqtt
```

That installs OS packages, creates the `meshcore` user, builds two venvs,
copies config templates and unit files, and enables units at boot. It
does **not** start them — you'll want to edit the configs first.

Add `--with-gpsd` if your Pi has its own GPS module and you want gpsd
installed alongside.

After the script runs, see [Configuration](#configuration) below.

---

## Manual install (the steps the script runs)

If something goes wrong, or you want to understand the setup, walk
through these by hand.

### 1. OS packages

```bash
sudo apt update
sudo apt install -y mosquitto mosquitto-clients python3 python3-venv python3-pip
sudo systemctl enable --now mosquitto
```

Add autosave settings to `/etc/mosquitto/mosquitto.conf` so retained
messages survive a broker crash rather than just a clean shutdown:

```
persistence true
persistence_location /var/lib/mosquitto/
autosave_on_changes true
autosave_interval 30
```

Verify mosquitto is up:

```bash
mosquitto_sub -t '$SYS/#' -C 1
```

You should see a single broker stats message. If not, mosquitto isn't
running.

### 2. Service user

```bash
sudo useradd --system --no-create-home --shell /usr/sbin/nologin meshcore
sudo usermod -aG dialout meshcore
```

The `dialout` group is what lets the gateway open `/dev/ttyACM*` without
root. Without it, the gateway service will start and immediately fail
with a permission error on the serial device.

### 3. Python installs

Two separate venvs, one per repo:

```bash
sudo mkdir -p /opt/meshcore-mqtt /opt/meshcore-rpc-services
sudo python3 -m venv /opt/meshcore-mqtt/venv
sudo python3 -m venv /opt/meshcore-rpc-services/venv

sudo /opt/meshcore-mqtt/venv/bin/pip install ~/meshcore-mqtt
sudo /opt/meshcore-rpc-services/venv/bin/pip install ~/meshcore-rpc-services
```

The second install gives you both `meshcore-rpc-services` and
`meshcore-tak-bridge` in `/opt/meshcore-rpc-services/venv/bin/` because
they share a wheel.

Verify the binaries exist:

```bash
ls /opt/meshcore-mqtt/venv/bin/meshcore-mqtt
ls /opt/meshcore-rpc-services/venv/bin/meshcore-rpc-services
ls /opt/meshcore-rpc-services/venv/bin/meshcore-tak-bridge
```

### 4. Configuration files

```bash
sudo mkdir -p /etc/meshcore-mqtt /etc/meshcore-rpc-services

sudo cp ~/meshcore-mqtt/config.example.yaml /etc/meshcore-mqtt/config.yaml
sudo cp ~/meshcore-rpc-services/config.example.yaml /etc/meshcore-rpc-services/config.yaml

sudo chown -R root:meshcore /etc/meshcore-mqtt /etc/meshcore-rpc-services
sudo chmod 750 /etc/meshcore-mqtt /etc/meshcore-rpc-services
sudo chmod 640 /etc/meshcore-mqtt/config.yaml /etc/meshcore-rpc-services/config.yaml
```

Then edit the configs — see [Configuration](#configuration) below.

### 5. systemd units

```bash
cd ~/meshcore-rpc-services/deploy/systemd
sudo cp meshcore-mqtt.service meshcore-rpc-services.service meshcore-tak-bridge.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable meshcore-mqtt meshcore-rpc-services meshcore-tak-bridge
```

### 6. udev rule for the gateway

This is the one fiddly part. The kernel might assign the RAK4631 to
`/dev/ttyACM0` today and `/dev/ttyACM1` tomorrow if you replug or boot
in a different order. We'll create a stable symlink `/dev/meshcore-gateway`.

```bash
sudo cp ~/meshcore-rpc-services/deploy/udev/99-meshcore-gateway.rules /etc/udev/rules.d/
```

The rule has placeholder VID/PID values. Find your board's actual values:

```bash
lsusb
```

Look for the line that disappears when you unplug the RAK4631. Something like:

```
Bus 001 Device 005: ID 1915:520f Nordic Semiconductor ASA
```

`1915:520f` is `VENDOR:PRODUCT`. Edit the rule:

```bash
sudo nano /etc/udev/rules.d/99-meshcore-gateway.rules
```

Replace the placeholder VID/PID lines with what `lsusb` showed. Reload:

```bash
sudo udevadm control --reload-rules
sudo udevadm trigger
ls -l /dev/meshcore-gateway
```

You should see a symlink pointing at `ttyACM0` (or wherever the kernel
put it). If you don't, see [Troubleshooting](#troubleshooting).

---

## Configuration

### Gateway: `/etc/meshcore-mqtt/config.yaml`

The default talks to the Pi's USB-connected RAK4631 via the udev symlink:

```yaml
mqtt:
  broker: localhost
  port: 1883
  topic_prefix: meshcore
  qos: 0
  retain: false

meshcore:
  connection_type: serial
  address: /dev/meshcore-gateway
  baudrate: 115200
  timeout: 5
  events:
    - CONTACT_MSG_RECV
    - CHANNEL_MSG_RECV
    - CONNECTED
    - DISCONNECTED
    - BATTERY
    - ADVERTISEMENT
    - DEVICE_INFO

log_level: INFO
```

### RPC service + TAK bridge: `/etc/meshcore-rpc-services/config.yaml`

One file, both processes read it. The service ignores the `tak:` section;
the bridge ignores everything else.

Minimal viable for a static-base, no-TAK setup:

```yaml
mqtt:
  host: localhost
  port: 1883
  client_id: meshcore-rpc-services
  qos: 1

service:
  db_path: /var/lib/meshcore-rpc-services/state.sqlite3
  log_level: INFO
  retention:
    days: 30
  base:
    source: static
    static_lat: 27.9379
    static_lon: -82.2859
```

Add for GPSD-driven base location:

```yaml
service:
  base:
    source: gpsd
    gpsd_host: 127.0.0.1
    gpsd_port: 2947
    publish_interval_s: 30.0
    max_acc_m: 50.0
```

Add for TAK output:

```yaml
tak:
  mqtt_client_id: meshcore-tak-bridge
  server:
    host: 192.168.1.50    # your TAK Server LAN IP
    port: 8087
  callsign_template: "MC-{id}"
  publish_interval_s: 10.0
  stale_after_s: 300
```

After editing, restart whatever picked up the change:

```bash
sudo systemctl restart meshcore-rpc-services meshcore-tak-bridge
```

---

## Bringing it up

```bash
sudo systemctl start meshcore-mqtt
sudo systemctl start meshcore-rpc-services
sudo systemctl start meshcore-tak-bridge
```

Verify everyone's running:

```bash
systemctl status meshcore-mqtt meshcore-rpc-services meshcore-tak-bridge
```

All three should show `active (running)`. If one is `failed`, see
[Troubleshooting](#troubleshooting).

Watch the wire to confirm the stack is talking:

```bash
mosquitto_sub -h localhost -t '#' -v
```

You should see, in order:

1. The gateway's retained `meshcore/status` and `mc/gateway/status`
2. The service's retained `mc/svc/health`
3. If the base is configured: retained `mc/base/location`
4. As field nodes report in: `mc/node/<id>/{location,state,battery}`

---

## GPSD (optional)

If you want the Pi's own GPS feeding the base location:

```bash
sudo apt install gpsd gpsd-clients
sudo systemctl enable --now gpsd.socket
```

By default Ubuntu's gpsd is socket-activated and only listens to `localhost`.
That's exactly what we want. Tell it about the GPS device by editing
`/etc/default/gpsd`:

```
DEVICES="/dev/serial0"   # or wherever your GPS shows up
GPSD_OPTIONS="-n"
```

Restart gpsd, then verify with `cgps` (Ctrl-C to exit). Once you see a
fix, set `service.base.source: gpsd` in the rpc-services config and
restart:

```bash
sudo systemctl restart meshcore-rpc-services
```

Watch the base location flow through:

```bash
mosquitto_sub -h localhost -t mc/base/location -v
```

---

## Updating

To deploy a new version of either repo:

```bash
cd ~/meshcore-mqtt && git pull
sudo /opt/meshcore-mqtt/venv/bin/pip install --upgrade ~/meshcore-mqtt
sudo systemctl restart meshcore-mqtt

cd ~/meshcore-rpc-services && git pull
sudo /opt/meshcore-rpc-services/venv/bin/pip install --upgrade ~/meshcore-rpc-services
sudo systemctl restart meshcore-rpc-services meshcore-tak-bridge
```

The SQLite migration runs on service startup; nothing manual needed for
schema changes if you've followed the migration patterns in the repo.

---

## Troubleshooting

### `meshcore-mqtt.service` fails immediately

Symptom: `systemctl status meshcore-mqtt` shows
`status=1/FAILURE` or a permission error.

Likely causes, in order of frequency:

1. **`/dev/meshcore-gateway` doesn't exist.** The udev rule isn't
   matching your board. Check:
   ```bash
   ls -l /dev/meshcore-gateway     # should be a symlink
   lsusb                           # confirm the board is detected
   udevadm info -a /dev/ttyACM0    # show what udev knows about it
   ```
   The `ATTRS{idVendor}` and `ATTRS{idProduct}` in the udev output need
   to match the rule. Edit `/etc/udev/rules.d/99-meshcore-gateway.rules`
   and reload:
   ```bash
   sudo udevadm control --reload-rules
   sudo udevadm trigger
   ```

2. **`meshcore` user not in `dialout` group.** Check with `groups meshcore`.
   Fix:
   ```bash
   sudo usermod -aG dialout meshcore
   sudo systemctl restart meshcore-mqtt
   ```

3. **Another process holds the serial port.** Common culprit: ModemManager
   probes any new USB-serial it sees. Check:
   ```bash
   sudo lsof /dev/ttyACM0
   ```
   If ModemManager is the culprit:
   ```bash
   sudo systemctl mask ModemManager.service
   ```

### `meshcore-rpc-services.service` fails

```bash
journalctl -u meshcore-rpc-services -n 50
```

Most likely:

1. **YAML syntax error in config.** The error message will be specific
   ("expected key, got X at line N"). Fix and `systemctl restart`.

2. **DB path not writable.** The systemd unit declares
   `StateDirectory=meshcore-rpc-services`, so `/var/lib/meshcore-rpc-services/`
   is auto-created and chowned. If you customized `db_path` to somewhere
   else, that path needs to exist and be writable by the `meshcore` user.

3. **Cannot connect to broker.** Check mosquitto is running:
   ```bash
   sudo systemctl status mosquitto
   mosquitto_sub -t '$SYS/#' -C 1
   ```

### `meshcore-tak-bridge.service` runs but ATAK shows nothing

The bridge starts even if the TAK Server is unreachable; it just
keeps reconnecting. Check its log:

```bash
journalctl -u meshcore-tak-bridge -f
```

Look for `TAK connected` (good) vs `TAK connection ended:
[Errno 111] Connection refused` (TAK Server unreachable or wrong port).

Also confirm the bridge is seeing the MQTT side. With it stopped:

```bash
sudo systemctl stop meshcore-tak-bridge
mosquitto_sub -h localhost -t 'mc/node/+/location' -t 'mc/base/location' -v
```

If nothing appears, no node has reported a location yet. Without a
location, the bridge has nothing to emit.

### "It worked yesterday and stopped working today"

```bash
journalctl -u meshcore-mqtt -u meshcore-rpc-services -u meshcore-tak-bridge \
    --since '6 hours ago'
```

Common causes:

- **Kernel 6.12 CDC-ACM regression.** Kernel 6.12.47 (and several adjacent
  point releases) has a known regression where CDC-ACM devices (`/dev/ttyACM*`)
  fail to enumerate — `dmesg` shows the device connecting but no node appears
  in `/dev/`. The gateway service will report
  `[Errno 2] No such file or directory: '/dev/meshcore-gateway'`.
  Workaround: pin to a known-good kernel (e.g. 6.11.x or 6.13.x) or use
  the latest LTS point release. Check your kernel version with `uname -r`
  and compare against known-bad releases in the Ubuntu/Debian changelogs.

- A kernel update changed how the USB serial device gets named and the
  udev rule needs adjusting (re-check `udevadm info -a`).

- The system clock is off and the radio's time-based ACKs are misbehaving.

### Resetting state without reinstalling

```bash
sudo systemctl stop meshcore-rpc-services meshcore-tak-bridge
sudo rm /var/lib/meshcore-rpc-services/state.sqlite3
# Clear retained MQTT state too:
sudo systemctl restart mosquitto
sudo systemctl start meshcore-rpc-services meshcore-tak-bridge
```

The DB will be recreated on startup; retained MQTT state will rebuild
as nodes report in.

---

## Reaching a remote TAK Server (WireGuard / cellular / Starlink)

If your TAK Server is at home and the Pi is in the field, the typical
path is: Pi → cellular hotspot or Starlink → public internet → WireGuard
tunnel → home network → TAK Server.

The bridge doesn't care that the route goes over a tunnel. As far as it
knows, the TAK Server is at some IP. Configure that IP in
`tak.server.host` and the bridge connects to it. The OS handles the
rest.

### Setting up WireGuard on the Pi

```bash
sudo apt install wireguard
```

Create `/etc/wireguard/wg0.conf` with your tunnel config:

```ini
[Interface]
PrivateKey = <pi's private key>
Address = 10.7.0.10/24
# Optional: route only TAK traffic through the tunnel, not all egress.
# Without this, the entire Pi sends DNS/NTP/etc. through wg0, which
# eats Starlink/cellular bandwidth.

[Peer]
PublicKey = <home wg server's public key>
Endpoint = <your home public IP or DDNS>:51820
# Only route the home subnet through the tunnel. The Pi reaches the
# TAK Server at 10.7.0.5 over wg0; everything else goes out the
# regular default route (Starlink dish).
AllowedIPs = 10.7.0.0/24
PersistentKeepalive = 25
```

Bring it up at boot:

```bash
sudo systemctl enable --now wg-quick@wg0
```

Verify the route:

```bash
ip route get 10.7.0.5     # should show "dev wg0"
ping -c 3 10.7.0.5
```

Then point the bridge at the WireGuard IP:

```yaml
# /etc/meshcore-rpc-services/config.yaml
tak:
  server:
    host: 10.7.0.5
    port: 8087
```

`sudo systemctl restart meshcore-tak-bridge` and watch the log:

```bash
journalctl -u meshcore-tak-bridge -f
```

You should see `TAK connecting to 10.7.0.5:8087` followed by `TAK connected`.

### What happens when the tunnel flaps

Starlink fades briefly several times an hour. Cellular drops cells when
the Pi moves between towers. Either way, expect the tunnel to go down
and come back up — sometimes for seconds, sometimes for minutes.

The bridge handles this:

1. The TCP connection to the TAK Server breaks (visible as
   `TAK connection ended: ...` in the log).
2. The bridge reconnects with exponential backoff (1s → 2s → 5s → 10s →
   30s, capped at 30s).
3. On successful reconnect, the bridge **drops any backlog** of CoT
   events that piled up during the outage and emits a fresh CoT for
   every entity in its current state.

Why drop the backlog? Because ATAK only cares about *current* position.
Replaying 5 minutes of slightly-old positions would cause markers to
jump around briefly until the next heartbeat caught up. Better to send
the present state as soon as we can talk to the server again.

The retained MQTT state on the Pi is unaffected — the gateway and RPC
service keep working through the outage; only the TAK side is
disconnected. When the bridge reconnects, it re-emits whatever was on
the retained topics at that moment.

### Choosing routing scope

Two valid choices for `AllowedIPs` on the Pi side:

- **`10.7.0.0/24`** (or whatever your home subnet is): only TAK traffic
  goes through the tunnel. Everything else (DNS, NTP, etc.) uses the
  default route. **Recommended.** This is the simpler, more debuggable
  setup, and it's correct for both Starlink and cellular.
- **`0.0.0.0/0`**: all egress traffic goes through the tunnel. The Pi
  is effectively on your home network. Don't do this unless you have
  a specific reason to. Two specific risks:
    - DNS for your WireGuard endpoint hostname (e.g. `home.example.com`)
      must resolve *before* wg0 comes up, otherwise you've created a
      chicken-and-egg failure on cold boot. Use a hardcoded `Endpoint`
      IP, or a separate name server outside the tunnel.
    - All your home's egress filtering applies to the Pi. If your home
      blocks something, the Pi can't reach it either.

### Bandwidth and CoT volume

CoT-over-TCP on this stack is small. Per-event size: ~400 bytes. With
default heartbeat of 10s and 5 nodes, that's:

```
6 entities × 1 event/10s × 400 bytes = 240 bytes/s ≈ 21 MB/day
```

Well under any cellular plan's overage threshold. Starlink is unlimited
for this kind of traffic.

If you want to cut it further, raise `tak.publish_interval_s` to 30 or
60 seconds. CoT staleness handles the rest — markers don't disappear
between heartbeats.

### Latency

WireGuard adds 1-2ms on a wired LAN. Over Starlink, expect 30-50ms; over
cellular, 50-150ms. None of this matters for ATAK markers, which update
on the order of seconds. It would matter for an inverse channel (TAK →
field commands) but the bridge is one-way so it's a non-issue.

### When to NOT use a tunnel

If you're operating without internet at all (deep field, no Starlink),
the TAK Server cannot be at home. Two options:

1. Run a TAK Server alongside the Pi (or on the Pi itself) and connect
   ATAK clients to it directly over Wi-Fi. Out of scope for this guide
   but doable.
2. Skip the bridge entirely and operate from the retained MQTT topics.
   `mosquitto_sub -t 'mc/node/+/state' -v` is a perfectly serviceable
   tactical display in a pinch.

A few things that matter when you're not in the lab:

**Boot time.** Cold boot to fully-up stack is ~30 seconds on a Pi 4.
mosquitto comes up first (~5s), the gateway and service start in
parallel (~10s each), the bridge waits for both (~20s total). If you
need it faster, the bottleneck is mosquitto's startup, not anything
in our code.

**Power loss recovery.** All three services are `Restart=always`. SQLite
is in WAL mode and writes are journaled, so `pkill -9` mid-write doesn't
corrupt anything you'd notice. The retained MQTT topics survive a broker
restart because mosquitto persists them to `/var/lib/mosquitto/`.

**Battery monitoring.** Add a watchdog by enabling
`WatchdogSec=60` in the service unit and having the service ping
systemd periodically. Not done here because the failure mode (process
hung but not crashed) hasn't shown up in practice. If it does, add it.

**Reading logs without a screen.** Plug in a USB serial cable to the
Pi's UART pins and run `journalctl -f` over that. Or from another
machine on the same network: `ssh meshcore-pi journalctl -u meshcore-mqtt -f`.

**Time sync.** With no internet, NTP can't sync. The Pi's clock will
drift. Without a fix:

- `time.now` responses get inaccurate
- `last_seen_age_s` calculations stay correct (they're relative)
- TAK CoT timestamps drift, which might confuse ATAK clients

Fix: add a hardware RTC (e.g. DS3231) and configure it as the system clock
source. Or set `service.base.source: gpsd` and have gpsd discipline the
clock with `chrony` configured to use gpsd as a refclock. The latter is
the right answer for off-grid; details are out of scope here.
