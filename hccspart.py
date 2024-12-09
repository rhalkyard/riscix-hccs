#!/usr/bin/env python3

"""
Quick and incredibly dirty utility for creating a RISC iX partition table on
an Armstrong-Walker IDEFS (as used in HCCS IDE cards) disc image. This code
makes all manner of assumptions and commits all manner of crimes. Use it only
on an image that's been freshly partitioned by !IDEMgr and contains no data
that you care about!
"""

import abc
import argparse
import collections
import io
import math
import os
import struct
import sys
from typing import Optional

def sum8(data: bytes):
    """
    Calculate an 8 bit byte-wise rolling sum
    """
    sum = 0
    for b in data:
        sum += int(b)
        if sum > 255:
            sum -= 255
    return sum

def ror(n: int, r: int):
    """Equivalent to ARM ROR Rout,Rn,Rr"""
    width=32
    return (2**width-1)&(n>>r|n<<(width-r))

def defect_checksum(defects: list[int]):
    """Calculate FileCore defect list checksum"""
    checksum = 0
    for defect in defects:
        checksum = ror(checksum, 13)
        checksum ^= defect
    
    checksum ^= checksum >> 16
    checksum ^= checksum >> 8
    checksum &= 0xff
    return checksum

def chunks(lst, n: int):
    """Yield successive n-sized chunks from lst."""
    for i in range(0, len(lst), n):
        yield lst[i:i + n]


class DiscImageException(Exception):
    pass


class HWParams(abc.ABC):
    """
    Abstract base class for hardware-specific parameter blocks.
    """

    # Struct format string
    format = ''

    @classmethod
    def get_data(cls, data: bytes):
        """
        Hardware-specific parameters are stored at the high end of 0x0-0x1bf
        (i.e. a 16 byte hardware-specific parameter block occpuies addresses
        0x1b0-0x1bf). Extract the correct amount of data based on our format
        string.
        """
        return data[-struct.calcsize(cls.format):]

    @classmethod
    @abc.abstractmethod
    def from_bytes(cls, data: bytes):
        pass

    @abc.abstractmethod
    def serialise(self):
        pass

    @abc.abstractmethod
    def __init__(self):
        pass


class AWHwParams(HWParams):
    """
    Representation of hardware-specific parameter block for Armstrong-Walker
    IDEFS. Starts with magic string 'Andy', followed by 12 bytes of
    unknown purpose.
    """
    magic = b'Andy'
    format = '<4s12s'

    @classmethod
    def from_bytes(cls, data: bytes):
        fields = struct.unpack(cls.format, cls.get_data(data))

        if fields[0] != cls.magic:
            raise DiscImageException(f'Bad magic number in hardware '
                                     f'parameters (0x{fields[0]:08x}, '
                                     f'should be 0x{cls.magic:08x})')
        else:
            return cls(fields[1])

    def __init__(self, params: bytes):
        self.params = params
    
    def serialise(self):
        return struct.pack(self.format, self.magic, self.params)


class DiscRecord(collections.namedtuple('DiscRecord', [
        'sectorsize', 'spt', 'heads', 'density', 'idlen', 'bpmb', 'skew',
        'bootopt', 'lowsector', 'nzones', 'zonespare', 'root', 'size', 
        'cycle', 'name_raw', 'filetype', 'reserved'])):
    """
    Representation of a FileCore Disc Record. See PRM for details.
    """

    format = '<BBBBBBBBBBHIIH10sI24s'

    @classmethod
    def from_bytes(cls, data: bytes):
        fields = list(struct.unpack(cls.format, data))
        # Sector size and bytes per map block are stored as the log2 of the
        # actual value. Represent them here as the actual value.
        fields[0] = 2 ** fields[0]
        fields[5] = 2 ** fields[5]
        return cls(*fields)
    
    @property
    def name(self):
        return self.name_raw.decode('ascii').strip('\x00')
    
    @name.setter
    def name(self, value: str):
        self.name_raw = value.encode('ascii')

    def serialise(self):
        return struct.pack(self.format, int(math.log2(self.sectorsize)),
                           self.spt, self.heads, self.density, self.idlen, 
                           int(math.log2(self.bpmb)), self.skew, self.bootopt,
                           self.lowsector, self.nzones, self.zonespare,
                           self.root, self.size, self.cycle,
                           self.name.encode('ascii'), self.filetype,
                           self.reserved)


