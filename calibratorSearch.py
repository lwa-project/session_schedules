#!/usr/bin/env python3

"""
Display VLSSr sources for calibration in an interactive GUI.
"""

import os
import re
import sys
import ephem
import numpy as np
import argparse
from functools import lru_cache
from urllib.request import urlopen
from urllib.parse import urlencode, quote_plus
from tempfile import NamedTemporaryFile
from xml.etree import ElementTree
import astropy.io.fits as astrofits

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from PIL import Image, ImageTk

import matplotlib
matplotlib.use('TkAgg')
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.figure import Figure
from matplotlib.ticker import FuncFormatter, NullFormatter, NullLocator

import lsl
from lsl import astro
from lsl.misc import parser as aph

__version__ = "0.2"
__author__ = "Jayce Dowell"


SIMBAD_REF_RE = re.compile('^(\[(?P<ref>[A-Za-z0-9]+)\]\s*)')


class CalibratorSearch(tk.Tk):
    def __init__(self, target=None, ra=None, dec=None):
        super().__init__()
        
        self.title('VLSSr Calibrator Search')
        self.geometry('725x575')
        
        self.scriptPath = os.path.abspath(__file__)
        self.scriptPath = os.path.split(self.scriptPath)[0]
        
        # Create the menu
        self.create_menu()
        
        # Create the main interface
        self.create_widgets()
        
        # Status bar
        self.statusbar = tk.Label(self, text="", bd=1, relief=tk.SUNKEN, anchor=tk.W)
        self.statusbar.pack(side=tk.BOTTOM, fill=tk.X)
        
        # Apply initial values if provided
        if target is not None:
            self.nameText.insert(0, target)
            if ra is None or dec is None:
                self.on_resolve()
        if ra is not None and dec is not None:
            self.raText.delete(0, tk.END)
            self.raText.insert(0, ra)
            self.decText.delete(0, tk.END)
            self.decText.insert(0, dec)
    
    def create_menu(self):
        menubar = tk.Menu(self)
        
        # File menu
        filemenu = tk.Menu(menubar, tearoff=0)
        filemenu.add_command(label="Quit", command=self.on_quit)
        menubar.add_cascade(label="File", menu=filemenu)
        
        # Help menu
        helpmenu = tk.Menu(menubar, tearoff=0)
        helpmenu.add_command(label="About", command=self.on_about)
        menubar.add_cascade(label="Help", menu=helpmenu)
        
        self.config(menu=menubar)
    
    def create_widgets(self):
        main_frame = tk.Frame(self)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # Target name/search section
        target_frame = tk.LabelFrame(main_frame, text="Target Parameters")
        target_frame.pack(fill=tk.X, padx=5, pady=5)
        
        # Name input
        name_frame = tk.Frame(target_frame)
        name_frame.pack(fill=tk.X, padx=5, pady=5)
        
        name_label = tk.Label(name_frame, text="Name:")
        name_label.pack(side=tk.LEFT, padx=5)
        
        self.nameText = tk.Entry(name_frame, width=20)
        self.nameText.pack(side=tk.LEFT, padx=5, expand=True, fill=tk.X)
        
        self.raText = tk.Entry(name_frame, width=20)
        self.raText.insert(0, "HH:MM:SS.SS")
        self.raText.pack(side=tk.RIGHT, padx=5, expand=True, fill=tk.X)
        
        ra_label = tk.Label(name_frame, text=" RA:")
        ra_label.pack(side=tk.RIGHT, padx=5)
        
        separator = tk.Label(name_frame, text=" - or - ")
        separator.pack(side=tk.RIGHT, padx=10)
        
        # Dec input
        dec_frame = tk.Frame(target_frame)
        dec_frame.pack(fill=tk.X, padx=5, pady=5)
        
        # Add padding to align with name field
        padding = tk.Label(dec_frame, text="     ")
        padding.pack(side=tk.LEFT, padx=5)
        
        resolve_button = tk.Button(dec_frame, text="Resolve", width=20, command=self.on_resolve)
        resolve_button.pack(side=tk.LEFT, padx=5, expand=True, fill=tk.X)
        
        self.decText = tk.Entry(dec_frame, width=20)
        self.decText.insert(0, "sDD:MM:SS.S")
        self.decText.pack(side=tk.RIGHT, padx=5, expand=True, fill=tk.X)
        
        dec_label = tk.Label(dec_frame, text="Dec:")
        dec_label.pack(side=tk.RIGHT, padx=5)
        
        separator = tk.Label(dec_frame, text="        ")
        separator.pack(side=tk.RIGHT, padx=10)
        
        # Search parameters
        search_frame = tk.LabelFrame(main_frame, text="VLSSr Search Parameters")
        search_frame.pack(fill=tk.X, padx=5, pady=5)
        
        # Search radius
        radius_frame = tk.Frame(search_frame)
        radius_frame.pack(fill=tk.X, padx=5, pady=5)
        
        radius_label = tk.Label(radius_frame, text="Search Radius")
        radius_label.pack(side=tk.LEFT, padx=5)
        
        min_label = tk.Label(radius_frame, text="Min:")
        min_label.pack(side=tk.LEFT, padx=5)
        
        self.ldText = tk.Entry(radius_frame, width=10)
        self.ldText.insert(0, "0.0")
        self.ldText.pack(side=tk.LEFT, padx=5)
        
        min_unit = tk.Label(radius_frame, text="deg")
        min_unit.pack(side=tk.LEFT)
        
        max_label = tk.Label(radius_frame, text="Max:")
        max_label.pack(side=tk.LEFT, padx=5)
        
        self.udText = tk.Entry(radius_frame, width=10)
        self.udText.insert(0, "3.0")
        self.udText.pack(side=tk.LEFT, padx=5)
        
        max_unit = tk.Label(radius_frame, text="deg")
        max_unit.pack(side=tk.LEFT)
        
        # Flux density
        flux_frame = tk.Frame(search_frame)
        flux_frame.pack(fill=tk.X, padx=5, pady=5)
        
        flux_label = tk.Label(flux_frame, text="Flux Density")
        flux_label.pack(side=tk.LEFT, padx=5)
        
        min_flux_label = tk.Label(flux_frame, text="Min:")
        min_flux_label.pack(side=tk.LEFT, padx=5)
        
        self.fdText = tk.Entry(flux_frame, width=10)
        self.fdText.insert(0, "10.0")
        self.fdText.pack(side=tk.LEFT, padx=5)
        
        flux_unit = tk.Label(flux_frame, text="Jy")
        flux_unit.pack(side=tk.LEFT)
        
        search_button = tk.Button(flux_frame, text="Search", command=self.on_search)
        search_button.pack(side=tk.RIGHT, padx=5)
        
        # Candidates list
        candidates_frame = tk.LabelFrame(main_frame, text="Candidates")
        candidates_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # Create Treeview for candidates
        columns = ('name', 'ra', 'dec', 'dist', 'flux', 'size')
        self.listControl = ttk.Treeview(candidates_frame, columns=columns, show='headings')
        
        # Configure column headings
        self.listControl.heading('name', text='Name')
        self.listControl.heading('ra', text='RA (J2000)')
        self.listControl.heading('dec', text='Dec (J2000)')
        self.listControl.heading('dist', text='Dist. (deg)')
        self.listControl.heading('flux', text='Flux (Jy)')
        self.listControl.heading('size', text='Size')
        
        # Configure column widths
        self.listControl.column('name', width=125)
        self.listControl.column('ra', width=100)
        self.listControl.column('dec', width=100)
        self.listControl.column('dist', width=75)
        self.listControl.column('flux', width=100)
        self.listControl.column('size', width=200)
        
        # Add scrollbars
        scrollbar_y = ttk.Scrollbar(candidates_frame, orient=tk.VERTICAL, command=self.listControl.yview)
        self.listControl.configure(yscrollcommand=scrollbar_y.set)
        
        scrollbar_x = ttk.Scrollbar(candidates_frame, orient=tk.HORIZONTAL, command=self.listControl.xview)
        self.listControl.configure(xscrollcommand=scrollbar_x.set)
        
        # Grid layout for the treeview and scrollbars
        self.listControl.grid(row=0, column=0, sticky='nsew')
        scrollbar_y.grid(row=0, column=1, sticky='ns')
        scrollbar_x.grid(row=1, column=0, sticky='ew')
        
        # Configure grid weights
        candidates_frame.grid_rowconfigure(0, weight=1)
        candidates_frame.grid_columnconfigure(0, weight=1)
        
        # Controls below the list
        controls_frame = tk.Frame(candidates_frame)
        controls_frame.grid(row=2, column=0, columnspan=2, sticky='ew', padx=5, pady=5)
        
        size_label = tk.Label(controls_frame, text="Image Size:")
        size_label.pack(side=tk.LEFT, padx=5)
        
        self.szText = tk.Entry(controls_frame, width=10)
        self.szText.insert(0, "1.0")
        self.szText.pack(side=tk.LEFT, padx=5)
        
        size_unit = tk.Label(controls_frame, text="deg")
        size_unit.pack(side=tk.LEFT)
        
        display_button = tk.Button(controls_frame, text="Display Selected", command=self.on_display)
        display_button.pack(side=tk.RIGHT, padx=5)
        
        # Bind keyboard events
        self.nameText.bind("<KeyRelease>", self.on_key_press)
        
        # Bind selection event to the treeview
        self.listControl.bind('<<TreeviewSelect>>', self.on_select_item)
        
    def on_key_press(self, event):
        if event.keysym == 'Return':
            self.on_resolve()
    
    def on_select_item(self, event):
        # Get selected item
        selection = self.listControl.selection()
        if selection:
            # Enable the display button
            pass
    
    def _clear_candidates(self):
        """Clear everything out of the candidates list."""
        for item in self.listControl.get_children():
            self.listControl.delete(item)
    
    def on_resolve(self, event=None):
        """Resolve the target name into coordinates and update the display."""
        source = self.nameText.get()
        
        if source != '':
            try:
                posn = astro.resolve_name(source)
                
                # Update RA/Dec fields
                ra_str = str(astro.deg_to_hms(posn.ra)).replace(' ', ':')
                dec_str = str(astro.deg_to_dms(posn.dec)).replace(' ', ':')
                
                self.raText.delete(0, tk.END)
                self.raText.insert(0, ra_str)
                self.decText.delete(0, tk.END)
                self.decText.insert(0, dec_str)
                
                self._clear_candidates()
                
            except RuntimeError as error:
                self.statusbar.config(text=f"Error resolving source: {str(error)}")
                self.raText.delete(0, tk.END)
                self.raText.insert(0, "HH:MM:SS.SS")
                self.decText.delete(0, tk.END)
                self.decText.insert(0, "sDD:MM:SS.S")
                
    def _reverse_name_lookup(self, ra, dec, radius_arcsec=15.0):
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
            self.statusbar.SetStatusText(f"Error during name lookup: {str(error)}", 0)
            
        if final_name == '---':
            # Try with a bigger search area
            final_name = self._reverse_name_lookup(ra, dec, radius_arcsec=2*radius_arcsec)
        else:
            # Try to get a "better" name for the source
            final_name = self._radio_name_query(final_name)
            
        return final_name
        
    @lru_cache(maxsize=64)
    def _radio_name_query(self, name):
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
            self.statusbar.SetStatusText(f"Error during radio name lookup: {str(error)}", 0)
            
        return final_name
        
    def on_search(self, event=None):
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
            ### Limit to 10 degree search
            max_dist = 10.0
            self.udText.delete(0, tk.END)
            self.udText.insert(0, f"{max_dist:.1f}")
        
        # Show busy cursor
        self.config(cursor="wait")
        self.update()
        
        # Find candidates
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
                    name = self._reverse_name_lookup(ra, dec)
                    candidates.append((name, ra, dec, sep, flux, maj, min, pa))
        
        except (IOError, ValueError, RuntimeError) as error:
            self.statusbar.config(text=f"Error during search: {str(error)}")
            
        ## Update the status bar
        if len(candidates) == 0:
            self.statusbar.config(text='No candidates found matching the search criteria')
        else:
            self.statusbar.config(text=f"Found {len(candidates)} candidates matching the search criteria")
            
        ## Sort by distance from the target
        candidates.sort(key=lambda x: x[3])
        
        # Restore normal cursor
        self.config(cursor="")
        
        # Create styles for formatting different rows
        style = ttk.Style()
        style.configure("Warning.Treeview.Item", foreground="#FFA500")
        style.configure("Error.Treeview.Item", foreground="red")
        
        # Update the candidate list in the window
        self._clear_candidates()
        for i, candidate in enumerate(candidates):
            name, ra, dec, sep, flux, maj, min, pa = candidate
            item_id = self.listControl.insert('', 'end', values=(
                name, ra, dec, f"{sep:.1f}", f"{flux:.1f}", f"{maj}\" by {min}\" @ {pa}")
            )
            
            # Flag items that look like they might be too far away
            if sep >= 3.5:
                self.listControl.item(item_id, tags=('error',))
            elif sep > 3.0:
                self.listControl.item(item_id, tags=('warning',))
                
            # Apply the styles
            self.listControl.tag_configure('warning', foreground="#FFA500")
            self.listControl.tag_configure('error', foreground='red')
            
    @lru_cache(maxsize=4)
    def _load_vlssr_image(self, ra, dec, size=0.5):
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
                self.statusbar.SetStatusText(f"Error loading image: {str(error)}", 0)
                
        return header, image
        
    def on_display(self, event=None):
        """
        Load the VLSSr image of the selected candidate and show it.
        """
        
        # Get selected item from Treeview
        selected_items = self.listControl.selection()
        if selected_items:  # Check if anything is selected
            index = selected_items[0]  # Get the first selected item
            
            # Get values from the selected row in the Treeview
            item_values = self.listControl.item(index, "values")
            name = item_values[0]  # Assuming name is the first column
            ra = item_values[1]    # Assuming RA is the second column
            dec = item_values[2]   # Assuming Dec is the third column
            
            # Get size value from text entry
            sz = self.szText.get()  # In Tkinter, use get() instead of GetValue()
            try:
                sz = float(sz)
            except ValueError as error:
                # Update status bar with error
                self.statusbar.config(text=f"Error displaying image: {str(error)}")  # Tkinter way to update status
                sz = 0.5
            
            # Show busy cursor
            self.config(cursor="watch")
            self.update()  # Force update to show the cursor change
            
            # Load the image
            header, image = self._load_vlssr_image(ra, dec, size=sz)
            
            # Restore cursor
            self.config(cursor="")
            
            # Display the image using your ImageViewer class
            ImageViewer(self, name, ra, dec, header, image)
            
    def on_about(self, event=None):
        """
        Display a very very very brief 'about' window.
        """
        # Create a custom toplevel window
        about_window = tk.Toplevel(self)
        about_window.title("About VLSSr Calibrator Search")
        about_window.geometry("400x450")
        about_window.resizable(False, False)
        
        # Try to load the icon
        try:
            icon_path = os.path.join(self.scriptPath, 'icons', 'lwa.png')
            if os.path.exists(icon_path):
                img = Image.open(icon_path)
                icon = ImageTk.PhotoImage(img)
                about_window.iconphoto(False, icon)
                
                # Also display the icon in the window
                icon_label = tk.Label(about_window, image=icon)
                icon_label.image = icon  # Keep a reference
                icon_label.pack(pady=10)
        except Exception as e:
            print(f"Could not load icon: {e}")
        
        # Add name and version
        tk.Label(about_window, text="VLSSr Calibrator Search", font=("Arial", 14, "bold")).pack(pady=5)
        tk.Label(about_window, text=f"Version: {__version__}").pack()
        
        # Description
        description = """GUI for searching the VLSSr (Lane et al., 2012 Radio Science v. 47, RS0K04) to find sources 
suitable for phase calibrators for the LWA single baseline interferometer.

LSL Version: {}""".format(lsl.version.version)
        
        desc_label = tk.Label(about_window, text=description, justify=tk.CENTER, wraplength=350)
        desc_label.pack(pady=10, padx=20)
        
        # Website
        website_frame = tk.Frame(about_window)
        website_frame.pack(pady=5)
        tk.Label(website_frame, text="Website: ").pack(side=tk.LEFT)
        website_link = tk.Label(website_frame, text="http://lwa.unm.edu", fg="blue", cursor="hand2")
        website_link.pack(side=tk.LEFT)
        website_link.bind("<Button-1>", lambda e: self.open_website("http://lwa.unm.edu"))
        
        # Developers and Doc Writers
        tk.Label(about_window, text=f"Developer: {__author__}").pack(pady=2)
        tk.Label(about_window, text=f"Documentation: {__author__}, Ivey Davis").pack(pady=2)
        
        # Close button
        tk.Button(about_window, text="Close", command=about_window.destroy).pack(pady=10)

    def open_website(self, url):
        """
        Open a website URL in the default browser
        """
        import webbrowser
        webbrowser.open(url)
    
    def on_quit(self, event=None):
        """
        Quit the main window.
        """
        
        self.destroy()


