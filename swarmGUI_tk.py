#!/usr/bin/env python3

import os
import re
import sys
import copy
import math
import ephem
import numpy
import argparse
import webbrowser
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, font as tkfont
from io import StringIO
from datetime import datetime, timedelta
from xml.etree import ElementTree
from html.parser import HTMLParser

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

from calibratorSearch_tk import CalibratorSearch as OCS

__version__ = "0.2"
__author__ = "Jayce Dowell"


def pid_print(*args, **kwds):
    print(f"[{os.getpid()}]", *args, **kwds)


def dec2sexstr(value, signed=True):
    """Convert decimal degrees/hours to sexagesimal string.

    Args:
        value: Decimal value (degrees or hours)
        signed: If True, include sign prefix for declination format
                If False, use unsigned format for RA

    Returns:
        Formatted string in DD:MM:SS.S or HH:MM:SS.SS format
    """
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


class ScanListCtrl(ttk.Treeview):
    """
    Custom Treeview widget that supports checkboxes and inline editing.
    Replaces wx.ListCtrl with TextEditMixin, ChoiceMixIn, and CheckListCtrlMixin.
    """

    def __init__(self, parent, **kwargs):
        # Define columns for scan list (matches original swarmGUI column order)
        self.columns = ('id', 'target', 'intent', 'comments', 'start', 'duration',
                       'ra', 'dec', 'freq1', 'freq2', 'filter')

        super().__init__(parent, columns=self.columns, show='headings', selectmode='extended', **kwargs)

        self.parent = parent
        self.nSelected = 0
        self._check_states = {}  # Track checkbox states by item id

        # Configure choice options for certain columns (0-indexed)
        # Column 2 = Intent, Column 10 = Filter (in new column order)
        self.choice_options = {2: ['FluxCal', 'PhaseCal', 'Target'], 10: ['1', '2', '3', '4', '5', '6', '7']}

        # Column mapping for attribute access (matches column order)
        self.columnMap = ['id', 'target', 'intent', 'comments', 'start', 'duration',
                          'ra', 'dec', 'frequency1', 'frequency2', 'filter']

        # Setup columns
        self._setup_columns()

        # Bind events for editing
        self.bind('<Double-1>', self._on_double_click)
        self.bind('<<TreeviewSelect>>', self._on_selection_change)

        # For inline editing
        self._edit_entry = None
        self._edit_item = None
        self._edit_column = None

    def _setup_columns(self):
        """Setup column headings and widths."""
        headings = {
            'id': ('ID', 50),
            'target': ('Target', 100),
            'intent': ('Intent', 100),
            'comments': ('Comments', 100),
            'start': ('Start (UTC)', 200),
            'duration': ('Duration', 125),
            'ra': ('RA (Hour J2000)', 120),
            'dec': ('Dec (Deg. J2000)', 120),
            'freq1': ('Tuning 1 (MHz)', 100),
            'freq2': ('Tuning 2 (MHz)', 100),
            'filter': ('Filter Code', 85),
        }

        for col in self.columns:
            text, width = headings.get(col, (col, 80))
            self.heading(col, text=text)
            self.column(col, width=width, minwidth=30)

        # Configure tag for invalid (red) rows
        self.tag_configure('invalid', foreground='red')

    def _on_double_click(self, event):
        """Handle double-click for inline editing."""
        region = self.identify_region(event.x, event.y)
        if region != 'cell':
            return

        column = self.identify_column(event.x)
        item = self.identify_row(event.y)

        if not item or not column:
            return

        # Get column index (column is like '#1', '#2', etc.)
        col_idx = int(column.replace('#', '')) - 1

        # Check if this column is editable
        if col_idx == 0:  # ID column not editable
            return

        # Get the item's current values
        values = self.item(item, 'values')
        if not values:
            return

        # Get row index
        children = self.get_children()
        row_idx = list(children).index(item) if item in children else None

        # Check if RA/Dec columns (6, 7) are editable for this scan
        # TRK_SOL, TRK_JOV have non-editable RA/Dec
        if col_idx in (6, 7) and hasattr(self.parent, 'project') and row_idx is not None:
            scan = self.parent.project.runs[0].scans[row_idx]
            if scan.mode in ('TRK_SOL', 'TRK_JOV'):
                return  # Don't allow editing

        # STEPPED mode has many non-editable columns
        # Columns 5-10: duration, ra, dec, freq1, freq2, filter
        if hasattr(self.parent, 'project') and row_idx is not None:
            scan = self.parent.project.runs[0].scans[row_idx]
            if scan.mode == 'STEPPED' and col_idx in [5, 6, 7, 8, 9, 10]:
                return  # Don't allow editing

        # Get the bounding box for the cell
        bbox = self.bbox(item, column)
        if not bbox:
            return

        # Create an entry widget for editing
        self._start_edit(item, column, col_idx, bbox, values[col_idx])

    def _start_edit(self, item, column, col_idx, bbox, current_value):
        """Start inline editing of a cell."""
        # Destroy any existing edit widget
        if self._edit_entry:
            self._edit_entry.destroy()

        # Check if this column uses a dropdown choice
        if col_idx in self.choice_options:
            self._edit_entry = ttk.Combobox(self, values=self.choice_options[col_idx], state='readonly')
            self._edit_entry.set(current_value)
        else:
            # Use tk.Entry instead of ttk.Entry for more predictable sizing
            # Use flat relief to avoid double-border effect with row selection
            self._edit_entry = tk.Entry(self, relief='flat', borderwidth=0,
                                        highlightthickness=1, highlightcolor='#4a90d9',
                                        highlightbackground='#cccccc')
            self._edit_entry.insert(0, current_value)
            self._edit_entry.select_range(0, tk.END)

        # Check for HiDPI scaling (common on macOS Retina)
        try:
            scaling = self.tk.call('tk', 'scaling')
        except:
            scaling = 1.0

        # Use bbox dimensions directly - they should be in the correct coordinate space
        x = bbox[0]
        y = bbox[1]
        width = bbox[2]
        height = bbox[3]

        self._edit_entry.place(x=x, y=y, width=width, height=height)
        self._edit_entry.focus_set()

        self._edit_item = item
        self._edit_column = column
        self._edit_col_idx = col_idx

        # Bind events
        self._edit_entry.bind('<Return>', self._finish_edit)
        self._edit_entry.bind('<Escape>', self._cancel_edit)
        self._edit_entry.bind('<FocusOut>', self._finish_edit)

    def _finish_edit(self, event=None):
        """Finish editing and save the value."""
        if not self._edit_entry or not self._edit_item:
            return

        new_value = self._edit_entry.get()
        old_values = list(self.item(self._edit_item, 'values'))

        # Find the row index
        children = self.get_children()
        row_idx = list(children).index(self._edit_item) if self._edit_item in children else None

        col_idx = self._edit_col_idx

        self._edit_entry.destroy()
        self._edit_entry = None
        item = self._edit_item
        self._edit_item = None
        self._edit_column = None

        # Update the display
        old_values[col_idx] = new_value
        self.item(item, values=old_values)

        # Trigger edit event on parent with row and column info
        if hasattr(self.parent, 'onCellEdit') and row_idx is not None:
            self.parent.onCellEdit(row_idx, col_idx, new_value)
        elif hasattr(self.parent, 'onEdit'):
            self.parent.onEdit(None)

    def _cancel_edit(self, event=None):
        """Cancel editing without saving."""
        if self._edit_entry:
            self._edit_entry.destroy()
            self._edit_entry = None
            self._edit_item = None
            self._edit_column = None

    def _on_selection_change(self, event):
        """Handle selection changes."""
        selection = self.selection()
        self.nSelected = len(selection)

        # Get the index of the selected item (if single selection)
        index = None
        if len(selection) == 1:
            children = self.get_children()
            for i, child in enumerate(children):
                if child == selection[0]:
                    index = i
                    break

        self.setCheckDependant(index)

    def setCheckDependant(self, index=None):
        """Update menu and toolbar states based on selection."""
        try:
            parent = self.parent
            if self.nSelected == 0:
                # Edit menu - disabled
                try:
                    parent.editmenu.entryconfig(parent.editmenu_cut_idx, state='disabled')
                    parent.editmenu.entryconfig(parent.editmenu_copy_idx, state='disabled')
                except (KeyError, AttributeError):
                    pass

                # Stepped scan edits - disabled
                try:
                    parent.obsmenu.entryconfig(parent.obsmenu_stepped_edit_idx, state='disabled')
                    parent.toolbar_buttons['edit_stepped'].config(state='disabled')
                except (KeyError, AttributeError):
                    pass

                # Proper motion, Remove and resolve - disabled
                try:
                    parent.obsmenu.entryconfig(parent.obsmenu_pmotion_idx, state='disabled')
                    parent.toolbar_buttons['pmotion'].config(state='disabled')
                except (KeyError, AttributeError):
                    pass
                try:
                    parent.obsmenu.entryconfig(parent.obsmenu_remove_idx, state='disabled')
                    parent.toolbar_buttons['remove'].config(state='disabled')
                except (KeyError, AttributeError):
                    pass
                try:
                    parent.obsmenu.entryconfig(parent.obsmenu_resolve_idx, state='disabled')
                except (KeyError, AttributeError):
                    pass

            elif self.nSelected == 1:
                # Edit menu - enabled
                try:
                    parent.editmenu.entryconfig(parent.editmenu_cut_idx, state='normal')
                    parent.editmenu.entryconfig(parent.editmenu_copy_idx, state='normal')
                except (KeyError, AttributeError):
                    pass

                # Stepped scan edits - check if STEPPED mode
                if index is not None:
                    try:
                        if parent.project.runs[0].scans[index].mode == 'STEPPED':
                            parent.obsmenu.entryconfig(parent.obsmenu_stepped_edit_idx, state='normal')
                            parent.toolbar_buttons['edit_stepped'].config(state='normal')
                        else:
                            parent.obsmenu.entryconfig(parent.obsmenu_stepped_edit_idx, state='disabled')
                            parent.toolbar_buttons['edit_stepped'].config(state='disabled')
                    except (KeyError, AttributeError):
                        pass

                # Proper motion, Remove and resolve - enabled
                try:
                    parent.obsmenu.entryconfig(parent.obsmenu_pmotion_idx, state='normal')
                    parent.toolbar_buttons['pmotion'].config(state='normal')
                except (KeyError, AttributeError):
                    pass
                try:
                    parent.obsmenu.entryconfig(parent.obsmenu_remove_idx, state='normal')
                    parent.toolbar_buttons['remove'].config(state='normal')
                except (KeyError, AttributeError):
                    pass
                try:
                    parent.obsmenu.entryconfig(parent.obsmenu_resolve_idx, state='normal')
                except (KeyError, AttributeError):
                    pass

            else:
                # Multiple selection
                try:
                    parent.editmenu.entryconfig(parent.editmenu_cut_idx, state='normal')
                    parent.editmenu.entryconfig(parent.editmenu_copy_idx, state='normal')
                except (KeyError, AttributeError):
                    pass

                try:
                    parent.obsmenu.entryconfig(parent.obsmenu_stepped_edit_idx, state='disabled')
                    parent.toolbar_buttons['edit_stepped'].config(state='disabled')
                except (KeyError, AttributeError):
                    pass

                try:
                    parent.obsmenu.entryconfig(parent.obsmenu_pmotion_idx, state='disabled')
                    parent.toolbar_buttons['pmotion'].config(state='disabled')
                except (KeyError, AttributeError):
                    pass
                try:
                    parent.obsmenu.entryconfig(parent.obsmenu_remove_idx, state='normal')
                    parent.toolbar_buttons['remove'].config(state='normal')
                except (KeyError, AttributeError):
                    pass
                try:
                    parent.obsmenu.entryconfig(parent.obsmenu_resolve_idx, state='disabled')
                except (KeyError, AttributeError):
                    pass
        except AttributeError:
            pass

    def CheckItem(self, index, check=True):
        """Set the check state of an item by index (selecting/deselecting it)."""
        children = self.get_children()
        if 0 <= index < len(children):
            item_id = children[index]
            if check:
                self.selection_add(item_id)
            else:
                self.selection_remove(item_id)
            self._check_states[index] = check

    def IsChecked(self, index):
        """Check if an item at given index is checked (selected)."""
        children = self.get_children()
        if 0 <= index < len(children):
            item_id = children[index]
            return item_id in self.selection()
        return False

    def DeleteAllItems(self):
        """Delete all items from the treeview."""
        for item in self.get_children():
            self.delete(item)
        self._check_states.clear()

    def DeleteAllColumns(self):
        """Reset columns - no-op for Treeview as columns are fixed."""
        pass

    def GetItemCount(self):
        """Get the number of items."""
        return len(self.get_children())

    def InsertItem(self, index, values):
        """Insert an item at the specified index."""
        children = self.get_children()
        if index >= len(children):
            item = self.insert('', 'end', values=values)
        else:
            item = self.insert('', index, values=values)
        return item

    def SetItem(self, item, col_idx, value):
        """Set a specific column value for an item."""
        values = list(self.item(item, 'values'))
        if col_idx < len(values):
            values[col_idx] = value
            self.item(item, values=values)

    def GetItem(self, item, col_idx):
        """Get a specific column value for an item."""
        values = self.item(item, 'values')
        if col_idx < len(values):
            return values[col_idx]
        return ''

    def GetNextSelected(self, start=-1):
        """Get the next selected item."""
        selection = self.selection()
        if selection:
            return selection[0]
        return None


