#!/usr/bin/env python3

import os
import re
import sys
import json
import ephem
import numpy
import argparse
import tkinter as tk
from tkinter import ttk, messagebox, font as tkfont
from functools import lru_cache
from urllib.request import urlopen
from urllib.parse import urlencode, quote_plus
from tempfile import NamedTemporaryFile
from xml.etree import ElementTree
import astropy.io.fits as astrofits

import matplotlib
matplotlib.use('TkAgg')
matplotlib.interactive(True)

from matplotlib.backends.backend_tkagg import NavigationToolbar2Tk, FigureCanvasTkAgg
from matplotlib.figure import Figure
from matplotlib.ticker import FuncFormatter, NullFormatter, NullLocator

import lsl
from lsl import astro
from lsl.misc import parser as aph


__version__ = "0.1"
__author__ = "Jayce Dowell"


SIMBAD_REF_RE = re.compile(r'^(\[(?P<ref>[A-Za-z0-9]+)\]\s*)')


class CalibratorSearch(tk.Tk):
    def __init__(self, target=None, ra=None, dec=None):
        super().__init__()

        self.title('VLSSr Calibrator Search')
        self.geometry('725x575')

        self.scriptPath = os.path.abspath(__file__)
        self.scriptPath = os.path.split(self.scriptPath)[0]

        self._loadRecommendedCalibrators()
        self.initUI()
        self.initEvents()

        if target is not None:
            self.nameText.delete(0, tk.END)
            self.nameText.insert(0, target)
            if ra is None or dec is None:
                self.onResolve()
        if ra is not None and dec is not None:
            self.raText.delete(0, tk.END)
            self.raText.insert(0, ra)
            self.decText.delete(0, tk.END)
            self.decText.insert(0, dec)

    def initUI(self):
        """
        Start the user interface.
        """

        # Menu bar
        menubar = tk.Menu(self)

        fileMenu = tk.Menu(menubar, tearoff=0)
        fileMenu.add_command(label='Quit', command=self.onQuit, accelerator='Ctrl+Q')
        menubar.add_cascade(label='File', menu=fileMenu)

        viewMenu = tk.Menu(menubar, tearoff=0)
        viewMenu.add_command(label='Recommended Calibrators...', command=self.onShowRecommendedCalibrators)
        menubar.add_cascade(label='View', menu=viewMenu)

        helpMenu = tk.Menu(menubar, tearoff=0)
        helpMenu.add_command(label='About', command=self.onAbout)
        menubar.add_cascade(label='Help', menu=helpMenu)

        self.config(menu=menubar)

        # Main container
        main_frame = ttk.Frame(self, padding="5")
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Create a bold font for section headers
        header_font = ('TkDefaultFont', 10, 'bold')

        # Target Parameters Section
        target_frame = ttk.LabelFrame(main_frame, text='Target Parameters', padding="5")
        target_frame.pack(fill=tk.X, pady=(0, 5))

        # Configure columns to expand - name entry and RA/Dec entries should expand
        target_frame.columnconfigure(1, weight=1)  # Name entry column
        target_frame.columnconfigure(4, weight=1)  # RA/Dec entry column

        # Use grid layout for target parameters
        # Row 0: Name, Name entry, Resolve, " - or - ", RA label, RA entry
        # Row 1: (empty), (empty), (empty), (empty), Dec label, Dec entry
        ttk.Label(target_frame, text='Name:').grid(row=0, column=0, sticky=tk.W, padx=5, pady=2)
        self.nameText = ttk.Entry(target_frame)
        self.nameText.grid(row=0, column=1, sticky=tk.EW, padx=5, pady=2)

        resolve_btn = ttk.Button(target_frame, text='Resolve', command=self.onResolve, width=10)
        resolve_btn.grid(row=1, column=1, sticky=tk.W, padx=5, pady=2)

        ttk.Label(target_frame, text=' - or - ').grid(row=0, column=2, padx=10, pady=2)

        ttk.Label(target_frame, text='RA:').grid(row=0, column=3, sticky=tk.E, padx=5, pady=2)
        self.raText = ttk.Entry(target_frame)
        self.raText.insert(0, 'HH:MM:SS.SS')
        self.raText.grid(row=0, column=4, sticky=tk.EW, padx=5, pady=2)

        ttk.Label(target_frame, text='Dec:').grid(row=1, column=3, sticky=tk.E, padx=5, pady=2)
        self.decText = ttk.Entry(target_frame)
        self.decText.insert(0, 'sDD:MM:SS.S')
        self.decText.grid(row=1, column=4, sticky=tk.EW, padx=5, pady=2)

        # VLSSr Search Parameters Section
        search_frame = ttk.LabelFrame(main_frame, text='VLSSr Search Parameters', padding="5")
        search_frame.pack(fill=tk.X, pady=(0, 5))

        # Configure columns - entry columns should expand
        search_frame.columnconfigure(2, weight=1)  # Min entry column
        search_frame.columnconfigure(5, weight=1)  # Max entry column

        # Use grid layout for search parameters
        # Row 0: Search Radius, Min label, Min entry, deg, Max label, Max entry, deg
        # Row 1: Flux Density, Min label, Min entry, Jy, (empty), Search button
        ttk.Label(search_frame, text='Search Radius').grid(row=0, column=0, sticky=tk.W, padx=5, pady=2)
        ttk.Label(search_frame, text='Min:').grid(row=0, column=1, sticky=tk.E, padx=5, pady=2)
        self.ldText = ttk.Entry(search_frame)
        self.ldText.insert(0, '0.0')
        self.ldText.grid(row=0, column=2, sticky=tk.EW, padx=2, pady=2)
        ttk.Label(search_frame, text='deg').grid(row=0, column=3, sticky=tk.W, padx=2, pady=2)

        ttk.Label(search_frame, text='Max:').grid(row=0, column=4, sticky=tk.E, padx=5, pady=2)
        self.udText = ttk.Entry(search_frame)
        self.udText.insert(0, '3.0')
        self.udText.grid(row=0, column=5, sticky=tk.EW, padx=2, pady=2)
        ttk.Label(search_frame, text='deg').grid(row=0, column=6, sticky=tk.W, padx=2, pady=2)

        ttk.Label(search_frame, text='Flux Density').grid(row=1, column=0, sticky=tk.W, padx=5, pady=2)
        ttk.Label(search_frame, text='Min:').grid(row=1, column=1, sticky=tk.E, padx=5, pady=2)
        self.fdText = ttk.Entry(search_frame)
        self.fdText.insert(0, '10.0')
        self.fdText.grid(row=1, column=2, sticky=tk.EW, padx=2, pady=2)
        ttk.Label(search_frame, text='Jy').grid(row=1, column=3, sticky=tk.W, padx=2, pady=2)

        search_btn = ttk.Button(search_frame, text='Search', command=self.onSearch, width=10)
        search_btn.grid(row=1, column=5, sticky=tk.E, padx=2, pady=2)

        # Candidates Section
        candidates_frame = ttk.LabelFrame(main_frame, text='Candidates', padding="5")
        candidates_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 5))

        # Treeview for candidates list
        columns = ('name', 'ra', 'dec', 'dist', 'flux', 'size')
        self.listControl = ttk.Treeview(candidates_frame, columns=columns, show='headings', height=10)

        self.listControl.heading('name', text='Name')
        self.listControl.heading('ra', text='RA (J2000)')
        self.listControl.heading('dec', text='Dec (J2000)')
        self.listControl.heading('dist', text='Dist. (deg)')
        self.listControl.heading('flux', text='Flux (Jy)')
        self.listControl.heading('size', text='Size')

        self.listControl.column('name', width=125)
        self.listControl.column('ra', width=100)
        self.listControl.column('dec', width=100)
        self.listControl.column('dist', width=80)
        self.listControl.column('flux', width=80)
        self.listControl.column('size', width=180)

        # Scrollbar for treeview
        scrollbar = ttk.Scrollbar(candidates_frame, orient=tk.VERTICAL, command=self.listControl.yview)
        self.listControl.configure(yscrollcommand=scrollbar.set)

        self.listControl.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # Configure tags for coloring rows (with italic font for flagged items)
        italic_font = tkfont.nametofont('TkDefaultFont').copy()
        italic_font.configure(slant='italic')
        self.listControl.tag_configure('italic_only', font=italic_font)
        self.listControl.tag_configure('warning', foreground='orange', font=italic_font)
        self.listControl.tag_configure('error', foreground='red', font=italic_font)

        # Configure tags for recommended calibrator grades
        self.listControl.tag_configure('grade_a', foreground='green')
        self.listControl.tag_configure('grade_b', foreground='#0099cc')  # Cyan/teal
        self.listControl.tag_configure('grade_nr', foreground='orange', font=italic_font)

        # Display controls
        display_frame = ttk.Frame(main_frame)
        display_frame.pack(fill=tk.X, pady=5)

        ttk.Label(display_frame, text='Image Size:').pack(side=tk.LEFT, padx=(0, 5))
        self.szText = ttk.Entry(display_frame, width=8)
        self.szText.insert(0, '1.0')
        self.szText.pack(side=tk.LEFT, padx=(0, 2))
        ttk.Label(display_frame, text='deg').pack(side=tk.LEFT, padx=(0, 10))

        display_btn = ttk.Button(display_frame, text='Display Selected', command=self.onDisplay, width=15)
        display_btn.pack(side=tk.RIGHT, padx=5)

        # Status bar
        self.statusbar = ttk.Label(main_frame, text='', relief=tk.SUNKEN, anchor=tk.W)
        self.statusbar.pack(fill=tk.X, side=tk.BOTTOM)

    def initEvents(self):
        """
        Bind the various events needed to make the GUI run.
        """

        # Keyboard shortcuts
        self.bind('<Control-q>', lambda e: self.onQuit())
        self.nameText.bind('<Return>', lambda e: self.onResolve())

        # Window close
        self.protocol("WM_DELETE_WINDOW", self.onQuit)

    def _loadRecommendedCalibrators(self):
        """
        Load the recommended calibrators from the JSON file in docs/.
        Creates a dictionary mapping source names to their quality grades.
        """

        self.recommended_calibrators = {}

        json_path = os.path.join(self.scriptPath, 'docs', 'recommended_calibrators.json')
        try:
            with open(json_path, 'r') as f:
                data = json.load(f)
            for cal in data.get('calibrators', []):
                name = cal['name']
                # Store both the original name and common variations
                self.recommended_calibrators[name] = cal['quality']
                # Also store without spaces (e.g., "3C 48" -> "3C48")
                self.recommended_calibrators[name.replace(' ', '')] = cal['quality']
        except (IOError, json.JSONDecodeError) as e:
            print(f"Warning: Could not load recommended calibrators: {e}")

    def _getRecommendedGrade(self, name):
        """
        Check if a source name matches a recommended calibrator.
        Returns the quality grade ('A', 'B', 'NR') or None if not found.
        """

        # Try exact match first
        if name in self.recommended_calibrators:
            return self.recommended_calibrators[name]
        # Try without spaces
        name_no_space = name.replace(' ', '')
        if name_no_space in self.recommended_calibrators:
            return self.recommended_calibrators[name_no_space]
        return None

    def _clearCandidates(self):
        """
        Clear everything out of the candidates list.
        """

        for item in self.listControl.get_children():
            self.listControl.delete(item)

    def onResolve(self, event=None):
        """
        Resolve the target name into a set of coordinates and update the display.
        """

        source = self.nameText.get()

        if source != '':
            try:
                posn = astro.resolve_name(source)
                self.raText.delete(0, tk.END)
                self.raText.insert(0, str(astro.deg_to_hms(posn.ra)).replace(' ', ':'))
                self.decText.delete(0, tk.END)
                self.decText.insert(0, str(astro.deg_to_dms(posn.dec)).replace(' ', ':'))

                self._clearCandidates()

            except RuntimeError as error:
                self.statusbar.config(text=f"Error resolving source: {str(error)}")
                self.raText.delete(0, tk.END)
                self.raText.insert(0, "HH:MM:SS.SS")
                self.decText.delete(0, tk.END)
                self.decText.insert(0, "sDD:MM:SS.S")

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
            self.statusbar.config(text=f"Error during name lookup: {str(error)}")

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
            result = urlopen(f"https://cdsweb.u-strasbg.fr/cgi-bin/nph-sesame/-oxpI/SNV?{quote_plus(name)}")
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
            self.statusbar.config(text=f"Error during radio name lookup: {str(error)}")

        return final_name

    def onSearch(self, event=None):
        """
        Search for VLSSr sources around the target position using the constraints
        provided, and update the candidate list.
        """

        # Load in the values
        ## Coordinates
        ra = self.raText.get()
        dec = self.decText.get()
        try:
            ephem.hours(str(ra))
            ephem.degrees(str(dec))
        except (ValueError, TypeError):
            return False
        ## Search criteria
        flux = float(self.fdText.get())
        min_dist = float(self.ldText.get())
        max_dist = float(self.udText.get())
        if max_dist > 10.0:
            ### Limit ourselves to a 10 degree search
            max_dist = 10.0
            self.udText.delete(0, tk.END)
            self.udText.insert(0, f"{max_dist:.1f}")

        self.config(cursor="watch")
        self.update()

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
            result = urlopen('https://www.cv.nrao.edu/cgi-bin/newVLSSlist.pl', data.encode())
            lines = result.readlines()

            ## Parse
            inside = False
            for line in lines:
                line = line.decode()
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
            self.statusbar.config(text=f"Error during search: {str(error)}")

        ## Update the status bar
        if len(candidates) == 0:
            self.statusbar.config(text='No candidates found matching the search criteria')
        else:
            self.statusbar.config(text=f"Found {len(candidates)} candidates matching the search criteria")

        ## Sort by distance from the target
        candidates.sort(key=lambda x:x[3])

        self.config(cursor="")

        # Update the candidate list in the window
        self._clearCandidates()
        for i,candidate in enumerate(candidates):
            name,ra,dec,sep,flux,maj,min_size,pa = candidate

            # Determine tags for row styling
            tags = []
            
            ## Flag things that look like they might be too far away
            if sep >= 3.5:
                tags.append('error')
            elif sep > 3.0:
                tags.append('warning')
                
            ## Flag things that look like they might be too faint
            if flux < 5.0:
                tags.append('italic_only')

            ## Flag things that look like they might be too large
            try:
                maj = float(maj)
                if maj >= 40.0:
                    tags.append('error')
            except ValueError:
                pass

            ## Check if this is a recommended calibrator
            ## Only apply grade coloring if there are no warning/error tags
            grade = self._getRecommendedGrade(name)
            has_warning = any(t in ('warning', 'error') for t in tags)
            if grade and not has_warning:
                if grade == 'A':
                    tags.append('grade_a')
                elif grade == 'B':
                    tags.append('grade_b')
                elif grade == 'NR':
                    tags.append('grade_nr')

            # Append grade to name for clarity
            display_name = f"{name} ({grade})" if grade else name

            self.listControl.insert('', tk.END, values=(
                display_name, ra, dec, f"{sep:.1f}", f"{flux:.1f}", f'{maj}" by {min_size}" @ {pa}'
            ), tags=tags)

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
                result = urlopen('https://www.cv.nrao.edu/cgi-bin/newVLSSpostage.pl', data.encode())
                th.write(result.read())
                th.flush()
                th.seek(0)

                hdulist = astrofits.open(th.name, 'readonly')
                for key in hdulist[0].header:
                    header[key] = hdulist[0].header[key]
                image = hdulist[0].data[0,0,:,:]
                hdulist.close()
            except (IOError, ValueError, RuntimeError) as error:
                self.statusbar.config(text=f"Error loading image: {str(error)}")

        return header, image

    def onDisplay(self, event=None):
        """
        Load the VLSSr image of the selected candidate and show it.
        """

        selection = self.listControl.selection()
        if selection:
            item = selection[0]
            values = self.listControl.item(item, 'values')
            name = values[0]
            ra = values[1]
            dec = values[2]

            sz = self.szText.get()
            try:
                sz = float(sz)
            except ValueError as error:
                self.statusbar.config(text=f"Error displaying image: {str(error)}")
                sz = 0.5

            self.config(cursor="watch")
            self.update()
            header, image = self._loadVLSSrImage(ra, dec, size=sz)
            self.config(cursor="")

            ImageViewer(self, name, ra, dec, header, image)

    def onShowRecommendedCalibrators(self):
        """
        Show a dialog listing all recommended calibrators.
        """

        # Load the full calibrator data from JSON
        json_path = os.path.join(self.scriptPath, 'docs', 'recommended_calibrators.json')
        try:
            with open(json_path, 'r') as f:
                data = json.load(f)
        except (IOError, json.JSONDecodeError) as e:
            messagebox.showerror("Error", f"Could not load calibrator data: {e}")
            return

        # Create dialog window
        dialog = tk.Toplevel(self)
        dialog.title('Recommended Calibrators')
        dialog.geometry('700x400')
        dialog.transient(self)

        main_frame = ttk.Frame(dialog, padding="5")
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Reference label
        ref_text = data.get('reference', '')
        ref_label = ttk.Label(main_frame, text=ref_text, wraplength=680, font=('TkDefaultFont', 9, 'italic'))
        ref_label.pack(fill=tk.X, pady=(0, 10))

        # Create treeview for calibrators
        columns = ('name', 'quality', 'ra', 'dec', 'flux')
        tree = ttk.Treeview(main_frame, columns=columns, show='headings', height=15)

        tree.heading('name', text='Name')
        tree.heading('quality', text='Grade')
        tree.heading('ra', text='RA (J2000)')
        tree.heading('dec', text='Dec (J2000)')
        tree.heading('flux', text='Flux 66MHz (Jy)')

        tree.column('name', width=100)
        tree.column('quality', width=60)
        tree.column('ra', width=120)
        tree.column('dec', width=120)
        tree.column('flux', width=100)

        # Configure tags for grade coloring
        tree.tag_configure('grade_a', foreground='green')
        tree.tag_configure('grade_b', foreground='#0099cc')
        italic_font = tkfont.nametofont('TkDefaultFont').copy()
        italic_font.configure(slant='italic')
        tree.tag_configure('grade_nr', foreground='orange', font=italic_font)

        # Scrollbar
        scrollbar = ttk.Scrollbar(main_frame, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)

        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # Populate the treeview
        for cal in data.get('calibrators', []):
            quality = cal['quality']
            if quality == 'A':
                tag = 'grade_a'
            elif quality == 'B':
                tag = 'grade_b'
            else:
                tag = 'grade_nr'

            tree.insert('', tk.END, values=(
                cal['name'],
                quality,
                cal['ra'],
                cal['dec'],
                cal['flux_66mhz']
            ), tags=(tag,))

        # Close button
        btn_frame = ttk.Frame(dialog)
        btn_frame.pack(fill=tk.X, pady=5)
        close_btn = ttk.Button(btn_frame, text='Close', command=dialog.destroy)
        close_btn.pack(side=tk.RIGHT, padx=5)

    def onAbout(self):
        """
        Display a very very very brief 'about' window.
        """

        about_text = f"""VLSSr Calibrator Search
Version {__version__}

GUI for searching the VLSSr (Lane et al., 2012 Radio Science v. 47, RS0K04) to find sources suitable for phase calibrators for the LWA single baseline interferometer.

LSL Version: {lsl.version.version}

Developer: {__author__}
Website: http://lwa.unm.edu"""

        messagebox.showinfo("About VLSSr Calibrator Search", about_text)

    def onQuit(self):
        """
        Quit the main window.
        """

        self.destroy()


