#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import math
from jinja2 import Environment, FileSystemLoader

import ephem

import pytz
from datetime import datetime

from lsl.astro import MJD_OFFSET, DJD_OFFSET
from lsl.transform import Time
from lsl.common.dp import fS
from lsl.common.stations import lwa1
from lsl.reader.tbn import filterCodes as TBNFilters
from lsl.reader.drx import filterCodes as DRXFilters

from lsl.reader.tbw import FrameSize as TBWSize
from lsl.reader.tbn import FrameSize as TBNSize
from lsl.reader.drx import FrameSize as DRXSize

from mod_python import Session as PySession


UTC = pytz.utc
nStands = 256


class Observer(object):
	"""Class to hold information about an observer."""
	
	def __init__(self, name, id):
		self.name = name
		self.id = int(id)


class Project(object):
	"""Class to hold all the information about a specific session for a 
	project/proposal."""
	
	def __init__(self, observer, name, id, sessions=[], comments=None):
		self.observer = observer
		self.name = name
		self.id = id
		self.comments = comments
		self.sessions = sessions
		
	def validate(self):
		failures = 0
		for session in self.sessions:
			failures += session.validate()
			
		if failures == 0:
			return True
		else:
			return False


class Session(object):
	"""Class to hold all of the observations in a session."""
	
	def __init__(self, name, id, observations=[], comments=None):
		self.name = name
		self.id = int(id)
		self.observations = observations
		self.comments = comments
		
	def validate(self):
		failures = 0
		totalData = 0.0
		for obs in self.observations:
			failures += obs.validate()
			totalData += obs.dataVolume
		if totalData >= (5*1024**4):
			failures += 1
		
		if failures == 0:
			return True
		else:
			return False


class Observation(object):
	"""Class to hold the specifics of an observations.  It currently
	handles TBW, TBN, TRK_RADEC, TRK_SOL, TRK_JOV."""
	
	id = 1

	def __init__(self, name, target, start, duration, mode, ra, dec, frequency1, frequency2, filter, MaxSNR=False, comments=None):
		self.name = name
		self.target = target
		self.ra = float(ra)
		self.dec = float(dec)
		self.start = start
		self.duration = str(duration)
		self.mode = mode
		self.frequency1 = float(frequency1)
		self.frequency2 = float(frequency2)
		self.filter = int(filter)
		self.MaxSNR = bool(MaxSNR)
		self.comments = comments
		
		self.mjd = self.getMJD()
		self.mpm = self.getMPM()
		self.dur = self.getDuration()
		self.freq1 = self.getFrequency1()
		self.freq2 = self.getFrequency2()
		self.beam = self.getBeamType()
		self.dataVolume = self.estimateBytes()

	def getMJD(self):
		"""Return the modified Julian Date corresponding to the date/time of the
		self.start string."""
		
		try:
			utc = UTC.localize(datetime.strptime(self.start, "UTC %Y %m %d %H:%M:%S.%f"))
		except ValueError:
			utc = UTC.localize(datetime.strptime(self.start, "UTC %Y %b %d %H:%M:%S.%f"))
		utc = Time(utc, format=Time.FORMAT_PY_DATE)
		return int(utc.utc_mjd)

	def getMPM(self):
		"""Return the number of milliseconds between the date/time specified in the
		self.start string and the previous UT midnight."""
		
		try:
			utc = UTC.localize(datetime.strptime(self.start, "UTC %Y %m %d %H:%M:%S.%f"))
			utcMidnight = UTC.localize(datetime.strptime(self.start[0:14], "UTC %Y %m %d"))
		except ValueError:
			utc = UTC.localize(datetime.strptime(self.start, "UTC %Y %b %d %H:%M:%S.%f"))
			utcMidnight = UTC.localize(datetime.strptime(self.start[0:15], "UTC %Y %b %d"))
		diff = utc - utcMidnight
		return int((diff.seconds + diff.microseconds/1000000.0)*1000.0)

	def getDuration(self):
		"""Parse the self.duration string with the format of HH:MM:SS.SSS to return the
		number of milliseconds in that period."""
		
		fields = self.duration.split(':')
		if len(fields) == 3:
			out = int(fields[0])*3600.0
			out += int(fields[1])*60.0
			out += float(fields[2])
		elif len(fields) == 2:
			out = int(fields[0])*60.0
			out += float(fields[1])
		else:
			out = float(fields[0])
		return int(out*1000.0)

	def getFrequency1(self):
		"""Return the number of "tuning words" corresponding to the first frequency."""
		
		freq1 = int(round(self.frequency1 * 2**32 / fS))
		self.frequency1 = freq1*fS / 2**32
		return freq1

	def getFrequency2(self):
		"""Return the number of "tuning words" corresponding to the second frequency."""
		
		freq2 = int(round(self.frequency2 * 2**32 / fS))
		self.frequency2 = freq2*fS / 2**32
		return freq2
		
	def getBeamType(self):
		"""Return a valid value for beam type based on whether maximum S/N beam 
		forming has been requested."""
		
		if self.MaxSNR:
			return 'MAX_SNR'
		else:
			return 'SIMPLE'
	
	def estimateBytes(self):
		"""Place holder for functions that return the estimate size of the data
		set being defined by the observation."""
		
		pass
	
	def getFixedBody(self):
		"""Place holder for functions that return ephem.Body objects (or None)
		that define the pointing center of the observation."""
		
		return None
	
	def computeVisibility(self, station=lwa1()):
		"""Return the fractional visibility of the target during the observation 
		period."""
		
		return 1.0
	
	def validate(self):
		"""Evaluate the observation and return True if it is valid, False
		otherwise."""
		
		pass
	
	def __cmp__(self, other):
		startSelf = self.mjd + self.mpm / (1000.0*3600.0*24.0)
		startOther = other.mjd + other.mpm / (1000.0*3600.0*24.0)
		if startSelf < startOther:
			return -1
		elif startSelf > startOther:
			return 1
		else:
			return 0


