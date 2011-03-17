#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import sys
import sdf

import wx
from wx.lib.mixins.listctrl import TextEditMixin, CheckListCtrlMixin


class ObservationListCtrl(wx.ListCtrl, TextEditMixin, CheckListCtrlMixin):
	def __init__(self, parent):
		wx.ListCtrl.__init__(self, parent, -1, style=wx.LC_REPORT)
		TextEditMixin.__init__(self)
		CheckListCtrlMixin.__init__(self)


ID_NEW = 11
ID_OPEN = 12
ID_SAVE = 13
ID_QUIT = 14

ID_INFO = 21
ID_ADD = 22
ID_REMOVE = 23
ID_VALIDATE = 24
ID_ADVANCED = 25

ID_HELP = 31
ID_ABOUT = 32

class SDFCreator(wx.Frame):
	def __init__(self, parent, title, args=[]):
		wx.Frame.__init__(self, parent, title=title, size=(750,500))
		
		self.dirname = ''
		
		self.initSDF()
		
		self.initUI()
		self.initEvents()
		self.CreateStatusBar()
		self.Show()
		
		if len(args) > 0:
			self.parseFile(args[0])
		
	def initSDF(self):
		# Create empty objects to get things started.  Values will get filled in as they
		# are found in the file
		po = sdf.ProjectOffice()
		observer = sdf.Observer('', 0, first='', last='')
		project = sdf.Project(observer, '', '', projectOffice=po)
		session = sdf.Session('session_name', 0, observations=[])
		project.sessions = [session,]
		
		self.project = project
		self.mode = ''
		
	def initUI(self):
		menubar = wx.MenuBar()
		
		fileMenu = wx.Menu()
		obsMenu = wx.Menu()
		helpMenu = wx.Menu()
		
		# File menu items
		new = wx.MenuItem(fileMenu, ID_NEW, '&New')
		fileMenu.AppendItem(new)
		open = wx.MenuItem(fileMenu, ID_OPEN, '&Open SDF')
		fileMenu.AppendItem(open)
		save = wx.MenuItem(fileMenu, ID_SAVE, '&Save SDF')
		fileMenu.AppendItem(save)
		fileMenu.AppendSeparator()
		quit = wx.MenuItem(fileMenu, ID_QUIT, '&Quit')
		fileMenu.AppendItem(quit)
		
		# Observer menu items
		info = wx.MenuItem(obsMenu, ID_INFO, '&Observer/Project Info.')
		obsMenu.AppendItem(info)
		add = wx.MenuItem(obsMenu, ID_ADD, '&Add')
		obsMenu.AppendItem(add)
		remove = wx.MenuItem(obsMenu, ID_REMOVE, '&Remove Selected')
		obsMenu.AppendItem(remove)
		validate = wx.MenuItem(obsMenu, ID_VALIDATE, '&Validate All')
		obsMenu.AppendItem(validate)
		obsMenu.AppendSeparator()
		advanced = wx.MenuItem(obsMenu, ID_ADVANCED, '&Advanced Settings')
		obsMenu.AppendItem(advanced)
		
		# Help menu items
		about = wx.MenuItem(helpMenu, ID_ABOUT, '&About')
		helpMenu.AppendItem(about)
		
		menubar.Append(fileMenu, '&File')
		menubar.Append(obsMenu, '&Observations')
		menubar.Append(helpMenu, '&Help')
		self.SetMenuBar(menubar)
		
		hbox = wx.BoxSizer(wx.HORIZONTAL)
		panel = wx.Panel(self, -1)
		
		self.listControl = ObservationListCtrl(panel)
		
		hbox.Add(self.listControl, 1, wx.EXPAND)
		panel.SetSizer(hbox)
	
	def initEvents(self):
		# File menu events
		self.Bind(wx.EVT_MENU, self.onNew, id=ID_NEW)
		self.Bind(wx.EVT_MENU, self.onLoad, id=ID_OPEN)
		self.Bind(wx.EVT_MENU, self.onSave, id=ID_SAVE)
		self.Bind(wx.EVT_MENU, self.onQuit, id=ID_QUIT)
		
		# Observer menu events
		self.Bind(wx.EVT_MENU, self.onInfo, id=ID_INFO)
		self.Bind(wx.EVT_MENU, self.onAdd, id=ID_ADD)
		self.Bind(wx.EVT_MENU, self.onRemove, id=ID_REMOVE)
		self.Bind(wx.EVT_MENU, self.onValidate, id=ID_VALIDATE)
		self.Bind(wx.EVT_MENU, self.onAdvanced, id=ID_ADVANCED)
		
		# Help menu events
		self.Bind(wx.EVT_MENU, self.onAbout, id=ID_ABOUT)
	
	def onNew(self, event):
		self.listControl.DeleteAllItems()
		self.listControl.DeleteAllColumns()
		self.initSDF()
		ObserverInfo(self)
	
	def onLoad(self, event):
		dialog = wx.FileDialog(self, "Select a SD File", self.dirname, '', 'Text Files (*.txt)|*.txt|All Files (*.*)|*.*', wx.OPEN)
		
		if dialog.ShowModal() == wx.ID_OK:
			self.dirname = dialog.GetDirectory()
			self.parseFile(dialog.GetPath())
		dialog.Destroy()
		
	def onOK(self, event):
		self.parent.parseFile(self.filenameEntry.GetValue())
		self.Close()

	def onSave(self, event):
		dialog = wx.FileDialog(self, "Select Output File", self.dirname, '', 'Text Files (*.txt)|*.txt|All Files (*.*)|*.*', wx.SAVE)
		
		if dialog.ShowModal() == wx.ID_OK:
			self.dirname = dialog.GetDirectory()
			
			fh = open(dialog.GetPath(), 'w')
			fh.write(self.project.render())
			fh.close()
			
		dialog.Destroy()
	
	def onInfo(self, event):
		ObserverInfo(self)
		
	def onAdd(self, event):
		pass
	
	def onRemove(self, event):
		bad = []
		for i in range(self.listControl.GetItemCount()):
			if self.listControl.IsChecked(i):
				bad.append(i)
				
		for i in bad:
			self.listControl.DeleteItem(i)
			del self.project.sessions[0].observations[i]
	
	def onValidate(self, event):
		pass
	
	def onAdvanced(self, event):
		pass
	
	def onAbout(self, event):
		wx.MessageBox('GUI interface for session definition file creation.', 'About')
	
	def onQuit(self, event):
		self.Close()
		
	def addColumns(self):
		self.listControl.InsertColumn(0, 'ID', width=50)
		self.listControl.InsertColumn(1, 'Name', width=100)
		self.listControl.InsertColumn(2, 'Target', width=100)
		self.listControl.InsertColumn(3, 'Comments', width=100)
		self.listControl.InsertColumn(4, 'Start (UTC)', width=250)
		
		if self.mode == 'TBW':
			pass
		elif self.mode == 'TBN':
			self.listControl.InsertColumn(5, 'Duration', width=150)
			self.listControl.InsertColumn(6, 'Frequency (MHz)', width=150)
			self.listControl.InsertColumn(7, 'Filter Code', width=100)
		elif self.mode == 'DRX':
			self.listControl.InsertColumn(5, 'Duration', width=150)
			self.listControl.InsertColumn(6, 'RA (Hour J2000)', width=150)
			self.listControl.InsertColumn(7, 'Dec (Deg J2000)', width=150)
			self.listControl.InsertColumn(8, 'Frequency 1 (MHz)', width=150)
			self.listControl.InsertColumn(9, 'Frequency 2 (MHz)', width=150)
			self.listControl.InsertColumn(10, 'Filter Code', width=100)
		else:
			pass
		
	def parseFile(self, filename):
		self.listControl.DeleteAllItems()
		self.listControl.DeleteAllColumns()
		self.initSDF()
		
		print "Parsing file '%s'" % filename
		fh = open(filename, 'r')
		self.project = sdf.parse(fh)
		if self.project.sessions[0].observations[0].mode == 'TBW':
			self.mode = 'TBW'
		elif self.project.sessions[0].observations[0].mode == 'TBN':
			self.mode = 'TBN'
		else:
			self.mode = 'DRX'
		fh.close()
		
		self.addColumns()
		id = 1
		for o in self.project.sessions[0].observations:
			index = self.listControl.InsertStringItem(sys.maxint, str(id))
			self.listControl.SetStringItem(index, 1, o.name)
			self.listControl.SetStringItem(index, 2, o.target)
			self.listControl.SetStringItem(index, 3, o.comments)
			self.listControl.SetStringItem(index, 4, o.start)
			
			if self.mode == 'TBN':
				self.listControl.SetStringItem(index, 5, o.duration)
				self.listControl.SetStringItem(index, 6, "%.6f" % (o.freq1 / 1e6))
				self.listControl.SetStringItem(index, 7, "%i" % o.filter)
			
			if self.mode == 'DRX':
				self.listControl.SetStringItem(index, 5, o.duration)
				self.listControl.SetStringItem(index, 6, "%.6f" % o.ra)
				self.listControl.SetStringItem(index, 7, "%+.6f" % o.dec)
				self.listControl.SetStringItem(index, 8, "%.6f" % (o.freq1 / 1e6))
				self.listControl.SetStringItem(index, 9, "%.6f" % (o.freq2 / 1e6))
				self.listControl.SetStringItem(index, 10, "%i" % o.filter)
		
			id += 1


