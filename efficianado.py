#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
efficianado.py - Script to pack a collection of SDFs into the shortest observing time 
using an adaptive genetic algorithm.
"""


import os
import sys
import copy
import math
import pytz
import ephem
import numpy
import getopt
import random
from datetime import datetime, timedelta

from multiprocessing import Pool, cpu_count

from scipy.stats import scoreatpercentile as percentile

from lsl.common import sdf
from lsl.transform import Time
from lsl.common.stations import lwa1
from lsl.astro import utcjd_to_unix, MJD_OFFSET

import matplotlib.dates
from matplotlib import pyplot as plt
from matplotlib.collections import LineCollection

# Date/time manipulation
_UTC = pytz.utc
_MST = pytz.timezone('US/Mountain')
formatString = '%Y/%m/%d %H:%M:%S.%f %Z'

# MCS session padding extent
sessionLag = timedelta(seconds=5)

# LST manipulation
solarDay    = timedelta(seconds=24*3600, microseconds=0)
siderealDay = timedelta(seconds=23*3600+56*60+4, microseconds=91000)
siderealRegression = solarDay - siderealDay


def usage(exitCode=None):
	print """efficianado.py - Script to schedule observations.
	
Usage: efficianado.py [OPTIONS] YYYY-MM-DD SDF1 [SDF2 [...]]

