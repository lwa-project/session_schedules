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
from datetime import datetime, timedelta, timezone
from xml.etree import ElementTree
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from tkinter import font as tkfont
import webbrowser

import conflict

import lsl
from lsl import astro
from lsl.common.dp import fS
from lsl.common import stations, idf
from lsl.astro import deg_to_dms, deg_to_hms, MJD_OFFSET, DJD_OFFSET
from lsl.reader.drx import FILTER_CODES as DRXFilters
from lsl.correlator import uvutils
from lsl.misc import parser as aph

import matplotlib
matplotlib.use('TkAgg')
matplotlib.interactive(True)

from matplotlib.backends.backend_tkagg import NavigationToolbar2Tk, FigureCanvasTkAgg
from matplotlib.figure import Figure
from matplotlib.ticker import NullFormatter, NullLocator

from calibratorSearch import CalibratorSearch as OCS

__version__ = "0.2"
__author__ = "Jayce Dowell"


def pid_print(*args, **kwds):
    print(f"[{os.getpid()}]", *args, **kwds)


class CheckableTreeview(ttk.Treeview):
    """A Treeview widget with checkboxes for each item."""

    def __init__(self, master=None, **kwargs):
        # Initialize the Treeview
        ttk.Treeview.__init__(self, master, **kwargs)
        self.checked_items = {}
        self.UNCHECKED = '☐'
        self.CHECKED = '☑'
        self.nSelected = 0

        # Bind click event to toggle checkboxes
        self.bind('<Button-1>', self.toggle_check)

    def toggle_check(self, event):
        """Toggle checkbox when clicked in the checkbox column."""
        region = self.identify("region", event.x, event.y)
        if region == "tree":
            item = self.identify_row(event.y)
            column = self.identify_column(event.x)

            if item and column == '#0':  # Checkbox is in first column
                # Toggle the checkbox
                if item in self.checked_items and self.checked_items[item]:
                    self.checked_items[item] = False
                    self.item(item, text=self.UNCHECKED)
                    self.nSelected -= 1
                else:
                    self.checked_items[item] = True
                    self.item(item, text=self.CHECKED)
                    self.nSelected += 1

                # Call the callback if it exists
                if hasattr(self, 'on_check_callback'):
                    self.on_check_callback(item)

                return "break"  # Prevent default behavior

    def is_checked(self, item):
        """Check if an item is checked."""
        return self.checked_items.get(item, False)

    def check_item(self, item, checked=True):
        """Programmatically check/uncheck an item."""
        old_state = self.checked_items.get(item, False)
        self.checked_items[item] = checked
        self.item(item, text=self.CHECKED if checked else self.UNCHECKED)

        # Update nSelected count
        if checked and not old_state:
            self.nSelected += 1
        elif not checked and old_state:
            self.nSelected -= 1

    def insert(self, parent, index, iid=None, **kw):
        """Override insert to add checkbox."""
        if 'text' not in kw:
            kw['text'] = self.UNCHECKED
        item = ttk.Treeview.insert(self, parent, index, iid, **kw)
        self.checked_items[item] = False
        return item


class EditableCell:
    """Mixin class to add cell editing capability to a Treeview."""
    # pylint: disable=no-member
    # This is a mixin class meant to be used with ttk.Treeview
    # Methods like bind, identify, bbox, item, etc. come from Treeview

    def __init__(self):
        self.entry_popup = None
        self.entry_col = None
        self.entry_item = None

        # Bind double-click to start editing
        self.bind('<Double-Button-1>', self.on_double_click)

    def on_double_click(self, event):
        """Handle double-click to edit cell."""
        region = self.identify("region", event.x, event.y)
        if region != "cell":
            return

        column = self.identify_column(event.x)
        item = self.identify_row(event.y)

        if not item:
            return

        # Check if this column is editable
        if hasattr(self, 'editable_columns'):
            col_index = int(column.replace('#', '')) - 1
            if col_index < 0 or col_index >= len(self.editable_columns):
                return
            if not self.editable_columns[col_index]:
                return

        # Open editor for this cell
        self.open_editor(column, item, event)

    def open_editor(self, column, item, event):
        """Open an entry widget for editing."""
        # Get the bounding box of the cell
        x, y, width, height = self.bbox(item, column)

        col_index = int(column.replace('#', '')) - 1

        # Get current value
        if col_index >= 0:
            current_value = self.item(item, 'values')[col_index]
        else:
            current_value = self.item(item, 'text')

        # Check if we have dropdown options for this column
        if hasattr(self, 'column_options') and col_index in self.column_options:
            # Use a combobox for dropdown
            self.entry_popup = ttk.Combobox(self, values=self.column_options[col_index])
            self.entry_popup.set(current_value)
        else:
            # Use an entry for text input
            self.entry_popup = ttk.Entry(self)
            self.entry_popup.insert(0, current_value)

        self.entry_popup.place(x=x, y=y, width=width, height=height)
        self.entry_popup.focus()
        self.entry_popup.select_range(0, tk.END)

        self.entry_col = col_index
        self.entry_item = item

        # Bind events to save or cancel
        self.entry_popup.bind('<Return>', self.save_edit)
        self.entry_popup.bind('<FocusOut>', self.save_edit)
        self.entry_popup.bind('<Escape>', self.cancel_edit)

    def save_edit(self, event):
        """Save the edited value."""
        if self.entry_popup:
            new_value = self.entry_popup.get()

            # Call the edit callback if it exists
            if hasattr(self, 'on_edit_callback'):
                # Let the callback handle the validation and update
                self.on_edit_callback(self.entry_item, self.entry_col, new_value)
            else:
                # Default: just update the display
                if self.entry_col >= 0:
                    values = list(self.item(self.entry_item, 'values'))
                    values[self.entry_col] = new_value
                    self.item(self.entry_item, values=values)

            self.entry_popup.destroy()
            self.entry_popup = None

    def cancel_edit(self, event):
        """Cancel editing."""
        if self.entry_popup:
            self.entry_popup.destroy()
            self.entry_popup = None


class ScanTreeview(CheckableTreeview, EditableCell):
    """Combined Treeview with checkboxes and editable cells for scans."""

    def __init__(self, master=None, **kwargs):
        CheckableTreeview.__init__(self, master, **kwargs)
        EditableCell.__init__(self)
        self.editable_columns = []
        self.column_options = {}


class SteppedTreeview(CheckableTreeview, EditableCell):
    """Treeview for stepped observations with editable cells."""

    def __init__(self, master=None, **kwargs):
        CheckableTreeview.__init__(self, master, **kwargs)
        EditableCell.__init__(self)
        self.editable_columns = []
        self.column_options = {}


class PlotPanel(tk.Frame):
    """Panel to hold matplotlib figures."""

    def __init__(self, parent, fig=None, **kwargs):
        tk.Frame.__init__(self, parent, **kwargs)

        if fig is None:
            self.figure = Figure()
        else:
            self.figure = fig

        self.canvas = FigureCanvasTkAgg(self.figure, master=self)
        self.canvas.draw()
        self.canvas.get_tk_widget().pack(side=tk.TOP, fill=tk.BOTH, expand=True)

    def get_figure(self):
        return self.figure

    def get_canvas(self):
        return self.canvas


# Menu/Toolbar IDs
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

_cleanup0RE = re.compile(r';;(;;)+')
_cleanup1RE = re.compile(r'^;;')


