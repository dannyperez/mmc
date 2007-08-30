#
# (c) 2004-2007 Linbox / Free&ALter Soft, http://linbox.com
#
# $Id$
#
# This file is part of LMC.
#
# LMC is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# LMC is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with LMC; if not, write to the Free Software
# Foundation, Inc., 51 Franklin St, Fifth Floor, Boston, MA  02110-1301  USA

import os
import os.path
import time
import ldap

from lmc.plugins.base import ldapUserGroupControl, LogView
from tools import *
from lmc.support.lmctools import ServiceManager
import lmc.plugins.network

class Dns(ldapUserGroupControl):

    def __init__(self, conffile = None, conffilebase = None):
        ldapUserGroupControl.__init__(self, conffilebase)
        self.configDns = lmc.plugins.network.NetworkConfig("network", conffile)
        self.reverseMarkup = "Reverse:"
        self.reversePrefix = ".in-addr.arpa"
        self.templateZone = """
// Auto generated by LMC agent - edit at your own risk !
zone "%(zone)s" {
    type master;
    database "ldap ldap://%(ldapurl)s????!bindname=%(admin)s,!x-bindpw=%(passwd)s 172800";
    notify yes;
};
"""

    def reverseZone(self, network):
        """
        Build a reverse zone name
        """
        ret = network.split(".")
        ret.reverse()
        return ".".join(ret) + self.reversePrefix

    def getZoneNetworkAddress(self, zone):
        """
        Return the network address of a zone thanks to its reverse
        """
        revZones = self.getReverseZone(zone)
        ret = []
        for rev in revZones:
            ret.append(self.translateReverse(rev))
        return ret

    def getAllZonesNetworkAddresses(self):
        """
        Return all the network addresses that are configured in the DNS.
        We only use the reverse zone to get them
        """
        ret = []
        for result in self.getZones(self.reversePrefix, True):
            ret.append(self.translateReverse(result[1]["zoneName"][0]))
        return ret
        
    def getReverseZone(self, name):
        """
        Return the name of the reverse zone of a zone
        """
        ret = []
        for result in self.getZones(reverse = True, base = "ou=" + name + "," + self.configDns.dnsDN):
            zoneName = result[1]["zoneName"][0]
            if zoneName.endswith(self.reversePrefix): ret.append(zoneName)            
        return ret

    def getZoneObjects(self, name, filt = None):
        """
        Return the objects defined in a zone
        """
        if filt:
            filt = "*" + filt.strip() + "*"
        else:
            filt = "*"
        search = self.l.search_s(self.configDns.dnsDN, ldap.SCOPE_SUBTREE, "(&(objectClass=dNSZone)(zoneName=%s)(relativeDomainName=%s))" % (name, filt), None)
        ret = []
        for result in search:
            relative = result[1]["relativeDomainName"][0]
            # Don't count these entries
            if relative != "@" and relative != name + ".":
                ret.append(result)
        return ret

    def getZoneObjectsCount(self, name):
        """
        Return the number of objects defined in a zone
        """
        search = self.l.search_s(self.configDns.dnsDN, ldap.SCOPE_SUBTREE, "(&(objectClass=dNSZone)(zoneName=%s))" % (name), ["relativeDomainName"])
        count = 0
        for result in search:
            relative = result[1]["relativeDomainName"][0]
            # Don't count these entries
            if relative != "@" and relative != name + ".":
                count = count + 1
        return count

    def getZone(self, zoneName):
        return self.l.search_s(self.configDns.dnsDN, ldap.SCOPE_SUBTREE, "(&(objectClass=dNSZone)(zoneName=%s)(relativeDomainName=%s))" % (zoneName, zoneName + "."), None)        
        
    def getZones(self, filt = "", reverse = False, base = None):
        """
        Return all available DNS zones. Reverse zones are returned only if reverse = True
        """
        filt = filt.strip()
        if not filt: filt = "*"
        else: filt = "*" + filt + "*"
        if not base: base = self.configDns.dnsDN
        search = self.l.search_s(base, ldap.SCOPE_SUBTREE, "(&(objectClass=dNSZone)(zoneName=%s)(relativeDomainName=%s))" % (filt, filt), None)
        ret = []
        for result in search:
            if (result[1]["zoneName"][0] + ".") == result[1]["relativeDomainName"][0]:
                if self.reversePrefix in result[1]["zoneName"][0]:
                    # Reverse zone
                    if reverse: ret.append(result)
                else: ret.append(result)
        return ret

    def zoneExists(self, zone):
        """
        Return true if the given zone exists
        """
        return len(self.getZone(zone)) == 1

    def addZone(self, name, network = None, netmask = None, reverse = False, description = None, nameserver = "ns", nameserverip = None):
        """
        @param name: the zone name
        @param network: the network address defined in this zone (needed to build the reverse zone)
        @param netmask: the netmask address (needed to build the reverse zone)
        """
        if reverse:
            if network == None or netmask == None:
                raise "Won't create reverse zone as asked, missing network or netmask"
            netmask = int(netmask)
            # Build network address start according to netmask
            elements = network.split(".")
            if netmask == 8:
                network = elements[0]
            elif netmask == 16:
                network = ".".join(elements[0:2])
            elif netmask == 24:
                network = ".".join(elements[0:3])
            else:
                raise "Won't create reverse zone as asked, netmask is not 8, 16 or 24"

        f = open(os.path.join(self.configDns.bindLdapDir, name), "w")
        d = {
            "zone" : name,
            "ldapurl" : self.ldapHost + "/" + self.configDns.dnsDN,
            "admin": self.config.get("ldap", "rootName").replace(",", "%2c").replace(" ", ""),
            "passwd" : self.config.get("ldap", "password")
            }    
        f.write(self.templateZone % d)
        if reverse:
            d["zone"] = self.reverseZone(network)
            f.write(self.templateZone % d)
        f.close()
        os.chmod(os.path.join(self.configDns.bindLdapDir, name), 0640)

        f = open(self.configDns.bindLdap, "r")
        found = False
        toadd = 'include "' + os.path.join(self.configDns.bindLdapDir, name) + '";\n'
        for line in f:
            if line == toadd:
                found = True
                break
        f.close()
        if not found:
            f = open(self.configDns.bindLdap, "a")
            f.write(toadd)
            f.close()

        # Create the needed zones object in LDAP
        if reverse:
            reverseZone = self.reverseZone(network)
            self.addDnsZone(reverseZone, "Reverve zone for " + name, name)
        else:
            reverseZone = None
        self.addDnsZone(name, description)
        
        # Fill SOA
        self.addSOA(name)
        ns = nameserver + "." + name + "."
        rec = {
            "nameserver" : ns,
            "emailaddr" :  "admin." + name + ".",
            "serial" : self.computeSerial(),
            "refresh" : "2D",
            "retry" : "15M",
            "expiry" : "2W",
            "minimum" : "1H",
            }
        self.setSOARecord(name, rec)
        self.setNSRecord(name, ns)

        # Fill SOA for reverse zone too
        if reverse:
            self.addSOA(reverseZone, name)
            self.setSOARecord(reverseZone, rec)
            self.setNSRecord(reverseZone, ns)

        if nameserverip:
            # Add a A record for the primary nameserver
            self.addRecordA(name, nameserver, nameserverip)

    def delZone(self, zone):
        """
        Delete a DNS zone with all its reverse zones
        
        @param name: the zone name to delete     
        """
        self.delRecursiveEntry("ou=" + zone + "," + self.configDns.dnsDN)
        os.unlink(os.path.join(self.configDns.bindLdapDir, zone))
        newcontent = []
        f = open(self.configDns.bindLdap, "r")
        for line in f:
            if not "/" + zone + '";' in line:
                newcontent.append(line)
        f.close()
        f = open(self.configDns.bindLdap, "w+")
        for line in newcontent:
            f.write(line)
        f.close()
        
    def addDnsZone(self, zoneName, description = None, container = None):
        """
        Add a dNSZone object in the LDAP.
        """
        if not container: container = zoneName
        # Create the container of this zone and its reverses if it does not exist
        try:
            self.addOu(container, self.configDns.dnsDN)
        except ldap.ALREADY_EXISTS:
            pass
        # Create the ou defining this zone and that will contain all records
        self.addOu(zoneName, "ou=" + container + "," + self.configDns.dnsDN)
        dn = "zoneName=" + zoneName + "," + "ou=" + zoneName + "," + "ou=" + container + "," + self.configDns.dnsDN
        entry = {
            "zoneName" : zoneName,
            "objectClass" : ["top", "dNSZone"],
            "relativeDomainName" : zoneName + ".",
            }
        if description: entry["tXTRecord"] = [description]
        attributes = [ (k,v) for k,v in entry.items() ]
        self.l.add_s(dn, attributes)

    def addSOA(self, zoneName, container = None, dnsClass = "IN"):
        if not container: container = zoneName
        dn = "relativeDomainName=@," + "ou=" + zoneName + "," + "ou=" + container + "," + self.configDns.dnsDN
        entry = {
            "zoneName" : zoneName,
            "objectClass" : ["top", "dNSZone"],
            "relativeDomainName" : "@",
            "dnsClass" : dnsClass
            }
        attributes=[ (k,v) for k,v in entry.items() ]
        self.l.add_s(dn, attributes)        

    def setSOARecord(self, zoneName, record):
        soa = self.l.search_s(self.configDns.dnsDN, ldap.SCOPE_SUBTREE, "(&(objectClass=dNSZone)(zoneName=%s)(relativeDomainName=@))" % zoneName, None)
        if soa:
            soaDN = soa[0][0]
            s = "%(nameserver)s %(emailaddr)s %(serial)s %(refresh)s %(retry)s %(expiry)s %(minimum)s" % record
            self.l.modify_s(soaDN, [(ldap.MOD_REPLACE, "sOARecord", [s])])

    def setNSRecord(self, zoneName, nameserver):
        soa = self.l.search_s(self.configDns.dnsDN, ldap.SCOPE_SUBTREE, "(&(objectClass=dNSZone)(zoneName=%s)(relativeDomainName=@))" % zoneName, None)
        if soa:
            soaDN = soa[0][0]
            self.l.modify_s(soaDN, [(ldap.MOD_REPLACE, "nSRecord", [nameserver])])
        # Also sync SOA record if there is one
        soaRecord = self.getSOARecord(zoneName)
        if soaRecord:
            soaRecord["nameserver"] = nameserver
            self.setSOARecord(zoneName, soaRecord)
            self.updateZoneSerial(zoneName)

    def setZoneDescription(self, zoneName, description):
        """
        Set a zone description using the txTRecord attribute
        """
        zone = self.getZone(zoneName)
        if zone:
            zoneDN = zone[0][0]
            if description:
                self.l.modify_s(zoneDN, [(ldap.MOD_REPLACE, "tXTRecord", [description])])
            else:
                # Just delete the txTRecord attribute
                self.l.modify_s(zoneDN, [(ldap.MOD_DELETE, "tXTRecord", None)])
            self.updateZoneSerial(zoneName)

    def getSOARecord(self, zoneName):
        """
        Return the content of the SOA record of a zone

        @rtype: dict
        """
        ret = {}
        soa = self.l.search_s(self.configDns.dnsDN, ldap.SCOPE_SUBTREE, "(&(objectClass=dNSZone)(zoneName=%s)(relativeDomainName=@))" % zoneName, ["soaRecord"])
        if soa:
            try:
                ret["nameserver"], ret["emailaddr"], ret["serial"], ret["refresh"], ret["retry"], ret["expiry"], ret["minimum"] = soa[0][1]["sOARecord"][0].split()
            except KeyError:
                pass
        return ret            

    def searchReverseZone(self, ip):
        """
        Search a convenient reverse zone for a IP
        """
        elements = ip.split(".")
        elements.pop()
        elements.reverse()
        while elements:
            rev = ".".join(elements) + self.reversePrefix
            ret = self.l.search_s(self.configDns.dnsDN, ldap.SCOPE_SUBTREE, "(&(objectClass=dNSZone)(zoneName=%s))" % rev, None)
            if ret:
                elements.reverse()
                # Return the reverse zone name and how the IPs are beginning in this zone
                return rev, ".".join(elements)
            elements.pop(0)
        return None

    def updateZoneSerial(self, zone):
        """
        Update the serial number of a zone. Needed after a zone modification.
        """
        soa = self.getSOARecord(zone)
        current = soa["serial"]
        soa["serial"] = self.computeSerial(current)
        self.setSOARecord(zone, soa)        

    def addRecordCNAME(self, zone, alias, cname, dnsClass = "IN"):
        """
        Add a canonical name record.
        The host name to which the alias is pointing must be a A record.
        CNAME chaining is not supported.

        @param zone: the DNS zone where the record is stored
        @type zone: str

        @param alias: alias pointing to the canonical name
        @type alias: str

        @param cname: CNAME to record (must be a registered A record
        @type cname: str    
        """
        # Check that the given cname is a A record
        record = self.getResourceRecord(zone, cname)
        try:
            if not "aRecord" in record[0][1]:
                raise "%s in not a A record" % cname
        except IndexError:                   
            raise "'%s' A record does not exist in the DNS zone" % cname
        # Add the CNAME record
        dn = "relativeDomainName=" + alias + "," + "ou=" + zone + "," + "ou=" + zone + "," + self.configDns.dnsDN
        entry = {
            "relativeDomainName" : alias,
            "objectClass" : ["top", "dNSZone"],
            "zoneName" : zone,
            "dNSClass" : dnsClass,
            "CNAMERecord" : cname,
        }
        attributes=[ (k,v) for k,v in entry.items() ]
        self.l.add_s(dn, attributes)
        self.updateZoneSerial(zone)
    
    def addRecordA(self, zone, hostname, ip, container = None, dnsClass = "IN"):
        """
        Add an entry for a zone and its reverse zone.

        @return: 0 if the host has been added in a reverse zone too, 1 if not
        @rtype: int
        """
        ret = 1
        if not container: container = zone
        dn = "relativeDomainName=" + hostname + "," + "ou=" + zone + "," + "ou=" + container + "," + self.configDns.dnsDN
        entry = {
            "relativeDomainName" : hostname,
            "objectClass" : ["top", "dNSZone"],
            "zoneName" : zone,
            "dNSClass" : dnsClass,
            "aRecord" : ip,
        }
        attributes=[ (k,v) for k,v in entry.items() ]
        self.l.add_s(dn, attributes)
        self.updateZoneSerial(zone)

        revZone = self.getReverseZone(zone)
        # Add host to corresponding reverse zone if there is one
        if revZone:
            # For now, we only manage a single reverse zone
            revZone = revZone[0]
            ipStart = self.translateReverse(revZone)
            # Check that the given IP can fit into the reverse zone
            if ip.startswith(ipStart):
                # Ok, add it
                ipLast = ip.replace(ipStart, "")
                elements = ipLast.split(".")
                elements.reverse()
                elements.pop() # Remove the last "."
                relative = ".".join(elements)
                dn = "relativeDomainName=" + relative + "," + "ou=" + revZone + "," + "ou=" + container + "," + self.configDns.dnsDN
                entry = {
                    "relativeDomainName" : relative,
                    "objectClass" : ["top", "dNSZone"],
                    "zoneName" : revZone,
                    "dNSClass" : dnsClass,
                    "pTRRecord" : hostname + "." + zone + ".",
                }
                attributes=[ (k,v) for k,v in entry.items() ]
                self.l.add_s(dn, attributes)
                self.updateZoneSerial(revZone)
                ret = 0
        return ret

    def delRecord(self, zone, hostname):
        """
        Remove a host from a zone.
        Remove it from the reverse zone too.
        """
        host = self.l.search_s(self.configDns.dnsDN, ldap.SCOPE_SUBTREE, "(&(objectClass=dNSZone)(zoneName=%s)(relativeDomainName=%s))" % (zone, hostname), None)
        if host:
            self.l.delete_s(host[0][0])
            self.updateZoneSerial(zone)
        revzones = self.getReverseZone(zone)
        for revzone in revzones:
            revhost = self.l.search_s(self.configDns.dnsDN, ldap.SCOPE_SUBTREE, "(&(objectClass=dNSZone)(zoneName=%s)(pTRRecord=%s))" % (revzone, hostname + "." + zone + "."), None)
            if revhost:
                self.l.delete_s(revhost[0][0])
                self.updateZoneSerial(revzone)

    def modifyRecord(self, zone, hostname, ip):
        """
        Change the IP address of a host in a zone.
        If the new IP already exists, an exception is raised.
        """
        if self.ipExists(zone, ip): raise "The IP %s has been already registered in zone %s" % (ip, zone)
        self.delRecord(zone, hostname)
        self.addRecordA(zone, hostname, ip)

    def computeSerial(self, oldSerial = ""):
        format = "%Y%m%d"
        today = time.strftime(format)
        if oldSerial.startswith(today):
            num = int(oldSerial[8:])
            num = num + 1
            if num >= 100: num = 99
            ret = today + "%02d" % num
        else:
            ret = today + "00"
        return ret 

    def translateReverse(self, revZone):
        """
        Translate a reverse zone name into a network address.
        """
        revZone = revZone.replace(self.reversePrefix, "")
        elements = revZone.split(".")
        elements.reverse()
        return ".".join(elements)

    def hostExists(self, zone, hostname):
        """
        Return true if one of these statements are true:
         - hostname is defined in the given zone
         - hostname is defined in a reverse of the given zone

        This method is useful to know if we can record a machine in a zone
        without duplicate.
        """
        search = self.l.search_s(self.configDns.dnsDN, ldap.SCOPE_SUBTREE, "(&(objectClass=dNSZone)(zoneName=%s)(relativeDomainName=%s))" % (zone, hostname), None)
        if search: return True
        revZone = self.getReverseZone(zone)
        if revZone:
            # Search host in the reverse zone
            revZone = revZone[0]
            fqdn = hostname + "." + zone + "."
            search = self.l.search_s(self.configDns.dnsDN, ldap.SCOPE_SUBTREE, "(&(objectClass=dNSZone)(zoneName=%s)(pTRRecord=%s))" % (revZone, fqdn), None)
            return len(search) > 0
        return False

    def ipExists(self, zone, ip):
        """
        Return true if one of these statements are true:
         - a hostname with the given ip is defined in the given zone
         - the ip is defined in the reverse of the given zone

        This method is useful to know if we can record a machine in a zone
        without duplicate.
        """
        search = self.l.search_s(self.configDns.dnsDN, ldap.SCOPE_SUBTREE, "(&(objectClass=dNSZone)(zoneName=%s)(aRecord=%s))" % (zone, ip), None)
        if search: return True
        revZone = self.getReverseZone(zone)
        if revZone:
            # Search IP in the reverse zone
            revZone = revZone[0]
            ipStart = self.translateReverse(revZone)
            ipLast = ip.replace(ipStart, "")
            elements = ipLast.split(".")
            elements.reverse()
            elements.pop() # Remove the last "."
            relative = ".".join(elements)
            search = self.l.search_s(self.configDns.dnsDN, ldap.SCOPE_SUBTREE, "(&(objectClass=dNSZone)(zoneName=%s)(relativeDomainName=%s))" % (revZone, relative), None)
            return len(search) > 0
        return False

    def resolve(self, zone, hostname):
        """
        Return the IP address of a host inside a zone.
        An empty string is returned if the host can't be resolved.

        @rtype: str
        """
        ret = ""
        search = self.l.search_s(self.configDns.dnsDN, ldap.SCOPE_SUBTREE, "(&(objectClass=dNSZone)(zoneName=%s)(relativeDomainName=%s))" % (zone, hostname), None)
        if search:
            try:
                ret = search[0][1]["aRecord"][0]
            except KeyError:
                pass
        return ret

    def getZoneFreeIp(self, zone, startAt = None):
        """
        Return the first available IP address of a zone.
        If startAt is given, start the search from this IP.

        If none available, return an empty string.

        @param zone: DNS zone name in LDAP
        @type zone: str

        @param startAt: IP to start search
        @type startAt: str
        """
        ret = ""
        networks = self.getZoneNetworkAddress(zone)
        # We support only one single reverse zone, that's why we do [0]
        basenetwork = networks[0]        
        dotcount = basenetwork.count(".")
        # Build a netmask
        netmask = (dotcount + 1) * 8
        # Build a quad dotted network address
        network = basenetwork + ((3 - dotcount) * ".0")
        if startAt: ip = startAt
        else: ip = network
        ip = ipNext(network, netmask, ip)
        while ip:
            if not self.ipExists(zone, ip):
                ret = ip
                break
            ip = ipNext(network, netmask, ip)
        return ret

    def getResourceRecord(self, zone, rr):
        """
        @return: a domain name resource record (RR)
        @rtype: dict
        """
        return self.l.search_s(self.configDns.dnsDN, ldap.SCOPE_SUBTREE, "(&(objectClass=dNSZone)(zoneName=%s)(relativeDomainName=%s))" % (zone, rr), None)
        

        
class DnsService(ServiceManager):

    def __init__(self, conffile = None):
        self.config = lmc.plugins.network.NetworkConfig("network", conffile)
        ServiceManager.__init__(self, self.config.dnsPidFile, self.config.dnsInit)


class DnsLogView(LogView):
    """
    Get DNS service log content.
    """

    def __init__(self):
        config = lmc.plugins.network.NetworkConfig("network")
        self.logfile = config.dnsLogFile
        self.maxElt= 200
        self.file = open(self.logfile, 'r')
        self.pattern = {
            "named-syslog" : "^(?P<b>[A-z]{3}) *(?P<d>[0-9]+) (?P<H>[0-9]{2}):(?P<M>[0-9]{2}):(?P<S>[0-9]{2}) .* named\[[0-9]+\]: (?P<extra>.*)$",
            }
