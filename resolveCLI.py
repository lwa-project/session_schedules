#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Apache mod_python module for resolving a catalog name to a RA/dec. pair using
the name resolver service at:
https://cdsweb.u-strasbg.fr/cgi-bin/nph-sesame/-oxp/SNV

Returns an XML file with the coordinates of the target or an error.
"""

import os
import sys
import math
import ephem
import urllib
from xml.etree import ElementTree


__version__ = "0.1"
__revision__ = "$Rev$"
__author__ = "Jayce Dowell"


def _resolveSource(name):
    """
    Resolve a source into a RA, dec pair.
    """
    
    try:
        result = urllib.urlopen('https://cdsweb.u-strasbg.fr/cgi-bin/nph-sesame/-oxp/SNV?%s' % urllib.quote_plus(name))
        tree = ElementTree.fromstring(result.read())
        target = tree.find('Target')
        service = target.find('Resolver')
        coords = service.find('jpos')
        
        service = service.attrib['name'].split('=', 1)[1]
        raS, decS = coords.text.split(None, 1)
        coordsys = 'J2000'
        
        ra = ephem.hours(raS) * 180/math.pi
        dec = ephem.degrees(decS) * 180/math.pi

    except (IOError, ValueError, RuntimeError) as e:
        service = "Error: %s" % str(e)
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
    