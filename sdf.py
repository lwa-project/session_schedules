# -*- coding: utf-8 -*-

"""Module that contains all of the relevant class to build up a representation 
of a session definition file as defined in MCS0030v2.  The hierarchy of classes
is:
  * Project - class that holds all of the information about the project (including
    the observer) and one or more sessions.  Technically, a SD file has only one
    session but this approach allows for the generation of multiple SD files from
    on Project object.
  * Observer - class that hold the observer's name and numeric ID
  * Session - class that holds all of the observations associated with a particular 
    DP output.  
  * Observations - class that hold information about a particular observation.  It
    includes a variety of attributes that are used to convert human-readable inputs
    to SDF data values.  The observation class is further subclasses into:
    - TBW - class for TBW observations
    - TBN - class for TBN observations
    - DRX - class for general DRX observation, with sub-classes:
      * Solar - class for solar tracking
      * Jovian - class for jovian tracking
    - Stepped - class for stepped observations
  * BeamStep - class that holds the information about a particular step in a Stepped
    Observation
    
All of the classes, except for Stepped and BeamStep, are complete and functional.  In 
addition, most class contain 'validate' attribute functions that can be used to 
determine if the project/session/observation are valid or not given the constraints of
the DP system."""

import math
import pytz
import ephem
from datetime import datetime

from jinja2 import Template

from lsl.transform import Time
from lsl.astro import MJD_OFFSET, DJD_OFFSET

from lsl.common.dp import fS
from lsl.common.stations import lwa1
from lsl.reader.tbn import filterCodes as TBNFilters
from lsl.reader.drx import filterCodes as DRXFilters
from lsl.reader.tbw import FrameSize as TBWSize
from lsl.reader.tbn import FrameSize as TBNSize
from lsl.reader.drx import FrameSize as DRXSize


__version__ = '0.1'
__revision__ = '$ Revision: 2 $'
__all__ = ['Observer', 'Project', 'Session', 'Observation', 'TBW', 'TBN', 'DRX', 'Solar', 'Jovian', 'Stepped', 'BeamStep', '__version__', '__revision__', '__all__']

_UTC = pytz.utc
_nStands = 256


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
			
	def render(self, session=0):
		if not self.validate() :
			raise RuntimeError("Invalid session/observation parameters.  Aborting.")
		if session >= len(self.sessions):
			raise IndexError("Invalid session index")
		
		self.sessions[session].observations.sort()
		return _SDFTemplate.render(project=self, whichSession=session)


class Session(object):
	"""Class to hold all of the observations in a session."""
	
	def __init__(self, name, id, observations=[], dataReturnMethod='DRSU', comments=None):
		self.name = name
		self.id = int(id)
		self.observations = observations
		self.dataReturnMethod = dataReturnMethod
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
			
	def __cmp__(self, other):
		self.observations.sort()
		other.observations.sort()
		
		startSelf = self.observations[0].mjd + self.observations[0].mpm / (1000.0*3600.0*24.0)
		startOther = other.observations[0].mjd + other.observations[0].mpm / (1000.0*3600.0*24.0)
		if startSelf < startOther:
			return -1
		elif startSelf > startOther:
			return 1
		else:
			return 0


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
			utc = _UTC.localize(datetime.strptime(self.start, "UTC %Y %m %d %H:%M:%S.%f"))
		except ValueError:
			utc = _UTC.localize(datetime.strptime(self.start, "UTC %Y %b %d %H:%M:%S.%f"))
		utc = Time(utc, format=Time.FORMAT_PY_DATE)
		return int(utc.utc_mjd)

	def getMPM(self):
		"""Return the number of milliseconds between the date/time specified in the
		self.start string and the previous UT midnight."""
		
		try:
			utc = _UTC.localize(datetime.strptime(self.start, "UTC %Y %m %d %H:%M:%S.%f"))
			utcMidnight = _UTC.localize(datetime.strptime(self.start[0:14], "UTC %Y %m %d"))
		except ValueError:
			utc = _UTC.localize(datetime.strptime(self.start, "UTC %Y %b %d %H:%M:%S.%f"))
			utcMidnight = _UTC.localize(datetime.strptime(self.start[0:15], "UTC %Y %b %d"))
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
		nBytes = nFrames * TBWSize * _nStands
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
		if failures == 0:
			return True
		else:
			return False