class SteppedListCtrl(ttk.Treeview):
    """
    Custom Treeview widget for stepped observations.
    """

    def __init__(self, parent, **kwargs):
        self.columns = ('id', 'c1', 'c1_units', 'c2', 'c2_units',
                       'duration', 'freq1', 'freq2', 'max_sn', 'remaining')

        super().__init__(parent, columns=self.columns, show='headings', selectmode='extended', **kwargs)

        self.parent = parent
        self.nSelected = 0
        self._check_states = {}

        # Setup columns
        self._setup_columns()

        # Bind events
        self.bind('<Double-1>', self._on_double_click)
        self.bind('<<TreeviewSelect>>', self._on_selection_change)

        # For inline editing
        self._edit_entry = None
        self._edit_item = None
        self._edit_column = None

    def _setup_columns(self):
        """Setup column headings and widths."""
        headings = {
            'id': ('ID', 30),
            'c1': ('C1', 80),
            'c1_units': ('C1 Units', 60),
            'c2': ('C2', 80),
            'c2_units': ('C2 Units', 60),
            'duration': ('Duration', 80),
            'freq1': ('Freq. 1', 80),
            'freq2': ('Freq. 2', 80),
            'max_sn': ('MaxSN', 50),
            'remaining': ('Remaining', 80),
        }

        for col in self.columns:
            text, width = headings.get(col, (col, 80))
            self.heading(col, text=text)
            self.column(col, width=width, minwidth=30)

    def _on_double_click(self, event):
        """Handle double-click for inline editing."""
        region = self.identify_region(event.x, event.y)
        if region != 'cell':
            return

        column = self.identify_column(event.x)
        item = self.identify_row(event.y)

        if not item or not column:
            return

        col_idx = int(column.replace('#', '')) - 1

        # ID column not editable
        if col_idx == 0:
            return

        values = self.item(item, 'values')
        if not values:
            return

        bbox = self.bbox(item, column)
        if not bbox:
            return

        self._start_edit(item, column, col_idx, bbox, values[col_idx])

    def _start_edit(self, item, column, col_idx, bbox, current_value):
        """Start inline editing of a cell."""
        if self._edit_entry:
            self._edit_entry.destroy()

        # Use tk.Entry with flat styling for consistent appearance with ScanListCtrl
        self._edit_entry = tk.Entry(self, relief='flat', borderwidth=0,
                                    highlightthickness=1, highlightcolor='#4a90d9',
                                    highlightbackground='#cccccc')
        self._edit_entry.insert(0, current_value)
        self._edit_entry.select_range(0, tk.END)

        self._edit_entry.place(x=bbox[0], y=bbox[1], width=bbox[2], height=bbox[3])
        self._edit_entry.focus_set()

        self._edit_item = item
        self._edit_column = column
        self._edit_col_idx = col_idx

        self._edit_entry.bind('<Return>', self._finish_edit)
        self._edit_entry.bind('<Escape>', self._cancel_edit)
        self._edit_entry.bind('<FocusOut>', self._finish_edit)

    def _finish_edit(self, event=None):
        """Finish editing and save the value."""
        if not self._edit_entry or not self._edit_item:
            return

        new_value = self._edit_entry.get()
        old_values = list(self.item(self._edit_item, 'values'))

        children = self.get_children()
        row_idx = list(children).index(self._edit_item) if self._edit_item in children else None

        col_idx = self._edit_col_idx

        self._edit_entry.destroy()
        self._edit_entry = None
        item = self._edit_item
        self._edit_item = None
        self._edit_column = None

        old_values[col_idx] = new_value
        self.item(item, values=old_values)

        if hasattr(self.parent, 'onCellEdit') and row_idx is not None:
            self.parent.onCellEdit(row_idx, col_idx, new_value)

    def _cancel_edit(self, event=None):
        """Cancel editing without saving."""
        if self._edit_entry:
            self._edit_entry.destroy()
            self._edit_entry = None
            self._edit_item = None
            self._edit_column = None

    def _on_selection_change(self, event):
        """Handle selection changes."""
        selection = self.selection()
        self.nSelected = len(selection)
        self.setCheckDependant()

    def setCheckDependant(self, index=None):
        """Update menu and toolbar states based on selection."""
        try:
            parent = self.parent
            if self.nSelected == 0:
                try:
                    parent.editmenu.entryconfig(parent.editmenu_cut_idx, state='disabled')
                    parent.editmenu.entryconfig(parent.editmenu_copy_idx, state='disabled')
                except (KeyError, AttributeError):
                    pass
            else:
                try:
                    parent.editmenu.entryconfig(parent.editmenu_cut_idx, state='normal')
                    parent.editmenu.entryconfig(parent.editmenu_copy_idx, state='normal')
                except (KeyError, AttributeError):
                    pass
        except AttributeError:
            pass

    def CheckItem(self, index, check=True):
        """Set the check state of an item by index (selecting/deselecting it)."""
        children = self.get_children()
        if 0 <= index < len(children):
            item_id = children[index]
            if check:
                self.selection_add(item_id)
            else:
                self.selection_remove(item_id)
            self._check_states[index] = check

    def IsChecked(self, index):
        """Check if an item at given index is checked (selected)."""
        children = self.get_children()
        if 0 <= index < len(children):
            item_id = children[index]
            return item_id in self.selection()
        return False

    def DeleteAllItems(self):
        """Delete all items from the treeview."""
        for item in self.get_children():
            self.delete(item)
        self._check_states.clear()

    def GetItemCount(self):
        """Get the number of items."""
        return len(self.get_children())

    def InsertItem(self, index, values):
        """Insert an item at the specified index."""
        children = self.get_children()
        if index >= len(children):
            item = self.insert('', 'end', values=values)
        else:
            item = self.insert('', index, values=values)
        return item

    def SetItem(self, item, col_idx, value):
        """Set a specific column value for an item."""
        values = list(self.item(item, 'values'))
        if col_idx < len(values):
            values[col_idx] = value
            self.item(item, values=values)

    def GetItem(self, item, col_idx):
        """Get a specific column value for an item."""
        values = self.item(item, 'values')
        if col_idx < len(values):
            return values[col_idx]
        return ''


