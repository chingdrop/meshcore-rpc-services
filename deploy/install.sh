#!/usr/bin/env bash
# Install the meshcore stack on a Raspberry Pi running Ubuntu Server 22.04+
# or Raspberry Pi OS bookworm+.
#
# Idempotent: re-running won't break anything. Safe to run after editing
# config files to pick up changes.
#
# What it does, in order:
#   1. Installs OS-level packages (mosquitto, python3-venv, gpsd if asked)
#   2. Creates the `meshcore` system user, in the dialout group
#   3. Installs both repos into /opt/<repo>/venv via pip
#   4. Drops config templates into /etc/<repo>/ if not already present
#   5. Installs systemd units and enables them
#   6. Installs the udev rule (with a reminder to edit the VID/PID)
#
# Run from the rpc-services repo root:
#     sudo bash deploy/install.sh /path/to/meshcore-mqtt
#
# Or specify the gateway repo location with --gateway-path.

set -euo pipefail

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

GATEWAY_REPO=""
RPC_REPO="$(cd "$(dirname "$0")/.." && pwd)"
INSTALL_GPSD="no"
USER_NAME="meshcore"
ENABLE_TAK="ask"

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

usage() {
    cat <<EOF
Usage: sudo bash $0 [options] [<gateway-repo-path>]

Options:
    --gateway-path PATH   Path to the meshcore-mqtt repo (also positional)
    --with-gpsd           Install and enable gpsd (Pi has its own GPS)
    --without-tak         Do not enable the TAK bridge unit at boot
    --user NAME           Service user account (default: meshcore)
    -h, --help            Show this and exit

Examples:
    sudo bash $0 ~/meshcore-mqtt
    sudo bash $0 --with-gpsd ~/repos/meshcore-mqtt
EOF
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --gateway-path) GATEWAY_REPO="$2"; shift 2 ;;
        --with-gpsd) INSTALL_GPSD="yes"; shift ;;
        --without-tak) ENABLE_TAK="no"; shift ;;
        --user) USER_NAME="$2"; shift 2 ;;
        -h|--help) usage ;;
        *) GATEWAY_REPO="$1"; shift ;;
    esac
done

if [[ -z "$GATEWAY_REPO" ]]; then
    echo "ERROR: gateway repo path is required."
    echo "Usage: sudo bash $0 /path/to/meshcore-mqtt" >&2
    exit 1
fi
if [[ ! -d "$GATEWAY_REPO" ]]; then
    echo "ERROR: gateway repo path does not exist: $GATEWAY_REPO" >&2
    exit 1
fi
if [[ $EUID -ne 0 ]]; then
    echo "ERROR: must run as root (use sudo)." >&2
    exit 1
fi

# Resolve to absolute paths.
GATEWAY_REPO=$(cd "$GATEWAY_REPO" && pwd)
RPC_REPO=$(cd "$RPC_REPO" && pwd)

echo
echo "========================================================================"
echo " meshcore stack installer"
echo "========================================================================"
echo "  Gateway repo:  $GATEWAY_REPO"
echo "  RPC repo:      $RPC_REPO"
echo "  Service user:  $USER_NAME"
echo "  GPSD install:  $INSTALL_GPSD"
echo "  Enable TAK:    $ENABLE_TAK"
echo "========================================================================"
echo

# ---------------------------------------------------------------------------
# 1. OS packages
# ---------------------------------------------------------------------------

echo "[1/6] Installing OS packages..."
apt-get update
APT_PKGS="mosquitto mosquitto-clients python3 python3-venv python3-pip"
if [[ "$INSTALL_GPSD" == "yes" ]]; then
    APT_PKGS="$APT_PKGS gpsd gpsd-clients"
fi
DEBIAN_FRONTEND=noninteractive apt-get install -y $APT_PKGS

systemctl enable --now mosquitto.service

# ---------------------------------------------------------------------------
# 2. Service user
# ---------------------------------------------------------------------------

echo "[2/6] Creating service user '$USER_NAME'..."
if ! id "$USER_NAME" >/dev/null 2>&1; then
    useradd --system --no-create-home --shell /usr/sbin/nologin "$USER_NAME"
fi
# dialout: required to open the gateway's USB serial device.
usermod -aG dialout "$USER_NAME"

# ---------------------------------------------------------------------------
# 3. Install both Python packages into /opt
# ---------------------------------------------------------------------------

install_into_venv() {
    local repo_path="$1"
    local venv_dir="$2"

    if [[ ! -d "$venv_dir" ]]; then
        python3 -m venv "$venv_dir"
    fi
    "$venv_dir/bin/pip" install --upgrade pip wheel >/dev/null
    "$venv_dir/bin/pip" install "$repo_path"
}

echo "[3/6] Installing meshcore-mqtt into /opt/meshcore-mqtt/venv..."
mkdir -p /opt/meshcore-mqtt
install_into_venv "$GATEWAY_REPO" /opt/meshcore-mqtt/venv

echo "      Installing meshcore-rpc-services into /opt/meshcore-rpc-services/venv..."
mkdir -p /opt/meshcore-rpc-services
install_into_venv "$RPC_REPO" /opt/meshcore-rpc-services/venv

# ---------------------------------------------------------------------------
# 4. Config templates
# ---------------------------------------------------------------------------

echo "[4/6] Installing config templates..."

