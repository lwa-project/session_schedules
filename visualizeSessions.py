#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import sys
import math
import pytz
import ephem
import numpy
from datetime import datetime, timedelta

from lsl.common import sdf, metabundle
from lsl.common import stations
from lsl.astro import utcjd_to_unix, MJD_OFFSET

import wx
from wx.lib.mixins.listctrl import CheckListCtrlMixin

import matplotlib
matplotlib.use('WXAgg')
matplotlib.interactive(True)

from matplotlib.backends.backend_wxagg import NavigationToolbar2WxAgg, FigureCanvasWxAgg
from matplotlib.figure import Figure

import matplotlib.dates
from matplotlib import pyplot as plt
from matplotlib.collections import LineCollection

__version__ = "0.1"
__revision__ = "$Rev$"
__author__ = "Jayce Dowell"

# Date/time manipulation
_UTC = pytz.utc
_MST = pytz.timezone('US/Mountain')
formatString = '%Y/%m/%d %H:%M:%S.%f %Z'

# MCS session padding extent
sessionLag = timedelta(seconds=5)


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


class FilesListCtrl(wx.ListCtrl, CheckListCtrlMixin):
	"""
	Class that combines a list with check boxes.  This is used for making the remove files
	dialog actually work.
	"""
	
	def __init__(self, parent, **kwargs):
		wx.ListCtrl.__init__(self, parent, style=wx.LC_REPORT, **kwargs)
		CheckListCtrlMixin.__init__(self)


