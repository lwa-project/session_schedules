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
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from tkinter import font as tkfont
import webbrowser

import conflict

import lsl
from lsl import astro
from lsl.common.dp import fS
from lsl.common import stations, sdf, sdfADP, sdfNDP
from lsl.astro import deg_to_dms, deg_to_hms, MJD_OFFSET, DJD_OFFSET
from lsl.reader.tbn import FILTER_CODES as TBNFilters
from lsl.reader.drx import FILTER_CODES as DRXFilters
from lsl.misc import parser as aph

import matplotlib
matplotlib.use('TkAgg')
matplotlib.interactive(True)

from matplotlib.backends.backend_tkagg import NavigationToolbar2Tk, FigureCanvasTkAgg
from matplotlib.figure import Figure
from matplotlib.ticker import NullFormatter, NullLocator

__version__ = "0.6"
__author__ = "Jayce Dowell"


ALLOW_TBW_TBN_SAME_SDF = True


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

            # Call the after_edit callback if it exists (PATCH 2 & 3)
            if hasattr(self, 'after_edit'):
                self.after_edit(self.entry_item, self.entry_col, new_value)
            elif hasattr(self, 'on_edit_callback'):
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


class ObservationTreeview(CheckableTreeview, EditableCell):
    """Combined Treeview with checkboxes and editable cells for observations."""

    def __init__(self, master=None, **kwargs):
        CheckableTreeview.__init__(self, master, **kwargs)
        EditableCell.__init__(self)
        self.editable_columns = []
        self.column_options = {}
        self.parent = None

    def after_edit(self, item, column, value):
        """
        PATCH 2: Override of after_edit() to call parent's on_edit() for validation.
        This connects the EditableCell editing to the SDFCreator validation.
        """
        if hasattr(self, 'parent') and hasattr(self.parent, 'on_edit'):
            return self.parent.on_edit(item, column, value)
        else:
            # Default behavior - just update the value
            values = list(self.item(item, 'values'))
            if 0 <= column < len(values):
                values[column] = value
                self.item(item, values=values)
            return True


class SteppedTreeview(CheckableTreeview, EditableCell):
    """Treeview for stepped observations with editable cells."""

    def __init__(self, master=None, **kwargs):
        CheckableTreeview.__init__(self, master, **kwargs)
        EditableCell.__init__(self)
        self.editable_columns = []
        self.column_options = {}
        self.parent = None

    def after_edit(self, item, column, value):
        """
        PATCH 3: Override of after_edit() for SteppedTreeview to call parent's on_edit().
        """
        if hasattr(self, 'parent') and hasattr(self.parent, 'on_edit'):
            return self.parent.on_edit(item, column, value)
        else:
            # Default behavior - just update the value
            values = list(self.item(item, 'values'))
            if 0 <= column < len(values):
                values[column] = value
                self.item(item, values=values)
            return True


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
ID_ADD_TBW = 23
ID_ADD_TBF = 24
ID_ADD_TBN = 25
ID_ADD_DRX_RADEC = 26
ID_ADD_DRX_SOLAR = 27
ID_ADD_DRX_JOVIAN = 28
ID_ADD_DRX_LUNAR = 29
ID_ADD_STEPPED_RADEC = 30
ID_ADD_STEPPED_AZALT = 31
ID_EDIT_STEPPED = 32
ID_REMOVE = 33
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

_cleanup0RE = re.compile(r';;(;;)+')
_cleanup1RE = re.compile(r'^;;')