class PlotPanel(ttk.Frame):
    """
    The PlotPanel has a Figure and a Canvas for matplotlib integration.
    Tkinter version using FigureCanvasTkAgg.
    """

    def __init__(self, parent, color=None, dpi=None, **kwargs):
        super().__init__(parent, **kwargs)
        self.parent = parent

        # Initialize matplotlib figure
        self.figure = Figure(dpi=dpi)
        self.canvas = FigureCanvasTkAgg(self.figure, master=self)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

        self.SetColor(color)
        self.draw()

        # Bind resize event
        self.bind('<Configure>', self._on_resize)

    def SetColor(self, rgbtuple=None):
        """Set figure and canvas colours to be the same."""
        if rgbtuple is None:
            # Get system background color
            try:
                bg = self.winfo_rgb(self.cget('background'))
                rgbtuple = (bg[0]//256, bg[1]//256, bg[2]//256)
            except:
                rgbtuple = (240, 240, 240)
        clr = [c/255. for c in rgbtuple]
        self.figure.set_facecolor(clr)
        self.figure.set_edgecolor(clr)

    def _on_resize(self, event):
        """Handle resize events."""
        # The canvas will automatically resize with the frame
        pass

    def draw(self):
        """Abstract method to be overridden by child classes."""
        pass


class IDFCreator(tk.Tk):
    """
    Main window for the IDF Creator application.
    Tkinter version of the wxPython IDFCreator.
    """

    def __init__(self, args):
        super().__init__()
        self.title('Swarm GUI')
        self.geometry('1100x500')
        self.minsize(900, 400)

        self.scriptPath = os.path.abspath(__file__)
        self.scriptPath = os.path.split(self.scriptPath)[0]

        self.dirname = ''
        self.toolbar_buttons = {}
        self.editmenu = {}
        self.obsmenu = {}

        self.buffer = None
        self.edited = False
        self.idf_active = False  # Track whether an IDF has been created/loaded

        self.initIDF()
        self.initUI()
        self.initEvents()

        idf._DRSUCapacityTB = args.drsu_size

        # Start with observation modes disabled until a file is loaded or new session created
        self.setMenuButtons('None')
        self.setIDFActive(False)

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

        # Default correlator settings
        self.project.runs[0].corr_channels = 256
        self.project.runs[0].corr_inttime = 1.0
        self.project.runs[0].corr_basis = 'linear'

        self.project.runs[0].drxGain = -1

    def initUI(self):
        """
        Start the user interface.
        """

        # Menu bar
        menubar = tk.Menu(self)

        # File menu
        fileMenu = tk.Menu(menubar, tearoff=0)
        fileMenu.add_command(label='New', command=self.onNew, accelerator='Ctrl+N')
        fileMenu.add_command(label='Open', command=self.onLoad, accelerator='Ctrl+O')
        fileMenu.add_command(label='Save', command=self.onSave, accelerator='Ctrl+S')
        self.save_menu_idx = 2
        fileMenu.add_command(label='Save As', command=self.onSaveAs)
        fileMenu.add_separator()
        fileMenu.add_command(label='Quit', command=self.onQuit, accelerator='Ctrl+Q')
        menubar.add_cascade(label='File', menu=fileMenu)
        self.fileMenu = fileMenu

        # Edit menu
        editMenu = tk.Menu(menubar, tearoff=0)
        editMenu.add_command(label='Cut Selected Scan', command=self.onCut, state='disabled')
        self.editmenu_cut_idx = 0
        editMenu.add_command(label='Copy Selected Scan', command=self.onCopy, state='disabled')
        self.editmenu_copy_idx = 1
        editMenu.add_command(label='Paste Before Selected', command=self.onPasteBefore, state='disabled')
        self.editmenu_paste_before_idx = 2
        editMenu.add_command(label='Paste After Selected', command=self.onPasteAfter, state='disabled')
        self.editmenu_paste_after_idx = 3
        editMenu.add_command(label='Paste at End of List', command=self.onPasteEnd, state='disabled')
        self.editmenu_paste_end_idx = 4
        menubar.add_cascade(label='Edit', menu=editMenu)
        self.editmenu = editMenu

        # Scans menu
        scansMenu = tk.Menu(menubar, tearoff=0)
        scansMenu.add_command(label='Observer/Project Info.', command=self.onInfo)
        scansMenu.add_command(label='Scheduling', command=self.onSchedule)
        scansMenu.add_separator()

        # Add submenu
        addMenu = tk.Menu(scansMenu, tearoff=0)
        addMenu.add_command(label='DRX - RA/Dec', command=self.onAddDRXR)
        self.obsmenu_drx_radec_idx = 0
        addMenu.add_command(label='DRX - Solar', command=self.onAddDRXS)
        self.obsmenu_drx_solar_idx = 1
        addMenu.add_command(label='DRX - Jovian', command=self.onAddDRXJ)
        self.obsmenu_drx_jovian_idx = 2
        # Note: Stepped observations are not supported in lsl.common.idf for swarm mode
        # addMenu.add_separator()
        # addMenu.add_command(label='DRX - Stepped - RA/Dec', command=self.onAddSteppedRADec)
        # self.obsmenu_stepped_radec_idx = 4
        # addMenu.add_command(label='DRX - Stepped - Az/Alt', command=self.onAddSteppedAzAlt)
        # self.obsmenu_stepped_azalt_idx = 5
        # addMenu.add_command(label='DRX - Edit Selected Stepped Scan', command=self.onEditStepped, state='disabled')
        # self.obsmenu_stepped_edit_idx = 6
        scansMenu.add_cascade(label='Add', menu=addMenu)
        self.addMenu = addMenu

        scansMenu.add_command(label='Proper Motion', command=self.onProperMotion, state='disabled')
        self.obsmenu_pmotion_idx = 4
        scansMenu.add_command(label='Remove Selected', command=self.onRemove, state='disabled')
        self.obsmenu_remove_idx = 5
        scansMenu.add_command(label='Validate All', command=self.onValidate, accelerator='F5')
        scansMenu.add_separator()
        scansMenu.add_command(label='Resolve Selected', command=self.onResolve, state='disabled', accelerator='F3')
        self.obsmenu_resolve_idx = 8
        scansMenu.add_command(label='Calibrator Search', command=self.onSearch, accelerator='F4')
        scansMenu.add_command(label='Run at a Glance', command=self.onTimeseries)
        scansMenu.add_command(label='UV Coverage', command=self.onUVCoverage)
        scansMenu.add_command(label='Advanced Settings', command=self.onAdvanced)
        menubar.add_cascade(label='Scans', menu=scansMenu)
        self.obsmenu = scansMenu

        # Data menu
        dataMenu = tk.Menu(menubar, tearoff=0)
        dataMenu.add_command(label='Estimated Data Volume', command=self.onVolume)
        menubar.add_cascade(label='Data', menu=dataMenu)

        # Help menu
        helpMenu = tk.Menu(menubar, tearoff=0)
        helpMenu.add_command(label='Swarm GUI Handbook', command=self.onHelp, accelerator='F1')
        helpMenu.add_command(label='Filter Codes', command=self.onFilterInfo)
        helpMenu.add_separator()
        helpMenu.add_command(label='About', command=self.onAbout)
        menubar.add_cascade(label='Help', menu=helpMenu)
        self.helpMenu = helpMenu

        self.config(menu=menubar)

        # Toolbar
        toolbar_frame = ttk.Frame(self)
        toolbar_frame.pack(fill=tk.X, side=tk.TOP)
        self._create_toolbar(toolbar_frame)

        # Status bar
        self.statusbar = ttk.Label(self, text='', relief=tk.SUNKEN, anchor=tk.W)
        self.statusbar.pack(fill=tk.X, side=tk.BOTTOM)

        # Main panel with scan list
        main_frame = ttk.Frame(self)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Scan list
        list_frame = ttk.Frame(main_frame)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        self.listControl = ScanListCtrl(list_frame)
        self.listControl.parent = self
        self.listControl.pack(fill=tk.BOTH, expand=True, side=tk.LEFT)

        # Scrollbars for list
        v_scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.listControl.yview)
        self.listControl.configure(yscrollcommand=v_scrollbar.set)
        v_scrollbar.pack(fill=tk.Y, side=tk.RIGHT)

        h_scrollbar = ttk.Scrollbar(main_frame, orient=tk.HORIZONTAL, command=self.listControl.xview)
        self.listControl.configure(xscrollcommand=h_scrollbar.set)
        h_scrollbar.pack(fill=tk.X, side=tk.BOTTOM)

        # Add columns to the list control
        self.addColumns()

    def _create_toolbar(self, parent):
        """Create the toolbar with icon buttons."""

        icons_path = os.path.join(self.scriptPath, 'icons')

        def make_button(parent, icon_name, text, command, tooltip=''):
            try:
                img = tk.PhotoImage(file=os.path.join(icons_path, f'{icon_name}.png'))
                btn = ttk.Button(parent, image=img, command=command)
                btn.image = img  # Keep a reference
            except:
                btn = ttk.Button(parent, text=text, command=command, width=3)
            btn.pack(side=tk.LEFT, padx=1, pady=1)
            return btn

        self.toolbar_buttons['new'] = make_button(parent, 'new', 'N', self.onNew)
        self.toolbar_buttons['open'] = make_button(parent, 'open', 'O', self.onLoad)
        self.toolbar_buttons['save'] = make_button(parent, 'save', 'S', self.onSave)
        self.toolbar_buttons['save_as'] = make_button(parent, 'save-as', 'SA', self.onSaveAs)
        self.toolbar_buttons['quit'] = make_button(parent, 'exit', 'Q', self.onQuit)

        ttk.Separator(parent, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=5)

        self.toolbar_buttons['drx_radec'] = make_button(parent, 'drx-radec', 'DRX-R', self.onAddDRXR)
        self.toolbar_buttons['drx_solar'] = make_button(parent, 'drx-solar', 'DRX-S', self.onAddDRXS)
        self.toolbar_buttons['drx_jovian'] = make_button(parent, 'drx-jovian', 'DRX-J', self.onAddDRXJ)
        # Note: Stepped observations are not supported in lsl.common.idf for swarm mode
        # self.toolbar_buttons['stepped_radec'] = make_button(parent, 'stepped-radec', 'ST-R', self.onAddSteppedRADec)
        # self.toolbar_buttons['stepped_azalt'] = make_button(parent, 'stepped-azalt', 'ST-A', self.onAddSteppedAzAlt)
        # self.toolbar_buttons['edit_stepped'] = make_button(parent, 'stepped-edit', 'ST-E', self.onEditStepped)
        # self.toolbar_buttons['edit_stepped'].config(state='disabled')
        self.toolbar_buttons['pmotion'] = make_button(parent, 'pmotion', 'PM', self.onProperMotion)
        self.toolbar_buttons['pmotion'].config(state='disabled')
        self.toolbar_buttons['remove'] = make_button(parent, 'remove', 'Rem', self.onRemove)
        self.toolbar_buttons['remove'].config(state='disabled')
        self.toolbar_buttons['validate'] = make_button(parent, 'validate', 'Val', self.onValidate)

        ttk.Separator(parent, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=5)

        self.toolbar_buttons['search'] = make_button(parent, 'search', 'Srch', self.onSearch)

        ttk.Separator(parent, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=5)

        self.toolbar_buttons['help'] = make_button(parent, 'help', '?', self.onHelp)

    def initEvents(self):
        """
        Set all of the various events in the main window.
        """

        # Keyboard shortcuts
        self.bind('<Control-n>', lambda e: self.onNew())
        self.bind('<Control-o>', lambda e: self.onLoad())
        self.bind('<Control-s>', lambda e: self.onSave())
        self.bind('<Control-q>', lambda e: self.onQuit())
        self.bind('<F1>', lambda e: self.onHelp())
        self.bind('<F3>', lambda e: self.onResolve())
        self.bind('<F4>', lambda e: self.onSearch())
        self.bind('<F5>', lambda e: self.onValidate())

        # Window close
        self.protocol("WM_DELETE_WINDOW", self.onQuit)

    def addColumns(self):
        """
        Add columns to the scan list and set up column mapping and coercion functions.
        """

        # Define conversion/validation functions
        def intentConv(text):
            if text in ['FluxCal', 'PhaseCal', 'Target']:
                return text
            raise ValueError(f"Invalid intent: {text}")

        def raConv(text):
            """Convert RA from HH:MM:SS.S to decimal hours."""
            text = text.replace('h', ':').replace('m', ':').replace('s', '')
            fields = text.split(':')
            sign = -1 if text[0] == '-' else 1
            try:
                hours = abs(float(fields[0]))
                mins = float(fields[1]) if len(fields) > 1 else 0.0
                secs = float(fields[2]) if len(fields) > 2 else 0.0
                value = sign * (hours + mins/60.0 + secs/3600.0)
                if value < 0 or value >= 24:
                    raise ValueError()
                return value
            except:
                raise ValueError(f"Invalid RA: {text}")

        def decConv(text):
            """Convert Dec from DD:MM:SS.S to decimal degrees."""
            text = text.replace('d', ':').replace("'", ':').replace('"', '')
            fields = text.split(':')
            sign = -1 if text[0] == '-' else 1
            try:
                degs = abs(float(fields[0]))
                mins = float(fields[1]) if len(fields) > 1 else 0.0
                secs = float(fields[2]) if len(fields) > 2 else 0.0
                value = sign * (degs + mins/60.0 + secs/3600.0)
                if value < -90 or value > 90:
                    raise ValueError()
                return value
            except:
                raise ValueError(f"Invalid Dec: {text}")

        def freqConv(text):
            """Convert frequency to MHz."""
            try:
                value = float(text)
                if value < 10 or value > 88:
                    raise ValueError()
                return value
            except:
                raise ValueError(f"Invalid frequency: {text}")

        def filterConv(text):
            """Validate filter code."""
            try:
                value = int(text)
                if value < 1 or value > 7:
                    raise ValueError()
                return value
            except:
                raise ValueError(f"Invalid filter code: {text}")

        def pmConv(text):
            """Parse proper motion."""
            try:
                fields = text.split()
                return [float(f) for f in fields]
            except:
                raise ValueError(f"Invalid proper motion: {text}")

        # Column mapping: column index -> scan attribute name
        # Matches column order: (id, target, intent, comments, start, duration, ra, dec, freq1, freq2, filter)
        self.columnMap = {
            1: 'target',
            2: 'intent',
            3: 'comments',
            4: 'start',
            5: 'duration',
            6: 'ra',
            7: 'dec',
            8: 'freq1',
            9: 'freq2',
            10: 'filter',
        }

        # Coercion/validation functions for each column
        self.coerceMap = {
            1: str,       # target
            2: intentConv,  # intent
            3: str,       # comments
            4: str,       # start
            5: str,       # duration
            6: raConv,    # ra
            7: decConv,   # dec
            8: freqConv,  # freq1
            9: freqConv,  # freq2
            10: filterConv,  # filter
        }

    def addScan(self, scan, id, update=False):
        """
        Add a scan to the list control.
        """

        # Get mode for conditional formatting
        mode = getattr(scan, 'mode', 'TRK_RADEC')

        # Format RA/Dec based on mode
        if mode == 'TRK_SOL':
            ra_str = 'Sun'
            dec_str = '--'
        elif mode == 'TRK_JOV':
            ra_str = 'Jupiter'
            dec_str = '--'
        elif mode == 'STEPPED':
            ra_str = 'STEPPED'
            dec_str = 'RA/Dec' if getattr(scan, 'RADec', True) else 'Az/Alt'
        else:
            # TRK_RADEC - format RA/Dec as sexagesimal
            if hasattr(scan, 'ra') and scan.ra is not None:
                ra_str = dec2sexstr(scan.ra, signed=False)
            else:
                ra_str = '--'
            if hasattr(scan, 'dec') and scan.dec is not None:
                dec_str = dec2sexstr(scan.dec, signed=True)
            else:
                dec_str = '--'

        # Format frequencies (convert tuning word to MHz)
        if mode == 'STEPPED':
            freq1_str = '--'
            freq2_str = '--'
        else:
            if hasattr(scan, 'freq1'):
                freq1_str = "%.6f" % (scan.freq1 * fS / 2**32 / 1e6)
            else:
                freq1_str = '--'
            if hasattr(scan, 'freq2'):
                freq2_str = "%.6f" % (scan.freq2 * fS / 2**32 / 1e6)
            else:
                freq2_str = '--'

        # Format duration (use duration property which returns formatted string)
        duration_str = scan.duration if hasattr(scan, 'duration') else '--'

        # Format start time
        start_str = scan.start if hasattr(scan, 'start') else '--'

        # Intent (normalize to camel case for display)
        intent_map = {'fluxcal': 'FluxCal', 'phasecal': 'PhaseCal', 'target': 'Target'}
        raw_intent = scan.intent if hasattr(scan, 'intent') else 'target'
        intent_str = intent_map.get(raw_intent.lower(), raw_intent)

        # Comments
        comments_str = scan.comments if hasattr(scan, 'comments') and scan.comments else 'None provided'

        # Filter (also special for STEPPED)
        if mode == 'STEPPED':
            filter_str = '--'
        else:
            filter_str = "%i" % scan.filter if hasattr(scan, 'filter') else '7'

        # Build values tuple - matches column order:
        # (id, target, intent, comments, start, duration, ra, dec, freq1, freq2, filter)
        values = (
            str(id),
            scan.target if hasattr(scan, 'target') else '',
            intent_str,
            comments_str,
            start_str,
            duration_str,
            ra_str,
            dec_str,
            freq1_str,
            freq2_str,
            filter_str,
        )

        if update:
            # Update existing item
            children = self.listControl.get_children()
            if id - 1 < len(children):
                self.listControl.item(children[id - 1], values=values)
        else:
            # Insert new item
            self.listControl.insert('', 'end', values=values)

        self.edited = True
        self.setSaveButton()

    def setSaveButton(self):
        """Update the save button state based on whether there are unsaved changes."""
        if self.edited:
            self.title('Swarm GUI *')
            # Enable save button/menu when there are unsaved changes
            if 'save' in self.toolbar_buttons:
                self.toolbar_buttons['save'].config(state='normal')
            self.fileMenu.entryconfig(self.save_menu_idx, state='normal')
        else:
            self.title('Swarm GUI')
            # Disable save button/menu when there are no unsaved changes
            if 'save' in self.toolbar_buttons:
                self.toolbar_buttons['save'].config(state='disabled')
            self.fileMenu.entryconfig(self.save_menu_idx, state='disabled')

    def setIDFActive(self, active):
        """Set whether an IDF is currently active (loaded or created)."""
        self.idf_active = active
        if active:
            self.setMenuButtons('DRX')
        else:
            self.setMenuButtons('None')

    def setMenuButtons(self, mode):
        """
        Given a mode of scan (TRK_RADEC, TRK_SOL, etc.), update the
        various menu items in 'Scans' and the toolbar buttons.
        """
        mode = mode.lower()

        # If no IDF is active, disable all observation buttons
        if not self.idf_active:
            obs_buttons = ['drx_radec', 'drx_solar', 'drx_jovian']
            for key in obs_buttons:
                if key in self.toolbar_buttons:
                    self.toolbar_buttons[key].config(state='disabled')

            # Also disable menu items
            try:
                self.addMenu.entryconfig(self.obsmenu_drx_radec_idx, state='disabled')
                self.addMenu.entryconfig(self.obsmenu_drx_solar_idx, state='disabled')
                self.addMenu.entryconfig(self.obsmenu_drx_jovian_idx, state='disabled')
            except:
                pass
            return

        if mode[0:3] == 'trk' or mode[0:3] == 'drx':
            # Enable DRX observation modes
            self.addMenu.entryconfig(self.obsmenu_drx_radec_idx, state='normal')
            self.addMenu.entryconfig(self.obsmenu_drx_solar_idx, state='normal')
            self.addMenu.entryconfig(self.obsmenu_drx_jovian_idx, state='normal')

            self.toolbar_buttons['drx_radec'].config(state='normal')
            self.toolbar_buttons['drx_solar'].config(state='normal')
            self.toolbar_buttons['drx_jovian'].config(state='normal')
        else:
            # Disable DRX observation modes
            self.addMenu.entryconfig(self.obsmenu_drx_radec_idx, state='disabled')
            self.addMenu.entryconfig(self.obsmenu_drx_solar_idx, state='disabled')
            self.addMenu.entryconfig(self.obsmenu_drx_jovian_idx, state='disabled')

            self.toolbar_buttons['drx_radec'].config(state='disabled')
            self.toolbar_buttons['drx_solar'].config(state='disabled')
            self.toolbar_buttons['drx_jovian'].config(state='disabled')

    def onNew(self):
        """Create a new IDF project."""
        if self.edited:
            result = messagebox.askyesnocancel(
                'Unsaved Changes',
                'The current file has unsaved changes. Do you want to save before creating a new file?'
            )
            if result is None:  # Cancel
                return
            elif result:  # Yes
                self.onSave()

        self.listControl.DeleteAllItems()
        self.initIDF()

        self.edited = False
        self.setSaveButton()

        # Activate IDF mode (enables observation buttons)
        self.setIDFActive(True)

        # Open observer info dialog
        ObserverInfo(self)

    def onLoad(self):
        """Load an IDF file."""
        if self.edited:
            result = messagebox.askyesno(
                'Confirm Open',
                'The current interferometer definition file has changes that have not been saved.\n\nOpen a new file anyways?',
                default=messagebox.NO
            )
            if not result:
                return

        filename = filedialog.askopenfilename(
            initialdir=self.dirname,
            filetypes=[('IDF Files', '*.idf *.txt'), ('All Files', '*.*')]
        )
        if filename:
            self.dirname = os.path.dirname(filename)
            self.parseFile(filename)
            self.edited = False
            self.setSaveButton()

    def onSave(self):
        """Save the current IDF file."""
        if not hasattr(self, 'filename') or not self.filename:
            self.onSaveAs()
            return

        # Validate first
        if not self.onValidate(confirmValid=False):
            return

        try:
            with open(self.filename, 'w') as f:
                f.write(self.project.render())
            self.edited = False
            self.setSaveButton()
            self.statusbar.config(text=f'Saved to {self.filename}')
        except Exception as e:
            self.displayError(e, title='Save Error')

    def onSaveAs(self):
        """Save the current IDF file with a new name."""
        filename = filedialog.asksaveasfilename(
            initialdir=self.dirname,
            defaultextension='.idf',
            filetypes=[('IDF Files', '*.idf'), ('Text Files', '*.txt'), ('All Files', '*.*')]
        )
        if filename:
            self.filename = filename
            self.dirname = os.path.dirname(filename)
            self.onSave()

    def onCopy(self):
        """Copy the selected (checked) scan(s) to the buffer."""
        self.buffer = []
        for i in range(self.listControl.GetItemCount()):
            if self.listControl.IsChecked(i):
                self.buffer.append(copy.deepcopy(self.project.runs[0].scans[i]))

        if self.buffer:
            # Enable paste menu items
            self.editmenu.entryconfig(self.editmenu_paste_before_idx, state='normal')
            self.editmenu.entryconfig(self.editmenu_paste_after_idx, state='normal')
            self.editmenu.entryconfig(self.editmenu_paste_end_idx, state='normal')

    def onCut(self):
        """Cut selected scan(s) to buffer."""
        self.onCopy()
        self.onRemove()

    def onPasteBefore(self):
        """Paste scan(s) before the first checked item."""
        if not self.buffer:
            return

        # Find first checked item
        firstChecked = None
        for i in range(self.listControl.GetItemCount()):
            if self.listControl.IsChecked(i):
                firstChecked = i
                break

        if firstChecked is not None:
            # Insert scans in reverse order to maintain buffer order
            for obs in self.buffer[::-1]:
                cObs = copy.deepcopy(obs)
                self.project.runs[0].scans.insert(firstChecked, cObs)

            self.edited = True
            self.setSaveButton()

            # Fix the times on scans to make things continuous
            for idx in range(firstChecked + len(self.buffer) - 1, -1, -1):
                if idx + 1 < len(self.project.runs[0].scans):
                    dur = self.project.runs[0].scans[idx].dur
                    tStart, _ = idf.get_scan_start_stop(self.project.runs[0].scans[idx + 1])
                    tStart -= timedelta(seconds=dur // 1000, microseconds=(dur % 1000) * 1000)
                    cStart = 'UTC %i %02i %02i %02i:%02i:%06.3f' % (
                        tStart.year, tStart.month, tStart.day,
                        tStart.hour, tStart.minute, tStart.second + tStart.microsecond / 1e6
                    )
                    self.project.runs[0].scans[idx].start = cStart

            # Refresh list
            self._refreshScanList()

    def onPasteAfter(self):
        """Paste scan(s) after the last checked item."""
        if not self.buffer:
            return

        # Find last checked item
        lastChecked = None
        for i in range(self.listControl.GetItemCount()):
            if self.listControl.IsChecked(i):
                lastChecked = i

        if lastChecked is not None:
            insertIdx = lastChecked + 1

            # Insert scans in reverse order to maintain buffer order
            for obs in self.buffer[::-1]:
                cObs = copy.deepcopy(obs)
                self.project.runs[0].scans.insert(insertIdx, cObs)

            self.edited = True
            self.setSaveButton()

            # Fix the times on scans to make things continuous
            for idx in range(lastChecked + 1, len(self.project.runs[0].scans)):
                _, tStop = idf.get_scan_start_stop(self.project.runs[0].scans[idx - 1])
                cStart = 'UTC %i %02i %02i %02i:%02i:%06.3f' % (
                    tStop.year, tStop.month, tStop.day,
                    tStop.hour, tStop.minute, tStop.second + tStop.microsecond / 1e6
                )
                self.project.runs[0].scans[idx].start = cStart

            # Refresh list
            self._refreshScanList()

    def onPasteEnd(self):
        """Paste the selected scan(s) at the end of the current run."""
        if not self.buffer:
            return

        lastIdx = len(self.project.runs[0].scans) - 1

        for obs in self.buffer:
            cObs = copy.deepcopy(obs)

            # Fix the time to be continuous with the previous scan
            if len(self.project.runs[0].scans) > 0:
                _, tStop = idf.get_scan_start_stop(self.project.runs[0].scans[-1])
                cStart = 'UTC %i %02i %02i %02i:%02i:%06.3f' % (
                    tStop.year, tStop.month, tStop.day,
                    tStop.hour, tStop.minute, tStop.second + tStop.microsecond / 1e6
                )
                cObs.start = cStart

            self.project.runs[0].scans.append(cObs)

        self.edited = True
        self.setSaveButton()

        # Refresh list
        self._refreshScanList()

    def _refreshScanList(self):
        """Refresh the scan list from the project data."""
        self.listControl.DeleteAllItems()
        for i, scan in enumerate(self.project.runs[0].scans):
            self.addScan(scan, i + 1)
        self.edited = True
        self.setSaveButton()

    def onInfo(self):
        """Open observer/project information dialog."""
        ObserverInfo(self)

    def onSchedule(self):
        """Open scheduling dialog."""
        ScheduleWindow(self)

    def _getCurrentDateString(self):
        """Get a datetime string, in UTC, for a new scan."""
        tStop = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        if self.listControl.GetItemCount() > 0:
            _, tStop = idf.get_scan_start_stop(self.project.runs[0].scans[-1])

        return 'UTC %i %02i %02i %02i:%02i:%06.3f' % (
            tStop.year, tStop.month, tStop.day,
            tStop.hour, tStop.minute, tStop.second + tStop.microsecond / 1e6
        )

    def _getDefaultFilter(self):
        """Get the default filter code."""
        return 7

    def onAddDRXR(self):
        """Add a DRX RA/Dec scan."""
        id = self.listControl.GetItemCount() + 1
        scan = idf.DRX('scan_%i' % id, 'target', self._getCurrentDateString(),
                      '00:00:00.000', 0.0, 0.0, 37.9e6, 74.0e6, self._getDefaultFilter())
        scan.intent = 'Target'
        self.project.runs[0].scans.append(scan)
        self.addScan(scan, id)

    def onAddDRXS(self):
        """Add a DRX Solar scan."""
        id = self.listControl.GetItemCount() + 1
        scan = idf.Solar('scan_%i' % id, 'target', self._getCurrentDateString(),
                        '00:00:00.000', 37.9e6, 74.0e6, self._getDefaultFilter())
        scan.intent = 'Target'
        self.project.runs[0].scans.append(scan)
        self.addScan(scan, id)

    def onAddDRXJ(self):
        """Add a DRX Jovian scan."""
        id = self.listControl.GetItemCount() + 1
        scan = idf.Jovian('scan_%i' % id, 'target', self._getCurrentDateString(),
                         '00:00:00.000', 37.9e6, 74.0e6, self._getDefaultFilter())
        scan.intent = 'Target'
        self.project.runs[0].scans.append(scan)
        self.addScan(scan, id)

    # Note: Stepped observations are not supported in lsl.common.idf for swarm mode
    # def onAddSteppedRADec(self):
    #     """Add a stepped RA/Dec scan."""
    #     id = self.listControl.GetItemCount() + 1
    #     scan = idf.Stepped('scan_%i' % id, 'target', self._getCurrentDateString(),
    #                       self._getDefaultFilter(), is_radec=True, steps=[])
    #     scan.intent = 'Target'
    #     self.project.runs[0].scans.append(scan)
    #     self.addScan(scan, id)

    # def onAddSteppedAzAlt(self):
    #     """Add a stepped Az/Alt scan."""
    #     id = self.listControl.GetItemCount() + 1
    #     scan = idf.Stepped('scan_%i' % id, 'target', self._getCurrentDateString(),
    #                       self._getDefaultFilter(), is_radec=False, steps=[])
    #     scan.intent = 'Target'
    #     self.project.runs[0].scans.append(scan)
    #     self.addScan(scan, id)

    # def onEditStepped(self):
    #     """Edit a stepped scan."""
    #     selection = self.listControl.selection()
    #     if not selection:
    #         return
    #     children = self.listControl.get_children()
    #     idx = list(children).index(selection[0])

    #     if self.project.runs[0].scans[idx].mode == 'STEPPED':
    #         SteppedEditor(self, idx)

    def onCellEdit(self, row_idx, col_idx, new_value):
        """Handle cell edit events from the list control."""
        if row_idx >= len(self.project.runs[0].scans):
            return

        scan = self.project.runs[0].scans[row_idx]

        try:
            # Update the scan object based on column
            if col_idx == 1:  # Target
                scan.target = new_value
            elif col_idx == 2:  # Intent
                if new_value not in ('FluxCal', 'PhaseCal', 'Target'):
                    raise ValueError(f"Invalid intent: {new_value}")
                scan.intent = new_value
            elif col_idx == 3:  # Comments
                scan.comments = new_value if new_value != 'None provided' else None
            elif col_idx == 4:  # Start time
                scan.start = new_value
            elif col_idx == 5:  # Duration
                scan.duration = new_value
            elif col_idx == 6:  # RA
                if new_value not in ('Sun', 'Jupiter', 'STEPPED', '--'):
                    scan.ra = self._parseRA(new_value)
            elif col_idx == 7:  # Dec
                if new_value not in ('--', 'RA/Dec', 'Az/Alt'):
                    scan.dec = self._parseDec(new_value)
            elif col_idx == 8:  # Freq1
                if new_value != '--':
                    scan.freq1 = int(float(new_value) * 1e6 * 2**32 / fS)
            elif col_idx == 9:  # Freq2
                if new_value != '--':
                    scan.freq2 = int(float(new_value) * 1e6 * 2**32 / fS)
            elif col_idx == 10:  # Filter
                value = int(new_value)
                if value < 1 or value > 7:
                    raise ValueError(f"Invalid filter code: {new_value}")
                scan.filter = value

            scan.update()
            self.statusbar.config(text='')
            self.edited = True
            self.setSaveButton()

            # Clear any error tag from the row on successful edit
            children = self.listControl.get_children()
            if row_idx < len(children):
                self.listControl.item(children[row_idx], tags=())

        except (ValueError, AttributeError) as e:
            self.statusbar.config(text=f'Error: {str(e)}')
            # Mark the row as having an error
            children = self.listControl.get_children()
            if row_idx < len(children):
                self.listControl.item(children[row_idx], tags=('error',))
                self.listControl.tag_configure('error', foreground='red')

    def _parseRA(self, text):
        """Parse RA string to decimal hours."""
        text = text.replace('h', ':').replace('m', ':').replace('s', '')
        fields = text.replace('+', '').split(':')
        fields = [float(f) for f in fields]
        value = 0
        for f, d in zip(fields, [1.0, 60.0, 3600.0]):
            value += (f / d)
        if value < 0 or value >= 24:
            raise ValueError("RA value must be 0 <= RA < 24")
        return value

    def _parseDec(self, text):
        """Parse Dec string to decimal degrees."""
        text = text.replace('d', ':').replace("'", ':').replace('"', '')
        sign = -1 if text.startswith('-') else 1
        fields = text.replace('+', '').replace('-', '').split(':')
        fields = [float(f) for f in fields]
        value = 0
        for f, d in zip(fields, [1.0, 60.0, 3600.0]):
            value += (f / d)
        value *= sign
        if value < -90 or value > 90:
            raise ValueError("Dec value must be -90 <= Dec <= 90")
        return value

    def onProperMotion(self):
        """Open proper motion dialog."""
        selection = self.listControl.selection()
        if not selection:
            return
        children = self.listControl.get_children()
        idx = list(children).index(selection[0])
        ProperMotionWindow(self, idx)

    def onRemove(self):
        """Remove selected scans."""
        selection = self.listControl.selection()
        if not selection:
            return

        # Get indices in reverse order to avoid shifting issues
        children = list(self.listControl.get_children())
        indices = sorted([children.index(s) for s in selection], reverse=True)

        for idx in indices:
            del self.project.runs[0].scans[idx]

        self._refreshScanList()

    def onValidate(self, confirmValid=True):
        """Validate all scans."""
        self.statusbar.config(text='Validating...')
        self.update()

        if len(self.project.runs[0].scans) == 0:
            if confirmValid:
                messagebox.showwarning('Validation', 'No scans to validate.')
            return False

        validObs = True

        # First, clear all invalid tags
        children = self.listControl.get_children()
        for item in children:
            current_tags = self.listControl.item(item, 'tags')
            if current_tags and 'invalid' in current_tags:
                new_tags = tuple(t for t in current_tags if t != 'invalid')
                self.listControl.item(item, tags=new_tags)

        # Loop through the scans and validate one-at-a-time so that
        # we can mark bad scans with red highlighting
        for i, obs in enumerate(self.project.runs[0].scans):
            pid_print(f"Validating scan {i+1}")
            valid = obs.validate(verbose=True)

            if not valid:
                validObs = False
                # Mark invalid rows
                children = self.listControl.get_children()
                if i < len(children):
                    self.listControl.item(children[i], tags=('invalid',))

        # Configure tag for invalid items
        self.listControl.tag_configure('invalid', foreground='red')
        self.update()

        # Do a global validation
        old_stdout = sys.stdout
        sys.stdout = StringIO()
        try:
            global_valid = self.project.validate(verbose=True)
            full_msg = sys.stdout.getvalue()
            sys.stdout = old_stdout

            if global_valid and validObs:
                self.statusbar.config(text='Validation complete - all valid')
                if confirmValid:
                    messagebox.showinfo('Validator Results',
                                       'Congratulations, you have a valid set of scans.')
                return True
            else:
                self.statusbar.config(text='Validation failed - see console for details')
                # Print errors to console
                for line in full_msg.split('\n'):
                    if 'Error' in line or 'error' in line:
                        pid_print(line)
                if confirmValid:
                    messagebox.showerror('Validation Error',
                                        full_msg if full_msg else 'Validation failed.')
                return False
        except Exception as e:
            sys.stdout = old_stdout
            self.statusbar.config(text='Validation error')
            self.displayError(e, title='Validation Error')
            return False

    def onResolve(self):
        """Open target resolution dialog."""
        selection = self.listControl.selection()
        if not selection:
            return
        children = self.listControl.get_children()
        idx = list(children).index(selection[0])
        ResolveTarget(self, idx)

    def onSearch(self):
        """Open calibrator search dialog."""
        selection = self.listControl.selection()
        if selection:
            children = self.listControl.get_children()
            idx = list(children).index(selection[0])
        else:
            idx = 0
        SearchWindow(self, idx)

    def onTimeseries(self):
        """Open Run at a Glance dialog."""
        RunDisplay(self)

    def onUVCoverage(self):
        """Open UV Coverage dialog."""
        RunUVCoverageDisplay(self)

    def onAdvanced(self):
        """Open advanced settings dialog."""
        AdvancedInfo(self)

    def onVolume(self):
        """Open data volume dialog."""
        VolumeInfo(self)

    def onHelp(self):
        """Open help window."""
        HelpWindow(self)

    def onFilterInfo(self):
        """Show filter code information."""
        def units(value):
            if value >= 1e6:
                return f"{value/1e6:.3f} MHz"
            elif value >= 1e3:
                return f"{value/1e3:.3f} kHz"
            else:
                return f"{value:.3f} Hz"

        msg = "Filter Code Information:\n\n"
        for code in range(1, 8):
            bw = DRXFilters[code]
            msg += f"Filter {code}: {units(bw)}\n"

        messagebox.showinfo('Filter Codes', msg)

    def onAbout(self):
        """Show about dialog."""
        msg = f"""Swarm GUI

Version: {__version__}
Author: {__author__}

A graphical interface for creating and editing
IDF files for the LWA Swarm interferometer.

Based on lsl version {lsl.version.version}"""

        messagebox.showinfo('About Swarm GUI', msg)

    def onQuit(self):
        """Quit the application."""
        if self.edited:
            result = messagebox.askyesnocancel(
                'Unsaved Changes',
                'The current file has unsaved changes. Do you want to save before quitting?'
            )
            if result is None:  # Cancel
                return
            elif result:  # Yes
                self.onSave()

        self.destroy()

    def parseFile(self, filename):
        """Parse an IDF file and populate the UI."""
        try:
            # Clear existing state
            self.listControl.DeleteAllItems()
            self.listControl.nSelected = 0
            self.listControl.setCheckDependant()
            self.initIDF()

            # Parse the file
            self.project = idf.parse_idf(filename)
            self.filename = filename

            pid_print(f"Parsing file '{filename}'")

            # Populate the list
            for i, scan in enumerate(self.project.runs[0].scans):
                self.addScan(scan, i + 1)

            # Activate IDF mode and update menu/toolbar state based on mode
            self.setIDFActive(True)
            if len(self.project.runs[0].scans) > 0:
                self.setMenuButtons(self.project.runs[0].scans[0].mode)

            self.edited = False
            self.setSaveButton()
            self.statusbar.config(text=f'Loaded {filename}')

        except Exception as e:
            self.displayError(e, title='Load Error')

    def displayError(self, error, details=None, title=None):
        """Display an error dialog."""
        if title is None:
            title = 'Error'
        msg = str(error)
        if details:
            msg += f"\n\nDetails:\n{details}"
        messagebox.showerror(title, msg)


# Regex patterns for cleaning up comment strings
_cleanup0RE = re.compile(r';;(;;)+')
_cleanup1RE = re.compile(r'^;;')


class ObserverInfo(tk.Toplevel):
    """
    Dialog for entering observer and project information.
    """

    def __init__(self, parent):
        super().__init__(parent)
        self.title('Observer Information')
        self.parent = parent
        self.transient(parent)

        self.initUI()
        self.initEvents()
        self.grab_set()

    def initUI(self):
        """Setup the UI elements."""
        # Main frame with scrolling
        main_frame = ttk.Frame(self)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        row = 0

        # Observer Information Section
        obs_label = ttk.Label(main_frame, text='Observer Information', font=('TkDefaultFont', 12, 'bold'))
        obs_label.grid(row=row, column=0, columnspan=4, sticky='w', pady=(0, 10))
        row += 1

        ttk.Separator(main_frame, orient='horizontal').grid(row=row, column=0, columnspan=4, sticky='ew', pady=5)
        row += 1

        ttk.Label(main_frame, text='Observer ID:').grid(row=row, column=0, sticky='e', padx=5, pady=2)
        self.observerIDEntry = ttk.Entry(main_frame, width=15)
        self.observerIDEntry.grid(row=row, column=1, sticky='w', padx=5, pady=2)

        ttk.Label(main_frame, text='First Name:').grid(row=row, column=2, sticky='e', padx=5, pady=2)
        self.observerFirstEntry = ttk.Entry(main_frame, width=20)
        self.observerFirstEntry.grid(row=row, column=3, sticky='w', padx=5, pady=2)
        row += 1

        ttk.Label(main_frame, text='Last Name:').grid(row=row, column=2, sticky='e', padx=5, pady=2)
        self.observerLastEntry = ttk.Entry(main_frame, width=20)
        self.observerLastEntry.grid(row=row, column=3, sticky='w', padx=5, pady=2)
        row += 1

        # Project Information Section
        ttk.Separator(main_frame, orient='horizontal').grid(row=row, column=0, columnspan=4, sticky='ew', pady=10)
        row += 1

        proj_label = ttk.Label(main_frame, text='Project Information', font=('TkDefaultFont', 12, 'bold'))
        proj_label.grid(row=row, column=0, columnspan=4, sticky='w', pady=(0, 10))
        row += 1

        ttk.Label(main_frame, text='Project ID:').grid(row=row, column=0, sticky='e', padx=5, pady=2)
        self.projectIDEntry = ttk.Entry(main_frame, width=15)
        self.projectIDEntry.grid(row=row, column=1, sticky='w', padx=5, pady=2)

        ttk.Label(main_frame, text='Project Title:').grid(row=row, column=2, sticky='e', padx=5, pady=2)
        self.projectTitleEntry = ttk.Entry(main_frame, width=30)
        self.projectTitleEntry.grid(row=row, column=3, sticky='w', padx=5, pady=2)
        row += 1

        ttk.Label(main_frame, text='Comments:').grid(row=row, column=0, sticky='ne', padx=5, pady=2)
        self.projectCommentsEntry = tk.Text(main_frame, width=50, height=3)
        self.projectCommentsEntry.grid(row=row, column=1, columnspan=3, sticky='w', padx=5, pady=2)
        row += 1

        # Run Information Section
        ttk.Separator(main_frame, orient='horizontal').grid(row=row, column=0, columnspan=4, sticky='ew', pady=10)
        row += 1

        run_label = ttk.Label(main_frame, text='Run Information', font=('TkDefaultFont', 12, 'bold'))
        run_label.grid(row=row, column=0, columnspan=4, sticky='w', pady=(0, 10))
        row += 1

        ttk.Label(main_frame, text='Run ID:').grid(row=row, column=0, sticky='e', padx=5, pady=2)
        self.runIDEntry = ttk.Entry(main_frame, width=15)
        self.runIDEntry.grid(row=row, column=1, sticky='w', padx=5, pady=2)

        ttk.Label(main_frame, text='Run Title:').grid(row=row, column=2, sticky='e', padx=5, pady=2)
        self.runTitleEntry = ttk.Entry(main_frame, width=30)
        self.runTitleEntry.grid(row=row, column=3, sticky='w', padx=5, pady=2)
        row += 1

        ttk.Label(main_frame, text='Comments:').grid(row=row, column=0, sticky='ne', padx=5, pady=2)
        self.runCommentsEntry = tk.Text(main_frame, width=50, height=3)
        self.runCommentsEntry.grid(row=row, column=1, columnspan=3, sticky='w', padx=5, pady=2)
        row += 1

        # Correlator Settings Section
        ttk.Separator(main_frame, orient='horizontal').grid(row=row, column=0, columnspan=4, sticky='ew', pady=10)
        row += 1

        corr_label = ttk.Label(main_frame, text='Correlator Settings', font=('TkDefaultFont', 12, 'bold'))
        corr_label.grid(row=row, column=0, columnspan=4, sticky='w', pady=(0, 10))
        row += 1

        ttk.Label(main_frame, text='Channels:').grid(row=row, column=0, sticky='e', padx=5, pady=2)
        self.nchnEntry = ttk.Entry(main_frame, width=10)
        self.nchnEntry.grid(row=row, column=1, sticky='w', padx=5, pady=2)

        ttk.Label(main_frame, text='Int. Time (s):').grid(row=row, column=2, sticky='e', padx=5, pady=2)
        self.tintEntry = ttk.Entry(main_frame, width=10)
        self.tintEntry.grid(row=row, column=3, sticky='w', padx=5, pady=2)
        row += 1

        ttk.Label(main_frame, text='Polarization:').grid(row=row, column=0, sticky='e', padx=5, pady=2)
        pol_frame = ttk.Frame(main_frame)
        pol_frame.grid(row=row, column=1, columnspan=3, sticky='w', padx=5, pady=2)

        self.pol_var = tk.StringVar(value='linear')
        ttk.Radiobutton(pol_frame, text='Linear', variable=self.pol_var, value='linear').pack(side=tk.LEFT, padx=5)
        ttk.Radiobutton(pol_frame, text='Circular', variable=self.pol_var, value='circular').pack(side=tk.LEFT, padx=5)
        ttk.Radiobutton(pol_frame, text='Stokes', variable=self.pol_var, value='stokes').pack(side=tk.LEFT, padx=5)
        row += 1

        # Data Return Section
        ttk.Separator(main_frame, orient='horizontal').grid(row=row, column=0, columnspan=4, sticky='ew', pady=10)
        row += 1

        data_label = ttk.Label(main_frame, text='Data Return', font=('TkDefaultFont', 12, 'bold'))
        data_label.grid(row=row, column=0, columnspan=4, sticky='w', pady=(0, 10))
        row += 1

        self.data_return_var = tk.StringVar(value='ucf')
        ttk.Radiobutton(main_frame, text='USB Hard Drive', variable=self.data_return_var, value='usb',
                       command=self.onRadioButtons).grid(row=row, column=0, columnspan=2, sticky='w', padx=5, pady=2)
        row += 1

        ttk.Radiobutton(main_frame, text='UCF (Username):', variable=self.data_return_var, value='ucf',
                       command=self.onRadioButtons).grid(row=row, column=0, sticky='w', padx=5, pady=2)
        self.ucfUsernameEntry = ttk.Entry(main_frame, width=20, state='disabled')
        self.ucfUsernameEntry.grid(row=row, column=1, sticky='w', padx=5, pady=2)
        row += 1

        # Buttons
        ttk.Separator(main_frame, orient='horizontal').grid(row=row, column=0, columnspan=4, sticky='ew', pady=10)
        row += 1

        btn_frame = ttk.Frame(main_frame)
        btn_frame.grid(row=row, column=0, columnspan=4, pady=10)

        ttk.Button(btn_frame, text='OK', command=self.onOK, width=10).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text='Cancel', command=self.onCancel, width=10).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text='Save Defaults', command=self.onSaveDefaults, width=12).pack(side=tk.LEFT, padx=5)

        # Load existing values
        self._loadValues()

    def _loadValues(self):
        """Load existing values from parent project, falling back to saved preferences."""
        project = self.parent.project

        # Load preferences from file
        preferences = {}
        try:
            config_file = os.path.join(os.path.expanduser('~'), '.sessionGUI')
            with open(config_file) as ph:
                for line in ph.readlines():
                    line = line.replace('\n', '')
                    if len(line) < 3 or line[0] == '#':
                        continue
                    key, value = line.split(None, 1)
                    preferences[key] = value
        except:
            pass

        # Observer - use project values if set, otherwise preferences
        if project.observer and project.observer.id != 0:
            self.observerIDEntry.insert(0, str(project.observer.id))
        elif 'ObserverID' in preferences:
            self.observerIDEntry.insert(0, preferences['ObserverID'])

        if project.observer and project.observer.first:
            self.observerFirstEntry.insert(0, project.observer.first)
            self.observerLastEntry.insert(0, project.observer.last)
        else:
            if 'ObserverFirstName' in preferences:
                self.observerFirstEntry.insert(0, preferences['ObserverFirstName'])
            if 'ObserverLastName' in preferences:
                self.observerLastEntry.insert(0, preferences['ObserverLastName'])

        # Project - use project values if set, otherwise preferences
        if project.id:
            self.projectIDEntry.insert(0, project.id)
        elif 'ProjectID' in preferences:
            self.projectIDEntry.insert(0, preferences['ProjectID'])

        if project.name:
            self.projectTitleEntry.insert(0, project.name)
        elif 'ProjectName' in preferences:
            self.projectTitleEntry.insert(0, preferences['ProjectName'])

        if project.comments:
            self.projectCommentsEntry.insert('1.0', project.comments.replace(';;', '\n'))

        # Run
        if project.runs:
            run = project.runs[0]
            self.runIDEntry.insert(0, str(run.id))
            self.runTitleEntry.insert(0, run.name)
            if run.comments:
                self.runCommentsEntry.insert('1.0', run.comments.replace(';;', '\n'))

            # Correlator settings
            self.nchnEntry.insert(0, str(run.corr_channels) if hasattr(run, 'corr_channels') else '256')
            self.tintEntry.insert(0, str(run.corr_inttime) if hasattr(run, 'corr_inttime') else '1.0')
            if hasattr(run, 'corr_basis'):
                self.pol_var.set(run.corr_basis)

            # Data return method
            if hasattr(run, 'data_return_method') and run.data_return_method == 'USB Harddrives':
                self.data_return_var.set('usb')
                self.ucfUsernameEntry.config(state='disabled')
            else:
                self.data_return_var.set('ucf')
                self.ucfUsernameEntry.config(state='normal')
                # Extract UCF username from comments
                if run.comments:
                    mtch = idf.UCF_USERNAME_RE.search(run.comments)
                    if mtch is not None:
                        self.ucfUsernameEntry.insert(0, mtch.group('username'))

    def initEvents(self):
        """Bind events."""
        self.protocol("WM_DELETE_WINDOW", self.onCancel)

    def onRadioButtons(self):
        """Handle data return radio button changes."""
        if self.data_return_var.get() == 'ucf':
            self.ucfUsernameEntry.config(state='normal')
        else:
            self.ucfUsernameEntry.config(state='disabled')

    def onOK(self):
        """Save the observer/project information."""
        try:
            project = self.parent.project

            # Validate observer ID
            try:
                observer_id = int(self.observerIDEntry.get())
                if observer_id < 1:
                    self.displayError('Observer ID must be greater than zero', title='Observer ID Error')
                    return
            except ValueError:
                self.displayError('Observer ID must be numeric', title='Observer ID Error')
                return

            # Validate run ID
            try:
                run_id = int(self.runIDEntry.get())
                if run_id < 1:
                    self.displayError('Run ID must be greater than zero', title='Run ID Error')
                    return
            except ValueError:
                self.displayError('Run ID must be numeric', title='Run ID Error')
                return

            # Validate UCF username if UCF return method selected
            if self.data_return_var.get() == 'ucf':
                ucf_username = self.ucfUsernameEntry.get().strip()
                if not ucf_username:
                    self.displayError('UCF username is required when using UCF data return method',
                                     title='Missing UCF Username')
                    return

            # Update observer
            project.observer = idf.Observer(
                self.observerFirstEntry.get() + ' ' + self.observerLastEntry.get(),
                observer_id,
                first=self.observerFirstEntry.get(),
                last=self.observerLastEntry.get()
            )

            # Update project
            project.id = self.projectIDEntry.get()
            project.name = self.projectTitleEntry.get()
            project.comments = self.projectCommentsEntry.get('1.0', tk.END).strip().replace('\n', ';;')

            # Update run
            if project.runs:
                run = project.runs[0]
                run.id = run_id
                run.name = self.runTitleEntry.get()
                run.comments = self.runCommentsEntry.get('1.0', tk.END).strip().replace('\n', ';;')

                # Correlator settings
                run.corr_channels = int(self.nchnEntry.get()) if self.nchnEntry.get() else 256
                run.corr_inttime = float(self.tintEntry.get()) if self.tintEntry.get() else 1.0
                run.corr_basis = self.pol_var.get()

                # Data return method
                if self.data_return_var.get() == 'usb':
                    run.data_return_method = 'USB Harddrives'
                else:
                    run.data_return_method = 'UCF'
                    # Add UCF username to comments
                    tempc = idf.UCF_USERNAME_RE.sub('', run.comments)
                    run.comments = tempc + ';;ucfuser:%s' % self.ucfUsernameEntry.get().strip()

            # Cleanup comments (remove duplicate semicolons)
            if project.comments:
                project.comments = _cleanup0RE.sub(';;', project.comments)
                project.comments = _cleanup1RE.sub('', project.comments)
            if project.runs and project.runs[0].comments:
                project.runs[0].comments = _cleanup0RE.sub(';;', project.runs[0].comments)
                project.runs[0].comments = _cleanup1RE.sub('', project.runs[0].comments)

            self.parent.mode = 'DRX'
            self.parent.setMenuButtons(self.parent.mode)
            if self.parent.listControl.GetItemCount() == 0:
                self.parent.addColumns()

            self.parent.edited = True
            self.parent.setSaveButton()
            self.destroy()

        except Exception as e:
            self.displayError(e, title='Validation Error')

    def onCancel(self):
        """Cancel without saving."""
        self.destroy()

    def onSaveDefaults(self):
        """Save current values as defaults."""
        try:
            config_file = os.path.join(os.path.expanduser('~'), '.sessionGUI')

            # Load existing preferences first
            preferences = {}
            try:
                with open(config_file) as ph:
                    for line in ph.readlines():
                        line = line.replace('\n', '')
                        if len(line) < 3 or line[0] == '#':
                            continue
                        key, value = line.split(None, 1)
                        preferences[key] = value
            except:
                pass

            # Update with current values
            try:
                preferences['ObserverID'] = int(self.observerIDEntry.get())
            except (TypeError, ValueError):
                pass
            first = self.observerFirstEntry.get()
            if first:
                preferences['ObserverFirstName'] = first
            last = self.observerLastEntry.get()
            if last:
                preferences['ObserverLastName'] = last
            pID = self.projectIDEntry.get()
            if pID:
                preferences['ProjectID'] = pID
            pTitle = self.projectTitleEntry.get()
            if pTitle:
                preferences['ProjectName'] = pTitle

            # Write preferences
            with open(config_file, 'w') as ph:
                for key in preferences:
                    ph.write(f"{key:-24s} {str(preferences[key])}\n")

            messagebox.showinfo('Saved', 'Defaults saved to ~/.sessionGUI')
        except Exception as e:
            self.displayError(e, title='Save Error')

    def displayError(self, error, details=None, title=None):
        """Display an error dialog."""
        if title is None:
            title = 'Error'
        msg = str(error)
        if details:
            msg += f"\n\nDetails:\n{details}"
        messagebox.showerror(title, msg)