# Gateway config
mkdir -p /etc/meshcore-mqtt
if [[ ! -f /etc/meshcore-mqtt/config.yaml ]]; then
    if [[ -f "$GATEWAY_REPO/config.example.yaml" ]]; then
        cp "$GATEWAY_REPO/config.example.yaml" /etc/meshcore-mqtt/config.yaml
        # Default the gateway to use the udev symlink, not a fragile ttyACM0.
        sed -i 's|connection_type: tcp|connection_type: serial|' /etc/meshcore-mqtt/config.yaml
        sed -i 's|address: ".*"|address: /dev/meshcore-gateway|' /etc/meshcore-mqtt/config.yaml
        echo "      Wrote /etc/meshcore-mqtt/config.yaml (edit before starting if needed)"
    else
        echo "      WARNING: $GATEWAY_REPO/config.example.yaml not found; create config.yaml manually"
    fi
else
    echo "      /etc/meshcore-mqtt/config.yaml exists; left untouched"
fi
chown -R root:"$USER_NAME" /etc/meshcore-mqtt
chmod 750 /etc/meshcore-mqtt
chmod 640 /etc/meshcore-mqtt/config.yaml 2>/dev/null || true

# RPC services / TAK bridge config (shared)
mkdir -p /etc/meshcore-rpc-services
if [[ ! -f /etc/meshcore-rpc-services/config.yaml ]]; then
    cp "$RPC_REPO/config.example.yaml" /etc/meshcore-rpc-services/config.yaml
    # Override db path to a system location.
    sed -i 's|db_path: ".*"|db_path: "/var/lib/meshcore-rpc-services/state.sqlite3"|' \
        /etc/meshcore-rpc-services/config.yaml
    echo "      Wrote /etc/meshcore-rpc-services/config.yaml (edit base.* and tak.* before starting)"
else
    echo "      /etc/meshcore-rpc-services/config.yaml exists; left untouched"
fi
chown -R root:"$USER_NAME" /etc/meshcore-rpc-services
chmod 750 /etc/meshcore-rpc-services
chmod 640 /etc/meshcore-rpc-services/config.yaml 2>/dev/null || true

# ---------------------------------------------------------------------------
# 5. systemd units
# ---------------------------------------------------------------------------

echo "[5/6] Installing systemd units..."

UNIT_DIR=/etc/systemd/system
DEPLOY_DIR="$RPC_REPO/deploy/systemd"

cp "$DEPLOY_DIR/meshcore-mqtt.service"          "$UNIT_DIR/"
cp "$DEPLOY_DIR/meshcore-rpc-services.service"  "$UNIT_DIR/"
cp "$DEPLOY_DIR/meshcore-tak-bridge.service"    "$UNIT_DIR/"

# If the unit files reference a different user than $USER_NAME, fix that.
if [[ "$USER_NAME" != "meshcore" ]]; then
    for unit in meshcore-mqtt meshcore-rpc-services meshcore-tak-bridge; do
        sed -i "s/^User=meshcore$/User=$USER_NAME/" "$UNIT_DIR/$unit.service"
        sed -i "s/^Group=meshcore$/Group=$USER_NAME/" "$UNIT_DIR/$unit.service"
    done
fi

systemctl daemon-reload
systemctl enable meshcore-mqtt.service
systemctl enable meshcore-rpc-services.service

case "$ENABLE_TAK" in
    yes)
        systemctl enable meshcore-tak-bridge.service ;;
    no)
        echo "      Skipping meshcore-tak-bridge.service (per --without-tak)" ;;
    ask)
        echo
        read -rp "Enable meshcore-tak-bridge.service at boot? [y/N] " yn
        if [[ "$yn" == "y" || "$yn" == "Y" ]]; then
            systemctl enable meshcore-tak-bridge.service
        else
            echo "      Skipped. Enable later with: sudo systemctl enable meshcore-tak-bridge.service"
        fi
        ;;
esac

# ---------------------------------------------------------------------------
# 6. udev rule for stable serial device
# ---------------------------------------------------------------------------

echo "[6/6] Installing udev rule for the gateway's serial device..."
cp "$RPC_REPO/deploy/udev/99-meshcore-gateway.rules" /etc/udev/rules.d/
udevadm control --reload-rules
udevadm trigger

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------

cat <<EOF

========================================================================
 Installation complete.
========================================================================

Next steps:

  1. Find your RAK4631's USB VID/PID:

         lsusb

     Look for a Nordic / Adafruit / RAK device. Note the ID shown
     after "ID" (e.g. 1915:520f).

  2. Edit /etc/udev/rules.d/99-meshcore-gateway.rules:
     replace the placeholder VID/PID with what lsusb showed,
     then run:

         sudo udevadm control --reload-rules
         sudo udevadm trigger
         ls -l /dev/meshcore-gateway

     You should see a symlink. If not, the rule isn't matching.
     Run \`udevadm info -a /dev/ttyACM0\` to inspect the actual
     attributes the kernel sees.

  3. Edit configs:
         /etc/meshcore-mqtt/config.yaml          (gateway)
         /etc/meshcore-rpc-services/config.yaml  (service + bridge)

     In the RPC services config:
       - service.base.source / static_lat / static_lon
         (or set source: gpsd if you ran with --with-gpsd)
       - tak.server.host  — your TAK Server's LAN IP
       - tak.server.port  — usually 8087

  4. Start the stack:

         sudo systemctl start meshcore-mqtt
         sudo systemctl start meshcore-rpc-services
         sudo systemctl start meshcore-tak-bridge   # if enabled

  5. Verify:

         systemctl status meshcore-mqtt meshcore-rpc-services meshcore-tak-bridge
         journalctl -u meshcore-mqtt -f          # follow gateway logs
         mosquitto_sub -t '#' -v                 # see all MQTT traffic

EOF
