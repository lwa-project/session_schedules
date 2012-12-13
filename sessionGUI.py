#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import sys
import copy
import math
import ephem
import getopt
try:
	import cStringIO as StringIO
except ImportError:
	import StringIO
from datetime import datetime

import conflict

from lsl.common.dp import fS
from lsl.common.stations import lwa1
from lsl.astro import deg_to_dms, deg_to_hms, MJD_OFFSET, DJD_OFFSET
from lsl.reader.tbn import filterCodes as TBNFilters
from lsl.reader.drx import filterCodes as DRXFilters
from lsl.common import sdf

import wx
import wx.html as html
from wx.lib.scrolledpanel import ScrolledPanel
from wx.lib.mixins.listctrl import TextEditMixin, CheckListCtrlMixin

import matplotlib
matplotlib.use('WXAgg')
matplotlib.interactive(True)

from matplotlib.backends.backend_wxagg import NavigationToolbar2WxAgg, FigureCanvasWxAgg
from matplotlib.figure import Figure
from matplotlib.ticker import NullFormatter, NullLocator


__version__ = "0.5"
__revision__ = "$Rev$"
__author__ = "Jayce Dowell"


ALLOW_TBW_TBN_SAME_SDF = True


def usage(exitCode=None):
	print """sessionGUI.py - GUI for making all sorts of session definition files (SDFs) for
LWA1.

Usage: sessionGUI.py [OPTIONS] [input_SDF_file]

Options:
-h, --help          Display this help information
-d, --drsu-size     Perform storage calcuations assuming the specified DRSU 
                    size in TB
"""

	if exitCode is not None:
		sys.exit(exitCode)
	else:
		return True


def parseOptions(args):
	config = {}
	config['drsuSize'] = sdf._DRSUCapacityTB

	# Read in and process the command line flags
	try:
		opts, args = getopt.getopt(args, "hd:", ["help", "drsu-size="])
	except getopt.GetoptError, err:
		# Print help information and exit:
		print str(err) # will print something like "option -a not recognized"
		usage(exitCode=2)
	
	# Work through opts
	for opt, value in opts:
		if opt in ('-h', '--help'):
			usage(exitCode=0)
		elif opt in ('-d', '--drsu-size'):
			config['drsuSize'] = int(value)
		else:
			assert False
	
	# Make sure we have a sane DRSU size
	try:
		assert(config['drsuSize'] > 0)
	except AssertionError:
		raise RuntimeError("Invalid DRSU size of %i TB (%i B)" % (config['drsuSize'], config['drsuSize']*1024**4))
	
	# Add in arguments
	config['args'] = args

	# Return configuration
	return config


class ObservationListCtrl(wx.ListCtrl, TextEditMixin, CheckListCtrlMixin):
	"""
	Class that combines an editable list with check boxes.
	"""
	
	def __init__(self, parent, **kwargs):
		wx.ListCtrl.__init__(self, parent, style=wx.LC_REPORT, **kwargs)
		TextEditMixin.__init__(self)
		CheckListCtrlMixin.__init__(self)
		
		self.nSelected = 0
		self.parent = parent
		
	def setCheckDependant(self, index=None):
		"""
		Update various menu entried and toolbar actions depending on what is selected.
		"""
		
		if self.nSelected == 0:
			# Edit menu - disabled
			try:
				self.parent.editmenu['cut'].Enable(False)
				self.parent.editmenu['copy'].Enable(False)
			except KeyError, AttributeError:
				pass
				
			# Stepped observation edits - disabled
			try:
				self.parent.obsmenu['steppedEdit'].Enable(False)
				self.parent.toolbar.EnableTool(ID_EDIT_STEPPED, False)
			except KeyError, AttributeError:
				pass
				
		elif self.nSelected == 1:
			# Edit menu - enabled
			try:
				self.parent.editmenu['cut'].Enable(True)
				self.parent.editmenu['copy'].Enable(True)
			except KeyError, AttributeError:
				pass
			
			# Stepped observation edits - enbled if there is an index and it is STEPPED, 
			# disabled otherwise
			if index is not None:
				if self.parent.project.sessions[0].observations[index].mode == 'STEPPED':
					try:
						self.parent.obsmenu['steppedEdit'].Enable(True)
						self.parent.toolbar.EnableTool(ID_EDIT_STEPPED, True)
					except KeyError, AttributeError:
						pass
				else:
					try:
						self.parent.obsmenu['steppedEdit'].Enable(False)
						self.parent.toolbar.EnableTool(ID_EDIT_STEPPED, False)
					except KeyError, AttributeError:
						pass
			else:
				# Stepped observation edits - disabled
				try:
					self.parent.obsmenu['steppedEdit'].Enable(False)
					self.parent.toolbar.EnableTool(ID_EDIT_STEPPED, False)
				except KeyError, AttributeError:
					pass
					
		else:
			# Edit menu - enabled
			try:
				self.parent.editmenu['cut'].Enable(True)
				self.parent.editmenu['copy'].Enable(True)
			except KeyError, AttributeError:
				pass
				
			# Stepped observation edits - disabled
			try:
				self.parent.obsmenu['steppedEdit'].Enable(False)
				self.parent.toolbar.EnableTool(ID_EDIT_STEPPED, False)
			except KeyError, AttributeError:
				pass
		
	def OnCheckItem(self, index, flag):
		"""
		Overwrite the default OnCheckItem function so that we can control the enabling
		and disabling of the STEPPED step editor button/menu item.
		"""
		
		if flag:
			self.nSelected += 1
		else:
			self.nSelected -= 1
		
		self.setCheckDependant(index=index)
		CheckListCtrlMixin.OnCheckItem(self, index, flag)
		
	def OpenEditor(self, col, row):
		"""
		Overwrite the default OpenEditor function so that select columns
		are not actually editable.
		"""
		
		if col in [0,]:
			pass
		elif self.parent.project.sessions[0].observations[row].mode == 'TBW' and col in [5, 6, 7]:
			pass
		elif self.parent.project.sessions[0].observations[row].mode in ['TRK_SOL', 'TRK_JOV'] and col in [6, 7]:
			pass
		elif self.parent.project.sessions[0].observations[row].mode == 'STEPPED' and col in [5, 6, 7, 8, 9, 11]:
			pass
		else:
			TextEditMixin.OpenEditor(self, col, row)


class SteppedListCtrl(wx.ListCtrl, TextEditMixin, CheckListCtrlMixin):
	"""
	Class that combines an editable list with check boxes.
	"""
	
	def __init__(self, parent, **kwargs):
		wx.ListCtrl.__init__(self, parent, style=wx.LC_REPORT, **kwargs)
		TextEditMixin.__init__(self)
		CheckListCtrlMixin.__init__(self)
		
		self.nSelected = 0
		self.parent = parent
		
	def setCheckDependant(self, index=None):
		"""
		Update various menu entried and toolbar actions depending on what is selected.
		"""
		
		if self.nSelected == 0:
			# Edit menu - disabled
			try:
				self.parent.editmenu['cut'].Enable(False)
				self.parent.editmenu['copy'].Enable(False)
			except KeyError, AttributeError:
				pass
			
		elif self.nSelected == 1:
			# Edit menu - enabled
			try:
				self.parent.editmenu['cut'].Enable(True)
				self.parent.editmenu['copy'].Enable(True)
			except KeyError, AttributeError:
				pass
				
		else:
			# Edit menu - enabled
			try:
				self.parent.editmenu['cut'].Enable(True)
				self.parent.editmenu['copy'].Enable(True)
			except KeyError, AttributeError:
				pass
			
	def OnCheckItem(self, index, flag):
		"""
		Overwrite the default OnCheckItem function so that we can control the enabling
		and disabling of the STEPPED step editor button/menu item.
		"""
		
		if flag:
			self.nSelected += 1
		else:
			self.nSelected -= 1
		
		self.setCheckDependant(index=index)
		CheckListCtrlMixin.OnCheckItem(self, index, flag)
		
	def OpenEditor(self, col, row):
		"""
		Overwrite the default OpenEditor class so that select columns
		are not actually editable.
		"""
		
		if col in [0,]:
			pass
		else:
			TextEditMixin.OpenEditor(self, col, row)


class PlotPanel(wx.Panel):
	"""
	The PlotPanel has a Figure and a Canvas. OnSize events simply set a 
	flag, and the actual resizing of the figure is triggered by an Idle event.
	
	From: http://www.scipy.org/Matplotlib_figure_in_a_wx_panel
	"""
	
	def __init__(self, parent, color=None, dpi=None, **kwargs):
		from matplotlib.backends.backend_wxagg import FigureCanvasWxAgg
		from matplotlib.figure import Figure

		# initialize Panel
		if 'id' not in kwargs.keys():
			kwargs['id'] = wx.ID_ANY
		if 'style' not in kwargs.keys():
			kwargs['style'] = wx.NO_FULL_REPAINT_ON_RESIZE
		wx.Panel.__init__(self, parent, **kwargs)

		# initialize matplotlib stuff
		self.figure = Figure(None, dpi)
		self.canvas = FigureCanvasWxAgg(self, -1, self.figure)
		self.SetColor(color)

		self._SetSize()
		self.draw()

		self._resizeflag = False

		self.Bind(wx.EVT_IDLE, self._onIdle)
		self.Bind(wx.EVT_SIZE, self._onSize)

	def SetColor( self, rgbtuple=None ):
		"""
		Set figure and canvas colours to be the same.
		"""
		
		if rgbtuple is None:
			rgbtuple = wx.SystemSettings.GetColour( wx.SYS_COLOUR_BTNFACE ).Get()
		clr = [c/255. for c in rgbtuple]
		self.figure.set_facecolor(clr)
		self.figure.set_edgecolor(clr)
		self.canvas.SetBackgroundColour(wx.Colour(*rgbtuple))

	def _onSize(self, event):
		self._resizeflag = True

	def _onIdle(self, evt):
		if self._resizeflag:
			self._resizeflag = False
			self._SetSize()

	def _SetSize(self):
		pixels = tuple(self.parent.GetClientSize())
		self.SetSize(pixels)
		self.canvas.SetSize(pixels)
		self.figure.set_size_inches(float( pixels[0] )/self.figure.get_dpi(), float( pixels[1] )/self.figure.get_dpi())

	def draw(self):
		pass # abstract, to be overridden by child classes


ID_NEW = 11
ID_OPEN = 12
ID_SAVE = 13
ID_SAVE_AS = 14
ID_QUIT = 15

ID_INFO = 21
ID_SCHEDULE = 22
ID_ADD_TBW = 23
ID_ADD_TBN = 24
ID_ADD_DRX_RADEC = 25
ID_ADD_DRX_SOLAR = 26
ID_ADD_DRX_JOVIAN = 27
ID_ADD_STEPPED_RADEC = 28
ID_ADD_STEPPED_AZALT = 29
ID_EDIT_STEPPED = 30
ID_REMOVE = 31
ID_VALIDATE = 40
ID_TIMESERIES = 41
ID_RESOLVE = 42
ID_ADVANCED = 43

ID_DATA_VOLUME = 51

ID_HELP = 61
ID_FILTER_INFO = 62
ID_ABOUT = 63

ID_LISTCTRL = 71

ID_CUT = 81
ID_COPY = 82
ID_PASTE_BEFORE = 83
ID_PASTE_AFTER = 84
ID_PASTE_END = 85

