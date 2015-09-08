# -*- coding: utf-8 -*-


import os

import storage.devices.pyudev
global_udev = storage.devices.pyudev.Udev()

# e.g come from formats/dmraid.py
DMRAIDMEMBERUDEVTYPES = ["adaptec_raid_member", "ddf_raid_member",
                 "hpt37x_raid_member", "hpt45x_raid_member",
                 "isw_raid_member",
                 "jmicron_raid_member", "lsi_mega_raid_member",
                 "nvidia_raid_member", "promise_fasttrack_raid_member",
                 "silicon_medley_raid_member", "via_raid_member"]
# e.g come from formats/mdraid.py
MDRAIDMEMBERUDEVTYPES = ["linux_raid_member"]


def udev_get_block_devices():

    # Wait for scsi adapters to be done with scanning their busses (#583143)
    os.system("modprobe scsi_wait_scan")
    os.system("rmmod scsi_wait_scan")
    os.system("udevadm settle --timeout=300")

    entries = []
    for path in udev_enumerate_block_devices():
        entry = udev_get_block_device(path)
        if entry:
            if entry["name"].startswith("md"):
                # mdraid is really braindead, when a device is stopped
                # it is no longer usefull in anyway (and we should not
                # probe it) yet it still sticks around, see bug rh523387
                state = None
                state_file = "/sys/%s/md/array_state" % entry["sysfs_path"]
                if os.access(state_file, os.R_OK):
                    state = open(state_file).read().strip()
                if state == "clear":
                    continue
            entries.append(entry)
    return entries

def __is_blacklisted_blockdev(dev_name):

    """Is this a blockdev we never want for an install?"""
    if dev_name.startswith("loop") or dev_name.startswith("ram") or dev_name.startswith("fd"):
        return True

    if os.path.exists("/sys/class/block/%s/device/model" %(dev_name,)):
        model = open("/sys/class/block/%s/device/model" %(dev_name,)).read()
        for bad in ("IBM *STMF KERNEL", "SCEI Flash-5", "DGC LUNZ"):
            if model.find(bad) != -1:
                return True
    return False

def udev_enumerate_block_devices():

    return filter(lambda d: not __is_blacklisted_blockdev(os.path.basename(d)),
                  udev_enumerate_devices(deviceClass="block"))

def udev_enumerate_devices(deviceClass="block"):
    
    devices = global_udev.enumerate_devices(subsystem=deviceClass)
    return [path[4:] for path in devices]

def udev_get_block_device(sysfs_path):
    
    dev = udev_get_device(sysfs_path)
    if not dev or not dev.has_key("name"):
        return None
    else:
        return dev

def udev_get_device(sysfs_path):
    
    if not os.path.exists("/sys%s" % sysfs_path):
        return None

    # we remove the /sys part when enumerating devices,
    # so we have to prepend it when creating the device
    dev = global_udev.create_device("/sys" + sysfs_path)
    if dev:
        dev["name"] = dev.sysname
        dev["sysfs_path"] = sysfs_path
        # now add in the contents of the uevent file since they're handy
        dev = udev_parse_uevent_file(dev)
    return dev

def udev_parse_uevent_file(dev):
    
    path = os.path.normpath("/sys/%s/uevent" % dev['sysfs_path'])
    if not os.access(path, os.R_OK):
        return dev

    # e.g modify for vCenter python2.4
    if os.access(path, os.F_OK):
        f = file(path)
        lines = f.readlines()
        f.close()
#    with open(path) as f:
#        for line in f.readlines():
        for line in lines:
            (key, equals, value) = line.strip().partition("=")
            if not equals:
                continue
            # e.g we need DEVNAME keep state /dev/xxx/xxx, not xxx
            # e.g modify by gongshenghua
            if key == "DEVNAME":
                continue
            dev[key] = value
    return dev

def udev_device_is_cdrom(info):

    """ Return True if the device is an optical drive. """
    # FIXME: how can we differentiate USB drives from CD-ROM drives?
    #         -- USB drives also generate a sdX device.
    return info.get("ID_CDROM") == "1"