class AdvancedInfo(tk.Toplevel):
    """
    Dialog for advanced settings (stations, ASP filter, DRX gain).
    """

    def __init__(self, parent):
        super().__init__(parent)
        self.title('Advanced Settings')
        self.parent = parent
        self.transient(parent)

        self.initUI()
        self.initEvents()
        self.grab_set()

    def initUI(self):
        """Setup the UI elements."""
        main_frame = ttk.Frame(self)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        row = 0

        # Stations Section
        sta_label = ttk.Label(main_frame, text='Stations', font=('TkDefaultFont', 12, 'bold'))
        sta_label.grid(row=row, column=0, columnspan=4, sticky='w', pady=(0, 10))
        row += 1

        ttk.Separator(main_frame, orient='horizontal').grid(row=row, column=0, columnspan=4, sticky='ew', pady=5)
        row += 1

        # Create checkboxes for stations
        self.staChecks = []
        sta_frame = ttk.Frame(main_frame)
        sta_frame.grid(row=row, column=0, columnspan=4, sticky='w', pady=5)

        try:
            all_stations = stations.get_all_stations()
            col = 0
            sta_row = 0
            for sta in all_stations:
                var = tk.BooleanVar(value=True)
                cb = ttk.Checkbutton(sta_frame, text=sta.name, variable=var)
                cb.grid(row=sta_row, column=col, sticky='w', padx=5, pady=2)
                self.staChecks.append((sta.name, var, cb))
                col += 1
                if col >= 3:
                    col = 0
                    sta_row += 1
        except:
            ttk.Label(sta_frame, text='No stations available').grid(row=0, column=0)

        row += 1

        # ASP Filter Section
        ttk.Separator(main_frame, orient='horizontal').grid(row=row, column=0, columnspan=4, sticky='ew', pady=10)
        row += 1

        asp_label = ttk.Label(main_frame, text='ASP Filter', font=('TkDefaultFont', 12, 'bold'))
        asp_label.grid(row=row, column=0, columnspan=4, sticky='w', pady=(0, 10))
        row += 1

        ttk.Label(main_frame, text='Filter Setting:').grid(row=row, column=0, sticky='e', padx=5, pady=2)
        self.aspCombo = ttk.Combobox(main_frame, state='readonly', width=20)
        self.aspCombo['values'] = ['MCS Decides', 'Split', 'Full', 'Reduced', 'Off', 'Split @ 3MHz', 'Full @ 3MHz']
        self.aspCombo.set('MCS Decides')
        self.aspCombo.grid(row=row, column=1, columnspan=2, sticky='w', padx=5, pady=2)
        row += 1

        # DRX Gain Section
        ttk.Separator(main_frame, orient='horizontal').grid(row=row, column=0, columnspan=4, sticky='ew', pady=10)
        row += 1

        gain_label = ttk.Label(main_frame, text='DRX Gain', font=('TkDefaultFont', 12, 'bold'))
        gain_label.grid(row=row, column=0, columnspan=4, sticky='w', pady=(0, 10))
        row += 1

        ttk.Label(main_frame, text='Gain Setting:').grid(row=row, column=0, sticky='e', padx=5, pady=2)
        self.gainCombo = ttk.Combobox(main_frame, state='readonly', width=15)
        self.gainCombo['values'] = ['MCS Decides'] + [str(i) for i in range(13)]
        self.gainCombo.set('MCS Decides')
        self.gainCombo.grid(row=row, column=1, sticky='w', padx=5, pady=2)

        ttk.Label(main_frame, text='(Lower values = higher gain)').grid(row=row, column=2, sticky='w', padx=5, pady=2)
        row += 1

        # Buttons
        ttk.Separator(main_frame, orient='horizontal').grid(row=row, column=0, columnspan=4, sticky='ew', pady=10)
        row += 1

        btn_frame = ttk.Frame(main_frame)
        btn_frame.grid(row=row, column=0, columnspan=4, pady=10)

        ttk.Button(btn_frame, text='OK', command=self.onOK, width=10).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text='Cancel', command=self.onCancel, width=10).pack(side=tk.LEFT, padx=5)

        # Load existing values
        self._loadValues()

    def _loadValues(self):
        """Load existing values from parent project."""
        if self.parent.project.runs and self.parent.project.runs[0].scans:
            run = self.parent.project.runs[0]
            scan = run.scans[0]

            # ASP filter
            if hasattr(scan, 'asp_filter'):
                asp_map = {0: 'MCS Decides', 1: 'Split', 2: 'Full', 3: 'Reduced', 4: 'Off', 5: 'Split @ 3MHz', 6: 'Full @ 3MHz'}
                self.aspCombo.set(asp_map.get(scan.asp_filter, 'MCS Decides'))

            # DRX Gain
            if hasattr(run, 'drxGain'):
                if run.drxGain < 0:
                    self.gainCombo.set('MCS Decides')
                else:
                    self.gainCombo.set(str(run.drxGain))

            # Stations
            if hasattr(run, 'stations'):
                for name, var, cb in self.staChecks:
                    var.set(name in [s.name for s in run.stations])

    def initEvents(self):
        """Bind events."""
        self.protocol("WM_DELETE_WINDOW", self.onCancel)

    def onOK(self):
        """Save the advanced settings."""
        try:
            run = self.parent.project.runs[0]

            # Check at least 2 stations selected
            selected_stations = [name for name, var, cb in self.staChecks if var.get()]
            if len(selected_stations) < 2:
                messagebox.showerror('Error', 'At least 2 stations must be selected.')
                return

            # Update stations
            all_stations = stations.get_all_stations()
            run.stations = [s for s in all_stations if s.name in selected_stations]

            # Update ASP filter
            asp_map = {'MCS Decides': 0, 'Split': 1, 'Full': 2, 'Reduced': 3, 'Off': 4, 'Split @ 3MHz': 5, 'Full @ 3MHz': 6}
            asp_value = asp_map.get(self.aspCombo.get(), 0)
            for scan in run.scans:
                scan.asp_filter = asp_value

            # Update DRX gain
            gain_str = self.gainCombo.get()
            if gain_str == 'MCS Decides':
                run.drxGain = -1
            else:
                run.drxGain = int(gain_str)

            self.parent.edited = True
            self.parent.setSaveButton()
            self.destroy()

        except Exception as e:
            messagebox.showerror('Error', str(e))

    def onCancel(self):
        """Cancel without saving."""
        self.destroy()


