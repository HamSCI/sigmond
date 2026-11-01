# A radiod-only station

> **Audience:** operator (and the fleet admin who builds the machine)
> **Status:** current
> **Verified against:** sigmond branch `radiod-only-profile` on 2026-11-01. The steps follow what AI6VN-PM VM 101 needed by hand that day; the documented path has not yet been run end to end from a fresh VM
> **Canonical for:** building and attaching a radiod-only station: a receiver host that runs radiod and nothing else, decoded by another station on the same LAN

A *radiod-only* station runs the radio and nothing else. It owns one or more
RX888s, turns each into a set of channels, and publishes them on the local
network. Another station on the same LAN, the *decoder host*, records,
decodes and uploads them, and serves their ka9q-web pages. The radiod-only
station uploads nothing and shows no web pages of its own.

Unfamiliar words are in the [glossary](glossary.md). Commands tagged `[host]`
run on the Proxmox host, `[VM]` inside a VM, and `[radiod]` / `[decoder]` on
the radiod-only machine and the decoder host. `smd` asks for your password
when it needs one; never type `sudo smd`.

## When to choose radiod-only

Choose it when:

- one machine cannot decode every card you have. Put the extra RX888s on a
  second, cheaper box and let the decoder host do the work. (At KFS: one
  Beelink decodes, a second Beelink only runs radiod.)
- the antenna is far from the decoder. Put a small box near the antenna and
  carry the channels over the LAN.
- you want a second receiver on a Proxmox host that already has a decoder VM.
  A radiod-only VM beside it is enough (AI6VN-PM, VM 101 beside VM 100).

Do not choose it when the two machines are on different LAN segments or
across a router. The channels are multicast with a TTL of 1, so they never
cross a router. Do not choose it when the network is Wi-Fi either. See
[networking.md](../networking.md).

## Bare metal

On a plain Debian machine with the RX888 plugged in:

```bash
[radiod] git clone https://github.com/HamSCI/sigmond
[radiod] ./sigmond/install.sh
[radiod] smd bringup radiod-only
```

`smd bringup radiod-only` refuses to start while any decoder (wspr-recorder,
psk-recorder and the rest) is enabled on the machine. It names each one;
disable them with the `smd disable <name>` lines it prints, or install on a
clean machine. It then records the role (`/etc/sigmond/station-role` reads
`radiod-only`), installs and starts radiod only, with `ttl = 1` so the
channels reach the LAN, and ends with a **radiod published on the LAN**
checkpoint. No card on the bus: the bring-up ends PARTIAL and finishes once
the card appears.

Check it:

```bash
[radiod] smd check
```

You should see one line per radiod (`radiod <id>`), and no upload lines,
because a radiod-only station uploads nothing.

### The sample rate: 64.8 Msps when every channel is below 32 MHz

The default is 129.6 Msps, which covers up to about 64 MHz. If every channel
you want is below 32 MHz (all of HF is), run at **64.8 Msps** instead. It
halves radiod's FFT load. On AI6VN-PM two radiods at 129.6 Msps saturated their
FFT cores; at 64.8 Msps one ran at about 25 %.

```bash
[radiod] smd config init radiod <id> --samprate 64800000 --ttl 1 --reconfig
[radiod] smd restart all
```

`<id>` is the radiod's name as `smd check` prints it. Keep `--ttl 1`. With 0
the channels never leave the machine.

## A VM on Proxmox

Build the radiod-only VM **from the Debian 13 cloud image**, as a new VM.

> ⛔ **Never `qm clone` the decoder VM.** Its disk is a 248 GB thin volume.
> On AI6VN-PM the clone filled the storage pool and Proxmox paused the
> running decoder VM (VM 100) until space was freed. A radiod-only VM needs
> a small disk of its own.

1. **Size it: at least two cores and 4 GB of memory.** With one core, radiod
   and the system share the same CPU. Sigmond will not isolate radiod there,
   because that would fence off every CPU, and it warns you. Building
   ka9q-radio needs more than 4 GB, so give the VM a swap file for the build (AI6VN-PM VM 101: 6 GB plus a 4 GB swap file).
2. **Give it the radio.** Pass the RX888's USB controller through to this VM
   (`hostpci`, as for the decoder VM). The controller can belong to only one
   VM.
