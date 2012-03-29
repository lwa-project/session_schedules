#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Script to shift an SDF file in time based on the first observation.  This
could be useful for moving SDF files around in time without observer intervention.

Usage:
  shiftSDF.py <input_SDF> <output_SDF>

Options:
  None
  
$Revision$
$LastChangedBy: jdowell $
$LastChangedDate: 2012-03-21 17:14:20 -0600 (Wed, 21 Mar 2012) $
"""

import os
import sys
import pytz
import ephem
import getopt

from datetime import datetime, date, time, timedelta

from lsl.common.stations import lwa1
from lsl.transform import Time
from lsl.astro import utcjd_to_unix, MJD_OFFSET
try:
	from lsl.common import sdf
except ImportError:
	import sdf


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
	print """shiftSDF.py - The Swiss army knife of SDF time shifting utilities.  Use
this script to:
  * Move a SDF file to a new start date/time
  * Move a SDF file to a new UTC date but the same LST
  * Apply a pointing correction (currently ~430 seconds in RA) to
    the observations
  * Switch the session ID to a new value
  * Convert TRK_SOL and TRK_JOV observations to TRK_RADEC
  * Only update one of the above and leave the time alone
  * Print out the contents of the SDF file in an easy-to-digest manner

Usage: shiftSDF.py [OPTIONS] input_file output_file

Options:
-h, --help           Display this help information
-l, --lst            Run in new date, same LST mode
-d, --date           Date to use in YYYY/MM/DD format
-t, --time           Time to use in HH:MM:SS.SSS format
-s, --sid            Update session ID/New session ID value
-r, --radec          Convert TRK_SOL/TRK_JOV to TRK_RADEC
-p, --pointing       Update pointing to correct for the pointing error
-n, --no-update      Do not update the time, only apply other options
-q, --query          Query the SDF only, make no changes
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
	config['sessionID'] = None
	config['makeRADec'] = False
	config['updatePointing'] = False
	config['pointingErrorRA'] = -430 / 3600.0	# hours
	config['pointingErrorDec'] = 0 / 3600.0		# degrees

	# Read in and process the command line flags
	try:
		opts, args = getopt.getopt(args, "hld:t:s:rpnq", ["help", "lst", "date=", "time=", "sid=", "radec", "pointing", "no-update", "query"])
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
		elif opt in ('-s', '--sid'):
			config['sessionID'] = int(value)
		elif opt in ('-r', '--radec'):
			config['makeRADec'] = True
		elif opt in ('-p', '--pointing'):
			config['updatePointing'] = True
		elif opt in ('-n', '--no-update'):
			config['updateTime'] = False
		elif opt in ('-q', '--query'):
			config['queryOnly'] = True
		else:
			assert False
	
	# Add in arguments
	config['args'] = args

	# Return configuration
	return config


def getObsStartStop(obs):
	"""
	Given an observation, get the start and stop times (returned as a two-
	element tuple).
	"""
	
	# UNIX timestamp for the start
	tStart = utcjd_to_unix(obs.mjd + MJD_OFFSET)
	tStart += obs.mpm / 1000.0
	
	# UNIX timestamp for the stop
	tStop = tStart +  obs.dur / 1000.0
	
	# Conversion to a timezone-aware datetime instance
	tStart = _UTC.localize( datetime.utcfromtimestamp(tStart) )
	tStop  = _UTC.localize( datetime.utcfromtimestamp(tStop ) )
	
	# Return
	return tStart, tStop