class RunDisplay(tk.Toplevel):
    """
    Display "Run at a Glance" - source altitude over time.
    """

    def __init__(self, parent):
        super().__init__(parent)
        self.title('Run at a Glance')
        self.parent = parent
        self.geometry('800x600')
        self.transient(parent)

        self.initUI()
        self.initEvents()
        self.initPlot()

    def initUI(self):
        """Setup the UI elements."""
        main_frame = ttk.Frame(self)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Options frame (above the plot)
        options_frame = ttk.Frame(main_frame)
        options_frame.pack(fill=tk.X, padx=5, pady=5)

        ttk.Label(options_frame, text='Color by:').pack(side=tk.LEFT, padx=(0, 5))
        self.color_by_var = tk.StringVar(value='Station')
        self.color_by_combo = ttk.Combobox(options_frame, textvariable=self.color_by_var,
                                           values=['Station', 'Intent'], state='readonly', width=10)
        self.color_by_combo.pack(side=tk.LEFT)
        self.color_by_combo.bind('<<ComboboxSelected>>', self.onColorByChanged)

        # Matplotlib figure
        self.figure = Figure(figsize=(8, 6), dpi=100)
        self.canvas = FigureCanvasTkAgg(self.figure, master=main_frame)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

        # Toolbar
        toolbar_frame = ttk.Frame(main_frame)
        toolbar_frame.pack(fill=tk.X)
        self.toolbar = NavigationToolbar2Tk(self.canvas, toolbar_frame)
        self.toolbar.update()

        # Status bar
        self.statusbar = ttk.Label(self, text='', relief=tk.SUNKEN, anchor=tk.W)
        self.statusbar.pack(fill=tk.X, side=tk.BOTTOM)

        # Close button
        btn_frame = ttk.Frame(self)
        btn_frame.pack(fill=tk.X, pady=5)
        ttk.Button(btn_frame, text='Close', command=self.destroy).pack()

    def initEvents(self):
        """Bind events."""
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.canvas.mpl_connect('motion_notify_event', self.on_motion)

    def onColorByChanged(self, event=None):
        """Handle color-by dropdown selection change."""
        self.initPlot()

    def initPlot(self):
        """Create the altitude plot with dual axes, station/intent colors, and scan labels."""
        self.obs = self.parent.project.runs[0].scans

        if len(self.obs) == 0:
            self.figure.clf()
            ax = self.figure.add_subplot(111)
            ax.text(0.5, 0.5, 'No scans to display', transform=ax.transAxes,
                   ha='center', va='center')
            self.canvas.draw()
            return

        # Find the earliest scan
        self.earliest = conflict.unravelObs(self.obs)[0][0]

        self.figure.clf()
        self.ax1 = self.figure.gca()
        self.ax2 = self.ax1.twiny()

        run = self.parent.project.runs[0]
        color_by = self.color_by_var.get()

        # Color mappings
        station_colors = {}
        intent_colors = {'fluxcal': '#1f77b4', 'phasecal': '#ff7f0e', 'target': '#2ca02c'}
        intents_used = set()

        for i, o in enumerate(self.obs):
            # Get the source
            src = o.fixed_body

            stepSize = o.dur / 1000.0 / 300
            if stepSize < 30.0:
                stepSize = 30.0

            # Get intent for this scan (normalize to lowercase for color lookup)
            intent = (o.intent if hasattr(o, 'intent') else 'target').lower()
            intents_used.add(intent)

            # Find altitude over the course of the scan for each station
            j = 0
            station_list = run.stations if hasattr(run, 'stations') and run.stations else [stations.lwa1]
            for station in station_list:
                t = []
                alt = []
                dt = 0.0

                # Get station observer
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
                station_id = station.id if hasattr(station, 'id') else str(j)
                if color_by == 'Intent':
                    # Color by intent
                    line, = self.ax1.plot(t, alt, label='%s - %s' % (o.target, intent),
                                          color=intent_colors.get(intent, '#7f7f7f'))
                else:
                    # Color by station (default)
                    try:
                        line, = self.ax1.plot(t, alt, label='%s - %s' % (o.target, station_id),
                                              color=station_colors[station])
                    except KeyError:
                        line, = self.ax1.plot(t, alt, label='%s - %s' % (o.target, station_id))
                        station_colors[station] = line.get_color()

                # Draw the scan limits and label the source
                if j == 0:
                    self.ax1.vlines(o.mjd + o.mpm / 1000.0 / (3600.0 * 24.0) - self.earliest, 0, 90, linestyle=':')
                    self.ax1.vlines(o.mjd + (o.mpm / 1000.0 + o.dur / 1000.0) / (3600.0 * 24.0) - self.earliest, 0, 90, linestyle=':')

                    self.ax1.text(o.mjd + o.mpm / 1000.0 / (3600.0 * 24.0) - self.earliest + o.dur / 1000 / 3600 / 24.0 * 0.02,
                                 2 + 10 * (i % 2), o.target, rotation='vertical', fontsize=8)

                j += 1

        # Add a legend
        if color_by == 'Intent':
            # Create legend with intent labels (only show intents that are used)
            from matplotlib.lines import Line2D
            legend_elements = []
            # Map lowercase keys to display names
            intent_display = {'fluxcal': 'FluxCal', 'phasecal': 'PhaseCal', 'target': 'Target'}
            for intent_key in ['fluxcal', 'phasecal', 'target']:
                if intent_key in intents_used:
                    legend_elements.append(Line2D([0], [0], color=intent_colors[intent_key],
                                                  label=intent_display[intent_key]))
            if legend_elements:
                self.ax1.legend(handles=legend_elements, loc=0)
        else:
            # Station labels only
            handles, labels = self.ax1.get_legend_handles_labels()
            station_labels = [l.rsplit(' -', 1)[1].strip() for l in labels]
            n_stations = len(station_list)
            if n_stations > 0:
                self.ax1.legend(handles[:n_stations], station_labels[:n_stations], loc=0)

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

        self.figure.tight_layout()
        self.canvas.draw()

    def on_motion(self, event):
        """Handle mouse motion over plot."""
        if event.inaxes and event.xdata is not None:
            # Events come from the second set of axes (in hours)
            t = event.xdata / 24.0 + self.earliest
            mjd = int(t)
            mpm = int((t - mjd) * 24.0 * 3600.0 * 1000.0)

            # Compute the run elapsed time
            elapsed = event.xdata * 3600.0
            eHour = int(elapsed / 3600)
            eMinute = int((elapsed % 3600) / 60)
            eSecond = (elapsed % 3600) % 60

            elapsed_str = "%02i:%02i:%06.3f" % (eHour, eMinute, eSecond)
            self.statusbar.config(text="MJD: %i  MPM: %i;  Run Elapsed Time: %s" % (mjd, mpm, elapsed_str))
        else:
            self.statusbar.config(text="")