class TBN(Observation):
	def __init__(self, name, target, start, duration, frequency, filter, comments=None):
		self.filterCodes = TBNFilters
		Observation.__init__(self, name, target, start, duration, 'TBN', 0.0, 0.0, frequency, 0.0, filter, comments=comments)

	def estimateBytes(self):
		nFrames = self.getDuration()/1000.0 * TBNFilters[self.filter] / 512
		nBytes = nFrames * TBNSize * _nStands * 2
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
		if failures == 0:
			return True
		else:
			return False

class DRX(Observation):
	def __init__(self, name, target, start, duration, ra, dec, frequency1, frequency2, filter, MaxSNR=False, comments=None):
		self.filterCodes = DRXFilters
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
		while dt <= self.dur/1000.0:
			lwa.date = self.mjd + (self.mpm/1000.0 + dt)/3600/24.0 + MJD_OFFSET - DJD_OFFSET
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
		if failures == 0:
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
		self.filterCodes = DRXFilters
		Observation.__init__(self, name, target, start, 0, 'STEPPED', 0.0, 0.0, 0.0, 0.0, filter, MaxSNR=MaxSNR, comments=comments)
		
	def append(self, newStep):
		self.steps.append(newStep)
		
	def estimateBytes(self):
		nFrames = self.getDuration()/1000.0 * DRXFilters[self.filter] / 4096
		nBytes = nFrames * DRXSize * 4
		return nBytes
		
	def validate(self):
		failures = 0
		for step in self.steps:
			stepValid = step.validate()
			if not stepValid:
				failures += 1
		# Advanced - Target Visibility
		if self.computeVisibility() < 1.0:
			failues += 1
		# Advanced - Data Volume
		if self.dataVolume >= (5*1024**4):
			failures += 1
		# Any failures indicates a bad observation
		if failures == 0:
			return True
		else:
			return False


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
			
	def validate(self):
		failures = 0
	     # Basic - Frequency and filter code values
		if self.freq1 < 219130984 or self.freq1 > 1928352663:
			failures += 1
		if self.freq2 < 219130984 or self.freq2 > 1928352663:
			failures += 1
		if self.filter not in [1,2,3,4,5,6,7]:
			failures += 1
		# Any failures indicates a bad observation
		if failures == 0:
			return True
		else:
			return False
			
	def __cmp__(self, other):
		startSelf = self.start
		startOther = other.start
		if startSelf < startOther:
			return -1
		elif startSelf > startOther:
			return 1
		else:
			return 0
			