class IDFCreator(tk.Tk):
    def __init__(self, title, args):
        tk.Tk.__init__(self)
        self.title(title)
        self.geometry("750x500")

        self.scriptPath = os.path.abspath(__file__)
        self.scriptPath = os.path.split(self.scriptPath)[0]

        self.dirname = ''
        self.toolbar = None
        self.statusbar = None
        self.savemenu = None
        self.editmenu = {}
        self.obsmenu = {}
        self.editMenu = None
        self.scansMenu = None

        self.buffer = None

        self.initIDF()

        self.initUI()
        self.initEvents()

        idf._DRSUCapacityTB = args.drsu_size

        if args.filename is not None:
            self.filename = args.filename
            self.parseFile(self.filename)
        else:
            self.filename = ''
            self.setMenuButtons('None')

        self.edited = False
        self.badEdit = False
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

        menubar = tk.Menu(self)
        self.config(menu=menubar)

        # File menu
        fileMenu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="File", menu=fileMenu)
        fileMenu.add_command(label="New", command=self.onNew, accelerator="Ctrl+N")
        fileMenu.add_command(label="Open", command=self.onLoad, accelerator="Ctrl+O")
        fileMenu.add_command(label="Save", command=self.onSave, accelerator="Ctrl+S")
        fileMenu.add_command(label="Save As", command=self.onSaveAs, accelerator="Ctrl+Shift+S")
        fileMenu.add_separator()
        fileMenu.add_command(label="Quit", command=self.onQuit, accelerator="Ctrl+Q")

        # Edit menu
        self.editMenu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Edit", menu=self.editMenu)
        self.editMenu.add_command(label="Cut Selected Scan", command=self.onCut, state=tk.DISABLED)
        self.editMenu.add_command(label="Copy Selected Scan", command=self.onCopy, state=tk.DISABLED)
        self.editMenu.add_command(label="Paste Before Selected", command=self.onPasteBefore, state=tk.DISABLED)
        self.editMenu.add_command(label="Paste After Selected", command=self.onPasteAfter, state=tk.DISABLED)
        self.editMenu.add_command(label="Paste at End of List", command=self.onPasteEnd, state=tk.DISABLED)

        self.editmenu['cut'] = 0
        self.editmenu['copy'] = 1
        self.editmenu['pasteBefore'] = 2
        self.editmenu['pasteAfter'] = 3
        self.editmenu['pasteEnd'] = 4

        # Scans menu
        self.scansMenu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Scans", menu=self.scansMenu)
        self.scansMenu.add_command(label="Observer/Project Info.", command=self.onInfo)
        self.scansMenu.add_command(label="Scheduling", command=self.onSchedule)
        self.scansMenu.add_separator()

        addMenu = tk.Menu(self.scansMenu, tearoff=0)
        self.scansMenu.add_cascade(label="Add", menu=addMenu)
        addMenu.add_command(label="DRX - RA/Dec", command=self.onAddDRXR)
        addMenu.add_command(label="DRX - Solar", command=self.onAddDRXS)
        addMenu.add_command(label="DRX - Jovian", command=self.onAddDRXJ)

        self.scansMenu.add_command(label="Proper Motion", command=self.onProperMotion, state=tk.DISABLED)
        self.scansMenu.add_command(label="Remove Selected", command=self.onRemove, state=tk.DISABLED)
        self.scansMenu.add_command(label="Validate All\tF5", command=self.onValidate, accelerator="F5")
        self.scansMenu.add_separator()
        self.scansMenu.add_command(label="Resolve Selected\tF3", command=self.onResolve, state=tk.DISABLED, accelerator="F3")
        self.scansMenu.add_command(label="Calibrator Search\tF4", command=self.onSearch, accelerator="F4")
        self.scansMenu.add_separator()
        self.scansMenu.add_command(label="Run at a Glance", command=self.onTimeseries)
        self.scansMenu.add_command(label="UV Coverage", command=self.onUVCoverage)
        self.scansMenu.add_command(label="Advanced Settings", command=self.onAdvanced)

        self.obsmenu['drx-radec'] = (addMenu, 0)
        self.obsmenu['drx-solar'] = (addMenu, 1)
        self.obsmenu['drx-jovian'] = (addMenu, 2)
        self.obsmenu['pmotion'] = (self.scansMenu, 3)
        self.obsmenu['remove'] = (self.scansMenu, 4)
        self.obsmenu['resolve'] = (self.scansMenu, 7)

        # Data menu
        dataMenu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Data", menu=dataMenu)
        dataMenu.add_command(label="Estimated Data Volume", command=self.onVolume)

        # Help menu
        helpMenu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Help", menu=helpMenu)
        helpMenu.add_command(label="Swarm GUI Handbook\tF1", command=self.onHelp, accelerator="F1")
        helpMenu.add_command(label="Filter Codes", command=self.onFilterInfo)
        helpMenu.add_separator()
        helpMenu.add_command(label="About", command=self.onAbout)

        # Toolbar
        toolbar_frame = tk.Frame(self, bd=1, relief=tk.RAISED)
        toolbar_frame.pack(side=tk.TOP, fill=tk.X)

        # Load icons if they exist
        icon_path = os.path.join(self.scriptPath, 'icons')
        self.icons = {}
        icon_names = ['new', 'open', 'save', 'save-as', 'exit', 'drx-radec', 'drx-solar',
                      'drx-jovian', 'proper-motion', 'remove', 'validate', 'search', 'help']
        for icon_name in icon_names:
            icon_file = os.path.join(icon_path, f'{icon_name}.png')
            if os.path.exists(icon_file):
                try:
                    self.icons[icon_name] = tk.PhotoImage(file=icon_file)
                except:
                    pass

        # Create toolbar buttons
        self.toolbar_buttons = {}

        btn = tk.Button(toolbar_frame, image=self.icons.get('new'), command=self.onNew) if 'new' in self.icons else tk.Button(toolbar_frame, text="New", command=self.onNew)
        btn.pack(side=tk.LEFT, padx=2, pady=2)
        self.toolbar_buttons['new'] = btn

        btn = tk.Button(toolbar_frame, image=self.icons.get('open'), command=self.onLoad) if 'open' in self.icons else tk.Button(toolbar_frame, text="Open", command=self.onLoad)
        btn.pack(side=tk.LEFT, padx=2, pady=2)
        self.toolbar_buttons['open'] = btn

        btn = tk.Button(toolbar_frame, image=self.icons.get('save'), command=self.onSave, state=tk.DISABLED) if 'save' in self.icons else tk.Button(toolbar_frame, text="Save", command=self.onSave, state=tk.DISABLED)
        btn.pack(side=tk.LEFT, padx=2, pady=2)
        self.toolbar_buttons['save'] = btn
        self.savemenu = btn

        btn = tk.Button(toolbar_frame, image=self.icons.get('save-as'), command=self.onSaveAs) if 'save-as' in self.icons else tk.Button(toolbar_frame, text="Save As", command=self.onSaveAs)
        btn.pack(side=tk.LEFT, padx=2, pady=2)
        self.toolbar_buttons['saveas'] = btn

        btn = tk.Button(toolbar_frame, image=self.icons.get('exit'), command=self.onQuit) if 'exit' in self.icons else tk.Button(toolbar_frame, text="Quit", command=self.onQuit)
        btn.pack(side=tk.LEFT, padx=2, pady=2)
        self.toolbar_buttons['quit'] = btn

        ttk.Separator(toolbar_frame, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=5)

        btn = tk.Button(toolbar_frame, image=self.icons.get('drx-radec'), command=self.onAddDRXR) if 'drx-radec' in self.icons else tk.Button(toolbar_frame, text="DRX-RA/Dec", command=self.onAddDRXR)
        btn.pack(side=tk.LEFT, padx=2, pady=2)
        self.toolbar_buttons['drx-radec'] = btn

        btn = tk.Button(toolbar_frame, image=self.icons.get('drx-solar'), command=self.onAddDRXS) if 'drx-solar' in self.icons else tk.Button(toolbar_frame, text="DRX-Solar", command=self.onAddDRXS)
        btn.pack(side=tk.LEFT, padx=2, pady=2)
        self.toolbar_buttons['drx-solar'] = btn

        btn = tk.Button(toolbar_frame, image=self.icons.get('drx-jovian'), command=self.onAddDRXJ) if 'drx-jovian' in self.icons else tk.Button(toolbar_frame, text="DRX-Jovian", command=self.onAddDRXJ)
        btn.pack(side=tk.LEFT, padx=2, pady=2)
        self.toolbar_buttons['drx-jovian'] = btn

        btn = tk.Button(toolbar_frame, image=self.icons.get('proper-motion'), command=self.onProperMotion, state=tk.DISABLED) if 'proper-motion' in self.icons else tk.Button(toolbar_frame, text="PM", command=self.onProperMotion, state=tk.DISABLED)
        btn.pack(side=tk.LEFT, padx=2, pady=2)
        self.toolbar_buttons['pmotion'] = btn

        btn = tk.Button(toolbar_frame, image=self.icons.get('remove'), command=self.onRemove, state=tk.DISABLED) if 'remove' in self.icons else tk.Button(toolbar_frame, text="Remove", command=self.onRemove, state=tk.DISABLED)
        btn.pack(side=tk.LEFT, padx=2, pady=2)
        self.toolbar_buttons['remove'] = btn

        btn = tk.Button(toolbar_frame, image=self.icons.get('validate'), command=self.onValidate) if 'validate' in self.icons else tk.Button(toolbar_frame, text="Validate", command=self.onValidate)
        btn.pack(side=tk.LEFT, padx=2, pady=2)
        self.toolbar_buttons['validate'] = btn

        ttk.Separator(toolbar_frame, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=5)

        btn = tk.Button(toolbar_frame, image=self.icons.get('search'), command=self.onSearch) if 'search' in self.icons else tk.Button(toolbar_frame, text="Search", command=self.onSearch)
        btn.pack(side=tk.LEFT, padx=2, pady=2)
        self.toolbar_buttons['search'] = btn

        ttk.Separator(toolbar_frame, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=5)

        btn = tk.Button(toolbar_frame, image=self.icons.get('help'), command=self.onHelp) if 'help' in self.icons else tk.Button(toolbar_frame, text="Help", command=self.onHelp)
        btn.pack(side=tk.LEFT, padx=2, pady=2)
        self.toolbar_buttons['help'] = btn

        # Status bar
        self.statusbar = tk.Label(self, text="Ready", bd=1, relief=tk.SUNKEN, anchor=tk.W)
        self.statusbar.pack(side=tk.BOTTOM, fill=tk.X)

        # Main panel with scrollable list
        main_frame = tk.Frame(self)
        main_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        # Create canvas with scrollbars for horizontal scrolling
        self.canvas = tk.Canvas(main_frame)
        h_scrollbar = ttk.Scrollbar(main_frame, orient=tk.HORIZONTAL, command=self.canvas.xview)
        v_scrollbar = ttk.Scrollbar(main_frame, orient=tk.VERTICAL, command=self.canvas.yview)

        self.canvas.configure(xscrollcommand=h_scrollbar.set, yscrollcommand=v_scrollbar.set)

        h_scrollbar.pack(side=tk.BOTTOM, fill=tk.X)
        v_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Create frame inside canvas
        self.listFrame = tk.Frame(self.canvas)
        self.canvas.create_window((0, 0), window=self.listFrame, anchor=tk.NW)

        # Scan list control
        self.listControl = ScanTreeview(self.listFrame)
        self.listControl.pack(fill=tk.BOTH, expand=True)
        self.listControl.parent = self

        # Bind scrolling
        self.listFrame.bind('<Configure>', lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))

        # Setup callbacks
        self.listControl.on_check_callback = self.onCheckItem
        self.listControl.on_edit_callback = self.on_edit

    def initEvents(self):
        """
        Set all of the various events in the main window.
        """

        # Keyboard shortcuts
        self.bind('<Control-n>', lambda e: self.onNew())
        self.bind('<Control-o>', lambda e: self.onLoad())
        self.bind('<Control-s>', lambda e: self.onSave())
        self.bind('<Control-Shift-S>', lambda e: self.onSaveAs())
        self.bind('<Control-q>', lambda e: self.onQuit())
        self.bind('<F1>', lambda e: self.onHelp())
        self.bind('<F3>', lambda e: self.onResolve())
        self.bind('<F4>', lambda e: self.onSearch())
        self.bind('<F5>', lambda e: self.onValidate())

        # Window close
        self.protocol("WM_DELETE_WINDOW", self.onQuit)

    def onCheckItem(self, item):
        """Handle checkbox state changes."""
        # Count selected items
        selected_count = self.listControl.nSelected

        # Get the index of the item
        index = None
        for i, child in enumerate(self.listControl.get_children()):
            if child == item:
                index = i
                break

        # Update menu and toolbar states
        if selected_count == 0:
            # Edit menu - disabled
            self.editMenu.entryconfig(self.editmenu['cut'], state=tk.DISABLED)
            self.editMenu.entryconfig(self.editmenu['copy'], state=tk.DISABLED)

            # Remove and resolve - disabled
            self.scansMenu.entryconfig(self.obsmenu['pmotion'][1], state=tk.DISABLED)
            self.scansMenu.entryconfig(self.obsmenu['remove'][1], state=tk.DISABLED)
            self.scansMenu.entryconfig(self.obsmenu['resolve'][1], state=tk.DISABLED)

            self.toolbar_buttons['pmotion'].config(state=tk.DISABLED)
            self.toolbar_buttons['remove'].config(state=tk.DISABLED)

        elif selected_count == 1:
            # Edit menu - enabled
            self.editMenu.entryconfig(self.editmenu['cut'], state=tk.NORMAL)
            self.editMenu.entryconfig(self.editmenu['copy'], state=tk.NORMAL)

            # Remove and resolve - enabled
            self.scansMenu.entryconfig(self.obsmenu['pmotion'][1], state=tk.NORMAL)
            self.scansMenu.entryconfig(self.obsmenu['remove'][1], state=tk.NORMAL)
            self.scansMenu.entryconfig(self.obsmenu['resolve'][1], state=tk.NORMAL)

            self.toolbar_buttons['pmotion'].config(state=tk.NORMAL)
            self.toolbar_buttons['remove'].config(state=tk.NORMAL)

        else:
            # Edit menu - enabled
            self.editMenu.entryconfig(self.editmenu['cut'], state=tk.NORMAL)
            self.editMenu.entryconfig(self.editmenu['copy'], state=tk.NORMAL)

            # Motion and resolve - disabled, remove - enabled
            self.scansMenu.entryconfig(self.obsmenu['pmotion'][1], state=tk.DISABLED)
            self.scansMenu.entryconfig(self.obsmenu['remove'][1], state=tk.NORMAL)
            self.scansMenu.entryconfig(self.obsmenu['resolve'][1], state=tk.DISABLED)

            self.toolbar_buttons['pmotion'].config(state=tk.DISABLED)
            self.toolbar_buttons['remove'].config(state=tk.NORMAL)

    def on_edit(self, item, col, new_value):
        """
        Handle cell editing with validation and update.
        This includes all 7 patches from sessionGUI migration.
        """
        # Get the row index
        row_index = None
        for i, child in enumerate(self.listControl.get_children()):
            if child == item:
                row_index = i
                break

        if row_index is None:
            return

        # Remove any previous error tags
        self.listControl.item(item, tags=())

        try:
            # Validate and convert the new value
            newData = self.coerceMap[col + 1](new_value)

            # Get the old value
            oldData = getattr(self.project.runs[0].scans[row_index], self.columnMap[col + 1])

            # Update if changed
            if newData != oldData:
                setattr(self.project.runs[0].scans[row_index], self.columnMap[col + 1], newData)
                self.project.runs[0].scans[row_index].update()

                # Update the display
                values = list(self.listControl.item(item, 'values'))
                values[col] = new_value
                self.listControl.item(item, values=values)

                self.edited = True
                self.badEdit = False
                self.setSaveButton()

                # Call after_edit callback if it exists
                if hasattr(self, 'after_edit'):
                    self.after_edit(row_index, col + 1, new_value)

        except ValueError as err:
            # Mark as error
            self.listControl.item(item, tags=('error',))
            self.listControl.tag_configure('error', background='#ffcccc')

            messagebox.showerror("Validation Error", str(err))
            self.badEdit = True
            pid_print(f"Error: {str(err)}")

    def onNew(self, event=None):
        """
        Create a new ID run.
        """

        if self.edited:
            result = messagebox.askyesno('Confirm New',
                'The current interferometer definition file has changes that have not been saved.\n\nStart a new run anyways?',
                icon=messagebox.WARNING, default=messagebox.NO)

            if not result:
                return False

        self.filename = ''
        self.edited = True
        self.badEdit = False
        self.setSaveButton()

        self.setMenuButtons('None')
        # Clear the tree
        for item in self.listControl.get_children():
            self.listControl.delete(item)
        self.listControl.nSelected = 0
        self.onCheckItem(None)
        self.initIDF()
        ObserverInfo(self)

    def onLoad(self, event=None):
        """
        Load an existing IDF file.
        """

        if self.edited:
            result = messagebox.askyesno('Confirm Open',
                'The current interferometer definition file has changes that have not been saved.\n\nOpen a new file anyways?',
                icon=messagebox.WARNING, default=messagebox.NO)

            if not result:
                return False

        filename = filedialog.askopenfilename(
            title="Select an IDF File",
            initialdir=self.dirname,
            filetypes=[("IDF Files", "*.idf *.txt"), ("All Files", "*.*")]
        )

        if filename:
            self.dirname = os.path.dirname(filename)
            self.filename = filename
            self.parseFile(filename)

            self.edited = False
            self.setSaveButton()

    def onSave(self, event=None):
        """
        Save the current scan to a file.
        """

        if self.filename == '':
            self.onSaveAs(event)
        else:
            if not self.onValidate(confirmValid=False):
                self.displayError('The interferometer definition file could not be saved due to errors in the file.',
                                title='Save Failed')
            else:
                try:
                    with open(self.filename, 'w') as fh:
                        fh.write(self.project.render())

                    self.edited = False
                    self.setSaveButton()
                except IOError as err:
                    self.displayError(f"Error saving to '{self.filename}'", details=err, title='Save Error')

    def onSaveAs(self, event=None):
        """
        Save the current scan to a new IDF file.
        """

        if not self.onValidate(confirmValid=False):
            self.displayError('The interferometer definition file could not be saved due to errors in the file.',
                            title='Save Failed')
        else:
            filename = filedialog.asksaveasfilename(
                title="Select Output File",
                initialdir=self.dirname,
                filetypes=[("IDF Files", "*.idf *.txt"), ("All Files", "*.*")],
                defaultextension=".idf"
            )

            if filename:
                self.dirname = os.path.dirname(filename)
                self.filename = filename
                try:
                    with open(self.filename, 'w') as fh:
                        fh.write(self.project.render())

                    self.edited = False
                    self.setSaveButton()
                except IOError as err:
                    self.displayError(f"Error saving to '{self.filename}'", details=err, title='Save Error')

    def onCopy(self, event=None):
        """
        Copy the selected scan(s) to the buffer.
        """

        self.buffer = []
        for i, child in enumerate(self.listControl.get_children()):
            if self.listControl.is_checked(child):
                self.buffer.append(copy.deepcopy(self.project.runs[0].scans[i]))

        # Enable paste menu items
        editMenu = self.nametowidget(self.cget('menu')).nametowidget('!menu')
        editMenu.entryconfig(self.editmenu['pasteBefore'], state=tk.NORMAL)
        editMenu.entryconfig(self.editmenu['pasteAfter'], state=tk.NORMAL)
        editMenu.entryconfig(self.editmenu['pasteEnd'], state=tk.NORMAL)

    def onCut(self, event=None):
        self.onCopy(event)
        self.onRemove(event)

    def onPasteBefore(self, event=None):
        firstChecked = None

        for i, child in enumerate(self.listControl.get_children()):
            if self.listControl.is_checked(child):
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
            for id, child in enumerate(self.listControl.get_children()):
                values = list(self.listControl.item(child, 'values'))
                values[0] = str(id + 1)
                self.listControl.item(child, values=values)

            # Fix the times on scans to make thing continuous
            for id in range(firstChecked + len(self.buffer) - 1, -1, -1):
                dur = self.project.runs[0].scans[id].dur

                tStart, _ = idf.get_scan_start_stop(self.project.runs[0].scans[id + 1])
                tStart -= timedelta(seconds=dur // 1000, microseconds=(dur % 1000) * 1000)
                cStart = 'UTC %i %02i %02i %02i:%02i:%06.3f' % (tStart.year, tStart.month, tStart.day,
                                                                tStart.hour, tStart.minute,
                                                                tStart.second + tStart.microsecond / 1e6)
                self.project.runs[0].scans[id].start = cStart
                self.addScan(self.project.runs[0].scans[id], id, update=True)

    def onPasteAfter(self, event=None):
        lastChecked = None

        for i, child in enumerate(self.listControl.get_children()):
            if self.listControl.is_checked(child):
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
            for id, child in enumerate(self.listControl.get_children()):
                values = list(self.listControl.item(child, 'values'))
                values[0] = str(id + 1)
                self.listControl.item(child, values=values)

    def onPasteEnd(self, event=None):
        if self.buffer is not None:
            for obs in self.buffer:
                cObs = copy.deepcopy(obs)

                self.project.runs[0].scans.append(cObs)
                self.addScan(cObs, len(self.project.runs[0].scans) - 1)

            self.edited = True
            self.setSaveButton()

    def onInfo(self, event=None):
        """Show observer/project/run information dialog."""
        ObserverInfo(self)

    def onSchedule(self, event=None):
        """Show scheduling window."""
        ScheduleWindow(self)

    def onAddDRXR(self, event=None):
        """Add DRX RA/Dec scan."""
        tStart = datetime.now(timezone.utc)
        tStart += timedelta(days=1)

        # Create new scan
        scan = idf.DRX('Target', 'Target', tStart, '00:00:10',
                      0.0, 0.0, 38e6, 74e6, 7, gain=self.project.runs[0].drxGain)

        self.project.runs[0].scans.append(scan)
        self.addScan(scan, len(self.project.runs[0].scans) - 1)

        self.edited = True
        self.setSaveButton()

    def onAddDRXS(self, event=None):
        """Add DRX Solar scan."""
        tStart = datetime.now(timezone.utc)
        tStart += timedelta(days=1)

        # Create new scan
        scan = idf.Solar('Sun', 'Target', tStart, '00:00:10', 38e6, 74e6, 7, gain=self.project.runs[0].drxGain)

        self.project.runs[0].scans.append(scan)
        self.addScan(scan, len(self.project.runs[0].scans) - 1)

        self.edited = True
        self.setSaveButton()

    def onAddDRXJ(self, event=None):
        """Add DRX Jovian scan."""
        tStart = datetime.now(timezone.utc)
        tStart += timedelta(days=1)

        # Create new scan
        scan = idf.Jovian('Jupiter', 'Target', tStart, '00:00:10', 38e6, 74e6, 7, gain=self.project.runs[0].drxGain)

        self.project.runs[0].scans.append(scan)
        self.addScan(scan, len(self.project.runs[0].scans) - 1)

        self.edited = True
        self.setSaveButton()

    def onProperMotion(self, event=None):
        """Show proper motion window for selected scan."""
        for i, child in enumerate(self.listControl.get_children()):
            if self.listControl.is_checked(child):
                ProperMotionWindow(self, i)
                break

    def onRemove(self, event=None):
        """Remove selected scans."""
        to_remove = []
        for i, child in enumerate(self.listControl.get_children()):
            if self.listControl.is_checked(child):
                to_remove.append((i, child))

        # Remove from list (in reverse order to maintain indices)
        for i, child in reversed(to_remove):
            del self.project.runs[0].scans[i]
            self.listControl.delete(child)

        # Re-number the remaining rows
        for id, child in enumerate(self.listControl.get_children()):
            values = list(self.listControl.item(child, 'values'))
            values[0] = str(id + 1)
            self.listControl.item(child, values=values)

        self.listControl.nSelected = 0
        self.onCheckItem(None)

        self.edited = True
        self.setSaveButton()

    def onValidate(self, event=None, confirmValid=True):
        """Validate all scans."""
        try:
            # Validate the IDF
            if len(self.project.runs[0].scans) == 0:
                raise RuntimeError("No scans defined")

            # Try to render it
            output = self.project.render()

            if confirmValid:
                messagebox.showinfo("Validation Successful", "All scans are valid!")
            return True

        except Exception as e:
            self.displayError("Validation failed", details=str(e), title="Validation Error")
            return False

    def onResolve(self, event=None):
        """Show resolve target window."""
        ResolveTarget(self)

    def onSearch(self, event=None):
        """Show calibrator search window."""
        scanID = -1
        for i, child in enumerate(self.listControl.get_children()):
            if self.listControl.is_checked(child):
                scanID = i
                break
        SearchWindow(self, scanID)

    def onTimeseries(self, event=None):
        """Show run at a glance window."""
        if len(self.project.runs[0].scans) > 0:
            RunDisplay(self)
        else:
            messagebox.showwarning("No Scans", "No scans defined yet!")

    def onUVCoverage(self, event=None):
        """Show UV coverage window."""
        if len(self.project.runs[0].scans) > 0:
            RunUVCoverageDisplay(self)
        else:
            messagebox.showwarning("No Scans", "No scans defined yet!")

    def onAdvanced(self, event=None):
        """Show advanced settings window."""
        AdvancedInfo(self)

    def onVolume(self, event=None):
        """Show data volume information."""
        if len(self.project.runs[0].scans) > 0:
            VolumeInfo(self)
        else:
            messagebox.showwarning("No Scans", "No scans defined yet!")

    def onHelp(self, event=None):
        """Show help window."""
        HelpWindow(self)

    def onFilterInfo(self, event=None):
        """Show filter codes information."""
        info = "DRX Filter Codes:\n\n"
        for code, bandwidth in DRXFilters.items():
            info += f"Code {code}: {bandwidth/1e6:.3f} MHz\n"
        messagebox.showinfo("Filter Codes", info)

    def onAbout(self, event=None):
        """Show about dialog."""
        about_text = f"""Swarm GUI - Interferometer Definition File Creator
Version {__version__}
Author: {__author__}

A GUI for creating interferometer definition files (IDFs) for LWA swarm mode observations."""
        messagebox.showinfo("About Swarm GUI", about_text)

    def onQuit(self, event=None):
        """Quit the application."""
        if self.edited:
            result = messagebox.askyesnocancel('Confirm Quit',
                'The current interferometer definition file has changes that have not been saved.\n\nSave before quitting?',
                icon=messagebox.WARNING)

            if result is None:  # Cancel
                return
            elif result:  # Yes
                self.onSave()

        self.quit()
        self.destroy()

    def addColumns(self):
        """Add columns to the scan list."""

        def intentConv(text):
            """Special conversion function for dealing with intents."""
            if text not in ['FluxCal', 'PhaseCal', 'Target']:
                raise ValueError("Intent must be one of: FluxCal, PhaseCal, Target")
            return text

        def raConv(text):
            """Special conversion function for dealing with RA."""
            fields = text.split(':')
            if len(fields) != 3:
                raise ValueError("RA must be in HH:MM:SS.S format")

            h = int(fields[0])
            m = int(fields[1])
            s = float(fields[2])

            value = (h + m/60.0 + s/3600.0)
            if value < 0 or value >= 24:
                raise ValueError("RA must be in the range [0, 24) hours")
            return value

        def decConv(text):
            """Special conversion function for dealing with Dec."""
            fields = text.split(':')
            if len(fields) != 3:
                raise ValueError("Dec must be in +/-DD:MM:SS.S format")

            sign = 1
            if fields[0][0] == '-':
                sign = -1

            d = int(fields[0])
            m = int(fields[1])
            s = float(fields[2])

            value = sign*(abs(d) + m/60.0 + s/3600.0)
            if value < -90 or value > 90:
                raise ValueError("Dec must be in the range [-90, 90] degrees")
            return value

        def freqConv(text, tbn=False):
            """Special conversion function for dealing with frequencies."""
            if tbn:
                lowerLimit = 0
                upperLimit = 2**32 - 1
            else:
                lowerLimit = int(round(10e6 * 2**32 / fS))
                upperLimit = int(round(88e6 * 2**32 / fS))

            value = float(text) * 1e6
            freq = int(round(value * 2**32 / fS))
            if freq < lowerLimit or freq > upperLimit:
                raise ValueError(f"Frequency of {value/1e6:.6f} MHz is out of the DP tuning range")
            else:
                return value

        def filterConv(text):
            """Special conversion function for dealing with filter codes."""
            value = int(text)
            if value < 1 or value > 7:
                raise ValueError("Filter code must be an integer between 1 and 7")
            else:
                return value

        def pmConv(text):
            """Special conversion function for dealing with proper motion pairs."""
            try:
                text = text.replace("---", "0.0")
                ra, dec = [float(v) for v in text.split(None, 1)]
            except (IndexError, TypeError):
                raise ValueError("Proper motion must be a space-separated pair of float values")
            return [ra, dec]

        # Define columns
        columns = [
            ('ID', 50),
            ('Target', 100),
            ('Intent', 100),
            ('Comments', 100),
            ('Start (UTC)', 235),
            ('Duration', 125),
            ('RA (Hour J2000)', 150),
            ('Dec (Deg. J2000)', 150),
            ('Tuning 1 (MHz)', 125),
            ('Tuning 2 (MHz)', 125),
            ('Filter Code', 85)
        ]

        # Configure the tree columns
        self.listControl['columns'] = [col[0] for col in columns[1:]]  # First column is tree column
        self.listControl.heading('#0', text=columns[0][0])
        self.listControl.column('#0', width=columns[0][1], minwidth=columns[0][1], stretch=False, anchor=tk.W)

        for i, (name, width) in enumerate(columns[1:]):
            self.listControl.heading(i, text=name)
            self.listControl.column(i, width=width, minwidth=width, stretch=False, anchor=tk.W)

        # Column mapping
        self.columnMap = []
        self.coerceMap = []

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
        self.columnMap.append('pm')  # For proper motion

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
        self.coerceMap.append(pmConv)  # For proper motion

        # Set editable columns (all 10 value columns; ID in tree column is not editable)
        self.listControl.editable_columns = [True] * 10

        # Set column options for dropdowns
        self.listControl.column_options = {
            1: ['FluxCal', 'PhaseCal', 'Target'],  # Intent
            9: ['1', '2', '3', '4', '5', '6', '7']  # Filter Code
        }

    def addScan(self, obs, id, update=False):
        """
        Add a scan to a particular location in the scan list.

        .. note::
            This only updates the list visible on the screen, not the IDF list
            stored in self.project
        """

        def dec2sexstr(value, signed=True):
            sign = 1
            if value < 0:
                sign = -1
            value = abs(value)

            d = sign * int(value)
            m = int(value * 60) % 60
            s = float(value * 3600) % 60

            if signed:
                return '%+03i:%02i:%04.1f' % (d, m, s)
            else:
                return '%02i:%02i:%05.2f' % (d, m, s)

        # Prepare values
        values = []
        values.append(obs.target)
        values.append(obs.intent)
        values.append(obs.comments if obs.comments is not None else 'None provided')
        values.append(obs.start)

        if obs.mode == 'STEPPED':
            values.append(obs.duration)
            values.append("STEPPED")
            values.append("RA/Dec" if obs.RADec else "Az/Alt")
            values.append("--")
            values.append("--")
        else:
            values.append(obs.duration)
            if obs.mode == 'TRK_SOL':
                values.append("Sun")
                values.append("--")
            elif obs.mode == 'TRK_JOV':
                values.append("Jupiter")
                values.append("--")
            else:
                values.append(dec2sexstr(obs.ra, signed=False))
                values.append(dec2sexstr(obs.dec, signed=True))
            values.append("%.6f" % (obs.freq1 * fS / 2**32 / 1e6))
            values.append("%.6f" % (obs.freq2 * fS / 2**32 / 1e6))

        values.append("%i" % obs.filter)

        if update:
            # Update existing item
            children = self.listControl.get_children()
            if id < len(children):
                item = children[id]
                self.listControl.item(item, text=self.listControl.UNCHECKED, values=values)
        else:
            # Insert new item
            self.listControl.insert('', id, text=self.listControl.UNCHECKED, values=values)

    def setSaveButton(self):
        """
        Control the state of the various 'save' options based on the value of
        self.edited.
        """

        if self.edited:
            self.savemenu.config(state=tk.NORMAL)
        else:
            self.savemenu.config(state=tk.DISABLED)

    def setMenuButtons(self, mode):
        """
        Given a mode of scan (TRK_RADEC, TRK_SOL, etc.), update the
        various menu items in 'Scans' and the toolbar buttons.
        """

        mode = mode.lower()

        if mode[0:3] == 'trk' or mode[0:3] == 'drx':
            state = tk.NORMAL
        else:
            state = tk.DISABLED

        # Update menu items using stored references
        for key in ['drx-radec', 'drx-solar', 'drx-jovian']:
            menu, index = self.obsmenu[key]
            menu.entryconfig(index, state=state)

        # Update toolbar buttons
        self.toolbar_buttons['drx-radec'].config(state=state)
        self.toolbar_buttons['drx-solar'].config(state=state)
        self.toolbar_buttons['drx-jovian'].config(state=state)

    def parseFile(self, filename):
        """
        Given a filename, parse the file using the idf.parse_idf() method and
        update all of the various aspects of the GUI (scan list, mode,
        button, menu items, etc.).
        """

        # Clear the tree
        for item in self.listControl.get_children():
            self.listControl.delete(item)
        self.listControl.nSelected = 0
        self.onCheckItem(None)
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
        id = 0
        for obs in self.project.runs[0].scans:
            self.addScan(obs, id)
            id += 1

    def displayError(self, error, details=None, title=None):
        """
        Display an error dialog and write an error message to the command
        line if requested.
        """
        if title is None:
            title = 'An Error has Occurred'

        if details is None:
            pid_print(f"Error: {str(error)}")
            self.statusbar.config(text=f"Error: {str(error)}")
            messagebox.showerror(title, str(error))
        else:
            pid_print(f"Error: {str(details)}")
            self.statusbar.config(text=f"Error: {str(details)}")
            messagebox.showerror(title, f"{str(error)}\n\nDetails:\n{str(details)}")


class ObserverInfo(tk.Toplevel):
    """
    Class to hold information about the observer (name, ID), the current project
    (title, ID), and what type of run this will be (DRX, etc.).
    """

    def __init__(self, parent):
        tk.Toplevel.__init__(self, parent)
        self.title('Observer Information')
        self.parent = parent

        # Make it modal
        self.transient(parent)
        self.grab_set()

        self.initUI()

        # Center on parent
        self.geometry("+%d+%d" % (parent.winfo_rootx() + 50, parent.winfo_rooty() + 50))

    def initUI(self):
        row = 0
        main_frame = ttk.Frame(self, padding="10")
        main_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))

        # Load preferences
        preferences = {}
        try:
            with open(os.path.join(os.path.expanduser('~'), '.sessionGUI')) as ph:
                for line in ph:
                    line = line.strip()
                    if len(line) < 3 or line[0] == '#':
                        continue
                    key, value = line.split(None, 1)
                    preferences[key] = value
        except:
            pass

        # Observer Information
        obs_frame = ttk.LabelFrame(main_frame, text="Observer Information", padding="5")
        obs_frame.grid(row=row, column=0, columnspan=6, sticky=(tk.W, tk.E), pady=5)

        ttk.Label(obs_frame, text="ID Number:").grid(row=0, column=0, sticky=tk.W, padx=5, pady=2)
        self.observerIDEntry = ttk.Entry(obs_frame, width=30)
        self.observerIDEntry.grid(row=0, column=1, columnspan=5, sticky=(tk.W, tk.E), padx=5, pady=2)

        ttk.Label(obs_frame, text="First Name:").grid(row=1, column=0, sticky=tk.W, padx=5, pady=2)
        self.observerFirstEntry = ttk.Entry(obs_frame, width=30)
        self.observerFirstEntry.grid(row=1, column=1, columnspan=5, sticky=(tk.W, tk.E), padx=5, pady=2)

        ttk.Label(obs_frame, text="Last Name:").grid(row=2, column=0, sticky=tk.W, padx=5, pady=2)
        self.observerLastEntry = ttk.Entry(obs_frame, width=30)
        self.observerLastEntry.grid(row=2, column=1, columnspan=5, sticky=(tk.W, tk.E), padx=5, pady=2)

        # Set values from preferences or project
        if self.parent.project.observer.id != 0:
            self.observerIDEntry.insert(0, str(self.parent.project.observer.id))
        elif 'ObserverID' in preferences:
            self.observerIDEntry.insert(0, preferences['ObserverID'])

        if self.parent.project.observer.first != '':
            self.observerFirstEntry.insert(0, self.parent.project.observer.first)
            self.observerLastEntry.insert(0, self.parent.project.observer.last)
        else:
            if 'ObserverFirstName' in preferences:
                self.observerFirstEntry.insert(0, preferences['ObserverFirstName'])
            if 'ObserverLastName' in preferences:
                self.observerLastEntry.insert(0, preferences['ObserverLastName'])

        row += 1

        # Project Information
        proj_frame = ttk.LabelFrame(main_frame, text="Project Information", padding="5")
        proj_frame.grid(row=row, column=0, columnspan=6, sticky=(tk.W, tk.E), pady=5)

        ttk.Label(proj_frame, text="ID Code:").grid(row=0, column=0, sticky=tk.W, padx=5, pady=2)
        self.projectIDEntry = ttk.Entry(proj_frame, width=30)
        self.projectIDEntry.grid(row=0, column=1, columnspan=5, sticky=(tk.W, tk.E), padx=5, pady=2)

        ttk.Label(proj_frame, text="Title:").grid(row=1, column=0, sticky=tk.W, padx=5, pady=2)
        self.projectTitleEntry = ttk.Entry(proj_frame, width=50)
        self.projectTitleEntry.grid(row=1, column=1, columnspan=5, sticky=(tk.W, tk.E), padx=5, pady=2)

        ttk.Label(proj_frame, text="Comments:").grid(row=2, column=0, sticky=tk.NW, padx=5, pady=2)
        self.projectCommentsEntry = tk.Text(proj_frame, width=50, height=3)
        self.projectCommentsEntry.grid(row=2, column=1, rowspan=3, columnspan=5, sticky=(tk.W, tk.E), padx=5, pady=2)

        # Set values from preferences or project
        if self.parent.project.id != '':
            self.projectIDEntry.insert(0, str(self.parent.project.id))
        elif 'ProjectID' in preferences:
            self.projectIDEntry.insert(0, preferences['ProjectID'])

        if self.parent.project.name != '':
            self.projectTitleEntry.insert(0, self.parent.project.name)
        elif 'ProjectName' in preferences:
            self.projectTitleEntry.insert(0, preferences['ProjectName'])

        if self.parent.project.comments != '' and self.parent.project.comments is not None:
            self.projectCommentsEntry.insert('1.0', self.parent.project.comments.replace(';;', '\n'))

        row += 1

        # Run Information
        run_frame = ttk.LabelFrame(main_frame, text="Run Information", padding="5")
        run_frame.grid(row=row, column=0, columnspan=6, sticky=(tk.W, tk.E), pady=5)

        ttk.Label(run_frame, text="ID Number:").grid(row=0, column=0, sticky=tk.W, padx=5, pady=2)
        self.runIDEntry = ttk.Entry(run_frame, width=30)
        self.runIDEntry.grid(row=0, column=1, columnspan=5, sticky=(tk.W, tk.E), padx=5, pady=2)

        ttk.Label(run_frame, text="Title:").grid(row=1, column=0, sticky=tk.W, padx=5, pady=2)
        self.runTitleEntry = ttk.Entry(run_frame, width=50)
        self.runTitleEntry.grid(row=1, column=1, columnspan=5, sticky=(tk.W, tk.E), padx=5, pady=2)

        ttk.Label(run_frame, text="Comments:").grid(row=2, column=0, sticky=tk.NW, padx=5, pady=2)
        self.runCommentsEntry = tk.Text(run_frame, width=50, height=3)
        self.runCommentsEntry.grid(row=2, column=1, rowspan=3, columnspan=5, sticky=(tk.W, tk.E), padx=5, pady=2)

        # Set values from project
        if self.parent.project.runs[0].id != '':
            self.runIDEntry.insert(0, str(self.parent.project.runs[0].id))
        if self.parent.project.runs[0].name != '':
            self.runTitleEntry.insert(0, self.parent.project.runs[0].name)
        if self.parent.project.runs[0].comments != '' and self.parent.project.runs[0].comments is not None:
            self.runCommentsEntry.insert('1.0',
                idf.UCF_USERNAME_RE.sub('', self.parent.project.runs[0].comments).replace(';;', '\n'))

        # Correlator Setup
        corr_frame = ttk.LabelFrame(run_frame, text="Correlator Setup", padding="5")
        corr_frame.grid(row=5, column=0, columnspan=6, sticky=(tk.W, tk.E), pady=5)

        ttk.Label(corr_frame, text="Channels:").grid(row=0, column=0, sticky=tk.W, padx=5, pady=2)
        self.nchnText = ttk.Entry(corr_frame, width=10)
        self.nchnText.grid(row=0, column=1, sticky=tk.W, padx=5, pady=2)

        ttk.Label(corr_frame, text="Int. Time:").grid(row=0, column=2, sticky=tk.W, padx=5, pady=2)
        self.tintText = ttk.Entry(corr_frame, width=10)
        self.tintText.grid(row=0, column=3, sticky=tk.W, padx=5, pady=2)

        if self.parent.project.runs[0].corr_channels:
            self.nchnText.insert(0, str(self.parent.project.runs[0].corr_channels))
        if self.parent.project.runs[0].corr_inttime:
            self.tintText.insert(0, str(self.parent.project.runs[0].corr_inttime))

        ttk.Label(corr_frame, text="Data Products:").grid(row=1, column=0, sticky=tk.W, padx=5, pady=2)

        self.linear = tk.BooleanVar(value=True)
        self.circul = tk.BooleanVar(value=False)
        self.stokes = tk.BooleanVar(value=False)

        ttk.Radiobutton(corr_frame, text="Linear", variable=self.linear, value=True,
                       command=lambda: self.set_basis('linear')).grid(row=1, column=1, sticky=tk.W, padx=5)
        ttk.Radiobutton(corr_frame, text="Circular", variable=self.circul, value=True,
                       command=lambda: self.set_basis('circular')).grid(row=1, column=2, sticky=tk.W, padx=5)
        ttk.Radiobutton(corr_frame, text="Stokes", variable=self.stokes, value=True, state=tk.DISABLED,
                       command=lambda: self.set_basis('stokes')).grid(row=1, column=3, sticky=tk.W, padx=5)

        if self.parent.project.runs[0].corr_basis:
            basis = self.parent.project.runs[0].corr_basis
            if basis == 'linear':
                self.linear.set(True)
            elif basis == 'circular':
                self.circul.set(True)
            else:
                self.stokes.set(True)

        # Data Return Method
        ret_frame = ttk.LabelFrame(run_frame, text="Data Return Method", padding="5")
        ret_frame.grid(row=6, column=0, columnspan=6, sticky=(tk.W, tk.E), pady=5)

        self.data_return = tk.StringVar(value="USB Harddrives")
        ttk.Radiobutton(ret_frame, text="Bare Drive(s)", variable=self.data_return,
                       value="USB Harddrives", command=self.onDataReturnChange).grid(row=0, column=0, sticky=tk.W, padx=5)
        ttk.Radiobutton(ret_frame, text="Copy to UCF", variable=self.data_return,
                       value="UCF", command=self.onDataReturnChange).grid(row=1, column=0, sticky=tk.W, padx=5)

        ttk.Label(ret_frame, text="UCF Username:").grid(row=1, column=1, sticky=tk.W, padx=5, pady=2)
        self.unamText = ttk.Entry(ret_frame, width=20, state=tk.DISABLED)
        self.unamText.grid(row=1, column=2, sticky=tk.W, padx=5, pady=2)

        if self.parent.project.runs[0].data_return_method == 'USB Harddrives':
            self.data_return.set("USB Harddrives")
        else:
            self.data_return.set("UCF")
            mtch = None
            if self.parent.project.runs[0].comments is not None:
                mtch = idf.UCF_USERNAME_RE.search(self.parent.project.runs[0].comments)
            if mtch is not None:
                self.unamText.insert(0, mtch.group('username'))
            self.unamText.config(state=tk.NORMAL)

        row += 1

        # Buttons
        button_frame = ttk.Frame(main_frame)
        button_frame.grid(row=row, column=0, columnspan=6, pady=10)

        ttk.Button(button_frame, text="OK", command=self.onOK).grid(row=0, column=0, padx=5)
        ttk.Button(button_frame, text="Cancel", command=self.onCancel).grid(row=0, column=1, padx=5)
        ttk.Button(button_frame, text="Save Defaults", command=self.onSaveDefaults).grid(row=0, column=2, padx=5)

    def set_basis(self, basis):
        """Handle basis selection."""
        if basis == 'linear':
            self.linear.set(True)
            self.circul.set(False)
            self.stokes.set(False)
        elif basis == 'circular':
            self.linear.set(False)
            self.circul.set(True)
            self.stokes.set(False)
        else:
            self.linear.set(False)
            self.circul.set(False)
            self.stokes.set(True)

    def onDataReturnChange(self):
        """Handle data return method changes."""
        if self.data_return.get() == "UCF":
            self.unamText.config(state=tk.NORMAL)
        else:
            self.unamText.config(state=tk.DISABLED)

    def onOK(self):
        """Validate and save the observer/project/run information."""
        # Validate required fields
        try:
            junk = int(self.observerIDEntry.get())
            if junk < 1:
                messagebox.showerror("Observer ID Error", "Observer ID must be greater than zero")
                return False
        except ValueError as err:
            messagebox.showerror("Observer ID Error", f"Observer ID must be numeric\n\n{err}")
            return False

        try:
            junk = int(self.runIDEntry.get())
            if junk < 1:
                messagebox.showerror("Run ID Error", "Run ID must be greater than zero")
                return False
        except ValueError as err:
            messagebox.showerror("Run ID Error", f"Run ID must be numeric\n\n{err}")
            return False

        # Update project
        self.parent.project.observer.id = int(self.observerIDEntry.get())
        self.parent.project.observer.first = self.observerFirstEntry.get()
        self.parent.project.observer.last = self.observerLastEntry.get()
        self.parent.project.observer.join_name()

        self.parent.project.id = self.projectIDEntry.get()
        self.parent.project.name = self.projectTitleEntry.get()
        self.parent.project.comments = self.projectCommentsEntry.get('1.0', tk.END).strip().replace('\n', ';;')

        self.parent.project.runs[0].id = int(self.runIDEntry.get())
        self.parent.project.runs[0].name = self.runTitleEntry.get()
        self.parent.project.runs[0].comments = self.runCommentsEntry.get('1.0', tk.END).strip().replace('\n', ';;')

        self.parent.project.runs[0].corr_channels = int(self.nchnText.get(), 10)
        self.parent.project.runs[0].corr_inttime = float(self.tintText.get())

        if self.linear.get():
            self.parent.project.runs[0].corr_basis = 'linear'
        elif self.circul.get():
            self.parent.project.runs[0].corr_basis = 'circular'
        else:
            self.parent.project.runs[0].corr_basis = 'stokes'

        if self.data_return.get() == "USB Harddrives":
            self.parent.project.runs[0].data_return_method = 'USB Harddrives'
        else:
            self.parent.project.runs[0].data_return_method = 'UCF'
            tempc = idf.UCF_USERNAME_RE.sub('', self.parent.project.runs[0].comments)
            self.parent.project.runs[0].comments = tempc + ';;ucfuser:%s' % self.unamText.get()

            mtch = idf.UCF_USERNAME_RE.search(self.parent.project.runs[0].comments)
            if mtch is None:
                messagebox.showerror("Missing UCF User Name",
                    "Cannot find UCF username needed for copying data to the UCF.")
                return False

        self.parent.mode = 'DRX'
        self.parent.setMenuButtons(self.parent.mode)
        if len(self.parent.listControl['columns']) == 0:
            self.parent.addColumns()

        # Cleanup the comments
        self.parent.project.comments = _cleanup0RE.sub(';;', self.parent.project.comments)
        self.parent.project.comments = _cleanup1RE.sub('', self.parent.project.comments)
        self.parent.project.runs[0].comments = _cleanup0RE.sub(';;', self.parent.project.runs[0].comments)
        self.parent.project.runs[0].comments = _cleanup1RE.sub('', self.parent.project.runs[0].comments)

        self.parent.edited = True
        self.parent.setSaveButton()

        self.destroy()

    def onCancel(self):
        self.destroy()

    def onSaveDefaults(self):
        """Save current values as defaults to ~/.sessionGUI."""
        preferences = {}
        try:
            with open(os.path.join(os.path.expanduser('~'), '.sessionGUI')) as ph:
                for line in ph:
                    line = line.strip()
                    if len(line) < 3 or line[0] == '#':
                        continue
                    key, value = line.split(None, 1)
                    preferences[key] = value
        except:
            pass

        try:
            preferences['ObserverID'] = int(self.observerIDEntry.get())
        except (TypeError, ValueError):
            pass

        first = self.observerFirstEntry.get()
        if len(first):
            preferences['ObserverFirstName'] = first

        last = self.observerLastEntry.get()
        if len(last):
            preferences['ObserverLastName'] = last

        pID = self.projectIDEntry.get()
        if len(pID):
            preferences['ProjectID'] = pID

        pTitle = self.projectTitleEntry.get()
        if len(pTitle):
            preferences['ProjectName'] = pTitle

        with open(os.path.join(os.path.expanduser('~'), '.sessionGUI'), 'w') as ph:
            for key in preferences:
                ph.write(f"{key:<24s} {str(preferences[key])}\n")

        messagebox.showinfo("Saved", "Defaults saved to ~/.sessionGUI")


