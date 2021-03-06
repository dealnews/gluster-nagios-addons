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

import sys
import lockfile
import logging
import time
from daemon import runner
from logging import handlers
import nscautils
import check_proc_util
import glusternagios

from glusternagios import utils
from glusternagios import glustercli


_nfsService = "NFS"
_shdService = "Self-Heal"
_smbService = "SMB"
_brickService = "Brick - %s"
_glusterdService = "Gluster Management"
_quotadService = "Quota"
_ctdbdService = "CTDB"
checkIdeSmartCmdPath = utils.CommandPath(
    'check_ide_smart', '/usr/lib64/nagios/plugins/check_ide_smart')
nagios_server_conf_path = nscautils.__NAGIOSSERVER_CONF


def getBrickStatus(volInfo):
    bricks = {}
    hostUuid = glustercli.hostUUIDGet()
    for volumeName, volumeInfo in volInfo.iteritems():
        for brick in volumeInfo['bricksInfo']:
            if brick.get('hostUuid') != hostUuid:
                continue
            brickPath = brick['name'].split(':')[1]
            if volumeInfo['volumeStatus'] == glustercli.VolumeStatus.OFFLINE:
                status = utils.PluginStatusCode.CRITICAL
                msg = "CRITICAL: Brick %s is down" % brickPath
            else:
                status, msg = check_proc_util.getBrickStatus(volumeName,
                                                             brick['name'])
            brickService = _brickService % brickPath
            bricks[brickService] = [status, msg]
    return bricks


class Status():
    def __init__(self, code=None, message=None):
        self.code = code
        self.message = message

    def isStatusChanged(self, code, message):
        if (self.code, self.message) != (code, message) or \
           code == utils.PluginStatusCode.CRITICAL:
            self.code = code
            self.message = message
            return True
        return False


class App():
    def __init__(self):
        self.stdin_path = '/dev/null'
        self.stdout_path = '/dev/null'
        self.stderr_path = '/dev/null'
        self.pidfile_path = '/var/run/glusterpmd.pid'
        self.pidfile_timeout = 5

    def run(self):
        hostName = nscautils.getCurrentHostNameInNagiosServer()
        sleepTime = int(nscautils.getProcessMonitorSleepTime())
        glusterdStatus = Status()
        nfsStatus = Status()
        smbStatus = Status()
        shdStatus = Status()
        quotaStatus = Status()
        ctdbStatus = Status()
        brickStatus = {}
        while True:
            if not hostName:
                hostName = nscautils.getCurrentHostNameInNagiosServer()
                if not hostName:
                    logger.warn("'hostname_in_nagios' is not configured "
                                "in %s" % nagios_server_conf_path)
                    time.sleep(sleepTime)
                    continue
            status, msg = check_proc_util.getGlusterdStatus()
            if glusterdStatus.isStatusChanged(status, msg):
                nscautils.send_to_nsca(hostName, _glusterdService, status, msg)

            # Get the volume status only if glusterfs is running to avoid
            # unusual delay
            if status != utils.PluginStatusCode.OK:
                logger.warn("Glusterd is not running")
                time.sleep(sleepTime)
                continue

            try:
                volInfo = glustercli.volumeInfo()
            except glusternagios.glustercli.GlusterCmdFailedException:
                logger.error("failed to find volume info")
                time.sleep(sleepTime)
                continue

            status, msg = check_proc_util.getNfsStatus(volInfo)
            if nfsStatus.isStatusChanged(status, msg):
                nscautils.send_to_nsca(hostName, _nfsService, status, msg)

            status, msg = check_proc_util.getSmbStatus(volInfo)
            if smbStatus.isStatusChanged(status, msg):
                nscautils.send_to_nsca(hostName, _smbService, status, msg)

            status, msg = check_proc_util.getCtdbStatus(smbStatus.code,
                                                        nfsStatus.code)
            if ctdbStatus.isStatusChanged(status, msg):
                nscautils.send_to_nsca(hostName, _ctdbdService, status, msg)

            status, msg = check_proc_util.getShdStatus(volInfo)
            if shdStatus.isStatusChanged(status, msg):
                nscautils.send_to_nsca(hostName, _shdService, status, msg)

            status, msg = check_proc_util.getQuotadStatus(volInfo)
            if quotaStatus.isStatusChanged(status, msg):
                nscautils.send_to_nsca(hostName, _quotadService, status, msg)

            brick = getBrickStatus(volInfo)
            # brickInfo contains status, and message
            for brickService, brickInfo in brick.iteritems():
                if brickInfo != brickStatus.get(brickService, [None]) \
                   or brickInfo[0] == utils.PluginStatusCode.CRITICAL:
                    brickStatus[brickService] = brickInfo
                    nscautils.send_to_nsca(hostName, brickService,
                                           brickInfo[0], brickInfo[1])
            time.sleep(sleepTime)

if __name__ == '__main__':
    app = App()
    logger = logging.getLogger("GlusterProcLog")
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    handler = handlers.TimedRotatingFileHandler(
        "/var/log/glusterpmd.log", 'midnight')
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    daemonRunner = runner.DaemonRunner(app)
    daemonRunner.daemon_context.files_preserve = [handler.stream]
    try:
        daemonRunner.do_action()
    except lockfile.LockTimeout:
        logger.error("failed to aquire lock")
    except runner.DaemonRunnerStopFailureError:
        logger.error("failed to get the lock file")
    sys.exit(utils.PluginStatusCode.OK)
