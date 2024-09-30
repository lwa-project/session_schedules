#!/usr/bin/env python3

import os
import re
import sys
import copy
import math
import ephem
import numpy
import argparse
from io import StringIO
from datetime import datetime, timedelta
from xml.etree import ElementTree

import conflict

import lsl
from lsl import astro
from lsl.common.dp import fS
from lsl.common import stations, idf
from lsl.astro import deg_to_dms, deg_to_hms, MJD_OFFSET, DJD_OFFSET
from lsl.reader.drx import FILTER_CODES as DRXFilters
from lsl.correlator import uvutils
from lsl.misc import parser as aph

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

from calibratorSearch import CalibratorSearch as OCS

__version__ = "0.2"
__author__ = "Jayce Dowell"


# Deal with the different wxPython versions
if 'phoenix' in wx.PlatformInfo:
    AppendMenuItem = lambda x, y: x.Append(y)
    AppendMenuMenu = lambda *args, **kwds: args[0].Append(*args[1:], **kwds)
    InsertListItem = lambda *args, **kwds: args[0].InsertItem(*args[1:], **kwds)
    SetListItem    = lambda *args, **kwds: args[0].SetItem(*args[1:], **kwds)
    SetDimensions  = lambda *args, **kwds: args[0].SetSize(*args[1:], **kwds)
    ## This one is a little trickier
    def AppendToolItem(*args, **kwds):
        args = args+(kwds['bmpDisabled'] if 'bmpDisabled' in kwds else wx.NullBitmap,)
        return args[0].AddTool(*args[1:], 
                               kind=kwds['kind'] if 'kind' in kwds else wx.ITEM_NORMAL,
                               clientData=kwds['clientData'] if 'clientData' in kwds else None,
                               shortHelp=kwds['shortHelp'] if 'shortHelp' in kwds else '',
                               longHelp=kwds['longHelp'] if 'longHelp' in kwds else '')
else:
    AppendMenuItem = lambda x, y: x.AppendItem(y)
    AppendMenuMenu = lambda *args, **kwds: args[0].AppendMenu(*args[1:], **kwds)
    InsertListItem = lambda *args, **kwds: args[0].InsertStringItem(*args[1:], **kwds)
    SetListItem    = lambda *args, **kwds: args[0].SetStringItem(*args[1:], **kwds)
    SetDimensions  = lambda *args, **kwds: args[0].SetDimensions(*args[1:], **kwds)
    AppendToolItem = lambda *args, **kwds: args[0].AddLabelTool(*args[1:], **kwds)


def pid_print(*args, **kwds):
    print(f"[{os.getpid()}]", *args, **kwds)


class ChoiceMixIn(wx.Control):
    def __init__(self, options={}):
        self.options = options
        self.choices = {}
        self.dropdown = None
        
        self.make_choices()
        self.Bind(wx.EVT_CHOICE, self.CloseDropdown)
        
    def make_choices(self):
        try:
            self.dropdown.Destroy()
        except AttributeError:
            pass
            
        for col in self.options.keys():
            choice = wx.Choice(self, -1, choices=self.options[col])
            font = self.GetFont()
            choice.SetFont(font)
            
            choice.Hide()
            try:
                self.choices[col].Destroy()
            except KeyError:
                pass
            self.choices[col] = choice
            self.choices[col].Bind(wx.EVT_KILL_FOCUS, self.CloseDropdown)
            
        self.dropdown = None
        self.active_row = -1
        self.active_col = -1
        
    def OpenDropdown(self, col, row):
        # give the derived class a chance to Allow/Veto this edit.
        event = wx.ListEvent(wx.wxEVT_COMMAND_LIST_BEGIN_LABEL_EDIT, self.GetId())
        event.m_itemIndex = row
        event.m_col = col
        item = self.GetItem(row, col)
        if 'phoenix' in wx.PlatformInfo:
            event_item = event.Item
        else:
            event_item = event.m_item
        event_item.SetId(item.GetId()) 
        event_item.SetColumn(item.GetColumn()) 
        event_item.SetData(item.GetData()) 
        event_item.SetText(item.GetText()) 
        ret = self.GetEventHandler().ProcessEvent(event)
        if ret and not event.IsAllowed():
            return   # user code doesn't allow the edit.
            
        x0 = self.col_locs[col]
        x1 = self.col_locs[col+1] - x0
        
        scrolloffset = self.GetScrollPos(wx.HORIZONTAL)

        # scroll forward
        if x0+x1-scrolloffset > self.GetSize()[0]:
            if wx.Platform == "__WXMSW__":
                # don't start scrolling unless we really need to
                offset = x0+x1-self.GetSize()[0]-scrolloffset
                # scroll a bit more than what is minimum required
                # so we don't have to scroll everytime the user presses TAB
                # which is very tireing to the eye
                addoffset = self.GetSize()[0]/4
                # but be careful at the end of the list
                if addoffset + scrolloffset < self.GetSize()[0]:
                    offset += addoffset

                self.ScrollList(offset, 0)
                scrolloffset = self.GetScrollPos(wx.HORIZONTAL)
            else:
                # Since we can not programmatically scroll the ListCtrl
                # close the editor so the user can scroll and open the editor
                # again
                self.dropdown.SetValue(self.GetItem(row, col).GetText())
                self.active_row = row
                self.active_col = col
                self.CloseDropdown()
                return

        y0 = self.GetItemRect(row)[1]
        
        try:
            self.dropdown = self.choices[col]
        except KeyError:
            return
            
        SetDimensions(self.dropdown, x0-scrolloffset,y0, x1,-1)
        
        idx = self.dropdown.FindString(self.GetItem(row, col).GetText())
        self.dropdown.SetSelection(idx)
        self.dropdown.Show()
        self.dropdown.Raise()
        #self.dropdown.SetSelection(-1,-1)
        self.dropdown.SetFocus()
        
        self.active_row = row
        self.active_col = col
        
    def CloseDropdown(self, event=None):
        if self.dropdown is None:
            return
        text = self.dropdown.GetString(self.dropdown.GetSelection())
        self.dropdown.Hide()
        self.SetFocus()
        
        # Event can be vetoed. It doesn't has SetEditCanceled(), what would 
        # require passing extra argument to CloseMenu() 
        event = wx.ListEvent(wx.wxEVT_COMMAND_LIST_END_LABEL_EDIT, self.GetId())
        if 'phoenix' in wx.PlatformInfo:
            event.Index = self.active_row
            event.Column = self.active_col
            item = wx.ListItem(self.GetItem(self.active_row, self.active_col))
            item.SetText(text)
            event.SetItem(item)
        else:
            event.m_itemIndex = self.active_row
            event.m_col = self.active_col
            item = self.GetItem(self.active_row, self.active_col)
            event.m_item.SetId(item.GetId()) 
            event.m_item.SetColumn(item.GetColumn()) 
            event.m_item.SetData(item.GetData()) 
            event.m_item.SetText(text) #should be empty string if editor was canceled
        ret = self.GetEventHandler().ProcessEvent(event)
        if not ret or event.IsAllowed():
            if self.IsVirtual():
                # replace by whather you use to populate the virtual ListCtrl
                # data source
                self.SetVirtualData(self.active_row, self.active_col, text)
            else:
                SetListItem(self, self.active_row, self.active_col, text)
        self.RefreshItem(self.active_row)


class ScanListCtrl(wx.ListCtrl, TextEditMixin, ChoiceMixIn, CheckListCtrlMixin):
    """
    Class that combines an editable list with check boxes.
    """
    
    def __init__(self, parent, **kwargs):
        wx.ListCtrl.__init__(self, parent, style=wx.LC_REPORT, **kwargs)
        TextEditMixin.__init__(self)
        ChoiceMixIn.__init__(self, {2:['FluxCal','PhaseCal','Target'], 10:['1','2','3','4','5','6','7']})
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
            except (KeyError, AttributeError):
                pass
                
            # Stepped scan edits - disabled
            try:
                self.parent.obsmenu['steppedEdit'].Enable(False)
                self.parent.toolbar.EnableTool(ID_EDIT_STEPPED, False)
            except (KeyError, AttributeError):
                pass
                
            # Remove and resolve - disabled
            self.parent.obsmenu['pmotion'].Enable(False)
            self.parent.toolbar.EnableTool(ID_PMOTION, False)
            self.parent.obsmenu['remove'].Enable(False)
            self.parent.toolbar.EnableTool(ID_REMOVE, False)
            self.parent.obsmenu['resolve'].Enable(False)
            
        elif self.nSelected == 1:
            # Edit menu - enabled
            try:
                self.parent.editmenu['cut'].Enable(True)
                self.parent.editmenu['copy'].Enable(True)
            except (KeyError, AttributeError):
                pass
                
            # Stepped scan edits - enbled if there is an index and it is STEPPED, 
            # disabled otherwise
            if index is not None:
                if self.parent.project.runs[0].scans[index].mode == 'STEPPED':
                    try:
                        self.parent.obsmenu['steppedEdit'].Enable(True)
                        self.parent.toolbar.EnableTool(ID_EDIT_STEPPED, True)
                    except (KeyError, AttributeError):
                        pass
                else:
                    try:
                        self.parent.obsmenu['steppedEdit'].Enable(False)
                        self.parent.toolbar.EnableTool(ID_EDIT_STEPPED, False)
                    except (KeyError, AttributeError):
                        pass
            else:
                # Stepped scan edits - disabled
                try:
                    self.parent.obsmenu['steppedEdit'].Enable(False)
                    self.parent.toolbar.EnableTool(ID_EDIT_STEPPED, False)
                except (KeyError, AttributeError):
                    pass
                    
            # Remove and resolve - enabled
            self.parent.obsmenu['pmotion'].Enable(True)
            self.parent.toolbar.EnableTool(ID_PMOTION, True)
            self.parent.obsmenu['remove'].Enable(True)
            self.parent.toolbar.EnableTool(ID_REMOVE, True)
            self.parent.obsmenu['resolve'].Enable(True)
            
        else:
            # Edit menu - enabled
            try:
                self.parent.editmenu['cut'].Enable(True)
                self.parent.editmenu['copy'].Enable(True)
            except (KeyError, AttributeError):
                pass
                
            # Stepped scan edits - disabled
            try:
                self.parent.obsmenu['steppedEdit'].Enable(False)
                self.parent.toolbar.EnableTool(ID_EDIT_STEPPED, False)
            except (KeyError, AttributeError):
                pass
                
            # Motion, remove, and resolve - enabled and disabled
            self.parent.obsmenu['pmotion'].Enable(False)
            self.parent.toolbar.EnableTool(ID_PMOTION, False)
            self.parent.obsmenu['remove'].Enable(True)
            self.parent.toolbar.EnableTool(ID_REMOVE, True)
            self.parent.obsmenu['resolve'].Enable(False)
            
    def CheckItem(self, index, check=True):
        """
        Catch for wxPython 4.1 which has a wx.ListCtrl.CheckItem() method 
        that interferes with CheckListCtrlMixin.CheckItem().
        """
        
        CheckListCtrlMixin.CheckItem(self, index, check=check)
        
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
        elif col in self.options.keys():
            ChoiceMixIn.OpenDropdown(self, col, row)
        elif self.parent.project.runs[0].scans[row].mode in ['TRK_SOL', 'TRK_JOV'] and col in [6, 7]:
            pass
        elif self.parent.project.runs[0].scans[row].mode == 'STEPPED' and col in [5, 6, 7, 8, 9, 11]:
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
            except (KeyError, AttributeError):
                pass
                
        elif self.nSelected == 1:
            # Edit menu - enabled
            try:
                self.parent.editmenu['cut'].Enable(True)
                self.parent.editmenu['copy'].Enable(True)
            except (KeyError, AttributeError):
                pass
                
        else:
            # Edit menu - enabled
            try:
                self.parent.editmenu['cut'].Enable(True)
                self.parent.editmenu['copy'].Enable(True)
            except (KeyError, AttributeError):
                pass
                
    def CheckItem(self, index, check=True):
        """
        Catch for wxPython 4.1 which has a wx.ListCtrl.CheckItem() method 
        that interferes with CheckListCtrlMixin.CheckItem().
        """
        
        CheckListCtrlMixin.CheckItem(self, index, check=check)
        
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
        self.parent = parent
        
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
ID_LOGGER = 15
ID_QUIT = 16

ID_INFO = 21
ID_SCHEDULE = 22
ID_ADD_DRX_RADEC = 26
ID_ADD_DRX_SOLAR = 27
ID_ADD_DRX_JOVIAN = 28
ID_ADD_STEPPED_RADEC = 29
ID_ADD_STEPPED_AZALT = 30
ID_EDIT_STEPPED = 31
ID_PMOTION = 32
ID_REMOVE = 33
ID_VALIDATE = 40
ID_TIMESERIES = 41
ID_UVCOVERAGE = 42
ID_RESOLVE = 43
ID_SEARCH = 44
ID_ADVANCED = 45

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