class AdvancedInfo(tk.Toplevel):
    def __init__(self, parent):
        tk.Toplevel.__init__(self, parent)
        self.title('Advanced Settings')
        self.parent = parent

        # Make it modal
        self.transient(parent)
        self.grab_set()

        self.initUI()

    def initUI(self):
        main_frame = ttk.Frame(self, padding="10")
        main_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))

        # Station selection
        station_frame = ttk.LabelFrame(main_frame, text="Stations", padding="5")
        station_frame.grid(row=0, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=5)

        ttk.Label(station_frame, text="Select stations to include in run:").grid(row=0, column=0, columnspan=2, sticky=tk.W, padx=5, pady=2)

        # Create listbox for stations
        self.station_listbox = tk.Listbox(station_frame, selectmode=tk.MULTIPLE, height=10)
        self.station_listbox.grid(row=1, column=0, columnspan=2, sticky=(tk.W, tk.E), padx=5, pady=5)

        # Populate with available stations (example - would need real station list)
        available_stations = ['LWA1', 'LWA-SV', 'LWA-NA']
        for station in available_stations:
            self.station_listbox.insert(tk.END, station)

        # DRX Gain
        gain_frame = ttk.LabelFrame(main_frame, text="DRX Gain", padding="5")
        gain_frame.grid(row=1, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=5)

        ttk.Label(gain_frame, text="Gain:").grid(row=0, column=0, sticky=tk.W, padx=5, pady=2)
        self.gain_combo = ttk.Combobox(gain_frame, values=['MCS Decides', '-1', '0', '6', '12'], width=15)
        self.gain_combo.set('MCS Decides')
        self.gain_combo.grid(row=0, column=1, sticky=tk.W, padx=5, pady=2)

        if hasattr(self.parent.project.runs[0], 'drxGain'):
            if self.parent.project.runs[0].drxGain == -1:
                self.gain_combo.set('MCS Decides')
            else:
                self.gain_combo.set(str(self.parent.project.runs[0].drxGain))

        # Buttons
        button_frame = ttk.Frame(main_frame)
        button_frame.grid(row=2, column=0, columnspan=2, pady=10)

        ttk.Button(button_frame, text="OK", command=self.onOK).grid(row=0, column=0, padx=5)
        ttk.Button(button_frame, text="Cancel", command=self.onCancel).grid(row=0, column=1, padx=5)

    def onOK(self):
        """Save advanced settings."""
        # Parse gain
        if self.gain_combo.get() == 'MCS Decides':
            gain = -1
        else:
            gain = int(self.gain_combo.get())

        self.parent.project.runs[0].drxGain = gain

        # Update all scans with new gain
        for scan in self.parent.project.runs[0].scans:
            scan.gain = gain

        self.parent.edited = True
        self.parent.setSaveButton()

        self.destroy()

    def onCancel(self):
        self.destroy()


