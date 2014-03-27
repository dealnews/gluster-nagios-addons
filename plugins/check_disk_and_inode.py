#!/usr/bin/python
# sadf.py -- nagios plugin uses sadf output for perf data
# Copyright (C) 2014 Red Hat Inc
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301, USA
#


import re
import sys
import commands
from optparse import OptionParser
from glusternagios import utils

WARNING_LEVEL = 80
CRITICAL_LEVEL = 90


def getVal(val):
    dmatch = re.compile('[0-9]+').match(val)
    if (dmatch):
        return float(eval(dmatch.group(0)))
    else:
        return 0


def getUsageAndFree(command, lvm):
    disk = {'path': None, 'usePercent': None, 'avail': None,
            'used': None, 'size': None, 'fs': None}
    status = commands.getstatusoutput(command)[1].split()
    disk['path'] = status[-1]
    disk['avail'] = getVal(status[-3])
    disk['used'] = getVal(status[-4])
    disk['size'] = getVal(status[-5])
    disk['fs'] = status[-6]
    disk['usePcent'] = getVal(status[-2])
    disk['availPcent'] = 100 - disk['usePcent']
    return disk


def getDisk(path, usage=None, lvm=False):
    if usage:
        return getUsageAndFree("df -B%s %s" % (usage, path), lvm)
    else:
        return getUsageAndFree("df -BG %s" % path, lvm)


def getInode(path, lvm=False):
    return getUsageAndFree("df -i %s" % path, lvm)


def getMounts(searchQuery, excludeList=[]):
    mountPaths = []
    f = open("/etc/mtab")
    for i in f.readlines():
        if i.startswith(searchQuery):
            if not excludeList:
                mountPaths.append(i.split()[0])
            else:
                device = i.split()
                if not device[0] in options.exclude and\
                   not device[1] in options.exclude:
                    mountPaths.append(device[0])
    f.close()
    return mountPaths


def parse_input():
    parser = OptionParser()
    parser.add_option('-w', '--warning', action='store', type='int',
                      dest='warn',
                      help='Warning count in %', default=WARNING_LEVEL)
    parser.add_option('-c', '--critical', action='store', type='int',
                      dest='crit',
                      help='Critical count in %', default=CRITICAL_LEVEL)
    parser.add_option('-u', '--usage', action="store", dest='usage',
                      help='Usage in %/GB/MB/KB', default=None)
    parser.add_option('-l', '--lvm', action="store_true",
                      dest='lvm',
                      help='List lvm mounts', default=False)
    parser.add_option('-d', '--inode', action="store_true", dest='inode',
                      help='List inode usage along with disk/brick usage',
                      default=False)
    parser.add_option('-a', '--all', action="store_true",
                      dest='all',
                      help='List all mounts', default=False)
    parser.add_option('-n', '--ignore', action="store_true",
                      dest='ignore',
                      help='Ignore errors', default=False)
    parser.add_option('-i', '--include', action='append', type='string',
                      dest='mountPath',
                      help='Mount path', default=[])
    parser.add_option('-x', '--exclude', action="append", type='string',
                      dest='exclude',
                      help='Exclude disk/brick')
    return parser.parse_args()


def showDiskUsage(warn, crit, mountPaths, toListInode, usage=False,
                  isLvm=False, ignoreError=False):
    diskPerf = []
    warnList = []
    critList = []
    diskList = []
    mounts = []
    level = -1

    for path in mountPaths:
        disk = getDisk(path,
                       usage,
                       isLvm)

        inode = getInode(path,
                         isLvm)

        if disk['path'] in mounts:
            continue
        if not disk['used'] or not inode['used']:
            if ignoreError:
                continue
            else:
                sys.exit(utils.PluginStatusCode.UNKNOWN)

        mounts.append(disk['path'])
        if usage:
            data = "%s=%.1f;%.1f;%.1f;0;%.1f" % (
                disk['path'],
                disk['used'],
                warn * disk['size'] / 100,
                crit * disk['size'] / 100,
                disk['size'])
            if toListInode:
                data += " %s=%.1f;%.1f;%.1f;0;%.1f" % (
                    inode['path'],
                    inode['used'],
                    warn * inode['used'] / 100,
                    crit * inode['used'] / 100,
                    inode['used'])
        else:
            data = "%s=%.2f;%s;%s;0;100" % (
                disk['path'],
                disk['usePcent'],
                warn,
                crit)

            if toListInode:
                data += " %s=%.2f;%s;%s;0;100" % (
                    inode['path'],
                    inode['usePcent'],
                    warn,
                    crit)
        diskPerf.append(data)

        if disk['usePcent'] >= crit or inode['usePcent'] >= crit:
            if disk['usePcent'] >= crit:
                critList.append(
                    "crit:disk:%s;%s;%s" % (disk['fs'],
                                            disk['path'],
                                            disk['usePcent']))
            else:
                critList.append("crit:inode:%s;%s;%s" % (inode['fs'],
                                                         inode['path'],
                                                         inode['usePcent']))
            if not level > utils.PluginStatusCode.WARNING:
                level = utils.PluginStatusCode.CRITICAL
        elif (disk['usePcent'] >= warn and disk['usePcent'] < crit) or (
                inode['usePcent'] >= warn and inode['usePcent'] < crit):
            if disk['usePcent'] >= warn:
                warnList.append("warn:disk:%s;%s;%s" % (disk['fs'],
                                                        disk['path'],
                                                        disk['usePcent']))
            else:
                warnList.append("warn:inode:%s;%s;%s" % (inode['fs'],
                                                         inode['path'],
                                                         inode['usePcent']))
            if not level > utils.PluginStatusCode.OK:
                level = utils.PluginStatusCode.WARNING
        else:
            diskList.append("%s=%s" % (disk['fs'], disk['path']))

    msg = " ".join(critList + warnList)
    if not msg:
        msg += " disks:mounts:(" + ",".join(diskList) + ")"

    return level, msg, diskPerf


if __name__ == '__main__':
    (options, args) = parse_input()

    if options.lvm:
        searchQuery = "/dev/mapper"
    else:
        searchQuery = "/"

    if not options.mountPath or options.lvm or options.all:
        options.mountPath += getMounts(searchQuery, options.exclude)

    level, msg, diskPerf = showDiskUsage(options.warn,
                                         options.crit,
                                         options.mountPath,
                                         options.inode,
                                         options.usage,
                                         options.lvm,
                                         options.ignore)

    if utils.PluginStatusCode.CRITICAL == level:
        sys.stdout.write("%s : %s | %s\n" % (
            utils.PluginStatus.CRITICAL,
            msg,
            " ".join(diskPerf)))
        sys.exit(utils.PluginStatusCode.CRITICAL)
    elif utils.PluginStatusCode.WARNING == level:
        sys.stdout.write("%s : %s | %s\n" % (
            utils.PluginStatus.WARNING,
            msg,
            " ".join(diskPerf)))
        sys.exit(utils.PluginStatusCode.WARNING)
    else:
        sys.stdout.write("%s : %s | %s\n" % (
            utils.PluginStatus.OK,
            msg,
            " ".join(diskPerf)))