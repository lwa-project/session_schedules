#!/usr/bin/env python3

import os
import sys
import math
import pytz
import time
import argparse
import subprocess
from datetime import date, time, datetime, timedelta

from lsl.common.adp import fS
from lsl.common import sdfADP as sdf
from lsl.common._sdf_utils import render_file_size as _render_file_size
from lsl.misc import parser as aph


_UTC = pytz.utc


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
    tSDF = datetime.combine(args.start_date, args.start_time, tzinfo=_UTC)
    
    # Convert the duration to a number of samples at fS
    samples = int(round(args.duration * fS))
    print(samples)
    
    # Load in the preferences
    prefs = load_preferences()
    
    # Create the SDF
    ## Observer
    obs = sdf.Observer("%s %s" % (prefs['ObserverFirstName'], prefs['ObserverLastName']),
                       prefs['ObserverID'])
    ## Project
    proj = sdf.Project(obs, "Simple TBF Run", args.project_code)
    if 'ProjectID' in prefs and 'ProjectName' in prefs:
        if args.project_code == prefs['ProjectID']:
            proj.name = prefs['ProjectName']
    ## Session
    ses = sdf.Session("Simple TBF Run", args.session_id)
    ses.drx_beam = 1
    ## Observation
    tbf = sdf.TBF('TBF', 'TBF', tSDF, args.frequency1, args.frequency2, 7, samples)
    ses.append(tbf)
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
        description='script to build a simple TBF SDF',
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
    parser.add_argument('-1', '--frequency1', type=aph.frequency, default='38.1MHz',
                        help='Tuning 1 center frequency')
    parser.add_argument('-2', '--frequency2', type=aph.frequency, default='74.05MHz',
                        help='Tuning 2 center frequency')
    parser.add_argument('-d', '--duration', type=aph.positive_float, default=0.25,
                        help='observation duration in seconds')
    args = parser.parse_args()
    main(args)