class RunUVCoverageDisplay(tk.Toplevel):
    """
    Display UV coverage plot for the run.
    """

    def __init__(self, parent):
        super().__init__(parent)
        self.title('UV Coverage')
        self.parent = parent
        self.geometry('800x600')
        self.transient(parent)

        self.initUI()
        self.initEvents()
        self.initPlot()

    def initUI(self):
        """Setup the UI elements."""
        main_frame = ttk.Frame(self)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Matplotlib figure
        self.figure = Figure(figsize=(8, 6), dpi=100)
        self.canvas = FigureCanvasTkAgg(self.figure, master=main_frame)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

        # Toolbar
        toolbar_frame = ttk.Frame(main_frame)
        toolbar_frame.pack(fill=tk.X)
        self.toolbar = NavigationToolbar2Tk(self.canvas, toolbar_frame)
        self.toolbar.update()

        # Close button
        btn_frame = ttk.Frame(self)
        btn_frame.pack(fill=tk.X, pady=5)
        ttk.Button(btn_frame, text='Close', command=self.destroy).pack()

    def initEvents(self):
        """Bind events."""
        self.protocol("WM_DELETE_WINDOW", self.destroy)

    def initPlot(self):
        """Create the UV coverage plot using actual baseline calculations."""
        self.obs = self.parent.project.runs[0].scans

        if len(self.obs) == 0:
            ax = self.figure.add_subplot(111)
            ax.text(0.5, 0.5, 'No scans to display', transform=ax.transAxes,
                   ha='center', va='center')
            self.canvas.draw()
            return

        # Find the earliest scan
        self.earliest = conflict.unravelObs(self.obs)[0][0]

        self.figure.clf()

        # Build up the list of antennas for UV coverage calculation
        antennas = []
        observer = stations.lwa1.get_observer()
        run = self.parent.project.runs[0]
        if hasattr(run, 'stations') and run.stations:
            for station in run.stations:
                stand = stations.Stand(len(antennas), *(stations.lwa1.get_enz_offset(station)))
                cable = stations.Cable('%s-%s' % (station.id, 0), 0.0, vf=1.0, dd=0.0)
                antenna = stations.Antenna(len(antennas), stand=stand, cable=cable, pol=0)
                antennas.append(antenna)
        else:
            # Default: create a simple 2-element array
            for i in range(2):
                stand = stations.Stand(i, i * 100.0, 0.0, 0.0)
                cable = stations.Cable('stand-%i' % i, 0.0, vf=1.0, dd=0.0)
                antenna = stations.Antenna(i, stand=stand, cable=cable, pol=0)
                antennas.append(antenna)

        uv_coverage = {}
        order = []
        for o in self.obs:
            # Get the source
            src = o.fixed_body
            if src.name not in order:
                order.append(src.name)
            if src.name + "@T1" not in uv_coverage:
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

        # Plot
        nPlot = len(order)
        nRow = int(numpy.ceil(numpy.sqrt(nPlot)))
        nCol = int(numpy.ceil(nPlot / nRow))

        for i, name in enumerate(order):
            key = name + '@T1'
            t1 = uv_coverage[key]
            t2 = uv_coverage[key.replace('@T1', '@T2')]

            ax = self.figure.add_subplot(nCol, nRow, i + 1)
            for t in t1:
                if t.size > 0:
                    ax.scatter(t[:, 0, 0], t[:, 1, 0], marker='+', color='b', s=1)
                    ax.scatter(-t[:, 0, 0], -t[:, 1, 0], marker='+', color='b', s=1)
            for t in t2:
                if t.size > 0:
                    ax.scatter(t[:, 0, 0], t[:, 1, 0], marker='+', color='g', s=1)
                    ax.scatter(-t[:, 0, 0], -t[:, 1, 0], marker='+', color='g', s=1)

            # Labels
            ax.set_xlabel('$u$ [k$\\lambda$]')
            ax.set_ylabel('$v$ [k$\\lambda$]')
            ax.set_title(name)

        self.figure.tight_layout()
        self.canvas.draw()


class VolumeInfo(tk.Toplevel):
    """
    Display estimated data volume information.
    """

    def __init__(self, parent):
        super().__init__(parent)
        self.title('Estimated Data Volume')
        self.parent = parent
        self.transient(parent)

        self.initUI()
        self.initEvents()
        self.grab_set()

    def initUI(self):
        """Setup the UI elements."""
        main_frame = ttk.Frame(self)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        row = 0

        # Title
        title_label = ttk.Label(main_frame, text='Estimated Data Volume', font=('TkDefaultFont', 12, 'bold'))
        title_label.grid(row=row, column=0, columnspan=4, pady=(0, 10))
        row += 1

        ttk.Separator(main_frame, orient='horizontal').grid(row=row, column=0, columnspan=4, sticky='ew', pady=5)
        row += 1

        # Headers
        ttk.Label(main_frame, text='Scan', font=('TkDefaultFont', 10, 'bold')).grid(row=row, column=0, padx=5, pady=2)
        ttk.Label(main_frame, text='Raw Volume', font=('TkDefaultFont', 10, 'bold')).grid(row=row, column=1, padx=5, pady=2)
        ttk.Label(main_frame, text='Corr. Volume', font=('TkDefaultFont', 10, 'bold')).grid(row=row, column=2, padx=5, pady=2)
        row += 1

        ttk.Separator(main_frame, orient='horizontal').grid(row=row, column=0, columnspan=4, sticky='ew', pady=5)
        row += 1

        # Calculate and display volumes
        run = self.parent.project.runs[0]
        total_raw = 0
        total_corr = 0

        for i, scan in enumerate(run.scans):
            ttk.Label(main_frame, text=f'Scan #{i+1}').grid(row=row, column=0, padx=5, pady=2, sticky='w')

            # Raw data volume
            if hasattr(scan, 'dataVolume'):
                raw_vol = scan.dataVolume
                if hasattr(run, 'stations'):
                    raw_vol *= len(run.stations)
            else:
                raw_vol = 0
            total_raw += raw_vol

            ttk.Label(main_frame, text=f'{raw_vol / 1e9:.2f} GB').grid(row=row, column=1, padx=5, pady=2)

            # Correlated data volume (estimate)
            if hasattr(scan, 'dur'):
                n_chan = run.corr_channels if hasattr(run, 'corr_channels') else 256
                t_int = run.corr_inttime if hasattr(run, 'corr_inttime') else 1.0
                n_sta = len(run.stations) if hasattr(run, 'stations') else 2
                n_bl = n_sta * (n_sta + 1) // 2
                corr_vol = 32 * 2 * 4 * n_chan * (scan.dur / 1000.0 / t_int) * n_bl * 1.02
            else:
                corr_vol = 0
            total_corr += corr_vol

            ttk.Label(main_frame, text=f'{corr_vol / 1e9:.2f} GB').grid(row=row, column=2, padx=5, pady=2)
            row += 1

        # Totals
        ttk.Separator(main_frame, orient='horizontal').grid(row=row, column=0, columnspan=4, sticky='ew', pady=5)
        row += 1

        ttk.Label(main_frame, text='Total', font=('TkDefaultFont', 10, 'bold')).grid(row=row, column=0, padx=5, pady=2, sticky='w')
        ttk.Label(main_frame, text=f'{total_raw / 1e9:.2f} GB', font=('TkDefaultFont', 10, 'bold')).grid(row=row, column=1, padx=5, pady=2)
        ttk.Label(main_frame, text=f'{total_corr / 1e9:.2f} GB', font=('TkDefaultFont', 10, 'bold')).grid(row=row, column=2, padx=5, pady=2)
        row += 1

        # OK button
        ttk.Separator(main_frame, orient='horizontal').grid(row=row, column=0, columnspan=4, sticky='ew', pady=10)
        row += 1

        ttk.Button(main_frame, text='OK', command=self.destroy, width=10).grid(row=row, column=0, columnspan=4, pady=10)

    def initEvents(self):
        """Bind events."""
        self.protocol("WM_DELETE_WINDOW", self.destroy)