class TBW(Observation):
	def __init__(self, name, target, start, samples, bits=12, comments=None):
		self.samples = samples
		self.bits = bits
		duration = int(samples) / 196000 + 1
		duration *= 1100 / 1000.0
		Observation.__init__(self, name, target, start, str(duration), 'TBW', 0.0, 0.0, 0.0, 0.0, 1, comments=comments)

	def estimateBytes(self):
		SamplesPerFrame = 400
		if self.bits == 4:
			SamplesPerFrame = 1200
		nFrames = self.samples / SamplesPerFrame
		nBytes = nFrames * TBWSize * nStands
		return nBytes
		
	def validate(self):
		failures = 0
		# Basic - Sample size and data bits agreement
		if self.bits == 12 and self.samples > 12000000:
			failures += 1
		if self.bits == 4 and self.samples > 36000000:
			failures += 1
		# Advanced - Data Volume
		if self.dataVolume >= (5*1024**4):
			failures += 1
		# Any failures indicates a bad observation
		if failues == 0:
			return True
		else:
			return False


class TBN(Observation):
	def __init__(self, name, target, start, duration, frequency, filter, comments=None):
		Observation.__init__(self, name, target, start, duration, 'TBN', 0.0, 0.0, frequency, 0.0, filter, comments=comments)

	def estimateBytes(self):
		nFrames = self.getDuration()/1000.0 * TBNFilters[self.filter] / 512
		nBytes = nFrames * TBNSize * nStands * 2
		return nBytes
		
	def validate(self):
		failures = 0
	     # Basic - Frequency and filter code values
		if self.freq1 < 219130984 or self.freq1 > 1928352663:
			failures += 1
		if self.filter not in [1,2,3,4,5,6,7]:
			failures += 1
		# Advanced - Data Volume
		if self.dataVolume >= (5*1024**4):
			failures += 1
		# Any failures indicates a bad observation
		if failues == 0:
			return True
		else:
			return False

class DRX(Observation):
	def __init__(self, name, target, start, duration, ra, dec, frequency1, frequency2, filter, MaxSNR=False, comments=None):
		Observation.__init__(self, name, target, start, duration, 'TRK_RADEC', ra, dec, frequency1, frequency2, filter, MaxSNR=MaxSNR, comments=comments)

	def estimateBytes(self):
		nFrames = self.getDuration()/1000.0 * DRXFilters[self.filter] / 4096
		nBytes = nFrames * DRXSize * 4
		return nBytes
		
	def getFixedBody(self):
		"""Return an ephem.Body object corresponding to where the observation is 
		pointed.  None if the observation mode is either TBN or TBW."""
		
		pnt = ephem.FixedBody()
		pnt._ra = self.ra / 12.0 * math.pi
		pnt._dec = self.dec / 180 * math.pi
		pnt._epoch = '2000'
		return pnt
		
	def computeVisibility(self, station=lwa1()):
		"""Return the fractional visibility of the target during the observation 
		period."""
		
		lwa = station.getObserver()
		pnt = self.getFixedBody()
		
		vis = 0
		cnt = 0
		dt = 0.0
		while dt <= self.duration:
			lwa.date = self.start + dt/3600/25.0 + MJD_OFFSET - DJD_OFFSET
			pnt.compute(lwa)
			
			cnt += 1
			if pnt.alt > 0:
				vis += 1
				
			dt += 300.0
		
		return float(vis)/float(cnt)
		
	def validate(self):
		failures = 0
	     # Basic - Frequency and filter code values
		if self.freq1 < 219130984 or self.freq1 > 1928352663:
			failures += 1
		if self.freq2 < 219130984 or self.freq2 > 1928352663:
			failures += 1
		if self.filter not in [1,2,3,4,5,6,7]:
			failures += 1
		# Advanced - Target Visibility
		if self.computeVisibility() < 1.0:
			failues += 1
		# Advanced - Data Volume
		if self.dataVolume >= (5*1024**4):
			failures += 1
		# Any failures indicates a bad observation
		if failues == 0:
			return True
		else:
			return False


