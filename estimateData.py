#!/usr/bin/env python

"""
Simple script for estimating the data volume for a TBN or DRX observation.

Usage:
   estimateData.py <mode> <filter code>  HH:MM:SS.SSS
   
.. note::
	For spectrometer data, the mode is given by SPC,<tlen>,<icount> where
	'tlen' is the transform length (channel count) and 'icount' is the 
	number of transforms per integration.

$Rev$
$LastChangedBy$
$LastChangedDate$
"""

import os
import sys

from lsl.reader.tbn import FrameSize as tbnFrameSize
from lsl.reader.tbn import filterCodes as tbnFilters
from lsl.reader.drx import FrameSize as drxFrameSize
from lsl.reader.drx import filterCodes as drxFilters


def main(args):
	# Parse the command line
	mode = args[0]
	filterCode = int(args[1])
	duration = args[2]

	# Convert the HH:MM:SS.SSS string to a duration in seconds
	try:
		hour, minute, second = duration.split(':', 2)
	except ValueError:
		try:
			hour = '0'
			minute, second = duration.split(':', 1)
		except ValueError:
			hour = '0'
			minute = '0'
			second = duration
	hour = int(hour)
	minute = int(minute)
	second = float(second)
	duration = hour*3600 + minute*60 + second

	# Figure out the data rate
	if mode == 'TBN':
		antpols = 520
		sampleRate = tbnFilters[filterCode]
		dataRate = 1.0*sampleRate/512*tbnFrameSize*antpols
	elif mode == 'DRX':
		tunepols = 4
		sampleRate = drxFilters[filterCode]
		dataRate = 1.0*sampleRate/4096*drxFrameSize*tunepols
	elif mode[0:3] == 'SPC':
		try:
			junk, tlen, icount = mode.split(',', 2)
		except ValueError:
			print "Spectrometer settings transform lenght and integration count not specified"
			sys.exit(1)
		tlen = int(tlen)
		icount = int(icount)
		tunes = 2
		products = 4
		sampleRate = drxFilters[filterCode]
		
		# Calculate the DR spectrometer frame size
		headerSize = 64
		dataSize = tlen*tunes*products*4
		dataRate = (headerSize+dataSize)/(1.0*tlen*icount/sampleRate)
	else:
		print "Unsupported mode: %s" % mode
		sys.exit(1)
		
	# Display the final answer
	print "%s: filter code %i (%i samples/s)" % (mode, filterCode, sampleRate)
	if mode[0:3] == 'SPC':
		print "  Channel Count: %i" % tlen
		print "  Resolution Bandwidth: %.3f Hz" % (1.0*sampleRate/tlen,)
		print "  Integration Count: %i" % icount
		print "  Integration Time: %.3f s" % (1.0*tlen*icount/sampleRate,)
		print "  Polarization Products: %i" % products
	print "  Data rate: %.2f MB/s" % (dataRate/1024**2,)
	print "  Data volume for %02i:%02i:%06.3f is %.2f GB" % (hour, minute, second, dataRate*duration/1024**3)


if __name__ == "__main__":
	main(sys.argv[1:])

