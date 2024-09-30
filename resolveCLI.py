#!/usr/bin/env python3

"""
Apache mod_python module for resolving a catalog name to a RA/dec. pair using
the name resolver service at:
https://cdsweb.u-strasbg.fr/cgi-bin/nph-sesame/-oxp/SNV

Returns an XML file with the coordinates of the target or an error.
"""

import os
import sys
import argparse

from lsl import astro


__version__ = "0.2"
__author__ = "Jayce Dowell"


def main(args):
    target = args.target
    try:
        posn = astro.resolve_name(target)
        ra = posn.ra
        pmRA = ''
        if posn.pm_ra is not None:
            pmRA =  " (%+.1f mas/yr proper motion)" % posn.pm_ra
        dec = posn.dec
        pmDec = ''
        if posn.pm_dec is not None:
            pmDec = " (%+.1f mas/yr proper motion)" % posn.pm_dec
        coordsys = 'J2000'
        service = posn.resolved_by
    except RuntimeError as e:
        ra = dec = -99
        pmRA = pmDec = ''
        coordsys = 'NA'
        service = f"Error: {str(e)}"
        
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
    
