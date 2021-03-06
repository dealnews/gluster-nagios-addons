#!/usr/bin/python
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

import os
import errno


from glusternagios import utils
from glusternagios import glustercli
from glusternagios import storage


_checkProc = utils.CommandPath('check_proc',
                               '/usr/lib64/nagios/plugins/check_procs')

_chkConfig = utils.CommandPath('chkconfig',
                               '/sbin/chkconfig', '/usr/sbin/chkconfig')

_glusterVolPath = "/var/lib/glusterd/vols"
_checkNfsCmd = [_checkProc.cmd, "-c", "1:", "-C", "glusterfs", "-a", "nfs"]
_checkShdCmd = [_checkProc.cmd, "-c", "1:", "-C", "glusterfs", "-a",
                "glustershd"]
_checkSmbCmd = [_checkProc.cmd, "-c", "1:", "-C", "smbd"]
_checkQuotaCmd = [_checkProc.cmd, "-c", "1:", "-C", "glusterfs", "-a",
                  "quotad"]
_checkBrickCmd = [_checkProc.cmd, "-C", "glusterfsd"]
_checkGlusterdCmd = [_checkProc.cmd, "-c", "1:", "-w", "1:1", "-C", "glusterd"]
_checkCtdbCmd = [_checkProc.cmd, "-c", "1:", "-C", "ctdbd"]
_chkConfigCtdb = [_chkConfig.cmd, "ctdb"]
checkIdeSmartCmdPath = utils.CommandPath(
    'check_ide_smart', '/usr/lib64/nagios/plugins/check_ide_smart')


class CtdbNodeStatus:
    OK = 'OK'
    UNHEALTHY = 'UNHEALTHY'
    PARTIALLYONLINE = 'PARTIALLYONLINE'
    DISABLED = 'DISABLED'


def _pidExists(pid):
    if type(pid) is int and pid > 0:
        return os.path.exists("/proc/%s" % pid)
    else:
        raise ValueError("invalid pid :%s" % pid)


def getBrickStatus(volumeName, brickName):
    status = None
    brickPath = brickName.split(':')[1]
    pidFile = brickName.replace(":/", "-").replace("/", "-") + ".pid"
    try:
        with open("%s/%s/run/%s" % (
                _glusterVolPath, volumeName, pidFile)) as f:
            try:
                if _pidExists(int(f.read().strip())):
                    status = utils.PluginStatusCode.OK
                    brickDevice = storage.getBrickDeviceName(brickPath)
                    disk = storage.getDisksForBrick(brickDevice)
                    cmd = [checkIdeSmartCmdPath.cmd, "-d", disk, "-n"]
                    rc, out, err = utils.execCmd(cmd)
                    if rc == utils.PluginStatusCode.CRITICAL and \
                       "tests failed" in out[0]:
                        status = utils.PluginStatusCode.WARNING
                        msg = "WARNING: Brick %s: %s" % (brickPath, out[0])
                else:
                    status = utils.PluginStatusCode.CRITICAL
            except ValueError as e:
                status = utils.PluginStatusCode.CRITICAL
                msg = "Invalid pid of brick %s: %s" % (brickPath,
                                                       str(e))
                return status, msg
    except IOError as e:
        if e.errno == errno.ENOENT:
            status = utils.PluginStatusCode.CRITICAL
        else:
            status = utils.PluginStatusCode.UNKNOWN
            msg = "UNKNOWN: Brick %s: %s" % (brickPath, str(e))
    finally:
        if status == utils.PluginStatusCode.OK:
            msg = "OK: Brick %s is up" % brickPath
        elif status == utils.PluginStatusCode.CRITICAL:
            msg = "CRITICAL: Brick %s is down" % brickPath
    return status, msg


def getNfsStatus(volInfo):
    # if nfs is already running we need not to check further
    status, msg, error = utils.execCmd(_checkNfsCmd)
    if status == utils.PluginStatusCode.OK:
        return status, "Process glusterfs-nfs is running"

    # if nfs is not running and any of the volume uses nfs
    # then its required to alert the user
    for volume, volumeInfo in volInfo.iteritems():
        if volumeInfo['volumeStatus'] == glustercli.VolumeStatus.OFFLINE:
            continue
        nfsStatus = volumeInfo.get('options', {}).get('nfs.disable', 'off')
        if nfsStatus == 'off':
            msg = "CRITICAL: Process glusterfs-nfs is not running"
            status = utils.PluginStatusCode.CRITICAL
            break
    else:
        msg = "OK: No gluster volume uses nfs"
        status = utils.PluginStatusCode.OK
    return status, msg


