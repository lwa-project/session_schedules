#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import sys
import sdf

from lsl.common.dp import fS
from lsl.reader.tbn import filterCodes as TBNFilters
from lsl.reader.drx import filterCodes as DRXFilters

import wx
import wx.html as html
from wx.lib.mixins.listctrl import TextEditMixin, CheckListCtrlMixin


__version__ = "0.1"
__revision__ = "$ Revision: 12 $"
__author__ = "Jayce Dowell"


class ObservationListCtrl(wx.ListCtrl, TextEditMixin, CheckListCtrlMixin):
	"""
	Class that combines an editable list with check boxes.
	"""
	
	def __init__(self, parent, **kwargs):
		wx.ListCtrl.__init__(self, parent, style=wx.LC_REPORT, **kwargs)
		TextEditMixin.__init__(self)
		CheckListCtrlMixin.__init__(self)

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
ID_ADD_TBW = 22
ID_ADD_TBN = 23
ID_ADD_DRX_RADEC = 24
ID_ADD_DRX_SOLAR = 25
ID_ADD_DRX_JOVIAN = 26
ID_ADD_STEPPED = 27
ID_REMOVE = 28
ID_VALIDATE = 29
ID_TIMESERIES = 30
ID_RESOLVE = 31
ID_ADVANCED = 32

ID_DATA_VOLUME = 41

ID_HELP = 51
ID_FILTER_INFO = 52
ID_ABOUT = 53

ID_LISTCTRL = 61