class ResolveTarget(tk.Toplevel):
    """
    Dialog to resolve astronomical target names to coordinates.
    """

    def __init__(self, parent, scanID=-1):
        super().__init__(parent)
        self.title('Resolve Target')
        self.parent = parent
        self.scanID = scanID
        self.transient(parent)

        self.initUI()
        self.initEvents()
        self.grab_set()

    def initUI(self):
        """Setup the UI elements."""
        main_frame = ttk.Frame(self)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        row = 0

        # Source name
        ttk.Label(main_frame, text='Source Name:').grid(row=row, column=0, sticky='e', padx=5, pady=5)
        self.sourceEntry = ttk.Entry(main_frame, width=30)
        self.sourceEntry.grid(row=row, column=1, columnspan=2, sticky='w', padx=5, pady=5)
        row += 1

        # Set initial source name from scan
        if self.scanID >= 0 and self.scanID < len(self.parent.project.runs[0].scans):
            scan = self.parent.project.runs[0].scans[self.scanID]
            if hasattr(scan, 'target'):
                self.sourceEntry.insert(0, scan.target)

        ttk.Separator(main_frame, orient='horizontal').grid(row=row, column=0, columnspan=3, sticky='ew', pady=10)
        row += 1

        # Resolved coordinates
        ttk.Label(main_frame, text='RA (J2000):').grid(row=row, column=0, sticky='e', padx=5, pady=5)
        self.raEntry = ttk.Entry(main_frame, width=20, state='readonly')
        self.raEntry.grid(row=row, column=1, sticky='w', padx=5, pady=5)
        row += 1

        ttk.Label(main_frame, text='Dec (J2000):').grid(row=row, column=0, sticky='e', padx=5, pady=5)
        self.decEntry = ttk.Entry(main_frame, width=20, state='readonly')
        self.decEntry.grid(row=row, column=1, sticky='w', padx=5, pady=5)
        row += 1

        ttk.Label(main_frame, text='Service:').grid(row=row, column=0, sticky='e', padx=5, pady=5)
        self.serviceEntry = ttk.Entry(main_frame, width=20, state='readonly')
        self.serviceEntry.grid(row=row, column=1, sticky='w', padx=5, pady=5)
        row += 1

        ttk.Separator(main_frame, orient='horizontal').grid(row=row, column=0, columnspan=3, sticky='ew', pady=10)
        row += 1

        # Proper motion
        self.pmVar = tk.BooleanVar(value=False)
        ttk.Checkbutton(main_frame, text='Include Proper Motion', variable=self.pmVar).grid(row=row, column=0, columnspan=2, sticky='w', padx=5, pady=5)
        row += 1

        ttk.Label(main_frame, text='PM RA (mas/yr):').grid(row=row, column=0, sticky='e', padx=5, pady=5)
        self.pmRaEntry = ttk.Entry(main_frame, width=15)
        self.pmRaEntry.grid(row=row, column=1, sticky='w', padx=5, pady=5)
        self.pmRaEntry.insert(0, '0.0')
        row += 1

        ttk.Label(main_frame, text='PM Dec (mas/yr):').grid(row=row, column=0, sticky='e', padx=5, pady=5)
        self.pmDecEntry = ttk.Entry(main_frame, width=15)
        self.pmDecEntry.grid(row=row, column=1, sticky='w', padx=5, pady=5)
        self.pmDecEntry.insert(0, '0.0')
        row += 1

        # Buttons
        ttk.Separator(main_frame, orient='horizontal').grid(row=row, column=0, columnspan=3, sticky='ew', pady=10)
        row += 1

        btn_frame = ttk.Frame(main_frame)
        btn_frame.grid(row=row, column=0, columnspan=3, pady=10)

        ttk.Button(btn_frame, text='Resolve', command=self.onResolve, width=10).pack(side=tk.LEFT, padx=5)
        self.applyBtn = ttk.Button(btn_frame, text='Apply', command=self.onApply, width=10, state='disabled')
        self.applyBtn.pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text='Cancel', command=self.destroy, width=10).pack(side=tk.LEFT, padx=5)

    def initEvents(self):
        """Bind events."""
        self.protocol("WM_DELETE_WINDOW", self.destroy)

    def onResolve(self):
        """Resolve the target name."""
        source_name = self.sourceEntry.get().strip()
        if not source_name:
            messagebox.showerror('Error', 'Please enter a source name.')
            return

        try:
            # Resolve using LSL's astro module (returns equ_posn object)
            posn = astro.resolve_name(source_name)

            # Format coordinates (posn.ra and posn.dec are in degrees)
            ra_str = str(astro.deg_to_hms(posn.ra)).replace(' ', ':')
            dec_str = str(astro.deg_to_dms(posn.dec)).replace(' ', ':')

            # Update display
            self.raEntry.config(state='normal')
            self.raEntry.delete(0, tk.END)
            self.raEntry.insert(0, ra_str)
            self.raEntry.config(state='readonly')

            self.decEntry.config(state='normal')
            self.decEntry.delete(0, tk.END)
            self.decEntry.insert(0, dec_str)
            self.decEntry.config(state='readonly')

            self.serviceEntry.config(state='normal')
            self.serviceEntry.delete(0, tk.END)
            self.serviceEntry.insert(0, posn.resolved_by)
            self.serviceEntry.config(state='readonly')

            # Store resolved values (convert RA from degrees to hours)
            self._resolved_ra = posn.ra / 15.0
            self._resolved_dec = posn.dec

            # Handle proper motion if available and checkbox is checked
            if self.pmVar.get():
                if posn.pm_ra is not None:
                    self.pmRaEntry.delete(0, tk.END)
                    self.pmRaEntry.insert(0, f"{posn.pm_ra:.2f}")
                if posn.pm_dec is not None:
                    self.pmDecEntry.delete(0, tk.END)
                    self.pmDecEntry.insert(0, f"{posn.pm_dec:.2f}")

            if self.scanID >= 0:
                self.applyBtn.config(state='normal')

        except RuntimeError:
            self.raEntry.config(state='normal')
            self.raEntry.delete(0, tk.END)
            self.raEntry.insert(0, '---')
            self.raEntry.config(state='readonly')

            self.decEntry.config(state='normal')
            self.decEntry.delete(0, tk.END)
            self.decEntry.insert(0, '---')
            self.decEntry.config(state='readonly')

            self.serviceEntry.config(state='normal')
            self.serviceEntry.delete(0, tk.END)
            self.serviceEntry.insert(0, 'Error resolving target')
            self.serviceEntry.config(state='readonly')

        except Exception as e:
            messagebox.showerror('Resolution Failed', f'Could not resolve "{source_name}": {str(e)}')

    def onApply(self):
        """Apply resolved coordinates to scan."""
        if self.scanID < 0 or self.scanID >= len(self.parent.project.runs[0].scans):
            return

        try:
            scan = self.parent.project.runs[0].scans[self.scanID]
            scan.target = self.sourceEntry.get().strip()
            scan.ra = self._resolved_ra
            scan.dec = self._resolved_dec

            if self.pmVar.get():
                pm_ra = float(self.pmRaEntry.get())
                pm_dec = float(self.pmDecEntry.get())
                scan.pm = [pm_ra, pm_dec]

            self.parent._refreshScanList()
            self.parent.edited = True
            self.parent.setSaveButton()
            self.destroy()

        except Exception as e:
            messagebox.showerror('Error', str(e))


class ScheduleWindow(tk.Toplevel):
    """
    Dialog for configuring scheduling options.
    """

    def __init__(self, parent):
        super().__init__(parent)
        self.title('Scheduling Options')
        self.parent = parent
        self.transient(parent)

        self.initUI()
        self.initEvents()
        self.grab_set()

    def initUI(self):
        """Setup the UI elements."""
        main_frame = ttk.Frame(self)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        row = 0

        # Title
        title_label = ttk.Label(main_frame, text='Run Scheduling', font=('TkDefaultFont', 12, 'bold'))
        title_label.grid(row=row, column=0, columnspan=2, pady=(0, 10))
        row += 1

        ttk.Separator(main_frame, orient='horizontal').grid(row=row, column=0, columnspan=2, sticky='ew', pady=5)
        row += 1

        # Radio buttons for scheduling type
        self.sched_var = tk.StringVar(value='sidereal')

        # Check existing comments for scheduling type
        if self.parent.project.runs:
            comments = self.parent.project.runs[0].comments or ''
            if 'ScheduleSiderealMovable' in comments:
                self.sched_var.set('sidereal')
            elif 'ScheduleSolarMovable' in comments:
                self.sched_var.set('solar')
            elif 'ScheduleFixed' in comments:
                self.sched_var.set('fixed')

        ttk.Radiobutton(main_frame, text='Sidereal time fixed, date changeable',
                       variable=self.sched_var, value='sidereal').grid(row=row, column=0, columnspan=2, sticky='w', padx=5, pady=5)
        row += 1

        ttk.Radiobutton(main_frame, text='UTC time fixed, date changeable',
                       variable=self.sched_var, value='solar').grid(row=row, column=0, columnspan=2, sticky='w', padx=5, pady=5)
        row += 1

        ttk.Radiobutton(main_frame, text='Use only specified date/time',
                       variable=self.sched_var, value='fixed').grid(row=row, column=0, columnspan=2, sticky='w', padx=5, pady=5)
        row += 1

        # Buttons
        ttk.Separator(main_frame, orient='horizontal').grid(row=row, column=0, columnspan=2, sticky='ew', pady=10)
        row += 1

        btn_frame = ttk.Frame(main_frame)
        btn_frame.grid(row=row, column=0, columnspan=2, pady=10)

        ttk.Button(btn_frame, text='Apply', command=self.onApply, width=10).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text='Cancel', command=self.destroy, width=10).pack(side=tk.LEFT, padx=5)

    def initEvents(self):
        """Bind events."""
        self.protocol("WM_DELETE_WINDOW", self.destroy)

    def onApply(self):
        """Apply scheduling settings."""
        if not self.parent.project.runs:
            return

        run = self.parent.project.runs[0]

        # Remove old scheduling tags
        comments = run.comments or ''
        for tag in ['ScheduleSiderealMovable', 'ScheduleSolarMovable', 'ScheduleFixed']:
            comments = comments.replace(tag, '').strip()

        # Add new tag
        sched_type = self.sched_var.get()
        if sched_type == 'sidereal':
            tag = 'ScheduleSiderealMovable'
        elif sched_type == 'solar':
            tag = 'ScheduleSolarMovable'
        else:
            tag = 'ScheduleFixed'

        if comments:
            comments = f'{comments} {tag}'
        else:
            comments = tag

        run.comments = comments

        self.parent.edited = True
        self.parent.setSaveButton()
        self.destroy()


class ProperMotionWindow(tk.Toplevel):
    """
    Dialog for editing proper motion values.
    """

    def __init__(self, parent, scanID):
        super().__init__(parent)
        self.title(f'Scan #{scanID + 1} Proper Motion')
        self.parent = parent
        self.scanID = scanID
        self.transient(parent)

        self.initUI()
        self.initEvents()
        self.grab_set()

    def initUI(self):
        """Setup the UI elements."""
        main_frame = ttk.Frame(self)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        row = 0

        scan = self.parent.project.runs[0].scans[self.scanID]

        # Target name (read-only)
        ttk.Label(main_frame, text='Target:').grid(row=row, column=0, sticky='e', padx=5, pady=5)
        self.targetLabel = ttk.Label(main_frame, text=scan.target if hasattr(scan, 'target') else 'Unknown')
        self.targetLabel.grid(row=row, column=1, sticky='w', padx=5, pady=5)
        row += 1

        ttk.Separator(main_frame, orient='horizontal').grid(row=row, column=0, columnspan=2, sticky='ew', pady=10)
        row += 1

        # Proper motion RA
        ttk.Label(main_frame, text='PM RA (mas/yr):').grid(row=row, column=0, sticky='e', padx=5, pady=5)
        self.pmRaEntry = ttk.Entry(main_frame, width=15)
        self.pmRaEntry.grid(row=row, column=1, sticky='w', padx=5, pady=5)
        if hasattr(scan, 'pm') and scan.pm:
            self.pmRaEntry.insert(0, f'{scan.pm[0]:.3f}')
        else:
            self.pmRaEntry.insert(0, '0.000')
        row += 1

        # Proper motion Dec
        ttk.Label(main_frame, text='PM Dec (mas/yr):').grid(row=row, column=0, sticky='e', padx=5, pady=5)
        self.pmDecEntry = ttk.Entry(main_frame, width=15)
        self.pmDecEntry.grid(row=row, column=1, sticky='w', padx=5, pady=5)
        if hasattr(scan, 'pm') and scan.pm:
            self.pmDecEntry.insert(0, f'{scan.pm[1]:.3f}')
        else:
            self.pmDecEntry.insert(0, '0.000')
        row += 1

        # Buttons
        ttk.Separator(main_frame, orient='horizontal').grid(row=row, column=0, columnspan=2, sticky='ew', pady=10)
        row += 1

        btn_frame = ttk.Frame(main_frame)
        btn_frame.grid(row=row, column=0, columnspan=2, pady=10)

        ttk.Button(btn_frame, text='Apply', command=self.onApply, width=10).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text='Cancel', command=self.destroy, width=10).pack(side=tk.LEFT, padx=5)

    def initEvents(self):
        """Bind events."""
        self.protocol("WM_DELETE_WINDOW", self.destroy)

    def onApply(self):
        """Apply proper motion values."""
        try:
            pm_ra = float(self.pmRaEntry.get())
            pm_dec = float(self.pmDecEntry.get())

            scan = self.parent.project.runs[0].scans[self.scanID]
            scan.pm = [pm_ra, pm_dec]

            self.parent._refreshScanList()
            self.parent.edited = True
            self.parent.setSaveButton()
            self.destroy()

        except ValueError:
            messagebox.showerror('Error', 'Invalid proper motion value. Please enter a number.')


