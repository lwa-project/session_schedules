#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Apache mod_python module for resolving a catalog name to a RA/dec. pair using
the name resolver service at:
http://www1.cadc-ccda.hia-iha.nrc-cnrc.gc.ca/NameResolver/find

Returns an XML file with the coordinates of the target or an error.
"""

import os
import sys
import urllib


__version__ = "0.1"
__revision__ = "$Rev$"
__author__ = "Jayce Dowell"


def _resolveSource(name):
    """
    Resolve a source into a RA, dec pair.
    """
    
    try:
        result = urllib.urlopen('http://www1.cadc-ccda.hia-iha.nrc-cnrc.gc.ca/NameResolver/find?target=%s' % urllib.quote_plus(name))
        
        line = result.readlines()
        target = (line[0].replace('\n', '').split('='))[1]
        service = (line[1].replace('\n', '').split('='))[1]
        service = service.replace('\r', '')
        coordsys = (line[2].replace('\n', '').split('='))[1]
        coordsys = coordsys.replace('\r', '')
        ra = (line[3].replace('\n', '').split('='))[1]
        dec = (line[4].replace('\n', '').split('='))[1]
        
        ra = float(ra)
        dec = float(dec)

    except IOError:
        service = 'Download Error'
        coordsys = 'NA'
        ra = -99.99
        dec = -99.99
        
    except ValueError:
        service = 'Download Error'
        coordsys = 'NA'
        ra = -99.99
        dec = -99.99
        
    return ra, dec, coordsys, service


def main(args):
    target = ' '.join(args)
    ra, dec, coordsys, service = _resolveSource(target)
    
    print "Target: %s" % target
    print "  RA:   %.4f hours" % (ra/15.0)
    print "  Dec: %+.4f degrees" % dec
    print "  Coord. System: %s" % coordsys
    print "==="
    print "Source: %s" % service


if __name__ == "__main__":
    main(sys.argv[1:])
    