def udev_device_is_disk(info):

    """ Return True is the device is a disk. """
    if udev_device_is_cdrom(info):
        return False
    has_range = os.path.exists("/sys/%s/range" % info['sysfs_path'])
    return info.get("DEVTYPE") == "disk" or has_range

def getDeviceByName(name, storage_devices):

    if not name:
        return None
    found = None
    for device in storage_devices:
        if device["name"] == name:
            found = device
            break
        if not device.has_key("type"):
            continue
        elif (device["type"] == "lvmlv" or device["type"] == "lvmvg") and \
                device["name"] == name.replace("--","-"):
            found = device
            break
    return found

def udev_device_is_biosraid(info):

    # Note that this function does *not* identify raid sets.
    # Tests to see if device is parto of a dmraid set.
    # dmraid and mdraid have the same ID_FS_USAGE string, ID_FS_TYPE has a
    # string that describes the type of dmraid (isw_raid_member...),  I do not
    # want to maintain a list and mdraid's ID_FS_TYPE='linux_raid_member', so
    # dmraid will be everything that is raid and not linux_raid_member
    if info.has_key("ID_FS_TYPE") and \
            (info["ID_FS_TYPE"] in DMRAIDMEMBERUDEVTYPES or \
             info["ID_FS_TYPE"] in MDRAIDMEMBERUDEVTYPES) and \
            info["ID_FS_TYPE"] != "linux_raid_member":
        return True
    return False

#####################################
#####################################

class StorageDevice(object):
    
    def __init__(self):
        
        self.storage_devices = udev_get_block_devices()

    def is_raid_member(self, info):
        
        if udev_device_is_biosraid(info) and udev_device_is_disk(info):
            return True
        return False

    def is_raid_map(self, info):
        
        if info.has_key("MD_LEVEL") and "raid" in info["MD_LEVEL"]:
            return True
        return False

    def get_raid_members(self):

        raid_members = []
        for info in self.storage_devices:
            if self.is_raid_member(info):
                raid_members.append(info["DEVNAME"])
        return raid_members
    
    def get_slaves(self, info):
        
        sysfs_path = info['sysfs_path']
        slave_names = []
        # e.g /sys/devices/virtual/block/md126/slaves
        slaves_dir = "/sys/%s/slaves" % sysfs_path
        if os.path.isdir(slaves_dir):
            slave_names = os.listdir(slaves_dir)
        return slave_names

    def get_raid_maps(self):

        raid_maps = []
        for info in self.storage_devices:
            slave_names = self.get_slaves(info)
            for slave_name in slave_names:
                slave_info = getDeviceByName(slave_name, self.storage_devices)
                if not self.is_raid_member(slave_info):
                    continue
                if info["DEVNAME"] not in raid_maps:
                    if not self.is_raid_map(info):
                        continue
                    raid_maps.append(info["DEVNAME"])
                break
        return raid_maps
    
    def is_partition(self, info):
        
        if (info.has_key("DEVTYPE") and info["DEVTYPE"] == "partition") or \
           (info.has_key("DM_UUID") and "part" in info["DM_UUID"]):
            return True
        return False
    
    def is_dm_device(self, info):
        
        if "/dev/dm" in info["DEVNAME"]:
            return True
        return False
    
    def is_nbd_device(self, info):
        
        if "/dev/nbd" in info["DEVNAME"]:
            return True
        return False
    
    def is_raid_container(self, info):
        
        if info.has_key("MD_LEVEL") and "raid" not in info["MD_LEVEL"]:
            return True
        return False
    