class Solar(DRX):
	def __init__(self, name, target, start, duration, frequency1, frequency2, filter, MaxSNR=False, comments=None):
		Observation.__init__(self, name, target, start, duration, 'TRK_SOL', 0.0, 0.0, frequency1, frequency2, filter, MaxSNR=MaxSNR, comments=comments)
		
	def getFixedBody(self):
		"""Return an ephem.Body object corresponding to where the observation is 
		pointed.  None if the observation mode is either TBN or TBW."""
		
		return ephem.Sun()


class Jovian(DRX):
	def __init__(self, name, target, start, duration, frequency1, frequency2, filter, MaxSNR=False, comments=None):
		Observation.__init__(self, name, target, start, duration, 'TRK_JOV', 0.0, 0.0, frequency1, frequency2, filter, MaxSNR=MaxSNR, comments=comments)

	def getFixedBody(self):
		"""Return an ephem.Body object corresponding to where the observation is 
		pointed.  None if the observation mode is either TBN or TBW."""
		
		return ephem.Jupiter()


class Stepped(Observation):
	def __init__(self, name, target, start, filter, RADec=True, MaxSNR=False, comments=None):
		self.RADec = bool(RADec)
		self.steps = []
		Observation.__init__(self, name, target, start, 0, 'STEPPED', 0.0, 0.0, 0.0, 0.0, filter, MaxSNR=MaxSNR, comments=comments)
		
	def estimateBytes(self):
		nFrames = self.getDuration()/1000.0 * DRXFilters[self.filter] / 4096
		nBytes = nFrames * DRXSize * 4
		return nBytes
		
	def append(self, newStep):
		self.steps.append(newStep)


class BeamStep(object):
	def __init__(self, c1, c2, start, frequency1, frequency2, MaxSNR=False):
		self.c1 = float(c1)
		self.c2 = float(c2)
		self.start = int(start)
		self.frequency1 = float(frequency1)
		self.frequnecy2 = float(frequency2)
		
		self.freq1 = self.getFrequency1()
		self.freq2 = self.getFrequency2()
		self.beam = self.getBeamType()
		
	def getFrequency1(self):
		"""Return the number of "tuning words" corresponding to the first frequency."""
		
		freq1 = int(round(self.frequency1 * 2**32 / fS))
		self.frequency1 = freq1*fS / 2**32
		return freq1

	def getFrequency2(self):
		"""Return the number of "tuning words" corresponding to the second frequency."""
		
		freq2 = int(round(self.frequency2 * 2**32 / fS))
		self.frequency2 = freq2*fS / 2**32
		return freq2
		
	def getBeamType(self):
		"""Return a valid value for beam type based on whether maximum S/N beam 
		forming has been requested."""
		
		if self.MaxSNR:
			return 'MAX_SNR'
		else:
			return 'SIMPLE'
			
	def __cmp__(self, other):
		startSelf = self.start
		startOther = other.start
		if startSelf < startOther:
			return -1
		elif startSelf > startOther:
			return 1
		else:
			return 0