class SDFCreator(wx.Frame):
	def __init__(self, parent, title, args=[]):
		wx.Frame.__init__(self, parent, title=title, size=(750,500))
		
		self.dirname = ''
		self.toolbar = None
		self.statusbar = None
		self.savemenu = None
		self.obsmenu = {}
		
		self.initSDF()
		
		self.initUI()
		self.initEvents()
		self.CreateStatusBar()
		self.Show()
		
		if len(args) > 0:
			self.filename = args[0]
			self.parseFile(self.filename)
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
		
		# Observer menu items
		info = wx.MenuItem(obsMenu, ID_INFO, 'Observer/&Project Info.')
		obsMenu.AppendItem(info)
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
		addStepped = wx.MenuItem(add, ID_ADD_STEPPED, 'DRX - Ste&pped')
		add.AppendItem(addStepped)
		obsMenu.AppendMenu(-1, '&Add', add)
		remove = wx.MenuItem(obsMenu, ID_REMOVE, '&Remove Selected')
		obsMenu.AppendItem(remove)
		validate = wx.MenuItem(obsMenu, ID_VALIDATE, '&Validate All')
		obsMenu.AppendItem(validate)
		obsMenu.AppendSeparator()
		resolve = wx.MenuItem(obsMenu, ID_RESOLVE, 'Resolve Selected')
		obsMenu.AppendItem(resolve)
		timeseries = wx.MenuItem(obsMenu, ID_TIMESERIES, 'Session at a &Glance')
		obsMenu.AppendItem(timeseries)
		advanced = wx.MenuItem(obsMenu, ID_ADVANCED, 'Advanced &Settings')
		obsMenu.AppendItem(advanced)
		
		# Save menu items and disable stepped observations (for now)
		self.obsmenu['tbn'] = addTBN
		self.obsmenu['tbw'] = addTBW
		self.obsmenu['drx-radec'] = addDRXR
		self.obsmenu['drx-solar'] = addDRXS
		self.obsmenu['drx-jovian'] = addDRXJ
		self.obsmenu['stepped'] = addStepped
		addStepped.Enable(False)
		
		# Data menu items
		volume = wx.MenuItem(obsMenu, ID_DATA_VOLUME, '&Estimated Data Volume')
		dataMenu.AppendItem(volume)
		
		# Help menu items
		help = wx.MenuItem(helpMenu, ID_HELP, 'Session GUI Handbook')
		helpMenu.AppendItem(help)
		finfo = wx.MenuItem(helpMenu, ID_FILTER_INFO, '&Filter Codes')
		helpMenu.AppendItem(finfo)
		helpMenu.AppendSeparator()
		about = wx.MenuItem(helpMenu, ID_ABOUT, '&About')
		helpMenu.AppendItem(about)
		
		menubar.Append(fileMenu, '&File')
		menubar.Append(obsMenu,  '&Observations')
		menubar.Append(dataMenu, '&Data')
		menubar.Append(helpMenu, '&Help')
		self.SetMenuBar(menubar)
		
		# Toolbar
		self.toolbar = self.CreateToolBar()
		self.toolbar.AddLabelTool(ID_NEW, '', wx.Bitmap('icons/new.png'), shortHelp='New', 
								longHelp='Clear the existing setup and start a new project/session')
		self.toolbar.AddLabelTool(ID_OPEN, '', wx.Bitmap('icons/open.png'), shortHelp='Open', 
								longHelp='Open and load an existing SD file')
		self.toolbar.AddLabelTool(ID_SAVE, '', wx.Bitmap('icons/save.png'), shortHelp='Save', 
								longHelp='Save the current setup')
		self.toolbar.AddLabelTool(ID_SAVE_AS, '', wx.Bitmap('icons/save-as.png'), shortHelp='Save as', 
								longHelp='Save the current setup to a new SD file')
		self.toolbar.AddLabelTool(ID_QUIT, '', wx.Bitmap('icons/exit.png'), shortHelp='Quit', 
								longHelp='Quit (without saving)')
		self.toolbar.AddSeparator()
		self.toolbar.AddLabelTool(ID_ADD_TBW, 'tbw', wx.Bitmap('icons/tbw.png'), shortHelp='Add TBW', 
								longHelp='Add a new all-sky TBW observation to the list')
		self.toolbar.AddLabelTool(ID_ADD_TBN, 'tbn', wx.Bitmap('icons/tbn.png'), shortHelp='Add TBN', 
								longHelp='Add a new all-sky TBN observation to the list')
		self.toolbar.AddLabelTool(ID_ADD_DRX_RADEC,  'drx-radec',  wx.Bitmap('icons/drx-radec.png'),  shortHelp='Add DRX - RA/Dec', 
								longHelp='Add a new beam forming DRX observation that tracks the sky (ra/dec)')
		self.toolbar.AddLabelTool(ID_ADD_DRX_SOLAR,  'drx-solar',  wx.Bitmap('icons/drx-solar.png'),  shortHelp='Add DRX - Solar', 
								longHelp='Add a new beam forming DRX observation that tracks the Sun')
		self.toolbar.AddLabelTool(ID_ADD_DRX_JOVIAN, 'drx-jovian', wx.Bitmap('icons/drx-jovian.png'), shortHelp='Add DRX - Jovian', 
								longHelp='Add a new beam forming DRX observation that tracks Jupiter')
		self.toolbar.AddLabelTool(ID_ADD_STEPPED,  'stepped', wx.Bitmap('icons/stepped.png'), shortHelp='Add DRX - Stepped', 
								longHelp='Add a new beam forming DRX observation with custom position and frequency stepping')
		self.toolbar.AddLabelTool(ID_REMOVE, '', wx.Bitmap('icons/remove.png'), shortHelp='Remove Selected', 
								longHelp='Remove the selected observations from the list')
		self.toolbar.AddLabelTool(ID_VALIDATE, '', wx.Bitmap('icons/validate.png'), shortHelp='Validate Observations', 
								longHelp='Validate the current set of parameters and observations')
		self.toolbar.AddSeparator()
		self.toolbar.AddLabelTool(ID_HELP, '', wx.Bitmap('icons/help.png'), shortHelp='Help', 
								longHelp='Display a brief help message for this program')
		self.toolbar.Realize()
		
		# Disable stepped observations (for now)
		self.toolbar.EnableTool(ID_ADD_STEPPED, False)
		
		# Status bar
		self.statusbar = self.CreateStatusBar()
		
		# Observation list
		hbox = wx.BoxSizer(wx.HORIZONTAL)
		panel = wx.Panel(self, -1)
		
		self.listControl = ObservationListCtrl(panel, id=ID_LISTCTRL)
		
		hbox.Add(self.listControl, 1, wx.EXPAND)
		panel.SetSizer(hbox)
	
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
		
		# Observer menu events
		self.Bind(wx.EVT_MENU, self.onInfo, id=ID_INFO)
		self.Bind(wx.EVT_MENU, self.onAddTBW, id=ID_ADD_TBW)
		self.Bind(wx.EVT_MENU, self.onAddTBN, id=ID_ADD_TBN)
		self.Bind(wx.EVT_MENU, self.onAddDRXR, id=ID_ADD_DRX_RADEC)
		self.Bind(wx.EVT_MENU, self.onAddDRXS, id=ID_ADD_DRX_SOLAR)
		self.Bind(wx.EVT_MENU, self.onAddDRXJ, id=ID_ADD_DRX_JOVIAN)
		self.Bind(wx.EVT_MENU, self.onAddStepped, id=ID_ADD_STEPPED)
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
		
		# Keypress bindings
		self.Bind(wx.EVT_KEY_DOWN, self.onKeyPressed)
	
	def onNew(self, event):
		"""
		Create a new SD session.
		"""
		
		self.edited = True
		self.setSaveButton()
		
		self.setMenuButtons('None')
		self.listControl.DeleteAllItems()
		self.listControl.DeleteAllColumns()
		self.initSDF()
		ObserverInfo(self)
	
	def onLoad(self, event):
		"""
		Load an existing SD file.
		"""
		
		dialog = wx.FileDialog(self, "Select a SD File", self.dirname, '', 'Text Files (*.txt)|*.txt|All Files (*.*)|*.*', wx.OPEN)
		
		if dialog.ShowModal() == wx.ID_OK:
			self.dirname = dialog.GetDirectory()
			self.filename = dialog.GetPath()
			self.parseFile(dialog.GetPath())
			
			self.edited = False
			self.setSaveButton()
			
		dialog.Destroy()

	def onSave(self, event):
		"""
		Save the current observation to a file.
		"""
		
		if self.filename == '':
			self.onSaveAs(event)
		else:
			self.onValidate(1, confirmValid=False)
			
			if not self.project.validate():
				wx.MessageBox('The session definition file could not be saved due to errors in the file.', 'Save Failed')
			else:
				fh = open(self.filename, 'w')
				fh.write(self.project.render())
				fh.close()
				
				self.edited = False
				self.setSaveButton()

	def onSaveAs(self, event):
		"""
		Save the current observation to a new SD file.
		"""
		
		self.onValidate(1, confirmValid=False)
		
		if not self.project.validate():
			wx.MessageBox('The session definition file could not be saved due to errors in the file.', 'Save Failed')
		else:
			dialog = wx.FileDialog(self, "Select Output File", self.dirname, '', 'Text Files (*.txt)|*.txt|All Files (*.*)|*.*', wx.SAVE|wx.FD_OVERWRITE_PROMPT)
			
			if dialog.ShowModal() == wx.ID_OK:
				self.dirname = dialog.GetDirectory()
				
				fh = open(dialog.GetPath(), 'w')
				fh.write(self.project.render())
				fh.close()
				self.filename = dialog.GetPath()
				
				self.edited = False
				self.setSaveButton()
				
			dialog.Destroy()
	
	def onInfo(self, event):
		"""
		Open up the observer/project information window.
		"""
		
		ObserverInfo(self)
		
	def onAddTBW(self, event):
		"""
		Add a TBW observation to the list and update the main window.
		"""
		
		id = self.listControl.GetItemCount() + 1
		bits = self.project.sessions[0].tbwBits
		samples = self.project.sessions[0].tbwSamples
		self.project.sessions[0].observations.append( sdf.TBW('tbw-%i' % id, 'All-Sky', '2011-01-01 00:00:00.000', samples, bits=bits) )
		self.addObservation(self.project.sessions[0].observations[-1], id)
		
		self.edited = True
		self.setSaveButton()
		
	def onAddTBN(self, event):
		"""
		Add a TBW observation to the list and update the main window.
		"""
		
		id = self.listControl.GetItemCount() + 1
		gain = self.project.sessions[0].tbnGain
		self.project.sessions[0].observations.append( sdf.TBN('tbn-%i' % id, 'All-Sky', '2011-01-01 00:00:00.000', '00:00:00.000', 38e6, 7) )
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
		self.project.sessions[0].observations.append( sdf.DRX('drx-%i' % id, 'target-%i' % id, '2011-01-01 00:00:00.000', '00:00:00.000', 0.0, 0.0, 38e6, 74e6, 7) )
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
		self.project.sessions[0].observations.append( sdf.Solar('solar-%i' % id, 'target-%i' % id, '2011-01-01 00:00:00.000', '00:00:00.000', 38e6, 74e6, 7) )
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
		self.project.sessions[0].observations.append( sdf.Jovian('jovian-%i' % id, 'target-%i' % id, '2011-01-01 00:00:00.000', '00:00:00.000', 38e6, 74e6, 7) )
		self.project.sessions[0].observations[-1].gain = gain
		self.addObservation(self.project.sessions[0].observations[-1], id)
		
		self.edited = True
		self.setSaveButton()
	
	def onAddStepped(self, event):
		"""
		Open up the advanced preferences window.
		"""
		
		pass
	
	def onEdit(self, event):
		"""
		Make the selected change to the underlying observation.
		"""
		
		obsIndex = event.GetIndex()
		obsAttr = event.GetColumn()
		try:
			newData = self.coerceMap[obsAttr](event.GetText())
			
			oldData = getattr(self.project.sessions[0].observations[obsIndex], self.columnMap[obsAttr])
			if newData != oldData:
				setattr(self.project.sessions[0].observations[obsIndex], self.columnMap[obsAttr], newData)
				self.project.sessions[0].observations[obsIndex].update()
			
				self.edited = True
				self.setSaveButton()
		except ValueError:
			pass
	
	def onRemove(self, event):
		"""
		Remove selected observations from the main window as well as the 
		self.project.sessions[0].observations list.
		"""
		
		def stillBad(lc):
			for i in range(lc.GetItemCount()):
				if lc.IsChecked(i):
					return i+1
			return 0

		bad = stillBad(self.listControl)
		while bad:
			i = bad - 1
			self.listControl.DeleteItem(i)
			del self.project.sessions[0].observations[i]
			bad = stillBad(self.listControl)
			
			self.edited = True
			self.setSaveButton()
	
	def onValidate(self, event, confirmValid=True):
		"""
		Validate the current observations.
		"""
		
		# Loop through the lists of observations and validate one-at-a-time so 
		# that we can mark bad observations
		i = 0
		validObs = True
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
		if self.project.validate():
			if confirmValid:
				wx.MessageBox('Congratulations, you have a valid set of observations.', 'Validator Results')
		else:
			if validObs:
				wx.MessageBox('All observations are valid, but there are errors in the session setup.', 'Validator Results')

	def onResolve(self, event):
		"""
		Display a window to resolve a target name to ra/dec coordinates.
		"""

		ResolveTarget(self)
	
	def onTimeseries(self, event):
		"""
		Display a window showing the layout of the observations in time.
		"""
		
		TimeseriesDisplay(self)
	
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
		
		filterInfo = "TBN"
		for tk,tv in TBNFilters.iteritems():
			tv, tu = units(tv)
			filterInfo = "%s\n%i  %.3f %-3s" % (filterInfo, tk, tv, tu)
		filterInfo = "%s\n\nDRX" % filterInfo
		
		for dk,dv in DRXFilters.iteritems():
			dv, du = units(dv)
			filterInfo = "%s\n%i  %.3f %-3s" % (filterInfo, dk, dv, du)
			
		wx.MessageBox(filterInfo, 'Filter Codes')
	
	def onAbout(self, event):
		"""
		Display a ver very very bried 'about' window.
		"""
		
		dialog = wx.AboutDialogInfo()
		
		dialog.SetIcon(wx.Icon('icons/lwa.png', wx.BITMAP_TYPE_PNG))
		dialog.SetName('Session GUI')
		dialog.SetVersion(__version__)
		dialog.SetDescription("""GUI for creating session definition files to define observations with the Long Wavelength Array.""")
		dialog.SetWebSite('http://lwa.unm.edu')
		dialog.AddDeveloper(__author__)
		
		wx.AboutBox(dialog)
	
	def onQuit(self, event):
		"""
		Quit the main window.
		"""
		
		if self.edited:
			dialog = wx.MessageDialog(self, 'The current session defintion file has changes that have not been saved.\n\nExit anyways?', 'Confirm Quit', style=wx.YES_NO|wx.NO_DEFAULT|wx.ICON_QUESTION)
			
			if dialog.ShowModal() == wx.ID_YES:
				self.Close()
			else:
				pass
		else:
			self.Close()
			
	def onKeyPressed(self, event):
		"""
		Deal with keypressed bindings one at a time.
		"""
		
		keycode = event.GetKeyCode()
		if keycode == wx.WXK_F1:
			HelpWindow(self)
		
	def addColumns(self):
		"""
		Add the various columns to the main window based on the type of 
		observations being defined.
		"""
		
		width = 50 + 100 + 100 + 100 + 225
		self.columnMap = []
		self.coerceMap = []
		
		def float6(s):
			return float(s)*1e6
		
		self.listControl.InsertColumn(0, 'ID', width=50)
		self.listControl.InsertColumn(1, 'Name', width=100)
		self.listControl.InsertColumn(2, 'Target', width=100)
		self.listControl.InsertColumn(3, 'Comments', width=100)
		self.listControl.InsertColumn(4, 'Start (UTC)', width=225)
		self.columnMap.append('id')
		self.columnMap.append('name')
		self.columnMap.append('target')
		self.columnMap.append('comments')
		self.columnMap.append('start')
		for i in xrange(5):
			self.coerceMap.append(str)
		
		if self.mode == 'TBW':
			pass
		elif self.mode == 'TBN':
			width += 125 + 125 + 85
			self.listControl.InsertColumn(5, 'Duration', width=125)
			self.listControl.InsertColumn(6, 'Frequency (MHz)', width=125)
			self.listControl.InsertColumn(7, 'Filter Code', width=85)
			self.columnMap.append('duration')
			self.columnMap.append('frequency1')
			self.columnMap.append('filter')
			self.coerceMap.append(str)
			self.coerceMap.append(float6)
			self.coerceMap.append(int)
		elif self.mode == 'DRX':
			width += 125 + 150 + 150 + 125 + 125 + 85 + 125
			self.listControl.InsertColumn(5, 'Duration', width=125)
			self.listControl.InsertColumn(6, 'RA (Hour J2000)', width=150)
			self.listControl.InsertColumn(7, 'Dec (Deg. J2000)', width=150)
			self.listControl.InsertColumn(8, 'Frequency 1 (MHz)', width=125)
			self.listControl.InsertColumn(9, 'Frequency 2 (MHz)', width=125)
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
			self.coerceMap.append(float)
			self.coerceMap.append(float)
			self.coerceMap.append(float6)
			self.coerceMap.append(float6)
			self.coerceMap.append(int)
			self.coerceMap.append(str)
		else:
			pass
		
		size = self.listControl.GetSize()
		size[0] = width
		self.listControl.SetMinSize(size)
		self.listControl.Fit()
		size = self.GetSize()
		size[0] = width
		self.SetMinSize(size)
		self.Fit()
		
	def addObservation(self, obs, id):
		"""
		Add an observation to a particular location in the observation list
		
		.. note::
			This only updates the list visible on the screen, not the SD list
			stored in self.project
		"""
		
		index = self.listControl.InsertStringItem(sys.maxint, str(id))
		self.listControl.SetStringItem(index, 1, obs.name)
		self.listControl.SetStringItem(index, 2, obs.target)
		if obs.comments is not None:
			self.listControl.SetStringItem(index, 3, obs.comments)
		else:
			self.listControl.SetStringItem(index, 3, 'None provided')
		self.listControl.SetStringItem(index, 4, obs.start)
			
		if self.mode == 'TBN':
			self.listControl.SetStringItem(index, 5, obs.duration)
			self.listControl.SetStringItem(index, 6, "%.6f" % (obs.freq1*fS/2**32 / 1e6))
			self.listControl.SetStringItem(index, 7, "%i" % obs.filter)
		
		if self.mode == 'DRX':
			self.listControl.SetStringItem(index, 5, obs.duration)
			if obs.mode == 'TRK_SOL':
				self.listControl.SetStringItem(index, 6, "Sun")
				self.listControl.SetStringItem(index, 7, "--")
			elif obs.mode == 'TRK_JOV':
				self.listControl.SetStringItem(index, 6, "Jupiter")
				self.listControl.SetStringItem(index, 7, "--")
			else:
				self.listControl.SetStringItem(index, 6, "%.6f" % obs.ra)
				self.listControl.SetStringItem(index, 7, "%+.6f" % obs.dec)
			self.listControl.SetStringItem(index, 8, "%.6f" % (obs.freq1*fS/2**32 / 1e6))
			self.listControl.SetStringItem(index, 9, "%.6f" % (obs.freq2*fS/2**32 / 1e6))
			self.listControl.SetStringItem(index, 10, "%i" % obs.filter)
			self.listControl.SetStringItem(index, 11, "%s" % obs.MaxSNR)
			
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
			self.obsmenu['tbn'].Enable(False)
			self.obsmenu['drx-radec'].Enable(False)
			self.obsmenu['drx-solar'].Enable(False)
			self.obsmenu['drx-jovian'].Enable(False)
			self.obsmenu['stepped'].Enable(False)
			
			self.toolbar.EnableTool(ID_ADD_TBW, True)
			self.toolbar.EnableTool(ID_ADD_TBN, False)
			self.toolbar.EnableTool(ID_ADD_DRX_RADEC,  False)
			self.toolbar.EnableTool(ID_ADD_DRX_SOLAR,  False)
			self.toolbar.EnableTool(ID_ADD_DRX_JOVIAN, False)
		elif mode == 'tbn':
			self.obsmenu['tbw'].Enable(False)
			self.obsmenu['tbn'].Enable(True)
			self.obsmenu['drx-radec'].Enable(False)
			self.obsmenu['drx-solar'].Enable(False)
			self.obsmenu['drx-jovian'].Enable(False)
			self.obsmenu['stepped'].Enable(False)
			
			self.toolbar.EnableTool(ID_ADD_TBW, False)
			self.toolbar.EnableTool(ID_ADD_TBN, True)
			self.toolbar.EnableTool(ID_ADD_DRX_RADEC,  False)
			self.toolbar.EnableTool(ID_ADD_DRX_SOLAR,  False)
			self.toolbar.EnableTool(ID_ADD_DRX_JOVIAN, False)
		elif mode[0:3] == 'trk' or mode[0:3] == 'drx':
			self.obsmenu['tbw'].Enable(False)
			self.obsmenu['tbn'].Enable(False)
			self.obsmenu['drx-radec'].Enable(True)
			self.obsmenu['drx-solar'].Enable(True)
			self.obsmenu['drx-jovian'].Enable(True)
			self.obsmenu['stepped'].Enable(False)
			
			self.toolbar.EnableTool(ID_ADD_TBW, False)
			self.toolbar.EnableTool(ID_ADD_TBN, False)
			self.toolbar.EnableTool(ID_ADD_DRX_RADEC,  True)
			self.toolbar.EnableTool(ID_ADD_DRX_SOLAR,  True)
			self.toolbar.EnableTool(ID_ADD_DRX_JOVIAN, True)
		else:
			self.obsmenu['tbw'].Enable(False)
			self.obsmenu['tbn'].Enable(False)
			self.obsmenu['drx-radec'].Enable(False)
			self.obsmenu['drx-solar'].Enable(False)
			self.obsmenu['drx-jovian'].Enable(False)
			self.obsmenu['stepped'].Enable(False)
			
			self.toolbar.EnableTool(ID_ADD_TBW, False)
			self.toolbar.EnableTool(ID_ADD_TBN, False)
			self.toolbar.EnableTool(ID_ADD_DRX_RADEC,  False)
			self.toolbar.EnableTool(ID_ADD_DRX_SOLAR,  False)
			self.toolbar.EnableTool(ID_ADD_DRX_JOVIAN, False)
	
	def parseFile(self, filename):
		"""
		Given a filename, parse the file using the sdf.parse() method and 
		update all of the various aspects of the GUI (observation list, mode, 
		button, menu items, etc.).
		"""
		
		self.listControl.DeleteAllItems()
		self.listControl.DeleteAllColumns()
		self.initSDF()
		
		print "Parsing file '%s'" % filename
		fh = open(filename, 'r')
		self.project = sdf.parse(fh)
		self.setMenuButtons(self.project.sessions[0].observations[0].mode)
		if self.project.sessions[0].observations[0].mode == 'TBW':
			self.mode = 'TBW'
		elif self.project.sessions[0].observations[0].mode == 'TBN':
			self.mode = 'TBN'
		elif self.project.sessions[0].observations[0].mode[0:3] == 'TRK':
			self.mode = 'DRX'
		else:
			pass
		fh.close()
		
		try:
			self.project.sessions[0].tbwBits = self.project.sessions[0].observations[0].bits
			self.project.sessions[0].tbwSamples = self.project.sessions[0].observations[0].samples
		except:
			pass
		self.project.sessions[0].tbnGain = self.project.sessions[0].observations[0].gain
		self.project.sessions[0].drxGain = self.project.sessions[0].observations[0].gain
		
		self.addColumns()
		id = 1
		for obs in self.project.sessions[0].observations:
			self.addObservation(obs, id)
			id += 1