def main(args):
	# Parse options/get file name
	config = parseOptions(args)
	
	# Load the station and objects to find the Sun and Jupiter
	observer = lwa1.getObserver()
	Sun = ephem.Sun()
	Jupiter = ephem.Jupiter()
	
	# Filenames in an easier format
	inputSDF  = config['args'][0]
	if not config['queryOnly']:
		outputSDF = config['args'][1]
	
	# Parse the input file and get the dates of the observations
	project = sdf.parseSDF(inputSDF)
	
	nObs = len(project.sessions[0].observations)
	tStart = [None,]*nObs
	for i in xrange(nObs):
		tStart[i]  = utcjd_to_unix(project.sessions[0].observations[i].mjd + MJD_OFFSET)
		tStart[i] += project.sessions[0].observations[i].mpm / 1000.0
		tStart[i]  = datetime.utcfromtimestamp(tStart[i])
		tStart[i]  = _UTC.localize(tStart[i])
	
	# Get the LST at the start
	observer.date = (min(tStart)).strftime('%Y/%m/%d %H:%M:%S')
	lst = observer.sidereal_time()
	
	# Report on the file
	print "Filename: %s" % inputSDF
	print " Project ID: %s" % project.id
	print " Session ID: %i" % project.sessions[0].id
	print " Observations appear to start at %s" % (min(tStart)).strftime(formatString)
	print " -> LST at %s for this date/time is %s" % (lwa1.name, lst)
	
	# Query only mode starts here...
	if config['queryOnly']:
		lastDur = project.sessions[0].observations[nObs-1].dur
		lastDur = timedelta(seconds=int(lastDur/1000), microseconds=(lastDur*1000) % 1000000)
		sessionDur = max(tStart) - min(tStart) + lastDur
		
		print " "
		print " Total Session Duration: %s" % sessionDur
		print " -> First observation starts at %s" % min(tStart).strftime(formatString)
		print " -> Last observation ends at %s" % (max(tStart) + lastDur).strftime(formatString)
		if project.sessions[0].observations[0].mode not in ('TBW', 'TBN'):
			drspec = 'No'
			if project.sessions[0].spcSetup[0] != 0 and project.sessions[0].spcSetup[1] != 0:
				drspec = 'Yes'
			drxBeam = project.sessions[0].drxBeam
			if drxBeam < 1:
				drxBeam = "MCS decides"
			else:
				drxBeam = "%i" % drxBeam
		print " DRX Beam: %s" % drxBeam
		print " DR Spectrometer used? %s" % drspec
		
		print " "
		print " Number of observations: %i" % nObs
		print " Observation Detail:"
		for i in xrange(nObs):
			currDur = project.sessions[0].observations[i].dur
			currDur = timedelta(seconds=int(currDur/1000), microseconds=(currDur*1000) % 1000000)
			
			print "  Observation #%i" % (i+1,)
			
			## Basic setup
			print "   Target: %s" % project.sessions[0].observations[i].target
			print "   Mode: %s" % project.sessions[0].observations[i].mode
			print "   Start:"
			print "    MJD: %i" % project.sessions[0].observations[i].mjd
			print "    MPM: %i" % project.sessions[0].observations[i].mpm
			print "    -> %s" % getObsStartStop(project.sessions[0].observations[i])[0].strftime(formatString)
			print "   Duration: %s" % currDur
			
			## DP setup
			if project.sessions[0].observations[i].mode not in ('TBW',):
				print "   Tuning 1: %.3f MHz" % (project.sessions[0].observations[i].frequency1/1e6,)
			if project.sessions[0].observations[i].mode not in ('TBW', 'TBN'):
				print "   Tuning 2: %.3f MHz" % (project.sessions[0].observations[i].frequency2/1e6,)
			if project.sessions[0].observations[i].mode not in ('TBW',):
				print "   Filter code: %i" % project.sessions[0].observations[i].filter
				
			## Comments/notes
			print "   Observer Comments: %s" % project.sessions[0].observations[i].comments
		
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
		print "Shifting observations to start at %s" % tNewStart.strftime(formatString)
		print "-> Difference of %i days, %.3f seconds" % (tShift.days, (tShift.seconds + tShift.microseconds/1000000.0),)
		print "-> LST at %s for this date/time is %s" % (lwa1.name, lst)
	
	else:
		tShift = timedelta(seconds=0)
	
	# Shift the start times and recompute the MJD and MPM values
	for i in xrange(nObs):
		tStart[i] += tShift
	
	
	#
	# Query and set the new session ID
	#
	print " "
	if config['sessionID'] is None:
		print "Enter the new session ID or return to keep current:"
		sid = raw_input('-> ')
		if len(sid) > 0:
			sid = int(sid)
		else:
			sid = project.sessions[0].id
	else:
		sid = config['sessionID']
	print "Shifting session ID from %i to %i" % (project.sessions[0].id, sid)
	project.sessions[0].id = sid
	
	
	#
	# Go! (apply the changes to the observations)
	#
	print " "
	newPOOC = []
	for i in xrange(nObs):
		print "Working on Observation #%i" % (i+1,)
		newPOOC.append("")
		
		#
		# Start MJD,MPM Shifting
		#
		if config['updateTime'] and tShift != timedelta(seconds=0):
			if len(newPOOC[-1]) != 0:
				newPOOC[-1] += ';;'
			newPOOC[-1] += 'Original MJD:%i,MPM:%i' % (project.sessions[0].observations[i].mjd, project.sessions[0].observations[i].mpm)
			
			start = tStart[i].strftime("%Z %Y %m %d %H:%M:%S.%f")
			start = start[:-3]

			utc = Time(tStart[i], format=Time.FORMAT_PY_DATE)
			mjd = int(utc.utc_mjd)
			
			utcMidnight = datetime(tStart[i].year, tStart[i].month, tStart[i].day, 0, 0, 0, tzinfo=_UTC)
			diff = tStart[i] - utcMidnight
			mpm = int(round((diff.seconds + diff.microseconds/1000000.0)*1000.0))
			
			print " Time shifting"
			print "  MJD: %8i -> %8i" % (project.sessions[0].observations[i].mjd, mjd)
			print "  MPM: %8i -> %8i" % (project.sessions[0].observations[i].mpm, mpm)
			
			project.sessions[0].observations[i].mjd = mjd
			project.sessions[0].observations[i].mpm = mpm
			project.sessions[0].observations[i].start = start
		
		#
		# Shift TRK_SOL to TRK_RADEC using the location of the Sun at the
		# center of the observation
		#
		if config['makeRADec'] and project.sessions[0].observations[i].mode == 'TRK_SOL':
			if len(newPOOC[-1]) != 0:
				newPOOC[-1] += ';;'
			newPOOC[-1] += 'Originally TRK_SOL'
			
			tStart, tStop = getObsStartStop(project.sessions[0].observations[i])
			
			# Find the mid-point of the observation
			duration = tStop - tStart
			tMid = tStart + duration // 2
			
			# Calculate the position of Jupiter at this time and convert the
			# RA value to decimal hours and the Dec. value to decimal degrees.
			observer.date = tMid.strftime(formatString)
			Sun.compute(observer)
			sRA = float(Sun.ra) * 180.0 / math.pi / 15.0
			sDec = float(Sun.dec) * 180.0 /math.pi
			
			print " Mode shifting"
			print "  Mode: %s -> TRK_RADEC" % project.sessions[0].observations[i].mode
			print "  Midpoint: %s" % tMid.strftime(formatString)
			print "  -> RA:   %9.6f hours" % sRA
			print "  -> Dec: %+10.6f degrees" % sDec
			
			# Update the observation
			oldObs = project.sessions[0].observations[i]
			newObs = sdf.DRX(oldObs.name, oldObs.target, oldObs.start, oldObs.duration, sRA, sDec, oldObs.frequency1, oldObs.frequency2, oldObs.filter, MaxSNR=oldObs.MaxSNR, comments=oldObs.comments)
			
			# Replace the observation
			project.sessions[0].observations[i] = newObs
		
		#
		# Shift TRK_JOV to TRK_RADEC using the location of Jupiter at the
		# center of the observation
		#
		if config['makeRADec'] and project.sessions[0].observations[i].mode == 'TRK_JOV':
			if len(newPOOC[-1]) != 0:
				newPOOC[-1] += ';;'
			newPOOC[-1] += 'Originally TRK_JOV'
			
			tStart, tStop = getObsStartStop(project.sessions[0].observations[i])
			
			# Find the mid-point of the observation
			duration = tStop - tStart
			tMid = tStart + duration // 2
			
			# Calculate the position of Jupiter at this time and convert the
			# RA value to decimal hours and the Dec. value to decimal degrees.
			observer.date = tMid.strftime(formatString)
			Jupiter.compute(observer)
			jRA = float(Jupiter.ra) * 180.0 / math.pi / 15.0
			jDec = float(Jupiter.dec) * 180.0 /math.pi
			
			print " Mode shifting"
			print "  Mode: %s -> TRK_RADEC" % project.sessions[0].observations[i].mode
			print "  Midpoint: %s" % tMid.strftime(formatString)
			print "  -> RA:   %9.6f hours" % jRA
			print "  -> Dec: %+10.6f degrees" % jDec
			
			# Update the observation
			oldObs = project.sessions[0].observations[i]
			newObs = sdf.DRX(oldObs.name, oldObs.target, oldObs.start, oldObs.duration, jRA, jDec, oldObs.frequency1, oldObs.frequency2, oldObs.filter, MaxSNR=oldObs.MaxSNR, comments=oldObs.comments)
			
			# Replace the observation
			project.sessions[0].observations[i] = newObs
	
		#
		# Apply the pointing correction to TRK_RADEC observations
		#
		if config['updatePointing'] and project.sessions[0].observations[i].mode == 'TRK_RADEC':
			if len(newPOOC[-1]) != 0:
				newPOOC[-1] += ';;'
			newPOOC[-1] += 'Applied pointing offset RA:%+.1fmin,Dec:%+.1farcmin' % (-config['pointingErrorRA']*60, -config['pointingErrorDec']*60)
			
			ra  = project.sessions[0].observations[i].ra  - config['pointingErrorRA']
			## Make sure RA is in bounds
			if ra < 0:
				ra += 24
			if ra >= 24:
				ra -= 24
			dec = project.sessions[0].observations[i].dec - config['pointingErrorDec']
			## Make sure dec is in bounds
			if dec > 90:
				dec = 90
			if dec < -90:
				dec = -90
			
			print " Position shifting"
			print "  RA:   %9.6f ->  %9.6f hours" % (project.sessions[0].observations[i].ra, ra)
			print "  Dec: %+10.6f -> %+10.6f degrees" % (project.sessions[0].observations[i].dec, dec)
			
			project.sessions[0].observations[i].ra  = ra
			project.sessions[0].observations[i].dec = dec
	
	
	#
	# Project office comments
	#
	# Update the project office comments with this change
	newPOSC = "Shifted SDF with shiftSDF.py (v%s, %s);;Time Shift? %s;;Mode Shift? %s;;Position Shift? %s" % (__version__, __revision__, 'Yes' if config['updateTime'] else 'No', 'Yes' if config['makeRADec'] else 'No', 'Yes' if config['updatePointing'] else 'No')
	
	if project.projectOffice.sessions[0] is None:
		project.projectOffice.sessions[0] = newPOSC
	else:
		project.projectOffice.sessions[0] += ';;%s' % newPOSC
		
	for i in xrange(nObs):
		try:
			project.projectOffice.observations[0][i] += ';;%s' % newPOOC[i]
		except Exception, e:
			print e
			project.projectOffice.observations[0][i] = '%s' % newPOOC[i]
	
	
	#
	# Save
	#
	fh = open(outputSDF, 'w')
	if not project.validate():
		# Make sure we are about to be valid
		project.validate(verbose=True)
		raise RuntimeError("Cannot validate SDF file")
			
	fh.write( project.render() )
	fh.close()


if __name__ == "__main__":
	main(sys.argv[1:])
	
