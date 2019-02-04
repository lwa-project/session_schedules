#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Script to shift an IDF file in time based on the first scan.  This
could be useful for moving IDF files around in time without observer intervention.

Usage:
shiftIDF.py <input_IDF> <output_IDF>

Options:
None

$Revision$
$LastChangedBy: jdowell $
$LastChangedDate: 2012-03-21 17:14:20 -0600 (Wed, 21 Mar 2012) $
"""

import os
import sys
import pytz
import math
import ephem
import getopt

from datetime import datetime, date, time, timedelta

import lsl
from lsl import astro
from lsl.common import stations
from lsl.transform import Time
from lsl.astro import utcjd_to_unix, MJD_OFFSET
try:
    from lsl.common import idf
except ImportError:
    import idf


__version__ = "0.1"
__revision__ = "$Rev$"

# Date/time manipulation
_UTC = pytz.utc
formatString = '%Y/%m/%d %H:%M:%S.%f %Z'

# LST manipulation
solarDay    = timedelta(seconds=24*3600, microseconds=0)
siderealDay = timedelta(seconds=23*3600+56*60+4, microseconds=91000)
siderealRegression = solarDay - siderealDay


def usage(exitCode=None):
    print """shiftIDF.py - The Swiss army knife of IDF time shifting utilities.  Use
this script to:
 * Move a IDF file to a new start date/time
 * Move a IDF file to a new UTC date but the same LST
 * Switch the run ID to a new value
 * Only update one of the above and leave the time alone
 * Print out the contents of the IDF file in an easy-to-digest manner

Usage: shiftIDF.py [OPTIONS] input_file [output_file]

Options:
-h, --help           Display this help information
-l, --lst            Run in new date, same LST mode
-d, --date           Date to use in YYYY/MM/DD format
-t, --time           Time to use in HH:MM:SS.SSS format
-r, --rid            Update run ID/New run ID value
-n, --no-update      Do not update the time, only apply other options
-q, --query          Query the IDF only, make no changes

Note:
* If an output file is not specified, one will be automatically
    determined.