def getCtdbStatus(smbStatus, nfsStatus):
    # If SMB/NFS is not running, then skip ctdb check
    if smbStatus != utils.PluginStatusCode.OK and \
       nfsStatus != utils.PluginStatusCode.OK:
        return (utils.PluginStatusCode.OK,
                "CTDB ignored as SMB and NFS are not running")

    status, msg, error = utils.execCmd(_checkCtdbCmd)
    if status != utils.PluginStatusCode.OK:
        status, msg, error = utils.execCmd(_chkConfigCtdb)
        if status == utils.PluginStatusCode.OK:
            return (utils.PluginStatusCode.CRITICAL,
                    "CTDB process is not running")
        return utils.PluginStatusCode.UNKNOWN, "CTDB not configured"

    # 'cdtb nodestatus' command will return the output in following format
    #
    # pnn:0 host_ip_address     OK (THIS NODE)
    #
    # Possible states are -
    # Ok,Disconnected,Banned,Disabled,Unhealthy,Stopped,Inactive,
    # PartiallyOnline
    # And combinations of them like
    # pnn:0 host_ip_address     BANNED|INACTIVE(THIS NODE)
    #
    # UNHEALTHY/DISABLED/PARTIALLYONLINE - node is partially operational
    # Any other state - node in not operational
    status, msg, error = utils.execCmd(['ctdb', 'nodestatus'])

    if len(msg) > 0:
            message = msg[0].split()
            if len(message) >= 2:
                msg = "Node status: %s" % message[2]
                if CtdbNodeStatus.OK in message[2]:
                    status = utils.PluginStatusCode.OK
                elif (CtdbNodeStatus.UNHEALTHY in message[2] or
                      CtdbNodeStatus.PARTIALLYONLINE in message[2] or
                      CtdbNodeStatus.DISABLED in message[2]):
                    status = utils.PluginStatusCode.WARNING
                else:
                    status = utils.PluginStatusCode.CRITICAL
    else:
        status = utils.PluginStatusCode.UNKNOWN
    return status, msg


def getSmbStatus(volInfo):
    status, msg, error = utils.execCmd(_checkSmbCmd)
    if status == utils.PluginStatusCode.OK:
        return status, "Process smb is running"

    # if smb is not running and any of the volume uses smb
    # then its required to alert the user
    for volume, volumeInfo in volInfo.iteritems():
        if volumeInfo['volumeStatus'] == glustercli.VolumeStatus.OFFLINE:
            continue
        cifsStatus = volumeInfo.get('options', {}).get('user.cifs', 'enable')
        smbStatus = volumeInfo.get('options', {}).get('user.smb', 'enable')
        if cifsStatus == 'enable' and smbStatus == 'enable':
            msg = "CRITICAL: Process smb is not running"
            status = utils.PluginStatusCode.CRITICAL
            break
    else:
        msg = "OK: No gluster volume uses smb"
        status = utils.PluginStatusCode.OK
    return status, msg


def getQuotadStatus(volInfo):
    # if quota is already running we need not to check further
    status, msg, error = utils.execCmd(_checkQuotaCmd)
    if status == utils.PluginStatusCode.OK:
        return status, "Process quotad is running"

    # if quota is not running and any of the volume uses quota
    # then the quotad process should be running in the host
    for volume, volumeInfo in volInfo.iteritems():
        if volumeInfo['volumeStatus'] == glustercli.VolumeStatus.OFFLINE:
            continue
        quotadStatus = volumeInfo.get('options', {}).get('features.quota', '')
        if quotadStatus == 'on':
            msg = "CRITICAL: Process quotad is not running"
            utils.PluginStatusCode.CRITICAL
            break
    else:
        msg = "OK: Quota not enabled"
        status = utils.PluginStatusCode.OK
    return status, msg


def getShdStatus(volInfo):
    status, msg, error = utils.execCmd(_checkShdCmd)
    if status == utils.PluginStatusCode.OK:
        return status, "Gluster Self Heal Daemon is running"

    hostUuid = glustercli.hostUUIDGet()
    for volumeName, volumeInfo in volInfo.iteritems():
        if volumeInfo['volumeStatus'] == glustercli.VolumeStatus.OFFLINE:
            continue
        if hasBricks(hostUuid, volumeInfo['bricksInfo']) and \
           int(volumeInfo['replicaCount']) > 1:
            status = utils.PluginStatusCode.CRITICAL
            msg = "CRITICAL: Gluster Self Heal Daemon not running"
            break
    else:
        msg = "OK: Process Gluster Self Heal Daemon"
        status = utils.PluginStatusCode.OK
    return status, msg


def getGlusterdStatus():
    status, msg, error = utils.execCmd(_checkGlusterdCmd)
    if status == utils.PluginStatusCode.OK:
        return status, "Process glusterd is running"
    elif status == utils.PluginStatusCode.CRITICAL:
        return status, "Process glusterd is not running"
    return status, msg[0] if len(msg) > 0 else ""


def hasBricks(hostUuid, bricks):
    for brick in bricks:
        if brick['hostUuid'] == hostUuid:
            return True
    return False
