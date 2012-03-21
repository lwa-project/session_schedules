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
$LastChangedBy$
$LastChangedDate$
"""

import os
import sys
import pytz

from datetime import datetime

from lsl.common.stations import lwa1
from lsl.transform import Time
from lsl.astro import utcjd_to_unix, MJD_OFFSET
try:
	from lsl.common import sdf
except ImportError:
	import sdf


_UTC = pytz.utc
formatString = '%Y/%m/%d %H:%M:%S.%f %Z'


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
			
	tNewStart = _UTC.localize(tNewStart)
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
	