class RunDisplay(tk.Toplevel):
    """
    Window for displaying the "Run at a Glance".
    """

    def __init__(self, parent):
        tk.Toplevel.__init__(self, parent)
        self.title('Run at a Glance')
        self.geometry('800x375')
        self.parent = parent

        self.initUI()
        self.initPlot()

    def initUI(self):
        """Start the user interface."""
        self.statusbar = tk.Label(self, text="", bd=1, relief=tk.SUNKEN, anchor=tk.W)
        self.statusbar.pack(side=tk.BOTTOM, fill=tk.X)

        # Create figure
        self.figure = Figure()
        self.canvas = FigureCanvasTkAgg(self.figure, master=self)
        self.canvas.draw()
        self.canvas.get_tk_widget().pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        self.toolbar = NavigationToolbar2Tk(self.canvas, self)
        self.toolbar.update()
        self.canvas.get_tk_widget().pack(side=tk.TOP, fill=tk.BOTH, expand=True)

    def initPlot(self):
        """Plot source altitude for the scans."""
        self.obs = self.parent.project.runs[0].scans

        if len(self.obs) == 0:
            return False

        # Find the earliest scan
        self.earliest = conflict.unravelObs(self.obs)[0][0]

        self.figure.clf()
        self.ax1 = self.figure.gca()
        self.ax2 = self.ax1.twiny()

        i = 0
        station_colors = {}
        for o in self.obs:
            # Get the source
            src = o.fixed_body

            stepSize = o.dur / 1000.0 / 300
            if stepSize < 30.0:
                stepSize = 30.0

            # Find its altitude over the course of the scan
            j = 0
            for station in self.parent.project.runs[0].stations:
                t = []
                alt = []
                dt = 0.0

                # The actual scans
                observer = station.get_observer()

                while dt < o.dur / 1000.0:
                    observer.date = o.mjd + (o.mpm / 1000.0 + dt) / 3600 / 24.0 + MJD_OFFSET - DJD_OFFSET
                    src.compute(observer)

                    alt.append(float(src.alt) * 180.0 / math.pi)
                    t.append(o.mjd + (o.mpm / 1000.0 + dt) / (3600.0 * 24.0) - self.earliest)

                    dt += stepSize

                # Make sure we get the end of the scan
                dt = o.dur / 1000.0
                observer.date = o.mjd + (o.mpm / 1000.0 + dt) / 3600 / 24.0 + MJD_OFFSET - DJD_OFFSET
                src.compute(observer)

                alt.append(float(src.alt) * 180.0 / math.pi)
                t.append(o.mjd + (o.mpm / 1000.0 + dt) / (3600.0 * 24.0) - self.earliest)

                # Plot the altitude over time
                try:
                    l, = self.ax1.plot(t, alt, label='%s - %s' % (o.target, station.id), color=station_colors[station])
                except KeyError:
                    l, = self.ax1.plot(t, alt, label='%s - %s' % (o.target, station.id))
                    station_colors[station] = l.get_color()

                # Draw the scan limits and label the source
                if j == 0:
                    self.ax1.vlines(o.mjd + o.mpm / 1000.0 / (3600.0 * 24.0) - self.earliest, 0, 90, linestyle=':')
                    self.ax1.vlines(o.mjd + (o.mpm / 1000.0 + o.dur / 1000.0) / (3600.0 * 24.0) - self.earliest, 0, 90, linestyle=':')

                    self.ax1.text(o.mjd + o.mpm / 1000.0 / (3600.0 * 24.0) - self.earliest + o.dur / 1000 / 3600 / 24.0 * 0.02,
                                 2 + 10 * (i % 2), o.target, rotation='vertical')

                j += 1

            i += 1

        # Add a legend
        handles, labels = self.ax1.get_legend_handles_labels()
        labels = [l.rsplit(' -', 1)[1] for l in labels]
        self.ax1.legend(handles[:len(self.parent.project.runs[0].stations)],
                       labels[:len(self.parent.project.runs[0].stations)], loc=0)

        # Second set of x axes
        self.ax1.xaxis.tick_bottom()
        self.ax1.set_ylim([0, 90])
        self.ax2.xaxis.tick_top()
        self.ax2.set_xlim([self.ax1.get_xlim()[0] * 24.0, self.ax1.get_xlim()[1] * 24.0])

        # Labels
        self.ax1.set_xlabel('MJD-%i [days]' % self.earliest)
        self.ax1.set_ylabel('Altitude [deg.]')
        self.ax2.set_xlabel('Run Elapsed Time [hours]')
        self.ax2.xaxis.set_label_position('top')

        # Draw
        self.canvas.draw()


