#!/usr/bin/env python

"""
Wrapper around the validator.py module to make it useable from the
command line.
""" 

import os
import re
import sys
import math
import pytz
import ephem
import argparse
import tempfile
import subprocess

from lsl.common import sdf, sdfADP
from lsl.common.stations import lwa1
from lsl.astro import utcjd_to_unix, MJD_OFFSET


_tpss_base = os.path.join(os.path.dirname(__file__), 'tpss')


def main(args):
    # Set the TPSS and SDF versions to use
    _tpss = '%s-lwa1' % _tpss_base
    _sdf = sdf
    if args.lwasv:
        _tpss = '%s-lwasv' % _tpss_base
        _sdf = sdfADP
        
    # Read the contents of the temporary SD file into a list so that we can examine
    # the file independently of the parser
    fh = open(args.filename, 'r')
    file = fh.readlines()
    fh.close()
    
    # Run the parser/validator.  This uses a copy of Steve's tpss program.  tpss is run
    # to level to to make sure that the file is valid.  If the exit status is not '2', 
    # the file is taken to be invalid.
    try:
        validator = subprocess.Popen([_tpss, args.filename, '2', '0'], bufsize=-1, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stdout, stderr = validator.communicate()
        status = validator.wait()
    except OSError as e:
        stdout, stderr = 'TPSS not used', ''
        status = 2
    if status == 2:
        valid = True
    else:
        valid = False
        
    # Recover the version of tpss used for the parsing using the first line.  
    # The version number is the first number in brackets before the forward 
    # slash, i.e., [version/pid] ...
    tpssVersion = stdout.split('\n')[0].split('/')[0].replace('[', '')
    
    # Run through the output of tpss to split the lines into a list that makes 
    # them easier to display in the browser.  In the process, align the tpss
    # output with the input text so that we know were errors are if we run into
    # them.
    tpssRE = re.compile(r"""(?P<keyword>[A-Z0-9_\+])='(?P<value>.*)'""")
    sdfRE  = re.compile(r"""(?P<keyword>[A-Z0-9_\+])[ \t]+(?P<value>.*)""")
    
    fileIndex = 0
    numObsP = 0
    totDurP = 0
    output = []
    errors = []
    lineNumbers = []
    for line in stdout.split('\n')[4:]:
        # Match the current tpss output line
        mtch = tpssRE.search(line)
        commentLine = False
        if mtch is None:
            lineNumbers.append(-1)
            commentLine = True
            
        if not commentLine:
            # Match the current  file in the file.  If it doesn't match, try again
            # until it does.
            mtch2 = sdfRE.search(file[fileIndex])
            while mtch2 is None or (mtch2.group('keyword') != mtch.group('keyword') and mtch2.group('value') != mtch.group('value')):
                fileIndex += 1
                mtch2 = sdfRE.search(file[fileIndex])
            # If we've made it this far we have matched
            lineNumbers.append(fileIndex+1)
            
        # Trim off the tpss version and pid values from the lines and save them
        try:
            junk, line = line.split(None, 1)
        except ValueError:
            break
        output.append(line)
        
        # Check for observations, durations, and fatal errors in the tpss output
        if output[-1].find('Parsing Defined Observation') != -1:
            numObsP += 1
            
        if output[-1].find('OBS_DUR=') != -1:
            totDurP += int((line.split())[-1])
            
        if output[-1].find('FATAL') != -1:
            junk, message = output[-1].split('FATAL: ', 1)
            errors.append( {'line': lineNumbers[-2], 'message': message} )
            
    # Parse the file into a sdf.Project instance
    project = _sdf.parse_sdf(args.filename)
    
    # Deal with a potentiall un-runnable TPSS
    if tpssVersion == 'TPSS not used':
        numObsP = len(project.sessions[0].observations)
        totDurP = sum([obs.dur for obs in project.sessions[0].observations])
        
    # Build a couple of dictionaries to lump everything together
    tpss = {'version': tpssVersion, 'output': output, 'valid': valid, 'errors': errors, 
            'numObs': numObsP, 'totDur': totDurP}
    
    # Report
    if tpss['valid']:
        print("Congratualtions, you have a valid SDF file.")
    else:
        print("There are 1 or more errors in your SDF file.")
        for err in tpss['errors']:
            print("On line %i: %s" % (err['line'], err['message']))
    print(" ")
    
    print("Summary:")
    print("Type of observations: %s" % project.sessions[0].observations[0].mode)
    print("Total number of parsed observations: %i" % tpss['numObs'])
    print("Total observing time: %.3f seconds" % (tpss['totDur'] / 1000.0,))
    print("TPSS version used for validation: %s" % tpss['version'])
    print(" ")
    
    if project.sessions[0].observations[0].mode not in ('TBW', 'TBN'):
        print("Source List:")
        for obs in project.sessions[0].observations:
            if obs.mode == 'TRK_RADEC':
                print("%s at RA: %.3f hours, Dec.: %+.3f degrees is visible for %i%% of the observation" % (obs.target, obs.ra, obs.dec, obs.computeVisibility() * 100))
            if obs.mode == 'TRK_SOL':
                print("Sun is visible for %i%% of the observation" % (obs.computeVisibility() * 100,))
            if obs.mode == 'TRK_JOV':
                print("Jupiter is visible for %i%% of the observation" % (obs.computeVisibility() * 100,))
            if obs.mode == 'STEPPED':
                print("Steps are:")
                for step in obs.steps:
                    if step.RADec:
                        print("  RA: %.3f hours, Dec.: %+.3f degrees" % (step.c1, step.c2))
                    else:
                        print(" azimuth: %.3f degrees, elevation: %.3f degrees" % (step.c1, step.c2))
                print("Combined visibility for all steps is %i%%." % (obs.computeVisibility() * 100,))
        print(" ")
        
    print("Validator Output:")
    for line in output:
        print(line)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description='off-line version of the LWA session definition file validator', 
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        )
    parser.add_argument('filename', type=str, 
                        help='SDF file to validate')
    parser.add_argument('-s', '--lwasv', action='store_true', 
                        help='validate for LWA-SV instead of LWA1')
    args = parser.parse_args()
    main(args)
    
