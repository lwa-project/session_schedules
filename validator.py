#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import re
import math
import ephem
import tempfile
import subprocess
from jinja2 import Environment, FileSystemLoader

from lsl.astro import MJD_OFFSET, DJD_OFFSET
from lsl.common.stations import lwa1


_tpss = '/var/www/data/schedule/tpss'


class _Pnt(object):
	"""Private object to store information about an observing target."""
	
	def __init__(self, name, ra, dec, start, duration):
		self.name = name
		self.ra = ra
		self.dec = dec
		self.start = start
		self.duration = duration
		self.type = 'radec'
		
		self.status = -1
		self.visibility = 0.0
		
	def getFixedBody(self):
		if self.type == 'TRK_SOL':
			pnt = ephem.Sun()
		elif self.type == 'TRK_JOV':
			pnt = ephem.Jupiter()
		else:
			pnt = ephem.FixedBody()
			pnt._ra = self.ra / 12.0 * math.pi
			pnt._dec = self.dec / 180 * math.pi
			pnt._epoch = '2000'
		return pnt
		
	def computeVisibility(self, station):
		if self.type in ['TBW', 'TBN']:
			self.visibility = 1.0
			self.status = 1
			return
		else:
			lwa = station.getObserver()
			pnt = self.getFixedBody()
			
			vis = 0
			cnt = 0
			dt = 0.0
			atStart = False
			atEnd = False
			while dt <= self.duration:
				lwa.date = self.start + dt/3600/25.0 + MJD_OFFSET - DJD_OFFSET
				pnt.compute(lwa)
				
				cnt += 1
				if pnt.alt > 0:
					vis += 1
					if cnt == 1:
						atStart = True
				
				dt += 300.0
				
			if pnt.alt > 0:
				atEnd = True
			
			self.visibility = float(vis)/float(cnt)
			if self.visibility == 0.0:
				self.status = 0
			elif self.visibility == 1.0:
				self.status = 1
			else:
				if atStart and not atEnd:
					self.status = 3
				elif atEnd and not atStart:
					self.status = 2
				else:
					self.status = 4
			return


def _getObjects(filename):
	"""Given a filename corresponding to a SD file, return a list of _Pnt objects
	corresponding to the targets in that file.  Note:  This function uses a simple
	keyword-based matcher to find observations and returned objects for the all-sky
	TBW and TBN observations as well."""
	
	fh = open(filename, 'r')
	
	obs = []
	for line in fh:
		if line.find('OBS_ID') != -1:
			obs.append( _Pnt('', 0.0, 90.0, 0, 0) )
			
		if line.find('OBS_MODE') != -1:
			junk, type = line.split()
			obs[-1].type = type
			
		if line.find('OBS_TARGET') != -1:
			junk, name = line.split(None, 1)
			obs[-1].name = name
		
		if line.find('OBS_START_MJD') != -1:
			junk, mjd = line.split()
			obs[-1].start += float(mjd)
		if line.find('OBS_START_MPM') != -1:
			junk, mpm = line.split()
			obs[-1].start += float(mpm)/1000.0 / (3600.0 * 24.0)
			
		if line.find('OBS_DUR ') != -1:
			junk, dur = line.split()
			obs[-1].duration = float(dur)/1000.0
			
		if line.find('OBS_RA') != -1:
			junk, ra = line.split()
			obs[-1].ra = float(ra)
		if line.find('OBS_DEC') != -1:
			junk, dec = line.split()
			obs[-1].dec = float(dec)
	return obs


def index(req):
	path = os.path.join(os.path.dirname(__file__), 'templates')
	env = Environment(loader=FileSystemLoader(path), extensions=['jinja2.ext.i18n'])
	env.install_null_translations()
	
	try:
		# Get the SD file's contents and save it to a temporary file in a temporary 
		# directory.  This is all done in binary mode so the file will have to be
		# re-opened later to work on it.
		sdfData = req.form['file']
	
		tmpDir = tempfile.mkdtemp(suffix='.sdf', prefix='validate-')
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
		numObsS = 0
		totDurS = 0
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
			
			if not commentLine:
				# Check for observations and durations in the original input file
				if file[fileIndex].find('OBS_ID') != -1:
					numObsS += 1
				if file[fileIndex].find('OBS_DUR ') != -1:
					totDurS += int((file[fileIndex].split())[-1])
		
		# Do a little extra checking on original file by looking at the observations 
		# that have been defined to see if the targets are above the horizon for the 
		# duration of the observation they correspond to.  Also, figure out if we are
		# in an all-sky mode, a beam forming mode, or a stepped beam forming mode.
		objects = _getObjects(tmpFile)
		for obj in objects:
			obj.computeVisibility(lwa1())
		if objects[0].type.find('TB') != -1:
			mode = 'all-sky'
		elif objects[0].type.find('TR') != -1:
			mode = 'beam forming'
		else:
			mode = 'stepped'
		
		# Cleanup the temporary file and directory
		os.unlink(tmpFile)
		os.rmdir(tmpDir)
		
		# Build a couple of dictionaries to lump everything together
		tpss = {'version': tpssVersion, 'output': output, 'valid': valid, 'errors': errors, 
				'numObs': numObsP, 'totDur': totDurP}
		sdf = {'mode': mode, 'output': file, 'numObs': numObsS, 'totDur': totDurP, 
				'targets': objects}
		
		template = env.get_template('validator-results.html')
		return template.render(tpss=tpss, sdf=sdf)
	except KeyError:
		template = env.get_template('validator.html')
		return template.render()