def index(req):
	sess = PySession.Session(req)
	pageMode = req.form.getfirst('mode', None)

	path = os.path.join(os.path.dirname(__file__), 'templates')
	env = Environment(loader=FileSystemLoader(path))

	if pageMode == 'observations':
		# Stage 2:  Observation Definitions
		sessionMode = req.form.getfirst('sessionMode', 'DRX')
		projectInfo = {}
		for keyword in ['firstName', 'lastName', 'observerID', 'projectName', 'projectID', 'projectComments']:
			projectInfo[keyword] = req.form.getfirst(keyword, None)
		projectInfo['sessionMode'] = sessionMode
		
		sess['projectInfo'] = projectInfo
		sess.save()
		
		if sessionMode == 'TBW':
			template = env.get_template('tbw.html')
		elif sessionMode == 'TBN':
			template = env.get_template('tbn.html')
		else:
			template = env.get_template('drx.html')
		return template.render(projectInfo=projectInfo)
	
	elif pageMode == 'definitions':
		# Stage 3:  Session Definition File Creation
		projectInfo = {}
		for keyword in ['firstName', 'lastName', 'observerID', 'projectName', 'projectID', 'projectComments', 'sessionMode']:
			projectInfo[keyword] = req.form.getfirst(keyword, None)
		
		sessionInfo = {}
		for keyword in ['sessionName', 'sessionID', 'sessionComments']:
			sessionInfo[keyword] = req.form.getfirst(keyword, None)
		
		observer = Observer(projectInfo['lastName']+', '+projectInfo['firstName'], projectInfo['observerID'])
		project = Project(observer, projectInfo['projectName'], projectInfo['projectID'], comments=projectInfo['projectComments'])
		session = Session(sessionInfo['sessionName'], sessionInfo['sessionID'], comments=sessionInfo['sessionComments'])
		
		numObs = 1
		observations = []
		while req.form.getfirst('obsName%i' % numObs, None) is not None:
			obsName = req.form.getfirst('obsName%i' % numObs, None)
			obsTarget = req.form.getfirst('obsTarget%i' % numObs, None)
			obsComments = req.form.getfirst('obsComments%i' % numObs, None)
			obsStart = req.form.getfirst('obsStart%i' % numObs, None)
			if projectInfo['sessionMode'] == 'TBW':
				obsBits = int(req.form.getfirst('bits', 12))
				obsSamples = int(req.form.getfirst('samples', 12000000))
				observations.append( TBW(obsName, obsTarget, obsStart, obsSamples, bits=obsBits, comments=obsComments) )
				
			if projectInfo['sessionMode'] == 'TBN':
				obsDur = req.form.getfirst('obsDuration%i' % numObs, '00:00:00.000')
				obsFreq = float(req.form.getfirst('obsFrequency%i' % numObs, 38.0))*1e6
				obsFilter = int(req.form.getfirst('obsFilter%i' % numObs, 7))
				observations.append( TBN(obsName, obsTarget, obsStart, obsDur, obsFreq, obsFilter, comments=obsComments) )
				
			if projectInfo['sessionMode'] == 'DRX':
				obsMode = req.form.getfirst('obsMode%i' % numObs, 'TRK_RADEC')
				obsDur = req.form.getfirst('obsDuration%i' % numObs, '00:00:00.000')
				obsFreq1 = float(req.form.getfirst('obsFrequency%i-1' % numObs, 38.0))*1e6
				obsFreq2 = float(req.form.getfirst('obsFrequency%i-2' % numObs, 38.0))*1e6
				obsFilter = int(req.form.getfirst('obsFilter%i' % numObs, 7))
				obsBeam = req.form.getfirst('obsBeam%i' % numObs, 'SIMPLE')
				if obsBeam == 'SIMPLE':
					MaxSNR = False
				else:
					MaxSNR = True
					
				if obsMode == 'TRK_RADEC':
					obsRA = float(req.form.getfirst('obsRA%i' % numObs, 0.000000))
					obsDec = float(req.form.getfirst('obsDec%i' % numObs, 0.000000))
					observations.append( DRX(obsName, obsTarget, obsStart, obsDur, obsRA, obsDec, obsFreq1, obsFreq2, obsFilter, MaxSNR=MaxSNR, comments=obsComments) )
					
				if obsMode == 'TRK_SOL':
					observations.append( Solar(obsName, obsTarget, obsStart, obsDur, obsFreq1, obsFreq2, obsFilter, MaxSNR=MaxSNR, comments=obsComments) )
					
				if obsMode == 'TRK_JOV':
					observations.append( Jovian(obsName, obsTarget, obsStart, obsDur, obsFreq1, obsFreq2, obsFilter, MaxSNR=MaxSNR, comments=obsComments) )
					
				if obsMode == 'STEPPED':
					pass
					
			numObs = numObs + 1
					
		session.observations = sorted(observations)
		project.sessions = [session,]
		
		sess['sessionInfo'] = sessionInfo
		sess['form'] = req.form
		sess.save()
		
		# Set the output content type and suggested file name
		req.headers_out["Content-type"] = 'text/plain'
		#req.headers_out["Content-Disposition"] = ';filename=%s_%i.txt' % (project.id, project.sessions[0].id)
		
		template = env.get_template('session_def.tmpl')
		return template.render(project=project, nStands=nStands, TBNFilters=TBNFilters, DRXFilters=DRXFilters)
		
	else:
		# Stage 1:  Observer and Proposal Information; DP Output Mode
		template = env.get_template('session.html')
		return template.render()