Options:
-h, --help             Display this help message
-m, --maintenance      Add a maintenance day (9 to 17 MT)
-l, --limits           Schedule search limit in days (default 14 days)
-p, --population-size  GA population size (default 1,000)
-g, --generations      Number of generations to use (default 250)
"""

	if exitCode is not None:
		sys.exit(exitCode)
	else:
		return True


def parseOptions(args):
	config = {}
	config['start'] = None
	config['limit'] = 14
	config['maintenance'] = []
	config['popSize'] = 1000
	config['generations'] = 200
	config['args'] = []
	
 	# Read in and process the command line flags
	try:
		opts, args = getopt.getopt(args, "hm:l:p:g:", ["help", "maintenance=", "limits=", "population-size=", "generations="])
		print 'cat'
	except getopt.GetoptError, err:
		# Print help information and exit:
		print str(err) # will print something like "option -a not recognized"
        #usage(exitCode=2)
        
    # Work through opts
	for opt, value in opts:
		print opt, value
		if opt in ('-h', '--help'):
			pass#usage(exitCode=0)
		elif opt in ('-m', '--maintenance'):
			config['maintenance'].append(value)
		elif opt in ('-l', '--limits'):
			config['limit'] = int(value)
		elif opt in ('-p', '--population-size'):
			config['popSize'] = int(value)
		elif opt in ('-g', '--generations'):
			config['generations'] = int(value)
		else:
			assert False
        
	# Add in arguments
	config['start'] = args[0]
	config['args'] = args[1:]

	# Return configuration
	return config


def round15Minutes(tNow):
	"""
	Round a datetime instance to the nearst 15 minutes
	"""
	
	reducedTime = tNow.minute*60 + tNow.second + tNow.microsecond/1000000.0
	nearest15min = round(reducedTime / 900)*900
	diff = nearest15min - reducedTime
	
	sign = 1
	if diff < 0:
		sign = -1
		diff = abs(diff)
	
	diffSeconds = int(diff)
	diffMicroseconds = int(round((diff - diffSeconds)*1e6))
	
	return tNow + sign*timedelta(seconds=diffSeconds, microseconds=diffMicroseconds)


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


def shiftWeek(project, startWeek, observer=lwa1.getObserver()):
	# Get the observations
	nObs = len(project.sessions[0].observations)
	tStart = [None,]*nObs
	for i in xrange(nObs):
		tStart[i]  = utcjd_to_unix(project.sessions[0].observations[i].mjd + MJD_OFFSET)
		tStart[i] += project.sessions[0].observations[i].mpm / 1000.0
		tStart[i]  = datetime.utcfromtimestamp(tStart[i])
		tStart[i]  = _UTC.localize(tStart[i])
		
	# Get the shift mode
	if project.sessions[0].comments.find('ScheduleSolarMovable') != -1:
		mode = 'Solar'
	elif project.sessions[0].comments.find('ScheduleFixed') != -1:
		mode = 'Fixed'
	else:
		mode = 'Sideral'
		
	# Fixed mode
	if mode == 'Fixed':
		tNewStart = min(tStart)
		
	# Sidereal mode
	elif mode == 'Sidereal':
		# Get the LST at the start
		observer.date = (min(tStart)).strftime('%Y/%m/%d %H:%M:%S')
		lst = observer.sidereal_time()
		
		tNewStart = datetime.combine(startWeek, min(tStart).time())
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
		if siderealShift.date() < startWeek.date():
			siderealShift += siderealDay
		if siderealShift.date() > startWeek.date():
			siderealShift -= siderealDay
		tNewStart = siderealShift
		
	# Solar mode
	else:
		tNewStart = datetime.combine(startWeek, min(tStart).time())
		tNewStart = _UTC.localize(tNewStart)
	
	# Get the new shift needed to translate the old times to the new times
	tShift = tNewStart - min(tStart)
	
	# Shift the start times and recompute the MJD and MPM values
	for i in xrange(nObs):
		tStart[i] += tShift
		
	# Get the LST at the start
	observer.date = (min(tStart)).strftime('%Y/%m/%d %H:%M:%S')
	lst = observer.sidereal_time()
	
	# Apply
	for i in xrange(nObs):
		start = tStart[i].strftime("%Z %Y %m %d %H:%M:%S.%f")
		start = start[:-3]

		utc = Time(tStart[i], format=Time.FORMAT_PY_DATE)
		mjd = int(utc.utc_mjd)
		
		utcMidnight = datetime(tStart[i].year, tStart[i].month, tStart[i].day, 0, 0, 0, tzinfo=_UTC)
		diff = tStart[i] - utcMidnight
		mpm = int(round((diff.seconds + diff.microseconds/1000000.0)*1000.0))
		
		project.sessions[0].observations[i].mjd = mjd
		project.sessions[0].observations[i].mpm = mpm
		project.sessions[0].observations[i].start = start
		
	return project


def describeSDF(observer, project):
	"""
	Given an ephem.Observer instance and a sdf.project instance, display the
	SDF file in a descriptive manner.  This function returns a string (like a
	__str__ call).
	"""
	
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
	
	out = ""
	out += " Project ID: %s\n" % project.id
	out += " Session ID: %i\n" % project.sessions[0].id
	out += " Observations appear to start at %s\n" % (min(tStart)).strftime(formatString)
	out += " -> LST at %s for this date/time is %s\n" % (lwa1.name, lst)

	lastDur = project.sessions[0].observations[nObs-1].dur
	lastDur = timedelta(seconds=int(lastDur/1000), microseconds=(lastDur*1000) % 1000000)
	sessionDur = max(tStart) - min(tStart) + lastDur
	
	out += "\n"
	out += " Total Session Duration: %s\n" % sessionDur
	out += " -> First observation starts at %s\n" % min(tStart).strftime(formatString)
	out += " -> Last observation ends at %s\n" % (max(tStart) + lastDur).strftime(formatString)
	if project.sessions[0].observations[0].mode not in ('TBW', 'TBN'):
		drspec = 'No'
		if project.sessions[0].spcSetup[0] != 0 and project.sessions[0].spcSetup[1] != 0:
			drspec = 'Yes'
		drxBeam = project.sessions[0].drxBeam
		if drxBeam < 1:
			drxBeam = "MCS decides"
		else:
			drxBeam = "%i" % drxBeam
	out += " DRX Beam: %s\n" % drxBeam
	out += " DR Spectrometer used? %s\n" % drspec
	if drspec == 'Yes':
		out += " -> %i channels, %i windows/integration\n" % tuple(project.sessions[0].spcSetup)
	
	out += "\n"
	out += " Number of observations: %i\n" % nObs
	
	return out


class gas(object):
	class SimultaneousBlock(object):
		def __init__(self, projects, sessionLag=sessionLag, mode='Sidereal'):
			self.projects = projects
			self.mode = mode
			
			tStart = []
			tStop  = []
			for obs in projects[0].sessions[0].observations:
				start, stop = getObsStartStop(obs)
				
				tStart.append(start)
				tStop.append(stop)
			print max(tStop) - min(tStart), projects[0].id
			
			self.start = min(tStart) - sessionLag
			self.stop  = max(tStop)  + sessionLag
			self.duration = self.stop - self.start
			self.nBeams = len(projects)
			
		def getStartStop(self, offset=0):
			if self.mode == 'Sidereal':
				start = self.start + offset*siderealDay
				stop  = self.stop  + offset*siderealDay
			elif self.mode == 'Solar':
				start = self.start + offset*solarDay
				stop  = self.stop  + offset*solarDay
			else:
				start = self.start
				stop  = self.stop
				
			return start, stop
			
		def getDuration(self):
			return self.duration
			
		def getBeamCount(self):
			return self.nBeams
			
		def getProjects(self, offset=0):
			output = []
			
			for project in self.projects:
				# Get the observations
				nObs = len(project.sessions[0].observations)
				tStart = [None,]*nObs
				for i in xrange(nObs):
					tStart[i]  = utcjd_to_unix(project.sessions[0].observations[i].mjd + MJD_OFFSET)
					tStart[i] += project.sessions[0].observations[i].mpm / 1000.0
					tStart[i]  = datetime.utcfromtimestamp(tStart[i])
					tStart[i]  = _UTC.localize(tStart[i])
				
				# Shift the start times and recompute the MJD and MPM values
				for i in xrange(nObs):
					if self.mode == 'Sidereal':
						tStart[i] += offset*siderealDay
					elif self.mode == 'Solar':
						tStart[i] += offset*solarDay
					else:
						pass
				
				# Apply
				for i in xrange(nObs):
					start = tStart[i].strftime("%Z %Y %m %d %H:%M:%S.%f")
					start = start[:-3]
	
					utc = Time(tStart[i], format=Time.FORMAT_PY_DATE)
					mjd = int(utc.utc_mjd)
					
					utcMidnight = datetime(tStart[i].year, tStart[i].month, tStart[i].day, 0, 0, 0, tzinfo=_UTC)
					diff = tStart[i] - utcMidnight
					mpm = int(round((diff.seconds + diff.microseconds/1000000.0)*1000.0))
					
					print " Time shifting %s, session %i" % (project.id, project.sessions[0].id)
					print "  Now at %s" % start
					print "  MJD: %8i -> %8i" % (project.sessions[0].observations[i].mjd, mjd)
					print "  MPM: %8i -> %8i" % (project.sessions[0].observations[i].mpm, mpm)
					
					project.sessions[0].observations[i].mjd = mjd
					project.sessions[0].observations[i].mpm = mpm
					project.sessions[0].observations[i].start = start
						
				output.append(project)
		
			return output

	def __init__(self, start, nOffsets=100, searchLimits=10):
		self.start = start
		self.nOffsets = nOffsets
		self.searchLimits = searchLimits - 1
		self.maintenance = []
		
		self.offsets = []
		self.projects = []
		
	def setMaintenance(self, days, dayLength=8):
		for day in days:
			start = day
			stop  = start + timedelta(seconds=dayLength*3600)
			self.maintenance.append( (start,stop) )
			
	def clearMaintenance(self):
		self.maintenance = []
			
	def validateParameters(self):
		for project in self.projects:
			start, stop = project.getStartStop()
		
			if project.mode == 'Fixed':
				if start < self.start:
					raise RuntimeError("%s starts before the search period begins" % project)
				if stop >= self.start + self.searchLimits*solarDay:
					raise RuntimeError("%s stops after the search period ends" % project)
				
				for m1,m2 in self.maintenance:
					if start >= m1 and start <= m2 or stop >= m1 and stop <= m2:
						raise RuntimeError("%s runs during the maintenance period" % project)
		
	def defineProjects(self, projects, observer=lwa1.getObserver()):
		# Shift to the start week
		projects = [shiftWeek(p, self.start) for p in projects]
		
		# Idenfity the projects by name and LST
		sessionLSTs = []
		sessionNames = []
		sessionModes = []
		for project in projects:
			# Start, stop, and duration
			sessionStart = getObsStartStop(project.sessions[0].observations[ 0])[0] - sessionLag
			sessionStop  = getObsStartStop(project.sessions[0].observations[-1])[1] + sessionLag
			duration = sessionStop-sessionStart
			
			# Get the LST at the start
			observer.date = sessionStart.strftime('%Y/%m/%d %H:%M:%S')
			lst = observer.sidereal_time()
			
			# Get the schedule mode
			if project.sessions[0].comments.find('ScheduleSolarMovable') != -1:
				mode = 'Solar'
			elif project.sessions[0].comments.find('ScheduleFixed') != -1:
				mode = 'Fixed'
			else:
				mode = 'Sidereal'
			
			sessionLSTs.append(lst)
			sessionNames.append(project.id)
			sessionModes.append(mode)
		
		# Unique LST values and projects
		uniqueLSTs = []
		uniquePIDs = []
		for lst in sessionLSTs:
			fLST = int(round(lst*24/(2*numpy.pi)*3600))
			if fLST not in uniqueLSTs:
				uniqueLSTs.append(fLST)
		for pid in sessionNames:
			if pid not in uniquePIDs:
				uniquePIDs.append(pid)
		
		# Grouping
		sessions = []
		for lst in uniqueLSTs:
			for pid in uniquePIDs:
				group = []
				mode = 'Sidereal'
				for i in xrange(len(projects)):
					if int(round(sessionLSTs[i]*24/(2*numpy.pi)*3600)) == lst and sessionNames[i] == pid:
						group.append(projects[i])
						mode = sessionModes[i]
				if len(group) == 0:
					continue
						
				if len(group) > 4:
					print "-> Group too large, breaking"
					
					for i in xrange(len(group)/4 + 1):
						subGroup = group[i*4:(i+1)*4]
						if len(subGroup) == 0:
							continue
						
						print "   -> Found group of %i sessions (%s) at LST ~%s of type %s" % (len(subGroup), subGroup[0].id, ephem.hours(lst/3600.0/24*2*numpy.pi), mode)
						sessions.append(self.SimultaneousBlock(subGroup, mode=mode))
						
				else:
					print "-> Found group of %i sessions (%s) at LST ~%s of type %s" % (len(group), group[0].id, ephem.hours(lst/3600.0/24*2*numpy.pi), mode)
					sessions.append(self.SimultaneousBlock(group, mode=mode))
		
		print "Total number of sessionettes is %i" % len(sessions)
		self.projects = sessions
		
		self.nProjects = len(self.projects)
		self.offsets.append([0   for p in xrange(self.nProjects)])
		self.offsets.append([p/4 for p in xrange(self.nProjects)])
		for o in xrange(2,self.nOffsets):
			self.offsets.append( [random.randint(0, self.searchLimits) for p in xrange(self.nProjects)]  )
		
	def fitness(self):
		output = numpy.zeros(self.nOffsets)
		
		for l,offsets in enumerate(self.offsets):
			globalStart, globalStop = self.projects[0].getStartStop()
			
			# Compute the new start and stop times for all projects
			starts = []
			stops = []
			beams = []
			for p,o in zip(self.projects, offsets):
				start, stop = p.getStartStop(offset=o)
				beam = p.getBeamCount()
				starts.append(start)
				stops.append(stop)
				beams.append(beam)
			
			# Find time conflicts on the beams
			f = 0
			for i in xrange(self.nProjects):
				startI = starts[i]
				stopI = stops[i]
				beamsFree1 = 4 - beams[i]
				
				for m1,m2 in self.maintenance:
					if startI >= m1 and startI <= m2 or stopI >= m1 and stopI <= m2:
						f -= 4
						beamsFree1 -= 4
					if m1 >= startI and m1 <= stopI or m2 >= startI and m2 <= stopI:
						f -= 4
						beamsFree1 -= 4
				
				if startI < globalStart:
					globalStart = startI
				if stopI > globalStop:
					globalStop = stopI
				
				for j in xrange(i+1, self.nProjects):
					if j == i:
						continue
					
					startJ = starts[j]
					stopJ = stops[j]
					beamsUsed = beams[j]
					
					if startJ >= startI and startJ <= stopI or stopJ >= startI and stopJ <= stopI:
						if (beamsUsed - beamsFree1) > 0:
							f -= (beamsUsed - beamsFree1)
						else:
							beamsFree1 -= beamsUsed
							
					if startI >= startJ and startI <= stopJ or stopI >= startJ and stopI <= stopJ:
						if (beamsUsed - beamsFree1) > 0:
							f -= (beamsUsed - beamsFree1)
						else:
							beamsFree1 -= beamsUsed
			
			# Get the run duration in days
			runDuration = globalStop - globalStart
			runDuration = runDuration.days + runDuration.seconds/3600.0/24.0
			
			# Fitness is a combination of the run duration and the number of overlaps
			if f < 0:
				f -= self.searchLimits
			else:
				f -= runDuration
				
			output[l] = f
			
		return output
			
	def run(self, extinctionInterval=50, nIterations=160):
		fMin = []
		fMax = []
		fMean = []
		fStd = []
		
		for i in xrange(nIterations):
			self.__evolve()
			#f = []
			#for o in self.offsets:
				#f.append(self.fitness(o))
			#f = numpy.array(f)
			f = self.fitness()
				
			fMin.append(f.min())
			fMax.append(f.max())
			fMean.append(f.mean())
			fStd.append(f.std())
			
			print i, nIterations, f.max(), f.min(), f.mean()
			
			if i % extinctionInterval == 0 and i != 0:
				limit = numpy.ceil(-f.max())
				if limit > self.searchLimits:
					limit = self.searchLimits*2
				self.searchLimits = limit
				
				good = numpy.where( f >= -limit )[0]
				cut = numpy.where( f < -limit )[0]
				if len(cut) == 0:
					cut = random.sample(range(self.nOffsets), int(round(self.nOffsets*0.5)))
				print f[good].max(), f.max()
				print f[cut].max(), f.max()
				for c in cut:
					self.offsets[c] =  [random.randint(0, self.searchLimits) for p in xrange(self.nProjects)]
					
				print 'Extinction:', self.searchLimits, len(good), len(cut)
			
		return fMax, fMin, fMean, fStd
	
	def getBest(self):
		#f = []
		#for o in self.offsets:
			#f.append(self.fitness(o))
		f = self.fitness()
			
		best = f.argmax()
		output = []
		for p,o in zip(self.projects, self.offsets[best]):
			output.extend(p.getProjects(o))
		
		def startSort(x, y):
			xs  = x.sessions[0].observations[0].mjd*1e6
			xs += x.sessions[0].observations[0].mpm/1000.0
			ys  = y.sessions[0].observations[0].mjd*1e6
			ys += y.sessions[0].observations[0].mpm/1000.0
			
			if xs > ys:
				return 1
			elif xs < ys:
				return -1
			else:
				if x.sessions[0].id > y.sessions[0].id:
					return 1
				elif x.sessions[0].id < y.sessions[0].id:
					return -1
				else:
					return 0
			
		output.sort(cmp=startSort)
		beam = 1
		for i in xrange(len(output)):
			output[i].sessions[0].drxBeam = beam
			beam += 1
			if beam == 5:
				beam = 1
			
		return output
		
	def __mutate(self, offsets, fraction=0.2):
		out = []
		
		for i in xrange(len(offsets)):
			if random.random() < fraction:
				newGene = random.randint(0, self.searchLimits)
				out.append(newGene)
			else:
				out.append(offsets[i])
				
		return out
				
	def __crossover(self, offsets1, offsets2, offsets3, fraction=0.5, mutate=True):
		out1 = []
		out2 = []
		out3 = []
		
		cp1 = random.randint(1,len(offsets1)-4)
		cp2 = random.randint(cp1+1, len(offsets1)-2)
		
		out1 = offsets1[:cp1]
		out1.extend(offsets2[cp1:cp2])
		out1.extend(offsets1[cp2:])
		
		out2 = offsets2[:cp1]
		out2.extend(offsets3[cp1:cp2])
		out2.extend(offsets2[cp2:])
		
		cp1 = random.randint(1,len(offsets1)-4)
		cp2 = random.randint(cp1+1, len(offsets1)-2)
		
		out3 = offsets3[:cp1]
		out3.extend(offsets2[cp1:cp2])
		out3.extend(offsets1[cp2:])
		
#		for i in xrange(len(offsets1)):
# 			genes = random.sample([offsets1[i], offsets2[i], offsets3[i]], 3)
# 			out1.append(genes[0])
# 			out2.append(genes[1])
# 			out3.append(genes[2])
			
		if mutate:
			out1 = self.__mutate(out1, fraction=0.1)
			out2 = self.__mutate(out2, fraction=0.1)
			out3 = self.__mutate(out3, fraction=0.1)
				
		return out1, out2, out3
		
	def __evolve(self, fraction=0.1):
		#f = []
		#for o in self.offsets:
			#f.append(self.fitness(o))
		
		#f = numpy.array(f)
		f = self.fitness()
		
		best  = numpy.where( f >= percentile(f, (100-fraction*100)) )[0]
	
		children = []
		while len(children) < int(round(self.nOffsets*fraction)):
			best1 = random.sample(best, len(best)/3)
			best2 = [b for b in best if b not in best1]
			best3 = best2[len(best1):]
			best2 = best2[:len(best1)]
		
			for i in xrange(len(best1)):
				children.extend( self.__crossover(self.offsets[best1[i]], self.offsets[best2[i]], self.offsets[best3[i]]) )
		
		#print "Population size: %i" % self.nOffsets
		#print "-> Elite children: %i" % len(best)
		#print "-> Crossover children: %i" % len(children)
		#print "-> Mutations: %i" % (self.nOffsets - len(best) - len(children))
		
		new = []
		new.extend([self.offsets[b] for b in best])
		new.extend(children)
		needed = self.nOffsets - len(new)
		for i in xrange(needed):
			new.append( self.__mutate(random.choice(self.offsets)) )
		
		new = new[:self.nOffsets]
		#print "-> New population size: %i" % len(new)
		
		self.offsets = new


def main(args):
	config = parseOptions(args)

	y,m,d = config['start'].split('-', 2)
	startWeek = datetime(int(y), int(m), int(d), 0, 0, 0)
	startWeek = _UTC.localize(startWeek)
	
	maintenance = []
	for value in config['maintenance']:
		y,m,d = value.split('-', 2)
		maintenance.append( datetime(int(y), int(m), int(d), 9, 0, 0) )
		maintenance[-1] = _MST.localize(maintenance[-1])
		maintenance[-1] = maintenance[-1].astimezone(_UTC)
	maintenance.sort()
	
	# Load the station
	observer = lwa1.getObserver()
	
	sessionSDFs = []
	for filename in config['args']:
		project = sdf.parseSDF(filename)
		sessionSDFs.append(project)
	
	# Try to work out a schedule
	observer.date = startWeek.strftime('%Y/%m/%d %H:%M:%S')
	startWeekLST = observer.sidereal_time()
	
	print "Schedule Setup:"
	print "  Start day: %s" % startWeek.strftime("%A, %Y-%m-%d")
	print "  Search period: %i days" % config['limit']
	print "  Maintenance Days:"
	if len(maintenance) == 0:
		print "    None"
	else:
		for m in maintenance:
			print "    %s" % m.astimezone(_MST).strftime("%A %Y-%m-%d")
	print " "
	print "Observations:"
	print "  SDF count: %i" % len(sessionSDFs)
	print " "
	
	g = gas(startWeek, nOffsets=config['popSize'], searchLimits=config['limit'])
	g.defineProjects(sessionSDFs)
	g.setMaintenance(maintenance)
	g.validateParameters()
	fMax, fMin, fMean, fStd = g.run(nIterations=config['generations'])
	
	# Plot population evolution
	fig = plt.figure()
	ax = fig.gca()
	ax.plot(fMax)
	#ax.plot(fMin)
	ax.plot(fMean)
	
	sessionSDFs = []
	sessionLSTs = []
	sessionNames = []
	sessionBeams = []
	sessionStarts = []
	sessionDurations = []
	
	beamHours = 0
	for project in g.getBest():
		pID = project.id
		sID = project.sessions[0].id
		
		if project.sessions[0].observations[0].mode in ('TBW', 'TBN'):
			beam = 5
		else:
			beam = project.sessions[0].drxBeam
		sessionStart = getObsStartStop(project.sessions[0].observations[ 0])[0] - sessionLag
		sessionStop  = getObsStartStop(project.sessions[0].observations[-1])[1] + sessionLag
		duration = sessionStop-sessionStart
		
		# Get the LST at the start
		observer.date = sessionStart.strftime('%Y/%m/%d %H:%M:%S')
		lst = observer.sidereal_time()
		
		sessionSDFs.append(project)
		sessionLSTs.append(lst)
		sessionNames.append('%s_%04i' % (pID, sID))
		sessionBeams.append(beam)
		sessionStarts.append(sessionStart)
		sessionDurations.append(duration)
		
		for obs in project.sessions[0].observations:
			start, stop = getObsStartStop(obs)
			diff = stop - start
			beamHours += diff.seconds/3600.0 + diff.days*24
			
	first = sessionStarts.index( min(sessionStarts) )
	last  = sessionStarts.index( max(sessionStarts) )
	scheduleDuration = sessionStarts[last] + sessionDurations[last] - sessionStarts[first]
	scheduleDuration = scheduleDuration.seconds/3600.0 + scheduleDuration.days*24
	
	print beamHours, 4*scheduleDuration, beamHours / scheduleDuration / 4
	
	# Plotting
	fig = plt.figure()
	ax = fig.gca()
	
	uniqueProjects = []
	for name in sessionNames:
		pID, sID = name.split('_', 1)
		if pID not in uniqueProjects:
			uniqueProjects.append(pID)
	colors = ['b','g','c','m','y']
	
	# Plot the sessions
	startMin = min(sessionStarts)
	startMax = max(sessionStarts)
	for name,beam,start,duration in zip(sessionNames, sessionBeams, sessionStarts, sessionDurations):
		d = duration.days*24*3600 + duration.seconds + duration.microseconds/1e6
		d /= 3600.0
		
		i = uniqueProjects.index(name.split('_')[0])
		ax.barh(beam-0.5, d/24, left=start, height=1.0, alpha=0.1, color=colors[i % len(colors)])
		
		ax.text(start+duration/2, beam, name, size=10, horizontalalignment='center', verticalalignment='center', rotation='vertical')
	
	# Plot free time more than 15 minutes
	frees = []
	tNow = round15Minutes(startMin)
	freeLength = timedelta(seconds=900)
	while tNow <= startMax:
		free = True
		
		for start,duration in zip(sessionStarts, sessionDurations):
			if tNow >= start and tNow <= start+duration:
				free = False
			if tNow+freeLength >= start and tNow+freeLength <= start+duration:
				free = False
				
		for m1 in maintenance:
			m2 = m1 + timedelta(seconds=8*3600)
			if tNow >= m1 and tNow <= m2:
				free = False
			if tNow+freeLength >= m1 and tNow+freeLength <= m2:
				free = False
				
		if free:
			frees.append(tNow)
		tNow += timedelta(seconds=900)
	freePeriods = [[frees[0], frees[0]],]
	for free in frees:
		if free-freePeriods[-1][1] <= freeLength:
			freePeriods[-1][1] = free
		else:
			freePeriods.append([free, free])
			
	for free1,free2 in freePeriods:
		duration = free2 - free1
		d = duration.days*24*3600 + duration.seconds + duration.microseconds/1e6
		d /= 3600.0
		if d < 0.5:
			continue
		
		ax.barh(-0.5, d/24, left=free1, alpha=0.1, height=1.0, color='r', hatch='/')
		ax.text(free1+duration/2, 0, '%i:%02i' % (int(d), int((d-int(d))*60)), size=10, horizontalalignment='center', verticalalignment='center', rotation='vertical')
	
	for m1 in maintenance:
		if m1 < min(sessionStarts) or m1 > max(sessionStarts):
			continue
	
		ax.barh(0.5, 8./24, left=m1, alpha=0.1, height=5.0, color='y', hatch='#')
		ax.text(m1+timedelta(seconds=4*3600), 3, 'Maintenance', size=10, horizontalalignment='center', verticalalignment='center', rotation='vertical')
	
	# Fix the x axis labels
	ax2 = ax.twiny()
	ax2.set_xticks(ax.get_xticks())
	ax2.set_xlim(ax.get_xlim())
	
	ax.xaxis.set_major_formatter( matplotlib.dates.DateFormatter("%Y-%m-%d\n%H:%M:%S", tz=_UTC))
	ax.set_xlabel('Time [UTC]')
	ax2.xaxis.set_major_formatter(matplotlib.dates.DateFormatter("%Y-%m-%d\n%H:%M:%S", tz=_MST))
	ax2.set_xlabel('Time [Mountain]')
	fig.autofmt_xdate()
	
	# Fix the y axis labels
	ax.set_ylim((6, -1.5))
	ax.set_yticks([6, 5.75, 5, 4, 3, 2, 1, 0, -1, -2])
	ax.set_yticklabels(['', 'Day/Night', 'TBN/TBW', 'Beam 4', 'Beam 3', 'Beam 2', 'Beam 1', 'Unassigned', 'MCS Decides', ''])
	
	# Setup the interaction with the boxes
	def onclick(event, projects=sessionSDFs, names=sessionNames, beams=sessionBeams, starts=sessionStarts, durations=sessionDurations, free=freePeriods):
		clickBeam = round(event.ydata)
		clickTime = matplotlib.dates.num2date(event.xdata)
		
		if clickBeam == 0:
			for i in xrange(len(free)):
				if clickTime >= free[i][0] and clickTime <= free[i][1]:
					fU = free[i]
					fM = (fU[0].astimezone(_MST), fU[1].astimezone(_MST))
					d = fU[1] - fU[0]
					print "Free time between %s and %s" % (fU[0].strftime(formatString), fU[1].strftime(formatString))
					print "               -> %s and %s" % (fM[0].strftime(formatString), fM[1].strftime(formatString))
					print "               -> %i:%02i in length" % (d.days*24+d.seconds/3600, d.seconds/60 % 60)
		else:
			project = None
			for i in xrange(len(projects)):
				if clickTime >= starts[i] and clickTime <= starts[i] + durations[i] and clickBeam == beams[i]:
					project = projects[i]
					break
			
			if project is not None:
				print describeSDF(observer, project)
			
	cid = fig.canvas.mpl_connect('button_press_event', onclick)
	
	plt.show()


if __name__ == "__main__":
	main(sys.argv[1:])
	
