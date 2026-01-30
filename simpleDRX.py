#!/usr/bin/env python3

import os
import sys
import math
import time
import argparse
import subprocess
from datetime import date, time, datetime, timedelta, timezone

from lsl import astro
from lsl.common import sdf
from lsl.common._sdf_utils import render_file_size as _render_file_size
from lsl.misc import parser as aph


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


def load_preferences():
    """
    Load sessionGUI.py preferences for the observer info.
    """
    
    preferences = {'ObserverID': 0,
                   'ObserverFirstName': 'Your',
                   'ObserverLastName': 'Name'}
    try:
        with open(os.path.join(os.path.expanduser('~'), '.sessionGUI')) as ph:
            pl = ph.readlines()
            
        preferences = {}
        for line in pl:
            line = line.replace('\n', '')
            if len(line) < 3:
                continue
            if line[0] == '#':
                continue
            key, value = line.split(None, 1)
            preferences[key] = value
    except:
        pass
        
    return preferences


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
    tSDF = datetime.combine(args.start_date, args.start_time, tzinfo=timezone.utc)
    
    # Load in the preferences
    prefs = load_preferences()
    
    # Create the SDF
    ## Observer
    obs = sdf.Observer("%s %s" % (prefs['ObserverFirstName'], prefs['ObserverLastName']),
                       prefs['ObserverID'])
    ## Project
    proj = sdf.Project(obs, "Simple DRX Run", args.project_code)
    if 'ProjectID' in prefs and 'ProjectName' in prefs:
        if args.project_code == prefs['ProjectID']:
            proj.name = prefs['ProjectName']
    ## Session
    ses = sdf.Session("Simple DRX Run", args.session_id)
    ses.drx_beam = args.beam
    ## Observation
    if args.target.lower() == 'sun':
        drx = sdf.Solar('DRX', args.target, tSDF, args.duration, args.frequency1, args.frequency2, 7, gain=args.gain)
    elif args.target.lower() == 'jupiter':
        drx = sdf.Jovian('DRX', args.target, tSDF, args.duration, args.frequency1, args.frequency2, 7, gain=args.gain)
    elif args.target.lower() == 'moon':
        drx = sdf.Lunar('DRX', args.target, tSDF, args.duration, args.frequency1, args.frequency2, 7, gain=args.gain)
    elif args.target.startswith('topo_'):
        try:
            _, az, alt = args.target.split('_', 2)
            az, alt = float(az), float(alt)
        except (IndexError, ValueError) as e:
            raise RuntimeError(f"Failed to interpret '{args.target}' as a topocentric coordinate designator: {str(e)}")
        drx = sdf.Stepped('Stepped', args.target, tSDF, 7, is_radec=False, gain=args.gain)
        drx.append( sdf.BeamStep(az, alt, args.duration, args.frequency1, args.frequency2, is_radec=False) )
    else:
        ### Resolve the target to coordinates
        posn = astro.resolve_name(args.target)
        drx = sdf.DRX('DRX', args.target, tSDF, args.duration, posn.ra/15, posn.dec, args.frequency1, args.frequency2, 7, gain=args.gain)
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
    print("# -> Estimated data volume is %-32s #" % _render_file_size(proj.sessions[0].observations[0].dataVolume))
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
                        help='target name to observe or "topo_<azimuth>_<elevation>" for a fixed topocentric pointing')
    parser.add_argument('-b', '--beam', type=aph.positive_int, default=1,
                        help='Beam to use')
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