class ImageViewer(tk.Toplevel):
    def __init__(self, parent, name, ra, dec, header, image):
        super().__init__(parent)
        
        self.title('VLSSr Field')
        self.geometry('500x400')
        
        self.parent = parent
        self.name = name
        self.ra = ra
        self.dec = dec
        self.header = header
        self.image = image
        
        self.create_widgets()
        self._state = {}
        self.init_plot()
        
    def create_widgets(self):
        # Create the main frame
        main_frame = tk.Frame(self)
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # Create matplotlib figure and canvas
        self.figure = Figure(figsize=(8, 6), dpi=100)
        self.canvas = FigureCanvasTkAgg(self.figure, main_frame)
        self.canvas.get_tk_widget().pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        
        # Add navigation toolbar
        self.toolbar = NavigationToolbar2Tk(self.canvas, main_frame)
        self.toolbar.update()
        self.canvas.get_tk_widget().pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        
        # Status bar
        self.statusbar = tk.Label(self, text="", bd=1, relief=tk.SUNKEN, anchor=tk.W)
        self.statusbar.pack(side=tk.BOTTOM, fill=tk.X)
        
        # Bind resize event
        self.bind("<Configure>", self.resizePlots)
        
    def _sin_px_to_sky(self, x, y):
        """
        Convert pixel coordinates to sky coordinates for a simple SIN projection.
        """
    
        x = (x - (self.header['CRPIX1']-1))*self.header['CDELT1'] * np.pi/180
        y = (y - (self.header['CRPIX2']-1))*self.header['CDELT2'] * np.pi/180
        rho = np.sqrt(x*x + y*y)
        c = np.arcsin(rho)
        sc = np.sin(c)
        cc = np.cos(c)
        ra0 = self.header['CRVAL1'] * np.pi/180
        dec0 = self.header['CRVAL2'] * np.pi/180
        ra = ra0 + np.arctan2(x*sc, rho*cc*np.cos(dec0) - y*sc*np.sin(dec0))
        if rho > 1e-10:
            dec = np.arcsin(cc*np.sin(dec0) + y*sc*np.cos(dec0)/rho)
        else:
            dec = dec0
        return ra*180/np.pi, dec*180/np.pi
        
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
        
    def init_plot(self):
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
        
        self.cidmotion = self.canvas.mpl_connect('motion_notify_event', self.on_motion)
        
    def on_motion(self, event):
        """
        Deal with motion events in the stand field window. This involves 
        setting the status bar with the current x and y coordinates as well
        as the stand number of the selected stand (if any).
        """
        
        if event.inaxes:
            clickX = event.xdata
            clickY = event.ydata
            clickX = np.clip(int(round(clickX)), 0, self.image.shape[1]-1)
            clickY = np.clip(int(round(clickY)), 0, self.image.shape[0]-1)
            
            ra, dec = self._sin_px_to_sky(clickX, clickY)
            
            ra /= 15.0
            rh = int(ra)
            rm = int((ra - rh)*60) % 60
            rs = (ra*3600) % 60.0
            
            dg = '-' if dec < 0 else '+'
            dec = abs(dec)
            dd = int(dec)
            dm = int((dec - dd)*60) % 60
            ds = (dec*3600) % 60.0
            
            status_text = "%i:%02i:%05.2f, %s%i:%02i:%04.1f @ %4.1f Jy" % (
                rh, rm, rs, dg, dd, dm, ds, self.image[clickY, clickX])
            self.statusbar.config(text=status_text)
        else:
            self.statusbar.config(text="")
            
    def disconnect(self):
        """
        Disconnect all the stored connection ids.
        """
        
        self.canvas.mpl_disconnect(self.cidmotion)
        
    def resizePlots(self, event):
        """
        Handle window resize events.
        """
        # Skip if this is not for our window or if the window is not ready
        if event.widget != self or not hasattr(self, 'figure'):
            return
            
        # Get toolbar height
        toolbar_height = self.toolbar.winfo_height()
        
        # Get window dimensions
        width = self.winfo_width()
        height = self.winfo_height() - toolbar_height - self.statusbar.winfo_height()
        
        if width > 1 and height > 1:  # Avoid invalid dimensions
            dpi = self.figure.get_dpi()
            new_width = width / dpi
            new_height = height / dpi
            
            # Only update if changed significantly to avoid constant redrawing
            curr_size = self.figure.get_size_inches()
            if (abs(curr_size[0] - new_width) > 0.1 or 
                abs(curr_size[1] - new_height) > 0.1):
                self.figure.set_size_inches(new_width, new_height)
                self.figure.tight_layout()
                self.canvas.draw_idle()  # Use draw_idle for better performance


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