3. **Replace the kernel before anything else.** The cloud image's kernel
   (`linux-image-cloud-amd64`) has no USB host drivers, so the RX888 never
   appears in `lsusb`. Install the standard kernel and boot into it first:

   ```bash
   [VM] apt install linux-image-amd64
   [VM] reboot
   [VM] uname -r
   ```

   `uname -r` must now end in `-amd64`, **not** `-cloud-amd64`. Only then
   remove the cloud kernel. Removing the kernel you are running makes apt
   stop and ask whether to abort:

   ```bash
   [VM] apt purge '~nlinux-image-.*cloud'
   ```

   (`~n` is an apt pattern, a regular expression on the package name; a shell
   glob such as `'linux-image-*cloud*'` is refused by apt 2.x.) If `uname -r`
   still shows `-cloud-amd64` after the reboot, the boot loader chose the
   cloud kernel: do not purge. Ask your fleet admin to choose the standard
   kernel in the boot menu. No second reboot is needed once you run the
   standard kernel. `lsusb` must show the RX888 (`04b4:00f1`, or `04b4:00f3`
   before its firmware is loaded).
4. **Then follow the bare-metal steps above** inside the VM: `install.sh`,
   `smd bringup radiod-only`, and the 64.8 Msps change if it applies.

### Its network on a Proxmox appliance host

On a host installed from the appliance image, the decoder VM sits on the
host-only bridge `vmbr1`: the host is `10.99.0.1`, the decoder VM
`10.99.0.2`. Put the radiod-only VM on the same bridge at **`10.99.0.3/29`**,
gateway `10.99.0.1`, DNS `10.99.0.1`. The two VMs share that bridge, so the
radiod's multicast reaches the decoder VM without touching the site LAN.

The VM at `.3` can be reached only from the host: the host forwards no ports
to it (the ssh and web relays all lead to the decoder VM at `.2`). To log in,
`ssh root@<host address>` first, then `ssh <user>@10.99.0.3` from there. It
needs nothing more, because it serves no pages; its ka9q-web page is on the
decoder host.