class RunUVCoverageDisplay(tk.Toplevel):
    def __init__(self, parent):
        tk.Toplevel.__init__(self, parent)
        self.title('Run (u,v) Coverage')
        self.geometry('800x375')
        self.parent = parent

        self.initUI()
        self.update()
        self.update_idletasks()
        self.initPlot()

    def initUI(self):
        """Start the user interface."""
        self.statusbar = tk.Label(self, text="", bd=1, relief=tk.SUNKEN, anchor=tk.W)
        self.statusbar.pack(side=tk.BOTTOM, fill=tk.X)

        # Create figure
        self.figure = Figure()
        self.canvas = FigureCanvasTkAgg(self.figure, master=self)
        self.canvas.draw()
        self.canvas.get_tk_widget().pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        self.toolbar = NavigationToolbar2Tk(self.canvas, self)
        self.toolbar.update()
        self.canvas.get_tk_widget().pack(side=tk.TOP, fill=tk.BOTH, expand=True)

    def initPlot(self):
        """Plot UV coverage for the scans."""
        self.obs = self.parent.project.runs[0].scans

        if len(self.obs) == 0:
            return False

        # Find the earliest scan
        self.earliest = conflict.unravelObs(self.obs)[0][0]

        self.figure.clf()

        # Build up the list of antennas to use for the UV coverage calculation
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
            # Get the source
            src = o.fixed_body
            if src.name not in order:
                order.append(src.name)
            try:
                uv_coverage[src.name + "@T1"]
            except KeyError:
                uv_coverage[src.name + "@T1"] = []
                uv_coverage[src.name + "@T2"] = []

            stepSize = o.dur / 1000.0 / 300
            if stepSize < 60.0:
                stepSize = 60.0

            # Find the UV coverage across the run
            dt = 0.0
            while dt < o.dur / 1000.0:
                observer.date = o.mjd + (o.mpm / 1000.0 + dt) / 3600 / 24.0 + MJD_OFFSET - DJD_OFFSET
                src.compute(observer)
                HA = (observer.sidereal_time() - src.ra) * 12 / numpy.pi
                dec = src.dec * 180 / numpy.pi

                uvw = uvutils.compute_uvw(antennas, HA=HA, dec=dec, freq=o.frequency1, site=observer, include_auto=False)
                uv_coverage[src.name + "@T1"].append(uvw / 1e3)

                uvw = uvutils.compute_uvw(antennas, HA=HA, dec=dec, freq=o.frequency2, site=observer, include_auto=False)
                uv_coverage[src.name + "@T2"].append(uvw / 1e3)

                dt += stepSize

            # Make sure we get the end of the scan
            dt = o.dur / 1000.0
            observer.date = o.mjd + (o.mpm / 1000.0 + dt) / 3600 / 24.0 + MJD_OFFSET - DJD_OFFSET
            src.compute(observer)
            HA = (observer.sidereal_time() - src.ra) * 12 / numpy.pi
            dec = src.dec * 180 / numpy.pi

            uvw = uvutils.compute_uvw(antennas, HA=HA, dec=dec, freq=o.frequency1, site=observer, include_auto=False)
            uv_coverage[src.name + "@T1"].append(uvw / 1e3)

            uvw = uvutils.compute_uvw(antennas, HA=HA, dec=dec, freq=o.frequency2, site=observer, include_auto=False)
            uv_coverage[src.name + "@T2"].append(uvw / 1e3)

            i += 1

        # Plot
        nPlot = len(order)
        nRow = int(numpy.ceil(numpy.sqrt(nPlot)))
        nCol = int(numpy.ceil(nPlot / nRow))

        i = 0
        for name in order:
            key = name + '@T1'
            t1 = uv_coverage[key]
            t2 = uv_coverage[key.replace('@T1', '@T2')]

            ax = self.figure.add_subplot(nCol, nRow, i + 1)
            for t in t1:
                ax.scatter(t[:, 0, 0], t[:, 1, 0], marker='+', color='b')
                ax.scatter(-t[:, 0, 0], -t[:, 1, 0], marker='+', color='b')
            for t in t2:
                ax.scatter(t[:, 0, 0], t[:, 1, 0], marker='+', color='g')
                ax.scatter(-t[:, 0, 0], -t[:, 1, 0], marker='+', color='g')

            # Labels
            ax.set_xlabel('$u$ [k$\\lambda$]')
            ax.set_ylabel('$v$ [k$\\lambda$]')
            ax.set_title(key.replace('@T1', ''))

            i += 1

        # Draw
        self.canvas.draw()


