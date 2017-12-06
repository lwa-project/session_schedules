#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Wrapper around the validator.py module to make it useable from the
command line.

$Rev$
$LastChangedBy$
$LastChangedDate$
""" 

import os
import re
import sys
import math
import pytz
import ephem
import getopt
import tempfile
import subprocess
try:
	import cStringIO as StringIO
except ImportError:
	import StringIO

from lsl.common import sdf
from lsl.common.stations import lwa1
from lsl.astro import utcjd_to_unix, MJD_OFFSET
try:
	from lsl.common import sdfADP
	adpReady = True
except ImportError:
	sdfADP = None
	adpReady = False


_tpss_base = os.path.join(os.path.dirname(__file__), 'tpss')


def usage(exitCode=None):
	print """validatorCLI.py - Off-line version of the LWA session definition file validator.

Usage: validatorCLI.py [OPTIONS] input_SDF_file

Options:
-h, --help          Display this help information
-s, --lwasv         Validate a SDF for LWA-SV instead of LWA1 (default = LWA1)
"""
	
	if exitCode is not None:
		sys.exit(exitCode)
	else:
		return True


def parseOptions(args):
	config = {}
	config['station'] = 'lwa1'
	
	# Read in and process the command line flags
	try:
		opts, args = getopt.getopt(args, "hs", ["help", "lwasv"])
	except getopt.GetoptError, err:
		# Print help information and exit:
		print str(err) # will print something like "option -a not recognized"
		usage(exitCode=2)
		
	# Work through opts
	for opt, value in opts:
		if opt in ('-h', '--help'):
			usage(exitCode=0)
		elif opt in ('-s', '--lwasv'):
			config['station'] = 'lwasv'
		else:
			assert False
			
	# Make sure we are ready for LWA-SV
	if config['station'] == 'lwasv' and not adpReady:
		raise RuntimeError("LWA-SV requested but the ADP-compatible SDF module could not be loaded")
		
	# Add in arguments
	config['args'] = args
	
	# Return configuration
	return config


def main(args):
	# Parse the command line
	config = parseOptions(args)
	filename = config['args'][0]
	
	# Set the TPSS and SDF versions to use
	_tpss = '%s-%s' % (_tpss_base, config['station'])
	_sdf = sdf
	if config['station'] == 'lwasv':
		_sdf = sdfADP
		
	# Read the contents of the temporary SD file into a list so that we can examine
	# the file independently of the parser
	fh = open(filename, 'r')
	file = fh.readlines()
	fh.close()
	
	# Run the parser/validator.  This uses a copy of Steve's tpss program.  tpss is run
	# to level to to make sure that the file is valid.  If the exit status is not '2', 
	# the file is taken to be invalid.
	validator = subprocess.Popen([_tpss, filename, '2', '0'], bufsize=-1, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
	stdout, stderr = validator.communicate()
	status = validator.wait()
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
	project = _sdf.parseSDF(filename)
	
	# Build a couple of dictionaries to lump everything together
	tpss = {'version': tpssVersion, 'output': output, 'valid': valid, 'errors': errors, 
			'numObs': numObsP, 'totDur': totDurP}
	
	# Report
	if tpss['valid']:
		print "Congratualtions, you have a valid SDF file."
	else:
		print "There are 1 or more errors in your SDF file."
		for err in tpss['errors']:
			print "On line %i: %s" % (err['line'], err['message'])
	print " "
	
	print "Summary:"
	print "Type of observations: %s" % project.sessions[0].observations[0].mode
	print "Total number of parsed observations: %i" % tpss['numObs']
	print "Total observing time: %.3f seconds" % (tpss['totDur'] / 1000.0,)
	print "TPSS version used for validation: %s" % tpss['version']
	print " "

	if project.sessions[0].observations[0].mode not in ('TBW', 'TBN'):
		print "Source List:"
		for obs in project.sessions[0].observations:
			if obs.mode == 'TRK_RADEC':
				print "%s at RA: %.3f hours, Dec.: %+.3f degrees is visible for %i%% of the observation" % (obs.target, obs.ra, obs.dec, obs.computeVisibility() * 100)
			if obs.mode == 'TRK_SOL':
				print "Sun is visible for %i%% of the observation" % (obs.computeVisibility() * 100,)
			if obs.mode == 'TRK_JOV':
				print "Jupiter is visible for %i%% of the observation" % (obs.computeVisibility() * 100,)
			if obs.mode == 'STEPPED':
				print "Steps are:"
				for step in obs.steps:
					if step.RADec:
						print "  RA: %.3f hours, Dec.: %+.3f degrees" % (step.c1, step.c2)
					else:
						"azimuth: %.3f degrees, elevation: %.3f degrees" % (step.c1, step.c2)
				print "Combined visibility for all steps is %i%%." % (obs.computeVisibility() * 100,)
		print " "
	
	print "Validator Output:"
	for line in output:
		print line


if __name__ == "__main__":
	main(sys.argv[1:])
	
