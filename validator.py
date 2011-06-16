#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Apache mod_python module for validating a SDF file against the validation 
functions in the sdf module and a version of the sch/tpss program from MCS
JR5.
"""

import os
import re
import math
import ephem
import tempfile
import subprocess
from jinja2 import Environment, FileSystemLoader

from sdf import *


__version__ = "0.1"
__revision__ = "$Rev$"
__author__ = "Jayce Dowell"


_tpss = '/var/www/data/schedule/tpss'


def index(req):
	path = os.path.join(os.path.dirname(__file__), 'templates')
	env = Environment(loader=FileSystemLoader(path), extensions=['jinja2.ext.i18n'])
	env.install_null_translations()
	
	try:
		# Get the SD file's contents and save it to a temporary file in a temporary 
		# directory.  This is all done in binary mode so the file will have to be
		# re-opened later to work on it.
		sdfData = req.form['file']
	except KeyError:
		template = env.get_template('validator.html')
		return template.render()
		
	tmpDir = tempfile.mkdtemp(prefix='validate-')
	tmpFile = os.path.join(tmpDir, 'sdf.txt')
	fh = open(tmpFile, 'wb')
	fh.write(sdfData.file.read())
	fh.close()
	
	# Read the contents of the temporary SD file into a list so that we can examine
	# the file independently of the parser
	fh = open(tmpFile, 'r')
	file = fh.readlines()
	fh.close()
	
	# Run the parser/validator.  This uses a copy of Steve's tpss program.  tpss is run
	# to level to to make sure that the file is valid.  If the exit status is not '2', 
	# the file is taken to be invalid.
	validator = subprocess.Popen([_tpss, tmpFile, '2', '1'], bufsize=-1, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
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
	
	# Cleanup the temporary file and directory
	os.unlink(tmpFile)
	os.rmdir(tmpDir)
	
	# Build a couple of dictionaries to lump everything together
	tpss = {'version': tpssVersion, 'output': output, 'valid': valid, 'errors': errors, 
			'numObs': numObsP, 'totDur': totDurP}
	
	template = env.get_template('validator-results.html')
	return template.render(tpss=tpss, sdf=file, project=parse(file))