class Visualization_GUI(object):
	"""
	Class to handle the parsing and plotting of the SDF files selected in the GUI.  The
	loadFiles() function relies on the 'filenames' attribute of the parent to be a list 
	of valid SDF filenames.
	"""
	
	def __init__(self, frame, observer=stations.lwa1.getObserver()):
		self.frame = frame
		self.observer = observer
		self.showDayNight = True
		self.showJupiter = False
		
		self.colors = ['Blue','Green','Cyan','Magenta','Yellow', 
					'Peru', 'Moccasin', 'Orange', 'DarkOrchid']
		
		self.sessionSDFs = []
		self.sessionNames = []
		self.sessionBeams = []
		self.sessionStarts = []
		self.sessionDurations = []
		self.unqiueProjects = []
	
	def loadFiles(self):
		"""
		Load in the SDF files listed in self.parent.filenames and compute the free time.
		"""
		
		sessionSDFs = []
		sessionNames = []
		sessionBeams = []
		sessionStarts = []
		sessionStops = []
		sessionDurations = []
		sessionDataFiles = []
	
		# Loop over filenames
		for filename in self.frame.filenames:
			try:
				project = sdf.parseSDF(filename)
				dataFile = None
			except Exception as e:
				try:
					project = metabundle.getSessionDefinition(filename)
					dataFile = metabundle.getSessionMetaData(filename)
				except Exception as e:
					print "Warning: Cannot parse '%s'" % os.path.basename(filename)
					continue
					
			pID = project.id
			sID = project.sessions[0].id
			
			if project.sessions[0].observations[0].mode in ('TBW', 'TBN'):
				beam = 5
			else:
				beam = project.sessions[0].drxBeam
			sessionStart = getObsStartStop(project.sessions[0].observations[ 0])[0] - sessionLag
			sessionStop  = getObsStartStop(project.sessions[0].observations[-1])[1] + sessionLag
			duration = sessionStop-sessionStart
			
			sessionSDFs.append(project)
			sessionNames.append('%s_%04i' % (pID, sID))
			sessionBeams.append(beam)
			sessionStarts.append(sessionStart)
			sessionStops.append(sessionStop)
			sessionDurations.append(duration)
			sessionDataFiles.append(dataFile)
			
		# Find unique project identifiers
		uniqueProjects = []
		for name in sessionNames:
			pID, sID = name.split('_', 1)
			if pID not in uniqueProjects:
				uniqueProjects.append(pID)
		
		# Save the data
		self.sessionSDFs = sessionSDFs
		self.sessionNames = sessionNames
		self.sessionBeams = sessionBeams
		self.sessionStarts = sessionStarts
		self.sessionStops = sessionStops
		self.sessionDurations = sessionDurations
		self.sessionDataFiles = sessionDataFiles
		self.uniqueProjects = uniqueProjects
		
		# Compute the free time
		self.getFreeTime()
		
		try:
			self.disconnect()
		except:
			pass
		self.connect()
		
	def getFreeTime(self, step=timedelta(seconds=900)):
		"""
		Using the list of SDFs read in by loadFiles(), find times when nothing is 
		scheduled and save these 'free' times to self.freePeriods.  This attribute
		contains a list of two-element tuples (free time start, stop).
		"""
		
		startMin = min(self.sessionStarts)
		startMax = max(self.sessionStarts)
		
		# Find free time in 15 minutes chunks
		frees = []
		tNow = round15Minutes(startMin)
		while tNow <= startMax:
			free = True
			# Loop over SDFs
			for start,duration in zip(self.sessionStarts, self.sessionDurations):
				if tNow >= start and tNow <= start+duration:
					free = False
				if tNow+step >= start and tNow+step <= start+duration:
					free = False
			if free:
				frees.append(tNow)
			tNow += step
		
		# Use the free times to come up with free periods
		if len(frees) > 0:
			freePeriods = [[frees[0], frees[0]],]
			for free in frees:
				if free-freePeriods[-1][1] <= step:
					freePeriods[-1][1] = free
				else:
					freePeriods.append([free, free])
		else:
			freePeriods = []
			
		# Save
		self.freePeriods = freePeriods
		
	def getSolarElevation(self, step=timedelta(seconds=900)):
		"""
		Using the time range of all loaded SDFs, return a two-element tuple of the 
		time and altitude for the Sun in 15 minute steps.
		"""
		
		# Find out how long we need to compute the position of the Sun
		start = min(self.sessionStarts)
		stop  = max(self.sessionStops)
		
		# Define the Sun
		Sun = ephem.Sun()
		
		# Go!
		tNow = start
		points = []
		alts = []
		while tNow <= stop:
			self.observer.date = tNow.strftime('%Y/%m/%d %H:%M:%S')
			Sun.compute(self.observer)
			alt = float(Sun.alt)*180/math.pi
			
			s = tNow - start
			s = s.days*24*3600 + s.seconds + s.microseconds/1e6
			s /= 3600.0

			points.append([matplotlib.dates.date2num(tNow),6])
			alts.append(alt)
			
			tNow += step
			
		return numpy.array(points), numpy.array(alts)


	def getJovianElevation(self, step=timedelta(seconds=900)):
		"""
		Using the time range of all loaded SDFs, return a two-element tuple of the 
		time and altitude for Jupiter in 15 minute steps.
		"""
		
		# Find out how long we need to compute the position of the Sun
		start = min(self.sessionStarts)
		stop  = max(self.sessionStops)
		
		# Setup Jupiter
		Jupiter = ephem.Jupiter()
		
		# Go!
		tNow = start
		points = []
		alts = []
		while tNow <= stop:
			self.observer.date = tNow.strftime('%Y/%m/%d %H:%M:%S')
			Jupiter.compute(self.observer)
			alt = float(Jupiter.alt)*180/math.pi
			
			s = tNow - start
			s = s.days*24*3600 + s.seconds + s.microseconds/1e6
			s /= 3600.0

			points.append([matplotlib.dates.date2num(tNow),6])
			alts.append(alt)
			
			tNow += step
			
		return numpy.array(points), numpy.array(alts)
		
	def draw(self, selected=None):
		"""
		Shows the sessions.
		"""
		
		self.frame.figure.clf()
		self.ax1 = self.frame.figure.gca()
		
		# Plot the sessions
		startMin = min(self.sessionStarts)
		startMax = max(self.sessionStarts)
		for s,(name,beam,start,duration) in enumerate(zip(self.sessionNames, self.sessionBeams, self.sessionStarts, self.sessionDurations)):
			d = duration.days*24*3600 + duration.seconds + duration.microseconds/1e6
			d /= 3600.0
			
			i = self.uniqueProjects.index(name.split('_')[0])
			if s == selected:
				alpha = 0.5
			else:
				alpha = 0.2
			self.ax1.barh(beam-0.5, d/24, left=start, height=1.0, alpha=alpha, color=self.colors[i % len(self.colors)])
			
			self.ax1.text(start+duration/2, beam, name, size=10, horizontalalignment='center', verticalalignment='center', rotation='vertical')
			
		# Plot the free time more than 30 minutes
		for s,(free1,free2) in enumerate(self.freePeriods):
			duration = free2 - free1
			d = duration.days*24*3600 + duration.seconds + duration.microseconds/1e6
			d /= 3600.0
			if d < 0.5:
				continue
				
			if -(s+1) == selected:
				alpha = 0.5
			else:
				alpha = 0.2
			self.ax1.barh(-0.5, d/24, left=free1, alpha=alpha, height=1.0, color='r', hatch='/')
			self.ax1.text(free1+duration/2, 0, '%i:%02i' % (int(d), int((d-int(d))*60)), size=10, horizontalalignment='center', verticalalignment='center', rotation='vertical')
			
		# Plot Sun elevation in a way that indicates day and night (if needed)
		if self.showDayNight:
			points, alts = self.getSolarElevation()
			points = points.reshape(-1, 1, 2)
			points[:,:,1] = 5.75
			segments = numpy.concatenate([points[:-1], points[1:]], axis=1)
			lc = LineCollection(segments, cmap=plt.get_cmap('Blues_r'), norm=plt.Normalize(-18, 0.25))
			lc.set_array(alts)
			lc.set_linewidth(5)
			self.ax1.add_collection(lc)
			
		# Plot Jupiter's elevation (if needed)
		if self.showJupiter:
			points, alts = self.getJovianElevation()
			points = points.reshape(-1, 1, 2)
			points[:,:,1] = -1.75
			segments = numpy.concatenate([points[:-1], points[1:]], axis=1)
			lc = LineCollection(segments, cmap=plt.get_cmap('RdYlGn'), norm=plt.Normalize(0, 90))
			lc.set_array(alts)
			lc.set_linewidth(5)
			self.ax1.add_collection(lc)
			
		# Fix the x axis labels so that we have both MT and UT
		self.ax2 = self.ax1.twiny()
		self.ax2.set_xticks(self.ax1.get_xticks())
		self.ax2.set_xlim(self.ax1.get_xlim())
		
		self.ax1.xaxis.set_major_formatter( matplotlib.dates.DateFormatter("%Y-%m-%d\n%H:%M:%S", tz=_UTC))
		self.ax1.set_xlabel('Time [UTC]')
		self.ax2.xaxis.set_major_formatter(matplotlib.dates.DateFormatter("%Y-%m-%d\n%H:%M:%S", tz=_MST))
		self.ax2.set_xlabel('Time [Mountain]')
		self.frame.figure.autofmt_xdate()
		
		# Fix the y axis labels to use beams, free time, etc.
		if self.showDayNight:
			lower = 6
		else:
			lower = 5.5
		if self.showJupiter:
			upper = -2
		else:
			upper = -1.5
		self.ax1.set_ylim((lower, upper))
		self.ax1.set_yticks([6, 5.75, 5, 4, 3, 2, 1, 0, -1, -1.75, -2])
		self.ax1.set_yticklabels(['', 'Day/Night', 'TBN/TBW', 'Beam 4', 'Beam 3', 'Beam 2', 'Beam 1', 'Unassigned', 'MCS Decides', 'Jupiter', ''])
			
		self.frame.canvas.draw()
		
	def describeSDF(self, sdfIndex):
		"""
		Given an self.sessionSDFs index, display the SDF file in a descriptive manner.
		This function returns a string (like a __str__ call).
		"""
		
		# Get the SDF/data file collection in question
		project = self.sessionSDFs[sdfIndex]
		dataFile = self.sessionDataFiles[sdfIndex]
		
		nObs = len(project.sessions[0].observations)
		tStart = [None,]*nObs
		for i in xrange(nObs):
			tStart[i]  = utcjd_to_unix(project.sessions[0].observations[i].mjd + MJD_OFFSET)
			tStart[i] += project.sessions[0].observations[i].mpm / 1000.0
			tStart[i]  = datetime.utcfromtimestamp(tStart[i])
			tStart[i]  = _UTC.localize(tStart[i])
		
		# Get the LST at the start
		self.observer.date = (min(tStart)).strftime('%Y/%m/%d %H:%M:%S')
		lst = self.observer.sidereal_time()
		
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
		else:
			out += " Transient Buffer: %s\n" % ('Wide band' if project.sessions[0].observations[0].mode == 'TBW' else 'Narrow band',)
		
		out += "\n"
		out += " Number of observations: %i\n" % nObs
		out += " Observation Detail:\n"
		for i in xrange(nObs):
			currDur = project.sessions[0].observations[i].dur
			currDur = timedelta(seconds=int(currDur/1000), microseconds=(currDur*1000) % 1000000)
			
			out += "  Observation #%i\n" % (i+1,)
			
			## Basic setup
			out += "   Target: %s\n" % project.sessions[0].observations[i].target
			out += "   Mode: %s\n" % project.sessions[0].observations[i].mode
			out += "   Start:\n"
			out += "    MJD: %i\n" % project.sessions[0].observations[i].mjd
			out += "    MPM: %i\n" % project.sessions[0].observations[i].mpm
			out += "    -> %s\n" % getObsStartStop(project.sessions[0].observations[i])[0].strftime(formatString)
			out += "   Duration: %s\n" % currDur
			
			## DP setup
			if project.sessions[0].observations[i].mode not in ('TBW',):
				out += "   Tuning 1: %.3f MHz\n" % (project.sessions[0].observations[i].frequency1/1e6,)
			if project.sessions[0].observations[i].mode not in ('TBW', 'TBN'):
				out += "   Tuning 2: %.3f MHz\n" % (project.sessions[0].observations[i].frequency2/1e6,)
			if project.sessions[0].observations[i].mode not in ('TBW',):
				out += "   Filter code: %i\n" % project.sessions[0].observations[i].filter
				
			## Comments/notes
			out += "   Observer Comments: %s\n" % project.sessions[0].observations[i].comments
			
			## Data file (optional)
			if dataFile is not None:
				try:
					dataFilename = dataFile[i+1]
					out += "   Data File Tag: %s\n" % dataFilename['tag']
				except KeyError:
					pass
					
		return out
		
	def describeFree(self, freeIndex):
		"""
		Given a self.freePeriods index, describe a block of free time.  This function 
		returns a string (like a __str__ call).
		"""
		
		# UT and MT
		fUT = self.freePeriods[freeIndex]
		fMT = (fUT[0].astimezone(_MST), fUT[1].astimezone(_MST))
		d = fUT[1] - fUT[0]
		
		out  = ""
		out += "Free time between %s and %s\n" % (fUT[0].strftime(formatString), fUT[1].strftime(formatString))
		out += "               -> %s and %s\n" % (fMT[0].strftime(formatString), fMT[1].strftime(formatString))
		out += "               -> %i:%02i in length\n" % (d.days*24+d.seconds/3600, d.seconds/60 % 60)
		
		return out
		
	def connect(self):
		"""
		Connect to all the events we need
		"""
		
		self.cidpress = self.frame.figure.canvas.mpl_connect('button_press_event', self.on_press)
		
	def on_press(self, event):
		"""
		On button press we will see if the mouse is over us and display some data
		"""
		
		if event.inaxes:
			clickBeam = round(event.ydata)
			clickTime = matplotlib.dates.num2date(event.xdata)
			
			if clickBeam == 0:
				for i in xrange(len(self.freePeriods)):
					if clickTime >= self.freePeriods[i][0] and clickTime <= self.freePeriods[i][1]:
						self.frame.info.SetValue(self.describeFree(i))
						self.draw(selected=-(i+1))
			else:
				project = None
				for i in xrange(len(self.sessionSDFs)):
					if clickTime >= self.sessionStarts[i] and clickTime <= self.sessionStarts[i] + self.sessionDurations[i] and clickBeam == self.sessionBeams[i]:
						self.frame.info.SetValue(self.describeSDF(i))
						self.draw(selected=i)
						
	def disconnect(self):
		"""
		Disconnect all the stored connection IDs.
		"""
		
		self.frame.figure1.canvas.mpl_disconnect(self.cidpress)


