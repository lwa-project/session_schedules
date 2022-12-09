#!/usr/bin/env python3

"""
Apache mod_python module for resolving a catalog name to a RA/dec. pair using
the name resolver service at:
https://cdsweb.u-strasbg.fr/cgi-bin/nph-sesame/-oxp/SNV

Returns an XML file with the coordinates of the target or an error.
"""

# Python2 compatibility
from __future__ import print_function, division
 
import os
import sys
import math
import ephem
try:
    from urllib2 import urlopen
    from urllib import urlencode, quote_plus
except ImportError:
    from urllib.request import urlopen
    from urllib.parse import urlencode, quote_plus
import argparse
from xml.etree import ElementTree


__version__ = "0.2"
__author__ = "Jayce Dowell"


def _resolveSource(name):
    """
    Resolve a source into a RA, dec pair.
    """
    
    try:
        result = urlopen('https://cdsweb.u-strasbg.fr/cgi-bin/nph-sesame/-oxp/SNV?%s' % quote_plus(name))
        tree = ElementTree.fromstring(result.read())
        target = tree.find('Target')
        service = target.find('Resolver')
        coords = service.find('jpos')
        try:
            pm = service.find('pm')
        except Exception as e:
            pm = None
            
        service = service.attrib['name'].split('=', 1)[1]
        raS, decS = coords.text.split(None, 1)
        coordsys = 'J2000'
        if pm is not None:
            pmRA = float(pm.find('pmRA').text)
            pmDec = float(pm.find('pmDE').text)
        else:
            pmRA = ''
            pmDec = ''
            
        ra = ephem.hours(raS) * 180/math.pi
        dec = ephem.degrees(decS) * 180/math.pi
        
    except (IOError, ValueError, RuntimeError) as e:
        service = "Error: %s" % str(e)
        coordsys = 'NA'
        ra = -99.99
        dec = -99.99
        pmRA = ''
        pmDec = ''
            
    return ra, dec, coordsys, service, pmRA, pmDec


def main(args):
    target = args.target
    ra, dec, coordsys, service, pmRA, pmDec = _resolveSource(target)
    if pmRA != '':
        pmRA =  " (+%.1f mas/yr proper motion)" % pmRA
    if pmDec != '':
        pmDec = " (+%.1f mas/yr proper motion)" % pmDec
        
    print("Target: %s" % target)
    print("  RA:   %.4f hours%s" % (ra/15.0, pmRA))
    print("  Dec: %+.4f degrees%s" % (dec, pmDec))
    print("  Coord. System: %s" % coordsys)
    print("===")
    print("Source: %s" % service)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description='resolve a catalog name to a RA/dec. pair using the CDS Sesame service', 
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
        )
    parser.add_argument('target', type=str, 
                        help='target name')
    args = parser.parse_args()
    main(args)
    