class VolumeInfo(tk.Toplevel):
    def __init__(self, parent):
        tk.Toplevel.__init__(self, parent)
        self.title('Estimated Data Volume')
        self.parent = parent

        # Make it modal
        self.transient(parent)
        self.grab_set()

        self.initUI()

    def initUI(self):
        main_frame = ttk.Frame(self, padding="10")
        main_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))

        row = 0

        # Title
        title_label = ttk.Label(main_frame, text="Estimated Data Volume:", font=('TkDefaultFont', 12, 'bold'))
        title_label.grid(row=row, column=0, columnspan=4, pady=10)
        row += 1

        # Headers
        ttk.Label(main_frame, text="Raw:").grid(row=row, column=2, padx=5, pady=2)
        ttk.Label(main_frame, text="Final:").grid(row=row, column=3, padx=5, pady=2)
        row += 1

        # Calculate volumes for each scan
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
            corDataVolume = 32 * 2 * 4 \
                            * self.parent.project.runs[0].corr_channels \
                            * obs.dur / 1000.0 / self.parent.project.runs[0].corr_inttime \
                            * len(self.parent.project.runs[0].stations) * (len(self.parent.project.runs[0].stations) + 1) / 2 \
                            * 1.02

            ttk.Label(main_frame, text=f'Scan #{scanCount}').grid(row=row, column=0, sticky=tk.W, padx=5, pady=2)
            ttk.Label(main_frame, text=mode).grid(row=row, column=1, padx=5, pady=2)
            ttk.Label(main_frame, text=f'{rawDataVolume/1024.0**3:.2f} GB').grid(row=row, column=2, padx=5, pady=2)
            ttk.Label(main_frame, text=f'{corDataVolume/1024.0**2:.2f} MB').grid(row=row, column=3, padx=5, pady=2)

            scanCount += 1
            rawTotalData += rawDataVolume
            corTotalData += corDataVolume
            row += 1

        # Separator
        ttk.Separator(main_frame, orient=tk.HORIZONTAL).grid(row=row, column=0, columnspan=4, sticky=(tk.W, tk.E), pady=10)
        row += 1

        # Totals
        ttk.Label(main_frame, text='Totals:', font=('TkDefaultFont', 10, 'bold')).grid(row=row, column=0, sticky=tk.W, padx=5, pady=2)
        ttk.Label(main_frame, text=f'{rawTotalData/1024.0**3:.2f} GB', font=('TkDefaultFont', 10, 'bold')).grid(row=row, column=2, padx=5, pady=2)
        ttk.Label(main_frame, text=f'{corTotalData/1024.0**3:.2f} GB', font=('TkDefaultFont', 10, 'bold')).grid(row=row, column=3, padx=5, pady=2)
        row += 1

        # OK button
        ttk.Button(main_frame, text="OK", command=self.destroy).grid(row=row, column=3, pady=10)