class SDFCreator(wx.Frame):
	def __init__(self, parent, title, config={}):
		wx.Frame.__init__(self, parent, title=title, size=(750,500))
		
		self.scriptPath = os.path.abspath(__file__)
		self.scriptPath = os.path.split(self.scriptPath)[0]
		
		self.dirname = ''
		self.toolbar = None
		self.statusbar = None
		self.savemenu = None
		self.editmenu = {}
		self.obsmenu = {}
		
		self.buffer = None
		
		self.initSDF()
		
		self.initUI()
		self.initEvents()
		self.Show()
		
		sdf._DRSUCapacityTB = config['drsuSize']
		
		if len(config['args']) > 0:
			self.filename = config['args'][0]
			self.parseFile(self.filename)
			if self.mode == 'TBW' and not ALLOW_TBW_TBN_SAME_SDF:
				self.finfo.Enable(False)
			else:
				self.finfo.Enable(True)
		else:
			self.filename = ''
			self.setMenuButtons('None')
			
		self.edited = False
		self.setSaveButton()
		
	def initSDF(self):
		"""
		Create an empty sdf.project instance to store all of the actual
		observations.
		"""
		
		po = sdf.ProjectOffice()
		observer = sdf.Observer('', 0, first='', last='')
		project = sdf.Project(observer, '', '', projectOffice=po)
		session = sdf.Session('session_name', 0, observations=[])
		project.sessions = [session,]
		
		self.project = project
		self.mode = ''
		
		self.project.sessions[0].tbwBits = 12
		self.project.sessions[0].tbwSamples = 12000000
		self.project.sessions[0].tbnGain = -1
		self.project.sessions[0].drxGain = -1
		
	def initUI(self):
		"""
		Start the user interface.
		"""
		
		menubar = wx.MenuBar()
		
		fileMenu = wx.Menu()
		editMenu = wx.Menu()
		obsMenu = wx.Menu()
		dataMenu = wx.Menu()
		helpMenu = wx.Menu()
		
		# File menu items
		new = wx.MenuItem(fileMenu, ID_NEW, '&New')
		fileMenu.AppendItem(new)
		open = wx.MenuItem(fileMenu, ID_OPEN, '&Open')
		fileMenu.AppendItem(open)
		save = wx.MenuItem(fileMenu, ID_SAVE, '&Save')
		fileMenu.AppendItem(save)
		saveas = wx.MenuItem(fileMenu, ID_SAVE_AS, 'S&ave As')
		fileMenu.AppendItem(saveas)
		fileMenu.AppendSeparator()
		quit = wx.MenuItem(fileMenu, ID_QUIT, '&Quit')
		fileMenu.AppendItem(quit)
		
		# Save the 'save' menu item
		self.savemenu = save
		
		# Edit menu items
		cut = wx.MenuItem(editMenu, ID_CUT, 'C&ut Selected Observation')
		editMenu.AppendItem(cut)
		cpy = wx.MenuItem(editMenu, ID_COPY, '&Copy Selected Observation')
		editMenu.AppendItem(cpy)
		pstb = wx.MenuItem(editMenu, ID_PASTE_BEFORE, '&Paste Before Selected')
		editMenu.AppendItem(pstb)
		psta = wx.MenuItem(editMenu, ID_PASTE_AFTER, '&Paste After Selected')
		editMenu.AppendItem(psta)
		pste = wx.MenuItem(editMenu, ID_PASTE_END, '&Paste at End of List')
		editMenu.AppendItem(pste)
		
		# Save menu items and disable all of them
		self.editmenu['cut'] = cut
		self.editmenu['copy'] = cpy
		self.editmenu['pasteBefore'] = pstb
		self.editmenu['pasteAfter'] = psta
		self.editmenu['pasteEnd'] = pste
		for k in self.editmenu.keys():
			self.editmenu[k].Enable(False)
		
		# Observer menu items
		info = wx.MenuItem(obsMenu, ID_INFO, 'Observer/&Project Info.')
		obsMenu.AppendItem(info)
		sch = wx.MenuItem(obsMenu, ID_SCHEDULE, 'Sc&heduling')
		obsMenu.AppendItem(sch)
		obsMenu.AppendSeparator()
		add = wx.Menu()
		addTBW = wx.MenuItem(add, ID_ADD_TBW, 'TB&W')
		add.AppendItem(addTBW)
		addTBN = wx.MenuItem(add, ID_ADD_TBN, 'TB&N')
		add.AppendItem(addTBN)
		add.AppendSeparator()
		addDRXR = wx.MenuItem(add, ID_ADD_DRX_RADEC, 'DRX - &RA/Dec')
		add.AppendItem(addDRXR)
		addDRXS = wx.MenuItem(add, ID_ADD_DRX_SOLAR, 'DRX - &Solar')
		add.AppendItem(addDRXS)
		addDRXJ = wx.MenuItem(add, ID_ADD_DRX_JOVIAN, 'DRX - &Jovian')
		add.AppendItem(addDRXJ)
		addSteppedRADec = wx.MenuItem(add, ID_ADD_STEPPED_RADEC, 'DRX - Ste&pped - RA/Dec')
		add.AppendItem(addSteppedRADec)
		addSteppedAzAlt = wx.MenuItem(add, ID_ADD_STEPPED_AZALT, 'DRX - Ste&pped - Az/Alt')
		add.AppendItem(addSteppedAzAlt)
		editStepped = wx.MenuItem(add, ID_EDIT_STEPPED, 'DRX - Edit Selected Stepped Obs.')
		add.AppendItem(editStepped)
		obsMenu.AppendMenu(-1, '&Add', add)
		remove = wx.MenuItem(obsMenu, ID_REMOVE, '&Remove Selected')
		obsMenu.AppendItem(remove)
		validate = wx.MenuItem(obsMenu, ID_VALIDATE, '&Validate All\tF5')
		obsMenu.AppendItem(validate)
		obsMenu.AppendSeparator()
		resolve = wx.MenuItem(obsMenu, ID_RESOLVE, 'Resolve Selected\tF3')
		obsMenu.AppendItem(resolve)
		timeseries = wx.MenuItem(obsMenu, ID_TIMESERIES, 'Session at a &Glance')
		obsMenu.AppendItem(timeseries)
		advanced = wx.MenuItem(obsMenu, ID_ADVANCED, 'Advanced &Settings')
		obsMenu.AppendItem(advanced)
		
		# Save menu items
		self.obsmenu['tbn'] = addTBN
		self.obsmenu['tbw'] = addTBW
		self.obsmenu['drx-radec'] = addDRXR
		self.obsmenu['drx-solar'] = addDRXS
		self.obsmenu['drx-jovian'] = addDRXJ
		self.obsmenu['steppedRADec'] = addSteppedRADec
		self.obsmenu['steppedAzAlt'] = addSteppedAzAlt
		self.obsmenu['steppedEdit'] = editStepped
		
		# Data menu items
		volume = wx.MenuItem(obsMenu, ID_DATA_VOLUME, '&Estimated Data Volume')
		dataMenu.AppendItem(volume)
		
		# Help menu items
		help = wx.MenuItem(helpMenu, ID_HELP, 'Session GUI Handbook\tF1')
		helpMenu.AppendItem(help)
		self.finfo = wx.MenuItem(helpMenu, ID_FILTER_INFO, '&Filter Codes')
		helpMenu.AppendItem(self.finfo)
		helpMenu.AppendSeparator()
		about = wx.MenuItem(helpMenu, ID_ABOUT, '&About')
		helpMenu.AppendItem(about)
		
		menubar.Append(fileMenu, '&File')
		menubar.Append(editMenu, '&Edit')
		menubar.Append(obsMenu,  '&Observations')
		menubar.Append(dataMenu, '&Data')
		menubar.Append(helpMenu, '&Help')
		self.SetMenuBar(menubar)
		
		# Toolbar
		self.toolbar = self.CreateToolBar()
		self.toolbar.AddLabelTool(ID_NEW, '', wx.Bitmap(os.path.join(self.scriptPath, 'icons', 'new.png')), shortHelp='New', 
								longHelp='Clear the existing setup and start a new project/session')
		self.toolbar.AddLabelTool(ID_OPEN, '', wx.Bitmap(os.path.join(self.scriptPath, 'icons', 'open.png')), shortHelp='Open', 
								longHelp='Open and load an existing SD file')
		self.toolbar.AddLabelTool(ID_SAVE, '', wx.Bitmap(os.path.join(self.scriptPath, 'icons', 'save.png')), shortHelp='Save', 
								longHelp='Save the current setup')
		self.toolbar.AddLabelTool(ID_SAVE_AS, '', wx.Bitmap(os.path.join(self.scriptPath, 'icons', 'save-as.png')), shortHelp='Save as', 
								longHelp='Save the current setup to a new SD file')
		self.toolbar.AddLabelTool(ID_QUIT, '', wx.Bitmap(os.path.join(self.scriptPath, 'icons', 'exit.png')), shortHelp='Quit', 
								longHelp='Quit (without saving)')
		self.toolbar.AddSeparator()
		self.toolbar.AddLabelTool(ID_ADD_TBW, 'tbw', wx.Bitmap(os.path.join(self.scriptPath, 'icons', 'tbw.png')), shortHelp='Add TBW', 
								longHelp='Add a new all-sky TBW observation to the list')
		self.toolbar.AddLabelTool(ID_ADD_TBN, 'tbn', wx.Bitmap(os.path.join(self.scriptPath, 'icons', 'tbn.png')), shortHelp='Add TBN', 
								longHelp='Add a new all-sky TBN observation to the list')
		self.toolbar.AddLabelTool(ID_ADD_DRX_RADEC,  'drx-radec',  wx.Bitmap(os.path.join(self.scriptPath, 'icons', 'drx-radec.png')),  shortHelp='Add DRX - RA/Dec', 
								longHelp='Add a new beam forming DRX observation that tracks the sky (ra/dec)')
		self.toolbar.AddLabelTool(ID_ADD_DRX_SOLAR,  'drx-solar',  wx.Bitmap(os.path.join(self.scriptPath, 'icons', 'drx-solar.png')),  shortHelp='Add DRX - Solar', 
								longHelp='Add a new beam forming DRX observation that tracks the Sun')
		self.toolbar.AddLabelTool(ID_ADD_DRX_JOVIAN, 'drx-jovian', wx.Bitmap(os.path.join(self.scriptPath, 'icons', 'drx-jovian.png')), shortHelp='Add DRX - Jovian', 
								longHelp='Add a new beam forming DRX observation that tracks Jupiter')
		self.toolbar.AddLabelTool(ID_ADD_STEPPED_RADEC,  'stepped', wx.Bitmap(os.path.join(self.scriptPath, 'icons', 'stepped-radec.png')), shortHelp='Add DRX - Stepped - RA/Dec', 
								longHelp='Add a new beam forming DRX observation with custom RA/Dec position and frequency stepping')
		self.toolbar.AddLabelTool(ID_ADD_STEPPED_AZALT,  'stepped', wx.Bitmap(os.path.join(self.scriptPath, 'icons', 'stepped-azalt.png')), shortHelp='Add DRX - Stepped - Az/Alt', 
								longHelp='Add a new beam forming DRX observation with custom az/alt position and frequency stepping')
		self.toolbar.AddLabelTool(ID_EDIT_STEPPED,  'step', wx.Bitmap(os.path.join(self.scriptPath, 'icons', 'stepped-edit.png')), shortHelp='Edit Selected Stepped Observation', 
								longHelp='Add and edit steps for the currently selected stepped observation')
		self.toolbar.AddLabelTool(ID_REMOVE, '', wx.Bitmap(os.path.join(self.scriptPath, 'icons', 'remove.png')), shortHelp='Remove Selected', 
								longHelp='Remove the selected observations from the list')
		self.toolbar.AddLabelTool(ID_VALIDATE, '', wx.Bitmap(os.path.join(self.scriptPath, 'icons', 'validate.png')), shortHelp='Validate Observations', 
								longHelp='Validate the current set of parameters and observations')
		self.toolbar.AddSeparator()
		self.toolbar.AddLabelTool(ID_HELP, '', wx.Bitmap(os.path.join(self.scriptPath, 'icons', 'help.png')), shortHelp='Help', 
								longHelp='Display a brief help message for this program')
		self.toolbar.Realize()
		
		# Status bar
		self.statusbar = self.CreateStatusBar()
		
		# Observation list
		hbox = wx.BoxSizer(wx.HORIZONTAL)
		self.panel = ScrolledPanel(self, -1)
		
		self.listControl = ObservationListCtrl(self.panel, id=ID_LISTCTRL)
		self.listControl.parent = self
		
		hbox.Add(self.listControl, 1, wx.EXPAND)
		self.panel.SetSizer(hbox)
	
	def initEvents(self):
		"""
		Set all of the various events in the main window.
		"""
		
		# File menu events
		self.Bind(wx.EVT_MENU, self.onNew, id=ID_NEW)
		self.Bind(wx.EVT_MENU, self.onLoad, id=ID_OPEN)
		self.Bind(wx.EVT_MENU, self.onSave, id=ID_SAVE)
		self.Bind(wx.EVT_MENU, self.onSaveAs, id=ID_SAVE_AS)
		self.Bind(wx.EVT_MENU, self.onQuit, id=ID_QUIT)
		
		# Edit menu events
		self.Bind(wx.EVT_MENU, self.onCut, id=ID_CUT)
		self.Bind(wx.EVT_MENU, self.onCopy, id=ID_COPY)
		self.Bind(wx.EVT_MENU, self.onPasteBefore, id=ID_PASTE_BEFORE)
		self.Bind(wx.EVT_MENU, self.onPasteAfter, id=ID_PASTE_AFTER)
		self.Bind(wx.EVT_MENU, self.onPasteEnd, id=ID_PASTE_END)
		
		# Observer menu events
		self.Bind(wx.EVT_MENU, self.onInfo, id=ID_INFO)
		self.Bind(wx.EVT_MENU, self.onSchedule, id=ID_SCHEDULE)
		self.Bind(wx.EVT_MENU, self.onAddTBW, id=ID_ADD_TBW)
		self.Bind(wx.EVT_MENU, self.onAddTBN, id=ID_ADD_TBN)
		self.Bind(wx.EVT_MENU, self.onAddDRXR, id=ID_ADD_DRX_RADEC)
		self.Bind(wx.EVT_MENU, self.onAddDRXS, id=ID_ADD_DRX_SOLAR)
		self.Bind(wx.EVT_MENU, self.onAddDRXJ, id=ID_ADD_DRX_JOVIAN)
		self.Bind(wx.EVT_MENU, self.onAddSteppedRADec, id=ID_ADD_STEPPED_RADEC)
		self.Bind(wx.EVT_MENU, self.onAddSteppedAzAlt, id=ID_ADD_STEPPED_AZALT)
		self.Bind(wx.EVT_MENU, self.onEditStepped, id=ID_EDIT_STEPPED)
		self.Bind(wx.EVT_MENU, self.onRemove, id=ID_REMOVE)
		self.Bind(wx.EVT_MENU, self.onValidate, id=ID_VALIDATE)
		self.Bind(wx.EVT_MENU, self.onResolve, id=ID_RESOLVE)
		self.Bind(wx.EVT_MENU, self.onTimeseries, id=ID_TIMESERIES)
		self.Bind(wx.EVT_MENU, self.onAdvanced, id=ID_ADVANCED)
		
		# Data menu events
		self.Bind(wx.EVT_MENU, self.onVolume, id=ID_DATA_VOLUME)
		
		# Help menu events
		self.Bind(wx.EVT_MENU, self.onHelp, id=ID_HELP)
		self.Bind(wx.EVT_MENU, self.onFilterInfo, id=ID_FILTER_INFO)
		self.Bind(wx.EVT_MENU, self.onAbout, id=ID_ABOUT)
		
		# Observation edits
		self.Bind(wx.EVT_LIST_END_LABEL_EDIT, self.onEdit, id=ID_LISTCTRL)

		# Window manager close
		self.Bind(wx.EVT_CLOSE, self.onQuit)
	
	def onNew(self, event):
		"""
		Create a new SD session.
		"""
		
		if self.edited:
			dialog = wx.MessageDialog(self, 'The current session defintion file has changes that have not been saved.\n\nStart a new session anyways?', 'Confirm New', style=wx.YES_NO|wx.NO_DEFAULT|wx.ICON_QUESTION)
			
			if dialog.ShowModal() == wx.ID_YES:
				pass
			else:
				return False
		
		self.filename = ''
		self.edited = True
		self.badEdit = False
		self.setSaveButton()
		
		self.setMenuButtons('None')
		self.listControl.DeleteAllItems()
		self.listControl.DeleteAllColumns()
		self.initSDF()
		ObserverInfo(self)

		if self.mode == 'TBW' and not ALLOW_TBW_TBN_SAME_SDF:
			self.finfo.Enable(False)
		else:
			self.finfo.Enable(True)
	
	def onLoad(self, event):
		"""
		Load an existing SD file.
		"""
		
		if self.edited:
			dialog = wx.MessageDialog(self, 'The current session defintion file has changes that have not been saved.\n\nOpen a new file anyways?', 'Confirm Open', style=wx.YES_NO|wx.NO_DEFAULT|wx.ICON_QUESTION)
			
			if dialog.ShowModal() == wx.ID_YES:
				pass
			else:
				return False
		
		dialog = wx.FileDialog(self, "Select a SD File", self.dirname, '', 'Text Files (*.txt)|*.txt|All Files (*.*)|*.*', wx.OPEN)
		
		if dialog.ShowModal() == wx.ID_OK:
			self.dirname = dialog.GetDirectory()
			self.filename = dialog.GetPath()
			self.parseFile(dialog.GetPath())
			
			self.edited = False
			self.setSaveButton()
			
		dialog.Destroy()

		if self.mode == 'TBW':
			self.finfo.Enable(False)
		else:
			self.finfo.Enable(True)

	def onSave(self, event):
		"""
		Save the current observation to a file.
		"""
		
		if self.filename == '':
			self.onSaveAs(event)
		else:
			
			if not self.onValidate(1, confirmValid=False):
				self.displayError('The session definition file could not be saved due to errors in the file.', title='Save Failed')
			else:
				try:
					fh = open(self.filename, 'w')
					fh.write(self.project.render())
					fh.close()
					
					self.edited = False
					self.setSaveButton()
				except IOError as err:
					self.displayError('Error saving to %s' % self.filename, details=err, title='Save Error')

	def onSaveAs(self, event):
		"""
		Save the current observation to a new SD file.
		"""
		
		if not self.onValidate(1, confirmValid=False):
			self.displayError('The session definition file could not be saved due to errors in the file.', title='Save Failed')
		else:
			dialog = wx.FileDialog(self, "Select Output File", self.dirname, '', 'Text Files (*.txt)|*.txt|All Files (*.*)|*.*', wx.SAVE|wx.FD_OVERWRITE_PROMPT)
			
			if dialog.ShowModal() == wx.ID_OK:
				self.dirname = dialog.GetDirectory()
				
				self.filename = dialog.GetPath()
				try:
					fh = open(self.filename, 'w')
					fh.write(self.project.render())
					fh.close()
				
					self.edited = False
					self.setSaveButton()
				except IOError as err:
					self.displayError('Error saving to %s' % self.filename, details=err, title='Save Error')
				
			dialog.Destroy()
			
	def onCopy(self, event):
		"""
		Copy the selected observation(s) to the buffer.
		"""
		
		self.buffer = []
		for i in xrange(self.listControl.GetItemCount()):
			if self.listControl.IsChecked(i):
				self.buffer.append( copy.deepcopy(self.project.sessions[0].observations[i]) )
				
		self.editmenu['pasteBefore'].Enable(True)
		self.editmenu['pasteAfter'].Enable(True)
		self.editmenu['pasteEnd'].Enable(True)
	
	def onCut(self, event):
		self.onCopy(event)
		self.onRemove(event)
		
	def onPasteBefore(self, event):
		firstChecked = None
		
		for i in xrange(self.listControl.GetItemCount()):
			if self.listControl.IsChecked(i):
				firstChecked = i
				break
				
		if firstChecked is not None:
			id = firstChecked
			
			for obs in self.buffer[::-1]:
				cObs = copy.deepcopy(obs)
				
				self.project.sessions[0].observations.insert(id, cObs)
				self.addObservation(self.project.sessions[0].observations[id], id)
				
			self.edited = True
			self.setSaveButton()
			
		# Re-number the remaining rows to keep the display clean
		for i in xrange(self.listControl.GetItemCount()):
			item = self.listControl.GetItem(i, 0)
			item.SetText('%i' % (i+1))
			self.listControl.SetItem(item)
			self.listControl.RefreshItem(item.GetId())
	
	def onPasteAfter(self, event):
		lastChecked = None
		
		for i in xrange(self.listControl.GetItemCount()):
			if self.listControl.IsChecked(i):
				lastChecked = i
				
		if lastChecked is not None:
			id = lastChecked + 1
			
			for obs in self.buffer[::-1]:
				cObs = copy.deepcopy(obs)
				
				self.project.sessions[0].observations.insert(id, cObs)
				self.addObservation(self.project.sessions[0].observations[id], id)
				
			self.edited = True
			self.setSaveButton()
			
		# Re-number the remaining rows to keep the display clean
		for i in xrange(self.listControl.GetItemCount()):
			item = self.listControl.GetItem(i, 0)
			item.SetText('%i' % (i+1))
			self.listControl.SetItem(item)
			self.listControl.RefreshItem(item.GetId())
	
	def onPasteEnd(self, event):
		"""
		Paste the selected observation(s) at the end of the current session.
		"""
		
		if self.buffer is not None:
			for obs in self.buffer:
				id = self.listControl.GetItemCount() + 1
				
				cObs = copy.deepcopy(obs)
				
				self.project.sessions[0].observations.append(cObs)
				self.addObservation(self.project.sessions[0].observations[-1], id)
				
			self.edited = True
			self.setSaveButton()
	
	def onInfo(self, event):
		"""
		Open up the observer/project information window.
		"""
		
		ObserverInfo(self)
		
	def onSchedule(self, event):
		"""
		Open up a dialog to set the scheduling.
		"""
		
		ScheduleWindow(self)
		
	def onAddTBW(self, event):
		"""
		Add a TBW observation to the list and update the main window.
		"""
		
		id = self.listControl.GetItemCount() + 1
		bits = self.project.sessions[0].tbwBits
		samples = self.project.sessions[0].tbwSamples
		self.project.sessions[0].observations.append( sdf.TBW('tbw-%i' % id, 'All-Sky', 'UTC %i 01 01 00:00:00.000' % datetime.now().year, samples, bits=bits) )
		self.addObservation(self.project.sessions[0].observations[-1], id)
		
		self.edited = True
		self.setSaveButton()
		
	def onAddTBN(self, event):
		"""
		Add a TBW observation to the list and update the main window.
		"""
		
		id = self.listControl.GetItemCount() + 1
		gain = self.project.sessions[0].tbnGain
		self.project.sessions[0].observations.append( sdf.TBN('tbn-%i' % id, 'All-Sky', 'UTC %i 01 01 00:00:00.000' % datetime.now().year, '00:00:00.000', 38e6, 7) )
		self.project.sessions[0].observations[-1].gain = gain
		self.addObservation(self.project.sessions[0].observations[-1], id)
		
		self.edited = True
		self.setSaveButton()
		
	def onAddDRXR(self, event):
		"""
		Add a tracking RA/Dec (DRX) observation to the list and update the main window.
		"""
		
		id = self.listControl.GetItemCount() + 1
		gain = self.project.sessions[0].drxGain
		self.project.sessions[0].observations.append( sdf.DRX('drx-%i' % id, 'target-%i' % id, 'UTC %i 01 01 00:00:00.000' % datetime.now().year, '00:00:00.000', 0.0, 0.0, 38e6, 74e6, 7) )
		self.project.sessions[0].observations[-1].gain = gain
		self.addObservation(self.project.sessions[0].observations[-1], id)
		
		self.edited = True
		self.setSaveButton()
		
	def onAddDRXS(self, event):
		"""
		Add a tracking Sun (DRX) observation to the list and update the main window.
		"""
		
		id = self.listControl.GetItemCount() + 1
		gain = self.project.sessions[0].drxGain
		self.project.sessions[0].observations.append( sdf.Solar('solar-%i' % id, 'target-%i' % id, 'UTC %i 01 01 00:00:00.000' % datetime.now().year, '00:00:00.000', 38e6, 74e6, 7) )
		self.project.sessions[0].observations[-1].gain = gain
		self.addObservation(self.project.sessions[0].observations[-1], id)
		
		self.edited = True
		self.setSaveButton()
		
	def onAddDRXJ(self, event):
		"""
		Add a tracking Jupiter (DRX) observation to the list and update the main window.
		"""
		
		id = self.listControl.GetItemCount() + 1
		gain = self.project.sessions[0].drxGain
		self.project.sessions[0].observations.append( sdf.Jovian('jovian-%i' % id, 'target-%i' % id, 'UTC %i 01 01 00:00:00.000' % datetime.now().year, '00:00:00.000', 38e6, 74e6, 7) )
		self.project.sessions[0].observations[-1].gain = gain
		self.addObservation(self.project.sessions[0].observations[-1], id)
		
		self.edited = True
		self.setSaveButton()
	
	def onAddSteppedRADec(self, event):
		"""
		Add a RA/Dec stepped observation block.
		"""
		
		id = self.listControl.GetItemCount() + 1
		gain = self.project.sessions[0].drxGain
		self.project.sessions[0].observations.append( sdf.Stepped('stps-%i' % id, 'radec-%i' % id, 'UTC %i 01 01 00:00:00.000' % datetime.now().year, 7, RADec=True) )
		self.project.sessions[0].observations[-1].gain = gain
		self.addObservation(self.project.sessions[0].observations[-1], id)
		
		self.edited = True
		self.setSaveButton()
		
	def onAddSteppedAzAlt(self, event):
		"""
		Add a Az/Alt stepped observation block.
		"""
		
		id = self.listControl.GetItemCount() + 1
		gain = self.project.sessions[0].drxGain
		self.project.sessions[0].observations.append( sdf.Stepped('stps-%i' % id, 'azalt-%i' % id, 'UTC %i 01 01 00:00:00.000' % datetime.now().year, 7, RADec=False) )
		self.project.sessions[0].observations[-1].gain = gain
		self.addObservation(self.project.sessions[0].observations[-1], id)
		
		self.edited = True
		self.setSaveButton()
		
	def onEditStepped(self, event):
		"""
		Add or edit steps to the currently selected stepped observtion.
		"""
		
		nChecked = 0
		whichChecked = None
		for i in xrange(self.listControl.GetItemCount()):
			if self.listControl.IsChecked(i):
				whichChecked = i
				nChecked += 1
				
		if nChecked != 1:
			return False
		if self.project.sessions[0].observations[i].mode != 'STEPPED':
			return False
			
		SteppedWindow(self, whichChecked)
	
	def onEdit(self, event):
		"""
		Make the selected change to the underlying observation.
		"""
		
		obsIndex = event.GetIndex()
		obsAttr = event.GetColumn()
		self.SetStatusText('')
		try:
			# Catch for deaing with the new TBN tuning range of 5 to 93 MHz
			isTBN = (self.project.sessions[0].observations[obsIndex].mode == 'TBN')
			if self.coerceMap[obsAttr].__name__ == "freqConv":
				newData = self.coerceMap[obsAttr](event.GetText(), tbn=isTBN)
			else:
				newData = self.coerceMap[obsAttr](event.GetText())
			
			oldData = getattr(self.project.sessions[0].observations[obsIndex], self.columnMap[obsAttr])
			setattr(self.project.sessions[0].observations[obsIndex], self.columnMap[obsAttr], newData)
			self.project.sessions[0].observations[obsIndex].update()
			
			item = self.listControl.GetItem(obsIndex, obsAttr)
			if self.listControl.GetItemTextColour(item.GetId()) != (0, 0, 0, 255):
				self.listControl.SetItemTextColour(item.GetId(), wx.BLACK)
				self.listControl.RefreshItem(item.GetId())
			
			self.edited = True
			self.setSaveButton()

			self.badEdit = False
			self.badEditLocation = (-1, -1)
		except ValueError as err:
			print '[%i] Error: %s' % (os.getpid(), str(err))
			self.SetStatusText('Error: %s' % str(err))
			
			item = self.listControl.GetItem(obsIndex, obsAttr)
			self.listControl.SetItemTextColour(item.GetId(), wx.RED)
			self.listControl.RefreshItem(item.GetId())

			self.badEdit = True
			self.badEditLocation = (obsIndex, obsAttr)
	
	def onRemove(self, event):
		"""
		Remove selected observations from the main window as well as the 
		self.project.sessions[0].observations list.
		"""
		
		def stillBad(lc):
			"""
			Function to recur throught the rows and check to see if any still 
			need to be removed.  Returns the index+1 of the next element to be
			removed.
			
			Why index+1?  Well... because 0 is interperated as False and 1+ as
			True.  Thus if any one row is bad, value corresponding to boolean
			True is returned.
			"""
			
			for i in xrange(lc.GetItemCount()):
				if lc.IsChecked(i):
					return i+1
			return 0

		# While there is still at least one bad row, continue looping and removing
		# rows
		bad = stillBad(self.listControl)
		while bad:
			i = bad - 1
			self.listControl.DeleteItem(i)
			del self.project.sessions[0].observations[i]
			self.listControl.nSelected -= 1
			bad = stillBad(self.listControl)
			
			self.edited = True
			self.setSaveButton()
			
		# Update the check controlled features
		self.listControl.setCheckDependant()
		
		# Re-number the remaining rows to keep the display clean
		for i in xrange(self.listControl.GetItemCount()):
			item = self.listControl.GetItem(i, 0)
			item.SetText('%i' % (i+1))
			self.listControl.SetItem(item)
			self.listControl.RefreshItem(item.GetId())
	
	def onValidate(self, event, confirmValid=True):
		"""
		Validate the current observations.
		"""
		
		try:
			if self.badEdit:
				validObs = False
				return False
			else:
				validObs = True
		except AttributeError:
			validObs = True

		# Loop through the lists of observations and validate one-at-a-time so 
		# that we can mark bad observations
		i = 0
		for obs in self.project.sessions[0].observations:
			print "[%i] Validating observation %i" % (os.getpid(), i+1)
			valid = obs.validate(verbose=True)
			for col in xrange(len(self.columnMap)):
				item = self.listControl.GetItem(i, col)
			
				if not valid:
					self.listControl.SetItemTextColour(item.GetId(), wx.RED)
					self.listControl.RefreshItem(item.GetId())
					validObs = False
				else:
					if self.listControl.GetItemTextColour(item.GetId()) != (0, 0, 0, 255):
						self.listControl.SetItemTextColour(item.GetId(), wx.BLACK)
						self.listControl.RefreshItem(item.GetId())
				
			i += 1
		
		# Do a global validation
		sys.stdout = StringIO.StringIO()
		if self.project.validate(verbose=True):
			msg =  sys.stdout.getvalue()[:-1]
			sys.stdout.close()
			sys.stdout = sys.__stdout__
			if confirmValid:
				wx.MessageBox('Congratulations, you have a valid set of observations.', 'Validator Results')
			return True
		else:
			msg =  sys.stdout.getvalue()[:-1]
			sys.stdout.close()
			sys.stdout = sys.__stdout__
			
			msgLines = msg.split('\n')
			for msg in msgLines:
				if msg.find('Error') != -1:
					print msg
					
			if validObs:
				wx.MessageBox('All observations are valid, but there are errors in the session setup.', 'Validator Results')
			return False

	def onResolve(self, event):
		"""
		Display a window to resolve a target name to ra/dec coordinates.
		"""

		ResolveTarget(self)
	
	def onTimeseries(self, event):
		"""
		Display a window showing the layout of the observations in time.
		"""
		
		SessionDisplay(self)
	
	def onAdvanced(self, event):
		"""
		Display the advanced settings dialog for controlling TBW samples and
		data return method.
		"""
		
		AdvancedInfo(self)
	
	def onVolume(self, event):
		"""
		Display a message window showing the data used for each observation 
		and the total data volume.
		"""
		
		VolumeInfo(self)
	
	def onHelp(self, event):
		"""
		Display the help window.
		"""
		
		HelpWindow(self)
	
	def onFilterInfo(self, event):
		"""
		Display a dialog box listing the TBN and DRX filter codes along with the
		bandwidth associated with each.
		"""
		
		def units(value):
			if value >= 1e6:
				return float(value)/1e6, 'MHz'
			elif value >= 1e3:
				return float(value)/1e3, 'kHz'
			else:
				return value, Hz
		
		if self.mode == 'TBN':
			filterInfo = "TBN"
			for tk,tv in TBNFilters.iteritems():
				tv, tu = units(tv)
				filterInfo = "%s\n%i  %.3f %-3s" % (filterInfo, tk, tv, tu)
		elif self.mode == 'DRX':
			filterInfo = "DRX"
			for dk,dv in DRXFilters.iteritems():
				dv, du = units(dv)
				filterInfo = "%s\n%i  %.3f %-3s" % (filterInfo, dk, dv, du)
		else:
			filterInfo = 'No filters defined for the current mode.'
			
		wx.MessageBox(filterInfo, 'Filter Codes')
	
	def onAbout(self, event):
		"""
		Display a ver very very brief 'about' window.
		"""
		
		dialog = wx.AboutDialogInfo()
		
		dialog.SetIcon(wx.Icon(os.path.join(self.scriptPath, 'icons', 'lwa.png'), wx.BITMAP_TYPE_PNG))
		dialog.SetName('Session GUI')
		dialog.SetVersion(__version__)
		dialog.SetDescription("""GUI for creating session definition files to define observations with the Long Wavelength Array.""")
		dialog.SetWebSite('http://lwa.unm.edu')
		dialog.AddDeveloper(__author__)
		
		# Debuggers/testers
		dialog.AddDocWriter(__author__)
		dialog.AddDocWriter("Chenoa Tremblay")
		dialog.AddDocWriter("Aaron Gibson")
		
		wx.AboutBox(dialog)
	
	def onQuit(self, event):
		"""
		Quit the main window.
		"""
		
		if self.edited:
			dialog = wx.MessageDialog(self, 'The current session defintion file has changes that have not been saved.\n\nExit anyways?', 'Confirm Quit', style=wx.YES_NO|wx.NO_DEFAULT|wx.ICON_QUESTION)
			
			if dialog.ShowModal() == wx.ID_YES:
				self.Destroy()
			else:
				pass
		else:
			self.Destroy()
		
		
	def addColumns(self):
		"""
		Add the various columns to the main window based on the type of 
		observations being defined.
		"""

		def raConv(text):
			"""
			Special conversion function for deal with RA values.
			"""
			
			fields = text.split(':')
			fields = [float(f) for f in fields]
			sign = 1
			if fields[0] < 0:
				sign = -1
			fields[0] = abs(fields[0])
			
			value = 0
			for f,d in zip(fields, [1.0, 60.0, 3600.0]):
				value += (f / d)
			value *= sign
			
			if value <= 0 or value >= 24:
				raise ValueError("RA value must be 0 < RA < 24")
			else:
				return value

		def decConv(text):
			"""
			Special conversion function for dealing with dec. values.
			"""
			
			fields = text.split(':')
			fields = [float(f) for f in fields]
			sign = 1
			if fields[0] < 0:
				sign = -1
			fields[0] = abs(fields[0])
			
			value = 0
			for f,d in zip(fields, [1.0, 60.0, 3600.0]):
				value += (f / d)
			value *= sign
			
			if value < -90 or value > 90:
				raise ValueError("Dec values must be -90 <= dec <= 90")
			else:
				return value

		def freqConv(text, tbn=False):
			"""
			Special conversion function for dealing with frequencies.
			"""
			
			lowerLimit = 219130984
			upperLimit = 1928352663
			if tbn:
				lowerLimit = 109565492
				upperLimit = 2037918156

			value = float(text)*1e6
			freq = int(round(value * 2**32 / fS))
			if freq < lowerLimit or freq > upperLimit:
				raise ValueError("Frequency of %.6f MHz is out of the DP tuning range" % (value/1e6,))
			else:
				return value

		def filterConv(text):
			"""
			Special conversion function for dealing with filter codes.
			"""

			value = int(text)
			if value < 1 or value > 7:
				raise ValueError("Filter code must be an integer between 1 and 7")
			else:
				return value
		
		def snrConv(text):
			"""
			Special conversion function for dealing with the MaxSNR keyword input.
			"""
			
			text = text.lower().capitalize()
			if text == 'True' or text == 'Yes':
				return True
			elif text == 'False' or text == 'No':
				return False
			else:
				raise ValueError("Unknown boolean conversion of '%s'" % text)
		
		width = 50 + 100 + 100 + 100 + 235
		self.columnMap = []
		self.coerceMap = []
		
		self.listControl.InsertColumn(0, 'ID', width=50)
		self.listControl.InsertColumn(1, 'Name', width=100)
		self.listControl.InsertColumn(2, 'Target', width=100)
		self.listControl.InsertColumn(3, 'Comments', width=100)
		self.listControl.InsertColumn(4, 'Start (UTC)', width=235)
		self.columnMap.append('id')
		self.columnMap.append('name')
		self.columnMap.append('target')
		self.columnMap.append('comments')
		self.columnMap.append('start')
		for i in xrange(5):
			self.coerceMap.append(str)
		
		if self.mode == 'TBW' and not ALLOW_TBW_TBN_SAME_SDF:
			pass
		elif self.mode == 'TBN' or (self.mode == 'TBW' and ALLOW_TBW_TBN_SAME_SDF):
			width += 125 + 125 + 85
			self.listControl.InsertColumn(5, 'Duration', width=125)
			self.listControl.InsertColumn(6, 'Frequency (MHz)', width=125)
			self.listControl.InsertColumn(7, 'Filter Code', width=85)
			self.columnMap.append('duration')
			self.columnMap.append('frequency1')
			self.columnMap.append('filter')
			self.coerceMap.append(str)
			self.coerceMap.append(freqConv)
			self.coerceMap.append(filterConv)
		elif self.mode == 'DRX':
			width += 125 + 150 + 150 + 125 + 125 + 85 + 125
			self.listControl.InsertColumn(5, 'Duration', width=125)
			self.listControl.InsertColumn(6, 'RA (Hour J2000)', width=150)
			self.listControl.InsertColumn(7, 'Dec (Deg. J2000)', width=150)
			self.listControl.InsertColumn(8, 'Tuning 1 (MHz)', width=125)
			self.listControl.InsertColumn(9, 'Tuning 2 (MHz)', width=125)
			self.listControl.InsertColumn(10, 'Filter Code', width=85)
			self.listControl.InsertColumn(11, 'Max S/N Beam?', width=125)
			self.columnMap.append('duration')
			self.columnMap.append('ra')
			self.columnMap.append('dec')
			self.columnMap.append('frequency1')
			self.columnMap.append('frequency2')
			self.columnMap.append('filter')
			self.columnMap.append('MaxSNR')
			self.coerceMap.append(str)
			self.coerceMap.append(raConv)
			self.coerceMap.append(decConv)
			self.coerceMap.append(freqConv)
			self.coerceMap.append(freqConv)
			self.coerceMap.append(filterConv)
			self.coerceMap.append(snrConv)
		else:
			pass
		
		size = self.listControl.GetSize()
		size[0] = width
		self.listControl.SetMinSize(size)
		self.listControl.Fit()
	
		size = self.GetSize()
		width = min([width, wx.GetDisplaySize()[0]])
		self.SetMinSize((width, size[1]))
		self.panel.SetupScrolling(scroll_x=True, scroll_y=False)
		self.Fit()
		
	def addObservation(self, obs, id):
		"""
		Add an observation to a particular location in the observation list
		
		.. note::
			This only updates the list visible on the screen, not the SD list
			stored in self.project
		"""
		
		listIndex = id
		
		index = self.listControl.InsertStringItem(listIndex, str(id))
		self.listControl.SetStringItem(index, 1, obs.name)
		self.listControl.SetStringItem(index, 2, obs.target)
		if obs.comments is not None:
			self.listControl.SetStringItem(index, 3, obs.comments)
		else:
			self.listControl.SetStringItem(index, 3, 'None provided')
		self.listControl.SetStringItem(index, 4, obs.start)
		
		if self.mode == 'TBN':
			if ALLOW_TBW_TBN_SAME_SDF and obs.mode == 'TBW':
				self.listControl.SetStringItem(index, 5, obs.duration)
				self.listControl.SetStringItem(index, 6, "--")
				self.listControl.SetStringItem(index, 7, "--")
			else:
				self.listControl.SetStringItem(index, 5, obs.duration)
				self.listControl.SetStringItem(index, 6, "%.6f" % (obs.freq1*fS/2**32 / 1e6))
				self.listControl.SetStringItem(index, 7, "%i" % obs.filter)
		elif self.mode == 'TBW':
			if ALLOW_TBW_TBN_SAME_SDF and obs.mode == 'TBW':
				self.listControl.SetStringItem(index, 5, obs.duration)
				self.listControl.SetStringItem(index, 6, "--")
				self.listControl.SetStringItem(index, 7, "--")
			elif ALLOW_TBW_TBN_SAME_SDF and obs.mode == 'TBN':
				self.listControl.SetStringItem(index, 5, obs.duration)
				self.listControl.SetStringItem(index, 6, "%.6f" % (obs.freq1*fS/2**32 / 1e6))
				self.listControl.SetStringItem(index, 7, "%i" % obs.filter)
		
		if self.mode == 'DRX':
			def dec2sexstr(value, signed=True):
				sign = 1
				if value < 0:
					sign = -1
				value = abs(value)
				
				d = sign*int(value)
				m = int(value*60) % 60
				s = float(value*3600) % 60
				
				if signed:
					return '%+03i:%02i:%04.1f' % (d, m, s)
				else:
					return '%02i:%02i:%05.2f' % (d, m, s)
			
			if obs.mode == 'STEPPED':
				obs.getDuration()
				self.listControl.SetStringItem(index, 5, obs.duration)
				self.listControl.SetStringItem(index, 8, "--")
				self.listControl.SetStringItem(index, 9, "--")
				self.listControl.SetStringItem(index, 11, "--")
			else:
				self.listControl.SetStringItem(index, 5, obs.duration)
				self.listControl.SetStringItem(index, 8, "%.6f" % (obs.freq1*fS/2**32 / 1e6))
				self.listControl.SetStringItem(index, 9, "%.6f" % (obs.freq2*fS/2**32 / 1e6))
				if obs.MaxSNR:
					self.listControl.SetStringItem(index, 11, "Yes")
				else:
					self.listControl.SetStringItem(index, 11, "No")
				
			if obs.mode == 'TRK_SOL':
				self.listControl.SetStringItem(index, 6, "Sun")
				self.listControl.SetStringItem(index, 7, "--")
			elif obs.mode == 'TRK_JOV':
				self.listControl.SetStringItem(index, 6, "Jupiter")
				self.listControl.SetStringItem(index, 7, "--")
			elif obs.mode == 'STEPPED':
				self.listControl.SetStringItem(index, 6, "STEPPED")
				self.listControl.SetStringItem(index, 7, "RA/Dec" if obs.RADec else "Az/Alt")
			else:
				self.listControl.SetStringItem(index, 6, dec2sexstr(obs.ra, signed=False))
				self.listControl.SetStringItem(index, 7, dec2sexstr(obs.dec, signed=True))
			self.listControl.SetStringItem(index, 10, "%i" % obs.filter)
	
	def setSaveButton(self):
		"""
		Control that data of the various 'save' options based on the value of
		self.edited.
		"""
		
		if self.edited:
			self.savemenu.Enable(True)
			self.toolbar.EnableTool(ID_SAVE, True)
		else:
			self.savemenu.Enable(False)
			self.toolbar.EnableTool(ID_SAVE, False)
	
	def setMenuButtons(self, mode):
		"""
		Given a mode of observation (TBW, TBN, TRK_RADEC, etc.), update the 
		various menu items in 'Observations' and the toolbar buttons.
		"""
		
		mode = mode.lower()
		
		if mode == 'tbw':
			self.obsmenu['tbw'].Enable(True)
			self.obsmenu['tbn'].Enable(ALLOW_TBW_TBN_SAME_SDF & True)
			self.obsmenu['drx-radec'].Enable(False)
			self.obsmenu['drx-solar'].Enable(False)
			self.obsmenu['drx-jovian'].Enable(False)
			self.obsmenu['steppedRADec'].Enable(False)
			self.obsmenu['steppedAzAlt'].Enable(False)
			self.obsmenu['steppedEdit'].Enable(False)
			
			self.toolbar.EnableTool(ID_ADD_TBW, True)
			self.toolbar.EnableTool(ID_ADD_TBN, ALLOW_TBW_TBN_SAME_SDF & True)
			self.toolbar.EnableTool(ID_ADD_DRX_RADEC,  False)
			self.toolbar.EnableTool(ID_ADD_DRX_SOLAR,  False)
			self.toolbar.EnableTool(ID_ADD_DRX_JOVIAN, False)
			self.toolbar.EnableTool(ID_ADD_STEPPED_RADEC, False)
			self.toolbar.EnableTool(ID_ADD_STEPPED_AZALT, False)
			self.toolbar.EnableTool(ID_EDIT_STEPPED, False)
		elif mode == 'tbn':
			self.obsmenu['tbw'].Enable(ALLOW_TBW_TBN_SAME_SDF & True)
			self.obsmenu['tbn'].Enable(True)
			self.obsmenu['drx-radec'].Enable(False)
			self.obsmenu['drx-solar'].Enable(False)
			self.obsmenu['drx-jovian'].Enable(False)
			self.obsmenu['steppedRADec'].Enable(False)
			self.obsmenu['steppedAzAlt'].Enable(False)
			self.obsmenu['steppedEdit'].Enable(False)
			
			self.toolbar.EnableTool(ID_ADD_TBW, ALLOW_TBW_TBN_SAME_SDF & True)
			self.toolbar.EnableTool(ID_ADD_TBN, True)
			self.toolbar.EnableTool(ID_ADD_DRX_RADEC,  False)
			self.toolbar.EnableTool(ID_ADD_DRX_SOLAR,  False)
			self.toolbar.EnableTool(ID_ADD_DRX_JOVIAN, False)
			self.toolbar.EnableTool(ID_ADD_STEPPED_RADEC, False)
			self.toolbar.EnableTool(ID_ADD_STEPPED_AZALT, False)
			self.toolbar.EnableTool(ID_EDIT_STEPPED, False)
		elif mode[0:3] == 'trk' or mode[0:3] == 'drx':
			self.obsmenu['tbw'].Enable(False)
			self.obsmenu['tbn'].Enable(False)
			self.obsmenu['drx-radec'].Enable(True)
			self.obsmenu['drx-solar'].Enable(True)
			self.obsmenu['drx-jovian'].Enable(True)
			self.obsmenu['steppedRADec'].Enable(True)
			self.obsmenu['steppedAzAlt'].Enable(True)
			self.obsmenu['steppedEdit'].Enable(False)
			
			self.toolbar.EnableTool(ID_ADD_TBW, False)
			self.toolbar.EnableTool(ID_ADD_TBN, False)
			self.toolbar.EnableTool(ID_ADD_DRX_RADEC,  True)
			self.toolbar.EnableTool(ID_ADD_DRX_SOLAR,  True)
			self.toolbar.EnableTool(ID_ADD_DRX_JOVIAN, True)
			self.toolbar.EnableTool(ID_ADD_STEPPED_RADEC, True)
			self.toolbar.EnableTool(ID_ADD_STEPPED_AZALT, True)
			self.toolbar.EnableTool(ID_EDIT_STEPPED, False)
		else:
			self.obsmenu['tbw'].Enable(False)
			self.obsmenu['tbn'].Enable(False)
			self.obsmenu['drx-radec'].Enable(False)
			self.obsmenu['drx-solar'].Enable(False)
			self.obsmenu['drx-jovian'].Enable(False)
			self.obsmenu['steppedRADec'].Enable(False)
			self.obsmenu['steppedAzAlt'].Enable(False)
			self.obsmenu['steppedEdit'].Enable(False)
			
			self.toolbar.EnableTool(ID_ADD_TBW, False)
			self.toolbar.EnableTool(ID_ADD_TBN, False)
			self.toolbar.EnableTool(ID_ADD_DRX_RADEC,  False)
			self.toolbar.EnableTool(ID_ADD_DRX_SOLAR,  False)
			self.toolbar.EnableTool(ID_ADD_DRX_JOVIAN, False)
			self.toolbar.EnableTool(ID_ADD_STEPPED_RADEC, False)
			self.toolbar.EnableTool(ID_ADD_STEPPED_AZALT, False)
			self.toolbar.EnableTool(ID_EDIT_STEPPED, False)
	
	def parseFile(self, filename):
		"""
		Given a filename, parse the file using the sdf.parseSDF() method and 
		update all of the various aspects of the GUI (observation list, mode, 
		button, menu items, etc.).
		"""
		
		self.listControl.DeleteAllItems()
		self.listControl.DeleteAllColumns()
		self.initSDF()
		
		print "Parsing file '%s'" % filename
		self.project = sdf.parseSDF(filename)
		self.setMenuButtons(self.project.sessions[0].observations[0].mode)
		if self.project.sessions[0].observations[0].mode == 'TBW':
			self.mode = 'TBW'
		elif self.project.sessions[0].observations[0].mode == 'TBN':
			self.mode = 'TBN'
		elif self.project.sessions[0].observations[0].mode[0:3] == 'TRK':
			self.mode = 'DRX'
		else:
			pass
		
		try:
			self.project.sessions[0].tbwBits = self.project.sessions[0].observations[0].bits
			self.project.sessions[0].tbwSamples = self.project.sessions[0].observations[0].samples
		except:
			if self.mode == 'TBN' and ALLOW_TBW_TBN_SAME_SDF:
				self.project.sessions[0].tbwBits = 12
				self.project.sessions[0].tbwSamples = 12000000
		self.project.sessions[0].tbnGain = self.project.sessions[0].observations[0].gain
		self.project.sessions[0].drxGain = self.project.sessions[0].observations[0].gain
		
		self.addColumns()
		id = 1
		for obs in self.project.sessions[0].observations:
			self.addObservation(obs, id)
			id += 1

	def displayError(self, error, details=None, title=None):
		"""
		Display an error dialog and write an error message to the command 
		line if requested.
		"""
		if title is None:
			title = 'An Error has Occured'
		

		if details is None:
			print "[%i] Error: %s" % (os.getpid(), str(error))
			self.statusbar.SetStatusText('Error: %s' % str(error), 1)
			dialog = wx.MessageDialog(self, '%s' % str(error), title, style=wx.OK|wx.ICON_ERROR)
		else:
			print "[%i] Error: %s" % (os.getpid(), str(details))
			self.statusbar.SetStatusText('Error: %s' % str(details), 1)
			dialog = wx.MessageDialog(self, '%s\n\nDetails:\n%s' % (str(error), str(details)), title, style=wx.OK|wx.ICON_ERROR)

		dialog.ShowModal()


