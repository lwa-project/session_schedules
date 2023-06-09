#!/usr/bin/env python3

import os
import sys
import math
import pytz
import time
import argparse
import subprocess
from xml.etree import ElementTree
from urllib.request import urlopen
from urllib.parse import urlencode, quote_plus
from datetime import date, time, datetime, timedelta

from lsl.common import sdfADP as sdf
from lsl.misc import parser as aph


_UTC = pytz.utc


def ra_conv(text):
    """
    Special conversion function for deal with RA values.
    """
    
    fields = text.split(':')
    fields = [float(f) for f in fields]
    sign = 1
    if fields[0] < 0:
        sign = -1
    fields[0] = abs(fields[0])
    
    value = 0
    for f,d in zip(fields, [1.0, 60.0, 3600.0]):
        value += (f / d)
    value *= sign
    
    if value < 0 or value >= 24:
        raise ValueError("RA value must be 0 <= RA < 24")
    else:
        return value
        
def dec_conv(text):
    """
    Special conversion function for dealing with dec. values.
    """
    
    fields = text.split(':')
    fields = [float(f) for f in fields]
    sign = 1
    if fields[0] < 0:
        sign = -1
    fields[0] = abs(fields[0])
    
    value = 0
    for f,d in zip(fields, [1.0, 60.0, 3600.0]):
        value += (f / d)
    value *= sign
    
    if value < -90 or value > 90:
        raise ValueError("Dec values must be -90 <= dec <= 90")
    else:
        return value


def resolve(target_name):
    try:
        result = urlopen('https://cdsweb.u-strasbg.fr/cgi-bin/nph-sesame/-oxp/SNV?%s' % quote_plus(target_name))
        tree = ElementTree.fromstring(result.read())
        target = tree.find('Target')
        service = target.find('Resolver')
        coords = service.find('jpos')
        
        service = service.attrib['name'].split('=', 1)[1]
        raS, decS = coords.text.split(None, 1)
    except (IOError, ValueError, AttributeError, RuntimeError):
        raise RuntimeError("Failed to resolve '%s'" % target_name)
        
    return ra_conv(raS), dec_conv(decS)


def main(args):
    # Set the start date/time
    y, m, d = args.start_date.split('/', 2)
    args.start_date = date(int(y,10), int(m,10), int(d,10))
    h, m, s = args.start_time.split(':', 2)
    us = int((float(s) - int(float(s)))*1e6)
    s = int(float(s))
    if us >= 1000000:
        us -= 1000000
        s += 1
    args.start_time = time(int(h,10), int(m,10), s, us)
    tSDF = datetime.combine(args.start_date, args.start_time, tzinfo=_UTC)
    
    # Resolve the target to coordinates
    ra, dec = resolve(args.target)
    
    # Create the SDF
    ## Observer
    obs = sdf.Observer("Your Name",  0)
    ## Project
    proj = sdf.Project(obs, "Simple DRX Run", args.project_code)
    ## Session
    ses = sdf.Session("Simple DRX Run", args.session_id)
    ## Observation
    drx = sdf.DRX('DRX', args.target, tSDF, args.duration, ra, dec, args.frequency1, args.frequency2, 7, gain=args.gain)
    ses.append(drx)
    proj.append(ses)
    
    # Validate and save
    try:
        filecontents = proj.render()
    except RuntimeError:
        print("ERROR: Invalid parameters:")
        proj.validate(verbose=True)
        sys.exit(1)
        
    print('################################################################')
    print('# Be sure to fill in your observer and title information below #')
    print("# -> Estimated data volume is %-32s #" % proj._render_file_size(proj.sessions[0].observations[0].dataVolume))
    print('################################################################')
    print(filecontents)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description='script to build a simple DRX SDF',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
        )
    parser.add_argument('project_code', type=str,
                        help='project code')
    parser.add_argument('session_id', type=int,
                        help='session ID')
    parser.add_argument('start_date', type=aph.date,
                        help='observation UTC start date; YYYY/MM/DD)')
    parser.add_argument('start_time', type=aph.time,
                        help='observation UTC start time; HH:MM:SS[.sss]')
    parser.add_argument('target', type=str,
                        help='target name to observe')
    parser.add_argument('-1', '--frequency1', type=aph.frequency, default='38.1MHz',
                        help='Tuning 1 center frequency')
    parser.add_argument('-2', '--frequency2', type=aph.frequency, default='74.05MHz',
                        help='Tuning 2 center frequency')
    parser.add_argument('-g', '--gain', type=int, default=6,
                        help='DRX gain')
    parser.add_argument('-d', '--duration', type=aph.positive_float, default=1800,
                        help='observation duration in seconds')
    args = parser.parse_args()
    main(args)