# Note: Stepped observations are not supported in lsl.common.idf for swarm mode
# class SteppedEditor(tk.Toplevel):
#     """
#     Dialog for editing stepped observation steps.
#     """
#
#     def __init__(self, parent, scanID):
#         super().__init__(parent)
#         self.title(f'Edit Stepped Scan #{scanID + 1}')
#         self.parent = parent
#         self.scanID = scanID
#         self.geometry('700x400')
#         self.transient(parent)
#
#         self.initUI()
#         self.initEvents()
#         self.grab_set()
#
#     def initUI(self):
#         """Setup the UI elements."""
#         main_frame = ttk.Frame(self)
#         main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
#
#         # Step list
#         list_frame = ttk.Frame(main_frame)
#         list_frame.pack(fill=tk.BOTH, expand=True)
#
#         self.listControl = SteppedListCtrl(list_frame)
#         self.listControl.parent = self
#         self.listControl.pack(fill=tk.BOTH, expand=True, side=tk.LEFT)
#
#         scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.listControl.yview)
#         self.listControl.configure(yscrollcommand=scrollbar.set)
#         scrollbar.pack(fill=tk.Y, side=tk.RIGHT)
#
#         # Buttons
#         btn_frame = ttk.Frame(main_frame)
#         btn_frame.pack(fill=tk.X, pady=10)
#
#         ttk.Button(btn_frame, text='Add Step', command=self.onAdd).pack(side=tk.LEFT, padx=5)
#         ttk.Button(btn_frame, text='Remove Step', command=self.onRemove).pack(side=tk.LEFT, padx=5)
#         ttk.Button(btn_frame, text='OK', command=self.onOK).pack(side=tk.RIGHT, padx=5)
#         ttk.Button(btn_frame, text='Cancel', command=self.destroy).pack(side=tk.RIGHT, padx=5)
#
#         # Load existing steps
#         self._loadSteps()
#
#     def _loadSteps(self):
#         """Load existing steps from the scan."""
#         scan = self.parent.project.runs[0].scans[self.scanID]
#         if hasattr(scan, 'steps'):
#             for i, step in enumerate(scan.steps):
#                 # Format step data for display
#                 values = (str(i + 1), '', '', '', '', '', '', '', '', '')
#                 self.listControl.insert('', 'end', values=values)
#
#     def initEvents(self):
#         """Bind events."""
#         self.protocol("WM_DELETE_WINDOW", self.destroy)
#
#     def onAdd(self):
#         """Add a new step."""
#         id = len(self.listControl.get_children()) + 1
#         values = (str(id), '0.0', 'hours', '0.0', 'degrees', '00:00:01.000', '37.9', '74.0', '7', '')
#         self.listControl.insert('', 'end', values=values)
#
#     def onRemove(self):
#         """Remove selected step."""
#         selection = self.listControl.selection()
#         if selection:
#             self.listControl.delete(selection[0])
#             # Renumber remaining steps
#             for i, item in enumerate(self.listControl.get_children()):
#                 values = list(self.listControl.item(item, 'values'))
#                 values[0] = str(i + 1)
#                 self.listControl.item(item, values=values)
#
#     def onOK(self):
#         """Save steps and close."""
#         # TODO: Parse step values from listControl and update the scan's steps list.
#         # Each step should include: c1, c1_units, c2, c2_units, duration, freq1, freq2, max_sn
#         # The scan object is at: self.parent.project.runs[0].scans[self.scanID]
#         # After parsing, call scan.update() to recalculate derived values.
#         self.parent.edited = True
#         self.parent.setSaveButton()
#         self.destroy()


class SearchWindow(OCS):
    """
    Calibrator search window that extends the base CalibratorSearch class.
    """

    def __init__(self, parent, scanID=-1):
        self.idf_parent = parent
        self.scanID = scanID

        # Get initial source info from scan
        target = None
        ra = None
        dec = None

        if scanID >= 0 and scanID < len(parent.project.runs[0].scans):
            scan = parent.project.runs[0].scans[scanID]
            target = scan.target if hasattr(scan, 'target') else None
            # Get RA/Dec as formatted strings from the list display
            children = parent.listControl.get_children()
            if scanID < len(children):
                values = parent.listControl.item(children[scanID], 'values')
                if values and len(values) > 7:
                    ra = values[6]   # RA column
                    dec = values[7]  # Dec column

        # CalibratorSearch extends tk.Tk, doesn't take parent
        super().__init__(target=target, ra=ra, dec=dec)

    def initUI(self):
        """Override to add Accept button."""
        super().initUI()

        # Add Accept button if we have a valid scan
        if self.scanID >= 0:
            btn_frame = ttk.Frame(self)
            btn_frame.pack(fill=tk.X, pady=5)
            ttk.Button(btn_frame, text='Accept', command=self.onAccept).pack(side=tk.LEFT, padx=5)

    def onAccept(self):
        """Apply selected calibrator to scan."""
        selection = self.listControl.selection()
        if not selection:
            messagebox.showwarning('No Selection', 'Please select a calibrator.')
            return

        try:
            values = self.listControl.item(selection[0], 'values')
            if values and self.scanID >= 0:
                scan = self.idf_parent.project.runs[0].scans[self.scanID]

                # Calibrator search columns: name, ra, dec, dist, flux, size
                name = values[0]
                ra_str = values[1]
                dec_str = values[2]

                # Update target name
                scan.target = name

                # Parse and update RA (convert from HH:MM:SS.SS to decimal hours)
                ra_str = ra_str.replace('h', ':').replace('m', ':').replace('s', '')
                ra_fields = ra_str.split(':')
                ra_hours = float(ra_fields[0])
                ra_mins = float(ra_fields[1]) if len(ra_fields) > 1 else 0.0
                ra_secs = float(ra_fields[2]) if len(ra_fields) > 2 else 0.0
                scan.ra = ra_hours + ra_mins/60.0 + ra_secs/3600.0

                # Parse and update Dec (convert from sDD:MM:SS.S to decimal degrees)
                dec_str = dec_str.replace('d', ':').replace("'", ':').replace('"', '')
                dec_fields = dec_str.split(':')
                dec_sign = -1 if dec_str.startswith('-') else 1
                dec_degs = abs(float(dec_fields[0]))
                dec_mins = float(dec_fields[1]) if len(dec_fields) > 1 else 0.0
                dec_secs = float(dec_fields[2]) if len(dec_fields) > 2 else 0.0
                scan.dec = dec_sign * (dec_degs + dec_mins/60.0 + dec_secs/3600.0)

                # Update the scan
                scan.update()

                self.idf_parent._refreshScanList()
                self.idf_parent.edited = True
                self.idf_parent.setSaveButton()

            self.destroy()

        except Exception as e:
            messagebox.showerror('Error', str(e))


class HelpWindow(tk.Toplevel):
    """
    Help window displaying HTML documentation.

    Provides basic HTML rendering using tk.Text with tags, supporting:
    - Headers (h4, h6)
    - Bold, italic, underline text
    - Unordered and ordered lists
    - Internal anchor links (scrolls to section)
    - External links (opens in browser)
    """

    def __init__(self, parent):
        super().__init__(parent)
        self.title('Swarm GUI Handbook')
        self.geometry('600x500')
        self.idf_parent = parent

        # Store anchor positions for internal navigation
        self.anchors = {}

        self.initUI()

    def initUI(self):
        """Build the window UI."""
        main_frame = ttk.Frame(self, padding="5")
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Text widget for help content
        text_frame = ttk.Frame(main_frame)
        text_frame.pack(fill=tk.BOTH, expand=True)

        self.text = tk.Text(text_frame, wrap=tk.WORD, padx=10, pady=10)
        scrollbar = ttk.Scrollbar(text_frame, orient=tk.VERTICAL, command=self.text.yview)
        self.text.configure(yscrollcommand=scrollbar.set)

        self.text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # Configure text tags for HTML rendering
        self._configure_tags()

        # Load and render help content
        self._load_help()
        self.text.config(state='disabled')

    def _configure_tags(self):
        """Configure text tags for HTML-like formatting."""
        # Get default font and create variants
        default_font = tkfont.nametofont('TkDefaultFont')
        base_size = default_font.cget('size')
        base_family = default_font.cget('family')

        # Header tags
        self.text.tag_configure('h4', font=(base_family, base_size + 4, 'bold'),
                                spacing1=10, spacing3=5)
        self.text.tag_configure('h6', font=(base_family, base_size + 2, 'bold'),
                                spacing1=8, spacing3=3)

        # Text formatting tags
        self.text.tag_configure('bold', font=(base_family, base_size, 'bold'))
        self.text.tag_configure('italic', font=(base_family, base_size, 'italic'))
        self.text.tag_configure('underline', underline=True)

        # Superscript and subscript (adjust offset)
        self.text.tag_configure('sup', offset=4, font=(base_family, base_size - 2))
        self.text.tag_configure('sub', offset=-4, font=(base_family, base_size - 2))

        # List item tags (with indent)
        self.text.tag_configure('listitem', lmargin1=20, lmargin2=35)
        self.text.tag_configure('olitem', lmargin1=20, lmargin2=35)

        # Link tags
        self.text.tag_configure('link', foreground='blue', underline=True)
        self.text.tag_bind('link', '<Enter>', lambda e: self.text.config(cursor='hand2'))
        self.text.tag_bind('link', '<Leave>', lambda e: self.text.config(cursor=''))

    def _load_help(self):
        """Load and render help content from HTML file."""
        help_path = os.path.join(self.idf_parent.scriptPath, 'docs', 'swarm_help.html')
        try:
            with open(help_path, 'r') as f:
                content = f.read()
            self._render_html(content)
        except FileNotFoundError:
            self.text.insert('1.0', 'Help file not found.\n\nPlease refer to the LWA documentation at http://lwa.unm.edu')

    def _render_html(self, html_content):
        """Parse and render HTML content to the text widget."""

        # Process the HTML content
        # Normalize multiple whitespace to single space, collapse newlines
        html_content = re.sub(r'\s+', ' ', html_content)

        # Find all tags and text segments
        pattern = r'(<[^>]+>|[^<]+)'
        segments = re.findall(pattern, html_content)

        # Stack to track active formatting
        format_stack = []

        # Link tracking
        current_link_href = None
        link_counter = 0

        # Ordered list tracking
        ol_counter = 0
        in_ol = False

        # Track if we just inserted a newline (to avoid redundant spaces after newlines)
        last_was_newline = True

        for segment in segments:
            if not segment or not segment.strip():
                continue

            if segment.startswith('<'):
                # Strip whitespace from tags only
                segment = segment.strip()
                tag_match = re.match(r'<(/?)(\w+)([^>]*)/?>', segment)
                if not tag_match:
                    continue

                is_closing = tag_match.group(1) == '/'
                tag_name = tag_match.group(2).lower()
                tag_attrs = tag_match.group(3)

                # Handle different tags
                if tag_name in ('html', 'body'):
                    continue

                elif tag_name == 'h4':
                    if not is_closing:
                        format_stack.append('h4')
                    else:
                        if 'h4' in format_stack:
                            format_stack.remove('h4')
                        self.text.insert(tk.END, '\n')
                        last_was_newline = True

                elif tag_name == 'h6':
                    if not is_closing:
                        format_stack.append('h6')
                    else:
                        if 'h6' in format_stack:
                            format_stack.remove('h6')
                        self.text.insert(tk.END, '\n')
                        last_was_newline = True

                elif tag_name == 'b':
                    if not is_closing:
                        format_stack.append('bold')
                    elif 'bold' in format_stack:
                        format_stack.remove('bold')

                elif tag_name == 'i':
                    if not is_closing:
                        format_stack.append('italic')
                    elif 'italic' in format_stack:
                        format_stack.remove('italic')

                elif tag_name == 'u':
                    if not is_closing:
                        format_stack.append('underline')
                    elif 'underline' in format_stack:
                        format_stack.remove('underline')

                elif tag_name == 'sup':
                    if not is_closing:
                        format_stack.append('sup')
                    elif 'sup' in format_stack:
                        format_stack.remove('sup')

                elif tag_name == 'sub':
                    if not is_closing:
                        format_stack.append('sub')
                    elif 'sub' in format_stack:
                        format_stack.remove('sub')

                elif tag_name == 'ul':
                    if not is_closing:
                        self.text.insert(tk.END, '\n')
                        last_was_newline = True
                    else:
                        self.text.insert(tk.END, '\n')
                        last_was_newline = True

                elif tag_name == 'ol':
                    if not is_closing:
                        in_ol = True
                        ol_counter = 0
                        self.text.insert(tk.END, '\n')
                        last_was_newline = True
                    else:
                        in_ol = False
                        self.text.insert(tk.END, '\n')
                        last_was_newline = True

                elif tag_name == 'li':
                    if not is_closing:
                        if in_ol:
                            ol_counter += 1
                            self.text.insert(tk.END, f'\n  {ol_counter}. ', 'olitem')
                        else:
                            self.text.insert(tk.END, '\n  \u2022 ', 'listitem')
                        last_was_newline = False  # We have content after the bullet

                elif tag_name == 'br':
                    self.text.insert(tk.END, '\n')
                    last_was_newline = True

                elif tag_name == 'p':
                    if is_closing:
                        self.text.insert(tk.END, '\n\n')
                        last_was_newline = True

                elif tag_name == 'a':
                    if not is_closing:
                        # Extract href and name attributes
                        href_match = re.search(r'href=["\']([^"\']+)["\']', tag_attrs)
                        name_match = re.search(r'name=["\']([^"\']+)["\']', tag_attrs)

                        if name_match:
                            # This is an anchor definition - store current position
                            anchor_name = name_match.group(1)
                            self.anchors[anchor_name] = self.text.index(tk.END)

                        if href_match:
                            # This is a link - add 'link' to format stack and track href
                            current_link_href = href_match.group(1)
                            link_counter += 1
                            # Create unique tag for this specific link
                            link_tag = f'link_{link_counter}'
                            format_stack.append('link')
                            format_stack.append(link_tag)

                            # Pre-configure the click binding for this link tag
                            if current_link_href.startswith('#'):
                                anchor_name = current_link_href[1:]
                                self.text.tag_bind(link_tag, '<Button-1>',
                                    lambda e, a=anchor_name: self._scroll_to_anchor(a))
                            else:
                                url = current_link_href
                                self.text.tag_bind(link_tag, '<Button-1>',
                                    lambda e, u=url: webbrowser.open(u))
                    else:
                        # Remove link tags from format stack
                        if current_link_href:
                            # Remove the unique link tag
                            for tag in list(format_stack):
                                if tag.startswith('link_'):
                                    format_stack.remove(tag)
                                    break
                            # Remove 'link' tag
                            if 'link' in format_stack:
                                format_stack.remove('link')
                            current_link_href = None

            else:
                # This is text content - decode HTML entities
                text = segment
                text = text.replace('&quot;', '"')
                text = text.replace('&amp;', '&')
                text = text.replace('&lt;', '<')
                text = text.replace('&gt;', '>')
                text = text.replace('&nbsp;', ' ')

                if text.strip():
                    # Handle leading space - preserve it unless we just had a newline
                    if text.startswith(' ') and not last_was_newline:
                        leading_space = ' '
                        text = text.lstrip()
                    else:
                        leading_space = ''
                        text = text.lstrip()

                    # Preserve trailing space
                    trailing_space = ' ' if segment.endswith(' ') else ''
                    text = text.rstrip()

                    # Apply current formatting tags
                    tags = tuple(format_stack) if format_stack else ()
                    if leading_space:
                        self.text.insert(tk.END, leading_space)
                    self.text.insert(tk.END, text, tags)
                    if trailing_space:
                        self.text.insert(tk.END, trailing_space)

                    last_was_newline = False

    def _scroll_to_anchor(self, anchor_name):
        """Scroll to an internal anchor."""
        if anchor_name in self.anchors:
            self.text.see(self.anchors[anchor_name])


# Main entry point
def main():
    parser = argparse.ArgumentParser(
        description='Create and edit interferometer definition files (IDF) for the LWA Swarm',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument('filename', type=str, nargs='?',
                       help='IDF file to edit')
    parser.add_argument('-d', '--drsu-size', type=int, default=idf._DRSUCapacityTB,
                       help='DRSU capacity in TB')
    args = parser.parse_args()

    app = IDFCreator(args)

    if args.filename:
        app.parseFile(args.filename)

    app.mainloop()


if __name__ == '__main__':
    main()
