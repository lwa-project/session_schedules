#!/usr/bin/env python

# Python2 compatibility
from __future__ import print_function, division

import os
import re
import sys
import ephem
import numpy
import argparse
try:
    from urllib2 import urlopen
    from urllib import urlencode, quote_plus
except ImportError:
    from urllib.request import urlopen
    from urllib.parse import urlencode, quote_plus
from tempfile import NamedTemporaryFile
from xml.etree import ElementTree
import astropy.io.fits as astrofits

import wx
import wx.html as html
from wx.lib.scrolledpanel import ScrolledPanel
from wx.lib.mixins.listctrl import TextEditMixin, CheckListCtrlMixin

import matplotlib
matplotlib.use('WXAgg')
matplotlib.interactive(True)

from matplotlib.backends.backend_wxagg import NavigationToolbar2WxAgg, FigureCanvasWxAgg
from matplotlib.figure import Figure
from matplotlib.ticker import FuncFormatter, NullFormatter, NullLocator

import lsl
from lsl.misc.lru_cache import lru_cache
from lsl.misc import parser as aph


__version__ = "0.1"
__author__ = "Jayce Dowell"


# Deal with the different wxPython versions
if 'phoenix' in wx.PlatformInfo:
    AppendMenuItem = lambda x, y: x.Append(y)
    AppendMenuMenu = lambda *args, **kwds: args[0].Append(*args[1:], **kwds)
    InsertListItem = lambda *args, **kwds: args[0].InsertItem(*args[1:], **kwds)
    SetListItem    = lambda *args, **kwds: args[0].SetItem(*args[1:], **kwds)
    ## This one is a little trickier
    def AppendToolItem(*args, **kwds):
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
    AppendToolItem = lambda *args, **kwds: args[0].AddLabelTool(*args[1:], **kwds)


ORANGE = wx.Colour(0xFF, 0xA5, 0x00)


SIMBAD_REF_RE = re.compile('^(\[(?P<ref>[A-Za-z0-9]+)\]\s*)')


ID_QUIT = 101
ID_RESOLVE = 201
ID_SEARCH = 202
ID_LISTCTRL = 203
ID_DISPLAY = 204
ID_ABOUT = 301