class ImageViewer(tk.Toplevel):
    def __init__(self, parent, name, ra, dec, header, image):
        super().__init__(parent)

        self.title('VLSSr Field')

        self.parent = parent
        self.name = name
        self.ra = ra
        self.dec = dec
        self.header = header
        self.image = image

        self._state = {}

        self.initUI()
        self.initEvents()
        self.initPlot()

    def initUI(self):
        """
        Start the user interface.
        """

        # Main frame
        main_frame = ttk.Frame(self)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Matplotlib figure
        self.figure = Figure(figsize=(5, 4), dpi=100)
        self.canvas = FigureCanvasTkAgg(self.figure, master=main_frame)
        self.canvas.get_tk_widget().pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        # Navigation toolbar
        self.toolbar = NavigationToolbar2Tk(self.canvas, main_frame)
        self.toolbar.update()

        # Status bar
        self.statusbar = ttk.Label(self, text='', relief=tk.SUNKEN, anchor=tk.W)
        self.statusbar.pack(fill=tk.X, side=tk.BOTTOM)

    def initEvents(self):
        """
        Bind the various events needed to make the GUI run.
        """

        # Resize the plot when the window is resized
        self.bind('<Configure>', self.resizePlots)

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
        self.ax1.set_title(f"{self.name}\nPeak: {peak:.1f} Jy/beam")
        cb = self.figure.colorbar(c, ax=self.ax1)
        cb.set_label('Jy/beam')
        self.figure.tight_layout()

        self.canvas.draw()
        self.connect()

    def connect(self):
        """
        Connect to all the events we need to interact with the plots.
        """

        self.cidmotion = self.figure.canvas.mpl_connect('motion_notify_event', self.on_motion)

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

            self.statusbar.config(text="%i:%02i:%05.2f, %s%i:%02i:%04.1f @ %4.1f Jy" % (rh, rm, rs, dg, dd, dm, ds, self.image[clickY, clickX]))
        else:
            self.statusbar.config(text="")

    def disconnect(self):
        """
        Disconnect all the stored connection ids.
        """

        self.figure.canvas.mpl_disconnect(self.cidmotion)

    def resizePlots(self, event):
        """
        Resize the matplotlib figure when the window is resized.
        """

        # Only respond to resize events for this window
        if event.widget != self:
            return

        # Get the current window size and toolbar height
        w = event.width
        h = event.height
        toolbar_h = self.toolbar.winfo_height()
        statusbar_h = self.statusbar.winfo_height()

        # Calculate new figure size in inches
        dpi = self.figure.get_dpi()
        new_w = max(1, w) / dpi
        new_h = max(1, h - toolbar_h - statusbar_h) / dpi

        # Update figure size and redraw
        self.figure.set_size_inches(new_w, new_h)
        self.figure.tight_layout()
        self.canvas.draw()


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

    app = CalibratorSearch(target=args.target, ra=args.ra, dec=args.dec)
    app.mainloop()