ID_OBS_INFO_OK = 211
ID_OBS_INFO_CANCEL = 212

class ObserverInfo(wx.Frame):
	"""
	Class to hold information about the observer (name, ID), the current project 
	(title, ID), and what type of session this will be (TBW, TBN, etc.).
	"""
	
	def __init__(self, parent):
		wx.Frame.__init__(self, parent, title='Observer Information', size=(750,675))
		
		self.parent = parent
		
		self.initUI()
		self.initEvents()
		self.Show()
		
	def initUI(self):
		row = 0
		panel = wx.Panel(self)
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
		
		sizer.Add(obs, pos=(row+0,0), span=(1,5), flag=wx.ALIGN_CENTER, border=5)
		
		sizer.Add(oid, pos=(row+1, 0), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		sizer.Add(oidText, pos=(row+1, 1), span=(1, 4), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		
		sizer.Add(fname, pos=(row+2, 0), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		sizer.Add(fnameText, pos=(row+2, 1), span=(1, 4), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		sizer.Add(lname, pos=(row+3, 0), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		sizer.Add(lnameText, pos=(row+3, 1), span=(1, 4), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		
		line = wx.StaticLine(panel)
		sizer.Add(line, pos=(row+4, 0), span=(1, 5), flag=wx.EXPAND|wx.BOTTOM, border=10)
		
		row += 5
		
		#
		# Project Info
		#
		
		prj = wx.StaticText(panel, label='Project Information')
		prj.SetFont(font)
		
		pid = wx.StaticText(panel, label='ID Number')
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
			pcomsText.SetValue(self.parent.project.comments)
		
		sizer.Add(prj, pos=(row+0,0), span=(1,5), flag=wx.ALIGN_CENTER, border=5)
		
		sizer.Add(pid, pos=(row+1, 0), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		sizer.Add(pidText, pos=(row+1, 1), span=(1, 4), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		
		sizer.Add(pname, pos=(row+2, 0), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		sizer.Add(pnameText, pos=(row+2, 1), span=(1, 4), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		sizer.Add(pcoms, pos=(row+3, 0), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		sizer.Add(pcomsText, pos=(row+3, 1), span=(4, 4), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		
		line = wx.StaticLine(panel)
		sizer.Add(line, pos=(row+7, 0), span=(1, 5), flag=wx.EXPAND|wx.BOTTOM, border=10)
		
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
			scomsText.SetValue(self.parent.project.sessions[0].comments)
		
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
			drxRB.SetValue(False)
			
		did = wx.StaticText(panel, label='Data Return Method')
		drsuRB = wx.RadioButton(panel, -2, 'DRSU', style=wx.RB_GROUP)
		usbRB = wx.RadioButton(panel, -2, 'USB Harddrive (4 max)')
		redRB = wx.RadioButton(panel, -2, 'Archive (describe reduction in session comments)')
		if self.parent.project.sessions[0].dataReturnMethod == 'DRSU':
			drsuRB.SetValue(True)
			usbRB.SetValue(False)
			redRB.SetValue(False)
		elif self.parent.project.sessions[0].dataReturnMethod == 'USB Harddrives':
			drsuRB.SetValue(False)
			usbRB.SetValue(True)
			redRB.SetValue(False)
		else:
			drsuRB.SetValue(False)
			usbRB.SetValue(False)
			redRB.SetValue(True)
		
		sizer.Add(ses, pos=(row+0, 0), span=(1,5), flag=wx.ALIGN_CENTER, border=5)
		
		sizer.Add(sid, pos=(row+1, 0), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		sizer.Add(sidText, pos=(row+1, 1), span=(1, 4), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		
		sizer.Add(sname, pos=(row+2, 0), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		sizer.Add(snameText, pos=(row+2, 1), span=(1, 4), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		sizer.Add(scoms, pos=(row+3, 0), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		sizer.Add(scomsText, pos=(row+3, 1), span=(4, 4), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		sizer.Add(tid, pos=(row+7,0), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		sizer.Add(tbwRB, pos=(row+7,1), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		sizer.Add(tbnRB, pos=(row+8,1), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		sizer.Add(drxRB, pos=(row+9,1), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		sizer.Add(did, pos=(row+10,0), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		sizer.Add(drsuRB, pos=(row+10,1), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		sizer.Add(usbRB, pos=(row+11,1), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		sizer.Add(redRB, pos=(row+12,1), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		
		line = wx.StaticLine(panel)
		sizer.Add(line, pos=(row+13, 0), span=(1, 5), flag=wx.EXPAND|wx.BOTTOM, border=10)
		
		row += 14
		
		#
		# Buttons
		#
		
		ok = wx.Button(panel, ID_OBS_INFO_OK, 'Ok', size=(90, 28))
		cancel = wx.Button(panel, ID_OBS_INFO_CANCEL, 'Cancel', size=(90, 28))
		sizer.Add(ok, pos=(row+0, 3))
		sizer.Add(cancel, pos=(row+0, 4), flag=wx.RIGHT|wx.BOTTOM, border=5)
		
		sizer.AddGrowableCol(1)
		sizer.AddGrowableRow(8)
		
		panel.SetSizerAndFit(sizer)
		
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
		self.redButton = redRB 
		
	def initEvents(self):
		self.Bind(wx.EVT_BUTTON, self.onOK, id=ID_OBS_INFO_OK)
		self.Bind(wx.EVT_BUTTON, self.onCancel, id=ID_OBS_INFO_CANCEL)
		
	def onOK(self, event):
		"""
		Save everything into all of the correct places.
		"""
		
		self.parent.project.observer.id = int(self.observerIDEntry.GetValue())
		self.parent.project.observer.first = self.observerFirstEntry.GetValue()
		self.parent.project.observer.last = self.observerLastEntry.GetValue()
		self.parent.project.observer.joinName()
		
		self.parent.project.id = self.projectIDEntry.GetValue()
		self.parent.project.name = self.projectTitleEntry.GetValue()
		self.parent.project.comments = self.projectCommentsEntry.GetValue()
		
		self.parent.project.sessions[0].id = self.sessionIDEntry.GetValue()
		self.parent.project.sessions[0].name = self.sessionTitleEntry.GetValue()
		self.parent.project.sessions[0].comments = self.sessionCommentsEntry.GetValue()
		
		if self.drsuButton.GetValue():
			self.parent.project.sessions[0].dataReturnMethod = 'DRSU'
		elif self.usbButton.GetValue():
			self.parent.project.sessions[0].dataReturnMethod = 'USB Harddrives'
		else:
			self.parent.project.sessions[0].dataReturnMethod = 'Reduced per session comments'
		
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
		
		self.Close()
		
	def onCancel(self, event):
		self.Close()


ID_ADV_INFO_OK = 311
ID_ADV_INFO_CANCEL = 312

class AdvancedInfo(wx.Frame):
	def __init__(self, parent):
		wx.Frame.__init__(self, parent, title='Advanced Settings', size=(800, 375))
		
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
		intervals = ['MCS Decides', 'Never', '1 minute', '5 minutes', '15 minutes', '30 minutes', '1 hour']
		
		row = 0
		panel = wx.Panel(self)
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
		mrpComboASP = wx.ComboBox(panel, -1, value='MCS Decides', size=(75, 20), choices=intervals, style=wx.CB_READONLY)
		mrpComboASP.SetStringSelection(self.__timeToCombo(self.parent.project.sessions[0].recordMIB['ASP']))
		mrpComboDP = wx.ComboBox(panel, -1, value='MCS Decides', size=(75, 20), choices=intervals, style=wx.CB_READONLY)
		mrpComboDP.SetStringSelection(self.__timeToCombo(self.parent.project.sessions[0].recordMIB['DP_']))
		mrpComboDR = wx.ComboBox(panel, -1, value='MCS Decides', size=(75, 20), choices=intervals, style=wx.CB_READONLY)
		mrpComboDR.SetStringSelection(self.__timeToCombo(self.parent.project.sessions[0].recordMIB['DR1']))
		mrpComboSHL = wx.ComboBox(panel, -1, value='MCS Decides', size=(75, 20), choices=intervals, style=wx.CB_READONLY)
		mrpComboSHL.SetStringSelection(self.__timeToCombo(self.parent.project.sessions[0].recordMIB['SHL']))
		mrpComboMCS = wx.ComboBox(panel, -1, value='MCS Decides', size=(75, 20), choices=intervals, style=wx.CB_READONLY)
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
		# TBW
		#
		
		if self.parent.mode == 'TBW':
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
		
		if self.parent.mode == 'TBN':
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
			
			sizer.Add(drx, pos=(row+0,0), span=(1,6), flag=wx.ALIGN_CENTER, border=5)
			
			sizer.Add(dgain, pos=(row+1, 0), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
			sizer.Add(dgainText, pos=(row+1, 1), span=(1, 1), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
			
			line = wx.StaticLine(panel)
			sizer.Add(line, pos=(row+2, 0), span=(1, 6), flag=wx.EXPAND|wx.BOTTOM, border=10)
			
			row += 3
		
		#
		# Buttons
		#
		
		ok = wx.Button(panel, ID_OBS_INFO_OK, 'Ok', size=(90, 28))
		cancel = wx.Button(panel, ID_OBS_INFO_CANCEL, 'Cancel', size=(90, 28))
		sizer.Add(ok, pos=(row+0, 4))
		sizer.Add(cancel, pos=(row+0, 5), flag=wx.RIGHT|wx.BOTTOM, border=5)
		
		panel.SetSizerAndFit(sizer)
		
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
		
		if self.parent.mode == 'TBW':
			self.tbwBits = tbitsText
			self.tbwSamp = tsampText
		
		if self.parent.mode == 'TBN':
			self.tbnGain = tgainText
		
		if self.parent.mode == 'DRX':
			self.drxGain = dgainText
		
	def initEvents(self):
		self.Bind(wx.EVT_BUTTON, self.onOK, id=ID_OBS_INFO_OK)
		self.Bind(wx.EVT_BUTTON, self.onCancel, id=ID_OBS_INFO_CANCEL)
	
	def onOK(self, event):
		"""
		Save everything into all of the correct places.
		"""
		
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
		
		if self.parent.mode == 'TBW':
			self.parent.project.sessions[0].tbwBits = int( self.tbwBits.GetValue().split('-')[0] )
			self.parent.project.sessions[0].tbwSamples = int( self.tbwSamp.GetValue() )
			
		if self.parent.mode == 'TBN':
			self.parent.project.sessions[0].tbnGain = self.__parseGainCombo(self.tbnGain)
			
		if self.parent.mode == 'DRX':
			self.parent.project.sessions[0].drxGain = self.__parseGainCombo(self.drxGain)
		
		for obs in self.parent.project.sessions[0].observations:
			if obs.mode == 'TBW':
				obs.bits = self.parent.project.sessions[0].tbwBits
				obs.samples = self.parent.project.sessions[0].tbwSamples
			elif obs.mode == 'TBN':
				obs.gain = self.parent.project.sessions[0].tbnGain
			else:
				obs.gain = self.parent.project.sessions[0].drxGain
		
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
			t, u = cb.GetValue().split(1)
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


class TSPanel(PlotPanel):
	def __init__(self, parent, observations=[], **kwargs):
		self.parent = parent
		self.obs = observations
		
		# initiate plotter
		PlotPanel.__init__( self, parent, **kwargs )
		self.SetColor( (255,255,255) )

	def draw(self):
		"""
		Draw data.
		"""
		
		if not hasattr( self, 'subplot' ):
			self.ax1 = self.figure.add_subplot( 111 )

		if len(self.obs) == 0:
			pass
		else:
			mode = self.obs[0].mode
			colors = ['blue', 'green', 'red', 'cyan', 'magenta']
			i = 0
			earliest = 1e20
			for o in self.obs:
				start = o.mjd + o.mpm/1000.0 / (3600.0*24.0)
				if start < earliest:
					earliest = start
			
			for o in self.obs:
				start = o.mjd + o.mpm/1000.0 / (3600.0*24.0)
				dur = o.dur/1000.0 / (3600.0*24.0)
				
				if mode == 'TBW' or mode == 'TBN':
					yl = 0.5
				else:
					yl = 0.5 + i
				self.ax1.barh(yl, dur, height=1.0, left=start-earliest, alpha=0.6, color=colors[i % len(colors)], label='Observation %i' % (i+1))
				i += 1
			self.ax1.legend(loc=0)
			
			# Second set of x axes
			self.ax1.xaxis.tick_bottom()
			self.ax2 = self.figure.add_axes(self.ax1.get_position(), sharey=self.ax1, frameon=False)
			self.ax2.xaxis.tick_top()
			self.ax2.set_xlim([self.ax1.get_xlim()[0]*24.0, self.ax1.get_xlim()[1]*24.0])
			
			# Labels
			self.ax1.set_xlabel('MJD-%i [days]' % earliest)
			self.ax1.set_ylabel('Observation')
			self.ax2.set_xlabel('Session Elapsed Time [hours]')
			self.ax2.xaxis.set_label_position('top')


class TimeseriesDisplay(wx.Frame):
	def __init__(self, parent):
		wx.Frame.__init__(self, parent, title='Advanced Settings', size=(800, 375))
		
		self.parent = parent
		
		self.initUI()
		self.initEvents()
		self.Show()
		
	def initUI(self):
		row = 0
		panel = wx.Panel(self)
		sizer = wx.GridBagSizer(5, 5)
		
		#
		# Plot
		#
		
		plotPanel = TSPanel(panel, observations=self.parent.project.sessions[0].observations)
		sizer.Add(plotPanel, pos=(0, 0), span=(2, 2), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
			
		line = wx.StaticLine(panel)
		sizer.Add(line, pos=(row+2, 0), span=(1, 2), flag=wx.EXPAND|wx.BOTTOM, border=10)
			
		row += 3
		
		#
		# Buttons
		#
		
		cancel = wx.Button(panel, ID_OBS_INFO_CANCEL, 'Cancel', size=(90, 28))
		sizer.Add(cancel, pos=(row+0, 2), flag=wx.RIGHT|wx.BOTTOM, border=5)
		
		sizer.AddGrowableCol(0)
		sizer.AddGrowableRow(0)
		
		panel.SetSizerAndFit(sizer)
		
	def initEvents(self):
		self.Bind(wx.EVT_BUTTON, self.onCancel, id=ID_OBS_INFO_CANCEL)
		
	def onCancel(self, event):
		self.Close()


ID_VOL_INFO_OK = 511

class VolumeInfo(wx.Frame):
	def __init__ (self, parent):
		nObs = len(parent.project.sessions[0].observations)		
		wx.Frame.__init__(self, parent, title='Estimated Data Volume', size=(300, nObs*20+120))
		
		self.parent = parent
		
		self.initUI()
		self.initEvents()
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
			idText = wx.StaticText(panel, label='Observation #%i' % observationCount)
			tpText = wx.StaticText(panel, label=obs.mode)
			dvText = wx.StaticText(panel, label='%.2f GB' % (obs.dataVolume/1024.0**3,))
			
			sizer.Add(idText, pos=(row+0, 0), flag=wx.ALIGN_LEFT, border=5)
			sizer.Add(tpText, pos=(row+0, 1), flag=wx.ALIGN_CENTER, border=5)
			sizer.Add(dvText, pos=(row+0, 2), flag=wx.ALIGN_RIGHT, border=5)
			
			observationCount += 1
			totalData += obs.dataVolume
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
		
		panel.SetSizerAndFit(sizer)
		
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
		
			self.raText.SetValue("%.6f" % (float(ra)/15.0,))
			self.decText.SetValue("%+.6f" % (float(dec),))
			self.srvText.SetValue(service[0:-2])

			if self.observationID != -1:
				self.appli.Enable(True)

		except IOError:
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
					print newData, oldData, self.parent.columnMap[obsAttr]
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
				except ValueError:
					pass

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


if __name__ == "__main__":
	app = wx.App()
	SDFCreator(None, title='Session Definition File', args=sys.argv[1:])
	app.MainLoop()
