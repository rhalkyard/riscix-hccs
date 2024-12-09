# Rough guide to bootstrapping RISC iX onto HCCS IDE cards

Matt Evans has done some fantastic work
[adding IDE support to RISC iX](https://github.com/evansm7/riscix_ide), and some time ago, I 
[contributed support](https://github.com/evansm7/riscix_ide/commit/a5ad025e43a764ff2465372769a4dd3176bc1e5a) 
for the [HCCS 8-bit IDE card](https://chrisacorns.computinghistory.org.uk/32bit_UpgradesH2Z/HCCS_IDE_A3000.html) 
that I have in my A3000. The process of suitably partitioning a drive and
bootstrapping a RISC iX installation onto it is quite an ordeal, so I thought I
ought to document it.

Aside from the HCCS-specific partitioning aspect, it should also give some
more general hints guide to bootstrapping a “real hardware” RISC iX
installation.

You will need: 

- An A3000 with 4MB RAM and an HCCS IDE card (if you just want to try RISC iX
  in emulation, this is NOT how you want to go about it, trust me).

- A modern-ish hard disc or flash drive in your A3000 - various aspects of
  this process assume LBA-style virtual drive geometry (16 heads, 63 sectors
  per track) and will need to be adjusted for physical CHS geometry.

- Willingness and ability to build Arculator from source.

- `hccspart.py` from this repo.

- `!Zap` and HCCS `!IDEMgr` RISC OS applications in your Arculator HostFS.

- A USB to IDE adaptor.

- The RISCiX image from https://www.4corn.co.uk/articles/riscix121c/.

- An NFS server, with the following on it:

    - `RISCiX.tar.gz` from https://archive.org/details/riscix, gunzipped
    (but NOT untarred) to `RISCiX.tar`.

    - The RISC iX IDE driver source from https://github.com/evansm7/riscix_ide.

- Quite a lot of patience.

## Building Arculator

For various reasons, the current released version of Arculator will not work
for bootstrapping RISC iX. The Arculator code in the 'rhalkyard'
branch at https://github.com/rhalkyard/arculator has the following
modifications to make it possible:

- Adds emulation for the HCCS 8-bit IDE podule.

- Removes the 1024-cylinder size limit on disc images.

- Replaces the built-in SLIRP implementation with libslirp - the built-in
  version was old, and RISC iX's TCP/IP implementation could crash it.

- Includes RISC OS and podule ROMs in the source tree (this is mostly just a
  quality of life improvement to make setup easier.)

To build:

```bash
git clone --branch rhalkyard https://github.com/rhalkyard/arculator
cd arculator
autoreconf -vif
./configure
make
```

## Building a copy of RISC iX with IDE drivers

Configure Arculator for a RISC iX build machine. An A540 is well-suited to this:

- Machine: A540

- Memory: 16MB

- Monitor: Multisync

- Podule 0: Acorn AKA31 SCSI Podule

    - SCSI ID 0: the RISC iX image from 4corn

- Podule 1: Acorn Ethernet III Podule (AEH54)

    - Network type: SLIRP

Boot the A540, hit F12 and run the following commands to configure it to boot
from SCSI:

```
Configure MonitorType 1
Configure SCSIFSDiscs 1
```

Setting `MonitorType` is important, as if it disagrees with your Arculator
monitor type, the RISC iX console may not appear.

Reboot, then load the `!RISCiX` application and click its Icon Bar icon. Do
NOT click 'OK' in the dialogue box, but instead middle-click to bring up the
maintenance menu, choose "Device Defaults", and select the following:

> Device: `sd` <br/>
> Major: 0 <br/>
> Unit: 0 <br/>
> Partition: 0

Open the maintenance menu again and choose "Single User." After a few
moments, the RISC OS desktop will go away and RISC iX will dump you into a
single-user-mode shell.

Edit the following configuration files:

`/etc/rc.net`:

- `BROADCAST='broadcast 10.0.2.255'`

- `ETHERNET=ea0`

- Add `/sbin/route add net 0 10.0.2.2 1` to its own line at the end of the
  file.

`/etc/rc.config`:

- `NIS=FALSE` (if you don't do this, your system will hang forever at boot
  trying to find an NIS server!)

`/etc/hosts`:

- Change the IP address for host `riscix` to 10.0.2.15

- Add an entry for your NFS server, since RISC iX does not support DNS out of
  the box, and NFS seems to want a hostname, not an IP address.

Use Control-D to exit single-user mode and resume booting. Log in as `root`
(password is `Tal540bo`), and run:

```sh
# Mount your NFS share
mount -t nfs <nfs-server>:share /mnt

# Extract /usr/src from RISCiX.tar. This will take about half an hour, and won't
# produce any output for quite a while (since /usr/src is basically at the end
# of the tape).
cd /
tar xvf /mnt/RISCiX.tar ./usr/src

# Copy kernel headers into place
cp -r /usr/src/sys/include/* /usr/include

# Copy the IDE driver into the kernel source tree
mkdir /usr/src/sys/dev/ecide
cp -r /mnt/riscix_ide/* /usr/src/sys/dev/ecide

# Create a new build configuration
mkdir /usr/src/sys/M 
cd /usr/src/sys/M 
cp ../SYSTEM/KERNCOMP* ../SYSTEM/*.c ../SYSTEM/*.h ../SYSTEM/Makefile . 
ln -s ../SHARED . 
cd /usr/src/sys/conf 
cp SYSTEMdevconf.h Mdevconf.h 
cp SYSTEMlinkopts Mlinkopts 
cp SYSTEMsqueezecmd Msqueezecmd 

# Patch the kernel source to recognise IDE podules and build the IDE driver
cd /usr/src/sys 
patch -p0 < dev/ecide/rix_kern_build.patch 

# Build the new kernel and install it
cd /usr/src/sys/M
make clean
make 
make install 

# Shut down
reboot -RISCOS
```

Boot into RISC iX again (this time just click 'OK' to go straight into
multi-user mode), then shut it down and close Arculator.

## Partitioning an HCCS IDE drive for RISC iX

Configure Arculator for an A3000:

- Machine: A3000

- Memory: 4MB

- Monitor: Multisync

- Internal expansion: HCCS 8-bit IDE Controller

    - Drive 4: New image, 1038 cylinders, 16 heads, 63 sectors (511MB)

- Podule 0: Acorn AKA31 SCSI Podule

    - SCSI ID 0: the same RISC iX image you used in the build machine

Boot the A3000, hit F12, and run the following commands to configure it to
boot from SCSI and detect IDE discs:

```
Configure MonitorType 1
Configure SCSIFSDiscs 1
Configure IDEFSDiscs 1
Configure IDEFSDelay 8
```

Reboot, and load `!IDEMgr`. Middle-click its icon in the icon bar, and choose
`Partition...`. Select your disc, then click `Guess shape` and make sure that
it matches the disc geometry you set up in Arculator. It probably won't - set
it to 16 heads, and 63 sectors per track. If this isn't correct, the RISC iX
partition table will land in the wrong place.

Create TWO partitions. The first will be for RISC OS, the second is a dummy
partition to reserve space for RISC iX. Due to limitations in the RISC iX boot
process and how HCCS cards handle partitions, you can only have a single RISC OS
partition when using RISC iX, and both the RISC OS partition and the RISC iX
root filesystem must fit within the FileCore 512MB limit (swap and additional
partitions are not subject to this limit).

RISC iX is very space-hungry for an old OS, I would suggest creating a 256MB
RISC OS partition, leaving at least 256MB for the RISC iX root.

Shut down RISC OS and close Arculator, then run the `hccspart.py` script on your
IDE disc image to generate a RISC iX partition table. For example:

```bash
./hccspart.py riscix-ide.hdf
```

With no arguments, it will generate a 20MB swap partition, and as large a root
partition as will fit in the space available. Swap and root sizes can be
specified manually on the command line, as well as additional partitions to
create. Run with `--help` for more information.

The root partition will always be partition 0 and the swap partition will
always be partition 1. Subsequent partitions will be created in the order
they appear on the command line.

The resulting partition table is checked for potential issues and presented
for review before writing.

## Cloning RISC iX onto your new IDE partition

Boot the A3000 again, open the `!RISCiX` application, configure it as you did
on the build machine (device=`sd`, major, minor and partition=0). Boot into
single-user mode, same as last time.

On the console, you should see some a message like this from `ecide`
reporting the partitions it has detected and their size (in 512 byte blocks).

```
ecide0:0 Found RISCiX partition table at cyl 519 (abs sector 523152)
ecide0:0 partitions:
  0:  524160-1080000 (size 483840)
  1:  1080000-1048320 (size 40320)
```

If not, then something has gone wrong, possibly with your disc geometry.
Partition 0 is your root partition, partition 1 is swap, any subsequent
partitions are up to you. Make a note of their sizes (which are in 512-byte
blocks, in classic Unix style), as you will need to specify it to `mkfs`. You
can recall the messages with `dmesg` if necessary.

Run the following commands to create IDE device nodes, format your root
partition, and clone the RISC iX installation from your SCSI drive onto it.

```sh
# Create block devices
mknod /dev/id0a b 40 0  # root
mknod /dev/id0b b 40 1  # swap
# mknod /dev/id0c b 40 2 # another partition
# mknod /dev/id0d b 40 3 # yet another partition

# Create fake raw devices for each of the above partitions. ecide does not 
# support raw access, but things like fsck get upset if they don't exist.
ln -s /dev/id0a /dev/rid0a
ln -s /dev/id0b /dev/rid0b
# etc. etc.

# Create filesystems on your root (and other non-swap) partitions
#       Device      Size in 512 byte blocks     Heads   Sectors per track
mkfs    /dev/id0a   483840                      16      63
# mkfs   /dev/id0c  <size>                      16      63

# Mount your new root FS
mount /dev/id0a /mnt

# Copy everything over except /dev (which needs special handling). Piping
# everything through tar is apparently the best way to preserve RISC iX's sparse 
# files - using cp will un-sparseify them!
cd /
tar cf - `ls -A1 | grep -v '^dev' | grep -v '^mnt'` | ( cd /mnt; tar xvf - )

# Copy /dev separately, since tar can't create device files
mkdir /mnt/dev
cd /dev
ls -A1 | cpio -pdvm /mnt/dev

mkdir /mnt/mnt
```

Edit `/mnt/etc/fstab` to use your new devices for `/` and `swap`, then reboot
into RISC OS with `reboot -RISCOS`.

## Patching the RISC iX boot loader

Start the A3000 again. Copy the `!RISCiX` application from the SCSI disc to
the RISC OS partition on your IDE disc.

Patch the following words in the `!RISCiX.RISCiXFS` module (`!Zap` is great
for this):

| Address  | Change from            | To                     |
| -------- | ---------------------- | ---------------------- |
| `&10514` | `00007473`             | `00006469`             |
| `&10CE0` | `MOV R0,&#0240`        | `MOV R0,#&0FC0`        |
| `&10CE4` | `ADD R0,R0,#&00400000` | `ADD R0,R0,#&00410000` |

The change at `&10514` replaces the string '`st`' with '`id`', allowing us to
use `Configure Device id0` to boot RISC iX from an IDE device, using the code
path intended for ST506 devices, while reporting an IDE device to RISC iX.

The changes at `&10CE0` and `&10CE4` change the filesystem SWI base used by
the ST506 code path, so that it calls IDEFS instead of ADFS.

## Finally booting RISC iX!

Remove the SCSI podule from the external slot of the A3000. You might want to
put an Ethernet II card back in its place.

Run the following commands:

```
IDEFS
RMLoad !RISCiX.RISCiXFS
Configure Device id0
Configure Unit 0
Configure Partition 0
RMReinit RISCiXFS
```

Note that if you open the 'Device Defaults' entry from the maintenance menu in
`!RISCiX`, your device settings will get clobbered (since `!RISCiX` thinks
they're invalid) and you will nave to run the above commands again.

Now you should be able to boot RISC iX using your patched boot loader! If so,
use a USB-to-IDE interface to write the disc image back to your A3000's hard
drive, and voilá! Your A3000 can now run an ancient Unix excruciatingly
slowly! Why did you want to do this again?
