#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# iTier — crea en un host KVM/libvirt la VM del appliance de un cliente
# (Debian 12 cloud image + cloud-init) y corre el instalador en el primer arranque.
# Ejecutar COMO ROOT en el host KVM:
#
#   bash crear-vm-kvm.sh --nombre cuyopack-srl \
#        --url https://<consola>/enrolar/<TOKEN>/install.sh \
#        [--bridge br0] [--disco 16] [--ram 2048] [--cores 2] [--ssh-key ~/.ssh/id_ed25519.pub]
#
# ⚠ La VM debe ir en un BRIDGE de la LAN del cliente (br0): los equipos Windows
#   se conectan al proxy por su IP LAN. Con la red NAT por defecto (virbr0) no llegan.
# Requisitos: libvirt + virt-install, qemu-img, y cloud-localds (cloud-image-utils)
#   o genisoimage. La URL sale de la consola (Instaladores → «Generar comando…»).
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

URL=""; NOMBRE=""; BRIDGE="br0"; DISCO="16"; RAM="2048"; CORES="2"; SSHKEY=""
DIR="${ITIER_VM_DIR:-/var/lib/libvirt/images}"
IMG_URL="https://cloud.debian.org/images/cloud/bookworm/latest/debian-12-genericcloud-amd64.qcow2"

while [ $# -gt 0 ]; do
  case "$1" in
    --url) URL="$2"; shift 2;;       --nombre) NOMBRE="$2"; shift 2;;
    --bridge) BRIDGE="$2"; shift 2;; --disco) DISCO="$2"; shift 2;;
    --ram) RAM="$2"; shift 2;;       --cores) CORES="$2"; shift 2;;
    --ssh-key) SSHKEY="$2"; shift 2;;
    *) echo "Opción desconocida: $1" >&2; exit 2;;
  esac
done

for c in virt-install virsh qemu-img curl; do
  command -v "$c" >/dev/null || { echo "Falta '$c' (Debian/Ubuntu: apt install qemu-kvm libvirt-daemon-system virtinst qemu-utils curl · CentOS/RHEL: yum install qemu-kvm libvirt virt-install qemu-img curl)." >&2; exit 1; }
done
[[ "$URL" =~ ^https://[A-Za-z0-9.-]+(:[0-9]+)?/enrolar/[A-Za-z0-9_-]+/install\.sh$ ]] \
  || { echo "--url inválida: tiene que ser https://<consola>/enrolar/<token>/install.sh" >&2; exit 2; }
[[ "$NOMBRE" =~ ^[a-z0-9-]+$ ]] || { echo "--nombre: solo minúsculas, números y guiones (el slug del cliente)." >&2; exit 2; }
ip link show "$BRIDGE" >/dev/null 2>&1 || { echo "No existe el bridge '$BRIDGE' en este host (ver --bridge)." >&2; exit 1; }
VM="itier-$NOMBRE"
virsh dominfo "$VM" >/dev/null 2>&1 && { echo "Ya existe la VM $VM." >&2; exit 1; }

BASE="$DIR/debian-12-genericcloud-amd64.qcow2"
if [ ! -s "$BASE" ]; then
  echo "[iTier] Descargando Debian 12 cloud image…"
  mkdir -p "$DIR"; curl -fL --retry 3 -o "$BASE" "$IMG_URL"
fi
DISK="$DIR/$VM.qcow2"; SEED="$DIR/$VM-seed.iso"
cp "$BASE" "$DISK"; qemu-img resize "$DISK" "${DISCO}G" >/dev/null

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
umask 077
{
  echo "#cloud-config"
  echo "hostname: $VM"
  echo "package_update: true"
  echo "packages: [curl, ca-certificates, qemu-guest-agent]"
  if [ -n "$SSHKEY" ]; then
    echo "users:"; echo "  - name: soporte"; echo "    sudo: ALL=(ALL) NOPASSWD:ALL"
    echo "    shell: /bin/bash"; echo "    ssh_authorized_keys:"; echo "      - $(cat "$SSHKEY")"
  fi
  echo "runcmd:"
  echo "  - systemctl enable --now qemu-guest-agent"
  echo "  - [ bash, -c, \"curl -fsSL '$URL' | bash >> /var/log/itier-instalacion.log 2>&1\" ]"
} > "$TMP/user-data"
printf 'instance-id: %s\nlocal-hostname: %s\n' "$VM" "$VM" > "$TMP/meta-data"
if command -v cloud-localds >/dev/null; then
  cloud-localds "$SEED" "$TMP/user-data" "$TMP/meta-data"
elif command -v genisoimage >/dev/null; then
  genisoimage -quiet -output "$SEED" -volid cidata -joliet -rock "$TMP/user-data" "$TMP/meta-data"
else
  echo "Falta cloud-localds (apt install cloud-image-utils) o genisoimage (yum/apt install genisoimage)." >&2; exit 1
fi

# Variante de SO: hosts viejos (CentOS 7) no conocen debian12 → se prueba una lista.
OSARG="--os-type linux"
if command -v osinfo-query >/dev/null; then
  for v in debian12 debian11 debian10 debian9 rhel7.0 linux2020 generic; do
    if osinfo-query os short-id="$v" 2>/dev/null | grep -q "$v"; then OSARG="--os-variant $v"; break; fi
  done
fi

echo "[iTier] Creando la VM $VM en el bridge $BRIDGE…"
virt-install --name "$VM" --memory "$RAM" --vcpus "$CORES" --cpu host-passthrough \
  --disk "path=$DISK,format=qcow2,bus=virtio" --disk "path=$SEED,device=cdrom" \
  --network "bridge=$BRIDGE,model=virtio" $OSARG --import \
  --graphics none --console pty,target_type=serial --channel unix,target_type=virtio,name=org.qemu.guest_agent.0 \
  --noautoconsole
virsh autostart "$VM" >/dev/null

cat <<FIN

[iTier] VM $VM creada y arrancando. El instalador corre solo en el primer boot
(unos 5-10 min: descarga Docker e imágenes). Seguimiento:
  virsh qemu-agent-command $VM '{"execute":"guest-exec","arguments":{"path":"/usr/bin/tail","arg":["-n","30","/var/log/itier-instalacion.log"],"capture-output":true}}'
  (o entrá por SSH con --ssh-key:  ssh soporte@<IP>  y  sudo tail -f /var/log/itier-instalacion.log)
IP de la VM:  virsh domifaddr $VM --source agent
La consola de iTier marca "Instalado" en Instaladores cuando termina.
Por seguridad borrá la imagen de arranque (contiene el enlace de un solo uso):
  virsh domblklist $VM    # (el CD-ROM es el de la ISO «-seed»)  →  virsh change-media $VM <destino> --eject --config; rm -f "$SEED"
FIN