_SDFTemplate = Template("""PI_ID           {{ project.observer.id }}
PI_NAME         {{ project.observer.name }}

PROJECT_ID      {{ project.id }}
PROJECT_TITLE   {{ project.name }}
PROJECT_REMPI   {{ project.comments|default('None provided', boolean=True)|truncate(4090, killwords=True) }}
PROJECT_REMPO   None

{% set session = project.sessions[whichSession] -%}
SESSION_ID      {{ session.id }}
SESSION_TITLE   {{ session.name|default('None provided', boolean=True) }}
SESSION_REMPI   {{ session.comments|default('None provided', boolean=True)|truncate(4090, killwords=True) }}
SESSION_REMPO   Requested data return method is {{ session.dataReturnMethod }}

{% for obs in session.observations -%}
OBS_ID          {{ loop.index }}
OBS_TITLE       {{ obs.name|default('None provided', boolean=True) }}
OBS_TARGET      {{ obs.target|default('None provided', boolean=True) }}
OBS_REMPI       {{ obs.comments|default('None provided', boolean=True)|truncate(4090, killwords=True) }}
OBS_REMPO       Estimated data volume for this observation is {{ obs.dataVolume|filesizeformat }}
OBS_START_MJD   {{ obs.mjd }}
OBS_START_MPM   {{ obs.mpm }}
OBS_START       {{ obs.start~'\n' }}
{%- if obs.mode == 'TBW' -%}
OBS_DUR         {{ "%i"|format(obs.dur) }}
OBS_DUR+        {{ "%.1f ms"|format(obs.samples / 196000) }} + estimated read-out time
OBS_MODE        {{ obs.mode }}
{%- endif %}
{%- if obs.mode == 'TBN' -%}
OBS_DUR         {{ obs.dur }}
OBS_DUR+        {{ obs.duration }}
OBS_MODE        {{ obs.mode }}
OBS_FREQ1       {{ obs.freq1 }}
OBS_FREQ1+      {{ "%.9f MHz"|format(obs.frequency1/1000000) }}
OBS_BW          {{ obs.filter }}
OBS_BW+         {{ "%.3f kHz"|format(obs.filterCodes[obs.filter]/1000) }}
{% endif %}
{%- if obs.mode == 'TRK_RADEC' -%}
OBS_DUR         {{ obs.dur }}
OBS_DUR+        {{ obs.duration }}
OBS_MODE        {{ obs.mode }}
OBS_RA          {{ obs.ra }}
OBS_DEC         {{ obs.dec }}
OBS_B           {{ obs.beam }}
OBS_FREQ1       {{ obs.freq1 }}
OBS_FREQ1+      {{ "%.9f MHz"|format(obs.frequency1/1000000) }}
OBS_FREQ2       {{ obs.freq2 }}
OBS_FREQ2+      {{ "%.9f MHz"|format(obs.frequency2/1000000) }}
OBS_BW          {{ obs.filter }}
OBS_BW+         {{ "%.3f MHz"|format(obs.filterCodes[obs.filter]/1000000) if obs.filterCodes[obs.filter] > 1000000 else "%.3f kHz"|format(obs.filterCodes[obs.filter]/1000) }}
{% endif %}
{%- if obs.mode == 'TRK_SOL' -%}
OBS_DUR         {{ obs.dur }}
OBS_DUR+        {{ obs.duration }}
OBS_MODE        {{ obs.mode }}
OBS_B           {{ obs.beam }}
OBS_FREQ1       {{ obs.freq1 }}
OBS_FREQ1+      {{ "%.9f MHz"|format(obs.frequency1/1000000) }}
OBS_FREQ2       {{ obs.freq2 }}
OBS_FREQ2+      {{ "%.9f MHz"|format(obs.frequency2/1000000) }}
OBS_BW          {{ obs.filter }}
OBS_BW+         {{ "%.3f MHz"|format(obs.filterCodes[obs.filter]/1000000) if obs.filterCodes[obs.filter] > 1000000 else "%.3f kHz"|format(obs.filterCodes[obs.filter]/1000) }}
{% endif %}
{%- if obs.mode == 'TRK_JOV' -%}
OBS_DUR         {{ obs.dur }}
OBS_DUR+        {{ obs.duration }}
OBS_MODE        {{ obs.mode }}
OBS_B           {{ obs.beam }}
OBS_FREQ1       {{ obs.freq1 }}
OBS_FREQ1+      {{ "%.9f MHz"|format(obs.frequency1/1000000) }}
OBS_FREQ2       {{ obs.freq2 }}
OBS_FREQ2+      {{ "%.9f MHz"|format(obs.frequency2/1000000) }}
OBS_BW          {{ obs.filter }}
OBS_BW+         {{ "%.3f MHz"|format(obs.filterCodes[obs.filter]/1000000) if obs.filterCodes[obs.filter] > 1000000 else "%.3f kHz"|format(obs.filterCodes[obs.filter]/1000) }}
{% endif %}
{% endfor %}

{%- set obs = session.observations|first -%}
{%- if obs.mode == 'TBW' -%}
OBS_TBW_BITS    {{ obs.bits }}
OBS_TBW_SAMPLES {{ obs.samples }}
{% endif %}

""")