class IDFCreator(wx.Frame):
    def __init__(self, parent, title, args):
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
        
        self.initIDF()
        
        self.initUI()
        self.initEvents()
        self.Show()
        
        idf._DRSUCapacityTB = args.drsu_size
        
        #self.logger = None
        #self.onLogger(None)
        
        if args.filename is not None:
            self.filename = args.filename
            self.parseFile(self.filename)
        else:
            self.filename = ''
            self.setMenuButtons('None')
            
        self.edited = False
        self.setSaveButton()
        
    def initIDF(self):
        """
        Create an empty idf.project instance to store all of the actual
        scans.
        """
        
        po = idf.ProjectOffice()
        observer = idf.Observer('', 0, first='', last='')
        project = idf.Project(observer, '', '', project_office=po)
        run = idf.Run('run_name', 0, scans=[])
        project.runs = [run,]
        
        self.project = project
        self.mode = ''
        
        self.project.runs[0].drxGain = -1
        
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
        AppendMenuItem(fileMenu, new)
        open = wx.MenuItem(fileMenu, ID_OPEN, '&Open')
        AppendMenuItem(fileMenu, open)
        save = wx.MenuItem(fileMenu, ID_SAVE, '&Save')
        AppendMenuItem(fileMenu, save)
        saveas = wx.MenuItem(fileMenu, ID_SAVE_AS, 'S&ave As')
        AppendMenuItem(fileMenu, saveas)
        fileMenu.AppendSeparator()
        #logger = wx.MenuItem(fileMenu, ID_LOGGER, '&Logger')
        #AppendMenuItem(fileMenu, logger)
        #fileMenu.AppendSeparator()
        quit = wx.MenuItem(fileMenu, ID_QUIT, '&Quit')
        AppendMenuItem(fileMenu, quit)
        
        # Save the 'save' menu item
        self.savemenu = save
        
        # Edit menu items
        cut = wx.MenuItem(editMenu, ID_CUT, 'C&ut Selected Scan')
        AppendMenuItem(editMenu, cut)
        cpy = wx.MenuItem(editMenu, ID_COPY, '&Copy Selected Scan')
        AppendMenuItem(editMenu, cpy)
        pstb = wx.MenuItem(editMenu, ID_PASTE_BEFORE, '&Paste Before Selected')
        AppendMenuItem(editMenu, pstb)
        psta = wx.MenuItem(editMenu, ID_PASTE_AFTER, '&Paste After Selected')
        AppendMenuItem(editMenu, psta)
        pste = wx.MenuItem(editMenu, ID_PASTE_END, '&Paste at End of List')
        AppendMenuItem(editMenu, pste)
        
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
        AppendMenuItem(obsMenu, info)
        sch = wx.MenuItem(obsMenu, ID_SCHEDULE, 'Sc&heduling')
        AppendMenuItem(obsMenu, sch)
        obsMenu.AppendSeparator()
        add = wx.Menu()
        addDRXR = wx.MenuItem(add, ID_ADD_DRX_RADEC, 'DRX - &RA/Dec')
        AppendMenuItem(add, addDRXR)
        addDRXS = wx.MenuItem(add, ID_ADD_DRX_SOLAR, 'DRX - &Solar')
        AppendMenuItem(add, addDRXS)
        addDRXJ = wx.MenuItem(add, ID_ADD_DRX_JOVIAN, 'DRX - &Jovian')
        AppendMenuItem(add, addDRXJ)
        #addSteppedRADec = wx.MenuItem(add, ID_ADD_STEPPED_RADEC, 'DRX - Ste&pped - RA/Dec')
        #AppendMenuItem(add, addSteppedRADec)
        #addSteppedAzAlt = wx.MenuItem(add, ID_ADD_STEPPED_AZALT, 'DRX - Ste&pped - Az/Alt')
        #AppendMenuItem(add, addSteppedAzAlt)
        #editStepped = wx.MenuItem(add, ID_EDIT_STEPPED, 'DRX - Edit Selected Stepped Obs.')
        #AppendMenuItem(add, editStepped)
        AppendMenuMenu(obsMenu, -1, '&Add', add)
        pmotion = wx.MenuItem(obsMenu, ID_PMOTION, '&Proper Motion')
        AppendMenuItem(obsMenu, pmotion)
        remove = wx.MenuItem(obsMenu, ID_REMOVE, '&Remove Selected')
        AppendMenuItem(obsMenu, remove)
        validate = wx.MenuItem(obsMenu, ID_VALIDATE, '&Validate All\tF5')
        AppendMenuItem(obsMenu, validate)
        obsMenu.AppendSeparator()
        resolve = wx.MenuItem(obsMenu, ID_RESOLVE, 'Resolve Selected\tF3')
        AppendMenuItem(obsMenu, resolve)
        search = wx.MenuItem(obsMenu, ID_SEARCH, 'Calibrator Search\tF4')
        AppendMenuItem(obsMenu, search)
        obsMenu.AppendSeparator()
        timeseries = wx.MenuItem(obsMenu, ID_TIMESERIES, 'Run at a &Glance')
        AppendMenuItem(obsMenu, timeseries)
        uvcoverage = wx.MenuItem(obsMenu, ID_UVCOVERAGE, '&UV Coverage')
        AppendMenuItem(obsMenu, uvcoverage)
        advanced = wx.MenuItem(obsMenu, ID_ADVANCED, 'Advanced &Settings')
        AppendMenuItem(obsMenu, advanced)
        
        # Save menu items
        self.obsmenu['drx-radec'] = addDRXR
        self.obsmenu['drx-solar'] = addDRXS
        self.obsmenu['drx-jovian'] = addDRXJ
        #self.obsmenu['steppedRADec'] = addSteppedRADec
        #self.obsmenu['steppedAzAlt'] = addSteppedAzAlt
        #self.obsmenu['steppedEdit'] = editStepped
        self.obsmenu['pmotion'] = pmotion
        self.obsmenu['remove'] = remove
        self.obsmenu['resolve'] = resolve
        for k in ('pmotion', 'remove', 'resolve'):
            self.obsmenu[k].Enable(False)
            
        # Data menu items
        volume = wx.MenuItem(obsMenu, ID_DATA_VOLUME, '&Estimated Data Volume')
        AppendMenuItem(dataMenu, volume)
        
        # Help menu items
        help = wx.MenuItem(helpMenu, ID_HELP, 'Swarm GUI Handbook\tF1')
        AppendMenuItem(helpMenu, help)
        self.finfo = wx.MenuItem(helpMenu, ID_FILTER_INFO, '&Filter Codes')
        AppendMenuItem(helpMenu, self.finfo)
        helpMenu.AppendSeparator()
        about = wx.MenuItem(helpMenu, ID_ABOUT, '&About')
        AppendMenuItem(helpMenu, about)
        
        menubar.Append(fileMenu, '&File')
        menubar.Append(editMenu, '&Edit')
        menubar.Append(obsMenu,  '&Scans')
        menubar.Append(dataMenu, '&Data')
        menubar.Append(helpMenu, '&Help')
        self.SetMenuBar(menubar)
        
        # Toolbar
        self.toolbar = self.CreateToolBar()
        AppendToolItem(self.toolbar, ID_NEW, '', wx.Bitmap(os.path.join(self.scriptPath, 'icons', 'new.png')), shortHelp='New', 
                                longHelp='Clear the existing setup and start a new project/run')
        AppendToolItem(self.toolbar, ID_OPEN, '', wx.Bitmap(os.path.join(self.scriptPath, 'icons', 'open.png')), shortHelp='Open', 
                                longHelp='Open and load an existing SD file')
        AppendToolItem(self.toolbar, ID_SAVE, '', wx.Bitmap(os.path.join(self.scriptPath, 'icons', 'save.png')), shortHelp='Save', 
                                longHelp='Save the current setup')
        AppendToolItem(self.toolbar, ID_SAVE_AS, '', wx.Bitmap(os.path.join(self.scriptPath, 'icons', 'save-as.png')), shortHelp='Save as', 
                                longHelp='Save the current setup to a new SD file')
        AppendToolItem(self.toolbar, ID_QUIT, '', wx.Bitmap(os.path.join(self.scriptPath, 'icons', 'exit.png')), shortHelp='Quit', 
                                longHelp='Quit (without saving)')
        self.toolbar.AddSeparator()
        AppendToolItem(self.toolbar, ID_ADD_DRX_RADEC,  'drx-radec',  wx.Bitmap(os.path.join(self.scriptPath, 'icons', 'drx-radec.png')),  shortHelp='Add DRX - RA/Dec', 
                                longHelp='Add a new beam forming DRX scan that tracks the sky (ra/dec)')
        AppendToolItem(self.toolbar, ID_ADD_DRX_SOLAR,  'drx-solar',  wx.Bitmap(os.path.join(self.scriptPath, 'icons', 'drx-solar.png')),  shortHelp='Add DRX - Solar', 
                                longHelp='Add a new beam forming DRX scan that tracks the Sun')
        AppendToolItem(self.toolbar, ID_ADD_DRX_JOVIAN, 'drx-jovian', wx.Bitmap(os.path.join(self.scriptPath, 'icons', 'drx-jovian.png')), shortHelp='Add DRX - Jovian', 
                                longHelp='Add a new beam forming DRX scan that tracks Jupiter')
        #AppendToolItem(self.toolbar, ID_ADD_STEPPED_RADEC,  'stepped', wx.Bitmap(os.path.join(self.scriptPath, 'icons', 'stepped-radec.png')), shortHelp='Add DRX - Stepped - RA/Dec', 
        #                        longHelp='Add a new beam forming DRX scan with custom RA/Dec position and frequency stepping')
        #AppendToolItem(self.toolbar, ID_ADD_STEPPED_AZALT,  'stepped', wx.Bitmap(os.path.join(self.scriptPath, 'icons', 'stepped-azalt.png')), shortHelp='Add DRX - Stepped - Az/Alt', 
        #                        longHelp='Add a new beam forming DRX scan with custom az/alt position and frequency stepping')
        #AppendToolItem(self.toolbar, ID_EDIT_STEPPED,  'step', wx.Bitmap(os.path.join(self.scriptPath, 'icons', 'stepped-edit.png')), shortHelp='Edit Selected Stepped Scan', 
        #                        longHelp='Add and edit steps for the currently selected stepped scan')
        AppendToolItem(self.toolbar, ID_PMOTION, '', wx.Bitmap(os.path.join(self.scriptPath, 'icons', 'proper-motion.png')), shortHelp='Advanced Options', 
                                longHelp='Set the proper motion for this scan')
        AppendToolItem(self.toolbar, ID_REMOVE, '', wx.Bitmap(os.path.join(self.scriptPath, 'icons', 'remove.png')), shortHelp='Remove Selected', 
                                longHelp='Remove the selected scans from the list')
        AppendToolItem(self.toolbar, ID_VALIDATE, '', wx.Bitmap(os.path.join(self.scriptPath, 'icons', 'validate.png')), shortHelp='Validate Scans', 
                                longHelp='Validate the current set of parameters and scans')
        self.toolbar.AddSeparator()
        AppendToolItem(self.toolbar, ID_SEARCH, '', wx.Bitmap(os.path.join(self.scriptPath, 'icons', 'search.png')), shortHelp='Calibrator Search', 
                                longHelp='Search for calibrators around the selected scan')
        self.toolbar.AddSeparator()
        AppendToolItem(self.toolbar, ID_HELP, '', wx.Bitmap(os.path.join(self.scriptPath, 'icons', 'help.png')), shortHelp='Help', 
                                longHelp='Display a brief help message for this program')
        self.toolbar.Realize()
        
        # Disable "pmotion" and "remove" in the toolbar
        self.toolbar.EnableTool(ID_PMOTION, False)
        self.toolbar.EnableTool(ID_REMOVE, False)
        
        # Status bar
        self.statusbar = self.CreateStatusBar()
        
        # Scan list
        hbox = wx.BoxSizer(wx.HORIZONTAL)
        self.panel = ScrolledPanel(self, -1)
        
        self.listControl = ScanListCtrl(self.panel, id=ID_LISTCTRL)
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
        #self.Bind(wx.EVT_MENU, self.onLogger, id=ID_LOGGER)
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
        self.Bind(wx.EVT_MENU, self.onAddDRXR, id=ID_ADD_DRX_RADEC)
        self.Bind(wx.EVT_MENU, self.onAddDRXS, id=ID_ADD_DRX_SOLAR)
        self.Bind(wx.EVT_MENU, self.onAddDRXJ, id=ID_ADD_DRX_JOVIAN)
        #self.Bind(wx.EVT_MENU, self.onAddSteppedRADec, id=ID_ADD_STEPPED_RADEC)
        #self.Bind(wx.EVT_MENU, self.onAddSteppedAzAlt, id=ID_ADD_STEPPED_AZALT)
        #self.Bind(wx.EVT_MENU, self.onEditStepped, id=ID_EDIT_STEPPED)
        self.Bind(wx.EVT_MENU, self.onProperMotion, id=ID_PMOTION)
        self.Bind(wx.EVT_MENU, self.onRemove, id=ID_REMOVE)
        self.Bind(wx.EVT_MENU, self.onValidate, id=ID_VALIDATE)
        self.Bind(wx.EVT_MENU, self.onResolve, id=ID_RESOLVE)
        self.Bind(wx.EVT_MENU, self.onSearch, id=ID_SEARCH)
        self.Bind(wx.EVT_MENU, self.onTimeseries, id=ID_TIMESERIES)
        self.Bind(wx.EVT_MENU, self.onUVCoverage, id=ID_UVCOVERAGE)
        self.Bind(wx.EVT_MENU, self.onAdvanced, id=ID_ADVANCED)
        
        # Data menu events
        self.Bind(wx.EVT_MENU, self.onVolume, id=ID_DATA_VOLUME)
        
        # Help menu events
        self.Bind(wx.EVT_MENU, self.onHelp, id=ID_HELP)
        self.Bind(wx.EVT_MENU, self.onFilterInfo, id=ID_FILTER_INFO)
        self.Bind(wx.EVT_MENU, self.onAbout, id=ID_ABOUT)
        
        # Scan edits
        self.Bind(wx.EVT_LIST_END_LABEL_EDIT, self.onEdit, id=ID_LISTCTRL)
        
        # Window manager close
        self.Bind(wx.EVT_CLOSE, self.onQuit)
        
    #def onLogger(self, event):
    #    """
    #    Create a new logger window, if needed
    #    """
    #    
    #    if self.logger is None:
    #        self.logger = wx.LogWindow(self, 'IDF Logger', True, False)
    #    elif not self.logger.Frame.IsShown():
    #        self.logger.Destroy()
    #        self.logger = wx.LogWindow(self, 'IDF Logger', True, False)
            
    def onNew(self, event):
        """
        Create a new ID run.
        """
        
        if self.edited:
            dialog = wx.MessageDialog(self, 'The current interferometer defintion file has changes that have not been saved.\n\nStart a new run anyways?', 'Confirm New', style=wx.YES_NO|wx.NO_DEFAULT|wx.ICON_QUESTION)
            
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
        self.listControl.nSelected = 0
        self.listControl.setCheckDependant()
        self.initIDF()
        ObserverInfo(self)
        
    def onLoad(self, event):
        """
        Load an existing SD file.
        """
        
        if self.edited:
            dialog = wx.MessageDialog(self, 'The current interferometer defintion file has changes that have not been saved.\n\nOpen a new file anyways?', 'Confirm Open', style=wx.YES_NO|wx.NO_DEFAULT|wx.ICON_QUESTION)
            
            if dialog.ShowModal() == wx.ID_YES:
                pass
            else:
                return False
                
        dialog = wx.FileDialog(self, "Select an ID File", self.dirname, '', 'IDF Files (*.idf,*.txt)|*.idf;*.txt|All Files|*', wx.FD_OPEN)
        if dialog.ShowModal() == wx.ID_OK:
            self.dirname = dialog.GetDirectory()
            self.filename = dialog.GetPath()
            self.parseFile(dialog.GetPath())
            
            self.edited = False
            self.setSaveButton()
            
        dialog.Destroy()
        
    def onSave(self, event):
        """
        Save the current scan to a file.
        """
        
        if self.filename == '':
            self.onSaveAs(event)
        else:
            
            if not self.onValidate(1, confirmValid=False):
                self.displayError('The interferometer definition file could not be saved due to errors in the file.', title='Save Failed')
            else:
                try:
                    with open(self.filename, 'w') as fh:
                        fh.write(self.project.render())
                        
                    self.edited = False
                    self.setSaveButton()
                except IOError as err:
                    self.displayError(f"Error saving to '{self.filename}'", details=err, title='Save Error')
                    
    def onSaveAs(self, event):
        """
        Save the current scan to a new ID file.
        """
        
        if not self.onValidate(1, confirmValid=False):
            self.displayError('The interferometer definition file could not be saved due to errors in the file.', title='Save Failed')
        else:
            dialog = wx.FileDialog(self, "Select Output File", self.dirname, '', 'IDF Files (*.idf,*.txt)|*.idf;*.txt|All Files|*', wx.FD_SAVE|wx.FD_OVERWRITE_PROMPT)
            
            if dialog.ShowModal() == wx.ID_OK:
                self.dirname = dialog.GetDirectory()
                
                self.filename = dialog.GetPath()
                try:
                    with open(self.filename, 'w') as fh:
                        fh.write(self.project.render())
                        
                    self.edited = False
                    self.setSaveButton()
                except IOError as err:
                    self.displayError(f"Error saving to '{self.filename}'", details=err, title='Save Error')
                    
            dialog.Destroy()
            
    def onCopy(self, event):
        """
        Copy the selected scan(s) to the buffer.
        """
        
        self.buffer = []
        for i in range(self.listControl.GetItemCount()):
            if self.listControl.IsChecked(i):
                self.buffer.append( copy.deepcopy(self.project.runs[0].scans[i]) )
                
        self.editmenu['pasteBefore'].Enable(True)
        self.editmenu['pasteAfter'].Enable(True)
        self.editmenu['pasteEnd'].Enable(True)
        
    def onCut(self, event):
        self.onCopy(event)
        self.onRemove(event)
        
    def onPasteBefore(self, event):
        firstChecked = None
        
        for i in range(self.listControl.GetItemCount()):
            if self.listControl.IsChecked(i):
                firstChecked = i
                break
                
        if firstChecked is not None:
            id = firstChecked
            
            for obs in self.buffer[::-1]:
                cObs = copy.deepcopy(obs)
                
                self.project.runs[0].scans.insert(id, cObs)
                self.addScan(self.project.runs[0].scans[id], id)
                
            self.edited = True
            self.setSaveButton()
            
            # Re-number the remaining rows to keep the display clean
            for id in range(self.listControl.GetItemCount()):
                item = self.listControl.GetItem(id, 0)
                item.SetText('%i' % (id+1))
                self.listControl.SetItem(item)
                self.listControl.RefreshItem(item.GetId())
                
            # Fix the times on DRX scans to make thing continuous
            for id in range(firstChecked+len(self.buffer)-1, -1, -1):
                dur = self.project.runs[0].scans[id].dur
                
                tStart, _ = idf.get_scan_start_stop(self.project.runs[0].scans[id+1])
                tStart -= timedelta(seconds=dur//1000, microseconds=(dur%1000)*1000)
                cStart = 'UTC %i %02i %02i %02i:%02i:%06.3f' % (tStart.year, tStart.month, tStart.day, tStart.hour, tStart.minute, tStart.second+tStart.microsecond/1e6)
                self.project.runs[0].scans[id].start = cStart
                self.addScan(self.project.runs[0].scans[id], id, update=True)
                
    def onPasteAfter(self, event):
        lastChecked = None
        
        for i in range(self.listControl.GetItemCount()):
            if self.listControl.IsChecked(i):
                lastChecked = i
                
        if lastChecked is not None:
            id = lastChecked + 1
            
            for obs in self.buffer[::-1]:
                cObs = copy.deepcopy(obs)
                
                self.project.runs[0].scans.insert(id, cObs)
                self.addScan(self.project.runs[0].scans[id], id)
                
            self.edited = True
            self.setSaveButton()
            
            # Re-number the remaining rows to keep the display clean
            for id in range(self.listControl.GetItemCount()):
                item = self.listControl.GetItem(id, 0)
                item.SetText('%i' % (id+1))
                self.listControl.SetItem(item)
                self.listControl.RefreshItem(item.GetId())
                
            # Fix the times on DRX scans to make thing continuous
            for id in range(lastChecked+1, self.listControl.GetItemCount()):
                _, tStop = idf.get_scan_start_stop(self.project.runs[0].scans[id-1])
                cStart = 'UTC %i %02i %02i %02i:%02i:%06.3f' % (tStop.year, tStop.month, tStop.day, tStop.hour, tStop.minute, tStop.second+tStop.microsecond/1e6)
                self.project.runs[0].scans[id].start = cStart
                self.addScan(self.project.runs[0].scans[id], id, update=True)
                
    def onPasteEnd(self, event):
        """
        Paste the selected scan(s) at the end of the current run.
        """
        
        lastChecked = self.listControl.GetItemCount() - 1
        
        if self.buffer is not None:
            id = lastChecked + 1
            
            for obs in self.buffer[::-1]:
                cObs = copy.deepcopy(obs)
                
                self.project.runs[0].scans.insert(id, cObs)
                self.addScan(self.project.runs[0].scans[id], id)
                
            self.edited = True
            self.setSaveButton()
            
        # Re-number the remaining rows to keep the display clean
        for id in range(self.listControl.GetItemCount()):
            item = self.listControl.GetItem(id, 0)
            item.SetText('%i' % (id+1))
            self.listControl.SetItem(item)
            self.listControl.RefreshItem(item.GetId())
            
        # Fix the times on DRX scans to make thing continuous
        for id in range(lastChecked+1, self.listControl.GetItemCount()):
            _, tStop = idf.get_scan_start_stop(self.project.runs[0].scans[id-1])
            cStart = 'UTC %i %02i %02i %02i:%02i:%06.3f' % (tStop.year, tStop.month, tStop.day, tStop.hour, tStop.minute, tStop.second+tStop.microsecond/1e6)
            self.project.runs[0].scans[id].start = cStart
            self.addScan(self.project.runs[0].scans[id], id, update=True)
            
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
        
    def _getCurrentDateString(self):
        """
        Function to get a datetime string, in UTC, for a new scan.
        """
        
        tStop = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        if self.listControl.GetItemCount() > 0:
            _, tStop = idf.get_scan_start_stop(self.project.runs[0].scans[-1])
            
        return 'UTC %i %02i %02i %02i:%02i:%06.3f' % (tStop.year, tStop.month, tStop.day, tStop.hour, tStop.minute, tStop.second+tStop.microsecond/1e6)
        
    def _getDefaultFilter(self):
        """
        Function to get the default value for the filter code for modes that 
        need a filter code.  This is mainly to help keep Sevilleta SDFs 
        default to appropriate filter instead of 7.
        """
        
        return 7
        
    def onAddDRXR(self, event):
        """
        Add a tracking RA/Dec (DRX) scan to the list and update the main window.
        """
        
        id = self.listControl.GetItemCount() + 1
        gain = self.project.runs[0].drxGain
        self.project.runs[0].scans.append( idf.DRX('DRX-%i' % id, 'Target', self._getCurrentDateString(), '00:00:00.000', 0.0, 0.0, 42e6, 74e6, self._getDefaultFilter(), gain=gain) )
        self.addScan(self.project.runs[0].scans[-1], id)
        
        self.edited = True
        self.setSaveButton()
        
    def onAddDRXS(self, event):
        """
        Add a tracking Sun (DRX) scan to the list and update the main window.
        """
        
        id = self.listControl.GetItemCount() + 1
        gain = self.project.runs[0].drxGain
        self.project.runs[0].scans.append( idf.Solar('Sun-%d' % id, 'Target', self._getCurrentDateString(), '00:00:00.000', 42e6, 74e6, self._getDefaultFilter(), gain=gain) )
        self.addScan(self.project.runs[0].scans[-1], id)
        
        self.edited = True
        self.setSaveButton()
        
    def onAddDRXJ(self, event):
        """
        Add a tracking Jupiter (DRX) scan to the list and update the main window.
        """
        
        id = self.listControl.GetItemCount() + 1
        gain = self.project.runs[0].drxGain
        self.project.runs[0].scans.append( idf.Jovian('Jupiter-%i' % id, 'Target', self._getCurrentDateString(), '00:00:00.000', 42e6, 74e6, self._getDefaultFilter(), gain=gain) )
        self.addScan(self.project.runs[0].scans[-1], id)
        
        self.edited = True
        self.setSaveButton()
        
    #def onAddSteppedRADec(self, event):
    #    """
    #    Add a RA/Dec stepped scan block.
    #    """
    #    
    #    id = self.listControl.GetItemCount() + 1
    #    gain = self.project.runs[0].drxGain
    #    self.project.runs[0].scans.append( idf.Stepped('stps-%i' % id, 'radec-%i' % id, self._getCurrentDateString(), self._getDefaultFilter(), RADec=True, gain=gain) )
    #    self.addScan(self.project.runs[0].scans[-1], id)
    #    
    #    self.edited = True
    #    self.setSaveButton()
    #    
    #def onAddSteppedAzAlt(self, event):
    #    """
    #    Add a Az/Alt stepped scan block.
    #    """
    #    
    #    id = self.listControl.GetItemCount() + 1
    #    gain = self.project.runs[0].drxGain
    #    self.project.runs[0].scans.append( idf.Stepped('stps-%i' % id, 'azalt-%i' % id, self._getCurrentDateString(), self._getDefaultFilter(), RADec=False, gain=gain) )
    #    self.addScan(self.project.runs[0].scans[-1], id)
    #    
    #    self.edited = True
    #    self.setSaveButton()
    #    
    #def onEditStepped(self, event):
    #    """
    #    Add or edit steps to the currently selected stepped observtion.
    #    """
    #    
    #    nChecked = 0
    #    whichChecked = None
    #    for i in range(self.listControl.GetItemCount()):
    #        if self.listControl.IsChecked(i):
    #            whichChecked = i
    #            nChecked += 1
    #            
    #    if nChecked != 1:
    #        return False
    #    if self.project.runs[0].scans[i].mode != 'STEPPED':
    #        return False
    #        
    #    SteppedWindow(self, whichChecked)
        
    def onEdit(self, event):
        """
        Make the selected change to the underlying scan.
        """
        
        obsIndex = event.GetIndex()
        obsAttr = event.GetColumn()
        self.SetStatusText('')
        try:
            newData = self.coerceMap[obsAttr](event.GetText())
                
            oldData = getattr(self.project.runs[0].scans[obsIndex], self.columnMap[obsAttr])
            setattr(self.project.runs[0].scans[obsIndex], self.columnMap[obsAttr], newData)
            self.project.runs[0].scans[obsIndex].update()
            
            item = self.listControl.GetItem(obsIndex, obsAttr)
            if self.listControl.GetItemTextColour(item.GetId()) != (0, 0, 0, 255):
                self.listControl.SetItemTextColour(item.GetId(), wx.BLACK)
                self.listControl.RefreshItem(item.GetId())
                
            self.edited = True
            self.setSaveButton()
            
            self.badEdit = False
            self.badEditLocation = (-1, -1)
        except ValueError as err:
            pid_print(f"Error: {str(err)}")
            self.SetStatusText('Error: %s' % str(err))
            
            item = self.listControl.GetItem(obsIndex, obsAttr)
            self.listControl.SetItemTextColour(item.GetId(), wx.RED)
            self.listControl.RefreshItem(item.GetId())
            
            self.badEdit = True
            self.badEditLocation = (obsIndex, obsAttr)
            
    def onProperMotion(self, event):
        """
        Bring up the proper motion dialog box.
        """
        
        nChecked = 0
        whichChecked = None
        for i in range(self.listControl.GetItemCount()):
            if self.listControl.IsChecked(i):
                whichChecked = i
                nChecked += 1
                
        if nChecked != 1:
            return False
            
        ProperMotionWindow(self, whichChecked)
        
    def onRemove(self, event):
        """
        Remove selected scans from the main window as well as the 
        self.project.runs[0].scans list.
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
            
            for i in range(lc.GetItemCount()):
                if lc.IsChecked(i):
                    return i+1
            return 0
            
        # While there is still at least one bad row, continue looping and removing
        # rows
        bad = stillBad(self.listControl)
        while bad:
            i = bad - 1
            self.listControl.DeleteItem(i)
            del self.project.runs[0].scans[i]
            self.listControl.nSelected -= 1
            bad = stillBad(self.listControl)
            
            self.edited = True
            self.setSaveButton()
            
        # Update the check controlled features
        self.listControl.setCheckDependant()
        
        # Re-number the remaining rows to keep the display clean
        for i in range(self.listControl.GetItemCount()):
            item = self.listControl.GetItem(i, 0)
            item.SetText(f"{i+1}")
            self.listControl.SetItem(item)
            self.listControl.RefreshItem(item.GetId())
            
    def onValidate(self, event, confirmValid=True):
        """
        Validate the current scans.
        """
        
        try:
            if self.badEdit:
                validObs = False
                return False
            else:
                validObs = True
        except AttributeError:
            validObs = True
            
        # Loop through the lists of scans and validate one-at-a-time so 
        # that we can mark bad scans
        i = 0
        for obs in self.project.runs[0].scans:
            for station in self.project.runs[0].stations:
                pid_print(f"Validating scan {i+1} on {station.id}")
                valid = obs.validate(verbose=True)
                for col in range(len(self.columnMap)-1):  # -1 for proper motion
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
        sys.stdout = StringIO()
        if self.project.validate(verbose=True):
            full_msg =  sys.stdout.getvalue()[:-1]
            sys.stdout.close()
            sys.stdout = sys.__stdout__
            if confirmValid:
                wx.MessageBox('Congratulations, you have a valid set of scans.', 'Validator Results')
            return True
        else:
            full_msg =  sys.stdout.getvalue()[:-1]
            sys.stdout.close()
            sys.stdout = sys.__stdout__
            
            msg_lines = full_msg.split('\n')
            for msg in msg_lines:
                if msg.find('Error') != -1:
                    pid_print(msg)
                    
            if validObs:
                wx.MessageBox('All scans are valid, but there are errors in the run setup.  See the command standard output for details.', 'Validator Results')
            return False
            
    def onResolve(self, event):
        """
        Display a window to resolve a target name to ra/dec coordinates.
        """
        
        ResolveTarget(self)
        
    def onSearch(self, event):
        """
        Launch an instance of the calibrator search tool.
        """
        
        whichChecked = None
        for i in range(self.listControl.GetItemCount()):
            if self.listControl.IsChecked(i):
                whichChecked = i
                break
                
        SearchWindow(self, whichChecked)
        
    def onTimeseries(self, event):
        """
        Display a window showing the layout of the scans in time.
        """
        
        RunDisplay(self)
        
    def onUVCoverage(self, event):
        """
        Display a window showing the UV coverage of each source.
        """
        
        RunUVCoverageDisplay(self)
        
    def onAdvanced(self, event):
        """
        Display the advanced settings dialog for controlling TBW samples and
        data return method.
        """
        
        AdvancedInfo(self)
        
    def onVolume(self, event):
        """
        Display a message window showing the data used for each scan 
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
                return float(value), 'Hz'
                
        filterInfo = "DRX"
        for dk,dv in DRXFilters.items():
            if dk > 7:
                continue
            dv, du = units(dv)
            filterInfo = f"{filterInfo}\n{dk}  {dv:.3f} {du:-3s}"
            
        wx.MessageBox(filterInfo, 'Filter Codes')
        
    def onAbout(self, event):
        """
        Display a ver very very brief 'about' window.
        """
        
        dialog = wx.AboutDialogInfo()
        
        dialog.SetIcon(wx.Icon(os.path.join(self.scriptPath, 'icons', 'lwa.png'), wx.BITMAP_TYPE_PNG))
        dialog.SetName('Swarm GUI')
        dialog.SetVersion(__version__)
        dialog.SetDescription("""GUI for creating interferometer definition files to define scans with the Long Wavelength Array.\n\nLSL Version: %s""" % lsl.version.version)
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
            dialog = wx.MessageDialog(self, 'The current interferometer defintion file has changes that have not been saved.\n\nExit anyways?', 'Confirm Quit', style=wx.YES_NO|wx.NO_DEFAULT|wx.ICON_QUESTION)
            
            if dialog.ShowModal() == wx.ID_YES:
                self.Destroy()
            else:
                pass
        else:
            self.Destroy()
            
    def addColumns(self):
        """
        Add the various columns to the main window based on the type of 
        scans being defined.
        """
        
        def intentConv(text):
            """
            Special conversion function for dealing with the scan intent.
            """
            
            if text.lower().strip() not in ('fluxcal', 'phasecal', 'target'):
                raise ValueError(f"Scan intent of '{text}' is not one of 'FluxCal', 'PhaseCal', or 'Target'")
            return text
            
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
            
            if value < 0 or value >= 24:
                raise ValueError("RA value must be 0 <= RA < 24")
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
                raise ValueError(f"Frequency of {value/1e6:.6f} MHz is out of the DP tuning range")
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
                
        def pmConv(text):
            """
            Special conversion function for dealing with proper motion pairs.
            """
            
            try:
                text = text.replace("---", "0.0")
                ra, dec = [float(v) for v in text.split(None, 1)]
            except (IndexError, TypeError):
                raise ValueError("Proper motion must be a space-separated pair of float values")
            return [ra, dec]
            
        width = 50 + 100 + 100 + 100 + 235 + 125 + 150 + 150 + 125 + 125 + 85
        self.columnMap = []
        self.coerceMap = []
        
        self.listControl.InsertColumn(0, 'ID', width=50)
        self.listControl.InsertColumn(1, 'Target', width=100)
        self.listControl.InsertColumn(2, 'Intent', width=100)
        self.listControl.InsertColumn(3, 'Comments', width=100)
        self.listControl.InsertColumn(4, 'Start (UTC)', width=235)
        self.listControl.InsertColumn(5, 'Duration', width=125)
        self.listControl.InsertColumn(6, 'RA (Hour J2000)', width=150)
        self.listControl.InsertColumn(7, 'Dec (Deg. J2000)', width=150)
        self.listControl.InsertColumn(8, 'Tuning 1 (MHz)', width=125)
        self.listControl.InsertColumn(9, 'Tuning 2 (MHz)', width=125)
        self.listControl.InsertColumn(10, 'Filter Code', width=85)
        # There is no "self.listControl.InsertColumn(11, ...)" since this 
        # is meant to store the proper motion data that is accessable from
        # a seperate window.
        self.columnMap.append('id')
        self.columnMap.append('target')
        self.columnMap.append('intent')
        self.columnMap.append('comments')
        self.columnMap.append('start')
        self.columnMap.append('duration')
        self.columnMap.append('ra')
        self.columnMap.append('dec')
        self.columnMap.append('frequency1')
        self.columnMap.append('frequency2')
        self.columnMap.append('filter')
        self.columnMap.append('pm')         # For proper motion
        self.coerceMap.append(str)
        self.coerceMap.append(str)
        self.coerceMap.append(intentConv)
        self.coerceMap.append(str)
        self.coerceMap.append(str)
        self.coerceMap.append(str)
        self.coerceMap.append(raConv)
        self.coerceMap.append(decConv)
        self.coerceMap.append(freqConv)
        self.coerceMap.append(freqConv)
        self.coerceMap.append(filterConv)
        self.coerceMap.append(pmConv)       # For proper motion
        
        size = self.listControl.GetSize()
        size[0] = width
        self.listControl.SetMinSize(size)
        self.listControl.Fit()
        
        size = self.GetSize()
        width = min([width, wx.GetDisplaySize()[0]])
        self.SetMinSize((width, size[1]))
        self.panel.SetupScrolling(scroll_x=True, scroll_y=False)
        self.Fit()
        
    def addScan(self, obs, id, update=False):
        """
        Add an scan to a particular location in the scan list
        
        .. note::
            This only updates the list visible on the screen, not the SD list
            stored in self.project
        """
        
        listIndex = id
        
        if not update:
            index = InsertListItem(self.listControl, listIndex, str(id))
        else:
            index = listIndex
        SetListItem(self.listControl, index, 1, obs.target)
        SetListItem(self.listControl, index, 2, obs.intent)
        if obs.comments is not None:
            SetListItem(self.listControl, index, 3, obs.comments)
        else:
            SetListItem(self.listControl, index, 3, 'None provided')
        SetListItem(self.listControl, index, 4, obs.start)
        
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
            obs.duration
            SetListItem(self.listControl, index, 5, obs.duration)
            SetListItem(self.listControl, index, 8, "--")
            SetListItem(self.listControl, index, 9, "--")
            SetListItem(self.listControl, index, 11, "--")
        else:
            SetListItem(self.listControl, index, 5, obs.duration)
            SetListItem(self.listControl, index, 8, "%.6f" % (obs.freq1*fS/2**32 / 1e6))
            SetListItem(self.listControl, index, 9, "%.6f" % (obs.freq2*fS/2**32 / 1e6))
            
        if obs.mode == 'TRK_SOL':
            SetListItem(self.listControl, index, 6, "Sun")
            SetListItem(self.listControl, index, 7, "--")
        elif obs.mode == 'TRK_JOV':
            SetListItem(self.listControl, index, 6, "Jupiter")
            SetListItem(self.listControl, index, 7, "--")
        elif obs.mode == 'STEPPED':
            SetListItem(self.listControl, index, 6, "STEPPED")
            SetListItem(self.listControl, index, 7, "RA/Dec" if obs.RADec else "Az/Alt")
        else:
            SetListItem(self.listControl, index, 6, dec2sexstr(obs.ra, signed=False))
            SetListItem(self.listControl, index, 7, dec2sexstr(obs.dec, signed=True))
        SetListItem(self.listControl, index, 10, "%i" % obs.filter)
        
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
        Given a mode of scan (TRK_RADEC, TRK_SOL, etc.), update the 
        various menu items in 'Scans' and the toolbar buttons.
        """
        
        mode = mode.lower()
        
        if mode[0:3] == 'trk' or mode[0:3] == 'drx':
            self.obsmenu['drx-radec'].Enable(True)
            self.obsmenu['drx-solar'].Enable(True)
            self.obsmenu['drx-jovian'].Enable(True)
            #self.obsmenu['steppedRADec'].Enable(True)
            #self.obsmenu['steppedAzAlt'].Enable(True)
            #self.obsmenu['steppedEdit'].Enable(False)
            
            self.toolbar.EnableTool(ID_ADD_DRX_RADEC,  True)
            self.toolbar.EnableTool(ID_ADD_DRX_SOLAR,  True)
            self.toolbar.EnableTool(ID_ADD_DRX_JOVIAN, True)
            #self.toolbar.EnableTool(ID_ADD_STEPPED_RADEC, True)
            #self.toolbar.EnableTool(ID_ADD_STEPPED_AZALT, True)
            #self.toolbar.EnableTool(ID_EDIT_STEPPED, False)
        else:
            self.obsmenu['drx-radec'].Enable(False)
            self.obsmenu['drx-solar'].Enable(False)
            self.obsmenu['drx-jovian'].Enable(False)
            #self.obsmenu['steppedRADec'].Enable(False)
            #self.obsmenu['steppedAzAlt'].Enable(False)
            #self.obsmenu['steppedEdit'].Enable(False)
            
            self.toolbar.EnableTool(ID_ADD_DRX_RADEC,  False)
            self.toolbar.EnableTool(ID_ADD_DRX_SOLAR,  False)
            self.toolbar.EnableTool(ID_ADD_DRX_JOVIAN, False)
            #self.toolbar.EnableTool(ID_ADD_STEPPED_RADEC, False)
            #self.toolbar.EnableTool(ID_ADD_STEPPED_AZALT, False)
            #self.toolbar.EnableTool(ID_EDIT_STEPPED, False)
            
    def parseFile(self, filename):
        """
        Given a filename, parse the file using the idf.parse_idf() method and 
        update all of the various aspects of the GUI (scan list, mode, 
        button, menu items, etc.).
        """
        
        self.listControl.DeleteAllItems()
        self.listControl.DeleteAllColumns()
        self.listControl.nSelected = 0
        self.listControl.setCheckDependant()
        self.initIDF()
        
        pid_print(f"Parsing file '{filename}'")
        try:
            self.project = idf.parse_idf(filename)
        except Exception as e:
            raise RuntimeError(f"Cannot parse provided IDF: {str(e)}")
        if len(self.project.runs) == 0:
            raise RuntimeError("Provided IDF does not define any runs")
        if len(self.project.runs[0].scans) == 0:
            raise RuntimeError("Provided IDF does not define any scans")
        self.setMenuButtons(self.project.runs[0].scans[0].mode)
        if self.project.runs[0].scans[0].mode[0:3] == 'TRK':
            self.mode = 'DRX'
        elif self.project.runs[0].scans[0].mode == 'STEPPED':
            self.mode = 'DRX'
        else:
            pass
            
        self.project.runs[0].drxGain = self.project.runs[0].scans[0].gain
        
        self.addColumns()
        id = 1
        for obs in self.project.runs[0].scans:
            self.addScan(obs, id)
            id += 1
            
    def displayError(self, error, details=None, title=None):
        """
        Display an error dialog and write an error message to the command 
        line if requested.
        """
        if title is None:
            title = 'An Error has Occured'
            
        if details is None:
            pid_print(f"Error: {str(error)}")
            self.statusbar.SetStatusText(f"Error: {str(error)}")
            dialog = wx.MessageDialog(self, str(error), title, style=wx.OK|wx.ICON_ERROR)
        else:
            pid_print(f"Error: {str(details)}")
            self.statusbar.SetStatusText(f"Error: {str(details)}")
            dialog = wx.MessageDialog(self, f"{str(error)}\n\nDetails:\n{str(details)}", title, style=wx.OK|wx.ICON_ERROR)
            
        dialog.ShowModal()


ID_OBS_INFO_DRSPEC = 211
ID_OBS_INFO_OK = 212
ID_OBS_INFO_CANCEL = 213
ID_OBS_INFO_DEFAULTS = 214

_cleanup0RE = re.compile(r';;(;;)+')
_cleanup1RE = re.compile(r'^;;')

class ObserverInfo(wx.Frame):
    """
    Class to hold information about the observer (name, ID), the current project 
    (title, ID), and what type of run this will be (DRX, SOL, etc.).
    """
    
    def __init__(self, parent):
        wx.Frame.__init__(self, parent, title='Observer Information')
        
        self.parent = parent
        
        self.initUI()
        self.initEvents()
        self.Show()
        
    def initUI(self):
        row = 0
        panel = ScrolledPanel(self)
        sizer = wx.GridBagSizer(0, 0)
        
        font = wx.SystemSettings.GetFont(wx.SYS_SYSTEM_FONT)
        font.SetPointSize(font.GetPointSize()+2)
        
        #
        # Preferences File
        #
        
        preferences = {}
        try:
            with open(os.path.join(os.path.expanduser('~'), '.sessionGUI')) as ph:
                pl = ph.readlines()
                
            preferences = {}
            for line in pl:
                line = line.replace('\n', '')
                if len(line) < 3:
                    continue
                if line[0] == '#':
                    continue
                key, value = line.split(None, 1)
                preferences[key] = value
        except:
            pass
            
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
        else:
            try:
                oidText.SetValue(preferences['ObserverID'])
            except KeyError:
                pass
        if self.parent.project.observer.first != '':
            fnameText.SetValue(self.parent.project.observer.first)
            lnameText.SetValue(self.parent.project.observer.last)
        else:
            fnameText.SetValue(self.parent.project.observer.name)
            if self.parent.project.observer.name == '':
                try:
                    fnameText.SetValue(preferences['ObserverFirstName'])
                    lnameText.SetValue(preferences['ObserverLastName'])
                except KeyError:
                    pass
                    
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
        else:
            try:
                pidText.SetValue(preferences['ProjectID'])
            except KeyError:
                pass
        if self.parent.project.name != '':
            pnameText.SetValue(self.parent.project.name)
        else:
            try:
                pnameText.SetValue(preferences['ProjectName'])
            except KeyError:
                pass
        if self.parent.project.comments != '' and self.parent.project.comments is not None:
            pcomsText.SetValue(self.parent.project.comments.replace(';;', '\n'))
        
        sizer.Add(prj, pos=(row+0,0), span=(1,6), flag=wx.ALIGN_CENTER, border=5)
        
        sizer.Add(pid, pos=(row+1, 0), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
        sizer.Add(pidText, pos=(row+1, 1), span=(1, 5), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
        
        sizer.Add(pname, pos=(row+2, 0), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
        sizer.Add(pnameText, pos=(row+2, 1), span=(1, 5), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
        sizer.Add(pcoms, pos=(row+3, 0), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
        sizer.Add(pcomsText, pos=(row+3, 1), span=(3, 5), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
        
        line = wx.StaticLine(panel)
        sizer.Add(line, pos=(row+6, 0), span=(1, 6), flag=wx.EXPAND|wx.BOTTOM, border=10)
        
        row += 7
        
        #
        # Run-Wide Info
        #
        
        ses = wx.StaticText(panel, label='Run Information')
        ses.SetFont(font)
        
        sid = wx.StaticText(panel, label='ID Number')
        sname = wx.StaticText(panel, label='Title')
        scoms = wx.StaticText(panel, label='Comments')
        
        sidText = wx.TextCtrl(panel)
        snameText = wx.TextCtrl(panel)
        scomsText = wx.TextCtrl(panel, style=wx.TE_MULTILINE)
        if self.parent.project.runs[0].id != '':
            sidText.SetValue(str(self.parent.project.runs[0].id))
        if self.parent.project.runs[0].name != '':
            snameText.SetValue(self.parent.project.runs[0].name)
        if self.parent.project.runs[0].comments != '' and self.parent.project.runs[0].comments is not None:
            scomsText.SetValue(idf.UCF_USERNAME_RE.sub('', self.parent.project.runs[0].comments).replace(';;', '\n'))
        
        cid = wx.StaticText(panel, label='Correlator Setup')
        nchn = wx.StaticText(panel, label='Channels')
        nchnText = wx.TextCtrl(panel)
        tint = wx.StaticText(panel, label='Int. Time')
        tintText = wx.TextCtrl(panel)
        spid = wx.StaticText(panel, label='Data Products')
        linear = wx.RadioButton(panel, -1, 'Linear', style=wx.RB_GROUP)
        circul = wx.RadioButton(panel, -1, 'Circular')
        stokes = wx.RadioButton(panel, -1, 'Stokes')
        if self.parent.project.runs[0].corr_channels:
            nchnText.SetValue(str(self.parent.project.runs[0].corr_channels))
        if self.parent.project.runs[0].corr_inttime:
            tintText.SetValue(str(self.parent.project.runs[0].corr_inttime))
        if self.parent.project.runs[0].corr_basis:
            basis = self.parent.project.runs[0].corr_basis
            if basis == 'linear':
                linear.SetValue(True)
                circul.SetValue(False)
                stokes.SetValue(False)
            elif basis == 'circular':
                linear.SetValue(False)
                circul.SetValue(True)
                stokes.SetValue(False)
            else:
                linear.SetValue(False)
                circul.SetValue(False)
                stokes.SetValue(True)
        stokes.Disable()
        
        did = wx.StaticText(panel, label='Data Return Method')
        usbRB  = wx.RadioButton(panel, -1, 'Bare Drive(s)', style=wx.RB_GROUP)
        ucfRB  = wx.RadioButton(panel, -1, 'Copy to UCF')
        
        unam = wx.StaticText(panel, label='UCF Username:')
        unamText = wx.TextCtrl(panel)
        unamText.Disable()
        
        if self.parent.project.runs[0].data_return_method == 'USB Harddrives':
            usbRB.SetValue(True)
            ucfRB.SetValue(False)
            
        else:
            usbRB.SetValue(False)
            ucfRB.SetValue(True)
            
            mtch = None
            if self.parent.project.runs[0].comments is not None:
                mtch = idf.UCF_USERNAME_RE.search(self.parent.project.runs[0].comments)
            if mtch is not None:
                unamText.SetValue(mtch.group('username'))
            unamText.Enable()
            
        sizer.Add(ses, pos=(row+0, 0), span=(1,6), flag=wx.ALIGN_CENTER, border=5)
        
        sizer.Add(sid, pos=(row+1, 0), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
        sizer.Add(sidText, pos=(row+1, 1), span=(1, 5), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
        
        sizer.Add(sname, pos=(row+2, 0), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
        sizer.Add(snameText, pos=(row+2, 1), span=(1, 5), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
        sizer.Add(scoms, pos=(row+3, 0), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
        sizer.Add(scomsText, pos=(row+3, 1), span=(3, 5), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
        
        sizer.Add(cid, pos=(row+6,0), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
        sizer.Add(nchn, pos=(row+6,2), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
        sizer.Add(nchnText, pos=(row+6,3), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
        sizer.Add(tint, pos=(row+6,4), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
        sizer.Add(tintText, pos=(row+6,5), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
        sizer.Add(spid, pos=(row+7,2), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
        sizer.Add(linear, pos=(row+7,3), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
        sizer.Add(circul, pos=(row+7,4), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
        sizer.Add(stokes, pos=(row+7,5), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
        
        sizer.Add(did, pos=(row+8,0), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
        sizer.Add(usbRB, pos=(row+8,1), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
        sizer.Add(ucfRB, pos=(row+9,1), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
        sizer.Add(unam, pos=(row+9,2), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
        sizer.Add(unamText, pos=(row+9,3), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
        
        line = wx.StaticLine(panel)
        sizer.Add(line, pos=(row+11, 0), span=(1, 6), flag=wx.EXPAND|wx.BOTTOM, border=10)
        
        row += 12
        
        #
        # Buttons
        #
        
        ok = wx.Button(panel, ID_OBS_INFO_OK, 'Ok', size=(90, 28))
        cancel = wx.Button(panel, ID_OBS_INFO_CANCEL, 'Cancel', size=(90, 28))
        defaults = wx.Button(panel, ID_OBS_INFO_DEFAULTS, 'Save Defaults', size=(95, 28))
        sizer.Add(ok, pos=(row+0, 4), flag=wx.ALL, border=5)
        sizer.Add(cancel, pos=(row+0, 5), flag=wx.ALL, border=5)
        sizer.Add(defaults, pos=(row+0, 0), flag=wx.ALL, border=5)
        
        panel.SetupScrolling(scroll_x=True, scroll_y=True) 
        panel.SetSizer(sizer)
        sizer.Fit(self)
        
        #
        # Save the various widgets for access later
        #
        
        self.observerIDEntry = oidText
        self.observerFirstEntry = fnameText
        self.observerLastEntry = lnameText
        
        self.projectIDEntry = pidText
        self.projectTitleEntry = pnameText
        self.projectCommentsEntry = pcomsText
        
        self.runIDEntry = sidText
        self.runTitleEntry = snameText
        self.runCommentsEntry = scomsText
        self.usbButton = usbRB
        self.ucfButton = ucfRB
        self.unamText = unamText
        self.nchnText = nchnText
        self.tintText = tintText
        self.linear = linear
        self.circul = circul
        self.stokes = stokes
        
    def initEvents(self):
        self.Bind(wx.EVT_RADIOBUTTON, self.onRadioButtons)
        
        self.Bind(wx.EVT_BUTTON, self.onOK, id=ID_OBS_INFO_OK)
        self.Bind(wx.EVT_BUTTON, self.onCancel, id=ID_OBS_INFO_CANCEL)
        self.Bind(wx.EVT_BUTTON, self.onSaveDefaults, id=ID_OBS_INFO_DEFAULTS)
        
    def onRadioButtons(self, event):
        """
        Toggle the UCF username option.
        """
        
        if self.ucfButton.GetValue():
            self.unamText.Enable()
        else:
            self.unamText.Disable()
            
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
            junk = int(self.runIDEntry.GetValue())
            if junk < 1:
                self.displayError('Run ID must be greater than zero', title='Run ID Error')
                return False
        except ValueError as err:
            self.displayError('Run ID must be numeric', details=err, title='Run ID Error')
            return False
        
        self.parent.project.observer.id = int(self.observerIDEntry.GetValue())
        self.parent.project.observer.first = self.observerFirstEntry.GetValue()
        self.parent.project.observer.last = self.observerLastEntry.GetValue()
        self.parent.project.observer.join_name()
        
        self.parent.project.id = self.projectIDEntry.GetValue()
        self.parent.project.name = self.projectTitleEntry.GetValue()
        self.parent.project.comments = self.projectCommentsEntry.GetValue().replace('\n', ';;')
        
        self.parent.project.runs[0].id = int(self.runIDEntry.GetValue())
        self.parent.project.runs[0].name = self.runTitleEntry.GetValue()
        self.parent.project.runs[0].comments = self.runCommentsEntry.GetValue().replace('\n', ';;')
        
        self.parent.project.runs[0].corr_channels = int(self.nchnText.GetValue(), 10)
        self.parent.project.runs[0].corr_inttime = float(self.tintText.GetValue())
        if self.linear.GetValue():
            self.parent.project.runs[0].corr_basis = 'linear'
        elif self.circul.GetValue():
            self.parent.project.runs[0].corr_basis = 'circular'
        else:
            self.parent.project.runs[0].corr_basis = 'stokes'
            
        if self.usbButton.GetValue():
            self.parent.project.runs[0].data_return_method = 'USB Harddrives'
        else:
            self.parent.project.runs[0].data_return_method = 'UCF'
            tempc = idf.UCF_USERNAME_RE.sub('', self.parent.project.runs[0].comments)
            self.parent.project.runs[0].comments = tempc + ';;ucfuser:%s' % self.unamText.GetValue()
            
            mtch = idf.UCF_USERNAME_RE.search(self.parent.project.runs[0].comments)
            if mtch is None:
                self.displayError('Cannot find UCF username needed for copying data to the UCF.', title='Missing UCF User Name')
                return False
                
        self.parent.mode = 'DRX'
        self.parent.setMenuButtons(self.parent.mode)
        if self.parent.listControl.GetColumnCount() == 0:
            self.parent.addColumns()
            
        # Cleanup the comments
        self.parent.project.comments = _cleanup0RE.sub(';;', self.parent.project.comments)
        self.parent.project.comments = _cleanup1RE.sub('', self.parent.project.comments)
        self.parent.project.runs[0].comments = _cleanup0RE.sub(';;', self.parent.project.runs[0].comments)
        self.parent.project.runs[0].comments = _cleanup1RE.sub('', self.parent.project.runs[0].comments)
        
        self.parent.edited = True
        self.parent.setSaveButton()
        
        self.Close()
        
    def onCancel(self, event):
        self.Close()
        
    def onSaveDefaults(self, event):
        preferences = {}
        try:
            with open(os.path.join(os.path.expanduser('~'), '.sessionGUI')) as ph:
                pl = ph.readlines()
                
            preferences = {}
            for line in pl:
                line = line.replace('\n', '')
                if len(line) < 3:
                    continue
                if line[0] == '#':
                    continue
                key, value = line.split(None, 1)
                preferences[key] = value
        except:
            pass
            
        try:
            preferences['ObserverID'] = int(self.observerIDEntry.GetValue())
        except (TypeError, ValueError):
            pass
        first = self.observerFirstEntry.GetValue()
        if len(first):
            preferences['ObserverFirstName'] = first
        last = self.observerLastEntry.GetValue()
        if len(last):
            preferences['ObserverLastName'] = last
        pID = self.projectIDEntry.GetValue()
        if len(pID):
            preferences['ProjectID'] = pID
        pTitle = self.projectTitleEntry.GetValue()
        if len(pTitle):
            preferences['ProjectName'] = pTitle
            
        with open(os.path.join(os.path.expanduser('~'), '.sessionGUI'), 'w') as ph:
            for key in preferences:
                ph.write(f"{key:-24s} {str(preferences[key])}\n")
                
    def displayError(self, error, details=None, title=None):
        """
        Display an error dialog and write an error message to the command 
        line if requested.
        """
        if title is None:
            title = 'An Error has Occured'
            
        if details is None:
            pid_print(f"Error: {str(error)}")
            dialog = wx.MessageDialog(self, str(error), title, style=wx.OK|wx.ICON_ERROR)
        else:
            pid_print(f"Error: {str(details)}")
            dialog = wx.MessageDialog(self, f"{str(error)}\n\nDetails:\n{str(details)}", title, style=wx.OK|wx.ICON_ERROR)
            
        dialog.ShowModal()


ID_ADV_INFO_OK = 312
ID_ADV_INFO_CANCEL = 313
ID_STATION_CHECKED = 314

class AdvancedInfo(wx.Frame):
    def __init__(self, parent):
        wx.Frame.__init__(self, parent, title='Advanced Settings')
        
        self.parent = parent
        
        self.initUI()
        self.initEvents()
        self.Show()
        
    def initUI(self):
        drxGain = [str(i) for i in range(13)]
        drxGain.insert(0, 'MCS Decides')
        aspFilters = ['MCS Decides', 'Split', 'Full', 'Reduced', 'Off', 'Split @ 3MHz', 'Full @ 3MHz']
        
        row = 0
        panel = ScrolledPanel(self)
        sizer = wx.GridBagSizer(0, 0)
        
        font = wx.SystemSettings.GetFont(wx.SYS_SYSTEM_FONT)
        font.SetPointSize(font.GetPointSize()+2)
        
        #
        # Stations
        #
        
        swm = wx.StaticText(panel, label='Interferometer Information')
        swm.SetFont(font)
        
        sta = wx.StaticText(panel, label='Stations')
        
        sizer.Add(swm, pos=(row+0, 0), span=(1,6), flag=wx.ALIGN_CENTER, border=5)
        sizer.Add(sta, pos=(row+1, 0), span=(1,2), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
        i = 0
        j = 2
        staChecks = []
        for station in stations.get_all_stations():
            staCheck = wx.CheckBox(panel, ID_STATION_CHECKED, label=station.name)
            if station in self.parent.project.runs[0].stations:
                staCheck.SetValue(True)
            else:
                staCheck.SetValue(True)
            sizer.Add(staCheck, pos=(row+1+i,j), span=(1,2), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
            staChecks.append((station.name,staCheck))
            
            j += 2
            if j >= 6:
                j = 2
                i += 1
        for dummy in ('OVROLWA',):
            staCheck = wx.CheckBox(panel, ID_STATION_CHECKED, label=dummy)
            staCheck.Enable(False)
            sizer.Add(staCheck, pos=(row+1+i,j), span=(1,2), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
            
            j += 2
            if j >= 6:
                j = 2
                i += 1
        line = wx.StaticLine(panel)
        sizer.Add(line, pos=(row+i+1, 0), span=(1, 6), flag=wx.EXPAND|wx.BOTTOM, border=10)
        
        row += 3+i
        
        #
        # ASP
        # 
        
        aspComboFlt = wx.ComboBox(panel, -1, value='MCS Decides', choices=aspFilters, style=wx.CB_READONLY)
        try:
            if self.parent.project.runs[0].scans[0].asp_filter == -1:
                aspComboFlt.SetStringSelection('MCS Decides')
            elif self.parent.project.runs[0].scans[0].asp_filter == 0:
                aspComboFlt.SetStringSelection('Split')
            elif self.parent.project.runs[0].scans[0].asp_filter == 1:
                aspComboFlt.SetStringSelection('Full')
            elif self.parent.project.runs[0].scans[0].asp_filter == 2:
                aspComboFlt.SetStringSelection('Reduced')
            elif self.parent.project.runs[0].scans[0].asp_filer == 4:
                aspComboFlt.SetStringSelection('Split @ 3MHz')
            elif self.parent.project.runs[0].scans[0].aspFlt == 5:
                aspComboFlt.SetStringSelection('Full @ 3MHz')
            else:
                aspComboFlt.SetStringSelection('Off')
                
        except IndexError:
            aspComboFlt.SetStringSelection('MCS Decides')
            
        asp = wx.StaticText(panel, label='ASP-Specific Information')
        asp.SetFont(font)
        
        flt = wx.StaticText(panel, label='Filter Mode Setting')
        fas1 = wx.StaticText(panel, label='for all inputs')
        
        sizer.Add(asp, pos=(row+0, 0), span=(1,6), flag=wx.ALIGN_CENTER, border=5)
        sizer.Add(flt, pos=(row+1, 0), span=(1, 2), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
        sizer.Add(aspComboFlt, pos=(row+1, 2), span=(1, 2), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
        sizer.Add(fas1, pos=(row+1, 4), span=(1, 2), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
        
        line = wx.StaticLine(panel)
        sizer.Add(line, pos=(row+2, 0), span=(1, 6), flag=wx.EXPAND|wx.BOTTOM, border=10)
        
        row += 3
        
        #
        # DRX
        #
        
        drx = wx.StaticText(panel, label='DRX-Specific Information')
        drx.SetFont(font)
        
        dgain = wx.StaticText(panel, label='Gain')
        dgainText =  wx.ComboBox(panel, -1, value='MCS Decides', choices=drxGain, style=wx.CB_READONLY)
        if len(self.parent.project.runs[0].scans) == 0 \
           or self.parent.project.runs[0].scans[0].gain == -1:
            dgainText.SetStringSelection('MCS Decides')
        else:
            dgainText.SetStringSelection('%i' % self.parent.project.runs[0].scans[0].gain)
        gainHelpIcon = wx.Bitmap(os.path.join(self.parent.scriptPath, 'icons', 'tooltip.png'))
        self.gainHelp = wx.StaticBitmap(panel, bitmap=gainHelpIcon)
        self.gainHelpText = "The 'MCS Decides' value is 6.  Smaller values represent higher gains."
        
        sizer.Add(drx, pos=(row+0,0), span=(1,6), flag=wx.ALIGN_CENTER, border=5)
        
        sizer.Add(dgain, pos=(row+1, 0), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
        sizer.Add(dgainText, pos=(row+1, 1), span=(1, 1), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=2)
        sizer.Add(self.gainHelp, pos=(row+1, 2), span=(1, 1), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
        
        line = wx.StaticLine(panel)
        sizer.Add(line, pos=(row+2, 0), span=(1, 6), flag=wx.EXPAND|wx.BOTTOM, border=10)
        
        row += 3
            
        #
        # Buttons
        #
        
        ok = wx.Button(panel, ID_OBS_INFO_OK, 'Ok', size=(90, 28))
        cancel = wx.Button(panel, ID_OBS_INFO_CANCEL, 'Cancel', size=(90, 28))
        sizer.Add(ok, pos=(row+0, 4), flag=wx.ALL, border=5)
        sizer.Add(cancel, pos=(row+0, 5), flag=wx.ALL, border=5)
        
        panel.SetupScrolling(scroll_x=True, scroll_y=True) 
        panel.SetSizer(sizer)
        sizer.Fit(self)
        
        #
        # Save the various widgets for access later
        #
        
        self.gain = dgainText
        self.aspFlt = aspComboFlt
        self.staChecks = staChecks
        
    def initEvents(self):
        self.Bind(wx.EVT_BUTTON, self.onOK, id=ID_OBS_INFO_OK)
        self.Bind(wx.EVT_BUTTON, self.onCancel, id=ID_OBS_INFO_CANCEL)
        try:
            self.gainHelp.Bind(wx.EVT_MOTION, self.onMouseOver)
        except AttributeError:
            pass
            
    def onMouseOver(self, event):
        """
        Show the gain help text to help users sort out our gains.
        """
        
        self.gainHelp.SetToolTipString(self.gainHelpText)
        
    def onOK(self, event):
        """
        Save everything into all of the correct places.
        """
        
        # Stations
        new_stations = []
        for station in stations.get_all_stations():
            for name,staCheck in self.staChecks:
                if station.name == name:
                    if staCheck.GetValue():
                        new_stations.append(station)
        if len(new_stations) < 2:
            self.displayError(f"Invalid Station Selection: only {len(new_stations)} selected", details='Must be two or more', 
                              title='Interferometer Setup Error')
            return False
        self.parent.project.runs[0].stations = new_stations
        
        # ASP
        aspFltDict = {'MCS Decides': -1, 'Split': 0, 'Full': 1, 'Reduced': 2, 'Off': 3, 
                                         'Split @ 3MHz': 4, 'Full @ 3MHz': 5}
        aspFlt = aspFltDict[self.aspFlt.GetValue()]
        for i in range(len(self.parent.project.runs[0].scans)):
            self.parent.project.runs[0].scans[i].asp_filter = aspFlt
            
        # DRX
        self.parent.project.runs[0].drxGain = self.__parseGainCombo(self.gain)
        for i in range(len(self.parent.project.runs[0].scans)):
            self.parent.project.runs[0].scans[i].gain = self.__parseGainCombo(self.gain)
            
        for obs in self.parent.project.runs[0].scans:
            for i in range(len(self.parent.project.runs[0].scans)):
                obs.gain = self.parent.project.runs[0].scans[i].gain
                
        self.parent.edited = True
        self.parent.setSaveButton()
        
        self.Close()
        
    def onCancel(self, event):
        self.Close()
        
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
        
    def displayError(self, error, details=None, title=None):
        """
        Display an error dialog and write an error message to the command 
        line if requested.
        """
        if title is None:
            title = 'An Error has Occured'
            
        if details is None:
            pid_print(f"Error: {str(error)}")
            dialog = wx.MessageDialog(self, str(error), title, style=wx.OK|wx.ICON_ERROR)
        else:
            pid_print(f"Error: {str(details)}")
            dialog = wx.MessageDialog(self, f"{str(error)}\n\nDetails:\n{str(details)}", title, style=wx.OK|wx.ICON_ERROR)
            
        dialog.ShowModal()


class RunDisplay(wx.Frame):
    """
    Window for displaying the "Run at a Glance".
    """
    
    def __init__(self, parent):
        wx.Frame.__init__(self, parent, title='Run at a Glance', size=(800, 375))
        
        self.parent = parent
        
        self.initUI()
        self.initEvents()
        self.Show()
        
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
        vbox1.Add(self.canvas,  1, wx.ALIGN_LEFT | wx.EXPAND)
        vbox1.Add(self.toolbar, 0, wx.ALIGN_LEFT)
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
        Test function to plot source altitude for the scans.
        """
        
        self.obs = self.parent.project.runs[0].scans
        
        if len(self.obs) == 0:
            return False
        
        ## Find the earliest scan
        self.earliest = conflict.unravelObs(self.obs)[0][0]
        
        self.figure.clf()
        self.ax1 = self.figure.gca()
        self.ax2 = self.ax1.twiny()
        
        i = 0
        station_colors = {}
        for o in self.obs:
            ## Get the source
            src = o.fixed_body
            
            stepSize = o.dur / 1000.0 / 300
            if stepSize < 30.0:
                stepSize = 30.0
                
            ## Find its altitude over the course of the scan
            j = 0
            for station in self.parent.project.runs[0].stations:
                t = []
                alt = []
                dt = 0.0
                
                ## The actual scans
                observer = station.get_observer()
                
                while dt < o.dur/1000.0:
                    observer.date = o.mjd + (o.mpm/1000.0 + dt)/3600/24.0 + MJD_OFFSET - DJD_OFFSET
                    src.compute(observer)
                    
                    alt.append( float(src.alt) * 180.0 / math.pi )
                    t.append( o.mjd + (o.mpm/1000.0 + dt) / (3600.0*24.0) - self.earliest )
                    
                    dt += stepSize
                    
                ## Make sure we get the end of the scan
                dt = o.dur/1000.0
                observer.date = o.mjd + (o.mpm/1000.0 + dt)/3600/24.0 + MJD_OFFSET - DJD_OFFSET
                src.compute(observer)
                
                alt.append( float(src.alt) * 180.0 / math.pi )
                t.append( o.mjd + (o.mpm/1000.0 + dt) / (3600.0*24.0) - self.earliest )
                
                ## Plot the altitude over time
                try:
                    l, = self.ax1.plot(t, alt, label='%s - %s' % (o.target, station.id), color=station_colors[station])
                except KeyError:
                    l, = self.ax1.plot(t, alt, label='%s - %s' % (o.target, station.id))
                    station_colors[station] = l.get_color()
                    
                ## Draw the scan limits and label the source
                if j == 0:
                    self.ax1.vlines(o.mjd + o.mpm/1000.0 / (3600.0*24.0) - self.earliest, 0, 90, linestyle=':')
                    self.ax1.vlines(o.mjd + (o.mpm/1000.0 + o.dur/1000.0) / (3600.0*24.0) - self.earliest, 0, 90, linestyle=':')
                    
                    self.ax1.text(o.mjd + o.mpm/1000.0 / (3600.0*24.0) - self.earliest + o.dur/1000/3600/24.0*0.02, 2+10*(i%2), o.target, rotation='vertical')
                    
                j += 1
                
            i += 1
            
        ### The 50% and 25% effective area limits
        #xlim = self.ax1.get_xlim()
        #self.ax1.hlines(math.asin(0.50**(1/1.6))*180/math.pi, *xlim, linestyle='-.', label='50% A$_e$(90$^{\circ}$)')
        #self.ax1.hlines(math.asin(0.25**(1/1.6))*180/math.pi, *xlim, linestyle='-.', label='25% A$_e$(90$^{\circ}$)')
        #self.ax1.set_xlim(xlim)
        
        ## Add a legend
        handles, labels = self.ax1.get_legend_handles_labels()
        labels = [l.rsplit(' -', 1)[1] for l in labels]
        self.ax1.legend(handles[:len(self.parent.project.runs[0].stations)], labels[:len(self.parent.project.runs[0].stations)], loc=0)
        
        ## Second set of x axes
        self.ax1.xaxis.tick_bottom()
        self.ax1.set_ylim([0, 90])
        self.ax2.xaxis.tick_top()
        self.ax2.set_xlim([self.ax1.get_xlim()[0]*24.0, self.ax1.get_xlim()[1]*24.0])
        
        ## Labels
        self.ax1.set_xlabel('MJD-%i [days]' % self.earliest)
        self.ax1.set_ylabel('Altitude [deg.]')
        self.ax2.set_xlabel('Run Elapsed Time [hours]')
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
            
            # Compute the run elapsed time
            elapsed = clickX*3600.0
            eHour = elapsed / 3600
            eMinute = (elapsed % 3600) / 60
            eSecond = (elapsed % 3600) % 60
            
            elapsed = "%02i:%02i:%06.3f" % (eHour, eMinute, eSecond)
            
            self.statusbar.SetStatusText("MJD: %i  MPM: %i;  Run Elapsed Time: %s" % (mjd, mpm, elapsed))
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
        # Get the current size of the window and the navigation toolbar
        w, h = self.GetClientSize()
        wt, ht = self.toolbar.GetSize()
        
        dpi = self.figure.get_dpi()
        newW = 1.0*w/dpi
        newH = 1.0*(h-ht)/dpi
        self.figure.set_size_inches((newW, newH))
        self.figure.tight_layout()
        self.figure.canvas.draw()
        
    def GetToolBar(self):
        # You will need to override GetToolBar if you are using an 
        # unmanaged toolbar in your frame
        return self.toolbar



class RunUVCoverageDisplay(wx.Frame):
    def __init__(self, parent):
        wx.Frame.__init__(self, parent, title='Run (u,v) Coverage', size=(800, 375))
        
        self.parent = parent
        
        self.initUI()
        self.initEvents()
        self.Show()
        
        wx.BeginBusyCursor()
        self.initPlot()
        wx.EndBusyCursor()
        
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
        vbox1.Add(self.canvas,  1, wx.ALIGN_LEFT | wx.EXPAND)
        vbox1.Add(self.toolbar, 0, wx.ALIGN_LEFT)
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
        Test function to plot source altitude for the scans.
        """
        
        self.obs = self.parent.project.runs[0].scans
        
        if len(self.obs) == 0:
            return False
        
        ## Find the earliest scan
        self.earliest = conflict.unravelObs(self.obs)[0][0]
        
        self.figure.clf()
        
        ## Build up the list of antennas to use for the UV coverage calculation
        antennas = []
        observer = stations.lwa1.get_observer()
        for station in self.parent.project.runs[0].stations:
            stand = stations.Stand(len(antennas), *(stations.lwa1.get_enz_offset(station)))
            cable = stations.Cable('%s-%s' % (station.id, 0), 0.0, vf=1.0, dd=0.0)
            antenna = stations.Antenna(len(antennas), stand=stand, cable=cable, pol=0)
            antennas.append(antenna)
            
        uv_coverage = {}
        i = 0
        order = []
        for o in self.obs:
            ## Get the source
            src = o.fixed_body
            if src.name not in order:
                order.append( src.name )
            try:
                uv_coverage[src.name+"@T1"]
            except KeyError:
                uv_coverage[src.name+"@T1"] = []
                uv_coverage[src.name+"@T2"] = []
                
            stepSize = o.dur / 1000.0 / 300
            if stepSize < 60.0:
                stepSize = 60.0
                
            ## Find the UV coverage across the run
            dt = 0.0
            while dt < o.dur/1000.0:
                observer.date = o.mjd + (o.mpm/1000.0 + dt)/3600/24.0 + MJD_OFFSET - DJD_OFFSET
                src.compute(observer)
                HA = (observer.sidereal_time() - src.ra) * 12/numpy.pi
                dec = src.dec * 180/numpy.pi
                
                uvw = uvutils.compute_uvw(antennas, HA=HA, dec=dec, freq=o.frequency1, site=observer, include_auto=False)
                uv_coverage[src.name+"@T1"].append( uvw/1e3 )
                            
                uvw = uvutils.compute_uvw(antennas, HA=HA, dec=dec, freq=o.frequency2, site=observer, include_auto=False)
                uv_coverage[src.name+"@T2"].append( uvw/1e3 )
                
                dt += stepSize
                
            ## Make sure we get the end of the scan
            dt = o.dur/1000.0
            observer.date = o.mjd + (o.mpm/1000.0 + dt)/3600/24.0 + MJD_OFFSET - DJD_OFFSET
            src.compute(observer)
            HA = (observer.sidereal_time() - src.ra) * 12/numpy.pi
            dec = src.dec * 180/numpy.pi
            
            uvw = uvutils.compute_uvw(antennas, HA=HA, dec=dec, freq=o.frequency1, site=observer, include_auto=False)
            uv_coverage[src.name+"@T1"].append( uvw/1e3 )
                        
            uvw = uvutils.compute_uvw(antennas, HA=HA, dec=dec, freq=o.frequency2, site=observer, include_auto=False)
            uv_coverage[src.name+"@T2"].append( uvw/1e3 )
            
            i += 1
            
        ## Plot
        nPlot = len(order)
        nRow = int(numpy.ceil(numpy.sqrt(nPlot)))
        nCol = int(numpy.ceil(nPlot/nRow))
        
        i = 0
        for name in order:
            key = name+'@T1'
            t1 = uv_coverage[key]
            t2 = uv_coverage[key.replace('@T1', '@T2')]
            
            ax = self.figure.add_subplot(nCol, nRow, i+1)
            for t in t1:
                ax.scatter( t[:,0,0],  t[:,1,0], marker='+', color='b')
                ax.scatter(-t[:,0,0], -t[:,1,0], marker='+', color='b')
            for t in t2:
                ax.scatter( t[:,0,0],  t[:,1,0], marker='+', color='g')
                ax.scatter(-t[:,0,0], -t[:,1,0], marker='+', color='g')
                
            ### Labels
            ax.set_xlabel('$u$ [k$\\lambda$]')
            ax.set_ylabel('$v$ [k$\\lambda$]')
            ax.set_title(key.replace('@T1', ''))
            
            i += 1
            
        ## Draw
        self.canvas.draw()
        
    def onCancel(self, event):
        self.Close()
        
    def resizePlots(self, event):
        # Get the current size of the window and the navigation toolbar
        w, h = self.GetClientSize()
        wt, ht = self.toolbar.GetSize()
        
        dpi = self.figure.get_dpi()
        newW = 1.0*w/dpi
        newH = 1.0*(h-ht)/dpi
        self.figure.set_size_inches((newW, newH))
        self.figure.tight_layout()
        self.figure.canvas.draw()
        
    def GetToolBar(self):
        # You will need to override GetToolBar if you are using an 
        # unmanaged toolbar in your frame
        return self.toolbar


ID_VOL_INFO_OK = 511

class VolumeInfo(wx.Frame):
    def __init__ (self, parent):
        wx.Frame.__init__(self, parent, title='Estimated Data Volume')
        
        self.parent = parent
        
        self.initUI()
        self.initEvents()
        self.Show()
        
    def initUI(self):
        row = 0
        panel = wx.Panel(self)
        sizer = wx.GridBagSizer(0, 0)
        
        font = wx.SystemSettings.GetFont(wx.SYS_SYSTEM_FONT)
        font.SetPointSize(font.GetPointSize()+2)
        
        dataText = wx.StaticText(panel, label='Estimated Data Volume:')
        dataText.SetFont(font)
        sizer.Add(dataText, pos=(row+0, 0), span=(1, 4), flag=wx.ALIGN_CENTER, border=5)
        
        line = wx.StaticLine(panel)
        sizer.Add(line, pos=(row+1, 0), span=(1, 4), flag=wx.EXPAND|wx.BOTTOM, border=10)
        
        row += 2
        
        rvText = wx.StaticText(panel, label='Raw:')
        cvText = wx.StaticText(panel, label='Final:')
        
        sizer.Add(rvText, pos=(row+0, 2), flag=wx.ALIGN_CENTER, border=5)
        sizer.Add(cvText, pos=(row+0, 3), flag=wx.ALIGN_RIGHT, border=5)
        
        row += 1
        
        scanCount = 1
        rawTotalData = 0
        corTotalData = 0.0
        for obs in self.parent.project.runs[0].scans:
            mode = obs.mode
            
            rawDataVolume = obs.dataVolume * len(self.parent.project.runs[0].stations)
            # 32-bit * real/complex * four polarization products
            # times channel count
            # times integration count
            # times baseline count
            # times padding for extra information in the output files
            corDataVolume = 32*2*4 \
                            * self.parent.project.runs[0].corr_channels \
                            * obs.dur/1000.0/self.parent.project.runs[0].corr_inttime \
                            * len(self.parent.project.runs[0].stations)*(len(self.parent.project.runs[0].stations)+1)/2 \
                            * 1.02
            
            idText = wx.StaticText(panel, label='Scan #%i' % scanCount)
            tpText = wx.StaticText(panel, label=mode)
            rvText = wx.StaticText(panel, label='%.2f GB' % (rawDataVolume/1024.0**3,))
            cvText = wx.StaticText(panel, label='%.2f MB' % (corDataVolume/1024.0**2,))
            
            sizer.Add(idText, pos=(row+0, 0), flag=wx.EXPAND|wx.LEFT|wx.RIGHT|wx.ALIGN_LEFT, border=5)
            sizer.Add(tpText, pos=(row+0, 1), flag=wx.EXPAND|wx.LEFT|wx.RIGHT|wx.ALIGN_CENTER, border=5)
            sizer.Add(rvText, pos=(row+0, 2), flag=wx.EXPAND|wx.LEFT|wx.RIGHT|wx.ALIGN_CENTER, border=5)
            sizer.Add(cvText, pos=(row+0, 3), flag=wx.EXPAND|wx.LEFT|wx.RIGHT|wx.ALIGN_RIGHT, border=5)
            
            scanCount += 1
            rawTotalData += rawDataVolume
            corTotalData += corDataVolume
            row += 1
            
        line = wx.StaticLine(panel)
        sizer.Add(line, pos=(row+0, 0), span=(1, 4), flag=wx.EXPAND|wx.BOTTOM, border=10)
        
        row += 1
        
        ttText = wx.StaticText(panel, label='Totals:')
        ttText.SetFont(font)
        rvText = wx.StaticText(panel, label='%.2f GB' % (rawTotalData/1024.0**3,))
        rvText.SetFont(font)
        cvText = wx.StaticText(panel, label='%.2f GB' % (corTotalData/1024.0**3,))
        cvText.SetFont(font)
        
        sizer.Add(ttText, pos=(row+0, 0), flag=wx.EXPAND|wx.LEFT|wx.RIGHT|wx.ALIGN_LEFT, border=5)
        sizer.Add(rvText, pos=(row+0, 2), flag=wx.EXPAND|wx.LEFT|wx.RIGHT|wx.ALIGN_CENTER, border=5)
        sizer.Add(cvText, pos=(row+0, 3), flag=wx.EXPAND|wx.LEFT|wx.RIGHT|wx.ALIGN_RIGHT, border=5)
        
        row += 1
        
        ok = wx.Button(panel, ID_VOL_INFO_OK, 'Ok', size=(90, 28))
        sizer.Add(ok, pos=(row+0, 3), flag=wx.ALL, border=5)
        
        panel.SetSizer(sizer)
        sizer.Fit(self)
        
    def initEvents(self):
        self.Bind(wx.EVT_BUTTON, self.onOk, id=ID_VOL_INFO_OK)
        
    def onOk(self, event):
        self.Close()


ID_RESOLVE_IPM = 611
ID_RESOLVE_RESOLVE = 612
ID_RESOLVE_APPLY = 613
ID_RESOLVE_CANCEL = 614

class ResolveTarget(wx.Frame):
    def __init__ (self, parent):	
        wx.Frame.__init__(self, parent, title='Resolve Target')
        
        self.parent = parent
        
        self.setSource()
        self.initUI()
        self.initEvents()
        self.Show()
        
    def setSource(self):
        for i in range(self.parent.listControl.GetItemCount()):
            if self.parent.listControl.IsChecked(i):
                item = self.parent.listControl.GetItem(i, 1)
                self.scanID = i
                self.source = item.GetText()
                return True
                
        self.scanID = -1
        self.source = ''
        return False
        
    def initUI(self):
        row = 0
        panel = wx.Panel(self)
        sizer = wx.GridBagSizer(0, 0)
        
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
        pr = wx.StaticText(panel, label='PM - RA (mas/yr):')
        prText = wx.TextCtrl(panel, style=wx.TE_READONLY)
        prText.SetValue('---')
        pd = wx.StaticText(panel, label='PM - Dec (mas/yr):')
        pdText = wx.TextCtrl(panel, style=wx.TE_READONLY)
        pdText.SetValue('---')
        
        sizer.Add(ra, pos=(row+2, 0), span=(1, 1), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
        sizer.Add(raText, pos=(row+2, 1), span=(1, 4), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
        sizer.Add(dec, pos=(row+3, 0), span=(1, 1), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
        sizer.Add(decText, pos=(row+3, 1), span=(1, 4), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
        sizer.Add(pr, pos=(row+4, 0), span=(1, 1), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
        sizer.Add(prText, pos=(row+4, 1), span=(1, 4), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
        sizer.Add(pd, pos=(row+5, 0), span=(1, 1), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
        sizer.Add(pdText, pos=(row+5, 1), span=(1, 4), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
        sizer.Add(srv, pos=(row+6, 0), span=(1, 1), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
        sizer.Add(srvText, pos=(row+6, 1), span=(1, 4), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
        
        line = wx.StaticLine(panel)
        sizer.Add(line, pos=(row+7, 0), span=(1, 5), flag=wx.EXPAND|wx.BOTTOM, border=10)
        
        inclPM = wx.CheckBox(panel, ID_RESOLVE_IPM, 'Include PM')
        
        resolve = wx.Button(panel, ID_RESOLVE_RESOLVE, 'Resolve', size=(90, 28))
        appli = wx.Button(panel, ID_RESOLVE_APPLY, 'Apply', size=(90, 28))
        cancel = wx.Button(panel, ID_RESOLVE_CANCEL, 'Cancel', size=(90, 28))
        
        sizer.Add(inclPM, pos=(row+8, 0), flag=wx.ALL, border=5)
        sizer.Add(resolve, pos=(row+8, 2), flag=wx.ALL, border=5)
        sizer.Add(appli, pos=(row+8, 3), flag=wx.ALL, border=5)
        sizer.Add(cancel, pos=(row+8, 4), flag=wx.ALL, border=5)
        
        panel.SetSizer(sizer)
        sizer.Fit(self)
        
        self.srcText = srcText
        self.raText = raText
        self.decText = decText
        self.prText = prText
        self.pdText = pdText
        self.srvText = srvText
        self.inclPM = inclPM
        self.appli = appli
        self.appli.Enable(False)
        
    def initEvents(self):
        self.Bind(wx.EVT_BUTTON, self.onResolve, id=ID_RESOLVE_RESOLVE)
        self.Bind(wx.EVT_BUTTON, self.onApply, id=ID_RESOLVE_APPLY)
        self.Bind(wx.EVT_BUTTON, self.onCancel, id=ID_RESOLVE_CANCEL)
        
    def onResolve(self, event):
        self.source = self.srcText.GetValue()
        try:
            posn = astro.resolve_name(self.source)
            self.raText.SetValue(str(astro.deg_to_hms(posn.ra)).replace(' ', ':'))
            self.decText.SetValue(str(astro.deg_to_dms(posn.dec)).replace(' ', ':'))
            self.prText.SetValue('---')
            self.pdText.SetValue('---')
            self.srvText.SetValue(posn.resolved_by)
            
            if self.inclPM.IsChecked():
                if posn.pm_ra is not None:
                    self.prText.SetValue(f"{posn.pm_ra:.2f}")
                    self.pdText.SetValue(f"{posn.pm_dec:.2f}")
                    
            if self.scanID != -1:
                self.appli.Enable(True)
        except RuntimeError:
            self.raText.SetValue("---")
            self.decText.SetValue("---")
            self.prText.SetValue("---")
            self.pdText.SetValue("---")
            self.srvText.SetValue("Error resolving target")
            
    def onApply(self, event):
        if self.scanID == -1:
            return False
        else:
            success = True
            
            obsIndex = self.scanID
            pmText = "%s %s" % (self.prText.GetValue(), self.pdText.GetValue())
            for obsAttr,widget in [(6,self.raText), (7,self.decText), (11,pmText)]:
                try:
                    try:
                        text = widget.GetValue()
                    except AttributeError:
                        text = widget
                    newData = self.parent.coerceMap[obsAttr](text)
                    
                    oldData = getattr(self.parent.project.runs[0].scans[obsIndex], self.parent.columnMap[obsAttr])
                    if newData != oldData:
                        setattr(self.parent.project.runs[0].scans[obsIndex], self.parent.columnMap[obsAttr], newData)
                        self.parent.project.runs[0].scans[obsIndex].update()
                        
                        if obsAttr < self.parent.listControl.GetColumnCount():
                            item = self.parent.listControl.GetItem(obsIndex, obsAttr)
                            item.SetText(text)
                            self.parent.listControl.SetItem(item)
                            self.parent.listControl.RefreshItem(item.GetId())
                            
                        self.parent.edited = True
                        self.parent.setSaveButton()
                        self.appli.Enable(False)
                except ValueError as err:
                    success = False
                    pid_print(f"Error: {str(err)}")
                    
            if success:
                self.Close()
                
    def onCancel(self, event):
        self.Close()


ID_SCHEDULE_APPLY = 612
ID_SCHEDULE_CANCEL = 613

class ScheduleWindow(wx.Frame):
    def __init__ (self, parent):	
        wx.Frame.__init__(self, parent, title='Run Scheduling')
        
        self.parent = parent
        
        self.initUI()
        self.initEvents()
        self.Show()
        
    def initUI(self):
        row = 0
        panel = wx.Panel(self)
        sizer = wx.GridBagSizer(0, 0)
        
        font = wx.SystemSettings.GetFont(wx.SYS_SYSTEM_FONT)
        font.SetPointSize(font.GetPointSize()+2)
        
        src = wx.StaticText(panel, label='Rescheduling Options:')
        src.SetFont(font)
        sizer.Add(src, pos=(row+0, 0), span=(1, 3), flag=wx.ALIGN_CENTER, border=5)
        row += 1
        
        sidereal = wx.RadioButton(panel, -1, 'Sidereal time fixed, date changable')
        solar = wx.RadioButton(panel, -1, 'UTC time fixed, date changable')
        fixed = wx.RadioButton(panel, -1, 'Use only specfied date/time')
        
        if self.parent.project.runs[0].comments.find('ScheduleSolarMovable') != -1:
            sidereal.SetValue(False)
            solar.SetValue(True)
            fixed.SetValue(False)
        elif self.parent.project.runs[0].comments.find('ScheduleFixed') != -1:
            sidereal.SetValue(False)
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
        
        panel.SetSizer(sizer)
        sizer.Fit(self)
        
        self.sidereal = sidereal
        self.solar = solar
        self.fixed = fixed
        
    def initEvents(self):
        self.Bind(wx.EVT_BUTTON, self.onApply, id=ID_SCHEDULE_APPLY)
        self.Bind(wx.EVT_BUTTON, self.onCancel, id=ID_SCHEDULE_CANCEL)
        
    def onApply(self, event):
        oldComments = self.parent.project.runs[0].comments
        oldComments = oldComments.replace('ScheduleSiderealMovable', '')
        oldComments = oldComments.replace('ScheduleSolarMovable', '')
        oldComments = oldComments.replace('SchedulFixed', '')
        
        if self.sidereal.GetValue():
            oldComments += ';;ScheduleSiderealMovable'
        elif self.solar.GetValue():
            oldComments += ';;ScheduleSolarMovable'
        elif self.fixed.GetValue():
            oldComments += ';;ScheduleFixed'
        else:
            pass
            
        self.parent.project.runs[0].comments = oldComments
        
        self.parent.edited = True
        self.parent.setSaveButton()
        
        self.Close()
        
    def onCancel(self, event):
        self.Close()


ID_PMOTION_APPLY = 712
ID_PMOTION_CANCEL = 713

class ProperMotionWindow(wx.Frame):
    def __init__ (self, parent, scanID):
        self.parent = parent
        self.scanID = scanID
        self.scan = self.parent.project.runs[0].scans[self.scanID]
        
        title = 'Scan #%i Proper Motion' % (scanID+1,)
        wx.Frame.__init__(self, parent, title=title)
        
        self.initUI()
        self.initEvents()
        self.Show()
        
    def initUI(self):
        row = 0
        panel = wx.Panel(self)
        sizer = wx.GridBagSizer(0, 0)
        
        src = wx.StaticText(panel, label='Target Name:')
        srcText = wx.TextCtrl(panel, style=wx.TE_READONLY)
        srcText.SetValue(self.scan.target)
        sizer.Add(src, pos=(row+0, 0), span=(1, 1), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
        sizer.Add(srcText, pos=(row+0, 1), span=(1, 4), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
        
        line = wx.StaticLine(panel)
        sizer.Add(line, pos=(row+1, 0), span=(1, 5), flag=wx.EXPAND|wx.BOTTOM, border=10)
        
        pr = wx.StaticText(panel, label='RA (mas/yr):')
        prText = wx.TextCtrl(panel)
        prText.SetValue("%+.3f" % self.scan.pm[0])
        pd = wx.StaticText(panel, label='Dec (mas/yr):')
        pdText = wx.TextCtrl(panel)
        pdText.SetValue("%+.3f" % self.scan.pm[1])
        
        sizer.Add(pr, pos=(row+2, 0), span=(1, 1), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
        sizer.Add(prText, pos=(row+2, 1), span=(1, 4), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
        sizer.Add(pd, pos=(row+3, 0), span=(1, 1), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
        sizer.Add(pdText, pos=(row+3, 1), span=(1, 4), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
        
        line = wx.StaticLine(panel)
        sizer.Add(line, pos=(row+4, 0), span=(1, 5), flag=wx.EXPAND|wx.BOTTOM, border=10)
        
        appli = wx.Button(panel, ID_PMOTION_APPLY, 'Apply', size=(90, 28))
        cancel = wx.Button(panel, ID_PMOTION_CANCEL, 'Cancel', size=(90, 28))
        
        sizer.Add(appli, pos=(row+5, 3), flag=wx.ALL, border=5)
        sizer.Add(cancel, pos=(row+5, 4), flag=wx.ALL, border=5)
        
        panel.SetSizer(sizer)
        sizer.Fit(self)
        
        self.prText = prText
        self.pdText = pdText
        
    def initEvents(self):
        self.Bind(wx.EVT_BUTTON, self.onApply, id=ID_PMOTION_APPLY)
        self.Bind(wx.EVT_BUTTON, self.onCancel, id=ID_PMOTION_CANCEL)
        
    def onApply(self, event):
        if self.scanID == -1:
            return False
        else:
            success = True
            
            obsIndex = self.scanID
            obsAttr = 11
            text = "%s %s" % (self.prText.GetValue(), self.pdText.GetValue())
            
            try:
                newData = self.parent.coerceMap[obsAttr](text)
                
                oldData = getattr(self.parent.project.runs[0].scans[obsIndex], self.parent.columnMap[obsAttr])
                if newData != oldData:
                    setattr(self.parent.project.runs[0].scans[obsIndex], self.parent.columnMap[obsAttr], newData)
                    self.parent.project.runs[0].scans[obsIndex].update()
                    
                    if obsAttr < self.parent.listControl.GetColumnCount():
                        item = self.parent.listControl.GetItem(obsIndex, obsAttr)
                        item.SetText(text)
                        self.parent.listControl.SetItem(item)
                        self.parent.listControl.RefreshItem(item.GetId())
                        
                    self.parent.edited = True
                    self.parent.setSaveButton()
            except ValueError as err:
                success = False
                pid_print(f"Error: {str(err)}")
                
            if success:
                self.Close()
                
    def onCancel(self, event):
        self.Close()


ID_ACCEPT = 811

class SearchWindow(OCS):
    def __init__(self, parent, scanID):
        self.parent = parent
        self.scanID = scanID
        target, ra, dec = None, None, None
        if self.scanID >= 0:
            item = self.parent.listControl.GetItem(self.scanID, 1)
            target = item.GetText()
            item = self.parent.listControl.GetItem(self.scanID, 6)
            ra = item.GetText()
            item = self.parent.listControl.GetItem(self.scanID, 7)
            dec = item.GetText()
        OCS.__init__(self, self.parent, 'Calibrator Search', target=target, ra=ra, dec=dec)
        
    def initUI(self):
        OCS.initUI(self)
        select = wx.Button(self.panel3, ID_ACCEPT, 'Accept', size=(100, 28))
        self.sizer3.Add(select, pos=(self.row+9, 5), span=(1, 1), flag=wx.EXPAND|wx.ALIGN_CENTER, border=5)
        self.panel.SetSizerAndFit(self.sizer)
        
    def initEvents(self):
        OCS.initEvents(self)
        self.Bind(wx.EVT_BUTTON, self.onAccept, id=ID_ACCEPT)
        
    def onAccept(self, event):
        index = self.listControl.GetNextSelected(-1)
        if index != -1:
            name = self.listControl.GetItem(index, 0)
            name = name.GetText()
            ra = self.listControl.GetItem(index, 1)
            ra = ra.GetText()
            dec = self.listControl.GetItem(index, 2)
            dec = dec.GetText()
            
            if self.scanID >= 0:
                obsIndex = self.scanID
                for obsAttr,widget in [(1,name), (6,ra), (7,dec)]:
                    try:
                        try:
                            text = widget.GetValue()
                        except AttributeError:
                            text = widget
                        newData = self.parent.coerceMap[obsAttr](text)
                        
                        oldData = getattr(self.parent.project.runs[0].scans[obsIndex], self.parent.columnMap[obsAttr])
                        if newData != oldData:
                            setattr(self.parent.project.runs[0].scans[obsIndex], self.parent.columnMap[obsAttr], newData)
                            self.parent.project.runs[0].scans[obsIndex].update()
                            
                            if obsAttr < self.parent.listControl.GetColumnCount():
                                item = self.parent.listControl.GetItem(obsIndex, obsAttr)
                                item.SetText(text)
                                self.parent.listControl.SetItem(item)
                                self.parent.listControl.RefreshItem(item.GetId())
                                
                            self.parent.edited = True
                            self.parent.setSaveButton()
                    except ValueError as err:
                        success = False
                        pid_print(f"Error: {str(err)}")
                        
        wx.CallAfter(self.onQuit, event)


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
        wx.Frame.__init__(self, parent, -1, 'Swarm GUI Handbook', size=(570, 400))
        
        self.initUI()
        self.Show()
        
    def initUI(self):
        panel = wx.Panel(self, -1, style=wx.BORDER_SUNKEN)
        vbox = wx.BoxSizer(wx.VERTICAL)
        
        help = HtmlWindow(panel)
        help.LoadPage(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'docs/swarm_help.html'))
        vbox.Add(help, 1, wx.EXPAND)
        
        self.CreateStatusBar()
        
        panel.SetSizerAndFit(vbox)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description='GUI for making all sorts of interferometer definition files (IDFs) for the LWA interferometer', 
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
        )
    parser.add_argument('filename', type=str, nargs='?', 
                        help='filename of IDF to edit')
    parser.add_argument('-d', '--drsu-size', type=aph.positive_int, default=idf._DRSUCapacityTB, 
                        help='perform storage calcuations assuming the specified DRSU size in TB')
    args = parser.parse_args()
    
    app = wx.App()
    IDFCreator(None, 'Interferometer Definition File', args)
    app.MainLoop()
    