class BootBlock:
    """
    Representation of a FileCore Boot Block
    """

    @classmethod
    def from_bytes(cls, data: bytes, hwparam_class: type[HWParams]):
        if sum8(data[:-1]) != data[0x1ff]:
            raise DiscImageException('Bad boot block checksum')
        defects = []
        hwparams_start = 0
        for (word,) in struct.iter_unpack('<I', data):
            hwparams_start += 4
            if word & 0xffffff00 == 0x20000000:
                if word & 0xff == defect_checksum(defects):
                    break
                else:
                    raise DiscImageException('Bad defect list checksum')
            else:
                defects.append(word)

        # If the defect list has consumed past the start of the disc record,
        # then it was clearly not valid and we shouldn't go further.
        if hwparams_start > 0x1c0:
            raise DiscImageException('Invalid defect list')

        hwparams = hwparam_class.from_bytes(data[hwparams_start:0x1c0])

        # Technically we should only use the boot block Disc Record in order to
        # find the map, and then use the map Disc Record as our actual source of
        # truth about the volume. However in practice, the boot block copy seems
        # to be good enough.
        discrec = DiscRecord.from_bytes(data[0x1c0:0x1fc])

        # Technically 1fc-1fe is the "non-ADFS partition descriptor" but AFAIK
        # it was only ever used for RISC iX. Note that the cylinder number here
        # is in 256-byte sectors.
        riscix_flag = data[0x1fc]
        if riscix_flag:
            riscix_cylinder = struct.unpack('<H', data[0x1fd:0x1ff])[0]
        else:
            riscix_cylinder = None

        return cls(defects, hwparams, discrec, riscix_cylinder)

    def __init__(self, defects: list[int]=[], hwparams: HWParams=None, 
                 disc_record: DiscRecord=None, 
                 riscix_cylinder: Optional[int]=None):
        self.defects = defects
        self.hwparams = hwparams
        self.disc_record = disc_record
        self.riscix_cylinder = riscix_cylinder

    def serialise(self):
        defects_end = 0x20000000 | defect_checksum(self.defects)
        defectlist = struct.pack('<I' + 'I' * len(self.defects),
                                 *(self.defects + [defects_end]))

        hwparams = self.hwparams.serialise()

        # left-pad hardware params to fill unused defect list space
        hwparams = bytes(0x1c0 - len(defectlist) - len(hwparams)) + hwparams

        disc_record = self.disc_record.serialise()
        if self.riscix_cylinder is not None:
            riscix_descriptor = struct.pack('<BH', 1, self.riscix_cylinder)
        else:
            riscix_descriptor = bytes([0, 0, 0])

        bootblock = defectlist + hwparams + disc_record + riscix_descriptor

        checksum = struct.pack('B', sum8(bootblock))
        return bootblock + checksum


class RiscixPartition:
    """
    Representation of a RISC iX partition table entry
    """
    format = '<3I16s'

    @classmethod
    def from_bytes(cls, data: bytes):
        fields = struct.unpack(cls.format, data)
        if fields[0] == 0 or fields[1] == 0:
            return None
        else:
            return cls(fields[3].strip(b'\0').decode('ascii'), fields[0],
                       fields[1])

    def __init__(self, name: str, start_cylinder: int, num_cylinders: int):
        self.name = name
        self.start_cylinder = start_cylinder
        self.num_cylinders = num_cylinders
    
    def __repr__(self):
        return f'RiscixPartition({self.name, self.start_cylinder, self.num_cylinders})'

    def serialise(self):
        return struct.pack(self.format, self.start_cylinder,
                           self.num_cylinders, 1, self.name.encode('ascii'))


