#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Script to shift an SDF file in time based on the first observation.  This scipts
keeps track of the LST and will shift an observation to the same LST on a new UTC
date.

Usage:
  shiftSDFLST.py <input_SDF> <output_SDF>

Options:
  None
  
$Revision$
$LastChangedBy$
$LastChangedDate$
"""

import os
import sys
import pytz

from datetime import datetime, timedelta

from lsl.common.stations import lwa1
from lsl.transform import Time
from lsl.astro import utcjd_to_unix, MJD_OFFSET
try:
	from lsl.common import sdf
except ImportError:
	import sdf


_UTC = pytz.utc
formatString = '%Y/%m/%d %H:%M:%S.%f %Z'


solarDay    = timedelta(seconds=24*3600, microseconds=0)
siderealDay = timedelta(seconds=23*3600+56*60+4, microseconds=91000)
siderealRegression = solarDay - siderealDay

print solarDay, siderealDay, siderealRegression


def main(args):
	observer = lwa1.getObserver()
	inputSDF  = args[0]
	outputSDF = args[1]
	
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
	
	print "File '%s' appears to start at %s" % (inputSDF, (min(tStart)).strftime(formatString))
	print "-> LST at %s is %s" % (lwa1.name, lst)
	
	# Get the few start date/time in UTC and report on the difference
	print " "
	print "Enter the new UTC start date:"
	tNewStart = raw_input('YYYY/MM/DD-> ')
	try:
		tNewStart = datetime.strptime(tNewStart+min(tStart).strftime(' %H:%M:%S'), '%Y/%m/%d %H:%M:%S')
	except Exception, e:
		print "Error: %s" % str(e)
		sys.exit(1)
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
	tShift = tNewStart - min(tStart)
	
	# Get the LST at the new start
	observer.date = (tNewStart).strftime('%Y/%m/%d %H:%M:%S')
	lst = observer.sidereal_time()
	
	print " "
	print "Shifting observations to %s (difference of %i days, %.3f seconds)" % (tNewStart.strftime(formatString), tShift.days, (tShift.seconds + tShift.microseconds/1000000.0),)
	print "-> LST at %s is %s" % (lwa1.name, lst)
	
	# Shift the start times and recompute the MJD and MPM values
	for i in xrange(nObs):
		tStart[i] += tShift
	
	print " "
	# Update the observations
	for i in xrange(nObs):
		start = tStart[i].strftime("%Z %Y %m %d %H:%M:%S.%f")
		start = start[:-3]

		utc = Time(tStart[i], format=Time.FORMAT_PY_DATE)
		mjd = int(utc.utc_mjd)
		
		utcMidnight = datetime(tStart[i].year, tStart[i].month, tStart[i].day, 0, 0, 0, tzinfo=_UTC)
		diff = tStart[i] - utcMidnight
		mpm = int(round((diff.seconds + diff.microseconds/1000000.0)*1000.0))
		
		print "Working on Observations #%i" % (i+1,)
		print " MJD: %8i -> %8i" % (project.sessions[0].observations[i].mjd, mjd)
		print " MPM: %8i -> %8i" % (project.sessions[0].observations[i].mpm, mpm)
		
		project.sessions[0].observations[i].mjd = mjd
		project.sessions[0].observations[i].mpm = mpm
		project.sessions[0].observations[i].start = start

	# Save
	fh = open(outputSDF, 'w')
	fh.write( project.render() )
	fh.close()


if __name__ == "__main__":
	main(sys.argv[1:])
	