ID_OBS_INFO_OK = 211
ID_OBS_INFO_CANCEL = 212

class ObserverInfo(wx.Frame):
	def __init__(self, parent):
		wx.Frame.__init__(self, parent, title='Observer Info.', size=(550,450))
		
		self.parent = parent
		self.observerIDEntry = None
		self.observerFirstEntry = None
		self.observerLastEntry = None
		self.projectIDEntry = None
		self.projectTitleEntry = None
		self.projectCommentsEntry = None
		self.tbwButton = None
		self.tbnButton = None
		self.drxButton = None
		
		self.initUI()
		self.initEvents()
		self.Show()
		
	def initUI(self):
		panel = wx.Panel(self)
		sizer = wx.GridBagSizer(5, 5)
		
		font = wx.SystemSettings_GetFont(wx.SYS_SYSTEM_FONT)
		font.SetPointSize(font.GetPointSize()+2)
		
		obs = wx.StaticText(panel, label='Observer Information')
		obs.SetFont(font)
		
		sid = wx.StaticText(panel, label='ID Number')
		fname = wx.StaticText(panel, label='First Name')
		lname = wx.StaticText(panel, label='Last Name')
		
		sidText = wx.TextCtrl(panel)
		fnameText = wx.TextCtrl(panel)
		lnameText = wx.TextCtrl(panel)
		if self.parent.project.observer.id != 0:
			sidText.SetValue(str(self.parent.project.observer.id))
		if self.parent.project.observer.first != '':
			fnameText.SetValue(self.parent.project.observer.first)
			lnameText.SetValue(self.parent.project.observer.last)
		
		ok = wx.Button(panel, ID_OBS_INFO_OK, 'Ok', size=(90, 28))
		cancel = wx.Button(panel, ID_OBS_INFO_CANCEL, 'Cancel', size=(90, 28))
		
		sizer.Add(obs, pos=(0,0), span=(1,5), flag=wx.ALIGN_CENTER, border=5)
		
		sizer.Add(sid, pos=(1, 0), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		sizer.Add(sidText, pos=(1, 1), span=(1, 4), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		
		sizer.Add(fname, pos=(2, 0), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		sizer.Add(fnameText, pos=(2, 1), span=(1, 4), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		sizer.Add(lname, pos=(3, 0), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		sizer.Add(lnameText, pos=(3, 1), span=(1, 4), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		
		line = wx.StaticLine(panel)
		sizer.Add(line, pos=(4, 0), span=(1, 5), flag=wx.EXPAND|wx.BOTTOM, border=10)
		
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
		
		sizer.Add(prj, pos=(5,0), span=(1,5), flag=wx.ALIGN_CENTER, border=5)
		
		sizer.Add(pid, pos=(6, 0), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		sizer.Add(pidText, pos=(6, 1), span=(1, 4), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		
		sizer.Add(pname, pos=(7, 0), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		sizer.Add(pnameText, pos=(7, 1), span=(1, 4), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		sizer.Add(pcoms, pos=(8, 0), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		sizer.Add(pcomsText, pos=(8, 1), span=(4, 4), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		
		line = wx.StaticLine(panel)
		sizer.Add(line, pos=(12, 0), span=(1, 5), flag=wx.EXPAND|wx.BOTTOM, border=10)
		
		ses = wx.StaticText(panel, label='Session Information')
		ses.SetFont(font)
		
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
		
		sizer.Add(ses, pos=(13,0), span=(1,5), flag=wx.ALIGN_CENTER, border=5)
		
		sizer.Add(tid, pos=(14,0), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		sizer.Add(tbwRB, pos=(14,1), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		sizer.Add(tbnRB, pos=(15,1), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		sizer.Add(drxRB, pos=(16,1), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
		
		sizer.Add(ok, pos=(17, 3))
		sizer.Add(cancel, pos=(17, 4), flag=wx.RIGHT|wx.BOTTOM, border=5)
		
		sizer.AddGrowableCol(1)
		sizer.AddGrowableRow(8)
		
		panel.SetSizerAndFit(sizer)
		
		self.observerIDEntry = sidText
		self.observerFirstEntry = fnameText
		self.observerLastEntry = lnameText
		self.projectIDEntry = pidText
		self.projectTitleEntry = pnameText
		self.projectCommentsEntry = pcomsText
		self.tbwButton = tbwRB
		self.tbnButton = tbnRB
		self.drxButton = drxRB
		
	def initEvents(self):
		self.Bind(wx.EVT_BUTTON, self.onOK, id=ID_OBS_INFO_OK)
		self.Bind(wx.EVT_BUTTON, self.onCancel, id=ID_OBS_INFO_CANCEL)
		
	def onOK(self, event):
		self.parent.project.observer.id = int(self.observerIDEntry.GetValue())
		self.parent.project.observer.first = self.observerFirstEntry.GetValue()
		self.parent.project.observer.last = self.observerLastEntry.GetValue()
		self.parent.project.observer.joinName()
		
		self.parent.project.id = self.projectIDEntry.GetValue()
		self.parent.project.name = self.projectTitleEntry.GetValue()
		self.parent.project.comments = self.projectCommentsEntry.GetValue()
		
		if self.parent.mode == '':
			if self.tbwButton:
				self.parent.mode = 'TBW'
			if self.tbnButton:
				self.parent.mode = 'TBN'
			if self.drxButton:
				self.parent.mode = 'DRX'
			self.parent.addColumns()
			
		else:
			if self.tbwButton:
				self.parent.mode = 'TBW'
			if self.tbnButton:
				self.parent.mode = 'TBN'
			if self.drxButton:
				self.parent.mode = 'DRX'
		
		self.Close()
		
	def onCancel(self, event):
		self.Close()


if __name__ == "__main__":
	app = wx.App()
	SDFCreator(None, title='Session Definition File', args=sys.argv[1:])
	app.MainLoop()
	