class RiscixPartitionTable(collections.UserList):
    """
    Representation of a RISC iX partition table

    NOTE: The RISC iX partition table format assumes 256 byte sectors.
    """
    ptable_magic = 0x70617274 # 'part'
    bbtable_magic = 0x42616421 # 'bad!'

    @classmethod
    def from_bytes(cls, data: bytes):
        magic = struct.unpack('<I', data[0:4])[0]
        if magic != cls.ptable_magic:
            raise DiscImageException('Invalid magic number in RISC iX '
                                     'partition table')

        partitions = []
        # Each partition table entry is 28 bytes, we support up to 16 entries
        for (chunk,) in struct.iter_unpack(f'28s', data[4:452]):
            part = RiscixPartition.from_bytes(chunk)
            if part is None:
                break
            else:
                partitions.append(part)

        return RiscixPartitionTable(partitions)

    def __init__(self, partitions: list[RiscixPartition]=[]):
        self.data = partitions

    def __repr__(self):
        return f'RiscixPartitionTable({self.data})'

    def serialise(self):
        ptable = struct.pack('<I' + len(self.data) * '28s', self.ptable_magic,
                             *[partition.serialise() for partition in self.data])
        ptable += b'\x00' * (512 - len(ptable))

        # Write out an empty bad-block table. IDE driver ignores it anyway.
        bbtable = struct.pack('<I', self.bbtable_magic)
        bbtable += b'\x00' * (512 - len(bbtable))
        return ptable + bbtable


AWPartition = collections.namedtuple('AWPartition',
                                     ['offset', 'bootblock', 'riscix_pt'])


def find_partitions(image: io.BufferedReader):
    """
    Find all RISC OS partitions in an image
    """
    offset = 0
    partitions = []
    while True:
        image.seek(offset + 0xc00, os.SEEK_SET)
        data = image.read(512)

        # Reached EOF, no more partitions
        if len(data) != 512:
            break

        # Parse the boot block.
        try:
            bootblock = BootBlock.from_bytes(data, AWHwParams)
        except DiscImageException as e:
            print(f'find_partitions: rejected potential RISC OS partition at '
                  f'{offset:x}: {e}')
            break

        # Validate partition extent
        if image.seek(offset + bootblock.disc_record.size, os.SEEK_SET) != \
                offset + bootblock.disc_record.size:
            break

        cyl_size = (bootblock.disc_record.sectorsize * 
                    bootblock.disc_record.spt * bootblock.disc_record.heads)

        if bootblock.riscix_cylinder:
            image.seek(offset + bootblock.riscix_cylinder // 2 * cyl_size,
                       os.SEEK_SET)
            riscix_pt = RiscixPartitionTable.from_bytes(image.read(1024))
        else:
            riscix_pt = None

        partitions.append(AWPartition(offset, bootblock, riscix_pt))
        offset += bootblock.disc_record.size

    return partitions


def print_riscos_partitions(partitions: list[AWPartition], image_size: int):
    print(f"    {'NAME':10} {'OFFSET':8} {'MB':6} {'RISC IX CYL.':8}")
    for p in partitions:
        offset = p.offset
        bootblock = p.bootblock
        disc_record = p.bootblock.disc_record
        if bootblock.riscix_cylinder is None:
            riscix = '-'
        else:
            riscix = bootblock.riscix_cylinder // 2
        print(f'    {disc_record.name:10} {offset:<8x} {disc_record.size/1024/1024:<4.2f} {riscix:<8}')

    if partitions:
        unused_start = (partitions[-1].offset + 
                        partitions[-1].bootblock.disc_record.size)
    else:
        unused_start = 0

    if partitions and partitions[-1].bootblock.riscix_cylinder:
        print(f"    {'[RISC iX]':10} {unused_start:<8x} {(image_size - unused_start)/1024/1024:<4.2f} -")
    elif image_size - unused_start > 0:
        print(f"    {'[unused]':10} {unused_start:<8x} {(image_size - unused_start)/1024/1024:<4.2f} -")