ID_ADD_FILES = 10
ID_REMOVE_FILES = 11
ID_QUIT = 12
ID_SHOW_DAYNIGHT = 20
ID_SHOW_JUPITER = 21
ID_ABOUT = 30

class MainWindow(wx.Frame):
	"""
	Main wxPython window for displaying the sessions and adding/removing files.
	"""
	
	def __init__(self, parent, id):
		self.dirname = ''
		self.scriptPath = os.path.abspath(__file__)
		self.scriptPath = os.path.split(self.scriptPath)[0]
		
		self.data = None
		self.filenames = []
		
		wx.Frame.__init__(self, parent, id, title="Visualize Sessions", size=(600,800))
		
		self.initUI()
		self.initEvents()
		self.Show()
		
	def initUI(self):
		"""
		Start the user interface.
		"""
		
		menubar = wx.MenuBar()
		
		fileMenu = wx.Menu()
		dispMenu = wx.Menu()
		helpMenu = wx.Menu()
		
		# File menu items
		add = wx.MenuItem(fileMenu, ID_ADD_FILES, '&Add File(s)')
		fileMenu.AppendItem(add)
		remove = wx.MenuItem(fileMenu, ID_REMOVE_FILES, '&Remove File(s)')
		fileMenu.AppendItem(remove)
		fileMenu.AppendSeparator()
		quit = wx.MenuItem(fileMenu, ID_QUIT, '&Quit')
		fileMenu.AppendItem(quit)
		
		# Display menu items
		daynight = wx.MenuItem(dispMenu, ID_SHOW_DAYNIGHT, 'Show Day/Night', kind=wx.ITEM_CHECK)
		dispMenu.AppendItem(daynight)
		jupiter = wx.MenuItem(dispMenu, ID_SHOW_JUPITER, 'Show Jupiter Visibility', kind=wx.ITEM_CHECK)
		dispMenu.AppendItem(jupiter)
		
		# Help menu items
		about = wx.MenuItem(helpMenu, ID_ABOUT, '&About')
		helpMenu.AppendItem(about)
		
		menubar.Append(fileMenu, '&File')
		menubar.Append(dispMenu, '&Display')
		menubar.Append(helpMenu, '&Help')
		self.SetMenuBar(menubar)
		
		# Menu defaults
		daynight.Check(True)
		jupiter.Check(False)
		
		vbox = wx.BoxSizer(wx.VERTICAL)
		
		# Add SDF plot
		panel1 = wx.Panel(self, -1)
		hbox1 = wx.BoxSizer(wx.VERTICAL)
		self.figure = Figure()
		self.canvas = FigureCanvasWxAgg(panel1, -1, self.figure)
		self.toolbar = NavigationToolbar2WxAgg(self.canvas)
		self.toolbar.Realize()
		hbox1.Add(self.canvas, 1, wx.EXPAND)
		hbox1.Add(self.toolbar, 0, wx.LEFT | wx.FIXED_MINSIZE)
		panel1.SetSizer(hbox1)
		vbox.Add(panel1, 1, wx.EXPAND)
		
		# Info window
		panel2 = wx.Panel(self, -1)
		hbox2 = wx.BoxSizer(wx.HORIZONTAL)
		self.info = wx.TextCtrl(panel2, style=wx.TE_MULTILINE|wx.EXPAND|wx.TE_READONLY, size=(600,400))
		hbox2.Add(self.info, 1, wx.EXPAND)
		panel2.SetSizer(hbox2)
		vbox.Add(panel2, 1, wx.EXPAND)
		
		# Use some sizers to see layout options
		self.SetSizer(vbox)
		self.SetAutoLayout(1)
		vbox.Fit(self)
		
	def initEvents(self):
		"""
		Set all of the various events in the main window.
		"""
		
		# File menu events
		self.Bind(wx.EVT_MENU, self.onAddFiles, id=ID_ADD_FILES)
		self.Bind(wx.EVT_MENU, self.onRemoveFiles, id=ID_REMOVE_FILES)
		self.Bind(wx.EVT_MENU, self.onQuit, id=ID_QUIT)
		
		# Display menu events
		self.Bind(wx.EVT_MENU, self.onDayNight, id=ID_SHOW_DAYNIGHT)
		self.Bind(wx.EVT_MENU, self.onJupiter, id=ID_SHOW_JUPITER)
		
		# Help menu events
		self.Bind(wx.EVT_MENU, self.onAbout, id=ID_ABOUT)
		
		# Make the images resizable
		self.Bind(wx.EVT_PAINT, self.resizePlot)
		
	def onAddFiles(self, event):
		"""
		Open a file or files.
		"""
		
		dlg = wx.FileDialog(self, "Choose a file", self.dirname, "", "*.*", wx.OPEN|wx.FD_MULTIPLE)
		if dlg.ShowModal() == wx.ID_OK:
			self.dirname = dlg.GetDirectory()
			filenames = dlg.GetFilenames()
			for filename in filenames:
				filename = os.path.join(self.dirname, filename)
				if filename not in self.filenames:
					self.filenames.append(filename)
					
			if self.data is None:
				self.data = Visualization_GUI(self)
				
			self.data.loadFiles()
			self.data.draw()
		dlg.Destroy()
		
	def onRemoveFiles(self, event):
		"""
		Remove a file or files.
		"""
		
		RemoveFilesDialog(self)
		
	def onDayNight(self, event):
		"""
		Toggle whether or not the day/night indicator is shown.
		"""
		
		if self.data is not None:
			if event.Checked():
				self.data.showDayNight = True
			else:
				self.data.showDayNight = False
			self.data.draw()
			
	def onJupiter(self, event):
		"""
		Toggle whether or not the Jupiter visibility indicator is shown.
		"""
		
		if self.data is not None:
			if event.Checked():
				self.data.showJupiter = True
			else:
				self.data.showJupiter = False
			self.data.draw()
			
	def onAbout(self, event):
		"""
		Display a ver very very brief 'about' window.
		"""
		
		dialog = wx.AboutDialogInfo()
		
		dialog.SetIcon(wx.Icon(os.path.join(self.scriptPath, 'icons', 'lwa.png'), wx.BITMAP_TYPE_PNG))
		dialog.SetName('Visualize Sessions')
		dialog.SetVersion(__version__)
		dialog.SetDescription("""GUI for displaying the current\nLWA1 schedule via its SDFs.""")
		dialog.SetWebSite('http://lwa.unm.edu')
		dialog.AddDeveloper(__author__)
		
		# Debuggers/testers
		dialog.AddDocWriter(__author__)
		
		wx.AboutBox(dialog)
	
	def onQuit(self, event):
		"""
		Quit the main window.
		"""
		
		self.Destroy()
		
	def resizePlot(self, event):
		w, h = self.GetSize()
		dpi = self.figure.get_dpi()
		newW = 1.0*w/dpi
		newH = 1.0*w/dpi
		self.figure.set_size_inches((newW, newH))
		self.figure.canvas.draw()

	def GetToolBar(self):
		# You will need to override GetToolBar if you are using an 
		# unmanaged toolbar in your frame
		return self.toolbar