class SDFCreator(tk.Tk):
    def __init__(self, title, args):
        tk.Tk.__init__(self)
        self.title(title)
        self.geometry("750x500")

        self.station = stations.lwa1
        self.sdf = sdf
        self.adp = False
        self.ndp = False
        if args.lwasv:
            self.station = stations.lwasv
            self.sdf = sdfADP
            self.adp = True
        if args.lwana:
            self.station = stations.lwana
            self.sdf = sdfNDP
            self.ndp = True

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

        self.sdf._DRSUCapacityTB = args.drsu_size

        if args.filename is not None:
            self.filename = args.filename
            self.parseFile(self.filename)
        else:
            self.filename = ''
            self.setMenuButtons('None')

        self.edited = False
        self.badEdit = False
        self.setSaveButton()

    def initSDF(self):
        """
        Create an empty sdf.project instance to store all of the actual
        observations.
        """

        po = self.sdf.ProjectOffice()
        observer = self.sdf.Observer('', 0, first='', last='')
        project = self.sdf.Project(observer, '', '', project_office=po)
        session = self.sdf.Session('session_name', 0, observations=[])
        project.sessions = [session,]

        self.project = project
        self.mode = ''

        self.project.sessions[0].drxGain = -1

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
        editMenu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Edit", menu=editMenu)
        editMenu.add_command(label="Cut Selected Observation", command=self.onCut, state=tk.DISABLED)
        editMenu.add_command(label="Copy Selected Observation", command=self.onCopy, state=tk.DISABLED)
        editMenu.add_command(label="Paste Before Selected", command=self.onPasteBefore, state=tk.DISABLED)
        editMenu.add_command(label="Paste After Selected", command=self.onPasteAfter, state=tk.DISABLED)
        editMenu.add_command(label="Paste at End of List", command=self.onPasteEnd, state=tk.DISABLED)

        self.editmenu['cut'] = 0
        self.editmenu['copy'] = 1
        self.editmenu['pasteBefore'] = 2
        self.editmenu['pasteAfter'] = 3
        self.editmenu['pasteEnd'] = 4

        # Observations menu
        obsMenu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Observations", menu=obsMenu)
        obsMenu.add_command(label="Observer/Project/Session Info.", command=self.onInfo)
        obsMenu.add_command(label="Scheduling", command=self.onSchedule)
        obsMenu.add_separator()

        addMenu = tk.Menu(obsMenu, tearoff=0)
        obsMenu.add_cascade(label="Add", menu=addMenu)
        addMenu.add_command(label="TBW", command=self.onAddTBW)
        addMenu.add_command(label="TBN", command=self.onAddTBN)
        addMenu.add_command(label="DRX - RA/Dec", command=self.onAddDRXR)
        addMenu.add_command(label="DRX - Solar", command=self.onAddDRXS)
        addMenu.add_command(label="DRX - Jovian", command=self.onAddDRXJ)
        addMenu.add_command(label="DRX - Lunar", command=self.onAddDRXL)
        addMenu.add_command(label="STEPPED - RA/Dec", command=self.onAddSteppedR)
        addMenu.add_command(label="STEPPED - Az/Alt", command=self.onAddSteppedA)

        obsMenu.add_command(label="Edit STEPPED Observation", command=self.onEditStepped, state=tk.DISABLED)
        obsMenu.add_command(label="Remove Selected", command=self.onRemove, state=tk.DISABLED)
        obsMenu.add_command(label="Validate All\tF5", command=self.onValidate, accelerator="F5")
        obsMenu.add_separator()
        obsMenu.add_command(label="Resolve Selected\tF3", command=self.onResolve, state=tk.DISABLED, accelerator="F3")
        obsMenu.add_separator()
        obsMenu.add_command(label="Session at a Glance", command=self.onTimeseries)
        obsMenu.add_command(label="Advanced Settings", command=self.onAdvanced)

        self.obsmenu['tbw'] = (addMenu, 0)
        self.obsmenu['tbn'] = (addMenu, 1)
        self.obsmenu['drx-radec'] = (addMenu, 2)
        self.obsmenu['drx-solar'] = (addMenu, 3)
        self.obsmenu['drx-jovian'] = (addMenu, 4)
        self.obsmenu['drx-lunar'] = (addMenu, 5)
        self.obsmenu['stepped-radec'] = (addMenu, 6)
        self.obsmenu['stepped-azalt'] = (addMenu, 7)
        self.obsmenu['steppedEdit'] = (obsMenu, 8)
        self.obsmenu['remove'] = (obsMenu, 9)
        self.obsmenu['resolve'] = (obsMenu, 12)

        # Data menu
        dataMenu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Data", menu=dataMenu)
        dataMenu.add_command(label="Estimated Data Volume", command=self.onVolume)

        # Help menu
        helpMenu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Help", menu=helpMenu)
        helpMenu.add_command(label="Session GUI Handbook\tF1", command=self.onHelp, accelerator="F1")
        helpMenu.add_command(label="Filter Codes", command=self.onFilterInfo)
        helpMenu.add_separator()
        helpMenu.add_command(label="About", command=self.onAbout)

        # Toolbar
        toolbar_frame = tk.Frame(self, bd=1, relief=tk.RAISED)
        toolbar_frame.pack(side=tk.TOP, fill=tk.X)

        # Create toolbar buttons
        self.toolbar_buttons = {}

        btn = tk.Button(toolbar_frame, text="New", command=self.onNew)
        btn.pack(side=tk.LEFT, padx=2, pady=2)
        self.toolbar_buttons['new'] = btn

        btn = tk.Button(toolbar_frame, text="Open", command=self.onLoad)
        btn.pack(side=tk.LEFT, padx=2, pady=2)
        self.toolbar_buttons['open'] = btn

        btn = tk.Button(toolbar_frame, text="Save", command=self.onSave, state=tk.DISABLED)
        btn.pack(side=tk.LEFT, padx=2, pady=2)
        self.toolbar_buttons['save'] = btn
        self.savemenu = btn

        btn = tk.Button(toolbar_frame, text="Save As", command=self.onSaveAs)
        btn.pack(side=tk.LEFT, padx=2, pady=2)
        self.toolbar_buttons['saveas'] = btn

        btn = tk.Button(toolbar_frame, text="Quit", command=self.onQuit)
        btn.pack(side=tk.LEFT, padx=2, pady=2)
        self.toolbar_buttons['quit'] = btn

        ttk.Separator(toolbar_frame, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=5)

        btn = tk.Button(toolbar_frame, text="TBW", command=self.onAddTBW)
        btn.pack(side=tk.LEFT, padx=2, pady=2)
        self.toolbar_buttons['tbw'] = btn

        btn = tk.Button(toolbar_frame, text="TBN", command=self.onAddTBN)
        btn.pack(side=tk.LEFT, padx=2, pady=2)
        self.toolbar_buttons['tbn'] = btn

        btn = tk.Button(toolbar_frame, text="DRX-RA/Dec", command=self.onAddDRXR)
        btn.pack(side=tk.LEFT, padx=2, pady=2)
        self.toolbar_buttons['drx-radec'] = btn

        btn = tk.Button(toolbar_frame, text="DRX-Solar", command=self.onAddDRXS)
        btn.pack(side=tk.LEFT, padx=2, pady=2)
        self.toolbar_buttons['drx-solar'] = btn

        btn = tk.Button(toolbar_frame, text="DRX-Jovian", command=self.onAddDRXJ)
        btn.pack(side=tk.LEFT, padx=2, pady=2)
        self.toolbar_buttons['drx-jovian'] = btn

        btn = tk.Button(toolbar_frame, text="Stepped", command=self.onAddSteppedR)
        btn.pack(side=tk.LEFT, padx=2, pady=2)
        self.toolbar_buttons['stepped'] = btn

        btn = tk.Button(toolbar_frame, text="Edit", command=self.onEditStepped, state=tk.DISABLED)
        btn.pack(side=tk.LEFT, padx=2, pady=2)
        self.toolbar_buttons['edit-stepped'] = btn

        btn = tk.Button(toolbar_frame, text="Remove", command=self.onRemove, state=tk.DISABLED)
        btn.pack(side=tk.LEFT, padx=2, pady=2)
        self.toolbar_buttons['remove'] = btn

        btn = tk.Button(toolbar_frame, text="Validate", command=self.onValidate)
        btn.pack(side=tk.LEFT, padx=2, pady=2)
        self.toolbar_buttons['validate'] = btn

        ttk.Separator(toolbar_frame, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=5)

        btn = tk.Button(toolbar_frame, text="Help", command=self.onHelp)
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

        # Observation list control
        self.listControl = ObservationTreeview(self.listFrame)
        self.listControl.pack(fill=tk.BOTH, expand=True)
        self.listControl.parent = self

        # Bind scrolling
        self.listFrame.bind('<Configure>', lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))

        # Setup callbacks
        self.listControl.on_check_callback = self.onCheckItem
        self.listControl.on_edit_callback = self.on_edit

        # PATCH 5: Configure tags for error highlighting
        self.listControl.tag_configure('error', foreground='red')
        self.listControl.tag_configure('invalid', foreground='red')

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
            editMenu = self.nametowidget(self.cget('menu')).nametowidget('!menu')
            try:
                editMenu.entryconfig(self.editmenu['cut'], state=tk.DISABLED)
                editMenu.entryconfig(self.editmenu['copy'], state=tk.DISABLED)
            except:
                pass

            # Stepped observation edits - disabled
            try:
                obsMenu = self.nametowidget(self.cget('menu')).nametowidget('!menu2')
                obsMenu.entryconfig(self.obsmenu['steppedEdit'][1], state=tk.DISABLED)
                self.toolbar_buttons['edit-stepped'].config(state=tk.DISABLED)
            except (KeyError, AttributeError):
                pass

            # Remove and resolve - disabled
            obsMenu = self.nametowidget(self.cget('menu')).nametowidget('!menu2')
            obsMenu.entryconfig(self.obsmenu['remove'][1], state=tk.DISABLED)
            obsMenu.entryconfig(self.obsmenu['resolve'][1], state=tk.DISABLED)

            self.toolbar_buttons['remove'].config(state=tk.DISABLED)

        elif selected_count == 1:
            # Edit menu - enabled
            editMenu = self.nametowidget(self.cget('menu')).nametowidget('!menu')
            try:
                editMenu.entryconfig(self.editmenu['cut'], state=tk.NORMAL)
                editMenu.entryconfig(self.editmenu['copy'], state=tk.NORMAL)
            except:
                pass

            # Stepped observation edits - enabled if there is an index and it is STEPPED,
            # disabled otherwise
            if index is not None and index < len(self.project.sessions[0].observations):
                if self.project.sessions[0].observations[index].mode == 'STEPPED':
                    try:
                        obsMenu = self.nametowidget(self.cget('menu')).nametowidget('!menu2')
                        obsMenu.entryconfig(self.obsmenu['steppedEdit'][1], state=tk.NORMAL)
                        self.toolbar_buttons['edit-stepped'].config(state=tk.NORMAL)
                    except (KeyError, AttributeError):
                        pass
                else:
                    try:
                        obsMenu = self.nametowidget(self.cget('menu')).nametowidget('!menu2')
                        obsMenu.entryconfig(self.obsmenu['steppedEdit'][1], state=tk.DISABLED)
                        self.toolbar_buttons['edit-stepped'].config(state=tk.DISABLED)
                    except (KeyError, AttributeError):
                        pass

            # Remove and resolve - enabled
            obsMenu = self.nametowidget(self.cget('menu')).nametowidget('!menu2')
            obsMenu.entryconfig(self.obsmenu['remove'][1], state=tk.NORMAL)
            obsMenu.entryconfig(self.obsmenu['resolve'][1], state=tk.NORMAL)

            self.toolbar_buttons['remove'].config(state=tk.NORMAL)

        else:
            # Edit menu - enabled
            editMenu = self.nametowidget(self.cget('menu')).nametowidget('!menu')
            try:
                editMenu.entryconfig(self.editmenu['cut'], state=tk.NORMAL)
                editMenu.entryconfig(self.editmenu['copy'], state=tk.NORMAL)
            except:
                pass

            # Stepped observation edits - disabled
            try:
                obsMenu = self.nametowidget(self.cget('menu')).nametowidget('!menu2')
                obsMenu.entryconfig(self.obsmenu['steppedEdit'][1], state=tk.DISABLED)
                self.toolbar_buttons['edit-stepped'].config(state=tk.DISABLED)
            except (KeyError, AttributeError):
                pass

            # Remove enabled, resolve disabled
            obsMenu = self.nametowidget(self.cget('menu')).nametowidget('!menu2')
            obsMenu.entryconfig(self.obsmenu['remove'][1], state=tk.NORMAL)
            obsMenu.entryconfig(self.obsmenu['resolve'][1], state=tk.DISABLED)

            self.toolbar_buttons['remove'].config(state=tk.NORMAL)

    def on_edit(self, item, col, new_value):
        """
        PATCH 1: Complete on_edit() method in SDFCreator.
        Handle cell editing with validation and update.
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

        # Clear status bar
        self.statusbar.config(text='')

        try:
            # Validate and convert the new value
            newData = self.coerceMap[col + 1](new_value)

            # Get the old value
            oldData = getattr(self.project.sessions[0].observations[row_index], self.columnMap[col + 1])

            # Update if changed
            if newData != oldData:
                setattr(self.project.sessions[0].observations[row_index], self.columnMap[col + 1], newData)
                self.project.sessions[0].observations[row_index].update()

                # Update the display
                values = list(self.listControl.item(item, 'values'))
                values[col] = new_value
                self.listControl.item(item, values=values)

                self.edited = True
                self.badEdit = False
                self.setSaveButton()

        except ValueError as err:
            # Mark as error
            self.listControl.item(item, tags=('error',))
            self.statusbar.config(text=f"Error: {str(err)}")

            messagebox.showerror("Validation Error", str(err))
            self.badEdit = True
            pid_print(f"Error: {str(err)}")

    def onNew(self, event=None):
        """
        Create a new SDF session.
        """

        if self.edited:
            result = messagebox.askyesno('Confirm New',
                'The current session definition file has changes that have not been saved.\n\nStart a new session anyways?',
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
        self.initSDF()
        ObserverInfo(self)

    def onLoad(self, event=None):
        """
        Load an existing SDF file.
        """

        if self.edited:
            result = messagebox.askyesno('Confirm Open',
                'The current session definition file has changes that have not been saved.\n\nOpen a new file anyways?',
                icon=messagebox.WARNING, default=messagebox.NO)

            if not result:
                return False

        filename = filedialog.askopenfilename(
            title="Select an SDF File",
            initialdir=self.dirname,
            filetypes=[("SDF Files", "*.sdf *.txt"), ("All Files", "*.*")]
        )

        if filename:
            self.dirname = os.path.dirname(filename)
            self.filename = filename
            self.parseFile(filename)

            self.edited = False
            self.setSaveButton()

    def onSave(self, event=None):
        """
        Save the current session to a file.
        """

        if self.filename == '':
            self.onSaveAs(event)
        else:
            if not self.onValidate(confirmValid=False):
                self.displayError('The session definition file could not be saved due to errors in the file.',
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
        Save the current session to a new SDF file.
        """

        if not self.onValidate(confirmValid=False):
            self.displayError('The session definition file could not be saved due to errors in the file.',
                            title='Save Failed')
        else:
            filename = filedialog.asksaveasfilename(
                title="Select Output File",
                initialdir=self.dirname,
                filetypes=[("SDF Files", "*.sdf *.txt"), ("All Files", "*.*")],
                defaultextension=".sdf"
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
        Copy the selected observation(s) to the buffer.
        """

        self.buffer = []
        for i, child in enumerate(self.listControl.get_children()):
            if self.listControl.is_checked(child):
                self.buffer.append(copy.deepcopy(self.project.sessions[0].observations[i]))

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

                self.project.sessions[0].observations.insert(id, cObs)
                self.addObservation(self.project.sessions[0].observations[id], id)

            self.edited = True
            self.setSaveButton()

            # Re-number the remaining rows to keep the display clean
            for id, child in enumerate(self.listControl.get_children()):
                values = list(self.listControl.item(child, 'values'))
                values[0] = str(id + 1)
                self.listControl.item(child, values=values)

            # Fix the times on observations to make thing continuous
            for id in range(firstChecked + len(self.buffer) - 1, -1, -1):
                dur = self.project.sessions[0].observations[id].dur

                tStart, _ = self.sdf.get_observation_start_stop(self.project.sessions[0].observations[id + 1])
                tStart -= timedelta(seconds=dur // 1000, microseconds=(dur % 1000) * 1000)
                cStart = 'UTC %i %02i %02i %02i:%02i:%06.3f' % (tStart.year, tStart.month, tStart.day,
                                                                tStart.hour, tStart.minute,
                                                                tStart.second + tStart.microsecond / 1e6)
                self.project.sessions[0].observations[id].start = cStart
                self.addObservation(self.project.sessions[0].observations[id], id, update=True)

    def onPasteAfter(self, event=None):
        lastChecked = None

        for i, child in enumerate(self.listControl.get_children()):
            if self.listControl.is_checked(child):
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
            for id, child in enumerate(self.listControl.get_children()):
                values = list(self.listControl.item(child, 'values'))
                values[0] = str(id + 1)
                self.listControl.item(child, values=values)

    def onPasteEnd(self, event=None):
        if self.buffer is not None:
            for obs in self.buffer:
                cObs = copy.deepcopy(obs)

                self.project.sessions[0].observations.append(cObs)
                self.addObservation(cObs, len(self.project.sessions[0].observations) - 1)

            self.edited = True
            self.setSaveButton()

    def onInfo(self, event=None):
        """Show observer/project/session information dialog."""
        ObserverInfo(self)

    def onSchedule(self, event=None):
        """Show scheduling window."""
        ScheduleWindow(self)

    def onAddTBW(self, event=None):
        """Add TBW observation."""
        tStart = datetime.now()
        tStart += timedelta(days=1)

        # Create new observation
        obs = self.sdf.TBW('TBW', 'Target', tStart, '00:00:30', 7)

        self.project.sessions[0].observations.append(obs)
        self.addObservation(obs, len(self.project.sessions[0].observations) - 1)

        self.edited = True
        self.setSaveButton()

    def onAddTBN(self, event=None):
        """Add TBN observation."""
        tStart = datetime.now()
        tStart += timedelta(days=1)

        # Create new observation
        obs = self.sdf.TBN('TBN', 'Target', tStart, '00:00:30', 38000000, 7, gain=self.project.sessions[0].tbnGain)

        self.project.sessions[0].observations.append(obs)
        self.addObservation(obs, len(self.project.sessions[0].observations) - 1)

        self.edited = True
        self.setSaveButton()

    def onAddDRXR(self, event=None):
        """Add DRX RA/Dec observation."""
        tStart = datetime.now()
        tStart += timedelta(days=1)

        # Create new observation
        obs = self.sdf.DRX('Target', 'Target', tStart, '00:00:10',
                      0.0, 0.0, 38e6, 74e6, 7, gain=self.project.sessions[0].drxGain)

        self.project.sessions[0].observations.append(obs)
        self.addObservation(obs, len(self.project.sessions[0].observations) - 1)

        self.edited = True
        self.setSaveButton()

    def onAddDRXS(self, event=None):
        """Add DRX Solar observation."""
        tStart = datetime.now()
        tStart += timedelta(days=1)

        # Create new observation
        obs = self.sdf.Solar('Sun', 'Target', tStart, '00:00:10', 38e6, 74e6, 7, gain=self.project.sessions[0].drxGain)

        self.project.sessions[0].observations.append(obs)
        self.addObservation(obs, len(self.project.sessions[0].observations) - 1)

        self.edited = True
        self.setSaveButton()

    def onAddDRXJ(self, event=None):
        """Add DRX Jovian observation."""
        tStart = datetime.now()
        tStart += timedelta(days=1)

        # Create new observation
        obs = self.sdf.Jovian('Jupiter', 'Target', tStart, '00:00:10', 38e6, 74e6, 7, gain=self.project.sessions[0].drxGain)

        self.project.sessions[0].observations.append(obs)
        self.addObservation(obs, len(self.project.sessions[0].observations) - 1)

        self.edited = True
        self.setSaveButton()

    def onAddDRXL(self, event=None):
        """Add DRX Lunar observation."""
        tStart = datetime.now()
        tStart += timedelta(days=1)

        # Create new observation
        obs = self.sdf.Lunar('Moon', 'Target', tStart, '00:00:10', 38e6, 74e6, 7, gain=self.project.sessions[0].drxGain)

        self.project.sessions[0].observations.append(obs)
        self.addObservation(obs, len(self.project.sessions[0].observations) - 1)

        self.edited = True
        self.setSaveButton()

    def onAddSteppedR(self, event=None):
        """Add STEPPED RA/Dec observation."""
        tStart = datetime.now()
        tStart += timedelta(days=1)

        # Create new observation
        obs = self.sdf.Stepped('Target', 'Target', tStart, 7, is_radec=True, gain=self.project.sessions[0].drxGain)
        obs.steps = []

        self.project.sessions[0].observations.append(obs)
        self.addObservation(obs, len(self.project.sessions[0].observations) - 1)

        self.edited = True
        self.setSaveButton()

    def onAddSteppedA(self, event=None):
        """Add STEPPED Az/Alt observation."""
        tStart = datetime.now()
        tStart += timedelta(days=1)

        # Create new observation
        obs = self.sdf.Stepped('Target', 'Target', tStart, 7, is_radec=False, gain=self.project.sessions[0].drxGain)
        obs.steps = []

        self.project.sessions[0].observations.append(obs)
        self.addObservation(obs, len(self.project.sessions[0].observations) - 1)

        self.edited = True
        self.setSaveButton()

    def onEditStepped(self, event=None):
        """Edit STEPPED observation."""
        for i, child in enumerate(self.listControl.get_children()):
            if self.listControl.is_checked(child):
                if self.project.sessions[0].observations[i].mode == 'STEPPED':
                    SteppedWindow(self, i)
                break

    def onRemove(self, event=None):
        """Remove selected observations."""
        to_remove = []
        for i, child in enumerate(self.listControl.get_children()):
            if self.listControl.is_checked(child):
                to_remove.append((i, child))

        # Remove from list (in reverse order to maintain indices)
        for i, child in reversed(to_remove):
            del self.project.sessions[0].observations[i]
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
        """
        PATCH 4 (Enhanced): Validate all observations with color coding.
        """
        # Check for bad edits first
        if hasattr(self, 'badEdit') and self.badEdit:
            messagebox.showerror("Validation Error",
                               "Please fix the cell editing error before validating.")
            return False

        # Capture validation output
        old_stdout = sys.stdout
        old_stderr = sys.stderr
        sys.stdout = StringIO()
        sys.stderr = StringIO()

        try:
            # Validate each observation
            all_valid = True
            for i, obs in enumerate(self.project.sessions[0].observations):
                try:
                    # Validate the observation
                    is_valid = obs.validate(verbose=True)

                    # Get the treeview item
                    items = self.listControl.get_children()
                    if i < len(items):
                        item = items[i]

                        if is_valid:
                            # Color valid observations black
                            self.listControl.item(item, tags=())
                        else:
                            # Color invalid observations red
                            self.listControl.item(item, tags=('invalid',))
                            all_valid = False

                except Exception as e:
                    pid_print(f"Error validating observation {i+1}: {str(e)}")
                    items = self.listControl.get_children()
                    if i < len(items):
                        item = items[i]
                        self.listControl.item(item, tags=('invalid',))
                    all_valid = False

            # Validate the entire project
            try:
                project_valid = self.project.validate(verbose=True)
            except Exception as e:
                pid_print(f"Error validating project: {str(e)}")
                project_valid = False

            # Get validation output
            stdout_output = sys.stdout.getvalue()
            stderr_output = sys.stderr.getvalue()

            # Restore stdout/stderr
            sys.stdout = old_stdout
            sys.stderr = old_stderr

            # Print validation output
            if stdout_output:
                print(stdout_output)
            if stderr_output:
                print(stderr_output, file=sys.stderr)

            # Show results
            final_valid = all_valid and project_valid

            if confirmValid:
                if final_valid:
                    messagebox.showinfo("Validation Results",
                                      "All observations are valid!")
                else:
                    messagebox.showerror("Validation Errors",
                                       "Validation failed. Invalid observations are marked in red.\n\n" +
                                       "Check the console output for details.")

            return final_valid

        except Exception as e:
            # Restore stdout/stderr
            sys.stdout = old_stdout
            sys.stderr = old_stderr

            messagebox.showerror("Validation Error",
                               f"Error during validation:\n\n{str(e)}")
            return False

    def onResolve(self, event=None):
        """Show resolve target window."""
        ResolveTarget(self)

    def onTimeseries(self, event=None):
        """Show session at a glance window."""
        if len(self.project.sessions[0].observations) > 0:
            SessionDisplay(self)
        else:
            messagebox.showwarning("No Observations", "No observations defined yet!")

    def onAdvanced(self, event=None):
        """Show advanced settings window."""
        AdvancedInfo(self)

    def onVolume(self, event=None):
        """Show data volume estimation window."""
        VolumeInfo(self)

    def onHelp(self, event=None):
        """Show help window."""
        create_help_window(self)

    def onFilterInfo(self, event=None):
        """Show filter code information."""
        msg = "TBN Filter Codes:\n\n"
        for code, bw in TBNFilters.items():
            msg += f"  {code}: {bw/1e3:.0f} kHz\n"
        msg += "\nDRX Filter Codes:\n\n"
        for code, bw in DRXFilters.items():
            msg += f"  {code}: {bw/1e6:.1f} MHz\n"
        messagebox.showinfo("Filter Codes", msg)

    def onAbout(self, event=None):
        """Show about dialog."""
        msg = f"Session GUI v{__version__}\n\n"
        msg += f"LSL Version: {lsl.version.version}\n\n"
        msg += "A GUI for creating and editing LWA Session Definition Files"
        messagebox.showinfo("About Session GUI", msg)

    def onQuit(self, event=None):
        """Quit the application."""
        if self.edited:
            result = messagebox.askyesno('Confirm Quit',
                'The current session definition file has changes that have not been saved.\n\nQuit anyways?',
                icon=messagebox.WARNING, default=messagebox.NO)

            if not result:
                return False

        self.destroy()

    def setSaveButton(self):
        """Enable/disable the save button based on edit status."""
        if self.edited:
            self.savemenu.config(state=tk.NORMAL)
        else:
            self.savemenu.config(state=tk.DISABLED)

    def setMenuButtons(self, mode):
        """Enable/disable menu buttons based on observation mode."""
        self.mode = mode

        # Get the add menu
        obsMenu = self.nametowidget(self.cget('menu')).nametowidget('!menu2')
        addMenu = None
        for i in range(obsMenu.index('end') + 1):
            try:
                if obsMenu.type(i) == 'cascade':
                    addMenu = obsMenu.nametowidget(obsMenu.entrycget(i, 'menu'))
                    break
            except:
                pass

        if addMenu is None:
            return

        if mode == 'None':
            # Enable all
            for key in ['tbw', 'tbn', 'drx-radec', 'drx-solar', 'drx-jovian', 'drx-lunar', 'stepped-radec', 'stepped-azalt']:
                try:
                    menu, idx = self.obsmenu[key]
                    menu.entryconfig(idx, state=tk.NORMAL)
                    if key in self.toolbar_buttons:
                        self.toolbar_buttons[key].config(state=tk.NORMAL)
                except:
                    pass

        elif mode == 'TBW':
            # Disable TBN, DRX, STEPPED
            for key in ['tbn', 'drx-radec', 'drx-solar', 'drx-jovian', 'drx-lunar', 'stepped-radec', 'stepped-azalt']:
                try:
                    menu, idx = self.obsmenu[key]
                    menu.entryconfig(idx, state=tk.DISABLED)
                    if key in self.toolbar_buttons:
                        self.toolbar_buttons[key].config(state=tk.DISABLED)
                except:
                    pass
            # Enable TBW
            try:
                menu, idx = self.obsmenu['tbw']
                menu.entryconfig(idx, state=tk.NORMAL)
                self.toolbar_buttons['tbw'].config(state=tk.NORMAL)
            except:
                pass

        elif mode in ['TBN', 'DRX', 'STEPPED']:
            # Disable TBW
            try:
                menu, idx = self.obsmenu['tbw']
                menu.entryconfig(idx, state=tk.DISABLED)
                self.toolbar_buttons['tbw'].config(state=tk.DISABLED)
            except:
                pass
            # Enable TBN, DRX, STEPPED
            for key in ['tbn', 'drx-radec', 'drx-solar', 'drx-jovian', 'drx-lunar', 'stepped-radec', 'stepped-azalt']:
                try:
                    menu, idx = self.obsmenu[key]
                    menu.entryconfig(idx, state=tk.NORMAL)
                    if key in self.toolbar_buttons:
                        self.toolbar_buttons[key].config(state=tk.NORMAL)
                except:
                    pass

    def parseFile(self, filename):
        """Parse an SDF file and populate the GUI."""
        try:
            # Parse the file
            project = self.sdf.parse_sdf(filename)
            self.project = project

            # Clear the list
            for item in self.listControl.get_children():
                self.listControl.delete(item)

            # Populate observations
            if len(project.sessions) > 0:
                for i, obs in enumerate(project.sessions[0].observations):
                    self.addObservation(obs, i)

                # Set mode based on first observation
                if len(project.sessions[0].observations) > 0:
                    mode = project.sessions[0].observations[0].mode
                    if mode in ['TRK_RADEC', 'TRK_SOL', 'TRK_JOV', 'TRK_LUN']:
                        mode = 'DRX'
                    self.setMenuButtons(mode)
                else:
                    self.setMenuButtons('None')

            self.statusbar.config(text=f"Loaded: {filename}")

        except Exception as e:
            self.displayError(f"Error loading file '{filename}'", details=str(e), title='Load Error')

    def addObservation(self, obs, index, update=False):
        """Add or update an observation in the list."""
        # Determine the mode
        mode = obs.mode
        if mode in ['TRK_RADEC', 'TRK_SOL', 'TRK_JOV', 'TRK_LUN']:
            mode_display = 'DRX'
        else:
            mode_display = mode

        # Get start time
        tStart, _ = self.sdf.get_observation_start_stop(obs)
        start_str = tStart.strftime('%Y/%m/%d %H:%M:%S')

        # Get duration
        dur_sec = obs.dur / 1000.0
        dur_str = f"{int(dur_sec//3600):02d}:{int((dur_sec%3600)//60):02d}:{dur_sec%60:06.3f}"

        # Build the row based on mode
        if mode == 'TBW':
            # TBW: ID, Target, RA, Dec, Start, Duration, Filter, Comments, Alt1, Alt2
            values = [
                str(index + 1),
                obs.target,
                '',
                '',
                start_str,
                dur_str,
                str(obs.filter),
                obs.comments if hasattr(obs, 'comments') else '',
                obs.alt1 if hasattr(obs, 'alt1') else '',
                obs.alt2 if hasattr(obs, 'alt2') else ''
            ]
            columns = ['ID', 'Target', 'RA', 'Dec', 'Start', 'Duration', 'Filter', 'Comments', 'Alt 1', 'Alt 2']

        elif mode == 'TBN':
            # TBN: ID, Target, RA, Dec, Start, Duration, Freq, Filter, Max SNR, Comments, Alt1, Alt2
            freq_mhz = obs.freq * fS / 2**32 / 1e6
            values = [
                str(index + 1),
                obs.target,
                '',
                '',
                start_str,
                dur_str,
                f"{freq_mhz:.3f}",
                str(obs.filter),
                'Yes' if obs.max_snr else 'No',
                obs.comments if hasattr(obs, 'comments') else '',
                obs.alt1 if hasattr(obs, 'alt1') else '',
                obs.alt2 if hasattr(obs, 'alt2') else ''
            ]
            columns = ['ID', 'Target', 'RA', 'Dec', 'Start', 'Duration', 'Freq (MHz)', 'Filter', 'Max SNR', 'Comments', 'Alt 1', 'Alt 2']

        elif mode in ['TRK_RADEC', 'TRK_SOL', 'TRK_JOV', 'TRK_LUN']:
            # DRX: ID, Target, RA, Dec, Start, Duration, Freq1, Freq2, Filter, Max SNR, Comments, Alt1, Alt2
            if mode == 'TRK_RADEC':
                ra_str = str(deg_to_hms(obs.ra)).replace(' ', ':')
                dec_str = str(deg_to_dms(obs.dec)).replace(' ', ':')
            else:
                ra_str = ''
                dec_str = ''

            freq1_mhz = obs.freq1 * fS / 2**32 / 1e6
            freq2_mhz = obs.freq2 * fS / 2**32 / 1e6

            values = [
                str(index + 1),
                obs.target,
                ra_str,
                dec_str,
                start_str,
                dur_str,
                f"{freq1_mhz:.3f}",
                f"{freq2_mhz:.3f}",
                str(obs.filter),
                'Yes' if obs.max_snr else 'No',
                obs.comments if hasattr(obs, 'comments') else '',
                obs.alt1 if hasattr(obs, 'alt1') else '',
                obs.alt2 if hasattr(obs, 'alt2') else ''
            ]
            columns = ['ID', 'Target', 'RA', 'Dec', 'Start', 'Duration', 'Freq 1 (MHz)', 'Freq 2 (MHz)', 'Filter', 'Max SNR', 'Comments', 'Alt 1', 'Alt 2']

        elif mode == 'STEPPED':
            # STEPPED: ID, Target, Comments, Start, Duration, Steps, C1?, RA/Dec?, Alt1, Alt2
            values = [
                str(index + 1),
                obs.target,
                obs.comments if hasattr(obs, 'comments') else '',
                start_str,
                dur_str,
                str(len(obs.steps)) if hasattr(obs, 'steps') else '0',
                'Yes' if obs.is_c1 else 'No',
                'Yes' if obs.is_radec else 'No',
                obs.alt1 if hasattr(obs, 'alt1') else '',
                obs.alt2 if hasattr(obs, 'alt2') else ''
            ]
            columns = ['ID', 'Target', 'Comments', 'Start', 'Duration', 'Steps', 'C1?', 'RA/Dec?', 'Alt 1', 'Alt 2']

        else:
            # Unknown mode
            values = [str(index + 1), obs.target, mode, '', '', '', '', '', '', '']
            columns = ['ID', 'Target', 'Mode', '', '', '', '', '', '', '']

        # Configure columns if needed
        if update or len(self.listControl['columns']) != len(columns):
            self.listControl['columns'] = columns
            self.listControl.heading('#0', text='')
            self.listControl.column('#0', width=30)
            for col in columns:
                self.listControl.heading(col, text=col)
                if col == 'ID':
                    self.listControl.column(col, width=50)
                elif col in ['Target', 'Comments']:
                    self.listControl.column(col, width=150)
                else:
                    self.listControl.column(col, width=100)

            # Set editable columns and column map
            self.setupColumnMapping(mode)

        # Add or update the item
        if update:
            items = self.listControl.get_children()
            if index < len(items):
                self.listControl.item(items[index], values=values)
        else:
            self.listControl.insert('', tk.END, values=values)

    def setupColumnMapping(self, mode):
        """Setup column mapping for editing."""
        # Define conversion functions
        def raConv(text):
            """Special conversion function for deal with RA values."""
            fields = text.split(':')
            fields = [float(f) for f in fields]
            sign = 1
            if fields[0] < 0:
                sign = -1
            fields[0] = abs(fields[0])

            value = 0
            for f, d in zip(fields, [1.0, 60.0, 3600.0]):
                value += (f / d)
            value *= sign

            return value * 15.0

        def decConv(text):
            """Special conversion function for dealing with dec. values."""
            fields = text.split(':')
            fields = [float(f) for f in fields]
            sign = 1
            if fields[0] < 0:
                sign = -1
            fields[0] = abs(fields[0])

            value = 0
            for f, d in zip(fields, [1.0, 60.0, 3600.0]):
                value += (f / d)
            value *= sign

            return value

        def freqConv(text, tbn=False):
            """Special conversion function for dealing with frequencies."""
            lowerLimit = 219130984
            upperLimit = 1928352663
            if tbn:
                lowerLimit = 109565492
                upperLimit = 2037918156

            value = float(text) * 1e6
            freq = int(round(value * 2**32 / fS))
            if freq < lowerLimit or freq > upperLimit:
                if self.ndp:
                    dpn = 'NDP'
                elif self.adp:
                    dpn = 'ADP'
                else:
                    dpn = 'DP'
                raise ValueError(f"Frequency of {value/1e6:.3f} MHz is outside the {dpn} tuning range")
            else:
                return freq

        def filterConv(text):
            """Special conversion function for dealing with filter codes."""
            value = int(text)
            if value < 1 or value > 7:
                raise ValueError("Filter code must be an integer between 1 and 7")
            else:
                return value

        def snrConv(text):
            """Special conversion function for dealing with the max_snr keyword input."""
            text = text.lower().capitalize()
            if text == 'True' or text == 'Yes':
                return True
            elif text == 'False' or text == 'No':
                return False
            else:
                raise ValueError(f"Unknown boolean conversion of '{text}'")

        # Set up mapping based on mode
        if mode == 'TBW':
            self.columnMap = [None, 'target', None, None, 'start', 'dur', 'filter', 'comments', 'alt1', 'alt2']
            self.coerceMap = [None, str, None, None, str, str, filterConv, str, str, str]
            self.listControl.editable_columns = [False, True, False, False, True, True, True, True, True, True]

        elif mode == 'TBN':
            self.columnMap = [None, 'target', None, None, 'start', 'dur', 'freq', 'filter', 'max_snr', 'comments', 'alt1', 'alt2']
            self.coerceMap = [None, str, None, None, str, str, lambda x: freqConv(x, tbn=True), filterConv, snrConv, str, str, str]
            self.listControl.editable_columns = [False, True, False, False, True, True, True, True, True, True, True, True]
            self.listControl.column_options = {7: ['Yes', 'No']}

        elif mode in ['TRK_RADEC', 'TRK_SOL', 'TRK_JOV', 'TRK_LUN', 'DRX']:
            self.columnMap = [None, 'target', 'ra', 'dec', 'start', 'dur', 'freq1', 'freq2', 'filter', 'max_snr', 'comments', 'alt1', 'alt2']
            self.coerceMap = [None, str, raConv, decConv, str, str, freqConv, freqConv, filterConv, snrConv, str, str, str]
            if mode == 'TRK_RADEC':
                self.listControl.editable_columns = [False, True, True, True, True, True, True, True, True, True, True, True, True]
            else:
                self.listControl.editable_columns = [False, True, False, False, True, True, True, True, True, True, True, True, True]
            self.listControl.column_options = {8: ['Yes', 'No']}

        elif mode == 'STEPPED':
            self.columnMap = [None, 'target', 'comments', 'start', 'dur', None, None, None, 'alt1', 'alt2']
            self.coerceMap = [None, str, str, str, str, None, None, None, str, str]
            self.listControl.editable_columns = [False, True, True, True, True, False, False, False, True, True]

        else:
            self.columnMap = []
            self.coerceMap = []
            self.listControl.editable_columns = []

    def displayError(self, message, details=None, title='Error'):
        """Display an error message."""
        if details:
            msg = f"{message}\n\n{details}"
        else:
            msg = message
        messagebox.showerror(title, msg)


class ObserverInfo(tk.Toplevel):
    """Dialog for editing observer/project/session information."""

    def __init__(self, parent):
        tk.Toplevel.__init__(self, parent)
        self.title("Observer/Project/Session Information")
        self.parent = parent

        self.create_widgets()
        self.load_data()

        # Make it modal
        self.transient(parent)
        self.grab_set()

    def create_widgets(self):
        """Create the dialog widgets."""
        # Main frame
        main_frame = ttk.Frame(self, padding=10)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Observer section
        observer_frame = ttk.LabelFrame(main_frame, text="Observer Information", padding=10)
        observer_frame.pack(fill=tk.X, padx=5, pady=5)

        ttk.Label(observer_frame, text="First Name:").grid(row=0, column=0, sticky=tk.W, pady=2)
        self.first_name = ttk.Entry(observer_frame, width=40)
        self.first_name.grid(row=0, column=1, sticky=tk.EW, pady=2)

        ttk.Label(observer_frame, text="Last Name:").grid(row=1, column=0, sticky=tk.W, pady=2)
        self.last_name = ttk.Entry(observer_frame, width=40)
        self.last_name.grid(row=1, column=1, sticky=tk.EW, pady=2)

        ttk.Label(observer_frame, text="Observer ID:").grid(row=2, column=0, sticky=tk.W, pady=2)
        self.observer_id = ttk.Entry(observer_frame, width=40)
        self.observer_id.grid(row=2, column=1, sticky=tk.EW, pady=2)

        observer_frame.columnconfigure(1, weight=1)

        # Project section
        project_frame = ttk.LabelFrame(main_frame, text="Project Information", padding=10)
        project_frame.pack(fill=tk.X, padx=5, pady=5)

        ttk.Label(project_frame, text="Project ID:").grid(row=0, column=0, sticky=tk.W, pady=2)
        self.project_id = ttk.Entry(project_frame, width=40)
        self.project_id.grid(row=0, column=1, sticky=tk.EW, pady=2)

        ttk.Label(project_frame, text="Project Title:").grid(row=1, column=0, sticky=tk.W, pady=2)
        self.project_title = ttk.Entry(project_frame, width=40)
        self.project_title.grid(row=1, column=1, sticky=tk.EW, pady=2)

        project_frame.columnconfigure(1, weight=1)

        # Session section
        session_frame = ttk.LabelFrame(main_frame, text="Session Information", padding=10)
        session_frame.pack(fill=tk.X, padx=5, pady=5)

        ttk.Label(session_frame, text="Session ID:").grid(row=0, column=0, sticky=tk.W, pady=2)
        self.session_id = ttk.Entry(session_frame, width=40)
        self.session_id.grid(row=0, column=1, sticky=tk.EW, pady=2)

        ttk.Label(session_frame, text="Session Title:").grid(row=1, column=0, sticky=tk.W, pady=2)
        self.session_title = ttk.Entry(session_frame, width=40)
        self.session_title.grid(row=1, column=1, sticky=tk.EW, pady=2)

        session_frame.columnconfigure(1, weight=1)

        # Buttons
        button_frame = ttk.Frame(main_frame)
        button_frame.pack(fill=tk.X, pady=10)

        ttk.Button(button_frame, text="OK", command=self.on_ok).pack(side=tk.RIGHT, padx=5)
        ttk.Button(button_frame, text="Cancel", command=self.on_cancel).pack(side=tk.RIGHT, padx=5)

    def load_data(self):
        """Load data from the parent project."""
        project = self.parent.project

        self.first_name.insert(0, project.observer.first)
        self.last_name.insert(0, project.observer.last)
        self.observer_id.insert(0, str(project.observer.id))

        self.project_id.insert(0, project.id)
        self.project_title.insert(0, project.name)

        if len(project.sessions) > 0:
            self.session_id.insert(0, str(project.sessions[0].id))
            self.session_title.insert(0, project.sessions[0].name)

    def on_ok(self):
        """Save the data and close."""
        try:
            project = self.parent.project

            project.observer.first = self.first_name.get()
            project.observer.last = self.last_name.get()
            project.observer.id = int(self.observer_id.get())

            project.id = self.project_id.get()
            project.name = self.project_title.get()

            if len(project.sessions) > 0:
                project.sessions[0].id = int(self.session_id.get())
                project.sessions[0].name = self.session_title.get()

            self.parent.edited = True
            self.parent.setSaveButton()

            self.destroy()

        except ValueError as e:
            messagebox.showerror("Invalid Input", f"Invalid value:\n\n{str(e)}")

    def on_cancel(self):
        """Close without saving."""
        self.destroy()


class AdvancedInfo(tk.Toplevel):
    """Dialog for advanced settings."""

    def __init__(self, parent):
        tk.Toplevel.__init__(self, parent)
        self.title("Advanced Settings")
        self.parent = parent

        self.create_widgets()
        self.load_data()

        # Make it modal
        self.transient(parent)
        self.grab_set()

    def create_widgets(self):
        """Create the dialog widgets."""
        main_frame = ttk.Frame(self, padding=10)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # DRX Gain
        ttk.Label(main_frame, text="DRX Gain:").grid(row=0, column=0, sticky=tk.W, pady=5)
        self.drx_gain = ttk.Entry(main_frame, width=20)
        self.drx_gain.grid(row=0, column=1, sticky=tk.W, pady=5)
        ttk.Label(main_frame, text="dB (or -1 for default)").grid(row=0, column=2, sticky=tk.W, pady=5)

        # Buttons
        button_frame = ttk.Frame(main_frame)
        button_frame.grid(row=10, column=0, columnspan=3, pady=10)

        ttk.Button(button_frame, text="OK", command=self.on_ok).pack(side=tk.RIGHT, padx=5)
        ttk.Button(button_frame, text="Cancel", command=self.on_cancel).pack(side=tk.RIGHT, padx=5)

    def load_data(self):
        """Load data from the parent project."""
        if len(self.parent.project.sessions) > 0:
            gain = self.parent.project.sessions[0].drxGain
            self.drx_gain.insert(0, str(gain))

    def on_ok(self):
        """Save the data and close."""
        try:
            gain = int(self.drx_gain.get())

            if len(self.parent.project.sessions) > 0:
                self.parent.project.sessions[0].drxGain = gain

            self.parent.edited = True
            self.parent.setSaveButton()

            self.destroy()

        except ValueError as e:
            messagebox.showerror("Invalid Input", f"Invalid value:\n\n{str(e)}")

    def on_cancel(self):
        """Close without saving."""
        self.destroy()


class SessionDisplay(tk.Toplevel):
    """Window to display session timeline visualization."""

    def __init__(self, parent):
        tk.Toplevel.__init__(self, parent)
        self.title("Session at a Glance")
        self.parent = parent
        self.geometry("800x600")

        self.create_widgets()
        self.plot_session()

    def create_widgets(self):
        """Create the window widgets."""
        # Create plot panel
        self.fig = Figure(figsize=(8, 6))
        self.plot_panel = PlotPanel(self, fig=self.fig)
        self.plot_panel.pack(fill=tk.BOTH, expand=True)

        # Toolbar
        toolbar = NavigationToolbar2Tk(self.plot_panel.get_canvas(), self)
        toolbar.update()

        # Close button
        button_frame = ttk.Frame(self)
        button_frame.pack(fill=tk.X, pady=5)
        ttk.Button(button_frame, text="Close", command=self.destroy).pack(side=tk.RIGHT, padx=5)

    def plot_session(self):
        """Plot the session timeline."""
        ax = self.fig.add_subplot(111)

        observations = self.parent.project.sessions[0].observations
        if len(observations) == 0:
            return

        # Plot each observation
        y_pos = 0
        labels = []
        colors = {'TBW': 'blue', 'TBN': 'green', 'TRK_RADEC': 'red', 'TRK_SOL': 'orange',
                  'TRK_JOV': 'purple', 'TRK_LUN': 'brown', 'STEPPED': 'cyan'}

        for i, obs in enumerate(observations):
            tStart, tStop = self.parent.sdf.get_observation_start_stop(obs)
            duration = (tStop - tStart).total_seconds() / 3600.0  # hours

            color = colors.get(obs.mode, 'gray')
            ax.barh(y_pos, duration, left=(tStart - observations[0].start).total_seconds() / 3600.0,
                   height=0.8, color=color, alpha=0.7)

            labels.append(f"{i+1}: {obs.target}")
            y_pos += 1

        ax.set_yticks(range(len(observations)))
        ax.set_yticklabels(labels)
        ax.set_xlabel('Time (hours from start)')
        ax.set_title('Session Timeline')
        ax.grid(True, alpha=0.3)

        self.fig.tight_layout()
        self.plot_panel.get_canvas().draw()


class VolumeInfo(tk.Toplevel):
    """Dialog to show estimated data volume."""

    def __init__(self, parent):
        tk.Toplevel.__init__(self, parent)
        self.title("Estimated Data Volume")
        self.parent = parent

        self.create_widgets()
        self.calculate_volume()

        # Make it modal
        self.transient(parent)
        self.grab_set()

    def create_widgets(self):
        """Create the dialog widgets."""
        main_frame = ttk.Frame(self, padding=10)
        main_frame.pack(fill=tk.BOTH, expand=True)

        self.text = tk.Text(main_frame, width=60, height=20, wrap=tk.WORD)
        self.text.pack(fill=tk.BOTH, expand=True)

        scrollbar = ttk.Scrollbar(main_frame, command=self.text.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.text.config(yscrollcommand=scrollbar.set)

        # Close button
        button_frame = ttk.Frame(main_frame)
        button_frame.pack(fill=tk.X, pady=10)
        ttk.Button(button_frame, text="Close", command=self.destroy).pack(side=tk.RIGHT)

    def calculate_volume(self):
        """Calculate and display data volume estimates."""
        self.text.delete('1.0', tk.END)

        observations = self.parent.project.sessions[0].observations
        total_volume = 0.0

        self.text.insert(tk.END, "Data Volume Estimates:\n\n")

        for i, obs in enumerate(observations):
            dur_sec = obs.dur / 1000.0

            if obs.mode == 'TBW':
                # TBW: ~196 GB per 30 seconds
                volume_gb = (dur_sec / 30.0) * 196.0
            elif obs.mode == 'TBN':
                # TBN: varies by filter, approximate
                volume_gb = dur_sec * 0.5  # Rough estimate
            elif obs.mode in ['TRK_RADEC', 'TRK_SOL', 'TRK_JOV', 'TRK_LUN']:
                # DRX: ~26 MB/s for 2 tunings
                volume_gb = dur_sec * 26.0 / 1024.0
            elif obs.mode == 'STEPPED':
                # STEPPED: similar to DRX
                volume_gb = dur_sec * 26.0 / 1024.0
            else:
                volume_gb = 0.0

            total_volume += volume_gb
            self.text.insert(tk.END, f"Observation {i+1} ({obs.target}): {volume_gb:.2f} GB\n")

        self.text.insert(tk.END, f"\nTotal Estimated Volume: {total_volume:.2f} GB\n")


class ResolveTarget(tk.Toplevel):
    """Dialog to resolve target names to coordinates."""

    def __init__(self, parent):
        tk.Toplevel.__init__(self, parent)
        self.title("Resolve Target")
        self.parent = parent

        self.create_widgets()

        # Make it modal
        self.transient(parent)
        self.grab_set()

    def create_widgets(self):
        """Create the dialog widgets."""
        main_frame = ttk.Frame(self, padding=10)
        main_frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(main_frame, text="Target Name:").grid(row=0, column=0, sticky=tk.W, pady=5)
        self.target_name = ttk.Entry(main_frame, width=40)
        self.target_name.grid(row=0, column=1, sticky=tk.EW, pady=5)

        # Buttons
        button_frame = ttk.Frame(main_frame)
        button_frame.grid(row=10, column=0, columnspan=2, pady=10)

        ttk.Button(button_frame, text="Resolve", command=self.on_resolve).pack(side=tk.LEFT, padx=5)
        ttk.Button(button_frame, text="Apply", command=self.on_apply, state=tk.DISABLED).pack(side=tk.LEFT, padx=5)
        ttk.Button(button_frame, text="Cancel", command=self.on_cancel).pack(side=tk.LEFT, padx=5)

        self.apply_button = button_frame.winfo_children()[1]

        # Results
        self.results = tk.Text(main_frame, width=60, height=10, wrap=tk.WORD)
        self.results.grid(row=11, column=0, columnspan=2, pady=10)
        self.results.config(state=tk.DISABLED)

        main_frame.columnconfigure(1, weight=1)

        self.resolved_coords = None

    def on_resolve(self):
        """Resolve the target name."""
        target = self.target_name.get().strip()
        if not target:
            messagebox.showerror("Error", "Please enter a target name")
            return

        try:
            # Try to resolve using ephem
            obj = ephem.FixedBody()

            # Try common objects first
            if target.lower() in ['sun']:
                obj = ephem.Sun()
            elif target.lower() in ['moon']:
                obj = ephem.Moon()
            elif target.lower() in ['jupiter']:
                obj = ephem.Jupiter()
            else:
                # Try as a catalog object (requires name lookup service)
                self.results.config(state=tk.NORMAL)
                self.results.delete('1.0', tk.END)
                self.results.insert(tk.END, "Online name resolution not implemented.\n")
                self.results.insert(tk.END, "Please enter coordinates manually.")
                self.results.config(state=tk.DISABLED)
                return

            obj.compute()

            ra_deg = math.degrees(float(obj.ra)) / 15.0  # Convert to hours
            dec_deg = math.degrees(float(obj.dec))

            self.resolved_coords = (ra_deg * 15.0, dec_deg)

            self.results.config(state=tk.NORMAL)
            self.results.delete('1.0', tk.END)
            self.results.insert(tk.END, f"Resolved '{target}':\n\n")
            self.results.insert(tk.END, f"RA:  {deg_to_hms(ra_deg * 15.0)}\n")
            self.results.insert(tk.END, f"Dec: {deg_to_dms(dec_deg)}\n")
            self.results.config(state=tk.DISABLED)

            self.apply_button.config(state=tk.NORMAL)

        except Exception as e:
            messagebox.showerror("Resolution Error", f"Could not resolve '{target}':\n\n{str(e)}")

    def on_apply(self):
        """Apply the resolved coordinates to the selected observation."""
        if self.resolved_coords is None:
            return

        # Find selected observation
        for i, child in enumerate(self.parent.listControl.get_children()):
            if self.parent.listControl.is_checked(child):
                obs = self.parent.project.sessions[0].observations[i]
                if obs.mode == 'TRK_RADEC':
                    obs.ra = self.resolved_coords[0]
                    obs.dec = self.resolved_coords[1]
                    obs.update()

                    # Update display
                    self.parent.addObservation(obs, i, update=True)
                    self.parent.edited = True
                    self.parent.setSaveButton()

                    messagebox.showinfo("Success", "Coordinates applied to selected observation")
                    self.destroy()
                    return
                else:
                    messagebox.showerror("Error", "Selected observation is not a DRX RA/Dec observation")
                    return

        messagebox.showerror("Error", "No observation selected")

    def on_cancel(self):
        """Close without applying."""
        self.destroy()


class ScheduleWindow(tk.Toplevel):
    """Dialog for scheduling settings."""

    def __init__(self, parent):
        tk.Toplevel.__init__(self, parent)
        self.title("Scheduling")
        self.parent = parent

        self.create_widgets()
        self.load_data()

        # Make it modal
        self.transient(parent)
        self.grab_set()

    def create_widgets(self):
        """Create the dialog widgets."""
        main_frame = ttk.Frame(self, padding=10)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # UCF Username
        ttk.Label(main_frame, text="UCF Username:").grid(row=0, column=0, sticky=tk.W, pady=5)
        self.ucf_username = ttk.Entry(main_frame, width=40)
        self.ucf_username.grid(row=0, column=1, sticky=tk.EW, pady=5)

        # Buttons
        button_frame = ttk.Frame(main_frame)
        button_frame.grid(row=10, column=0, columnspan=2, pady=10)

        ttk.Button(button_frame, text="OK", command=self.on_ok).pack(side=tk.RIGHT, padx=5)
        ttk.Button(button_frame, text="Cancel", command=self.on_cancel).pack(side=tk.RIGHT, padx=5)

        main_frame.columnconfigure(1, weight=1)

    def load_data(self):
        """Load data from the parent project."""
        if hasattr(self.parent.project, 'ucf_username'):
            self.ucf_username.insert(0, self.parent.project.ucf_username)

    def on_ok(self):
        """Save the data and close."""
        self.parent.project.ucf_username = self.ucf_username.get()
        self.parent.edited = True
        self.parent.setSaveButton()
        self.destroy()

    def on_cancel(self):
        """Close without saving."""
        self.destroy()


class SteppedWindow(tk.Toplevel):
    """Window to edit stepped observation steps."""

    def __init__(self, parent, obs_index):
        tk.Toplevel.__init__(self, parent)
        self.title("Edit Stepped Observation")
        self.parent = parent
        self.obs_index = obs_index
        self.geometry("800x600")

        self.create_widgets()
        self.load_data()

    def create_widgets(self):
        """Create the window widgets."""
        main_frame = ttk.Frame(self, padding=10)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Stepped list control (PATCH 7: Use SteppedTreeview directly, no import)
        self.listControl = SteppedTreeview(main_frame)
        self.listControl.pack(fill=tk.BOTH, expand=True)
        self.listControl.parent = self

        # Setup columns for stepped observation
        columns = ['Step', 'C1 Freq (MHz)', 'C2 Freq (MHz)', 'Duration (s)', 'RA/Az', 'Dec/Alt', 'Is_C1?']
        self.listControl['columns'] = columns
        self.listControl.heading('#0', text='')
        self.listControl.column('#0', width=30)
        for col in columns:
            self.listControl.heading(col, text=col)
            self.listControl.column(col, width=100)

        # Buttons
        button_frame = ttk.Frame(main_frame)
        button_frame.pack(fill=tk.X, pady=5)

        ttk.Button(button_frame, text="Add Step", command=self.on_add_step).pack(side=tk.LEFT, padx=5)
        ttk.Button(button_frame, text="Remove Selected", command=self.on_remove).pack(side=tk.LEFT, padx=5)
        ttk.Button(button_frame, text="Done", command=self.on_done).pack(side=tk.RIGHT, padx=5)

    def load_data(self):
        """Load steps from the observation."""
        obs = self.parent.project.sessions[0].observations[self.obs_index]

        if hasattr(obs, 'steps'):
            for i, step in enumerate(obs.steps):
                values = [
                    str(i + 1),
                    f"{step.freq1 * fS / 2**32 / 1e6:.3f}",
                    f"{step.freq2 * fS / 2**32 / 1e6:.3f}",
                    f"{step.dur / 1000.0:.3f}",
                    f"{step.c1:.6f}",
                    f"{step.c2:.6f}",
                    'Yes' if step.is_c1 else 'No'
                ]
                self.listControl.insert('', tk.END, values=values)

    def on_add_step(self):
        """Add a new step."""
        # Add a default step
        step_num = len(self.listControl.get_children()) + 1
        values = [str(step_num), '38.0', '74.0', '10.0', '0.0', '0.0', 'Yes']
        self.listControl.insert('', tk.END, values=values)

    def on_remove(self):
        """Remove selected steps."""
        to_remove = []
        for child in self.listControl.get_children():
            if self.listControl.is_checked(child):
                to_remove.append(child)

        for child in to_remove:
            self.listControl.delete(child)

        # Re-number
        for i, child in enumerate(self.listControl.get_children()):
            values = list(self.listControl.item(child, 'values'))
            values[0] = str(i + 1)
            self.listControl.item(child, values=values)

    def on_edit(self, item, col, new_value):
        """Handle cell editing."""
        # Simple update for now
        values = list(self.listControl.item(item, 'values'))
        if 0 <= col < len(values):
            values[col] = new_value
            self.listControl.item(item, values=values)

    def on_done(self):
        """Save the steps and close."""
        obs = self.parent.project.sessions[0].observations[self.obs_index]

        # Clear existing steps
        obs.steps = []

        # Add steps from the list
        for child in self.listControl.get_children():
            values = self.listControl.item(child, 'values')
            try:
                step = self.parent.sdf.BeamStep()
                step.freq1 = int(round(float(values[1]) * 1e6 * 2**32 / fS))
                step.freq2 = int(round(float(values[2]) * 1e6 * 2**32 / fS))
                step.dur = int(float(values[3]) * 1000)
                step.c1 = float(values[4])
                step.c2 = float(values[5])
                step.is_c1 = (values[6].lower() == 'yes')

                obs.steps.append(step)
            except Exception as e:
                messagebox.showerror("Error", f"Error parsing step:\n\n{str(e)}")
                return

        # Update the parent display
        self.parent.addObservation(obs, self.obs_index, update=True)
        self.parent.edited = True
        self.parent.setSaveButton()

        self.destroy()


# PATCH 6: HelpWindow wrapper function
def create_help_window(parent):
    """
    Create and display the help window.
    This is a wrapper function to maintain compatibility.
    """
    help_file = os.path.join(parent.scriptPath, 'docs', 'help.html')

    if os.path.exists(help_file):
        # Try to open in web browser
        try:
            webbrowser.open(f'file://{help_file}')
        except:
            messagebox.showinfo("Help",
                              f"Help file located at:\n{help_file}\n\n" +
                              "Please open this file in your web browser.")
    else:
        # Fallback if help file doesn't exist
        messagebox.showinfo("Help",
                          f"Help file not found: {help_file}\n\n" +
                          "Please check the documentation online at:\n" +
                          "http://lwa.unm.edu")


def main(args):
    """Main function to run the application."""
    app = SDFCreator("Session GUI", args)
    app.mainloop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description='GUI for creating and editing LWA Session Definition Files',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument('filename', type=str, nargs='?', default=None,
                        help='SDF file to load')
    parser.add_argument('-s', '--lwasv', action='store_true',
                        help='use LWA-SV instead of LWA1')
    parser.add_argument('-n', '--lwana', action='store_true',
                        help='use LWA-NA instead of LWA1')
    parser.add_argument('-d', '--drsu-size', type=float, default=116.0,
                        help='DRSU size in TB')
    args = parser.parse_args()
    main(args)