def print_riscix_partitions(partitions: RiscixPartitionTable, cyl_size: int):
    print(f"    {'NAME':16} {'START':6} {'CYLS':6} {'MB':6}")

    for p in partitions:
        print(f'    {p.name:16} {p.start_cylinder//2:<6} {p.num_cylinders//2:<6} {p.num_cylinders//2*cyl_size/1024/1024:<4.2f}')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('image', help='Armstrong-Walker IDEFS disc image')
    p.add_argument('swap_size', metavar='swap-size', type=int, default=20,
                   nargs='?', help='Size of swap partition in MB (default: '
                   '20MB)')
    p.add_argument('root_size', metavar='root-size', type=int, default=None,
                   nargs='?', help='Size of root partition in MB (default: '
                   'as large as possible)')
    p.add_argument('extra_partitions', metavar='partition-name=partition-size',
                   nargs='*', help='Additional partitions to create')
    p.add_argument('--yes', '-y', action='store_true',
                   help='Write partition table without prompting for review')
    args = p.parse_args()

    with open(args.image, 'rb') as image:
        image_size = image.seek(0, os.SEEK_END)

        riscos_partitions = find_partitions(image)

    print(f'Found {len(riscos_partitions)} RISC OS partitions:')
    print_riscos_partitions(riscos_partitions, image_size)
    
    if len(riscos_partitions) == 0:
        print('No RISC OS partitions recognised. Please partition the disc '
              'with !IDEMgr first')
        sys.exit(1)

    # The first RISC OS partition must contain the RISC iX information
    partition = riscos_partitions[0]
    discrec = partition.bootblock.disc_record
    if partition.bootblock.riscix_cylinder:
        new_riscix = False
        print(f'Using existing RISC iX partition table (linked from '
              f'\'{discrec.name}\') at cylinder '
              f'{partition.bootblock.riscix_cylinder//2}.')
    else:
        print(f'No existing RISC iX partition table found.')
        new_riscix = True
        unused_space = image_size - partition.offset - discrec.size
        if len(riscos_partitions) == 1 and unused_space > 100 * 1024 * 1024:
            # Empty space following RISC OS partition
            print(f'Using unused {unused_space/1024/1024} MB at end of image '
                  f'for RISC iX')
        elif len(riscos_partitions) >= 2:
            # Blow away remaining partitions to make some empty space
            to_erase = [p.disc_record.name for p in riscos_partitions[1:]]
            print(f'The following RISC OS partitions will be erased and '
                  f'used for RISC iX: {", ".join(to_erase)}')
        else:
            print('To create a RISC iX partition, you must either have '
                  '>100MB unused space after a single RISC OS partition, or '
                  'at least 2 RISC OS partitions, the first of which will '
                  'be preserved, and the remainder turned over to RISC iX')
            sys.exit(1)


    # Root filesystem must fit within 512MB limit of the last RISC OS
    # partition
    max_root_bytes = 512 * 1024 * 1024 - discrec.size

    # Size (in bytes) of one cylinder
    cyl_size = discrec.sectorsize * discrec.spt * discrec.heads

    # Number of cylinders in image
    image_cyls = image_size / cyl_size

    # Number of cylinders occupied by RISC OS partition
    riscos_cyls = discrec.size / cyl_size

    if new_riscix == False:
        print(f'Existing RISC iX partition table (will be replaced)')
        print_riscix_partitions(partition.riscix_pt, cyl_size)
        riscix_start_cyl = partition.bootblock.riscix_cylinder // 2
    else:
        riscix_start_cyl = math.ceil(riscos_cyls)
        partition.bootblock.riscix_cylinder = riscix_start_cyl * 2

    # Place RISC iX partition table at first cylinder boundary following
    # RISC OS
    riscix_total_cyls = math.floor(image_cyls - riscix_start_cyl)

    # Size of swap partition in cylinders
    riscix_swap_cyls = math.floor(args.swap_size*1024*1024 / (cyl_size))

    # Calculate space taken up by extra partitions, rounding up to
    # cylinder boundaries.
    extra_partitions = [p.split('=') for p in args.extra_partitions]
    extra_cyls = sum(math.ceil(int(p[1])*1024*1024/cyl_size)
                     for p in extra_partitions)

    # Root partition starts at next cylinder boundary after partition table
    riscix_root_start_cyl = riscix_start_cyl + 1
    if args.root_size is None:
        riscix_root_cyls = riscix_total_cyls - riscix_swap_cyls - extra_cyls - 1
        riscix_root_cyls = min(riscix_root_cyls, max_root_bytes // cyl_size)
    else:
        riscix_root_cyls = math.floor(args.root_size*1024*1024 / (cyl_size))

    # ... followed by swap
    riscix_swap_start_cyl = riscix_root_start_cyl + riscix_root_cyls

    riscix_partitions = [RiscixPartition('Root', (riscix_start_cyl + 1) * 2,
                                         riscix_root_cyls * 2),
                         RiscixPartition('Swap', riscix_swap_start_cyl * 2,
                                         riscix_swap_cyls * 2)]

    # ... followed by any extra partitions
    next_cyl = riscix_swap_start_cyl + riscix_swap_cyls
    for extra in extra_partitions:
        name = extra[0]
        size = int(extra[1])
        length_cyls = math.floor(size*1024*1024 / cyl_size)
        riscix_partitions.append(RiscixPartition(name, next_cyl * 2,
                                                 length_cyls * 2))
        next_cyl += length_cyls

    end_offset = next_cyl * cyl_size
    unused = image_size - (partition.offset + end_offset)

    new_riscix_pt = RiscixPartitionTable(riscix_partitions)

    print(f'RISC iX partition descriptor will be added to RISC OS partition '
          f'\'{discrec.name}\'')
    print(f'RISC iX partition table will be at cylinder {riscix_start_cyl} '
          f'relative to RISC OS partition')
    print(f'RISC iX partition table:')
    print_riscix_partitions(new_riscix_pt, cyl_size)

    if unused > 0:
        print(f'{unused / 1024/1024:4.2f}MB unused at end of disc')
    elif unused < 0:
        total = (next_cyl - riscix_start_cyl) * cyl_size
        available = image_size - partition.offset - riscix_start_cyl * cyl_size
        print(f'RISC iX partitions do not fit in available space! (total '
              f'size: {total/1024/1024:.2f}MB, available: '
              f'{available/1024/1024:.2f}MB)')
        sys.exit(1)

    if riscix_root_cyls * cyl_size < 64 * 1024 * 1024:
        print('ERROR: RISC iX root partition too small for a viable '
              'installation (must be >64MB)')
        sys.exit(1)

    if riscix_root_cyls * cyl_size + discrec.size > 512 * 1024 * 1024:
        print(f'ERROR: RISC iX root partition does not fit within 512MB of '
              f'the last RISC OS partition - must be smaller than '
              f'{max_root_bytes/1024/1024:.2f}MB')
        sys.exit(1)

    if not args.yes:
        response = None
        while not (response == 'y' or response == 'n'):
            print('OK to write to disc? y/n')
            response = sys.stdin.readline().strip()

        if response == 'n':
            sys.exit(0)

    new_bootblock_data = partition.bootblock.serialise()
    new_riscix_pt_data = new_riscix_pt.serialise()

    with open(args.image, 'r+b') as image:
        # Write updated boot block with pointer to RISC iX partition table
        image.seek(partition.offset + 0x0c00)
        image.write(new_bootblock_data)

        # Invalidate boot block of the partition we're converting to RISC iX
        if new_riscix:
            image.seek(partition.offset + discrec.size + 0xc00)
            image.write(bytes([0])*512)

        # Write RISC iX partition table
        image.seek(partition.offset + riscix_start_cyl * cyl_size)
        image.write(new_riscix_pt_data)


if __name__ == '__main__':
    main()