- Hosts installed since 2026-11 already have `vmbr1` as a `/29`. A host
  installed earlier has a `/30`, which holds only the host and one VM. Ask
  your fleet admin to change its `address` in `/etc/network/interfaces` to
  `10.99.0.1/29`, to change `MGMT_NET`'s `/30` to `/29` in the masquerade
  lines, and to change the decoder VM's own address to `10.99.0.2/29`.
  Sigmond on the host reads either size. Such a host also lacks
  `bridge-mcsnoop 0` on `vmbr1` (see [the network between the
  two](#the-network-between-the-two)).
- **IPv6-only sites.** The VMs reach the internet through the host's IPv4
  translator (`clat`), which has a smaller MTU. Without a clamp, `apt` in a
  new VM hangs on the first large download. Hosts installed since 2026-11
  clamp TCP MSS out `clat` for every VM. On an older host, lower the VM's MTU
  for the install (`ip link set dev <nic> mtu 1280`) or ask your fleet admin
  to add the clamp:
  `iptables -t mangle -A FORWARD -o clat -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu`.

### CPU layout on a Proxmox host that is radiod-only

By default the CPU layout keeps one physical core (one hyperthread pair) for
the Proxmox host alone ([CLAUDE.md, "CPU pinning & the Proxmox
host"](../../CLAUDE.md#cpu-pinning--the-proxmox-host)). A host whose only job
is a radiod-only VM can give that core to the VM instead, which leaves room
for one more radiod. `HOST_RESERVE_PAIRS` sets how many cores the host
keeps. The layout takes it from, in order:

1. `HOST_RESERVE_PAIRS=<n>` in the environment of the command that computes
   the layout,
2. the value saved in `/etc/sigmond/host-layout.env`,
3. `0` when the host records the radiod-only role
   (`/etc/sigmond-appliance/station-role`, written by the setup wizard),
4. otherwise `1`.

⚠ **Re-run the layout after the wizard has recorded radiod-only.** The layout
is first computed before the wizard asks the question, so it was computed
with the default of `1`. On the host, after the wizard has finished:

```bash
[host] bash /root/sigmond-appliance/sigmond/scripts/proxmox/host-setup.sh --skip-rac
```

(On a host not installed from the appliance image, run the same
`scripts/proxmox/host-setup.sh --skip-rac` from your sigmond checkout.) It
reads the role, prints `Keeping 0 core(s) for the host alone, from the
radiod-only station role`, writes the new layout, with
`HOST_RESERVE_PAIRS=0`, to `/etc/sigmond/host-layout.env` (so later runs
keep it), and rewrites the kernel's `isolcpus`. Answer `N` to its reboot for
now.

It does **not** re-bind the VM: its pinning hookscript, its CPU affinity and
its core count still hold the old layout. No supported command does that yet.
`bootstrap.sh` is not it: it does nothing on a VM it already set up, and it
starts a whole install on an appliance VM. Until a supported re-bind
exists, your fleet admin does it by hand on the host. These are the steps
used on AI6VN-PM on 2026-11-01, with the values taken from the layout
host-setup.sh just wrote. They are taken from `host-apply.sh`, the script
that normally does them.

```bash
# [host] -- manual until a supported re-bind exists
VMID=<vmid>                                  # the radiod-only VM
SIG=/root/sigmond-appliance/sigmond          # the sigmond checkout on this host
SNIP=/var/lib/vz/snippets/cpu-pin-$VMID.sh
. /etc/sigmond/host-layout.env               # RADIOD_CPUS WORKER_CPUS VCPU_TO_PCPU VM_*
NCPU=$(nproc)
AFF=$(printf '%s\n' $VCPU_TO_PCPU | sort -n | paste -sd, -)        # the VM's pCPUs
HI=$(printf '%s\n' $VCPU_TO_PCPU | sort -n | tail -n 1)
HK=$(seq -s' ' $((HI + 1)) $((NCPU - 1)))   # host-only CPUs; empty when it kept none
RF=$(sed -n 's/^RADIOD_FREQ_KHZ=//p' "$SNIP"); RF=${RF:-3200000}     # keep this host's clocks
WF=$(sed -n 's/^WORKER_FREQ_KHZ=//p' "$SNIP"); WF=${WF:-1400000}
HF=$(sed -n 's/^HOUSEKEEPING_FREQ_KHZ=//p' "$SNIP"); HF=${HF:-$WF}

# 1. the hookscript: per-vCPU pinning and per-pCPU clocks
sed -e "s|@@VMID@@|$VMID|g" \
    -e "s|@@RADIOD_CPUS@@|$RADIOD_CPUS|g" \
    -e "s|@@WORKER_CPUS@@|$WORKER_CPUS|g" \
    -e "s|@@VCPU_TO_PCPU@@|$VCPU_TO_PCPU|g" \
    -e "s|@@RADIOD_FREQ_KHZ@@|$RF|g" \
    -e "s|@@WORKER_FREQ_KHZ@@|$WF|g" \
    -e "s|@@HOUSEKEEPING_CPUS@@|$HK|g" \
    -e "s|@@HOUSEKEEPING_FREQ_KHZ@@|$HF|g" \
    "$SIG/scripts/proxmox/cpu-pin-VMID.sh.template" > "$SNIP"
chmod +x "$SNIP"

# 2. the VM's affinity and core count
qm set $VMID --hookscript "local:snippets/cpu-pin-$VMID.sh"
qm set $VMID --affinity "$AFF"
qm set $VMID --cores $VM_CORES --sockets 1
qm set $VMID --args "-smp ${VM_VCPU_COUNT},sockets=1,cores=${VM_CORES},threads=${VM_THREADS},maxcpus=${VM_VCPU_COUNT} -cpu host,host-cache-info=on,topoext=on,+kvm_pv_eoi,+kvm_pv_unhalt,-svm"

# 3. radiod's L3 slice (no-op on a CPU without cat_l3), then IRQs and QEMU's
#    other threads: onto the host-only CPUs, or the VM's worker pCPUs if none
RP=$(printf '%s\n' $RADIOD_CPUS | sort -n | paste -sd, -)
OP=$(seq 0 $((NCPU - 1)) | grep -vxF -f <(printf '%s\n' $RADIOD_CPUS) | paste -sd, -)
HERD=$(printf '%s\n' ${HK:-$WORKER_CPUS} | sort -n | paste -sd, -)
U=/etc/systemd/system
sed -i "s|^\(ExecStart=.*sigmond-host-resctrl\.sh\) [^ ]* [^ ]*|\1 $RP $OP|" $U/sigmond-host-resctrl.service
sed -i "s|^\(ExecStart=.*sigmond-host-irq-affinity\.sh\) .*|\1 $HERD|" $U/sigmond-host-irq-affinity.service
sed -i "s|^\(ExecStart=.*radiod-vm-fence\.sh $VMID\) [^ ]*|\1 $HERD|" $U/radiod-vm-fence.service
systemctl daemon-reload
systemctl restart sigmond-host-resctrl sigmond-host-irq-affinity radiod-vm-fence

# 4. pin the running VM now, then restart it for its new core count
bash "$SNIP" $VMID post-start
qm shutdown $VMID && qm start $VMID
```

If host-setup.sh said the host needs a reboot (it does whenever `isolcpus`
changed), reboot the host instead of step 4's `qm shutdown`/`qm start`. The
VM comes back on the new layout. Check with `qm config $VMID | grep -E
'affinity|cores|hookscript'` and `taskset -pc` on its `CPU N/KVM` threads.
Do not change the VM's CPU count in the Proxmox GUI
([do-not-touch.md](do-not-touch.md)).

To force a value instead, put it in front of the command, for example
`HOST_RESERVE_PAIRS=1 bash …/host-setup.sh --skip-rac` keeps a core for
the host even on a radiod-only host.

A host that runs a decoder VM *and* a radiod-only VM is not radiod-only.
Leave its reserve at `1`.

## The network between the two

The radiod's channels are IP multicast. The switch (or, between two VMs, the
host's bridge) must forward them. A switch with IGMP snooping and **no IGMP
querier** forwards them for about four minutes and then stops. The decoder
goes quiet with no error. The cure is a querier on the LAN or snooping off.
See [networking.md, `lan-needs-querier`](../networking.md#lan-needs-querier),
and run `smd admin diag net` on both machines.

A Linux bridge on a Proxmox host snoops too. On AI6VN-PM the two VMs on
`vmbr1` heard each other only after snooping was turned off on that bridge.
Appliance hosts installed since 2026-11 have `bridge-mcsnoop 0` in their
`vmbr1` stanza. On a host built before that, ask your fleet admin to add
that line to the `vmbr1` stanza in `/etc/network/interfaces` and run
`ifreload -a`.

## Attaching it to the decoder host

On the decoder host, the radiod-only station shows up by itself:

```bash
[decoder] smd admin sources list
```

Its radiod appears under *Discovered sources* with **WHERE** set to the
radiod-only machine's hostname (a radiod on the decoder host itself says
`local`). If it is missing, the multicast is not arriving: see the section
above.

Then:

1. **Declare it in coordination** with the host it runs on. Your fleet admin
   adds a block to the decoder host's `/etc/sigmond/coordination.toml`,
   re-renders the env (`smd config refresh`), and runs `smd apply`:

   ```toml
   [radiod."<status name>"]
   host = "<radiod-only hostname>"
   ```

   `<status name>` is the KEY in `smd admin sources list` without its
   `radiod:` prefix (e.g. `se-status.local`); `host` is its WHERE. A radiod
   with a `host` other than `localhost` is remote. `smd apply` then removes
   the decoder host's `239.0.0.0/8` route on `lo` (it prints
   `coordination declares remote radiod ... removing the 239/8→lo route`).
   That route would otherwise swallow the remote channels. `ip route show
   239.0.0.0/8` on the decoder host must not say `dev lo`. The decoder host's
   ka9q-web also serves a page for the remote radiod, after its local ones.
2. **Point each decoder instance at it:**

   ```bash
   [decoder] smd admin sources add <client>@<reporter> radiod:<name>
   [decoder] smd admin sources apply
   ```

   For example `smd admin sources add wspr-recorder@AI6VN-SE
   radiod:se-status.local`. `sources apply` writes each instance's config
   and prints the restart it needs. Run that restart.

On the decoder host, `smd check` should then show the instances decoding. On the radiod-only machine,
`smd check` still shows only its radiod lines.
