#!/usr/bin/env bash
# Local, isolated release-qualification VM lifecycle. Never targets production.
set -Eeuo pipefail
VM_NAME=asr-qa-disposable
BASE_NAME=asr-qa-asl3-template
LIBVIRT_URI=qemu:///system
QA_DIR="${ASR_QA_DIR:-$HOME/vms/asr-qa}"
DISK="$QA_DIR/${VM_NAME}.qcow2"
BASE_DISK="$QA_DIR/${BASE_NAME}.qcow2"
REPORT_DIR="$QA_DIR/reports"
usage() { echo 'Usage: asr-vm-cycle.sh {doctor|create|status|destroy}'; }
fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
vm_exists() { virsh -c "$LIBVIRT_URI" dominfo "$VM_NAME" >/dev/null 2>&1; }
base_exists() { virsh -c "$LIBVIRT_URI" dominfo "$BASE_NAME" >/dev/null 2>&1; }
require_tools() { for bin in virsh qemu-img virt-install; do command -v "$bin" >/dev/null || fail "Missing $bin"; done; }
doctor() {
  require_tools
  [ -r /dev/kvm ] && [ -w /dev/kvm ] || fail 'KVM acceleration inaccessible'
  virsh -c "$LIBVIRT_URI" list --all >/dev/null || fail 'Cannot access system libvirt'
  [[ "$(virsh -c "$LIBVIRT_URI" net-info default)" == *'Active:         yes'* ]] || fail 'Libvirt default NAT network is not active'
  echo "KVM and libvirt: ready"
  if base_exists && [ -f "$BASE_DISK" ] && [ "$(virsh -c "$LIBVIRT_URI" domstate "$BASE_NAME" | tr -d "\r")" = "shut off" ]; then
    echo "ASL3 base template: ready ($BASE_NAME)"
  else
    echo "ASL3 base template: NOT CREATED (provision a clean, powered-off ASL3 guest first)"
  fi
  if vm_exists; then echo "Disposable test VM: present"; else echo "Disposable test VM: absent"; fi
}
create() {
  require_tools
  base_exists || fail "Missing clean template VM: $BASE_NAME"
  [ -f "$BASE_DISK" ] || fail "Missing template disk: $BASE_DISK"
  [ -L "$BASE_DISK" ] && fail "Template disk cannot be a symlink"
  qemu-img info --output=json "$BASE_DISK" | python3 -c 'import json,sys; i=json.load(sys.stdin); assert i.get("format")=="qcow2" and not i.get("backing-filename"), "Template must be standalone qcow2"'  || fail "Template disk invalid"
  # QEMU runs as libvirt-qemu. The dedicated QA directory must grant
  # effective traverse/write access via ACL (mode bits alone are insufficient).
  [ -d "$QA_DIR" ] || fail "Missing dedicated VM storage directory: $QA_DIR"
  getfacl -cp "$QA_DIR" | grep -Eq '^user:libvirt-qemu:rwx$' || fail 'QA storage lacks libvirt-qemu ACL'
  ! vm_exists || fail "Disposable VM already exists; destroy it explicitly before recreating"
  [ "$(virsh -c "$LIBVIRT_URI" domstate "$BASE_NAME" | tr -d '\r')" = 'shut off' ] || fail 'Template must be powered off'
  mkdir -p "$QA_DIR" "$REPORT_DIR"
  [ ! -e "$DISK" ] || fail "Refusing to overwrite existing disk: $DISK"
  # qcow2 backing file stays immutable; disposable overlay is removed on destroy.
  qemu-img create -f qcow2 -F qcow2 -b "$BASE_DISK" "$DISK"
  setfacl -m u:libvirt-qemu:rw "$DISK"
  if ! virt-install --connect "$LIBVIRT_URI" --name "$VM_NAME" \
      --memory 2048 --vcpus 2 --import --disk "path=$DISK,format=qcow2,bus=virtio" \
      --network network=default --graphics none --noautoconsole --os-variant debian12 \
      --boot hd --print-xml > "$QA_DIR/$VM_NAME.xml"; then
    rm -f -- "$DISK" "$QA_DIR/$VM_NAME.xml"
    fail 'Unable to prepare disposable guest XML'
  fi
  if ! virsh -c "$LIBVIRT_URI" define "$QA_DIR/$VM_NAME.xml"; then
    rm -f -- "$DISK" "$QA_DIR/$VM_NAME.xml"
    fail 'Unable to define disposable guest'
  fi
  virsh -c "$LIBVIRT_URI" start "$VM_NAME"
  echo "Created $VM_NAME; inspect guest readiness before running tests."
}
status() {
  require_tools
  if vm_exists; then virsh -c "$LIBVIRT_URI" dominfo "$VM_NAME"; else echo 'No disposable VM'; fi
}
destroy() {
  require_tools
  vm_exists || fail 'Disposable VM does not exist; refusing ambiguous cleanup'
  # Explicit name, disk, and path; never delete template or other guests.
  if [ "$(virsh -c "$LIBVIRT_URI" domstate "$VM_NAME" | tr -d '\r')" != 'shut off' ]; then
    virsh -c "$LIBVIRT_URI" destroy "$VM_NAME"
  fi
  virsh -c "$LIBVIRT_URI" undefine "$VM_NAME"
  [ ! -e "$DISK" ] || rm -- "$DISK"
  rm -f -- "$QA_DIR/$VM_NAME.xml"
  echo "Destroyed disposable VM. Reports retained under $REPORT_DIR"
}
case "${1:-}" in
  doctor) doctor;; create) create;; status) status;; destroy) destroy;; *) usage; exit 2;;
esac