class CalibratorSearch(wx.Frame):
    def __init__(self, parent, title, target=None, ra=None, dec=None):
        wx.Frame.__init__(self, parent, title=title, size=(725, 575))
        
        self.scriptPath = os.path.abspath(__file__)
        self.scriptPath = os.path.split(self.scriptPath)[0]
        
        self.initUI()
        self.initEvents()
        self.Show()
        
        if target is not None:
            # Set the target name
            self.nameText.SetValue(target)
            if ra is None or dec is None:
                ## If RA/Dec is not defined, try to resolve
                self.onResolve(None)
        if ra is not None and dec is not None:
            # Set the RA/Dec
            self.raText.SetValue(ra)
            self.decText.SetValue(dec)
            ## Search
            #self.onSearch(None)
            
    def initUI(self):
        """
        Start the user interface.
        """
        
        # Menu bar
        menubar = wx.MenuBar()
        
        fileMenu = wx.Menu()
        helpMenu = wx.Menu()
        
        # File menu items
        quit = wx.MenuItem(fileMenu, ID_QUIT, '&Quit')
        AppendMenuItem(fileMenu, quit)
        
        # Help menu items
        about = wx.MenuItem(helpMenu, ID_ABOUT, '&About')
        AppendMenuItem(helpMenu, about)
        
        menubar.Append(fileMenu, '&File')
        menubar.Append(helpMenu, '&Help')
        self.SetMenuBar(menubar)
        
        # Status bar
        self.statusbar = self.CreateStatusBar()
        
        font = wx.SystemSettings.GetFont(wx.SYS_SYSTEM_FONT)
        font.SetPointSize(font.GetPointSize()+2)
        
        row = 0
        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)
        
        # Target name/search
        panel1 = wx.Panel(panel)
        sizer1 = wx.GridBagSizer(7, 7)
        ## Name
        lbl = wx.StaticText(panel1, label='Target Parameters')
        lbl.SetFont(font)
        
        src = wx.StaticText(panel1, label='Name:')
        srcText = wx.TextCtrl(panel1, size=(150,-1))
        srcText.SetValue('')
        sizer1.Add(lbl, pos=(row+0, 0), span=(1, 7), flag=wx.ALIGN_CENTER, border=5)
        sizer1.Add(src, pos=(row+1, 0), span=(1, 1), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
        sizer1.Add(srcText, pos=(row+1, 1), span=(1, 2), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
        
        ## Coordinates
        lbl = wx.StaticText(panel1, label=' - or - ')
        ra = wx.StaticText(panel1, label='RA:')
        raText = wx.TextCtrl(panel1, size=(150,-1))
        raText.SetValue('HH:MM:SS.SS')
        dec = wx.StaticText(panel1, label='Dec:')
        decText = wx.TextCtrl(panel1, size=(150,-1))
        decText.SetValue('sDD:MM:SS.S')
        sizer1.Add(lbl, pos=(row+1, 3), span=(1, 1), flag=wx.ALIGN_CENTER, border=5)
        sizer1.Add(ra, pos=(row+1, 4), span=(1, 1), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
        sizer1.Add(raText, pos=(row+1, 5), span=(1, 2), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
        sizer1.Add(dec, pos=(row+2, 4), span=(1, 1), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
        sizer1.Add(decText, pos=(row+2, 5), span=(1, 2), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
        
        ## Control button
        resolve = wx.Button(panel1, ID_RESOLVE, 'Resolve', size=(90, 28))
        sizer1.Add(resolve, pos=(row+2, 1), span=(1,1), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
        
        line = wx.StaticLine(panel1)
        sizer1.Add(line, pos=(row+3, 0), span=(1, 7), flag=wx.EXPAND|wx.BOTTOM, border=10)
        
        panel1.SetSizer(sizer1)
        sizer.Add(panel1, flag=wx.ALIGN_CENTER)
        
        # Search control
        ## Setup
        panel2 = wx.Panel(panel)
        sizer2 = wx.GridBagSizer(7, 7)
        lbl = wx.StaticText(panel2, label='VLSSr Search Parameters')
        lbl.SetFont(font)
        
        ## Distance
        dist = wx.StaticText(panel2, label='Search Radius')
        ld = wx.StaticText(panel2, label='Min:')
        ldText = wx.TextCtrl(panel2)
        ldText.SetValue('0.0')
        ldUnit = wx.StaticText(panel2, label='deg')
        ud = wx.StaticText(panel2, label='Max:')
        udText = wx.TextCtrl(panel2)
        udText.SetValue('3.0')
        udUnit = wx.StaticText(panel2, label='deg')
        sizer2.Add(lbl, pos=(row+0, 0), span=(1, 7), flag=wx.ALIGN_CENTER, border=5)
        sizer2.Add(dist, pos=(row+1, 0), span=(1, 1), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
        sizer2.Add(ld, pos=(row+1, 1), span=(1, 1), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
        sizer2.Add(ldText, pos=(row+1, 2), span=(1, 1), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
        sizer2.Add(ldUnit, pos=(row+1, 3), span=(1, 1), flag=wx.EXPAND|wx.ALIGN_LEFT, border=5)
        sizer2.Add(ud, pos=(row+1, 4), span=(1, 1), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
        sizer2.Add(udText, pos=(row+1, 5), span=(1, 1), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
        sizer2.Add(udUnit, pos=(row+1, 6), span=(1, 1), flag=wx.EXPAND|wx.ALIGN_LEFT, border=5)
        
        ## Flux density
        fd = wx.StaticText(panel2, label='Flux Density')
        mdLabl = wx.StaticText(panel2, label='Min:')
        fdText = wx.TextCtrl(panel2)
        fdText.SetValue('10.0')
        fdUnit = wx.StaticText(panel2, label='Jy')
        sizer2.Add(fd, pos=(row+2, 0), span=(1, 1), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
        sizer2.Add(mdLabl, pos=(row+2, 1), span=(1, 1), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
        sizer2.Add(fdText, pos=(row+2, 2), span=(1, 1), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
        sizer2.Add(fdUnit, pos=(row+2, 3), span=(1, 1), flag=wx.EXPAND|wx.ALIGN_LEFT, border=5)
        
        ## Control button
        search = wx.Button(panel2, ID_SEARCH, 'Search', size=(90, 28))
        sizer2.Add(search, pos=(row+2, 5), span=(1,1), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
        
        line = wx.StaticLine(panel2)
        sizer2.Add(line, pos=(row+3, 0), span=(1, 7), flag=wx.EXPAND|wx.BOTTOM, border=10)
        
        panel2.SetSizer(sizer2)
        sizer.Add(panel2, flag=wx.ALIGN_CENTER)
        
        # Candidate list
        ## Setup
        panel3 = wx.Panel(panel)
        sizer3 = wx.GridBagSizer(7,7)
        lbl = wx.StaticText(panel3, label='Candidates')
        lbl.SetFont(font)
        
        ## Listing
        self.listControl = wx.ListCtrl(panel3, id=ID_LISTCTRL, style=wx.LC_REPORT)
        self.listControl.InsertColumn(0, 'Name', width=125)
        self.listControl.InsertColumn(1, 'RA (J2000)', width=100)
        self.listControl.InsertColumn(2, 'Dec (J2000)', width=100)
        self.listControl.InsertColumn(3, 'Dist. (deg)', width=100)
        self.listControl.InsertColumn(4, 'Flux (Jy)', width=100)
        self.listControl.InsertColumn(5, 'Size', width=200)
        size = self.listControl.GetSize()
        size[0] = 725
        size[1] = 225
        self.listControl.SetMinSize(size)
        self.listControl.Fit()
        sizer3.Add(lbl, pos=(row+0, 0), span=(1, 7), flag=wx.ALIGN_CENTER, border=5)
        sizer3.Add(self.listControl, pos=(row+1, 0), span=(8,7), flag=wx.EXPAND|wx.ALIGN_CENTER, border=5)
        
        ## Controls
        sz = wx.StaticText(panel3, label='Image Size:')
        szText = wx.TextCtrl(panel3)
        szText.SetValue('1.0')
        szUnit = wx.StaticText(panel3, label='deg')
        sizer3.Add(sz, pos=(row+9, 0), span=(1, 1), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
        sizer3.Add(szText, pos=(row+9, 1), span=(1, 2), flag=wx.EXPAND|wx.LEFT|wx.RIGHT, border=5)
        sizer3.Add(szUnit, pos=(row+9, 3), span=(1, 1), flag=wx.EXPAND|wx.ALIGN_LEFT, border=5)
        display = wx.Button(panel3, ID_DISPLAY, 'Display Selected', size=(100, 28))
        sizer3.Add(display, pos=(row+9, 6), span=(1, 1), flag=wx.EXPAND|wx.ALIGN_CENTER, border=5)
        
        panel3.SetSizer(sizer3)
        sizer.Add(panel3, flag=wx.EXPAND|wx.LEFT|wx.RIGHT)
        
        panel.SetSizerAndFit(sizer)
        
        # Save
        self.nameText = srcText
        self.raText = raText
        self.decText = decText
        self.fdText = fdText
        self.ldText = ldText
        self.udText = udText
        self.szText = szText
        
        # For embedding purposes
        self.row = row
        self.panel = panel
        self.sizer = sizer
        self.panel3 = panel3
        self.sizer3 = sizer3
        
    def initEvents(self):
        """
        Bind the various events need to make the GUI run.
        """
    
        # File menu events
        self.Bind(wx.EVT_MENU, self.onQuit, id=ID_QUIT)
        
        # Help menu events
        self.Bind(wx.EVT_MENU, self.onAbout, id=ID_ABOUT)
        
        # Window manager close
        self.Bind(wx.EVT_CLOSE, self.onQuit)
        
        # Buttons
        self.Bind(wx.EVT_BUTTON, self.onResolve, id=ID_RESOLVE)
        self.Bind(wx.EVT_BUTTON, self.onSearch, id=ID_SEARCH)
        self.Bind(wx.EVT_BUTTON, self.onDisplay, id=ID_DISPLAY)
        
        # Keyboard
        self.nameText.Bind(wx.EVT_KEY_UP, self.onKeyPress)
        
    def _clearCandidates(self):
        """
        Clear everything out of the candidates list.
        """
        
        for i in range(self.listControl.GetItemCount()):
            self.listControl.DeleteItem(0)
            
    def onResolve(self, event):
        """
        Resolve the target name into a set of coordinates and update the display.
        """
        
        source = self.nameText.GetValue()
        
        if source != '':
            try:
                result = urlopen('https://cdsweb.u-strasbg.fr/cgi-bin/nph-sesame/-oxp/SNV?%s' % quote_plus(source))
                tree = ElementTree.fromstring(result.read())
                target = tree.find('Target')
                service = target.find('Resolver')
                coords = service.find('jpos')
                
                service = service.attrib['name'].split('=', 1)[1]
                raS, decS = coords.text.split(None, 1)
                
                self.raText.SetValue(raS)
                self.decText.SetValue(decS)
                
                self._clearCandidates()
                
            except (IOError, IndexError, AttributeError, ElementTree.ParseError) as error:
                self.statusbar.SetStatusText('Error resolving source: %s' % str(error), 0)
                self.raText.SetValue("HH:MM:SS.SS")
                self.decText.SetValue("sDD:MM:SS.S")
                
    def _reverseNameLookup(self, ra, dec, radius_arcsec=15.0):
        """
        Perform a reverse name lookup (coordinates to name) via Simbad and return 
        the name of the source.  Returns '---' if no name can be found.
        """
        
        final_name = '---'
        
        try:
            result = urlopen('https://simbad.u-strasbg.fr/simbad/sim-coo?Coord=%s&CooFrame=FK5&CooEpoch=%s&CooEqui=%s&CooDefinedFrames=none&Radius=%f&Radius.unit=arcsec&submit=submit%%20query&CoordList=&list.idopt=CATLIST&list.idcat=3C%%2C4C%%2CVLSS%%2CNVSS%%2CTXS&output.format=VOTable' % (quote_plus('%s %s' % (ra, dec)), 2000.0, 2000.0, radius_arcsec))
            tree = ElementTree.fromstring(result.read())
            namespaces = {'VO': 'http://www.ivoa.net/xml/VOTable/v1.2'}
            resource = tree.find('VO:RESOURCE', namespaces=namespaces)
            table = resource.find('VO:TABLE', namespaces=namespaces)
            
            rank = -1
            
            fields = []
            for f in table.findall('VO:FIELD', namespaces=namespaces):
                fields.append(f.attrib['ID'])
            data = table.find('VO:DATA', namespaces=namespaces)
            tabledata = data.find('VO:TABLEDATA', namespaces=namespaces)
            for row in tabledata.findall('VO:TR', namespaces=namespaces):
                entry = {}
                for name,col in zip(fields, row.findall('VO:TD', namespaces=namespaces)):
                    entry[name] = col.text
                entry_rank = int(entry['NB_REF'], 10)
                
                if entry_rank > rank:
                    final_name = SIMBAD_REF_RE.sub('', entry['MAIN_ID'])
                    rank = entry_rank
        except (IOError, ValueError, AttributeError, ElementTree.ParseError) as error:
            self.statusbar.SetStatusText('Error during name lookup: %s' % str(error), 0)
            
        if final_name == '---':
            # Try with a bigger search area
            final_name = self._reverseNameLookup(ra, dec, radius_arcsec=2*radius_arcsec)
        else:
            # Try to get a "better" name for the source
            final_name = self._radioNameQuery(final_name)
            
        return final_name
        
    @lru_cache(maxsize=64)
    def _radioNameQuery(self, name):
        final_name = name
        
        preferred_order = {'3C':5, '4C':4, 'TXS':3, 'VLSS':2, 'NVSS': 1}
        
        rank = -1
        for catalog in preferred_order.keys():
            if name.startswith(catalog):
                if preferred_order[catalog] > rank:
                    rank = preferred_order[catalog]
                    
        try:
            result = urlopen('https://cdsweb.u-strasbg.fr/cgi-bin/nph-sesame/-oxpI/SNV?%s' % quote_plus(name))
            tree = ElementTree.fromstring(result.read())
            target = tree.find('Target')
            service = target.find('Resolver')
            for alias in service.findall('alias'):
                for catalog in preferred_order.keys():
                    if alias.text.startswith(catalog):
                        if preferred_order[catalog] > rank:
                            final_name = alias.text
                            rank = preferred_order[catalog]
        except (IOError, ValueError, AttributeError, ElementTree.ParseError) as error:
            self.statusbar.SetStatusText('Error during radio name lookup: %s' % str(error), 0)
            
        return final_name
        
    def onSearch(self, event):
        """
        Search for VLSSr sources around the target position using the constrains 
        provided, and update the candidate list.
        """
        
        # Load in the values
        ## Coodinates
        ra = self.raText.GetValue()
        dec = self.decText.GetValue()
        try:
            ephem.hours(str(ra))
            ephem.degrees(str(dec))
        except (ValueError, TypeError):
            return False
        ## Search criteria
        flux = float(self.fdText.GetValue())
        min_dist = float(self.ldText.GetValue())
        max_dist = float(self.udText.GetValue())
        if max_dist > 10.0:
            ### Limit ourselves to a 10 degree search
            max_dist = 10.0
            self.udText.SetValue('%.1f' % max_dist)
            self.udText.Refresh()
            
        wx.BeginBusyCursor()
        
        # Find the candidates
        ## Query the candidates
        data = urlencode({'Equinox': 3, 
                          'DecFit': 0, 
                          'FluxDensity': flux,  
                          'ObjName': '', 
                          'RA': ra.replace(':', ' '), 
                          'Dec': dec.replace(':', ' '), 
                          'searchrad': max_dist*3600, 
                          'verhalf': 12*60, 
                          'poslist': ''})
        candidates = []
        try:
            result = urlopen('https://www.cv.nrao.edu/cgi-bin/newVLSSlist.pl', data)
            lines = result.readlines()
            
            ## Parse
            inside = False
            for line in lines:
                line = line.rstrip()
                if line.find('h  m    s    d  m   s   Ori      Jy') != -1:
                    inside = True
                elif line.find('Found') != -1 and inside:
                    inside = False
                elif inside:
                    fields = line.split(None, 13)
                    if len(fields) < 14:
                        continue
                    ra = ':'.join(fields[0:3])
                    dec = ':'.join(fields[3:6])
                    sep = float(fields[6]) / 3600.0
                    flux = float(fields[7])
                    maj, min, pa = fields[8], fields[9], fields[10]
                    if sep < min_dist:
                        continue
                    name = self._reverseNameLookup(ra, dec)
                    candidates.append( (name,ra,dec,sep,flux,maj,min,pa) )
                    
        except (IOError, ValueError, RuntimeError) as error:
            self.statusbar.SetStatusText('Error during search: %s' % str(error), 0)
            
        ## Update the status bar
        if len(candidates) == 0:
            self.statusbar.SetStatusText('No candidates found matching the search criteria', 0)
        else:
            self.statusbar.SetStatusText("Found %i candidates matching the search criteria" % len(candidates), 0)
            
        ## Sort by distance from the target
        candidates.sort(key=lambda x:x[3])
        
        wx.EndBusyCursor()
        
        fontW = wx.SystemSettings.GetFont(wx.SYS_SYSTEM_FONT)
        fontW.SetStyle(wx.ITALIC)
        fontE = wx.SystemSettings.GetFont(wx.SYS_SYSTEM_FONT)
        fontE.SetStyle(wx.ITALIC)
        fontE.SetWeight(wx.BOLD)
        
        # Update the candidate list in the window
        self._clearCandidates()
        for i,candidate in enumerate(candidates):
            name,ra,dec,sep,flux,maj,min,pa = candidate
            index = InsertListItem(self.listControl, i, name)
            
            SetListItem(self.listControl, index, 1, ra)
            SetListItem(self.listControl, index, 2, dec)
            SetListItem(self.listControl, index, 3, "%.1f" % sep)
            SetListItem(self.listControl, index, 4, "%.1f" % flux)
            SetListItem(self.listControl, index, 5, "%s\" by %s\" @ %s" % (maj, min, pa))
            
            ## Flag things that look like they might be too far away
            if sep >= 3.5:
                item = self.listControl.GetItem(index, 3)
                self.listControl.SetItemTextColour(item.GetId(), wx.RED)
                self.listControl.SetItemFont(item.GetId(), fontE)
                self.listControl.RefreshItem(item.GetId())
            elif sep > 3.0:
                item = self.listControl.GetItem(index, 3)
                self.listControl.SetItemTextColour(item.GetId(), ORANGE)
                self.listControl.SetItemFont(item.GetId(), fontW)
                self.listControl.RefreshItem(item.GetId())
                
            ## Flag things that look like they might be too faint
            if flux < 5.0:
                item = self.listControl.GetItem(index, 4)
                self.listControl.SetItemFont(item.GetId(), fontE)
                self.listControl.RefreshItem(item.GetId())
                
            ## Flag things that look like they might be too large
            try:
                maj = float(maj)
                if maj >= 40.0:
                    item = self.listControl.GetItem(index, 5)
                    self.listControl.SetItemTextColour(item.GetId(), wx.RED)
                    self.listControl.SetItemFont(item.GetId(), fontE)
                    self.listControl.RefreshItem(item.GetId())
            except ValueError:
                pass
                
    @lru_cache(maxsize=4)
    def _loadVLSSrImage(self, ra, dec, size=0.5):
        """
        Given an RA and declination string, query the VLSSr postage stamp server
        and return a header/image for those coordinates.
        """
        
        header = {}
        image = None
        
        data = urlencode({'Equinox': 2, 
                          'ObjName': '', 
                          'RA': ra.replace(':', ' '), 
                          'Dec': dec.replace(':', ' '), 
                          'Size': '%f %f' % (size, size), 
                          'Cells': '15.0 15.0', 
                          'MAPROG': 'SIN',
                          'rotate': 0.0,  
                          'Type': 'image/x-fits'})
                    
        with NamedTemporaryFile(suffix='.fits', prefix='vlssr-') as th:
            try:
                result = urlopen('https://www.cv.nrao.edu/cgi-bin/newVLSSpostage.pl', data)
                th.write(result.read())
                th.flush()
                th.seek(0)
                
                hdulist = astrofits.open(th.name, 'readonly')
                for key in hdulist[0].header:
                    header[key] = hdulist[0].header[key]
                image = hdulist[0].data[0,0,:,:]
                hdulist.close()
            except (IOError, ValueError, RuntimeError) as error:
                self.statusbar.SetStatusText('Error loading image: %s' % str(error), 0)
                
        return header, image
        
    def onDisplay(self, event):
        """
        Load the VLSSr image of the selected candidate and show it.
        """
        
        index = self.listControl.GetNextSelected(-1)
        if index != -1:
            name = self.listControl.GetItem(index, 0)
            name = name.GetText()
            ra = self.listControl.GetItem(index, 1)
            ra = ra.GetText()
            dec = self.listControl.GetItem(index, 2)
            dec = dec.GetText()
            
            sz = self.szText.GetValue()
            try:
                sz = float(sz)
            except ValueError as error:
                self.statusbar.SetStatusText('Error displaying image: %s' % str(error), 0)
                sz = 0.5
            
            wx.BeginBusyCursor()
            header, image = self._loadVLSSrImage(ra, dec, size=sz)
            wx.EndBusyCursor()
            
            ImageViewer(self, name, ra, dec, header, image)
            
    def onKeyPress(self, event):
        if event.GetKeyCode() == wx.WXK_RETURN:
            self.onResolve(event)
            
    def onAbout(self, event):
        """
        Display a very very very brief 'about' window.
        """
        
        dialog = wx.AboutDialogInfo()
        
        dialog.SetIcon(wx.Icon(os.path.join(self.scriptPath, 'icons', 'lwa.png'), wx.BITMAP_TYPE_PNG))
        dialog.SetName('VLSSr Calibrator Search')
        dialog.SetVersion(__version__)
        dialog.SetDescription("""GUI for searching the VLSSr (Lane et al., 2012 Radio Science v. 47, RS0K04) to find sources suitable for phase calibrators for the LWA single baseline interferometer.\n\nLSL Version: %s""" % lsl.version.version)
        dialog.SetWebSite('http://lwa.unm.edu')
        dialog.AddDeveloper(__author__)
        
        # Debuggers/testers
        dialog.AddDocWriter(__author__)
        dialog.AddDocWriter('Ivey Davis')
        
        wx.AboutBox(dialog)
        
    def onQuit(self, event):
        """
        Quit the main window.
        """
        
        self.Destroy()


class ImageViewer(wx.Frame):
    def __init__(self, parent, name, ra, dec, header, image):
        wx.Frame.__init__(self, parent, title='VLSSr Field', size=(800, 375))
        
        self.parent = parent
        self.name = name
        self.ra = ra
        self.dec = dec
        self.header = header
        self.image = image
        
        self.initUI()
        self.initEvents()
        self.Show()
        
        self._state = {}
        self.initPlot()
            
    def initUI(self):
        """
        Start the user interface.
        """
        
        self.statusbar = self.CreateStatusBar()
        
        hbox = wx.BoxSizer(wx.HORIZONTAL)
        
        # Add plots to panel 1
        panel = wx.Panel(self, -1)
        vbox1 = wx.BoxSizer(wx.VERTICAL)
        self.figure = Figure()
        self.canvas = FigureCanvasWxAgg(panel, -1, self.figure)
        self.toolbar = NavigationToolbar2WxAgg(self.canvas)
        self.toolbar.Realize()
        vbox1.Add(self.canvas,  1, wx.ALIGN_TOP | wx.EXPAND)
        vbox1.Add(self.toolbar, 0, wx.ALIGN_BOTTOM)
        panel.SetSizer(vbox1)
        hbox.Add(panel, 1, wx.EXPAND)
        
        # Use some sizers to see layout options
        self.SetSizer(hbox)
        self.SetAutoLayout(1)
        hbox.Fit(self)
        
    def initEvents(self):
        """
        Bind the various events needed to make the GUI run.
        """
        
        pass
        
    def _sin_px_to_sky(self, x, y):
        """
        Convert pixel coordinates to sky coordinates for a simple SIN projection.
        """
    
        x = (x - (self.header['CRPIX1']-1))*self.header['CDELT1'] * numpy.pi/180
        y = (y - (self.header['CRPIX2']-1))*self.header['CDELT2'] * numpy.pi/180
        rho = numpy.sqrt(x*x + y*y)
        c = numpy.arcsin(rho)
        sc = numpy.sin(c)
        cc = numpy.cos(c)
        ra0 = self.header['CRVAL1'] * numpy.pi/180
        dec0 = self.header['CRVAL2'] * numpy.pi/180
        ra = ra0 \
             + numpy.arctan2(x*sc, rho*cc*numpy.cos(dec0) - y*sc*numpy.sin(dec0))
        if rho > 1e-10:
            dec = numpy.arcsin(cc*numpy.sin(dec0) + y*sc*numpy.cos(dec0)/rho)
        else:
            dec = dec0
        return ra*180/numpy.pi, dec*180/numpy.pi
        
    def _ra_ticks(self, t, tick_number):
        try:
            oh, om, os = self._state['ra_tick']
        except KeyError:
            oh, om, os = -99, -99, -99
            
        conv = lambda x: self._sin_px_to_sky(x, 0)[0]
        units = ('$^h$', '$^m$', '$^s$')
        
        value = conv(t) / 15.0
        value = value % 24.0
        h = int(value)
        m = int((value - h)*60) % 60
        s = int(value*3600) % 60.0
        
        if tick_number == 1 or h != oh:
            label = "%i%s%02i%s%02.0f%s" % (h, units[0], m, units[1], s, units[2])
        elif m != om:
            label = "%02i%s%02.0f%s" % (m, units[1], s, units[2])
        else:
            label = "%02.0f%s" % (s, units[2])
            
        self._state['ra_tick'] = (h, m, s)
        
        return label
        
    def _dec_ticks(self, t, tick_number):
        try:
            og, od, om, os = self._state['dec_tick']
        except KeyError:
            og, od, om, os = '&', -99, -99, -99
            
        conv = lambda y: self._sin_px_to_sky(0, y)[1]
        units = ('$^\\circ$', "'", '"')
        
        value = conv(t)
        g = '+' if value >= 0 else '-'
        value = abs(value)
        d = int(value)
        m = int((value - d)*60) % 60
        s = int(value*3600) % 60.0
        
        if tick_number == 1 or d != od or g != og:
            label = "%s%i%s%02i%s%02.0f%s" % (g, d, units[0], m, units[1], s, units[2])
        elif m != om:
            label = "%02i%s%02.0f%s" % (m, units[1], s, units[2])
        else:
            label = "%02.0f%s" % (s, units[2])
            
        self._state['dec_tick'] = (g, d, m, s)
        
        return label
        
    def initPlot(self):
        """
        Setup the image.
        """
        
        self.figure.clf()
        self.ax1 = self.figure.gca()
        
        peak = self.image.max()
        vmin = -1
        vmax = min([20.0, peak*0.5])
        
        c = self.ax1.imshow(self.image, origin='lower', interpolation='nearest', 
                            vmin=vmin, vmax=vmax, cmap='gist_yarg')
        self.ax1.xaxis.set_major_formatter(FuncFormatter(self._ra_ticks))
        self.ax1.yaxis.set_major_formatter(FuncFormatter(self._dec_ticks))
        self.ax1.set_xlabel('RA (J2000)')
        self.ax1.set_ylabel('Dec. (J2000)')
        self.ax1.set_title("%s\nPeak: %.1f Jy/beam" % (self.name, peak))
        cb = self.figure.colorbar(c, ax=self.ax1)
        cb.set_label('Jy/beam')
        self.figure.tight_layout()
        
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
            clickX = numpy.clip(int(round(clickX)), 0, self.image.shape[1]-1)
            clickY = numpy.clip(int(round(clickY)), 0, self.image.shape[0]-1)
            
            ra, dec = self._sin_px_to_sky(clickX, clickY)
            
            ra /= 15.0
            rh = int(ra)
            rm = int((ra - rh)*60) % 60
            rs = (ra*3600) % 60.0
            
            dg =  '-' if dec < 0 else '+'
            dec = abs(dec)
            dd = int(dec)
            dm = int((dec - dd)*60) % 60
            ds = (dec*3600) % 60.0
            
            self.statusbar.SetStatusText("%i:%02i:%05.2f, %s%i:%02i:%04.1f @ %4.1f Jy" % (rh, rm, rs, dg, dd, dm, ds, self.image[clickY, clickX]))
        else:
            self.statusbar.SetStatusText("")
            
    def disconnect(self):
        """
        Disconnect all the stored connection ids.
        """
        
        self.figure.canvas.mpl_disconnect(self.cidmotion)
        
    def GetToolBar(self):
        # You will need to override GetToolBar if you are using an 
        # unmanaged toolbar in your frame
        return self.toolbar
        

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description='GUI for finding a phase calibrator for the LWA single baseline interferometer', 
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
        )
    parser.add_argument('-t', '--target', type=str, 
                        help='target name')
    parser.add_argument('-r', '--ra', type=aph.hours, 
                        help='target RA; HH:MM:SS.SS format, J2000')
    parser.add_argument('-d', '--dec', type=aph.degrees, 
                        help='target declination; sDD:MM:SS.S format, J2000')
    args = parser.parse_args()
    
    app = wx.App()
    CalibratorSearch(None, title='VLSSr Calibrator Search', target=args.target, ra=args.ra, dec=args.dec)
    app.MainLoop()
    