class ResolveTarget(tk.Toplevel):
    def __init__(self, parent):
        tk.Toplevel.__init__(self, parent)
        self.title('Resolve Target')
        self.parent = parent

        # Make it modal
        self.transient(parent)
        self.grab_set()

        self.setSource()
        self.initUI()

    def setSource(self):
        for i, child in enumerate(self.parent.listControl.get_children()):
            if self.parent.listControl.is_checked(child):
                values = self.parent.listControl.item(child, 'values')
                self.scanID = i
                self.source = values[0]  # Target name
                return True

        self.scanID = -1
        self.source = ''
        return False

    def initUI(self):
        main_frame = ttk.Frame(self, padding="10")
        main_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))

        row = 0

        # Target name
        ttk.Label(main_frame, text="Target Name:").grid(row=row, column=0, sticky=tk.W, padx=5, pady=2)
        self.srcText = ttk.Entry(main_frame, width=40)
        self.srcText.insert(0, self.source)
        self.srcText.grid(row=row, column=1, columnspan=4, sticky=(tk.W, tk.E), padx=5, pady=2)
        row += 1

        ttk.Separator(main_frame, orient=tk.HORIZONTAL).grid(row=row, column=0, columnspan=5, sticky=(tk.W, tk.E), pady=10)
        row += 1

        # RA
        ttk.Label(main_frame, text="RA (hours, J2000):").grid(row=row, column=0, sticky=tk.W, padx=5, pady=2)
        self.raText = ttk.Entry(main_frame, width=40, state='readonly')
        self.raText.insert(0, '---')
        self.raText.grid(row=row, column=1, columnspan=4, sticky=(tk.W, tk.E), padx=5, pady=2)
        row += 1

        # Dec
        ttk.Label(main_frame, text="Dec (degrees, J2000):").grid(row=row, column=0, sticky=tk.W, padx=5, pady=2)
        self.decText = ttk.Entry(main_frame, width=40, state='readonly')
        self.decText.insert(0, '---')
        self.decText.grid(row=row, column=1, columnspan=4, sticky=(tk.W, tk.E), padx=5, pady=2)
        row += 1

        # PM - RA
        ttk.Label(main_frame, text="PM - RA (mas/yr):").grid(row=row, column=0, sticky=tk.W, padx=5, pady=2)
        self.prText = ttk.Entry(main_frame, width=40, state='readonly')
        self.prText.insert(0, '---')
        self.prText.grid(row=row, column=1, columnspan=4, sticky=(tk.W, tk.E), padx=5, pady=2)
        row += 1

        # PM - Dec
        ttk.Label(main_frame, text="PM - Dec (mas/yr):").grid(row=row, column=0, sticky=tk.W, padx=5, pady=2)
        self.pdText = ttk.Entry(main_frame, width=40, state='readonly')
        self.pdText.insert(0, '---')
        self.pdText.grid(row=row, column=1, columnspan=4, sticky=(tk.W, tk.E), padx=5, pady=2)
        row += 1

        # Service used
        ttk.Label(main_frame, text="Service Used:").grid(row=row, column=0, sticky=tk.W, padx=5, pady=2)
        self.srvText = ttk.Entry(main_frame, width=40, state='readonly')
        self.srvText.insert(0, '---')
        self.srvText.grid(row=row, column=1, columnspan=4, sticky=(tk.W, tk.E), padx=5, pady=2)
        row += 1

        ttk.Separator(main_frame, orient=tk.HORIZONTAL).grid(row=row, column=0, columnspan=5, sticky=(tk.W, tk.E), pady=10)
        row += 1

        # Include PM checkbox
        self.inclPM = tk.BooleanVar(value=False)
        ttk.Checkbutton(main_frame, text="Include PM", variable=self.inclPM).grid(row=row, column=0, sticky=tk.W, padx=5, pady=2)

        # Buttons
        ttk.Button(main_frame, text="Resolve", command=self.onResolve).grid(row=row, column=2, padx=5)
        self.appli = ttk.Button(main_frame, text="Apply", command=self.onApply, state=tk.DISABLED)
        self.appli.grid(row=row, column=3, padx=5)
        ttk.Button(main_frame, text="Cancel", command=self.destroy).grid(row=row, column=4, padx=5)

    def onResolve(self):
        self.source = self.srcText.get()
        try:
            posn = astro.resolve_name(self.source)

            # Update fields (make them normal first to edit)
            self.raText.config(state='normal')
            self.raText.delete(0, tk.END)
            self.raText.insert(0, str(astro.deg_to_hms(posn.ra)).replace(' ', ':'))
            self.raText.config(state='readonly')

            self.decText.config(state='normal')
            self.decText.delete(0, tk.END)
            self.decText.insert(0, str(astro.deg_to_dms(posn.dec)).replace(' ', ':'))
            self.decText.config(state='readonly')

            self.prText.config(state='normal')
            self.prText.delete(0, tk.END)
            self.prText.insert(0, '---')
            self.prText.config(state='readonly')

            self.pdText.config(state='normal')
            self.pdText.delete(0, tk.END)
            self.pdText.insert(0, '---')
            self.pdText.config(state='readonly')

            self.srvText.config(state='normal')
            self.srvText.delete(0, tk.END)
            self.srvText.insert(0, posn.resolved_by)
            self.srvText.config(state='readonly')

            if self.inclPM.get():
                if posn.pm_ra is not None:
                    self.prText.config(state='normal')
                    self.prText.delete(0, tk.END)
                    self.prText.insert(0, f"{posn.pm_ra:.2f}")
                    self.prText.config(state='readonly')

                    self.pdText.config(state='normal')
                    self.pdText.delete(0, tk.END)
                    self.pdText.insert(0, f"{posn.pm_dec:.2f}")
                    self.pdText.config(state='readonly')

            if self.scanID != -1:
                self.appli.config(state=tk.NORMAL)

        except RuntimeError:
            self.raText.config(state='normal')
            self.raText.delete(0, tk.END)
            self.raText.insert(0, "---")
            self.raText.config(state='readonly')

            self.decText.config(state='normal')
            self.decText.delete(0, tk.END)
            self.decText.insert(0, "---")
            self.decText.config(state='readonly')

            self.srvText.config(state='normal')
            self.srvText.delete(0, tk.END)
            self.srvText.insert(0, "Error resolving target")
            self.srvText.config(state='readonly')

    def onApply(self):
        if self.scanID == -1:
            return False
        else:
            success = True

            obsIndex = self.scanID
            pmText = "%s %s" % (self.prText.get(), self.pdText.get())

            for obsAttr, widget in [(6, self.raText), (7, self.decText), (11, pmText)]:
                try:
                    try:
                        text = widget.get()
                    except AttributeError:
                        text = widget

                    newData = self.parent.coerceMap[obsAttr](text)

                    oldData = getattr(self.parent.project.runs[0].scans[obsIndex], self.parent.columnMap[obsAttr])
                    if newData != oldData:
                        setattr(self.parent.project.runs[0].scans[obsIndex], self.parent.columnMap[obsAttr], newData)
                        self.parent.project.runs[0].scans[obsIndex].update()

                        if obsAttr < len(self.parent.listControl['columns']):
                            children = self.parent.listControl.get_children()
                            item = children[obsIndex]
                            values = list(self.parent.listControl.item(item, 'values'))
                            values[obsAttr - 1] = text
                            self.parent.listControl.item(item, values=values)

                        self.parent.edited = True
                        self.parent.setSaveButton()
                        self.appli.config(state=tk.DISABLED)

                except ValueError as err:
                    success = False
                    pid_print(f"Error: {str(err)}")

            if success:
                self.destroy()


