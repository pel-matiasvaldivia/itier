#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# iTier — crea en Proxmox la VM del appliance de un cliente (Debian 12 cloud
# image + cloud-init) y deja que el primer arranque corra el instalador.
# Ejecutar COMO ROOT en el HOST Proxmox:
#
#   bash crear-vm-proxmox.sh --url https://itierconsole.../enrolar/<TOKEN>/install.sh \
#        --nombre cuyopack-srl [--vmid 120] [--storage local-lvm] [--bridge vmbr0] \
#        [--disco 16G] [--ram 2048] [--cores 2] [--ssh-key ~/.ssh/id_ed25519.pub]
#
# La URL sale de la consola: Instaladores → «Generar comando de instalación»
# (es el enlace del comando, sin el «curl … | sudo bash»). Vence en 24 h.
# Requisitos: almacenamiento «local» con contenido «Snippets» habilitado
# (Datacenter → Storage → local → Content).
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

URL=""; NOMBRE=""; VMID=""; STORAGE="local-lvm"; SNIP="local"; BRIDGE="vmbr0"
DISCO="16G"; RAM="2048"; CORES="2"; SSHKEY=""
IMG_URL="https://cloud.debian.org/images/cloud/bookworm/latest/debian-12-genericcloud-amd64.qcow2"

while [ $# -gt 0 ]; do
  case "$1" in
    --url) URL="$2"; shift 2;;       --nombre) NOMBRE="$2"; shift 2;;
    --vmid) VMID="$2"; shift 2;;     --storage) STORAGE="$2"; shift 2;;
    --snippets) SNIP="$2"; shift 2;; --bridge) BRIDGE="$2"; shift 2;;
    --disco) DISCO="$2"; shift 2;;   --ram) RAM="$2"; shift 2;;
    --cores) CORES="$2"; shift 2;;   --ssh-key) SSHKEY="$2"; shift 2;;
    *) echo "Opción desconocida: $1" >&2; exit 2;;
  esac
done

command -v qm >/dev/null || { echo "Esto se corre en el host Proxmox (falta 'qm')." >&2; exit 1; }
[[ "$URL" =~ ^https://[A-Za-z0-9.-]+(:[0-9]+)?/enrolar/[A-Za-z0-9_-]+/install\.sh$ ]] \
  || { echo "--url inválida: tiene que ser https://<consola>/enrolar/<token>/install.sh" >&2; exit 2; }
[[ "$NOMBRE" =~ ^[a-z0-9-]+$ ]] || { echo "--nombre: solo minúsculas, números y guiones (el slug del cliente)." >&2; exit 2; }
[ -n "$VMID" ] || VMID="$(pvesh get /cluster/nextid)"
qm status "$VMID" >/dev/null 2>&1 && { echo "Ya existe la VM $VMID." >&2; exit 1; }

SNIPDIR="$(pvesm path "$SNIP:snippets/x" 2>/dev/null | sed 's#/x$##')"
[ -n "$SNIPDIR" ] && mkdir -p "$SNIPDIR" || { echo "El storage '$SNIP' no tiene Snippets habilitado." >&2; exit 1; }

IMG="/var/lib/vz/template/iso/debian-12-genericcloud-amd64.qcow2"
if [ ! -s "$IMG" ]; then
  echo "[iTier] Descargando Debian 12 cloud image…"
  mkdir -p "$(dirname "$IMG")"; curl -fL --retry 3 -o "$IMG" "$IMG_URL"
fi

# user-data de cloud-init: instala el agente de la VM y corre el instalador de iTier.
UD="$SNIPDIR/itier-$VMID-user.yml"
umask 077
{
  echo "#cloud-config"
  echo "hostname: itier-$NOMBRE"
  echo "package_update: true"
  echo "packages: [curl, ca-certificates, qemu-guest-agent]"
  if [ -n "$SSHKEY" ]; then
    echo "users:"; echo "  - name: soporte"; echo "    sudo: ALL=(ALL) NOPASSWD:ALL"
    echo "    shell: /bin/bash"; echo "    ssh_authorized_keys:"; echo "      - $(cat "$SSHKEY")"
  fi
  echo "runcmd:"
  echo "  - systemctl enable --now qemu-guest-agent"
  echo "  - [ bash, -c, \"curl -fsSL '$URL' | bash >> /var/log/itier-instalacion.log 2>&1\" ]"
} > "$UD"

echo "[iTier] Creando la VM $VMID (itier-$NOMBRE)…"
qm create "$VMID" --name "itier-$NOMBRE" --memory "$RAM" --cores "$CORES" --cpu host \
  --net0 "virtio,bridge=$BRIDGE" --scsihw virtio-scsi-pci --agent 1 --ostype l26 \
  --serial0 socket --vga serial0 --onboot 1
qm importdisk "$VMID" "$IMG" "$STORAGE" >/dev/null
qm set "$VMID" --scsi0 "$STORAGE:vm-$VMID-disk-0" --boot order=scsi0 \
  --ide2 "$STORAGE:cloudinit" --ipconfig0 ip=dhcp \
  --cicustom "user=$SNIP:snippets/itier-$VMID-user.yml" >/dev/null
qm resize "$VMID" scsi0 "$DISCO" >/dev/null
qm start "$VMID"

cat <<FIN

[iTier] VM $VMID creada y arrancando. El instalador corre solo en el primer boot
(tarda unos 5-10 min: descarga Docker e imágenes). Seguimiento:
  qm guest exec $VMID -- tail -n 30 /var/log/itier-instalacion.log
La consola de iTier marca "Instalado" en Instaladores cuando termina.
Por seguridad borrá el user-data (contiene el enlace de un solo uso):
  rm -f "$UD"
FIN