#    def is_device_firstnum(self, info):
#        
#        if info.has_key("MINOR") and info["MINOR"] == "0":
#            return True
#        return False
    
    def is_LVM_dm(self, info):
        
        if info.has_key("DM_UUID") and "LVM-" in info["DM_UUID"]:
            return True
        return False

    def is_crypt_mapper(self, info):

        if info.has_key("DM_UUID") and "CRYPT-LUKS" in info["DM_UUID"]:
            return True
        return False

    def get_useful_hard_devices(self):
        
        useful_hard_devices = []
        for info in self.storage_devices:
            if not udev_device_is_disk(info):
                continue
            if udev_device_is_cdrom(info):
                continue
            if udev_device_is_biosraid(info):
                continue
#            if self.is_dm_device(info):
#                continue
            if self.is_nbd_device(info):
                continue
            if self.is_raid_container(info):
                continue
#            if not self.is_device_firstnum(info):
#                continue
            if self.is_LVM_dm(info):
                continue
            if self.is_partition(info):
                continue
            if self.is_crypt_mapper(info):
                continue
            useful_hard_devices.append(info)
        return useful_hard_devices
    
    def get_dev_name(self, info):
        
        if self.is_dm_device(info):
            if info.has_key("symlinks") and info["symlinks"]:
                
                if info.has_key("DM_NAME") and \
                   os.access("/dev/mapper/" + info["DM_NAME"], os.F_OK):
                    return "/dev/mapper/" + info["DM_NAME"]
                
                lname = None
                for link_name in info["symlinks"]:
                    if "/dev/disk/by-id/dm-name" in link_name:
                        lname = link_name
                        break
                if lname:
                    lines = os.popen('ls -l %s' % lname).readlines()
                    relname = None
                    for line in lines:
                        if lname in line and '->' in line:
                            relname = line.split('/')[-1]
                    if not relname:
                        return lname
                    lines = os.popen('ls -l /dev/mapper/').readlines()
                    for line in lines:
                        if relname in line:
                            if len(line.split())==11:
                                return '/dev/mapper/'+line.split()[-3]
                return info["symlinks"][0]
        return info["DEVNAME"]
    
    def is_in_the_same_dev(self, info_item, info):
        
        # e.g partition/device/lv/vg in the same harddisk, have the same MAJOR
        #if (not info_item.has_key("MAJOR")) or (not info.has_key("MAJOR")):
        #    return False
        #if info_item["MAJOR"] != info["MAJOR"]:
        #    return False
        
        # e.g partition/device/lv/vg in the same harddisk, startswith the same DEVPATH
        if (not info_item.has_key("DEVPATH")) or (not info.has_key("DEVPATH")):
            return False
        if not info_item["DEVPATH"].startswith(info["DEVPATH"]):
            if not info_item.has_key("DM_NAME"):
                return False
            if not info.has_key("DM_NAME"):
                return False
            if not info_item["DM_NAME"].startswith(info["DM_NAME"]):
                return False
        
        return True
    
    def get_dev_pts(self, info):
        
        partitions = []
        for info_item in self.storage_devices:
#            if self.is_device_firstnum(info_item):
#                continue
            if not self.is_partition(info_item):
                continue
            if not self.is_in_the_same_dev(info_item, info):
                continue
            partitions.append(info_item)
        return partitions
    
    def get_devices_ptinfo(self):
        
        useful_hard_devices = self.get_useful_hard_devices()
        devices_ptinfo = []
        for info in useful_hard_devices:
            pts = []
            devices_pt = {
                "hard_disk":self.get_dev_name(info),
                "partitions":pts,
            }
            
            partitions = self.get_dev_pts(info)
            for ptinfo in partitions:
                pts.append(self.get_dev_name(ptinfo))
            
            devices_ptinfo.append(devices_pt)

        hd_devs = []
        for x in devices_ptinfo:
            hd_flag = True
            for y in devices_ptinfo:
                if (x.get("hard_disk") != y.get("hard_disk")) and x.get("hard_disk").startswith(y.get("hard_disk")):
                    hd_flag = False
                    y["partitions"].append(x.get("hard_disk"))
                    y["partitions"].sort()
                    break
            if hd_flag:
                hd_devs.append(x)
                
        return hd_devs