ID_OBS_INFO_OK = 211
ID_OBS_INFO_CANCEL = 212

class ObserverInfo(wx.Frame):
	"""
	Class to hold information about the observer (name, ID), the current project 
	(title, ID), and what type of session this will be (TBW, TBN, etc.).
	"""
	
	def __init__(self, parent):
		wx.Frame.__init__(self, parent, title='Observer Information', size=(825,735))
		
		self.parent = parent
		
		self.initUI()
		self.initEvents()
		self.Show()
		
	def initUI(self):
		row = 0
		panel = ScrolledPanel(self)
		sizer = wx.GridBagSizer(5, 5)
		
		font = wx.SystemSettings_GetFont(wx.SYS_SYSTEM_FONT)
		font.SetPointSize(font.GetPointSize()+2)
		
		#
		# Observer Info
		#
		
		obs = wx.StaticText(panel, label='Observer Information')
		obs.SetFont(font)
		
		oid = wx.StaticText(panel, label='ID Number')
		fname = wx.StaticText(panel, label='First Name')
		lname = wx.StaticText(panel, label='Last Name')
		
		oidText = wx.TextCtrl(panel)
		fnameText = wx.TextCtrl(panel)
		lnameText = wx.TextCtrl(panel)
		if self.parent.project.observer.id != 0:
			oidText.SetValue(str(self.parent.project.observer.id))
		if self.parent.project.observer.first != '':
			fnameText.SetValue(self.parent.project.observer.first)
			lnameText.SetValue(self.parent.project.observer.last)
		
		sizer.Add(obs, pos=(row+0,0), span=(1,6), flag=wx.ALIGN_CENTER, border=5)
		
		sizer.Add(oid, pos=(row+1, 0), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		sizer.Add(oidText, pos=(row+1, 1), span=(1, 5), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		
		sizer.Add(fname, pos=(row+2, 0), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		sizer.Add(fnameText, pos=(row+2, 1), span=(1, 5), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		sizer.Add(lname, pos=(row+3, 0), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		sizer.Add(lnameText, pos=(row+3, 1), span=(1, 5), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		
		line = wx.StaticLine(panel)
		sizer.Add(line, pos=(row+4, 0), span=(1, 6), flag=wx.EXPAND|wx.BOTTOM, border=10)
		
		row += 5
		
		#
		# Project Info
		#
		
		prj = wx.StaticText(panel, label='Project Information')
		prj.SetFont(font)
		
		pid = wx.StaticText(panel, label='ID Code')
		pname = wx.StaticText(panel, label='Title')
		pcoms = wx.StaticText(panel, label='Comments')
		
		pidText = wx.TextCtrl(panel)
		pnameText = wx.TextCtrl(panel)
		pcomsText = wx.TextCtrl(panel, style=wx.TE_MULTILINE)
		if self.parent.project.id != '':
			pidText.SetValue(str(self.parent.project.id))
		if self.parent.project.name != '':
			pnameText.SetValue(self.parent.project.name)
		if self.parent.project.comments != '' and self.parent.project.comments is not None:
			pcomsText.SetValue(self.parent.project.comments.replace(';;', '\n'))
		
		sizer.Add(prj, pos=(row+0,0), span=(1,6), flag=wx.ALIGN_CENTER, border=5)
		
		sizer.Add(pid, pos=(row+1, 0), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		sizer.Add(pidText, pos=(row+1, 1), span=(1, 5), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		
		sizer.Add(pname, pos=(row+2, 0), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		sizer.Add(pnameText, pos=(row+2, 1), span=(1, 5), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		sizer.Add(pcoms, pos=(row+3, 0), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		sizer.Add(pcomsText, pos=(row+3, 1), span=(4, 5), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		
		line = wx.StaticLine(panel)
		sizer.Add(line, pos=(row+7, 0), span=(1, 6), flag=wx.EXPAND|wx.BOTTOM, border=10)
		
		row += 8
		
		#
		# Session-Wide Info
		#
		
		ses = wx.StaticText(panel, label='Session Information')
		ses.SetFont(font)
		
		sid = wx.StaticText(panel, label='ID Number')
		sname = wx.StaticText(panel, label='Title')
		scoms = wx.StaticText(panel, label='Comments')
		
		sidText = wx.TextCtrl(panel)
		snameText = wx.TextCtrl(panel)
		scomsText = wx.TextCtrl(panel, style=wx.TE_MULTILINE)
		if self.parent.project.sessions[0].id != '':
			sidText.SetValue(str(self.parent.project.sessions[0].id))
		if self.parent.project.sessions[0].name != '':
			snameText.SetValue(self.parent.project.sessions[0].name)
		if self.parent.project.sessions[0].comments != '' and self.parent.project.sessions[0].comments is not None:
			scomsText.SetValue(self.parent.project.sessions[0].comments.replace(';;', '\n'))
		
		tid = wx.StaticText(panel, label='Session Type')
		tbwRB = wx.RadioButton(panel, -1, 'Transient Buffer-Wide (TBW)', style=wx.RB_GROUP)
		tbnRB = wx.RadioButton(panel, -1, 'Transient Buffer-Narrow (TBN)')
		drxRB = wx.RadioButton(panel, -1, 'Beam Forming')
		if self.parent.mode != '':
			if self.parent.mode == 'TBW':
				tbwRB.SetValue(True)
				tbnRB.SetValue(False)
				drxRB.SetValue(False)
			elif self.parent.mode == 'TBN':
				tbwRB.SetValue(False)
				tbnRB.SetValue(True)
				drxRB.SetValue(False)
			else:
				tbwRB.SetValue(False)
				tbnRB.SetValue(False)
				drxRB.SetValue(True)
				
			tbwRB.Disable()
			tbnRB.Disable()
			drxRB.Disable()
			
		else:
			tbwRB.SetValue(False)
			tbnRB.SetValue(False)
			drxRB.SetValue(True)
			
		did = wx.StaticText(panel, label='Data Return Method')
		drsuRB = wx.RadioButton(panel, -1, 'DRSU', style=wx.RB_GROUP)
		usbRB = wx.RadioButton(panel, -1, 'USB Harddrive (4 max)')
		drsRB = wx.RadioButton(panel, -1, 'DR spectrometer')
		redRB = wx.RadioButton(panel, -1, 'Archive (describe in comments)')
		
		nchn = wx.StaticText(panel, label='Channels')
		nchnText = wx.TextCtrl(panel)
		nint = wx.StaticText(panel, label='FFTs/int.')
		nintText = wx.TextCtrl(panel)
		spid = wx.StaticText(panel, label='Data Products')
		linear = wx.RadioButton(panel, -1, 'Linear', style=wx.RB_GROUP)
		stokes = wx.RadioButton(panel, -1, 'Stokes')
		
		if self.parent.project.sessions[0].dataReturnMethod == 'DR Spectrometer' or (self.parent.project.sessions[0].spcSetup[0] != 0 and self.parent.project.sessions[0].spcSetup[1] != 0):
			drsuRB.SetValue(False)
			usbRB.SetValue(False)
			drsRB.SetValue(True)
			redRB.SetValue(False)
			
			nchnText.SetValue("%i" % self.parent.project.sessions[0].spcSetup[0])
			nintText.SetValue("%i" % self.parent.project.sessions[0].spcSetup[1])
			mt = self.parent.project.sessions[0].spcMetatag
			if mt is None:
				linear.SetValue(True)
				stokes.SetValue(False)
			else:
				junk, mt = mt.split('=', 1)
				mt = mt.replace('}', '')
				
				if mt in ('XX', 'YY', 'XY', 'YX', 'XXYY', 'XXXYYXYY'):
					linear.SetValue(True)
					stokes.SetValue(False)
				else:
					linear.SetValue(False)
					stokes.SetValue(True)
					
		elif self.parent.project.sessions[0].dataReturnMethod == 'DRSU':
			drsuRB.SetValue(True)
			usbRB.SetValue(False)
			drsRB.SetValue(False)
			redRB.SetValue(False)
			
			nchnText.SetValue("1024")
			nintText.SetValue("6144")
		elif self.parent.project.sessions[0].dataReturnMethod == 'USB Harddrives':
			drsuRB.SetValue(False)
			usbRB.SetValue(True)
			drsRB.SetValue(False)
			redRB.SetValue(False)
			
			nchnText.SetValue("1024")
			nintText.SetValue("6144")
			linear.SetValue(True)
			stokes.SetValue(False)
		else:
			drsuRB.SetValue(False)
			usbRB.SetValue(False)
			drsRB.SetValue(False)
			redRB.SetValue(True)
			
			nchnText.SetValue("32")
			nintText.SetValue("6144")
			linear.SetValue(True)
			stokes.SetValue(False)
			
		if self.parent.mode != '':
			if self.parent.mode == 'TBW':
				drsRB.Disable()
				nchnText.Disable()
				nintText.Disable()
				linear.Disable()
				stokes.Disable()
			elif self.parent.mode == 'TBN':
				drsRB.Disable()
				nchnText.Disable()
				nintText.Disable()
				linear.Disable()
				stokes.Disable()
			else:
				pass
		
		sizer.Add(ses, pos=(row+0, 0), span=(1,6), flag=wx.ALIGN_CENTER, border=5)
		
		sizer.Add(sid, pos=(row+1, 0), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		sizer.Add(sidText, pos=(row+1, 1), span=(1, 5), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		
		sizer.Add(sname, pos=(row+2, 0), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		sizer.Add(snameText, pos=(row+2, 1), span=(1, 5), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		sizer.Add(scoms, pos=(row+3, 0), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		sizer.Add(scomsText, pos=(row+3, 1), span=(4, 5), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		sizer.Add(tid, pos=(row+7,0), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		sizer.Add(tbwRB, pos=(row+7,1), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		sizer.Add(tbnRB, pos=(row+8,1), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		sizer.Add(drxRB, pos=(row+9,1), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		sizer.Add(did, pos=(row+10,0), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		sizer.Add(drsuRB, pos=(row+10,1), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		sizer.Add(usbRB, pos=(row+11,1), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		sizer.Add(drsRB, pos=(row+12,1), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		sizer.Add(nchn, pos=(row+12,2), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		sizer.Add(nchnText, pos=(row+12,3), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		sizer.Add(nint, pos=(row+12,4), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		sizer.Add(nintText, pos=(row+12,5), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		sizer.Add(spid, pos=(row+13,2), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		sizer.Add(linear, pos=(row+13,3), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		sizer.Add(stokes, pos=(row+13,4), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		
		sizer.Add(redRB, pos=(row+14,1), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		
		line = wx.StaticLine(panel)
		sizer.Add(line, pos=(row+15, 0), span=(1, 6), flag=wx.EXPAND|wx.BOTTOM, border=10)
		
		row += 16
		
		#
		# Buttons
		#
		
		ok = wx.Button(panel, ID_OBS_INFO_OK, 'Ok', size=(90, 28))
		cancel = wx.Button(panel, ID_OBS_INFO_CANCEL, 'Cancel', size=(90, 28))
		sizer.Add(ok, pos=(row+0, 4))
		sizer.Add(cancel, pos=(row+0, 5), flag=wx.RIGHT|wx.BOTTOM, border=5)
		
		sizer.AddGrowableCol(1)
		sizer.AddGrowableRow(8)
		
		panel.SetupScrolling(scroll_x=True, scroll_y=True) 
		panel.SetSizer(sizer)
		panel.Fit()
		
		#
		# Save the various widgets for access later
		#
		
		self.observerIDEntry = oidText
		self.observerFirstEntry = fnameText
		self.observerLastEntry = lnameText
		
		self.projectIDEntry = pidText
		self.projectTitleEntry = pnameText
		self.projectCommentsEntry = pcomsText
		
		self.sessionIDEntry = sidText
		self.sessionTitleEntry = snameText
		self.sessionCommentsEntry = scomsText
		self.tbwButton = tbwRB
		self.tbnButton = tbnRB
		self.drxButton = drxRB
		self.drsuButton = drsuRB
		self.usbButton = usbRB
		self.drsButton = drsRB
		self.nchnText = nchnText
		self.nintText = nintText
		self.linear = linear
		self.stokes = stokes
		self.redButton = redRB 
		
	def initEvents(self):
		self.Bind(wx.EVT_BUTTON, self.onOK, id=ID_OBS_INFO_OK)
		self.Bind(wx.EVT_BUTTON, self.onCancel, id=ID_OBS_INFO_CANCEL)
		
	def onOK(self, event):
		"""
		Save everything into all of the correct places.
		"""
		
		try:
			junk = int(self.observerIDEntry.GetValue())
			if junk < 1:
				self.displayError('Observer ID must be greater than zero', title='Observer ID Error')
				return False
		except ValueError as err:
			self.displayError('Observer ID must be numeric', details=err, title='Observer ID Error')
			return False

		try:
			junk = int(self.sessionIDEntry.GetValue())
			if junk < 1:
				self.displayError('Session ID must be greater than zero', title='Session ID Error')
				return False
		except ValueError as err:
			self.displayError('Session ID must be numeric', details=err, title='Session ID Error')
			return False
		
		self.parent.project.observer.id = int(self.observerIDEntry.GetValue())
		self.parent.project.observer.first = self.observerFirstEntry.GetValue()
		self.parent.project.observer.last = self.observerLastEntry.GetValue()
		self.parent.project.observer.joinName()
		
		self.parent.project.id = self.projectIDEntry.GetValue()
		self.parent.project.name = self.projectTitleEntry.GetValue()
		self.parent.project.comments = self.projectCommentsEntry.GetValue().replace('\n', ';;')
		
		self.parent.project.sessions[0].id = int(self.sessionIDEntry.GetValue())
		self.parent.project.sessions[0].name = self.sessionTitleEntry.GetValue()
		self.parent.project.sessions[0].comments = self.sessionCommentsEntry.GetValue().replace('\n', ';;')
		
		if self.drsuButton.GetValue():
			self.parent.project.sessions[0].dataReturnMethod = 'DRSU'
			self.parent.project.sessions[0].spcSetup = [0, 0]
			self.parent.project.sessions[0].spcMetatag = None
		elif self.usbButton.GetValue():
			self.parent.project.sessions[0].dataReturnMethod = 'USB Harddrives'
			self.parent.project.sessions[0].spcSetup = [0, 0]
			self.parent.project.sessions[0].spcMetatag = None
		elif self.drsButton.GetValue():
			self.parent.project.sessions[0].dataReturnMethod = 'DR Spectrometer'
			nchn = int(self.nchnText.GetValue())
			nint = int(self.nintText.GetValue())
			self.parent.project.sessions[0].spcSetup = [nchn, nint]
			
			mt = self.parent.project.sessions[0].spcMetatag
			if mt is None:
				isLinear = True
			else:
				junk, mt = mt.split('=', 1)
				mt = mt.replace('}', '')
				
				if mt in ('XX', 'YY', 'XY', 'YX', 'XXYY', 'XXXYYXYY'):
					isLinear = True
				else:
					isLinear = False
			if self.linear.GetValue() and not isLinear:
				self.parent.project.sessions[0].spcMetatag = '{Stokes=XXYY}'
			if self.stokes.GetValue() and isLinear:
				self.parent.project.sessions[0].spcMetatag = '{Stokes=IQUV}'
		else:
			self.parent.project.sessions[0].dataReturnMethod = 'Reduced per comments'
			self.parent.project.sessions[0].spcSetup = [0, 0]
			self.parent.project.sessions[0].spcMetatag = None
		
		if self.tbwButton.GetValue():
			self.parent.mode = 'TBW'
			self.parent.project.sessions[0].includeStationStatic = True
		elif self.tbnButton.GetValue():
			self.parent.mode = 'TBN'
			self.parent.project.sessions[0].includeStationStatic = True
		else:
			self.parent.mode = 'DRX'
		self.parent.setMenuButtons(self.parent.mode)
		if self.parent.listControl.GetColumnCount() == 0:
			self.parent.addColumns()
		
		self.parent.edited = True
		self.parent.setSaveButton()
		
		self.Close()
		
	def onCancel(self, event):
		self.Close()

	def displayError(self, error, details=None, title=None):
		"""
		Display an error dialog and write an error message to the command 
		line if requested.
		"""
		if title is None:
			title = 'An Error has Occured'
		

		if details is None:
			print "[%i] Error: %s" % (os.getpid(), str(error))
			dialog = wx.MessageDialog(self, '%s' % str(error), title, style=wx.OK|wx.ICON_ERROR)
		else:
			print "[%i] Error: %s" % (os.getpid(), str(details))
			dialog = wx.MessageDialog(self, '%s\n\nDetails:\n%s' % (str(error), str(details)), title, style=wx.OK|wx.ICON_ERROR)

		dialog.ShowModal()


ID_ADV_INFO_OK = 311
ID_ADV_INFO_CANCEL = 312

class AdvancedInfo(wx.Frame):
	def __init__(self, parent):
		if parent.mode == 'TBW'and ALLOW_TBW_TBN_SAME_SDF:
			size = (925, 675)
		elif parent.mode == 'TBW':
			size = (925, 575)
		else:
			size = (925, 580)

		wx.Frame.__init__(self, parent, title='Advanced Settings', size=size)
		
		self.parent = parent
		self.bitsEntry = None
		self.samplesEntry = None
		self.drsuButton = None
		self.usbButton = None
		self.reduceButton = None
		self.reduceEntry = None
		
		self.initUI()
		self.initEvents()
		self.Show()
		
	def initUI(self):
		bits = ['12-bit', '4-bit']
		tbnGain = ['%i' % i for i in xrange(31)]
		tbnGain.insert(0, 'MCS Decides')
		drxGain = ['%i' % i for i in xrange(13)]
		drxGain.insert(0, 'MCS Decides')
		drxBeam = ['%i' %i for i in xrange(1, 5)]
		drxBeam.insert(0, 'MCS Decides')
		intervals = ['MCS Decides', 'Never', '1 minute', '5 minutes', '15 minutes', '30 minutes', '1 hour']
		aspFilters = ['MCS Decides', 'Split', 'Full', 'Reduced', 'Off']
		aspAttn = ['%i' % i for i in xrange(16)]
		aspAttn.insert(0, 'MCS Decides')
		
		row = 0
		panel = ScrolledPanel(self)
		sizer = wx.GridBagSizer(5, 5)
		
		font = wx.SystemSettings_GetFont(wx.SYS_SYSTEM_FONT)
		font.SetPointSize(font.GetPointSize()+2)
		
		#
		# MCS
		#
		
		mcs = wx.StaticText(panel, label='MCS-Specific Information')
		mcs.SetFont(font)
		
		mrp = wx.StaticText(panel, label='MIB Recording Period:')
		mrpASP = wx.StaticText(panel, label='ASP')
		mrpDP = wx.StaticText(panel, label='DP')
		mrpDR = wx.StaticText(panel, label='DR1 - DR5')
		mrpSHL = wx.StaticText(panel, label='SHL')
		mrpMCS = wx.StaticText(panel, label='MSC')
		mrpComboASP = wx.ComboBox(panel, -1, value='MCS Decides', choices=intervals, style=wx.CB_READONLY)
		mrpComboASP.SetStringSelection(self.__timeToCombo(self.parent.project.sessions[0].recordMIB['ASP']))
		mrpComboDP = wx.ComboBox(panel, -1, value='MCS Decides', choices=intervals, style=wx.CB_READONLY)
		mrpComboDP.SetStringSelection(self.__timeToCombo(self.parent.project.sessions[0].recordMIB['DP_']))
		mrpComboDR = wx.ComboBox(panel, -1, value='MCS Decides', choices=intervals, style=wx.CB_READONLY)
		mrpComboDR.SetStringSelection(self.__timeToCombo(self.parent.project.sessions[0].recordMIB['DR1']))
		mrpComboSHL = wx.ComboBox(panel, -1, value='MCS Decides', choices=intervals, style=wx.CB_READONLY)
		mrpComboSHL.SetStringSelection(self.__timeToCombo(self.parent.project.sessions[0].recordMIB['SHL']))
		mrpComboMCS = wx.ComboBox(panel, -1, value='MCS Decides', choices=intervals, style=wx.CB_READONLY)
		mrpComboMCS.SetStringSelection(self.__timeToCombo(self.parent.project.sessions[0].recordMIB['MCS']))
		
		mup = wx.StaticText(panel, label='MIB Update Period:')
		mupASP = wx.StaticText(panel, label='ASP')
		mupDP = wx.StaticText(panel, label='DP')
		mupDR = wx.StaticText(panel, label='DR1 - DR5')
		mupSHL = wx.StaticText(panel, label='SHL')
		mupMCS = wx.StaticText(panel, label='MSC')
		mupComboASP = wx.ComboBox(panel, -1, value='MCS Decides', choices=intervals, style=wx.CB_READONLY)
		mupComboASP.SetStringSelection(self.__timeToCombo(self.parent.project.sessions[0].updateMIB['ASP']))
		mupComboDP = wx.ComboBox(panel, -1, value='MCS Decides', choices=intervals, style=wx.CB_READONLY)
		mupComboDP.SetStringSelection(self.__timeToCombo(self.parent.project.sessions[0].updateMIB['DP_']))
		mupComboDR = wx.ComboBox(panel, -1, value='MCS Decides', choices=intervals, style=wx.CB_READONLY)
		mupComboDR.SetStringSelection(self.__timeToCombo(self.parent.project.sessions[0].updateMIB['DR1']))
		mupComboSHL = wx.ComboBox(panel, -1, value='MCS Decides', choices=intervals, style=wx.CB_READONLY)
		mupComboSHL.SetStringSelection(self.__timeToCombo(self.parent.project.sessions[0].updateMIB['SHL']))
		mupComboMCS = wx.ComboBox(panel, -1, value='MCS Decides', choices=intervals, style=wx.CB_READONLY)
		mupComboMCS.SetStringSelection(self.__timeToCombo(self.parent.project.sessions[0].updateMIB['MCS']))
		
		schLog = wx.CheckBox(panel, -1, label='Include relevant MSC/Scheduler Log')
		schLog.SetValue(self.parent.project.sessions[0].logScheduler)
		exeLog = wx.CheckBox(panel, -1, label='Include relevant MSC/Executive Log')
		exeLog.SetValue(self.parent.project.sessions[0].logExecutive)
		
		incSMIB = wx.CheckBox(panel, -1, 'Include station static MIB')
		incSMIB.SetValue(self.parent.project.sessions[0].includeStationStatic)
		incDESG = wx.CheckBox(panel, -1, 'Include design and calibration information')
		incDESG.SetValue(self.parent.project.sessions[0].includeDesign)
		
		sizer.Add(mcs, pos=(row+0,0), span=(1,6), flag=wx.ALIGN_CENTER, border=5)
		
		sizer.Add(mrp, pos=(row+1, 0), span=(1, 2), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		sizer.Add(mrpASP, pos=(row+2, 0), span=(1, 1), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		sizer.Add(mrpComboASP, pos=(row+2, 1), span=(1,1), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		sizer.Add(mrpDP, pos=(row+2, 2), span=(1, 1), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		sizer.Add(mrpComboDP, pos=(row+2, 3), span=(1,1), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		sizer.Add(mrpDR, pos=(row+2, 4), span=(1, 1), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		sizer.Add(mrpComboDR, pos=(row+2, 5), span=(1,1), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		sizer.Add(mrpSHL, pos=(row+3, 0), span=(1, 1), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		sizer.Add(mrpComboSHL, pos=(row+3, 1), span=(1,1), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		sizer.Add(mrpMCS, pos=(row+3, 2), span=(1, 1), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		sizer.Add(mrpComboMCS, pos=(row+3, 3), span=(1,1), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		
		sizer.Add(mup, pos=(row+4, 0), span=(1, 2), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		sizer.Add(mupASP, pos=(row+5, 0), span=(1, 1), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		sizer.Add(mupComboASP, pos=(row+5, 1), span=(1,1), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		sizer.Add(mupDP, pos=(row+5, 2), span=(1, 1), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		sizer.Add(mupComboDP, pos=(row+5, 3), span=(1,1), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		sizer.Add(mupDR, pos=(row+5, 4), span=(1, 1), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		sizer.Add(mupComboDR, pos=(row+5, 5), span=(1,1), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		sizer.Add(mupSHL, pos=(row+6, 0), span=(1, 1), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		sizer.Add(mupComboSHL, pos=(row+6, 1), span=(1,1), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		sizer.Add(mupMCS, pos=(row+6, 2), span=(1, 1), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		sizer.Add(mupComboMCS, pos=(row+6, 3), span=(1,1), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		
		sizer.Add(schLog, pos=(row+7, 0), span=(1, 2), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		sizer.Add(exeLog, pos=(row+7, 2), span=(1, 3), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		sizer.Add(incSMIB, pos=(row+8, 0), span=(1, 2), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		sizer.Add(incDESG, pos=(row+8, 2), span=(1, 3), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		
		line = wx.StaticLine(panel)
		sizer.Add(line, pos=(row+9, 0), span=(1, 6), flag=wx.EXPAND|wx.BOTTOM, border=10)
		
		row += 10

		#
		# ASP
		# 
	
		aspComboFlt = wx.ComboBox(panel, -1, value='MCS Decides', choices=aspFilters, style=wx.CB_READONLY)
		if self.parent.project.sessions[0].aspFlt[0] == -1:
			aspComboFlt.SetStringSelection('MCS Decides')
		elif self.parent.project.sessions[0].aspFlt[0] == 0:
			aspComboFlt.SetStringSelection('Split')
		elif self.parent.project.sessions[0].aspFlt[0] == 1:
			aspComboFlt.SetStringSelection('Full')
		elif self.parent.project.sessions[0].aspFlt[0] == 2:
			aspComboFlt.SetStringSelection('Reduced')
		else:
			aspComboAT1.SetStringSelection('Off')
		aspComboAT1 = wx.ComboBox(panel, -1, value='MCS Decides', choices=aspAttn, style=wx.CB_READONLY)
		if self.parent.project.sessions[0].aspAT1[0] == -1:
			aspComboAT1.SetStringSelection('MCS Decides')
		else:
			aspComboAT1.SetStringSelection('%i' % self.parent.project.sessions[0].aspAT1[0])
		aspComboAT2 = wx.ComboBox(panel, -1, value='MCS Decides', choices=aspAttn, style=wx.CB_READONLY)
		if self.parent.project.sessions[0].aspAT2[0] == -1:
			aspComboAT2.SetStringSelection('MCS Decides')
		else:
			aspComboAT2.SetStringSelection('%i' % self.parent.project.sessions[0].aspAT2[0])
		aspComboATS = wx.ComboBox(panel, -1, value='MCS Decides', choices=aspAttn, style=wx.CB_READONLY)
		if self.parent.project.sessions[0].aspATS[0] == -1:
			aspComboATS.SetStringSelection('MCS Decides')
		else:
			aspComboATS.SetStringSelection('%i' % self.parent.project.sessions[0].aspATS[0])

		asp = wx.StaticText(panel, label='ASP-Specific Information')
		asp.SetFont(font)

		flt = wx.StaticText(panel, label='Filter Mode Setting')
		at1 = wx.StaticText(panel, label='First Attenuator Setting')
		at2 = wx.StaticText(panel, label='Second Attenuator Setting')
		ats = wx.StaticText(panel, label='Split Attenuator Setting')
		fas1 = wx.StaticText(panel, label='for all inputs')
		fas2 = wx.StaticText(panel, label='for all inputs')
		fas3 = wx.StaticText(panel, label='for all inputs')
		fas4 = wx.StaticText(panel, label='for all inputs')

		sizer.Add(asp, pos=(row+0, 0), span=(1,6), flag=wx.ALIGN_CENTER, border=5)
		sizer.Add(flt, pos=(row+1, 0), span=(1, 2), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		sizer.Add(aspComboFlt, pos=(row+1, 2), span=(1, 2), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		sizer.Add(fas1, pos=(row+1, 4), span=(1, 2), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		sizer.Add(at1, pos=(row+2, 0), span=(1, 2), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		sizer.Add(aspComboAT1, pos=(row+2, 2), span=(1, 2), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		sizer.Add(fas2, pos=(row+2, 4), span=(1, 2), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		sizer.Add(at2, pos=(row+3, 0), span=(1, 2), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		sizer.Add(aspComboAT2, pos=(row+3, 2), span=(1, 2), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		sizer.Add(fas3, pos=(row+3, 4), span=(1, 2), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		sizer.Add(ats, pos=(row+4, 0), span=(1, 2), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		sizer.Add(aspComboATS, pos=(row+4, 2), span=(1, 2), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		sizer.Add(fas4, pos=(row+4, 4), span=(1, 2), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		
		line = wx.StaticLine(panel)
		sizer.Add(line, pos=(row+5, 0), span=(1, 6), flag=wx.EXPAND|wx.BOTTOM, border=10)
			
		row += 6
		
		#
		# TBW
		#
		
		if self.parent.mode == 'TBW' or (self.parent.mode == 'TBN' and ALLOW_TBW_TBN_SAME_SDF):
			tbw = wx.StaticText(panel, label='TBW-Specific Information')
			tbw.SetFont(font)
			
			tbits = wx.StaticText(panel, label='Data')
			tsamp = wx.StaticText(panel, label='Samples')
			tunit = wx.StaticText(panel, label='per capture')
			
			tbitsText = wx.ComboBox(panel, -1, value='12-bit', choices=bits, style=wx.CB_READONLY)
			tsampText = wx.TextCtrl(panel)
			tbitsText.SetStringSelection('%i-bit' % self.parent.project.sessions[0].tbwBits)
			tsampText.SetValue("%i" % self.parent.project.sessions[0].tbwSamples)
			
			sizer.Add(tbw, pos=(row+0,0), span=(1,6), flag=wx.ALIGN_CENTER, border=5)
			
			sizer.Add(tbits, pos=(row+1, 0), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
			sizer.Add(tbitsText, pos=(row+1, 1), span=(1, 1), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
			sizer.Add(tsamp, pos=(row+2, 0), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
			sizer.Add(tsampText, pos=(row+2, 1), span=(1, 1), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
			sizer.Add(tunit, pos=(row+2, 2), span=(1, 2), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
			
			line = wx.StaticLine(panel)
			sizer.Add(line, pos=(row+3, 0), span=(1, 6), flag=wx.EXPAND|wx.BOTTOM, border=10)
			
			row += 4
		
		#
		# TBN
		#
		
		if self.parent.mode == 'TBN' or (self.parent.mode == 'TBW' and ALLOW_TBW_TBN_SAME_SDF):
			tbn = wx.StaticText(panel, label='TBN-Specific Information')
			tbn.SetFont(font)
			
			tgain = wx.StaticText(panel, label='Gain')
			tgainText =  wx.ComboBox(panel, -1, value='MCS Decides', choices=tbnGain, style=wx.CB_READONLY)
			if self.parent.project.sessions[0].tbnGain == -1:
				tgainText.SetStringSelection('MCS Decides')
			else:
				tgainText.SetStringSelection('%i' % self.parent.project.sessions[0].tbnGain)
			
			sizer.Add(tbn, pos=(row+0,0), span=(1,6), flag=wx.ALIGN_CENTER, border=5)
			
			sizer.Add(tgain, pos=(row+1, 0), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
			sizer.Add(tgainText, pos=(row+1, 1), span=(1, 1), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
			
			line = wx.StaticLine(panel)
			sizer.Add(line, pos=(row+2, 0), span=(1, 6), flag=wx.EXPAND|wx.BOTTOM, border=10)
			
			row += 3
		
		#
		# DRX
		#
		
		if self.parent.mode == 'DRX':
			drx = wx.StaticText(panel, label='DRX-Specific Information')
			drx.SetFont(font)
			
			dgain = wx.StaticText(panel, label='Gain')
			dgainText =  wx.ComboBox(panel, -1, value='MCS Decides', choices=drxGain, style=wx.CB_READONLY)
			if self.parent.project.sessions[0].drxGain == -1:
				dgainText.SetStringSelection('MCS Decides')
			else:
				dgainText.SetStringSelection('%i' % self.parent.project.sessions[0].drxGain)
				
			dbeam = wx.StaticText(panel, label='Beam')
			dbeamText = wx.ComboBox(panel, -1, value='MCS Decides', choices=drxBeam, style=wx.CB_READONLY)
			if self.parent.project.sessions[0].drxBeam == -1:
				dbeamText.SetStringSelection('MCS Decides')
			else:
				dbeamText.SetStringSelection('%i' % self.parent.project.sessions[0].drxBeam)
				
			sizer.Add(drx, pos=(row+0,0), span=(1,6), flag=wx.ALIGN_CENTER, border=5)
			
			sizer.Add(dgain, pos=(row+1, 0), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
			sizer.Add(dgainText, pos=(row+1, 1), span=(1, 1), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
			sizer.Add(dbeam, pos=(row+2, 0), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
			sizer.Add(dbeamText, pos=(row+2, 1), span=(1, 1), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
			
			line = wx.StaticLine(panel)
			sizer.Add(line, pos=(row+3, 0), span=(1, 6), flag=wx.EXPAND|wx.BOTTOM, border=10)
			
			row += 4
			
		#
		# DROS
		#
		
		if self.parent.project.sessions[0].dataReturnMethod == 'DR Spectrometer' or (self.parent.project.sessions[0].spcSetup[0] != 0 and self.parent.project.sessions[0].spcSetup[1] != 0):
			dros = wx.StaticText(panel, label='DR Spectrometer Information')
			dros.SetFont(font)
			
			opt = wx.StaticText(panel, label='Data Products')
			
			mt = self.parent.project.sessions[0].spcMetatag
			if mt is None:
				isLinear = True
			else:
				junk, mt = mt.split('=', 1)
				mt = mt.replace('}', '')
				
				if mt in ('XX', 'YY', 'XY', 'YX', 'XXYY', 'XXXYYXYY'):
					isLinear = True
				else:
					isLinear = False
					
			if isLinear:
				opt1 = wx.RadioButton(panel, -1, 'XX', style=wx.RB_GROUP)
				opt2 = wx.RadioButton(panel, -1, 'XY')
				opt3 = wx.RadioButton(panel, -1, 'YX')
				opt4 = wx.RadioButton(panel, -1, 'YY')
				opt5 = wx.RadioButton(panel, -1, 'XX and YY')
				opt6 = wx.RadioButton(panel, -1, 'XX, XY, YX, and YY')
			else:
				opt1 = wx.RadioButton(panel, -1, 'I', style=wx.RB_GROUP)
				opt2 = wx.RadioButton(panel, -1, 'Q')
				opt3 = wx.RadioButton(panel, -1, 'U')
				opt4 = wx.RadioButton(panel, -1, 'V')
				opt5 = wx.RadioButton(panel, -1, 'I and V')
				opt6 = wx.RadioButton(panel, -1, 'I, Q, U, and V')
				
			opt1.SetValue(False)
			opt2.SetValue(False)
			opt3.SetValue(False)
			opt4.SetValue(False)
			opt5.SetValue(False)
			opt6.SetValue(False)
			
			# What's this?  The current version of DROS v2 (November 9, 2012) 
			# only supports XXYY, IV, and IQUV.
			opt1.Enable(False)
			opt2.Enable(False)
			opt3.Enable(False)
			opt4.Enable(False)
			if isLinear:
				opt6.Enable(False)
				
			if mt in ('XX', 'I'):
				opt1.SetValue(True)
			elif mt in ('XY', 'Q'):
				opt2.SetValue(True)
			elif mt in ('YX', 'U'):
				opt3.SetValue(True)
			elif mt in ('YY', 'V'):
				opt4.SetValue(True)
			elif mt in ('XXYY', 'IV'):
				opt5.SetValue(True)
			else:
				opt6.SetValue(True)
				
			sizer.Add(dros, pos=(row+0,0), span=(1,6), flag=wx.ALIGN_CENTER, border=5)
			
			sizer.Add(opt,  pos=(row+1, 0), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
			sizer.Add(opt1, pos=(row+1, 1), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
			sizer.Add(opt2, pos=(row+1, 2), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
			sizer.Add(opt3, pos=(row+1, 3), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
			sizer.Add(opt4, pos=(row+2, 1), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
			sizer.Add(opt5, pos=(row+2, 2), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
			sizer.Add(opt6, pos=(row+2, 3), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
			
			line = wx.StaticLine(panel)
			sizer.Add(line, pos=(row+3, 0), span=(1, 6), flag=wx.EXPAND|wx.BOTTOM, border=10)
			
			row += 4
		#
		# Buttons
		#
		
		ok = wx.Button(panel, ID_OBS_INFO_OK, 'Ok', size=(90, 28))
		cancel = wx.Button(panel, ID_OBS_INFO_CANCEL, 'Cancel', size=(90, 28))
		sizer.Add(ok, pos=(row+0, 4))
		sizer.Add(cancel, pos=(row+0, 5), flag=wx.RIGHT|wx.BOTTOM, border=5)
		
		panel.SetupScrolling(scroll_x=True, scroll_y=True) 
		panel.SetSizer(sizer)
		panel.Fit()
		
		#
		# Save the various widgets for access later
		#
		
		self.mrpASP = mrpComboASP
		self.mrpDP = mrpComboDP
		self.mrpDR = mrpComboDR
		self.mrpSHL = mrpComboSHL
		self.mrpMCS = mrpComboMCS
		self.mupASP = mupComboASP
		self.mupDP = mupComboDP
		self.mupDR = mupComboDR
		self.mupSHL = mupComboSHL
		self.mupMCS = mupComboMCS
		self.schLog = schLog
		self.exeLog = exeLog
		self.incSMIB = incSMIB
		self.incDESG = incDESG
		
		if self.parent.mode == 'TBW' or (self.parent.mode == 'TBN' and ALLOW_TBW_TBN_SAME_SDF):
			self.tbwBits = tbitsText
			self.tbwSamp = tsampText
		
		if self.parent.mode == 'TBN' or (self.parent.mode == 'TBW' and ALLOW_TBW_TBN_SAME_SDF):
			self.tbnGain = tgainText
		
		if self.parent.mode == 'DRX':
			self.drxGain = dgainText
			self.drxBeam = dbeamText

		self.aspFlt = aspComboFlt
		self.aspAT1 = aspComboAT1
		self.aspAT2 = aspComboAT2
		self.aspATS = aspComboATS
		
		if self.parent.project.sessions[0].dataReturnMethod == 'DR Spectrometer' or (self.parent.project.sessions[0].spcSetup[0] != 0 and self.parent.project.sessions[0].spcSetup[1] != 0):
			self.opt1 = opt1
			self.opt2 = opt2
			self.opt3 = opt3
			self.opt4 = opt4
			self.opt5 = opt5
			self.opt6 = opt6
			
	def initEvents(self):
		self.Bind(wx.EVT_BUTTON, self.onOK, id=ID_OBS_INFO_OK)
		self.Bind(wx.EVT_BUTTON, self.onCancel, id=ID_OBS_INFO_CANCEL)
	
	def onOK(self, event):
		"""
		Save everything into all of the correct places.
		"""

		if self.parent.mode == 'TBW' or (self.parent.mode == 'TBN' and ALLOW_TBW_TBN_SAME_SDF):
			tbwBits = int( self.tbwBits.GetValue().split('-')[0] )
			tbwSamp = int( self.tbwSamp.GetValue() )
			if tbwSamp < 0:
				self.displayError('Number of TBW samples must be positive', title='TBW Sample Error')
				return False

			if tbwBits == 4  and tbwSamp > 36000000:
				self.displayError('Number of TBW samples too large for a %i-bit capture' % tbwBits, 
							details='%i > 36000000' % tbwSamp, title='TBW Sample Error')
				return False

			if tbwBits == 12 and tbwSamp > 12000000:
				self.displayError('Number of TBW samples too large for a %i-bit capture' % tbwBits, 
							details='%i > 12000000' % tbwSamp, title='TBW Sample Error')
				return False
		
		self.parent.project.sessions[0].recordMIB['ASP'] = self.__parseTimeCombo(self.mrpASP)
		self.parent.project.sessions[0].recordMIB['DP_'] = self.__parseTimeCombo(self.mrpDP)
		for i in range(1,6):
			self.parent.project.sessions[0].recordMIB['DR%i' % i] = self.__parseTimeCombo(self.mrpDR)
		self.parent.project.sessions[0].recordMIB['SHL'] = self.__parseTimeCombo(self.mrpSHL)
		self.parent.project.sessions[0].recordMIB['MCS'] = self.__parseTimeCombo(self.mrpMCS)
			
		self.parent.project.sessions[0].updateMIB['ASP'] = self.__parseTimeCombo(self.mupASP)
		self.parent.project.sessions[0].updateMIB['DP_'] = self.__parseTimeCombo(self.mupDP)
		for i in range(1,6):
			self.parent.project.sessions[0].recordMIB['DR%i' % i] = self.__parseTimeCombo(self.mupDR)
		self.parent.project.sessions[0].updateMIB['SHL'] = self.__parseTimeCombo(self.mupSHL)
		self.parent.project.sessions[0].updateMIB['MCS'] = self.__parseTimeCombo(self.mupMCS)
		
		self.parent.project.sessions[0].logScheduler = self.schLog.GetValue()
		self.parent.project.sessions[0].logExecutive = self.exeLog.GetValue()
		
		self.parent.project.sessions[0].includeStationStatic = self.incSMIB.GetValue()
		self.parent.project.sessions[0].includeDesign = self.incDESG.GetValue()
		
		aspFltDict = {'MCS Decides': -1, 'Split': 0, 'Full': 1, 'Reduced': 2, 'Off': 3}
		aspFlt = aspFltDict[self.aspFlt.GetValue()]
		aspAT1 = -1 if self.aspAT1.GetValue() == 'MCS Decides' else int(self.aspAT1.GetValue())
		aspAT2 = -1 if self.aspAT2.GetValue() == 'MCS Decides' else int(self.aspAT2.GetValue())
		aspATS = -1 if self.aspATS.GetValue() == 'MCS Decides' else int(self.aspATS.GetValue())
		for i in xrange(len(self.parent.project.sessions[0].aspFlt)):
			self.parent.project.sessions[0].aspFlt[i] = aspFlt
			self.parent.project.sessions[0].aspAT1[i] = aspAT1
			self.parent.project.sessions[0].aspAT2[i] = aspAT2
			self.parent.project.sessions[0].aspATS[i] = aspATS

		if self.parent.mode == 'TBW' or (self.parent.mode == 'TBN' and ALLOW_TBW_TBN_SAME_SDF):
			self.parent.project.sessions[0].tbwBits = int( self.tbwBits.GetValue().split('-')[0] )
			self.parent.project.sessions[0].tbwSamples = int( self.tbwSamp.GetValue() )
			
		if self.parent.mode == 'TBN' or (self.parent.mode == 'TBW' and ALLOW_TBW_TBN_SAME_SDF):
			self.parent.project.sessions[0].tbnGain = self.__parseGainCombo(self.tbnGain)
			
		if self.parent.mode == 'DRX':
			self.parent.project.sessions[0].drxGain = self.__parseGainCombo(self.drxGain)
			self.parent.project.sessions[0].drxBeam = self.__parseGainCombo(self.drxBeam)
		
		for obs in self.parent.project.sessions[0].observations:
			if obs.mode == 'TBW' or (self.parent.mode == 'TBN' and ALLOW_TBW_TBN_SAME_SDF):
				obs.bits = self.parent.project.sessions[0].tbwBits
				obs.samples = self.parent.project.sessions[0].tbwSamples
			elif obs.mode == 'TBN' or (self.parent.mode == 'TBW' and ALLOW_TBW_TBN_SAME_SDF):
				obs.gain = self.parent.project.sessions[0].tbnGain
			else:
				obs.gain = self.parent.project.sessions[0].drxGain
				
		if self.parent.project.sessions[0].dataReturnMethod == 'DR Spectrometer' or (self.parent.project.sessions[0].spcSetup[0] != 0 and self.parent.project.sessions[0].spcSetup[1] != 0):
			mt = self.parent.project.sessions[0].spcMetatag
			if mt is None:
				isLinear = True
			else:
				junk, mt = mt.split('=', 1)
				mt = mt.replace('}', '')
				
				if mt in ('XX', 'YY', 'XY', 'YX', 'XXYY', 'XXXYYXYY'):
					isLinear = True
				else:
					isLinear = False
					
			if isLinear:
				if self.opt1.GetValue():
					self.parent.project.sessions[0].spcMetatag = '{Stokes=XX}'
				elif self.opt2.GetValue():
					self.parent.project.sessions[0].spcMetatag = '{Stokes=XY}'
				elif self.opt3.GetValue():
					self.parent.project.sessions[0].spcMetatag = '{Stokes=YX}'
				elif self.opt4.GetValue():
					self.parent.project.sessions[0].spcMetatag = '{Stokes=YY}'
				elif self.opt5.GetValue():
					self.parent.project.sessions[0].spcMetatag = '{Stokes=XXYY}'
				else:
					self.parent.project.sessions[0].spcMetatag = '{Stokes=XXXYYXYY}'
			else:
				if self.opt1.GetValue():
					self.parent.project.sessions[0].spcMetatag = '{Stokes=I}'
				elif self.opt2.GetValue():
					self.parent.project.sessions[0].spcMetatag = '{Stokes=Q}'
				elif self.opt3.GetValue():
					self.parent.project.sessions[0].spcMetatag = '{Stokes=U}'
				elif self.opt4.GetValue():
					self.parent.project.sessions[0].spcMetatag = '{Stokes=V}'
				elif self.opt5.GetValue():
					self.parent.project.sessions[0].spcMetatag = '{Stokes=IV}'
				else:
					self.parent.project.sessions[0].spcMetatag = '{Stokes=IQUV}'
		
		self.parent.edited = True
		self.parent.setSaveButton()

		self.Close()
	
	def onCancel(self, event):
		self.Close()
		
	def __parseTimeCombo(self, cb):
		"""
		Given a combo box that represents some times, parse it and return
		the time in minutes.
		"""
		
		if cb.GetValue() == 'MCS Decides':
			out = -1
		elif cb.GetValue() == 'Never':
			out = 0
		else:
			t, u = cb.GetValue().split(None, 1)
			if u.find('minute') >= 0:
				out = int(t)
			else:
				out = int(t)*60
				
		return out
		
	def __parseGainCombo(self, cb):
		"""
		Given a combo box that represents some times, parse it and return
		the time in minutes.
		"""
		
		if cb.GetValue() == 'MCS Decides':
			out = -1
		else:
			out = int(cb.GetValue())
			
		return out
		
	def __timeToCombo(self, time, options=[1, 5, 15, 30, 60]):
		"""
		Convert a time onto the rigid system imposed by the GUI.
		"""
		
		if time == -1:
			return "MCS Decides"
		elif time == 0:
			return "Never"
		else:
			if time <= 1:
				return "1 minute"
			elif time <= 5:
				return "5 minutes"
			elif time <= 15:
				return "15 minutes"
			elif time <= 30:
				return "30 minutes"
			else:
				return "1 hour"

	def displayError(self, error, details=None, title=None):
		"""
		Display an error dialog and write an error message to the command 
		line if requested.
		"""
		if title is None:
			title = 'An Error has Occured'
		

		if details is None:
			print "[%i] Error: %s" % (os.getpid(), str(error))
			dialog = wx.MessageDialog(self, '%s' % str(error), title, style=wx.OK|wx.ICON_ERROR)
		else:
			print "[%i] Error: %s" % (os.getpid(), str(details))
			dialog = wx.MessageDialog(self, '%s\n\nDetails:\n%s' % (str(error), str(details)), title, style=wx.OK|wx.ICON_ERROR)

		dialog.ShowModal()


class SessionDisplay(wx.Frame):
	"""
	Window for displaying the "Session at a Glance".
	"""
	
	def __init__(self, parent):
		wx.Frame.__init__(self, parent, title='Session at a Glance', size=(800, 375))
		
		self.parent = parent
		
		self.initUI()
		self.initEvents()
		self.Show()
		
		if self.parent.mode == 'DRX':
			self.initPlotDRX()
		else:
			self.initPlot()
		
	def initUI(self):
		"""
		Start the user interface.
		"""
		
		self.statusbar = self.CreateStatusBar()
		
		hbox = wx.BoxSizer(wx.HORIZONTAL)
		
		# Add plots to panel 1
		panel1 = wx.Panel(self, -1)
		vbox1 = wx.BoxSizer(wx.VERTICAL)
		self.figure = Figure()
		self.canvas = FigureCanvasWxAgg(panel1, -1, self.figure)
		self.toolbar = NavigationToolbar2WxAgg(self.canvas)
		self.toolbar.Realize()
		vbox1.Add(self.canvas,  1, wx.EXPAND)
		vbox1.Add(self.toolbar, 0, wx.LEFT | wx.FIXED_MINSIZE)
		panel1.SetSizer(vbox1)
		hbox.Add(panel1, 1, wx.EXPAND)
		
		# Use some sizers to see layout options
		self.SetSizer(hbox)
		self.SetAutoLayout(1)
		hbox.Fit(self)
		
	def initEvents(self):
		"""
		Set all of the various events in the data range window.
		"""
		
		# Make the images resizable
		self.Bind(wx.EVT_PAINT, self.resizePlots)
	
	def initPlot(self):
		"""
		Populate the figure/canvas areas with a plot.  We only need to do this
		once for this type of window.
		"""
		
		self.obs = self.parent.project.sessions[0].observations

		if len(self.obs) == 0:
			return False
		
		mode = self.obs[0].mode
		colors = ['blue', 'green', 'red', 'cyan', 'magenta', 'yellow', 'orange', 'lavender']
		
		## Find the earliest observation
		self.earliest = conflict.unravelObs(self.obs)[0][0]
		yls = [0]*len(self.obs)
		tkr = NullLocator()
		
		self.figure.clf()
		self.ax1 = self.figure.gca()
		
		## The actual observations
		i = 0
		for yl,o in zip(yls, self.obs):
			start = o.mjd + o.mpm/1000.0 / (3600.0*24.0)
			dur = o.dur/1000.0 / (3600.0*24.0)
			
			self.ax1.barh(yl, dur, height=1.0, left=start-self.earliest, alpha=0.6, color=colors[i % len(colors)], 
						label='Observation %i' % (i+1))
			self.ax1.annotate('%i' % (i+1), (start-self.earliest+dur/2, yl+0.5))
			i += 1
		
		## Second set of x axes
		self.ax1.xaxis.tick_bottom()
		self.ax2 = self.figure.add_axes(self.ax1.get_position(), sharey=self.ax1, frameon=False)
		self.ax2.xaxis.tick_top()
		self.ax2.set_xlim([self.ax1.get_xlim()[0]*24.0, self.ax1.get_xlim()[1]*24.0])
		
		## Labels
		self.ax1.set_xlabel('MJD-%i [days]' % self.earliest)
		self.ax1.set_ylabel('Observation')
		self.ax2.set_xlabel('Session Elapsed Time [hours]')
		self.ax2.xaxis.set_label_position('top')
		self.ax1.yaxis.set_major_formatter( NullFormatter() )
		self.ax2.yaxis.set_major_formatter( NullFormatter() )
		if tkr is not None:
			for ax in [self.ax1.yaxis, self.ax2.yaxis]:
				ax.set_major_locator( tkr )
				ax.set_minor_locator( tkr )
				
		## Draw
		self.canvas.draw()
		self.connect()
		
	def initPlotDRX(self):
		"""
		Test function to plot source elevation for the observations.
		"""
		
		self.obs = self.parent.project.sessions[0].observations

		if len(self.obs) == 0:
			return False
		
		## Find the earliest observation
		self.earliest = conflict.unravelObs(self.obs)[0][0]
		
		self.figure.clf()
		self.ax1 = self.figure.gca()
		
		## The actual observations
		observer = lwa1.getObserver()
		
		i = 0
		for o in self.obs:
			t = []
			el = []
			
			if o.mode not in ('TBW', 'TBN', 'STEPPED'):
				## Get the source
				src = o.getFixedBody()
				
				dt = 0.0
				stepSize = o.dur / 1000.0 / 300
				if stepSize < 30.0:
					stepSize = 30.0
				
				## Find its elevation over the course of the observation
				while dt < o.dur/1000.0:
					observer.date = o.mjd + (o.mpm/1000.0 + dt)/3600/24.0 + MJD_OFFSET - DJD_OFFSET
					src.compute(observer)
				
					el.append( float(src.alt) * 180.0 / math.pi )
					t.append( o.mjd + (o.mpm/1000.0 + dt) / (3600.0*24.0) - self.earliest )
				
					dt += stepSize
					
				## Make sure we get the end of the observation
				dt = o.dur/1000.0
				observer.date = o.mjd + (o.mpm/1000.0 + dt)/3600/24.0 + MJD_OFFSET - DJD_OFFSET
				src.compute(observer)
				
				el.append( float(src.alt) * 180.0 / math.pi )
				t.append( o.mjd + (o.mpm/1000.0 + dt) / (3600.0*24.0) - self.earliest )
				
				## Plot the elevation over time
				self.ax1.plot(t, el, label='%s' % o.target)
				
				## Draw the observation limits
				self.ax1.vlines(o.mjd + o.mpm/1000.0 / (3600.0*24.0) - self.earliest, 0, 90, linestyle=':')
				self.ax1.vlines(o.mjd + (o.mpm/1000.0 + o.dur/1000.0) / (3600.0*24.0) - self.earliest, 0, 90, linestyle=':')
				
				i += 1
				
			elif o.mode == 'STEPPED':
				t0 = o.mjd + (o.mpm/1000.0) / (3600.0*24.0)
				
				for s in o.steps:
					## Get the source
					src = s.getFixedBody()
					
					## Figure out if we have RA/Dec or az/alt
					if src is not None:
						observer.date = t0 + MJD_OFFSET - DJD_OFFSET
						src.compute(observer)
						alt = float(src.alt) * 180.0 / math.pi
					else:
						alt = s.c2
						
					el.append( alt )
					t.append( t0 - self.earliest)
					t0 += (s.dur/1000.0) / (3600.0*24.0)
					el.append( alt )
					t.append( t0 - self.earliest )
					
				## Plot the elevation over time
				self.ax1.plot(t, el, label='%s' % o.target)
				
				## Draw the observation limits
				self.ax1.vlines(o.mjd + o.mpm/1000.0 / (3600.0*24.0) - self.earliest, 0, 90, linestyle=':')
				self.ax1.vlines(o.mjd + (o.mpm/1000.0 + o.dur/1000.0) / (3600.0*24.0) - self.earliest, 0, 90, linestyle=':')
				
				i += 1
				
			else:
				pass
			
		## Add a legend
		handles, labels = self.ax1.get_legend_handles_labels()
		self.ax1.legend(handles[:i], labels[:i], loc=0)
			
		## Second set of x axes
		self.ax1.xaxis.tick_bottom()
		self.ax1.set_ylim([0, 90])
		self.ax2 = self.figure.add_axes(self.ax1.get_position(), sharey=self.ax1, frameon=False)
		self.ax2.xaxis.tick_top()
		self.ax2.set_xlim([self.ax1.get_xlim()[0]*24.0, self.ax1.get_xlim()[1]*24.0])
			
		## Labels
		self.ax1.set_xlabel('MJD-%i [days]' % self.earliest)
		self.ax1.set_ylabel('Elevation [deg.]')
		self.ax2.set_xlabel('Session Elapsed Time [hours]')
		self.ax2.xaxis.set_label_position('top')
		
		## Draw
		self.canvas.draw()
		self.connect()
	
	def connect(self):
		"""
		Connect to all the events we need to interact with the plots.
		"""
		
		self.cidmotion  = self.figure.canvas.mpl_connect('motion_notify_event', self.on_motion)
	
	def on_motion(self, event):
		"""
		Deal with motion events in the stand field window.  This involves 
		setting the status bar with the current x and y coordinates as well
		as the stand number of the selected stand (if any).
		"""
		
		if event.inaxes:
			clickX = event.xdata
			clickY = event.ydata
			
			# It looks like the events are coming from the second set of axes, 
			# which are in hours, not days.  First, compute MJD and MPM of the
			# current mouse location
			t = clickX/24.0 + self.earliest
			mjd = int(t)
			mpm = int( (t - mjd)*24.0*3600.0*1000.0 )
			
			# Compute the session elapsed time
			elapsed = clickX*3600.0
			eHour = elapsed / 3600
			eMinute = (elapsed % 3600) / 60
			eSecond = (elapsed % 3600) % 60
			
			elapsed = "%02i:%02i:%06.3f" % (eHour, eMinute, eSecond)
			
			self.statusbar.SetStatusText("MJD: %i  MPM: %i;  Session Elapsed Time: %s" % (mjd, mpm, elapsed))
		else:
			self.statusbar.SetStatusText("")
	
	def disconnect(self):
		"""
		Disconnect all the stored connection ids.
		"""
		
		self.figure.canvas.mpl_disconnect(self.cidmotion)
		
	def onCancel(self, event):
		self.Close()
		
	def resizePlots(self, event):
		w, h = self.GetSize()
		dpi = self.figure.get_dpi()
		newW = 1.0*w/dpi
		newH1 = 1.0*(h/2-100)/dpi
		newH2 = 1.0*(h/2-75)/dpi
		self.figure.set_size_inches((newW, newH1))
		self.figure.canvas.draw()

	def GetToolBar(self):
		# You will need to override GetToolBar if you are using an 
		# unmanaged toolbar in your frame
		return self.toolbar


ID_VOL_INFO_OK = 511

class VolumeInfo(wx.Frame):
	def __init__ (self, parent):
		nObs = len(parent.project.sessions[0].observations)		
		wx.Frame.__init__(self, parent, title='Estimated Data Volume', size=(400, nObs*20+120))
		
		self.parent = parent
		
		self.initUI()
		self.initEvents()
		x,y = self.GetBestSize()
		self.SetSize((x,y))
		self.Show()
		
	def initUI(self):
		row = 0
		panel = wx.Panel(self)
		sizer = wx.GridBagSizer(5, 5)
		
		font = wx.SystemSettings_GetFont(wx.SYS_SYSTEM_FONT)
		font.SetPointSize(font.GetPointSize()+2)
		
		dataText = wx.StaticText(panel, label='Estimated Data Volume:')
		dataText.SetFont(font)
		sizer.Add(dataText, pos=(row+0, 0), span=(1, 3), flag=wx.ALIGN_CENTER, border=5)
	
		line = wx.StaticLine(panel)
		sizer.Add(line, pos=(row+1, 0), span=(1, 3), flag=wx.EXPAND|wx.BOTTOM, border=10)
		
		row += 2

		observationCount = 1
		totalData = 0
		for obs in self.parent.project.sessions[0].observations:
			if self.parent.project.sessions[0].spcSetup[0] != 0 and self.parent.project.sessions[0].spcSetup[1] != 0:
				mt = self.parent.project.sessions[0].spcMetatag
				if mt is None:
					mt = '{Stokes=XXYY}'
				junk, mt = mt.split('=', 1)
				mt = mt.replace('}', '')
				
				if mt in ('XX', 'YY', 'XY', 'YX', 'XXYY', 'XXXYYXYY'):
					products = len(mt)/2
				else:
					products = len(mt)
					
				mode = "%s+%s" % (obs.mode, mt)
				
				tunes = 2
				tlen, icount = self.parent.project.sessions[0].spcSetup
				sampleRate = obs.filterCodes[obs.filter]
				duration = obs.getDuration() / 1000.0
				dataVolume = (76 + tlen*tunes*products*4) / (1.0*tlen*icount/sampleRate) * duration
			else:
				mode = obs.mode
				
				dataVolume = obs.dataVolume
			
			idText = wx.StaticText(panel, label='Observation #%i' % observationCount)
			tpText = wx.StaticText(panel, label=mode)
			dvText = wx.StaticText(panel, label='%.2f GB' % (dataVolume/1024.0**3,))
			
			sizer.Add(idText, pos=(row+0, 0), flag=wx.ALIGN_LEFT, border=5)
			sizer.Add(tpText, pos=(row+0, 1), flag=wx.ALIGN_CENTER, border=5)
			sizer.Add(dvText, pos=(row+0, 2), flag=wx.ALIGN_RIGHT, border=5)
			
			observationCount += 1
			totalData += dataVolume
			row += 1
			
		line = wx.StaticLine(panel)
		sizer.Add(line, pos=(row+0, 0), span=(1, 3), flag=wx.EXPAND|wx.BOTTOM, border=10)

		row += 1

		ttText = wx.StaticText(panel, label='Total:')
		ttText.SetFont(font)
		dvText = wx.StaticText(panel, label='%.2f GB' % (totalData/1024.0**3,))
		dvText.SetFont(font)
		
		sizer.Add(ttText, pos=(row+0, 0), flag=wx.ALIGN_LEFT, border=5)
		sizer.Add(dvText, pos=(row+0, 2), flag=wx.ALIGN_RIGHT, border=5)

		row += 1

		ok = wx.Button(panel, ID_VOL_INFO_OK, 'Ok', size=(90, 28))
		sizer.Add(ok, pos=(row+0, 2))
		
		panel.SetSizer(sizer)
		panel.Fit()
		
	def initEvents(self):
		self.Bind(wx.EVT_BUTTON, self.onOk, id=ID_VOL_INFO_OK)
		
	def onOk(self, event):
		self.Close()


ID_RESOLVE_RESOLVE = 611
ID_RESOLVE_APPLY = 612
ID_RESOLVE_CANCEL = 613

class ResolveTarget(wx.Frame):
	def __init__ (self, parent):	
		wx.Frame.__init__(self, parent, title='Resolve Target', size=(475, 200))
		
		self.parent = parent
		
		self.setSource()
		if self.source == 'Invalid Mode':
			wx.MessageBox('All-sky modes (TBW and TBN) are not directed at a particular target.', 'All-Sky Mode')
		else:
			self.initUI()
			self.initEvents()
			x,y = self.GetBestSize()
			self.SetSize((x,y))
			self.Show()

	def setSource(self):
		if self.parent.mode.upper() == 'DRX':
			for i in range(self.parent.listControl.GetItemCount()):
				if self.parent.listControl.IsChecked(i):
					item = self.parent.listControl.GetItem(i, 2)
					self.observationID = i
					self.source = item.GetText()
					return True

			self.observationID = -1
			self.source = ''
			return False
			
		else:
			self.observationID = -1
			self.source = 'Invalid Mode'
			return False
		
	def initUI(self):
		row = 0
		panel = wx.Panel(self)
		sizer = wx.GridBagSizer(5, 5)
		
		src = wx.StaticText(panel, label='Target Name:')
		srcText = wx.TextCtrl(panel)
		srcText.SetValue(self.source)
		sizer.Add(src, pos=(row+0, 0), span=(1, 1), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		sizer.Add(srcText, pos=(row+0, 1), span=(1, 4), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
	
		line = wx.StaticLine(panel)
		sizer.Add(line, pos=(row+1, 0), span=(1, 5), flag=wx.EXPAND|wx.BOTTOM, border=10)

		ra = wx.StaticText(panel, label='RA (hours, J2000):')
		raText = wx.TextCtrl(panel, style=wx.TE_READONLY)
		raText.SetValue('---')
		dec = wx.StaticText(panel, label='Dec (degrees, J2000):')
		decText = wx.TextCtrl(panel, style=wx.TE_READONLY)
		decText.SetValue('---')
		srv = wx.StaticText(panel, label='Service Used:')
		srvText = wx.TextCtrl(panel, style=wx.TE_READONLY)
		srvText.SetValue('---')

		sizer.Add(ra, pos=(row+2, 0), span=(1, 1), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		sizer.Add(raText, pos=(row+2, 1), span=(1, 4), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		sizer.Add(dec, pos=(row+3, 0), span=(1, 1), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		sizer.Add(decText, pos=(row+3, 1), span=(1, 4), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		sizer.Add(srv, pos=(row+4, 0), span=(1, 1), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		sizer.Add(srvText, pos=(row+4, 1), span=(1, 4), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
	
		line = wx.StaticLine(panel)
		sizer.Add(line, pos=(row+5, 0), span=(1, 5), flag=wx.EXPAND|wx.BOTTOM, border=10)

		resolve = wx.Button(panel, ID_RESOLVE_RESOLVE, 'Resolve', size=(90, 28))
		appli = wx.Button(panel, ID_RESOLVE_APPLY, 'Apply', size=(90, 28))
		cancel = wx.Button(panel, ID_RESOLVE_CANCEL, 'Cancel', size=(90, 28))
		
		sizer.Add(resolve, pos=(row+6, 2))
		sizer.Add(appli, pos=(row+6, 3))
		sizer.Add(cancel, pos=(row+6, 4))
		
		panel.SetSizerAndFit(sizer)

		self.srcText = srcText
		self.raText = raText
		self.decText = decText
		self.srvText = srvText
		self.appli = appli
		self.appli.Enable(False)

	def initEvents(self):
		self.Bind(wx.EVT_BUTTON, self.onResolve, id=ID_RESOLVE_RESOLVE)
		self.Bind(wx.EVT_BUTTON, self.onApply, id=ID_RESOLVE_APPLY)
		self.Bind(wx.EVT_BUTTON, self.onCancel, id=ID_RESOLVE_CANCEL)

	def onResolve(self, event):
		import urllib

		self.source = self.srcText.GetValue()
		try:
			result = urllib.urlopen('http://www1.cadc-ccda.hia-iha.nrc-cnrc.gc.ca/NameResolver/find?target=%s' % urllib.quote_plus(self.source))
		
			line = result.readlines()
			target = (line[0].replace('\n', '').split('='))[1]
			service = (line[1].replace('\n', '').split('='))[1]
			service = service.replace('(', ' @ ')
			coordsys = (line[2].replace('\n', '').split('='))[1]
			ra = (line[3].replace('\n', '').split('='))[1]
			dec = (line[4].replace('\n', '').split('='))[1]
			
			temp = deg_to_hms(float(ra))
			raS = "%i:%02i:%05.2f" % (temp.hours, temp.minutes, temp.seconds)
			temp = deg_to_dms(float(dec))
			decS = "%+i:%02i:%04.1f" % ((-1.0 if temp.neg else 1.0)*temp.degrees, temp.minutes, temp.seconds)
			
			self.raText.SetValue(raS)
			self.decText.SetValue(decS)
			self.srvText.SetValue(service[0:-2])

			if self.observationID != -1:
				self.appli.Enable(True)

		except IOError:
			self.raText.SetValue("---")
			self.decText.SetValue("---")
			self.srvText.SetValue("Error resolving target")
		
		except ValueError:
			self.raText.SetValue("---")
			self.decText.SetValue("---")
			self.srvText.SetValue("Error resolving target")

	def onApply(self, event):
		if self.observationID == -1:
			return False
		elif self.parent.project.sessions[0].observations[self.observationID].mode != 'TRK_RADEC':
			return False
		else:
			obsIndex = self.observationID
			for obsAttr,widget in [(6,self.raText), (7,self.decText)]:
				try:
					newData = self.parent.coerceMap[obsAttr](widget.GetValue())
			
					oldData = getattr(self.parent.project.sessions[0].observations[obsIndex], self.parent.columnMap[obsAttr])
					if newData != oldData:
						setattr(self.parent.project.sessions[0].observations[obsIndex], self.parent.columnMap[obsAttr], newData)
						self.parent.project.sessions[0].observations[obsIndex].update()
				
						item = self.parent.listControl.GetItem(obsIndex, obsAttr)
						item.SetText(widget.GetValue())
						self.parent.listControl.SetItem(item)
						self.parent.listControl.RefreshItem(item.GetId())

						self.parent.edited = True
						self.parent.setSaveButton()
						self.appli.Enable(False)
				except ValueError as err:
					print '[%i] Error: %s' % (os.getpid(), str(err))

	def onCancel(self, event):
		self.Close()


ID_SCHEDULE_APPLY = 612
ID_SCHEDULE_CANCEL = 613

class ScheduleWindow(wx.Frame):
	def __init__ (self, parent):	
		wx.Frame.__init__(self, parent, title='Session Scheduling', size=(375, 150))
		
		self.parent = parent
		
		self.initUI()
		self.initEvents()
		self.Show()
		
	def initUI(self):
		row = 0
		panel = wx.Panel(self)
		sizer = wx.GridBagSizer(3, 3)
		
		font = wx.SystemSettings_GetFont(wx.SYS_SYSTEM_FONT)
		font.SetPointSize(font.GetPointSize()+2)
		
		src = wx.StaticText(panel, label='Rescheduling Options:')
		src.SetFont(font)
		sizer.Add(src, pos=(row+0, 0), span=(1, 3), flag=wx.ALIGN_CENTER, border=5)
		row += 1
		
		sidereal = wx.RadioButton(panel, -1, 'Sidereal time fixed, date changable')
		solar = wx.RadioButton(panel, -1, 'UTC time fixed, date changable')
		fixed = wx.RadioButton(panel, -1, 'Use only specfied date/time')
		
		if self.parent.project.sessions[0].comments.find('ScheduleSolarMovable') != -1:
			sidereal.SetValue(False)
			solar.SetValue(True)
			fixed.SetValue(False)
		elif self.parent.project.sessions[0].comments.find('ScheduleFixed') != -1:
			sidereal.SetValue(Flase)
			solar.SetValue(False)
			fixed.SetValue(True)
		else:
			sidereal.SetValue(True)
			solar.SetValue(False)
			fixed.SetValue(False)
		
		sizer.Add(sidereal, pos=(row+0, 0))
		sizer.Add(solar, pos=(row+1, 0))
		sizer.Add(fixed, pos=(row+2, 0))
		row += 3
		
		line = wx.StaticLine(panel)
		sizer.Add(line, pos=(row+0, 0), span=(1, 3), flag=wx.EXPAND|wx.BOTTOM, border=10)
		row += 1
		
		appli = wx.Button(panel, ID_SCHEDULE_APPLY, 'Apply', size=(90, 28))
		cancel = wx.Button(panel, ID_SCHEDULE_CANCEL, 'Cancel', size=(90, 28))
		
		sizer.Add(appli, pos=(row+0, 0))
		sizer.Add(cancel, pos=(row+0, 1))
		
		panel.SetSizerAndFit(sizer)
		
		self.sidereal = sidereal
		self.solar = solar
		self.fixed = fixed
		
	def initEvents(self):
		self.Bind(wx.EVT_BUTTON, self.onApply, id=ID_SCHEDULE_APPLY)
		self.Bind(wx.EVT_BUTTON, self.onCancel, id=ID_SCHEDULE_CANCEL)
		
	def onApply(self, event):
		oldComments = self.parent.project.sessions[0].comments
		oldComments = oldComments.replace('ScheduleSiderealMovable', '')
		oldComments = oldComments.replace('ScheduleSolarMovable', '')
		oldComments = oldComments.replace('SchedulFixed', '')
		
		if self.sidereal.GetValue():
			oldComments += 'ScheduleSiderealMovable'
		elif self.solar.GetValue():
			oldComments += 'ScheduleSolarMovable'
		elif self.fixed.GetValue():
			oldComments += 'ScheduleFixed'
		else:
			pass
		
		self.parent.project.sessions[0].comments = oldComments
		
		self.parent.edited = True
		self.parent.setSaveButton()
		
		self.Close()

	def onCancel(self, event):
		self.Close()


class HtmlWindow(wx.html.HtmlWindow): 
	def __init__(self, parent): 
		wx.html.HtmlWindow.__init__(self, parent, style=wx.NO_FULL_REPAINT_ON_RESIZE|wx.SUNKEN_BORDER) 
		
		if "gtk2" in wx.PlatformInfo: 
			self.SetStandardFonts()
		
	def OnLinkClicked(self, link): 
		a = link.GetHref()
		if a.startswith('#'): 
			wx.html.HtmlWindow.OnLinkClicked(self, link) 
		else: 
			wx.LaunchDefaultBrowser(link.GetHref())


class HelpWindow(wx.Frame):
	def __init__(self, parent):
		wx.Frame.__init__(self, parent, -1, 'Session GUI Handbook', size=(570, 400))

		self.initUI()
		self.Show()

	def initUI(self):
		panel = wx.Panel(self, -1, style=wx.BORDER_SUNKEN)
		vbox = wx.BoxSizer(wx.VERTICAL)
		
		help = HtmlWindow(panel)
		help.LoadPage('docs/help.html')
		vbox.Add(help, 1, wx.EXPAND)
		
		self.CreateStatusBar()

		panel.SetSizer(vbox)


ID_STEPPED_DONE = 701
ID_STEPPED_ADD_SINGLE_STEP = 711
ID_STEPPED_REMOVE = 712
ID_STEPPED_CUT = 713
ID_STEPPED_COPY = 714
ID_STEPPED_PASTE_BEFORE = 715
ID_STEPPED_PASTE_AFTER = 716
ID_STEPPED_PASTE_END = 717
ID_STEPPED_LISTCTRL = 721

class SteppedWindow(wx.Frame):
	def __init__ (self, parent, obsID):
		self.parent = parent
		self.obsID = obsID
		self.obs = self.parent.project.sessions[0].observations[self.obsID]
		self.RADec = self.obs.RADec
		
		title = '%s Stepped Observation #%i' % ("RA/Dec" if self.RADec else "Az/Alt", obsID+1)
		wx.Frame.__init__(self, parent, title=title, size=(375, 350))
		
		self.editmenu = {}
		self.buffer = None
		
		self.initUI()
		self.initEvents()
		self.Show()
		
		self.loadSteps()
		
	def initUI(self):
		stepType = "RA/Dec" if self.RADec else "Az/Alt"
		
		# Menubar
		menubar = wx.MenuBar()
		
		editMenu = wx.Menu()
		stpMenu = wx.Menu()
		
		cut = wx.MenuItem(editMenu, ID_STEPPED_CUT, 'C&ut Selected Observation')
		editMenu.AppendItem(cut)
		cpy = wx.MenuItem(editMenu, ID_STEPPED_COPY, '&Copy Selected Observation')
		editMenu.AppendItem(cpy)
		pstb = wx.MenuItem(editMenu, ID_STEPPED_PASTE_BEFORE, '&Paste Before Selected')
		editMenu.AppendItem(pstb)
		psta = wx.MenuItem(editMenu, ID_STEPPED_PASTE_AFTER, '&Paste After Selected')
		editMenu.AppendItem(psta)
		pste = wx.MenuItem(editMenu, ID_STEPPED_PASTE_END, '&Paste at End of List')
		editMenu.AppendItem(pste)
		
		# Save menu items and disable all of them
		self.editmenu['cut'] = cut
		self.editmenu['copy'] = cpy
		self.editmenu['pasteBefore'] = pstb
		self.editmenu['pasteAfter'] = psta
		self.editmenu['pasteEnd'] = pste
		for k in self.editmenu.keys():
			self.editmenu[k].Enable(False)
		
		# Steps Menu
		addStep = wx.MenuItem(stpMenu, ID_STEPPED_ADD_SINGLE_STEP, 'Add a Step')
		stpMenu.AppendItem(addStep)
		remove = wx.MenuItem(stpMenu, ID_STEPPED_REMOVE, '&Remove Selected Step(s)')
		stpMenu.AppendItem(remove)
		stpMenu.AppendSeparator()
		done = wx.MenuItem(stpMenu, ID_STEPPED_DONE, 'Done')
		stpMenu.AppendItem(done)
		
		menubar.Append(editMenu, '&Edit')
		menubar.Append(stpMenu, '&Steps')
		self.SetMenuBar(menubar)
		
		# Toolbar
		self.toolbar = self.CreateToolBar()
		self.toolbar.AddLabelTool(ID_STEPPED_DONE, 'step', wx.Bitmap(os.path.join(self.parent.scriptPath, 'icons', 'stepped-done.png')), shortHelp='Finish making changes', longHelp='Finish making changes to the steps and close the window')
		self.toolbar.AddSeparator()
		self.toolbar.AddLabelTool(ID_STEPPED_ADD_SINGLE_STEP,  'step', wx.Bitmap(os.path.join(self.parent.scriptPath, 'icons', 'stepped-add.png')), shortHelp='Add %s Step' % stepType, 
								longHelp='Add a new %s step with custom position and frequency stepping to to Stepped Observation #%i' % (stepType, self.obsID))
		self.toolbar.AddLabelTool(ID_STEPPED_REMOVE, '', wx.Bitmap(os.path.join(self.parent.scriptPath, 'icons', 'remove.png')), shortHelp='Remove Selected', 
								longHelp='Remove the selected step from the step list for Stepped Observation #%i' % self.obsID)
		self.toolbar.Realize()
		
		# Status bar
		self.statusbar = self.CreateStatusBar()
		
		# Observation list
		hbox = wx.BoxSizer(wx.HORIZONTAL)
		panel = wx.Panel(self, -1)
		
		self.listControl = SteppedListCtrl(panel, id=ID_STEPPED_LISTCTRL)
		self.listControl.parent = self
		
		hbox.Add(self.listControl, 1, wx.EXPAND)
		panel.SetSizer(hbox)
		
	def initEvents(self):
		# Edit events
		self.Bind(wx.EVT_MENU, self.onCut, id=ID_STEPPED_CUT)
		self.Bind(wx.EVT_MENU, self.onCopy, id=ID_STEPPED_COPY)
		self.Bind(wx.EVT_MENU, self.onPasteBefore, id=ID_STEPPED_PASTE_BEFORE)
		self.Bind(wx.EVT_MENU, self.onPasteAfter, id=ID_STEPPED_PASTE_AFTER)
		self.Bind(wx.EVT_MENU, self.onPasteEnd, id=ID_STEPPED_PASTE_END)
		
		# Toolbar events
		self.Bind(wx.EVT_MENU, self.onQuit, id=ID_STEPPED_DONE)
		self.Bind(wx.EVT_MENU, self.onAddStep, id=ID_STEPPED_ADD_SINGLE_STEP)
		self.Bind(wx.EVT_MENU, self.onRemove, id=ID_STEPPED_REMOVE)
		
		# Step edits
		self.Bind(wx.EVT_LIST_END_LABEL_EDIT, self.onEdit, id=ID_STEPPED_LISTCTRL)
		
		# Window manager close
		self.Bind(wx.EVT_CLOSE, self.onQuit)
	
	def onCopy(self, event):
		"""
		Copy the selected step(s) to the buffer.
		"""
		
		self.buffer = []
		for i in xrange(self.listControl.GetItemCount()):
			if self.listControl.IsChecked(i):
				self.buffer.append( copy.deepcopy(self.obs.steps[i]) )
				
		self.editmenu['pasteBefore'].Enable(True)
		self.editmenu['pasteAfter'].Enable(True)
		self.editmenu['pasteEnd'].Enable(True)
	
	def onCut(self, event):
		self.onCopy(event)
		self.onRemove(event)
		
	def onPasteBefore(self, event):
		firstChecked = None
		
		for i in xrange(self.listControl.GetItemCount()):
			if self.listControl.IsChecked(i):
				firstChecked = i
				break
				
		if firstChecked is not None:
			id = firstChecked
			
			for stp in self.buffer[::-1]:
				cStp = copy.deepcopy(stp)
				
				self.obs.steps.insert(id, cStp)
				self.addStep(self.obs.steps[id], id)
				
			self.parent.edited = True
			self.parent.setSaveButton()
			
		# Re-number the remaining rows to keep the display clean
		for i in xrange(self.listControl.GetItemCount()):
			item = self.listControl.GetItem(i, 0)
			item.SetText('%i' % (i+1))
			self.listControl.SetItem(item)
			self.listControl.RefreshItem(item.GetId())
			
	def onPasteAfter(self, event):
		lastChecked = None
		
		for i in xrange(self.listControl.GetItemCount()):
			if self.listControl.IsChecked(i):
				lastChecked = i
				
		if lastChecked is not None:
			id = lastChecked + 1
			
			for stp in self.buffer[::-1]:
				cStp = copy.deepcopy(stp)
				
				self.obs.steps.insert(id, cStp)
				self.addStep(self.obs.steps[id], id)
				
			self.parent.edited = True
			self.parent.setSaveButton()
			
		# Re-number the remaining rows to keep the display clean
		for i in xrange(self.listControl.GetItemCount()):
			item = self.listControl.GetItem(i, 0)
			item.SetText('%i' % (i+1))
			self.listControl.SetItem(item)
			self.listControl.RefreshItem(item.GetId())
	
	def onPasteEnd(self, event):
		"""
		Paste the selected observation(s) at the end of the current session.
		"""
		
		if self.buffer is not None:
			for stp in self.buffer:
				id = self.listControl.GetItemCount() + 1
				
				cStp = copy.deepcopy(stp)
				
				self.obs.steps(cStp)
				self.addStep(self.obs.steps[-1], id)
				
			self.parent.edited = True
			self.parent.setSaveButton()
	
	def onAddStep(self, event):
		"""
		Add a new step.
		"""
		
		id = self.listControl.GetItemCount() + 1
		self.obs.steps.append( sdf.BeamStep(0.0, 0.0, '00:00:00.000', 38e6, 74e6, RADec=self.RADec) )
		self.addStep(self.obs.steps[-1], id)
	
	def onEdit(self, event):
		"""
		Make the selected change to the underlying observation.
		"""
		
		obsIndex = event.GetIndex()
		obsAttr = event.GetColumn()
		self.SetStatusText('')
		try:
			newData = self.coerceMap[obsAttr](event.GetText())
			
			oldData = getattr(self.obs.steps[obsIndex], self.columnMap[obsAttr])
			setattr(self.obs.steps[obsIndex], self.columnMap[obsAttr], newData)
			self.obs.steps[obsIndex].update()
			self.obs.update()
			
			# If the duration has changed, update the main window
			if self.columnMap[obsAttr] == 'duration':
				item = self.parent.listControl.GetItem(self.obsID, 5)
				item.SetText(self.obs.duration)
				self.parent.listControl.SetItem(item)
			
			item = self.listControl.GetItem(obsIndex, obsAttr)
			if self.listControl.GetItemTextColour(item.GetId()) != (0, 0, 0, 255):
				self.listControl.SetItemTextColour(item.GetId(), wx.BLACK)
				self.listControl.RefreshItem(item.GetId())
				
			self.parent.edited = True
			self.parent.setSaveButton()
			
			self.badEdit = False
			self.badEditLocation = (-1, -1)
		except ValueError as err:
			print '[%i] Error: %s' % (os.getpid(), str(err))
			self.SetStatusText('Error: %s' % str(err))
			
			item = self.listControl.GetItem(obsIndex, obsAttr)
			self.listControl.SetItemTextColour(item.GetId(), wx.RED)
			self.listControl.RefreshItem(item.GetId())

			self.badEdit = True
			self.badEditLocation = (obsIndex, obsAttr)
	
	def onRemove(self, event):
		"""
		Remove selected observations from the main window as well as the 
		self.project.sessions[0].observations list.
		"""
		
		def stillBad(lc):
			"""
			Function to recur throught the rows and check to see if any still 
			need to be removed.  Returns the index+1 of the next element to be
			removed.
			
			Why index+1?  Well... because 0 is interperated as False and 1+ as
			True.  Thus if any one row is bad, value corresponding to boolean
			True is returned.
			"""
			
			for i in xrange(lc.GetItemCount()):
				if lc.IsChecked(i):
					return i+1
			return 0

		# While there is still at least one bad row, continue looping and removing
		# rows
		bad = stillBad(self.listControl)
		while bad:
			i = bad - 1
			self.listControl.DeleteItem(i)
			del self.obs.steps[i]
			bad = stillBad(self.listControl)
		self.listControl.setCheckDependant()
		
		# Re-number the remaining rows to keep the display clean
		for i in xrange(self.listControl.GetItemCount()):
			item = self.listControl.GetItem(i, 0)
			item.SetText('%i' % (i+1))
			self.listControl.SetItem(item)
			self.listControl.RefreshItem(item.GetId())
			
	def onQuit(self, event):
		"""
		Exit out of the step editer.
		"""
		
		self.Destroy()
		
	def addColumns(self):
		"""
		Add the various columns to the main window based on the type of 
		observations being defined.
		"""

		def raConv(text):
			"""
			Special conversion function for deal with RA values.
			"""
			
			fields = text.split(':')
			fields = [float(f) for f in fields]
			sign = 1
			if fields[0] < 0:
				sign = -1
			fields[0] = abs(fields[0])
			
			value = 0
			for f,d in zip(fields, [1.0, 60.0, 3600.0]):
				value += (f / d)
			value *= sign
			
			if value <= 0 or value >= 24:
				raise ValueError("RA value must be 0 < RA < 24")
			else:
				return value

		def decConv(text):
			"""
			Special conversion function for dealing with dec. values.
			"""
			
			fields = text.split(':')
			fields = [float(f) for f in fields]
			sign = 1
			if fields[0] < 0:
				sign = -1
			fields[0] = abs(fields[0])
			
			value = 0
			for f,d in zip(fields, [1.0, 60.0, 3600.0]):
				value += (f / d)
			value *= sign
			
			if value < -90 or value > 90:
				raise ValueError("Dec values must be -90 <= dec <= 90")
			else:
				return value

		def azConv(text):
			"""
			Special conversion functio for azimuth values.
			"""
			
			fields = text.split(':')
			fields = [float(f) for f in fields]
			sign = 1
			if fields[0] < 0:
				sign = -1
			fields[0] = abs(fields[0])
			
			value = 0
			for f,d in zip(fields, [1.0, 60.0, 3600.0]):
				value += (f / d)
			value *= sign
			
			if value < 0 or value > 360:
				raise ValueError("Azimuth values must be 0 <= dec <= 360")
			else:
				return value
				
		def altConv(text):
			"""
			Special conversion functio for altitude/elevation values.
			"""
			
			fields = text.split(':')
			fields = [float(f) for f in fields]
			sign = 1
			if fields[0] < 0:
				sign = -1
			fields[0] = abs(fields[0])
			
			value = 0
			for f,d in zip(fields, [1.0, 60.0, 3600.0]):
				value += (f / d)
			value *= sign
			
			if value < 0 or value > 90:
				raise ValueError("Elevation values must be 0 <= dec <= 90")
			else:
				return value

		def freqConv(text):
			"""
			Special conversion function for dealing with frequencies.
			"""

			value = float(text)*1e6
			freq = int(round(value * 2**32 / fS))
			if freq < 219130984 or freq > 1928352663:
				raise ValueError("Frequency of %.6f MHz is out of the DP tuning range" % (value/1e6,))
			else:
				return value
		
		def snrConv(text):
			"""
			Special conversion function for dealing with the MaxSNR keyword input.
			"""
			
			text = text.lower().capitalize()
			if text == 'True' or text == 'Yes':
				return True
			elif text == 'False' or text == 'No':
				return False
			else:
				raise ValueError("Unknown boolean conversion of '%s'" % text)
		
		width = 50 + 125
		self.columnMap = []
		self.coerceMap = []
		
		self.listControl.InsertColumn(0, 'ID', width=50)
		self.listControl.InsertColumn(1, 'Duration', width=125)
		self.columnMap.append('id')
		self.columnMap.append('duration')
		self.coerceMap.append(str)
		self.coerceMap.append(str)
		
		width += 125 + 150 + 150 + 125 + 125 + 125
		if self.RADec:
			self.listControl.InsertColumn(2, 'RA (Hour J2000)', width=150)
			self.listControl.InsertColumn(3, 'Dec (Deg. J2000)', width=150)
			self.columnMap.append('c1')
			self.columnMap.append('c2')
			self.coerceMap.append(raConv)
			self.coerceMap.append(decConv)
		else:
			self.listControl.InsertColumn(2, 'Azimuth (Deg.)', width=150)
			self.listControl.InsertColumn(3, 'Elevation (Deg.)', width=150)
			self.columnMap.append('c1')
			self.columnMap.append('c2')
			self.coerceMap.append(azConv)
			self.coerceMap.append(altConv)
		self.listControl.InsertColumn(4, 'Tuning 1 (MHz)', width=125)
		self.listControl.InsertColumn(5, 'Tuning 2 (MHz)', width=125)
		self.listControl.InsertColumn(6, 'Max S/N Beam?', width=125)
		self.columnMap.append('frequency1')
		self.columnMap.append('frequency2')
		self.columnMap.append('MaxSNR')
		self.coerceMap.append(freqConv)
		self.coerceMap.append(freqConv)
		self.coerceMap.append(snrConv)
		
		size = self.listControl.GetSize()
		size[0] = width
		self.listControl.SetMinSize(size)
		self.listControl.Fit()
		size = self.GetSize()
		size[0] = width
		self.SetMinSize(size)
		self.Fit()
		
	def addStep(self, step, id):
		"""
		Add a step to the currently selected observation
		
		.. note::
			This only updates the list visible on the screen, not the SD list
			stored in self.obs
		"""
		
		listIndex = id
		
		index = self.listControl.InsertStringItem(listIndex, str(id))
		def dec2sexstr(value, signed=True):
			sign = 1
			if value < 0:
				sign = -1
			value = abs(value)
			
			d = sign*int(value)
			m = int(value*60) % 60
			s = float(value*3600) % 60
			
			if signed:
				return '%+03i:%02i:%04.1f' % (d, m, s)
			else:
				return '%02i:%02i:%05.2f' % (d, m, s)
				
		self.listControl.SetStringItem(index, 1, step.duration)
		self.listControl.SetStringItem(index, 4, "%.6f" % (step.freq1*fS/2**32 / 1e6))
		self.listControl.SetStringItem(index, 5, "%.6f" % (step.freq2*fS/2**32 / 1e6))
		if step.MaxSNR:
			self.listControl.SetStringItem(index, 6, "Yes")
		else:
			self.listControl.SetStringItem(index, 6, "No")
			
		self.listControl.SetStringItem(index, 2, dec2sexstr(step.c1, signed=False))
		self.listControl.SetStringItem(index, 3, dec2sexstr(step.c2, signed=True))
		
	def loadSteps(self):
		"""
		Read in the steps currenlty defined as part of the observation.
		"""
		
		self.listControl.DeleteAllItems()
		self.listControl.DeleteAllColumns()
		self.addColumns()
		
		for i, step in enumerate(self.obs.steps):
			self.addStep(step, i+1)


if __name__ == "__main__":
	config = parseOptions(sys.argv[1:])
	
	app = wx.App()
	SDFCreator(None, title='Session Definition File', config=config)
	app.MainLoop()