* The -t and -l options are mutually exclusive.
"""

    if exitCode is not None:
        sys.exit(exitCode)
    else:
        return True


def parseOptions(args):
    config = {}
    config['queryOnly'] = False
    config['lstMode'] = False
    config['updateTime'] = True
    config['time'] = None
    config['date'] = None
    config['runID'] = None

    # Read in and process the command line flags
    try:
        opts, args = getopt.getopt(args, "hld:t:r:nq", ["help", "lst", "date=", "time=", "rid=", "no-update", "query"])
    except getopt.GetoptError, err:
        # Print help information and exit:
        print str(err) # will print something like "option -a not recognized"
        usage(exitCode=2)
    
    # Work through opts
    for opt, value in opts:
        if opt in ('-h', '--help'):
            usage(exitCode=0)
        elif opt in ('-l', '--lst'):
            config['lstMode'] = True
        elif opt in ('-d', '--date'):
            try:
                fields = value.split('/', 2)
                fields = [int(f) for f in fields]
                config['date'] = date(fields[0], fields[1], fields[2])
            except ValueError:
                raise RuntimeError("Unknown date: %s" % value)
        elif opt in ('-t', '--time'):
            try:
                fields = value.split(':', 2)
                fields = [int(fields[0]), int(fields[1]), int(float(fields[2])), int((float(fields[2])*1e6) % 1000000)]
                config['time'] = time(fields[0], fields[1], fields[2], fields[3])
            except ValueError:
                raise RuntimeError("Unknown time: %s" % value)
        elif opt in ('-r', '--rid'):
            config['runID'] = int(value)
        elif opt in ('-n', '--no-update'):
            config['updateTime'] = False
        elif opt in ('-q', '--query'):
            config['queryOnly'] = True
        else:
            assert False
            
    # Add in arguments
    config['args'] = args
    
    # Validate
    if config['time'] is not None and config['lstMode']:
        raise RuntimeError("Specifying a time and LST shifting are mutually exclusive")
    if len(config['args']) not in (1, 2):
        raise RuntimeError("Must specify a IDF file")
        
    # Return configuration
    return config


def getScanStartStop(scn):
    """
    Given an scan, get the start and stop times (returned as a two-
    element tuple).
    """
    
    # UNIX timestamp for the start
    tStart = utcjd_to_unix(scn.mjd + MJD_OFFSET)
    tStart += scn.mpm / 1000.0
    
    # UNIX timestamp for the stop
    tStop = tStart +  scn.dur / 1000.0
    
    # Conversion to a timezone-aware datetime instance
    tStart = _UTC.localize( datetime.utcfromtimestamp(tStart) )
    tStop  = _UTC.localize( datetime.utcfromtimestamp(tStop ) )
    
    # Return
    return tStart, tStop


def main(args):
    # Parse options/get file name
    config = parseOptions(args)
    
    # Filenames in an easier format - input
    inputIDF  = config['args'][0]
    
    # Parse the input file and get the dates of the scans
    station = stations.lwa1
    project = idf.parseIDF(inputIDF)
    
    # Load the station and objects to find the Sun and Jupiter
    observer = station.getObserver()
    Sun = ephem.Sun()
    Jupiter = ephem.Jupiter()
    
    nObs = len(project.runs[0].scans)
    tStart = [None,]*nObs
    for i in xrange(nObs):
        tStart[i]  = utcjd_to_unix(project.runs[0].scans[i].mjd + MJD_OFFSET)
        tStart[i] += project.runs[0].scans[i].mpm / 1000.0
        tStart[i]  = datetime.utcfromtimestamp(tStart[i])
        tStart[i]  = _UTC.localize(tStart[i])
        
    # Get the LST at the start
    observer.date = (min(tStart)).strftime('%Y/%m/%d %H:%M:%S')
    lst = observer.sidereal_time()
    
    # Report on the file
    print "Filename: %s" % inputIDF
    print " Project ID: %s" % project.id
    print " Run ID: %i" % project.runs[0].id
    print " Scans appear to start at %s" % (min(tStart)).strftime(formatString)
    print " -> LST at %s for this date/time is %s" % (station.name, lst)
    
    # Filenames in an easier format - output
    if not config['queryOnly']:
        try:
            outputIDF = config['args'][1]
        except IndexError:
            outputIDF  = None
            
    # Query only mode starts here...
    if config['queryOnly']:
        lastDur = project.runs[0].scans[nObs-1].dur
        lastDur = timedelta(seconds=int(lastDur/1000), microseconds=(lastDur*1000) % 1000000)
        runDur = max(tStart) - min(tStart) + lastDur
        
        print " "
        print " Total Run Duration: %s" % runDur
        print " -> First scan starts at %s" % min(tStart).strftime(formatString)
        print " -> Last scan ends at %s" % (max(tStart) + lastDur).strftime(formatString)
        print " Correlator Setup:"
        print " -> %i channels" % project.runs[0].corr_channels
        print " -> %.3f s integration time" % project.runs[0].corr_inttime
        print " -> %s output polarization basis" % project.runs[0].corr_basis
        
        print " "
        print " Number of scans: %i" % nObs
        print " Scan Detail:"
        for i in xrange(nObs):
            currDur = project.runs[0].scans[i].dur
            currDur = timedelta(seconds=int(currDur/1000), microseconds=(currDur*1000) % 1000000)
            
            print "  Scan #%i" % (i+1,)
            
            ## Basic setup
            print "   Target: %s" % project.runs[0].scans[i].target
            print "   Intent: %s" % project.runs[0].scans[i].intent
            print "   Start:"
            print "    MJD: %i" % project.runs[0].scans[i].mjd
            print "    MPM: %i" % project.runs[0].scans[i].mpm
            print "    -> %s" % getScanStartStop(project.runs[0].scans[i])[0].strftime(formatString)
            print "   Duration: %s" % currDur
            
            ## DP setup
            print "   Tuning 1: %.3f MHz" % (project.runs[0].scans[i].frequency1/1e6,)
            print "   Tuning 2: %.3f MHz" % (project.runs[0].scans[i].frequency2/1e6,)
            print "   Filter code: %i" % project.runs[0].scans[i].filter
            
            ## Comments/notes
            print "   Observer Comments: %s" % project.runs[0].scans[i].comments
            
        # Valid?
        print " "
        try:
            if project.validate():
                print " Valid?  Yes"
            else:
                print " Valid?  No"
        except:
            print " Valid?  No"
            
        # And then exits
        sys.exit()
        
    #
    # Query the time and compute the time shifts
    #
    if config['updateTime']:
        # Get the new start date/time in UTC and report on the difference
        if config['lstMode']:
            if config['date'] is None:
                print " "
                print "Enter the new UTC start date:"
                tNewStart = raw_input('YYYY/MM/DD-> ')
                try:
                    fields = tNewStart.split('/', 2)
                    fields = [int(f) for f in fields]
                    tNewStart = date(fields[0], fields[1], fields[2])
                    tNewStart = datetime.combine(tNewStart, min(tStart).time())
                except Exception, e:
                    print "Error: %s" % str(e)
                    sys.exit(1)
                    
            else:
                tNewStart = datetime.combine(config['date'], min(tStart).time())
                
            tNewStart = _UTC.localize(tNewStart)
            
            # Figure out a new start time on the correct day
            diff = ((tNewStart - min(tStart)).days) * siderealRegression
            ## Try to make sure that the timedelta object is less than 1 day
            while diff.days > 0:
                diff -= siderealDay
            while diff.days < -1:
                diff += siderealDay
            ## Come up with the new start time
            siderealShift = tNewStart - diff
            ## Another check to make sure we are are the right day
            if siderealShift.date() < tNewStart.date():
                siderealShift += siderealDay
            if siderealShift.date() > tNewStart.date():
                siderealShift -= siderealDay
            ## And yet another one to deal with the corner case that scan starts at ~UT 00:00
            if min(tStart) == siderealShift:
                newSiderealShift1 = siderealShift + siderealDay
                newSiderealShift2 = siderealShift - siderealDay
                if newSiderealShift1.date() == tNewStart.date():
                    siderealShift = newSiderealShift1
                elif newSiderealShift2.date() == tNewStart.date():
                    siderealShift = newSiderealShift2
            tNewStart = siderealShift
            
        else:
            if config['date'] is None or config['time'] is None:
                print " "
                print "Enter the new UTC start date/time:"
                tNewStart = raw_input('YYYY/MM/DD HH:MM:SS.SSS -> ')
                try:
                    tNewStart = datetime.strptime(tNewStart, '%Y/%m/%d %H:%M:%S.%f')
                except ValueError:
                    try:
                        tNewStart = datetime.strptime(tNewStart, '%Y/%m/%d %H:%M:%S')
                    except Exception, e:
                        print "Error: %s" % str(e)
                        sys.exit(1)
                        
            else:
                tNewStart = datetime.combine(config['date'], config['time'])
            
            tNewStart = _UTC.localize(tNewStart)
            
        # Get the new shift needed to translate the old times to the new times
        tShift = tNewStart - min(tStart)
        
        # Get the LST at the new start
        observer.date = (tNewStart).strftime('%Y/%m/%d %H:%M:%S')
        lst = observer.sidereal_time()
        
        print " "
        print "Shifting scans to start at %s" % tNewStart.strftime(formatString)
        print "-> Difference of %i days, %.3f seconds" % (tShift.days, (tShift.seconds + tShift.microseconds/1000000.0),)
        print "-> LST at %s for this date/time is %s" % (station.name, lst)
        if tShift.days == 0 and tShift.seconds == 0 and tShift.microseconds == 0:
            print " "
            print "The current shift is zero.  Do you want to continue anyways?"
            yesNo = raw_input("-> [y/N] ")
            if yesNo not in ('y', 'Y'):
                sys.exit()
                
    else:
        tShift = timedelta(seconds=0)
        
    # Shift the start times and recompute the MJD and MPM values
    for i in xrange(nObs):
        tStart[i] += tShift
        
    #
    # Query and set the new run ID
    #
    print " "
    if config['runID'] is None:
        print "Enter the new run ID or return to keep current:"
        sid = raw_input('-> ')
        if len(sid) > 0:
            sid = int(sid)
        else:
            sid = project.runs[0].id
    else:
        sid = config['runID']
    print "Shifting run ID from %i to %i" % (project.runs[0].id, sid)
    project.runs[0].id = sid
    
    #
    # Go! (apply the changes to the scans)
    #
    print " "
    newPOOC = []
    for i in xrange(nObs):
        print "Working on Scan #%i" % (i+1,)
        newPOOC.append("")
        
        #
        # Start MJD,MPM Shifting
        #
        if config['updateTime'] and tShift != timedelta(seconds=0):
            if len(newPOOC[-1]) != 0:
                newPOOC[-1] += ';;'
            newPOOC[-1] += 'Original MJD:%i,MPM:%i' % (project.runs[0].scans[i].mjd, project.runs[0].scans[i].mpm)
            
            start = tStart[i].strftime("%Z %Y %m %d %H:%M:%S.%f")
            start = start[:-3]

            utc = Time(tStart[i], format=Time.FORMAT_PY_DATE)
            mjd = int(utc.utc_mjd)
            
            utcMidnight = datetime(tStart[i].year, tStart[i].month, tStart[i].day, 0, 0, 0, tzinfo=_UTC)
            diff = tStart[i] - utcMidnight
            mpm = int(round((diff.seconds + diff.microseconds/1000000.0)*1000.0))
            
            print " Time shifting"
            print "  MJD: %8i -> %8i" % (project.runs[0].scans[i].mjd, mjd)
            print "  MPM: %8i -> %8i" % (project.runs[0].scans[i].mpm, mpm)
            
            project.runs[0].scans[i].mjd = mjd
            project.runs[0].scans[i].mpm = mpm
            project.runs[0].scans[i].start = start
            
    #
    # Project office comments
    #
    # Update the project office comments with this change
    newPOSC = "Shifted IDF with shiftIDF.py (v%s, %s);;Time Shift? %s" % (__version__, __revision__, 'Yes' if config['updateTime'] else 'No')
    
    if project.projectOffice.runs[0] is None:
        project.projectOffice.runs[0] = newPOSC
    else:
        project.projectOffice.runs[0] += ';;%s' % newPOSC
        
    for i in xrange(nObs):
        try:
            project.projectOffice.scans[0][i] += ';;%s' % newPOOC[i]
        except Exception, e:
            print e
            project.projectOffice.scans[0][i] = '%s' % newPOOC[i]
            
    #
    # Save
    #
    if outputIDF is None:
        pID = project.id
        rID = project.runs[0].id
        foStart = min(tStart)
        outputIDF = '%s_%s_%s_%04i.idf' % (pID, foStart.strftime('%y%m%d'), foStart.strftime('%H%M'), rID)
        
    print " "
    print "Saving to: %s" % outputIDF
    fh = open(outputIDF, 'w')
    if not project.validate():
        # Make sure we are about to be valid
        project.validate(verbose=True)
        raise RuntimeError("Cannot validate IDF file")
        
    fh.write( project.render() )
    fh.close()


if __name__ == "__main__":
    main(sys.argv[1:])
    