class ScheduleWindow(tk.Toplevel):
    def __init__(self, parent):
        tk.Toplevel.__init__(self, parent)
        self.title('Run Scheduling')
        self.parent = parent

        # Make it modal
        self.transient(parent)
        self.grab_set()

        self.initUI()

    def initUI(self):
        main_frame = ttk.Frame(self, padding="10")
        main_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))

        row = 0

        # Title
        title_label = ttk.Label(main_frame, text="Rescheduling Options:", font=('TkDefaultFont', 12, 'bold'))
        title_label.grid(row=row, column=0, columnspan=3, pady=10)
        row += 1

        # Radio buttons
        self.schedule_type = tk.StringVar(value="sidereal")

        if self.parent.project.runs[0].comments.find('ScheduleSolarMovable') != -1:
            self.schedule_type.set("solar")
        elif self.parent.project.runs[0].comments.find('ScheduleFixed') != -1:
            self.schedule_type.set("fixed")
        else:
            self.schedule_type.set("sidereal")

        ttk.Radiobutton(main_frame, text="Sidereal time fixed, date changeable",
                       variable=self.schedule_type, value="sidereal").grid(row=row, column=0, sticky=tk.W, padx=5, pady=2)
        row += 1

        ttk.Radiobutton(main_frame, text="UTC time fixed, date changeable",
                       variable=self.schedule_type, value="solar").grid(row=row, column=0, sticky=tk.W, padx=5, pady=2)
        row += 1

        ttk.Radiobutton(main_frame, text="Use only specified date/time",
                       variable=self.schedule_type, value="fixed").grid(row=row, column=0, sticky=tk.W, padx=5, pady=2)
        row += 1

        ttk.Separator(main_frame, orient=tk.HORIZONTAL).grid(row=row, column=0, columnspan=3, sticky=(tk.W, tk.E), pady=10)
        row += 1

        # Buttons
        button_frame = ttk.Frame(main_frame)
        button_frame.grid(row=row, column=0, columnspan=3)

        ttk.Button(button_frame, text="Apply", command=self.onApply).grid(row=0, column=0, padx=5)
        ttk.Button(button_frame, text="Cancel", command=self.destroy).grid(row=0, column=1, padx=5)

    def onApply(self):
        oldComments = self.parent.project.runs[0].comments
        oldComments = oldComments.replace('ScheduleSiderealMovable', '')
        oldComments = oldComments.replace('ScheduleSolarMovable', '')
        oldComments = oldComments.replace('ScheduleFixed', '')

        if self.schedule_type.get() == "sidereal":
            oldComments += ';;ScheduleSiderealMovable'
        elif self.schedule_type.get() == "solar":
            oldComments += ';;ScheduleSolarMovable'
        elif self.schedule_type.get() == "fixed":
            oldComments += ';;ScheduleFixed'

        self.parent.project.runs[0].comments = oldComments

        self.parent.edited = True
        self.parent.setSaveButton()

        self.destroy()


class ProperMotionWindow(tk.Toplevel):
    def __init__(self, parent, scanID):
        self.parent = parent
        self.scanID = scanID
        self.scan = self.parent.project.runs[0].scans[self.scanID]

        title = 'Scan #%i Proper Motion' % (scanID + 1,)
        tk.Toplevel.__init__(self, parent)
        self.title(title)

        # Make it modal
        self.transient(parent)
        self.grab_set()

        self.initUI()

    def initUI(self):
        main_frame = ttk.Frame(self, padding="10")
        main_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))

        row = 0

        # Target name
        ttk.Label(main_frame, text="Target Name:").grid(row=row, column=0, sticky=tk.W, padx=5, pady=2)
        srcText = ttk.Entry(main_frame, width=40, state='readonly')
        srcText.insert(0, self.scan.target)
        srcText.grid(row=row, column=1, columnspan=4, sticky=(tk.W, tk.E), padx=5, pady=2)
        row += 1

        ttk.Separator(main_frame, orient=tk.HORIZONTAL).grid(row=row, column=0, columnspan=5, sticky=(tk.W, tk.E), pady=10)
        row += 1

        # PM - RA
        ttk.Label(main_frame, text="RA (mas/yr):").grid(row=row, column=0, sticky=tk.W, padx=5, pady=2)
        self.prText = ttk.Entry(main_frame, width=40)
        self.prText.insert(0, "%+.3f" % self.scan.pm[0])
        self.prText.grid(row=row, column=1, columnspan=4, sticky=(tk.W, tk.E), padx=5, pady=2)
        row += 1

        # PM - Dec
        ttk.Label(main_frame, text="Dec (mas/yr):").grid(row=row, column=0, sticky=tk.W, padx=5, pady=2)
        self.pdText = ttk.Entry(main_frame, width=40)
        self.pdText.insert(0, "%+.3f" % self.scan.pm[1])
        self.pdText.grid(row=row, column=1, columnspan=4, sticky=(tk.W, tk.E), padx=5, pady=2)
        row += 1

        ttk.Separator(main_frame, orient=tk.HORIZONTAL).grid(row=row, column=0, columnspan=5, sticky=(tk.W, tk.E), pady=10)
        row += 1

        # Buttons
        button_frame = ttk.Frame(main_frame)
        button_frame.grid(row=row, column=0, columnspan=5)

        ttk.Button(button_frame, text="Apply", command=self.onApply).grid(row=0, column=0, padx=5)
        ttk.Button(button_frame, text="Cancel", command=self.destroy).grid(row=0, column=1, padx=5)

    def onApply(self):
        if self.scanID == -1:
            return False
        else:
            success = True

            obsIndex = self.scanID
            obsAttr = 11
            text = "%s %s" % (self.prText.get(), self.pdText.get())

            try:
                newData = self.parent.coerceMap[obsAttr](text)

                oldData = getattr(self.parent.project.runs[0].scans[obsIndex], self.parent.columnMap[obsAttr])
                if newData != oldData:
                    setattr(self.parent.project.runs[0].scans[obsIndex], self.parent.columnMap[obsAttr], newData)
                    self.parent.project.runs[0].scans[obsIndex].update()

                    self.parent.edited = True
                    self.parent.setSaveButton()

            except ValueError as err:
                success = False
                pid_print(f"Error: {str(err)}")

            if success:
                self.destroy()


class SearchWindow(OCS):
    def __init__(self, parent, scanID):
        self.parent = parent
        self.scanID = scanID
        target, ra, dec = None, None, None

        if self.scanID >= 0:
            children = self.parent.listControl.get_children()
            if self.scanID < len(children):
                values = self.parent.listControl.item(children[self.scanID], 'values')
                target = values[0]  # Target
                ra = values[5]      # RA
                dec = values[6]     # Dec

        OCS.__init__(self, target=target, ra=ra, dec=dec)


class HelpWindow(tk.Toplevel):
    def __init__(self, parent):
        tk.Toplevel.__init__(self, parent)
        self.title('Swarm GUI Handbook')
        self.geometry('570x400')

        self.initUI()

    def initUI(self):
        # Create main frame
        main_frame = ttk.Frame(self)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Create text widget with scrollbar
        text_frame = ttk.Frame(main_frame)
        text_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        scrollbar = ttk.Scrollbar(text_frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.text = tk.Text(text_frame, wrap=tk.WORD, yscrollcommand=scrollbar.set)
        self.text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.config(command=self.text.yview)

        # Try to load help file
        help_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'docs/swarm_help.html')

        if os.path.exists(help_file):
            try:
                with open(help_file, 'r') as f:
                    help_text = f.read()
                    # Strip HTML tags for basic display
                    help_text = re.sub('<[^<]+?>', '', help_text)
                    self.text.insert('1.0', help_text)
            except:
                self.text.insert('1.0', "Help file could not be loaded.")
        else:
            help_text = """Swarm GUI Handbook

Welcome to Swarm GUI - a tool for creating Interferometer Definition Files (IDFs) for LWA swarm mode observations.

Main Features:
- Create and edit DRX scans (RA/Dec, Solar, Jovian)
- Manage observer, project, and run information
- Validate IDF files
- Visualize run timelines and UV coverage
- Search for calibrators
- Estimate data volumes

Getting Started:
1. Use File > New to start a new IDF
2. Enter observer and project information
3. Add scans using the Scans menu or toolbar
4. Edit scan parameters directly in the list
5. Validate and save your IDF

For more information, see the online documentation.
"""
            self.text.insert('1.0', help_text)

        self.text.config(state=tk.DISABLED)

        # Status bar
        self.statusbar = tk.Label(self, text="", bd=1, relief=tk.SUNKEN, anchor=tk.W)
        self.statusbar.pack(side=tk.BOTTOM, fill=tk.X)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description='GUI for making all sorts of interferometer definition files (IDFs) for the LWA interferometer',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument('filename', type=str, nargs='?',
                       help='filename of IDF to edit')
    parser.add_argument('-d', '--drsu-size', type=aph.positive_int, default=idf._DRSUCapacityTB,
                       help='perform storage calculations assuming the specified DRSU size in TB')
    args = parser.parse_args()

    app = IDFCreator('Interferometer Definition File', args)
    app.mainloop()
