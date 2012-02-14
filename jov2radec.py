#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Short script to take in a SDF file and convert the TRK_JOV observations
to TRK_RADEC with the RA/Dec of Jupiter set for the center of the observation.

Usage:
  jov2radec.py <input_SDF> <output_SDF>

Options:
  None

$Rev$
$LastChangedBy$
$LastChangedDate$
"""

import os
import sys
import math
import pytz
import ephem

from datetime import datetime

from lsl.common.stations import lwa1
from lsl.astro import utcjd_to_unix, MJD_OFFSET
try:
	from lsl.common import sdf
except ImportError:
	import sdf


_UTC = pytz.utc
formatString = '%Y/%m/%d %H:%M:%S'


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
	observer = lwa1.getObserver()
	Jupiter = ephem.Jupiter()
	inputSDF  = args[0]
	outputSDF = args[1]
	
	# Parse the input file and get the dates of the observations
	project = sdf.parseSDF(inputSDF)
	
	# Check all of the observations for TRK_JOV
	nObs = len(project.sessions[0].observations)
	changed = False
	for i in xrange(nObs):
		if project.sessions[0].observations[i].mode == 'TRK_JOV':
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
			
			# Update the observation
			oldObs = project.sessions[0].observations[i]
			newObs = sdf.DRX(oldObs.name, oldObs.target, oldObs.start, oldObs.duration, jRA, jDec, oldObs.frequency1, oldObs.frequency2, oldObs.filter, MaxSNR=oldObs.MaxSNR, comments=oldObs.comments)
			
			# Replace the observation
			changed = True
			project.sessions[0].observations[i] = newObs
	
	# See if we've done anything
	if not changed:
		print "No TRK_JOV observations found, nothing to be done."
		sys.exit()
	
	# Update the project office comments with this change
	if project.projectOffice.sessions[0] is None:
		project.projectOffice.sessions[0] = 'Shifted TRK_JOV to TRK_RADEC'
	else:
		project.projectOffice.sessions[0] += '; Shifted TRK_JOV to TRK_RADEC'
	
	# Save
	fh = open(outputSDF, 'w')
	fh.write( project.render() )
	fh.close()


if __name__ == "__main__":
	main(sys.argv[1:])
	