ID_REMOVE_LISTCTRL = 100
ID_REMOVE_OK = 101
ID_REMOVE_CANCEL = 102

class RemoveFilesDialog(wx.Frame):
	"""
	Class to implement a window that displays a checkable list of filenames so 
	that the selected files can be removed from the plot window.
	"""
	
	def __init__(self, parent):
		wx.Frame.__init__(self, parent, title='Select Files to Remove', size=(600,300))
		
		self.parent = parent
		
		self.initUI()
		self.initEvents()
		self.loadFiles()
		self.Show()
		
	def initUI(self):
		"""
		Start the user interface.
		"""
		
		# File list
		hbox = wx.BoxSizer(wx.VERTICAL)
		panel = wx.Panel(self, -1)
		
		self.listControl = FilesListCtrl(panel, id=ID_REMOVE_LISTCTRL)
		self.listControl.parent = self
		hbox.Add(self.listControl, 1, wx.EXPAND)
		
		# Buttons
		ok = wx.Button(panel, ID_REMOVE_OK, 'Ok', size=(90, 28))
		cancel = wx.Button(panel, ID_REMOVE_CANCEL, 'Cancel', size=(90, 28))
		hbox.Add(ok)
		hbox.Add(cancel)
		
		panel.SetSizer(hbox)
		
	def initEvents(self):
		"""
		Set all of the various events in the file removable window.
		"""
		
		self.Bind(wx.EVT_BUTTON, self.onOK, id=ID_REMOVE_OK)
		self.Bind(wx.EVT_BUTTON, self.onCancel, id=ID_REMOVE_CANCEL)
		
	def loadFiles(self):
		"""
		Setup the checkable list and populate it with what is currently loaded.
		"""
		
		# Add the filename column
		self.listControl.InsertColumn(0, 'Filename', width=550)
		
		# Fill!
		for i,filename in enumerate(self.parent.filenames):
			index = self.listControl.InsertStringItem(i, filename)
	
	def onOK(self, event):
		"""
		Process the checklist and remove as necessary.
		"""
		
		# Build a list of filenames to remove
		toRemove = []
		for i in xrange(self.listControl.GetItemCount()):
			if self.listControl.IsChecked(i):
				toRemove.append( self.parent.filenames[i] )
		
		# Remove them
		for filename in toRemove:
			self.parent.filenames.remove(filename)
		
		# Reload the SDFs and update the plot if needed
		if len(toRemove) > 0:
			self.parent.data.loadFiles()
			self.parent.data.draw()
		self.Close()
	
	def onCancel(self, event):
		"""
		Quit without deleting any files.
		"""
		
		self.Close()


def main(args):
	app = wx.App(0)
	frame = MainWindow(None, -1)
	if len(args) > 0:
		frame.filenames = args
		
		frame.data = Visualization_GUI(frame, observer=observer)
		frame.data.loadFiles()
		frame.data.draw()
		
	app.MainLoop()


if __name__ == "__main__":
	main(sys.argv[1:])
	
