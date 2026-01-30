#!/usr/bin/env python3

import os
import re
import sys
import copy
import math
import ephem
import argparse
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, font as tkfont
from io import StringIO
from datetime import datetime, timedelta
from xml.etree import ElementTree

import conflict

import lsl
from lsl import astro
from lsl.common.ndp import fS
from lsl.common import stations
from lsl.astro import deg_to_dms, deg_to_hms, MJD_OFFSET, DJD_OFFSET
from lsl.reader.drx import FILTER_CODES as DRXFilters
from lsl.common import sdf
from lsl.misc import parser as aph

import matplotlib
matplotlib.use('TkAgg')
matplotlib.interactive(True)

from matplotlib.backends.backend_tkagg import NavigationToolbar2Tk, FigureCanvasTkAgg
from matplotlib.figure import Figure
from matplotlib.ticker import NullFormatter, NullLocator

__version__ = "0.6"
__author__ = "Jayce Dowell"


def pid_print(*args, **kwds):
    print(f"[{os.getpid()}]", *args, **kwds)


class ObservationListCtrl(ttk.Treeview):
    """
    Custom Treeview widget that supports checkboxes and inline editing.
    Replaces wx.ListCtrl with TextEditMixin, ChoiceMixIn, and CheckListCtrlMixin.
    """

    def __init__(self, parent, **kwargs):
        # Define columns
        self.columns = ('id', 'name', 'target', 'start', 'duration',
                       'ra', 'dec', 'freq1', 'freq2', 'filter', 'high_dr', 'comments')

        super().__init__(parent, columns=self.columns, show='headings', selectmode='extended', **kwargs)

        self.parent = parent
        self.nSelected = 0
        self._check_states = {}  # Track checkbox states by item id

        # Configure choice options for certain columns
        self.choice_options = {10: ['1','2','3','4','5','6','7'], 11: ['No','Yes']}

        # Setup columns
        self._setup_columns()

        # Bind events for editing
        self.bind('<Double-1>', self._on_double_click)
        self.bind('<<TreeviewSelect>>', self._on_selection_change)

        # For inline editing
        self._edit_entry = None
        self._edit_var = None
        self._edit_item = None
        self._edit_column = None

    def _setup_columns(self):
        """Setup column headings and widths."""
        headings = {
            'id': ('ID', 30),
            'name': ('Name', 100),
            'target': ('Target', 100),
            'start': ('Start', 140),
            'duration': ('Duration', 80),
            'ra': ('RA', 90),
            'dec': ('Dec', 90),
            'freq1': ('Freq. 1', 80),
            'freq2': ('Freq. 2', 80),
            'filter': ('Filter', 50),
            'high_dr': ('High DR', 60),
            'comments': ('Comments', 100),
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

        # Get column index (column is like '#1', '#2', etc.)
        col_idx = int(column.replace('#', '')) - 1

        # Check if this column is editable
        if col_idx == 0:  # ID column not editable
            return

        # Get the item's current values
        values = self.item(item, 'values')
        if not values:
            return

        # Check if RA/Dec columns (6, 7) are editable for this observation
        # In DRX mode, Sun, Jupiter, Moon, and STEPPED observations have non-editable RA/Dec
        if col_idx in (6, 7) and hasattr(self.parent, 'mode') and self.parent.mode == 'DRX':
            # Get the row index to check observation mode
            children = self.get_children()
            row_idx = None
            for i, child in enumerate(children):
                if child == item:
                    row_idx = i
                    break
            if row_idx is not None and hasattr(self.parent, 'project'):
                obs = self.parent.project.sessions[0].observations[row_idx]
                if obs.mode in ('TRK_SOL', 'TRK_JOV', 'TRK_LUN', 'STEPPED'):
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
            self._edit_var = tk.StringVar(value=current_value)
            self._edit_entry = tk.OptionMenu(self, self._edit_var, *self.choice_options[col_idx])
            self._edit_entry.config(relief='flat', borderwidth=0,
                                    highlightthickness=1, highlightcolor='#4a90d9',
                                    highlightbackground='#cccccc', anchor='w')
        else:
            # Use tk.Entry with flat relief to avoid double-border effect
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

        # Bind events
        self._edit_entry.bind('<Return>', self._finish_edit)
        self._edit_entry.bind('<Escape>', self._cancel_edit)
        self._edit_entry.bind('<FocusOut>', self._finish_edit)

    def _finish_edit(self, event=None):
        """Finish editing and save the value."""
        if not self._edit_entry or not self._edit_item:
            return

        # Get value from StringVar (OptionMenu) or Entry widget
        if hasattr(self, '_edit_var') and self._edit_var:
            new_value = self._edit_var.get()
        else:
            new_value = self._edit_entry.get()
        old_values = list(self.item(self._edit_item, 'values'))

        # Find the row index
        children = self.get_children()
        row_idx = None
        for i, child in enumerate(children):
            if child == self._edit_item:
                row_idx = i
                break

        col_idx = self._edit_col_idx

        self._edit_entry.destroy()
        self._edit_entry = None
        self._edit_var = None
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
            self._edit_var = None
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

    def CheckItem(self, item, check=True):
        """Set the check state of an item."""
        self._check_states[item] = check
        # Update visual representation if needed
        self._update_check_display(item)

    def IsChecked(self, item):
        """Check if an item is checked."""
        return self._check_states.get(item, False)

    def _update_check_display(self, item):
        """Update the visual display of check state."""
        # For simplicity, we'll use tags to show checked state
        if self._check_states.get(item, False):
            self.item(item, tags=('checked',))
        else:
            self.item(item, tags=())

    def setCheckDependant(self, index=None):
        """Update menu and toolbar states based on selection."""
        try:
            parent = self.parent
            if self.nSelected == 0:
                # Edit menu - disabled
                try:
                    parent.editmenu['cut'].entryconfig(parent.editmenu['cut_idx'], state='disabled')
                    parent.editmenu['copy'].entryconfig(parent.editmenu['copy_idx'], state='disabled')
                except (KeyError, AttributeError):
                    pass

                # Stepped observation edits - disabled
                try:
                    parent.obsmenu['steppedEdit'].entryconfig(parent.obsmenu['steppedEdit_idx'], state='disabled')
                    parent.toolbar_buttons['edit_stepped'].config(state='disabled')
                except (KeyError, AttributeError):
                    pass

                # Remove and resolve - disabled
                try:
                    parent.obsmenu['remove'].entryconfig(parent.obsmenu['remove_idx'], state='disabled')
                    parent.toolbar_buttons['remove'].config(state='disabled')
                    parent.obsmenu['resolve'].entryconfig(parent.obsmenu['resolve_idx'], state='disabled')
                except (KeyError, AttributeError):
                    pass

            elif self.nSelected == 1:
                # Edit menu - enabled
                try:
                    parent.editmenu['cut'].entryconfig(parent.editmenu['cut_idx'], state='normal')
                    parent.editmenu['copy'].entryconfig(parent.editmenu['copy_idx'], state='normal')
                except (KeyError, AttributeError):
                    pass

                # Stepped observation edits - check if STEPPED mode
                if index is not None:
                    try:
                        if parent.project.sessions[0].observations[index].mode == 'STEPPED':
                            parent.obsmenu['steppedEdit'].entryconfig(parent.obsmenu['steppedEdit_idx'], state='normal')
                            parent.toolbar_buttons['edit_stepped'].config(state='normal')
                        else:
                            parent.obsmenu['steppedEdit'].entryconfig(parent.obsmenu['steppedEdit_idx'], state='disabled')
                            parent.toolbar_buttons['edit_stepped'].config(state='disabled')
                    except (KeyError, AttributeError):
                        pass

                # Remove and resolve - enabled
                try:
                    parent.obsmenu['remove'].entryconfig(parent.obsmenu['remove_idx'], state='normal')
                    parent.toolbar_buttons['remove'].config(state='normal')
                    parent.obsmenu['resolve'].entryconfig(parent.obsmenu['resolve_idx'], state='normal')
                except (KeyError, AttributeError):
                    pass

            else:
                # Multiple selection
                try:
                    parent.editmenu['cut'].entryconfig(parent.editmenu['cut_idx'], state='normal')
                    parent.editmenu['copy'].entryconfig(parent.editmenu['copy_idx'], state='normal')
                except (KeyError, AttributeError):
                    pass

                try:
                    parent.obsmenu['steppedEdit'].entryconfig(parent.obsmenu['steppedEdit_idx'], state='disabled')
                    parent.toolbar_buttons['edit_stepped'].config(state='disabled')
                except (KeyError, AttributeError):
                    pass

                try:
                    parent.obsmenu['remove'].entryconfig(parent.obsmenu['remove_idx'], state='normal')
                    parent.toolbar_buttons['remove'].config(state='normal')
                    parent.obsmenu['resolve'].entryconfig(parent.obsmenu['resolve_idx'], state='disabled')
                except (KeyError, AttributeError):
                    pass
        except AttributeError:
            pass

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

        self._edit_entry = None
        self._edit_item = None
        self._edit_column = None

    def _setup_columns(self):
        """Setup column headings and widths."""
        headings = {
            'id': ('ID', 30),
            'c1': ('C1', 90),
            'c1_units': ('', 50),
            'c2': ('C2', 90),
            'c2_units': ('', 50),
            'duration': ('Duration', 100),
            'freq1': ('Freq. 1', 80),
            'freq2': ('Freq. 2', 80),
            'max_sn': ('MaxSN', 50),
            'remaining': ('Remaining', 100),
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
        if col_idx == 0:  # ID column not editable
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

        # Use tk.Entry with flat relief to avoid double-border effect
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
        values = list(self.item(self._edit_item, 'values'))
        values[self._edit_col_idx] = new_value
        self.item(self._edit_item, values=values)

        self._edit_entry.destroy()
        self._edit_entry = None

        if hasattr(self.parent, 'onEdit'):
            self.parent.onEdit(None)

    def _cancel_edit(self, event=None):
        """Cancel editing without saving."""
        if self._edit_entry:
            self._edit_entry.destroy()
            self._edit_entry = None

    def _on_selection_change(self, event):
        """Handle selection changes."""
        self.nSelected = len(self.selection())

    def CheckItem(self, item, check=True):
        self._check_states[item] = check

    def IsChecked(self, item):
        return self._check_states.get(item, False)

    def DeleteAllItems(self):
        for item in self.get_children():
            self.delete(item)
        self._check_states.clear()

    def GetItemCount(self):
        return len(self.get_children())


class PlotPanel(ttk.Frame):
    """
    Panel containing a matplotlib figure for displaying observation timelines.
    """

    def __init__(self, parent, **kwargs):
        super().__init__(parent, **kwargs)

        self.parent = parent

        # Create matplotlib figure
        self.figure = Figure(figsize=(6, 4), dpi=100)
        self.canvas = FigureCanvasTkAgg(self.figure, master=self)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

        # Navigation toolbar
        self.toolbar = NavigationToolbar2Tk(self.canvas, self)
        self.toolbar.update()

        self.ax = None

    def clear(self):
        """Clear the figure."""
        self.figure.clf()
        self.ax = self.figure.gca()

    def draw(self):
        """Redraw the canvas."""
        self.figure.tight_layout()
        self.canvas.draw()


class SDFCreator(tk.Tk):
    """
    Main application window for creating Session Definition Files.
    """

    def __init__(self, args):
        super().__init__()

        self.title('Session GUI')
        self.geometry('1100x550')

        self.station = stations.lwa1

        self.scriptPath = os.path.abspath(__file__)
        self.scriptPath = os.path.split(self.scriptPath)[0]

        self.dirname = ''
        self.toolbar_buttons = {}
        self.editmenu = {}
        self.obsmenu = {}

        self.buffer = None
        self.sdf_active = False  # Track whether an SDF has been created/loaded

        self.initSDF()
        self.initUI()
        self.initEvents()

        sdf._DRSUCapacityTB = args.drsu_size

        if args.filename is not None:
            self.filename = args.filename
            self.parseFile(self.filename)
            self.helpMenu.entryconfig(self.finfo_idx, state='normal')
            self.setSDFActive(True)
        else:
            self.filename = ''
            self.setMenuButtons('None')
            self.setSDFActive(False)

        self.edited = False
        self.setSaveButton()

    def initSDF(self):
        """
        Create an empty sdf.project instance to store all of the actual
        observations.
        """

        po = sdf.ProjectOffice()
        observer = sdf.Observer('', 0, first='', last='')
        project = sdf.Project(observer, '', '', project_office=po)
        session = sdf.Session('session_name', 0, observations=[])
        project.sessions = [session,]

        self.project = project
        self.mode = ''

        self.project.sessions[0].tbtSamples = 12000000
        self.project.sessions[0].drxGain = -1

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
        editMenu.add_command(label='Cut Selected Observation', command=self.onCut, state='disabled')
        self.editmenu['cut'] = editMenu
        self.editmenu['cut_idx'] = 0
        editMenu.add_command(label='Copy Selected Observation', command=self.onCopy, state='disabled')
        self.editmenu['copy'] = editMenu
        self.editmenu['copy_idx'] = 1
        editMenu.add_command(label='Paste Before Selected', command=self.onPasteBefore, state='disabled')
        self.editmenu['pasteBefore'] = editMenu
        self.editmenu['pasteBefore_idx'] = 2
        editMenu.add_command(label='Paste After Selected', command=self.onPasteAfter, state='disabled')
        self.editmenu['pasteAfter'] = editMenu
        self.editmenu['pasteAfter_idx'] = 3
        editMenu.add_command(label='Paste at End of List', command=self.onPasteEnd, state='disabled')
        self.editmenu['pasteEnd'] = editMenu
        self.editmenu['pasteEnd_idx'] = 4
        menubar.add_cascade(label='Edit', menu=editMenu)
        self.editMenu = editMenu

        # Observations menu
        obsMenu = tk.Menu(menubar, tearoff=0)
        obsMenu.add_command(label='Observer/Project Info.', command=self.onInfo)
        obsMenu.add_command(label='Scheduling', command=self.onSchedule)
        obsMenu.add_separator()

        # Add submenu
        addMenu = tk.Menu(obsMenu, tearoff=0)
        addMenu.add_command(label='TBT', command=self.onAddTBT)
        self.obsmenu['tbt_idx'] = 0
        addMenu.add_command(label='TBS', command=self.onAddTBS)
        self.obsmenu['tbs_idx'] = 1
        addMenu.add_separator()
        addMenu.add_command(label='DRX - RA/Dec', command=self.onAddDRXR)
        self.obsmenu['drx_radec_idx'] = 3
        addMenu.add_command(label='DRX - Solar', command=self.onAddDRXS)
        self.obsmenu['drx_solar_idx'] = 4
        addMenu.add_command(label='DRX - Jovian', command=self.onAddDRXJ)
        self.obsmenu['drx_jovian_idx'] = 5
        addMenu.add_command(label='DRX - Lunar', command=self.onAddDRXL)
        self.obsmenu['drx_lunar_idx'] = 6
        addMenu.add_command(label='DRX - Stepped - RA/Dec', command=self.onAddSteppedRADec)
        self.obsmenu['stepped_radec_idx'] = 7
        addMenu.add_command(label='DRX - Stepped - Az/Alt', command=self.onAddSteppedAzAlt)
        self.obsmenu['stepped_azalt_idx'] = 8
        addMenu.add_command(label='DRX - Edit Selected Stepped Obs.', command=self.onEditStepped, state='disabled')
        self.obsmenu['steppedEdit'] = addMenu
        self.obsmenu['steppedEdit_idx'] = 9
        obsMenu.add_cascade(label='Add', menu=addMenu)
        self.addMenu = addMenu  # Store reference to add menu

        obsMenu.add_command(label='Remove Selected', command=self.onRemove, state='disabled')
        self.obsmenu['remove'] = obsMenu
        self.obsmenu['remove_idx'] = 4
        obsMenu.add_command(label='Validate All', command=self.onValidate, accelerator='F5')
        obsMenu.add_separator()
        obsMenu.add_command(label='Resolve Selected', command=self.onResolve, state='disabled', accelerator='F3')
        self.obsmenu['resolve'] = obsMenu
        self.obsmenu['resolve_idx'] = 7
        obsMenu.add_command(label='Session at a Glance', command=self.onTimeseries)
        obsMenu.add_command(label='Advanced Settings', command=self.onAdvanced)
        menubar.add_cascade(label='Observations', menu=obsMenu)
        self.obsMenu = obsMenu

        # Data menu
        dataMenu = tk.Menu(menubar, tearoff=0)
        dataMenu.add_command(label='Estimated Data Volume', command=self.onVolume)
        menubar.add_cascade(label='Data', menu=dataMenu)

        # Help menu
        helpMenu = tk.Menu(menubar, tearoff=0)
        helpMenu.add_command(label='Session GUI Handbook', command=self.onHelp, accelerator='F1')
        helpMenu.add_command(label='Filter Codes', command=self.onFilterInfo)
        self.finfo_idx = 1
        helpMenu.add_separator()
        helpMenu.add_command(label='About', command=self.onAbout)
        menubar.add_cascade(label='Help', menu=helpMenu)
        self.helpMenu = helpMenu

        self.config(menu=menubar)

        # Toolbar
        toolbar_frame = ttk.Frame(self)
        toolbar_frame.pack(fill=tk.X, side=tk.TOP)

        # Load icons and create toolbar buttons
        self._create_toolbar(toolbar_frame)

        # Status bar
        self.statusbar = ttk.Label(self, text='', relief=tk.SUNKEN, anchor=tk.W)
        self.statusbar.pack(fill=tk.X, side=tk.BOTTOM)

        # Main panel with observation list
        main_frame = ttk.Frame(self)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Observation list
        list_frame = ttk.Frame(main_frame)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        self.listControl = ObservationListCtrl(list_frame)
        self.listControl.parent = self
        self.listControl.pack(fill=tk.BOTH, expand=True, side=tk.LEFT)

        # Scrollbar for list
        scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.listControl.yview)
        self.listControl.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(fill=tk.Y, side=tk.RIGHT)

    def _create_toolbar(self, parent):
        """Create the toolbar with icon buttons."""

        # Try to load icons, fall back to text if not available
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

        self.toolbar_buttons['tbt'] = make_button(parent, 'tbt', 'TBT', self.onAddTBT)
        self.toolbar_buttons['tbs'] = make_button(parent, 'tbs', 'TBS', self.onAddTBS)
        self.toolbar_buttons['drx_radec'] = make_button(parent, 'drx-radec', 'DRX-R', self.onAddDRXR)
        self.toolbar_buttons['drx_solar'] = make_button(parent, 'drx-solar', 'DRX-S', self.onAddDRXS)
        self.toolbar_buttons['drx_jovian'] = make_button(parent, 'drx-jovian', 'DRX-J', self.onAddDRXJ)
        self.toolbar_buttons['drx_lunar'] = make_button(parent, 'drx-lunar', 'DRX-L', self.onAddDRXL)
        self.toolbar_buttons['stepped_radec'] = make_button(parent, 'stepped-radec', 'ST-R', self.onAddSteppedRADec)
        self.toolbar_buttons['stepped_azalt'] = make_button(parent, 'stepped-azalt', 'ST-A', self.onAddSteppedAzAlt)
        self.toolbar_buttons['edit_stepped'] = make_button(parent, 'stepped-edit', 'ST-E', self.onEditStepped)
        self.toolbar_buttons['edit_stepped'].config(state='disabled')
        self.toolbar_buttons['remove'] = make_button(parent, 'remove', 'Rem', self.onRemove)
        self.toolbar_buttons['remove'].config(state='disabled')
        self.toolbar_buttons['validate'] = make_button(parent, 'validate', 'Val', self.onValidate)

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
        self.bind('<F5>', lambda e: self.onValidate())

        # Window close
        self.protocol("WM_DELETE_WINDOW", self.onQuit)

    def setSaveButton(self):
        """Update the save button state based on whether there are unsaved changes."""
        if self.edited:
            self.title('Session GUI *')
        else:
            self.title('Session GUI')

    def setSDFActive(self, active):
        """
        Enable or disable GUI elements based on whether an SDF is active.
        When no SDF is created/loaded, most controls should be disabled.
        """
        self.sdf_active = active
        state = 'normal' if active else 'disabled'

        # Mapping from toolbar button keys to menu indices
        menu_idx_map = {
            'tbw': 'tbw_idx',
            'tbf': 'tbf_idx',
            'tbn': 'tbn_idx',
            'drx_radec': 'drx_radec_idx',
            'drx_solar': 'drx_solar_idx',
            'drx_jovian': 'drx_jovian_idx',
            'drx_lunar': 'drx_lunar_idx',
            'stepped_radec': 'stepped_radec_idx',
            'stepped_azalt': 'stepped_azalt_idx',
        }

        if active:
            # When enabling, respect mode-based button restrictions
            self.setMenuButtons(self.mode if self.mode else 'None')
        else:
            # When disabling, disable all observation toolbar buttons and menu items
            obs_buttons = ['tbw', 'tbf', 'tbn', 'drx_radec', 'drx_solar', 'drx_jovian',
                           'drx_lunar', 'stepped_radec', 'stepped_azalt', 'validate']
            for key in obs_buttons:
                if key in self.toolbar_buttons:
                    self.toolbar_buttons[key].config(state='disabled')
                # Also disable menu items
                if hasattr(self, 'addMenu') and key in menu_idx_map:
                    try:
                        self.addMenu.entryconfig(self.obsmenu[menu_idx_map[key]], state='disabled')
                    except:
                        pass

        # Save buttons
        self.toolbar_buttons['save'].config(state=state)
        self.toolbar_buttons['save_as'].config(state=state)
        self.fileMenu.entryconfig(self.save_menu_idx, state=state)

        # Observation menu items (Info, Scheduling, Add submenu, etc.)
        # Items 0=Info, 1=Scheduling, 2=separator, 3=Add submenu, 4=Remove, 5=Validate, 6=separator, 7=Resolve, 8=Session at a Glance, 9=Advanced
        for idx in (0, 1, 3, 5, 8, 9):
            try:
                self.obsMenu.entryconfig(idx, state=state)
            except:
                pass

        # Data menu
        try:
            self.master.nametowidget(self.master.cget('menu')).entryconfig('Data', state=state)
        except:
            pass

    def setMenuButtons(self, mode):
        """
        Given a mode of observation (TBT, TBS, TRK_RADEC, etc.), update the
        various menu items in 'Observations' and the toolbar buttons.
        """

        mode = mode.lower()
        self.mode = mode.upper() if mode != 'none' else ''

        # Mapping from toolbar button keys to menu indices
        menu_idx_map = {
            'tbt': 'tbt_idx',
            'tbs': 'tbs_idx',
            'drx_radec': 'drx_radec_idx',
            'drx_solar': 'drx_solar_idx',
            'drx_jovian': 'drx_jovian_idx',
            'drx_lunar': 'drx_lunar_idx',
            'stepped_radec': 'stepped_radec_idx',
            'stepped_azalt': 'stepped_azalt_idx',
        }

        # If no SDF is active, keep all observation buttons and menu items disabled
        if not self.sdf_active:
            obs_buttons = ['tbt', 'tbs', 'drx_radec', 'drx_solar', 'drx_jovian',
                           'drx_lunar', 'stepped_radec', 'stepped_azalt']
            for key in obs_buttons:
                if key in self.toolbar_buttons:
                    self.toolbar_buttons[key].config(state='disabled')
                # Also disable menu items
                if hasattr(self, 'addMenu') and key in menu_idx_map:
                    try:
                        self.addMenu.entryconfig(self.obsmenu[menu_idx_map[key]], state='disabled')
                    except:
                        pass
            return

        # Define button states based on mode
        states = {
            'tbt': {'tbt': 'normal', 'tbs': 'normal',
                   'drx_radec': 'disabled', 'drx_solar': 'disabled', 'drx_jovian': 'disabled', 'drx_lunar': 'disabled',
                   'stepped_radec': 'disabled', 'stepped_azalt': 'disabled'},
            'tbs': {'tbt': 'normal', 'tbs': 'normal',
                   'drx_radec': 'disabled', 'drx_solar': 'disabled', 'drx_jovian': 'disabled', 'drx_lunar': 'disabled',
                   'stepped_radec': 'disabled', 'stepped_azalt': 'disabled'},
            'drx': {'tbt': 'disabled', 'tbs': 'disabled',
                   'drx_radec': 'normal', 'drx_solar': 'normal', 'drx_jovian': 'normal', 'drx_lunar': 'normal',
                   'stepped_radec': 'normal', 'stepped_azalt': 'normal'},
            'none': {'tbt': 'normal', 'tbs': 'normal',
                    'drx_radec': 'normal', 'drx_solar': 'normal', 'drx_jovian': 'normal', 'drx_lunar': 'normal',
                    'stepped_radec': 'normal', 'stepped_azalt': 'normal'},
        }

        current_states = states.get(mode, states['none'])

        # Update toolbar buttons and menu items
        for key, state in current_states.items():
            if key in self.toolbar_buttons:
                self.toolbar_buttons[key].config(state=state)
            # Also update menu items
            if hasattr(self, 'addMenu') and key in menu_idx_map:
                try:
                    self.addMenu.entryconfig(self.obsmenu[menu_idx_map[key]], state=state)
                except:
                    pass

    def _getCurrentDateString(self):
        """
        Function to get a datetime string, in UTC, for a new observation.
        """

        tStop = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        if self.listControl.GetItemCount() > 0:
            if self.mode == 'DRX':
                _, tStop = sdf.get_observation_start_stop(self.project.sessions[0].observations[-1])
            elif self.mode == 'TBS':
                _, tStop = sdf.get_observation_start_stop(self.project.sessions[0].observations[-1])
                tStop += timedelta(seconds=20)

        return 'UTC %i %02i %02i %02i:%02i:%06.3f' % (tStop.year, tStop.month, tStop.day, tStop.hour, tStop.minute, tStop.second+tStop.microsecond/1e6)

    def _getDefaultFilter(self):
        """
        Function to get the default value for the filter code.
        """
        return 7

    def addColumns(self):
        """
        Add the various columns to the main window based on the type of observations.
        """

        # Configure columns based on mode
        if self.mode == 'TBT':
            columns = ('id', 'name', 'target', 'comments', 'start', 'duration', 'frequency', 'filter')
            headings = {'id': ('ID', 40), 'name': ('Name', 100), 'target': ('Target', 100),
                       'comments': ('Comments', 150), 'start': ('Start', 180),
                       'duration': ('Duration', 100), 'frequency': ('Frequency (MHz)', 100), 'filter': ('Filter Code', 70)}
            self.columnMap = ['id', 'name', 'target', 'comments', 'start', 'duration', 'frequency1', 'filter']
        elif self.mode == 'TBS':
            columns = ('id', 'name', 'target', 'comments', 'start', 'duration', 'frequency', 'filter')
            headings = {'id': ('ID', 40), 'name': ('Name', 100), 'target': ('Target', 100),
                       'comments': ('Comments', 150), 'start': ('Start', 180),
                       'duration': ('Duration', 100), 'frequency': ('Frequency (MHz)', 100), 'filter': ('Filter Code', 70)}
            self.columnMap = ['id', 'name', 'target', 'comments', 'start', 'duration', 'frequency1', 'filter']
        elif self.mode == 'DRX':
            columns = ('id', 'name', 'target', 'comments', 'start', 'duration', 'ra', 'dec', 'freq1', 'freq2', 'filter', 'high_dr')
            headings = {'id': ('ID', 40), 'name': ('Name', 100), 'target': ('Target', 100),
                       'comments': ('Comments', 120), 'start': ('Start', 180),
                       'duration': ('Duration', 100), 'ra': ('RA (J2000)', 100), 'dec': ('Dec (J2000)', 100),
                       'freq1': ('Tuning 1 (MHz)', 100), 'freq2': ('Tuning 2 (MHz)', 100),
                       'filter': ('Filter', 50), 'high_dr': ('High DR', 60)}
            self.columnMap = ['id', 'name', 'target', 'comments', 'start', 'duration', 'ra', 'dec', 'frequency1', 'frequency2', 'filter', 'high_dr']
        else:
            columns = ('id', 'name', 'target', 'comments', 'start')
            headings = {'id': ('ID', 40), 'name': ('Name', 100), 'target': ('Target', 100),
                       'comments': ('Comments', 150), 'start': ('Start', 180)}
            self.columnMap = ['id', 'name', 'target', 'comments', 'start']

        # Update treeview columns
        self.listControl['columns'] = columns
        for col in columns:
            text, width = headings.get(col, (col, 80))
            self.listControl.heading(col, text=text)
            self.listControl.column(col, width=width, minwidth=30)

    def addObservation(self, obs, id, update=False):
        """
        Add an observation to a particular location in the observation list.
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

        # Build values based on mode
        if self.mode == 'TBS':
            values = (str(id), obs.name, obs.target,
                     obs.comments if obs.comments else 'None provided',
                     obs.start, obs.duration,
                     "%.6f" % (obs.freq1 * fS / 2**32 / 1e6),
                     "%i" % obs.filter)
        elif self.mode == 'TBT':
            if obs.mode == 'TBT':
                values = (str(id), obs.name, obs.target,
                         obs.comments if obs.comments else 'None provided',
                         obs.start, obs.duration, "--", "--")
            elif obs.mode == 'TBS':
                values = (str(id), obs.name, obs.target,
                         obs.comments if obs.comments else 'None provided',
                         obs.start, obs.duration,
                         "%.6f" % (obs.freq1 * fS / 2**32 / 1e6),
                         "%i" % obs.filter)
            else:
                values = (str(id), obs.name, obs.target,
                         obs.comments if obs.comments else 'None provided',
                         obs.start, obs.duration, "--", "--")
        elif self.mode == 'DRX':
            if obs.mode == 'STEPPED':
                ra_str = "STEPPED"
                dec_str = "RA/Dec" if obs.is_radec else "Az/Alt"
                freq1_str = "--"
                freq2_str = "--"
                high_dr_str = "--"
            else:
                freq1_str = "%.6f" % (obs.freq1 * fS / 2**32 / 1e6)
                freq2_str = "%.6f" % (obs.freq2 * fS / 2**32 / 1e6)
                high_dr_str = "Yes" if obs.high_dr else "No"

                if obs.mode == 'TRK_SOL':
                    ra_str = "Sun"
                    dec_str = "--"
                elif obs.mode == 'TRK_JOV':
                    ra_str = "Jupiter"
                    dec_str = "--"
                elif obs.mode == 'TRK_LUN':
                    ra_str = "Moon"
                    dec_str = "--"
                else:
                    ra_str = dec2sexstr(obs.ra, signed=False)
                    dec_str = dec2sexstr(obs.dec, signed=True)

            values = (str(id), obs.name, obs.target,
                     obs.comments if obs.comments else 'None provided',
                     obs.start, obs.duration,
                     ra_str, dec_str, freq1_str, freq2_str,
                     "%i" % obs.filter, high_dr_str)
        else:
            values = (str(id), obs.name, obs.target,
                     obs.comments if obs.comments else 'None provided',
                     obs.start)

        if update:
            # Find and update existing item
            children = self.listControl.get_children()
            if id - 1 < len(children):
                item = children[id - 1]
                self.listControl.item(item, values=values)
        else:
            # Insert new item
            self.listControl.insert('', id - 1, values=values)

    def onNew(self, event=None):
        """Create a new SD session."""
        if self.edited:
            result = messagebox.askyesno('Confirm New',
                'The current session definition file has changes that have not been saved.\n\nStart a new session anyways?',
                default=messagebox.NO)
            if not result:
                return False

        self.filename = ''
        self.edited = True
        self.setSaveButton()

        self.setMenuButtons('None')
        self.listControl.DeleteAllItems()
        self.listControl.nSelected = 0
        self.listControl.setCheckDependant()
        self.initSDF()
        ObserverInfo(self)

        # Enable GUI after creating new SDF
        self.setSDFActive(True)
        self.helpMenu.entryconfig(self.finfo_idx, state='normal')

    def onLoad(self, event=None):
        """Load an existing SD file."""
        if self.edited:
            result = messagebox.askyesno('Confirm Open',
                'The current session definition file has changes that have not been saved.\n\nOpen a new file anyways?',
                default=messagebox.NO)
            if not result:
                return False

        filename = filedialog.askopenfilename(
            initialdir=self.dirname,
            filetypes=[('SDF Files', '*.sdf *.txt'), ('All Files', '*.*')]
        )
        if filename:
            self.dirname = os.path.dirname(filename)
            self.filename = filename
            self.parseFile(filename)

            self.edited = False
            self.setSaveButton()

            # Enable GUI after loading SDF
            self.setSDFActive(True)
            self.helpMenu.entryconfig(self.finfo_idx, state='normal')

    def onSave(self, event=None):
        """Save the current observation to a file."""
        if self.filename == '':
            self.onSaveAs(event)
        else:
            if not self.onValidate(confirmValid=False):
                return False
            self._saveFile(self.filename)
            self.edited = False
            self.setSaveButton()

    def onSaveAs(self, event=None):
        """Save to a new file."""
        filename = filedialog.asksaveasfilename(
            initialdir=self.dirname,
            defaultextension='.sdf',
            filetypes=[('SDF Files', '*.sdf'), ('Text Files', '*.txt'), ('All Files', '*.*')]
        )
        if filename:
            self.dirname = os.path.dirname(filename)
            self.filename = filename
            if not self.onValidate(confirmValid=False):
                return False
            self._saveFile(filename)
            self.edited = False
            self.setSaveButton()

    def onQuit(self, event=None):
        """Quit the application."""
        if self.edited:
            result = messagebox.askyesnocancel(
                'Unsaved Changes',
                'The current session definition file has unsaved changes. Do you want to save before quitting?'
            )
            if result is None:  # Cancel
                return
            elif result:  # Yes - save first
                self.onSave()
        self.destroy()

    def onCut(self, event=None):
        """Cut selected observation(s) to the buffer."""
        self.onCopy(event)
        self.onRemove(event)

    def onCopy(self, event=None):
        """Copy selected observation(s) to the buffer."""
        selection = self.listControl.selection()
        if not selection:
            return

        self.buffer = []
        children = self.listControl.get_children()
        for i, child in enumerate(children):
            if child in selection:
                self.buffer.append(copy.deepcopy(self.project.sessions[0].observations[i]))

        # Enable paste options
        self.editMenu.entryconfig(self.editmenu['pasteBefore_idx'], state='normal')
        self.editMenu.entryconfig(self.editmenu['pasteAfter_idx'], state='normal')
        self.editMenu.entryconfig(self.editmenu['pasteEnd_idx'], state='normal')

        self.statusbar.config(text=f'Copied {len(self.buffer)} observation(s)')

    def onPasteBefore(self, event=None):
        """Paste before selected observation."""
        if not self.buffer:
            return

        selection = self.listControl.selection()
        if not selection:
            return

        children = self.listControl.get_children()
        firstChecked = None
        for i, child in enumerate(children):
            if child in selection:
                firstChecked = i
                break

        if firstChecked is not None:
            for obs in self.buffer[::-1]:
                cObs = copy.deepcopy(obs)
                self.project.sessions[0].observations.insert(firstChecked, cObs)
                self.addObservation(self.project.sessions[0].observations[firstChecked], firstChecked + 1)

            self._renumberObservations()
            self.edited = True
            self.setSaveButton()

    def onPasteAfter(self, event=None):
        """Paste after selected observation."""
        if not self.buffer:
            return

        selection = self.listControl.selection()
        if not selection:
            return

        children = self.listControl.get_children()
        lastChecked = None
        for i, child in enumerate(children):
            if child in selection:
                lastChecked = i

        if lastChecked is not None:
            id = lastChecked + 1
            for obs in self.buffer[::-1]:
                cObs = copy.deepcopy(obs)
                self.project.sessions[0].observations.insert(id, cObs)
                self.addObservation(self.project.sessions[0].observations[id], id + 1)

            self._renumberObservations()
            self.edited = True
            self.setSaveButton()

    def onPasteEnd(self, event=None):
        """Paste at end of observation list."""
        if not self.buffer:
            return

        for obs in self.buffer:
            cObs = copy.deepcopy(obs)
            id = len(self.project.sessions[0].observations)
            self.project.sessions[0].observations.append(cObs)
            self.addObservation(cObs, id + 1)

        self._renumberObservations()
        self.edited = True
        self.setSaveButton()

    def _renumberObservations(self):
        """Re-number all observations in the list."""
        for i, child in enumerate(self.listControl.get_children()):
            values = list(self.listControl.item(child, 'values'))
            values[0] = str(i + 1)
            self.listControl.item(child, values=values)

    def onInfo(self, event=None):
        """Open observer/project info dialog."""
        ObserverInfo(self)

    def onSchedule(self, event=None):
        """Open scheduling window."""
        ScheduleWindow(self)

    def onAddTBT(self, event=None):
        """Add a TBT observation to the list."""
        if self.mode == '':
            self.mode = 'TBT'
            self.setMenuButtons('TBT')
            self.addColumns()

        id = self.listControl.GetItemCount() + 1
        samples = self.project.sessions[0].tbtSamples
        self.project.sessions[0].observations.append(
            sdf.TBT('tbt-%i' % id, 'All-Sky', self._getCurrentDateString(), samples)
        )
        self.addObservation(self.project.sessions[0].observations[-1], id)

        self.edited = True
        self.setSaveButton()

    def onAddTBS(self, event=None):
        """Add a TBS observation to the list."""
        if self.mode == '':
            self.mode = 'TBS'
            self.setMenuButtons('TBS')
            self.addColumns()

        id = self.listControl.GetItemCount() + 1
        self.project.sessions[0].observations.append(
            sdf.TBS('tbs-%i' % id, 'All-Sky', self._getCurrentDateString(), '00:00:00.000', 38e6, self._getDefaultFilter())
        )
        self.addObservation(self.project.sessions[0].observations[-1], id)

        self.edited = True
        self.setSaveButton()

    def onAddDRXR(self, event=None):
        """Add a tracking RA/Dec (DRX) observation to the list."""
        if self.mode == '':
            self.mode = 'DRX'
            self.setMenuButtons('DRX')
            self.addColumns()

        id = self.listControl.GetItemCount() + 1
        gain = self.project.sessions[0].drxGain
        self.project.sessions[0].observations.append(
            sdf.DRX('drx-%i' % id, 'target-%i' % id, self._getCurrentDateString(), '00:00:00.000', 0.0, 0.0, 42e6, 74e6, self._getDefaultFilter(), gain=gain)
        )
        self.addObservation(self.project.sessions[0].observations[-1], id)

        self.edited = True
        self.setSaveButton()

    def onAddDRXS(self, event=None):
        """Add a tracking Sun (DRX) observation to the list."""
        if self.mode == '':
            self.mode = 'DRX'
            self.setMenuButtons('DRX')
            self.addColumns()

        id = self.listControl.GetItemCount() + 1
        gain = self.project.sessions[0].drxGain
        self.project.sessions[0].observations.append(
            sdf.Solar('solar-%i' % id, 'Sun', self._getCurrentDateString(), '00:00:00.000', 42e6, 74e6, self._getDefaultFilter(), gain=gain)
        )
        self.addObservation(self.project.sessions[0].observations[-1], id)

        self.edited = True
        self.setSaveButton()

    def onAddDRXJ(self, event=None):
        """Add a tracking Jupiter (DRX) observation to the list."""
        if self.mode == '':
            self.mode = 'DRX'
            self.setMenuButtons('DRX')
            self.addColumns()

        id = self.listControl.GetItemCount() + 1
        gain = self.project.sessions[0].drxGain
        self.project.sessions[0].observations.append(
            sdf.Jovian('jovian-%i' % id, 'Jupiter', self._getCurrentDateString(), '00:00:00.000', 42e6, 74e6, self._getDefaultFilter(), gain=gain)
        )
        self.addObservation(self.project.sessions[0].observations[-1], id)

        self.edited = True
        self.setSaveButton()

    def onAddDRXL(self, event=None):
        """Add a tracking Moon (DRX) observation to the list."""
        if self.mode == '':
            self.mode = 'DRX'
            self.setMenuButtons('DRX')
            self.addColumns()

        id = self.listControl.GetItemCount() + 1
        gain = self.project.sessions[0].drxGain
        self.project.sessions[0].observations.append(
            sdf.Lunar('lunar-%i' % id, 'Moon', self._getCurrentDateString(), '00:00:00.000', 42e6, 74e6, self._getDefaultFilter(), gain=gain)
        )
        self.addObservation(self.project.sessions[0].observations[-1], id)

        self.edited = True
        self.setSaveButton()

    def onAddSteppedRADec(self, event=None):
        """Add a RA/Dec stepped observation block."""
        if self.mode == '':
            self.mode = 'DRX'
            self.setMenuButtons('DRX')
            self.addColumns()

        id = self.listControl.GetItemCount() + 1
        gain = self.project.sessions[0].drxGain
        self.project.sessions[0].observations.append(
            sdf.Stepped('stps-%i' % id, 'radec-%i' % id, self._getCurrentDateString(), self._getDefaultFilter(), is_radec=True, gain=gain)
        )
        self.addObservation(self.project.sessions[0].observations[-1], id)

        self.edited = True
        self.setSaveButton()

    def onAddSteppedAzAlt(self, event=None):
        """Add a Az/Alt stepped observation block."""
        if self.mode == '':
            self.mode = 'DRX'
            self.setMenuButtons('DRX')
            self.addColumns()

        id = self.listControl.GetItemCount() + 1
        gain = self.project.sessions[0].drxGain
        self.project.sessions[0].observations.append(
            sdf.Stepped('stps-%i' % id, 'azalt-%i' % id, self._getCurrentDateString(), self._getDefaultFilter(), is_radec=False, gain=gain)
        )
        self.addObservation(self.project.sessions[0].observations[-1], id)

        self.edited = True
        self.setSaveButton()

    def onEditStepped(self, event=None):
        """Edit steps for the currently selected stepped observation."""
        selection = self.listControl.selection()
        if not selection:
            return False

        # Find the index of the selected item
        children = self.listControl.get_children()
        whichChecked = None
        for i, child in enumerate(children):
            if child in selection:
                whichChecked = i
                break

        if whichChecked is None:
            return False

        if self.project.sessions[0].observations[whichChecked].mode != 'STEPPED':
            messagebox.showwarning('Invalid Selection', 'Selected observation is not a STEPPED observation.')
            return False

        SteppedWindow(self, whichChecked)

    def onRemove(self, event=None):
        """Remove selected observations from the list."""
        selection = self.listControl.selection()
        if not selection:
            return

        # Get indices of selected items (in reverse order to avoid index shifting)
        children = list(self.listControl.get_children())
        indices = []
        for i, child in enumerate(children):
            if child in selection:
                indices.append(i)

        # Remove in reverse order
        for i in sorted(indices, reverse=True):
            self.listControl.delete(children[i])
            del self.project.sessions[0].observations[i]

        # Re-number remaining rows
        for i, child in enumerate(self.listControl.get_children()):
            values = list(self.listControl.item(child, 'values'))
            values[0] = str(i + 1)
            self.listControl.item(child, values=values)

        self.listControl.nSelected = 0
        self.listControl.setCheckDependant()

        self.edited = True
        self.setSaveButton()

    def onValidate(self, event=None, confirmValid=True):
        """Validate all observations."""
        self.statusbar.config(text='Validating...')
        self.update()

        if len(self.project.sessions[0].observations) == 0:
            if confirmValid:
                messagebox.showwarning('Validation', 'No observations to validate.')
            return False

        # Loop through observations and validate
        all_valid = True
        for i, obs in enumerate(self.project.sessions[0].observations):
            pid_print(f"Validating observation {i+1}")
            valid = obs.validate(verbose=True)
            if not valid:
                all_valid = False
                # Mark invalid rows (could add tag support here)
                children = self.listControl.get_children()
                if i < len(children):
                    self.listControl.item(children[i], tags=('invalid',))

        # Configure tag for invalid items
        self.listControl.tag_configure('invalid', foreground='red')

        # Global validation
        sys.stdout = StringIO()
        if self.project.validate(verbose=True):
            full_msg = sys.stdout.getvalue()[:-1] if sys.stdout.getvalue() else ''
            sys.stdout.close()
            sys.stdout = sys.__stdout__
            self.statusbar.config(text='Validation complete - all valid')
            if confirmValid:
                messagebox.showinfo('Validation', 'Congratulations, you have a valid set of observations.')
            return True
        else:
            full_msg = sys.stdout.getvalue()[:-1] if sys.stdout.getvalue() else ''
            sys.stdout.close()
            sys.stdout = sys.__stdout__

            # Print errors
            for line in full_msg.split('\n'):
                if 'Error' in line:
                    pid_print(line)

            self.statusbar.config(text='Validation failed - see console for details')
            if confirmValid:
                if all_valid:
                    messagebox.showwarning('Validation', 'All observations are valid, but there are errors in the session setup. See the console for details.')
                else:
                    messagebox.showerror('Validation', 'Validation failed. See the console for details.')
            return False

    def onResolve(self, event=None):
        """Resolve selected observation target."""
        ResolveTarget(self)

    def onTimeseries(self, event=None):
        """Show session at a glance."""
        SessionDisplay(self)

    def onAdvanced(self, event=None):
        """Open advanced settings."""
        AdvancedInfo(self)

    def onVolume(self, event=None):
        """Show estimated data volume."""
        VolumeInfo(self)

    def onHelp(self, event=None):
        """Show help window."""
        HelpWindow(self)

    def onFilterInfo(self, event=None):
        """Display filter codes for TBS and DRX modes."""
        def units(value):
            if value >= 1e6:
                return float(value) / 1e6, 'MHz'
            elif value >= 1e3:
                return float(value) / 1e3, 'kHz'
            else:
                return float(value), 'Hz'

        if self.mode == 'TBS':
            filterInfo = "TBS Filter Codes:\n"
            filterInfo += "  8:  200.000 kHz\n"
        elif self.mode == 'DRX':
            filterInfo = "DRX Filter Codes:\n"
            for dk, dv in DRXFilters.items():
                if dk > 7:
                    continue
                dv, du = units(dv)
                filterInfo += f"  {dk}:  {dv:.3f} {du}\n"
        else:
            filterInfo = 'No filters defined for the current mode.'

        messagebox.showinfo('Filter Codes', filterInfo)

    def onAbout(self, event=None):
        """Show about dialog."""
        about_text = f"""Session GUI
Version {__version__}

GUI for creating and editing Session Definition Files (SDFs) for the LWA.

LSL Version: {lsl.version.version}

Developer: {__author__}
Website: http://lwa.unm.edu"""
        messagebox.showinfo('About Session GUI', about_text)

    def onEdit(self, event=None):
        """Handle edit events from the list control."""
        self.edited = True
        self.setSaveButton()

    def onCellEdit(self, row_idx, col_idx, new_value):
        """Handle cell edit events and update the underlying observation."""
        if row_idx >= len(self.project.sessions[0].observations):
            return

        obs = self.project.sessions[0].observations[row_idx]

        # Map column index to observation attribute based on mode
        try:
            if col_idx == 1:  # Name
                obs.name = new_value
            elif col_idx == 2:  # Target
                obs.target = new_value
            elif col_idx == 3:  # Comments
                obs.comments = new_value if new_value != 'None provided' else None
            elif col_idx == 4:  # Start time
                obs.start = new_value
            elif col_idx == 5:  # Duration
                obs.duration = new_value
            elif self.mode == 'DRX':
                if col_idx == 6:  # RA
                    if new_value not in ('Sun', 'Jupiter', 'Moon', 'STEPPED', '--'):
                        obs.ra = self._parseRA(new_value)
                elif col_idx == 7:  # Dec
                    if new_value not in ('--', 'RA/Dec', 'Az/Alt'):
                        obs.dec = self._parseDec(new_value)
                elif col_idx == 8:  # Freq1
                    if new_value != '--':
                        obs.freq1 = int(float(new_value) * 1e6 * 2**32 / fS)
                elif col_idx == 9:  # Freq2
                    if new_value != '--':
                        obs.freq2 = int(float(new_value) * 1e6 * 2**32 / fS)
                elif col_idx == 10:  # Filter
                    obs.filter = int(new_value)
                elif col_idx == 11:  # High DR
                    obs.high_dr = (new_value.lower() == 'yes')
            elif self.mode in ('TBS', 'TBT'):
                if col_idx == 6:  # Frequency
                    if new_value != '--':
                        obs.freq1 = int(float(new_value) * 1e6 * 2**32 / fS)
                elif col_idx == 7:  # Filter
                    if new_value != '--':
                        obs.filter = int(new_value)

            obs.update()
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
        sign = 1
        if text.startswith('-'):
            sign = -1
        text = text.replace('+', '').replace('-', '')
        fields = text.split(':')
        fields = [float(f) for f in fields]
        value = 0
        for f, d in zip(fields, [1.0, 60.0, 3600.0]):
            value += (f / d)
        value *= sign
        if value < -90 or value > 90:
            raise ValueError("Dec value must be -90 <= dec <= 90")
        return value

    def parseFile(self, filename):
        """
        Given a filename, parse the file using the sdf.parse_sdf() method and
        update all of the various aspects of the GUI.
        """

        self.statusbar.config(text=f'Loading {filename}...')
        self.update()

        self.listControl.DeleteAllItems()
        self.listControl.nSelected = 0
        self.listControl.setCheckDependant()
        self.initSDF()

        pid_print(f"Parsing file '{filename}'")
        try:
            self.project = sdf.parse_sdf(filename)
        except Exception as e:
            messagebox.showerror('Parse Error', f"Cannot parse provided SDF: {str(e)}")
            return

        if len(self.project.sessions) == 0:
            messagebox.showerror('Parse Error', "Provided SDF does not define any sessions")
            return

        if len(self.project.sessions[0].observations) == 0:
            messagebox.showerror('Parse Error', "Provided SDF does not define any observations")
            return

        # Determine mode from first observation
        first_obs_mode = self.project.sessions[0].observations[0].mode
        if first_obs_mode == 'TBT':
            self.mode = 'TBT'
        elif first_obs_mode == 'TBS':
            self.mode = 'TBS'
        elif first_obs_mode.startswith('TRK') or first_obs_mode == 'STEPPED':
            self.mode = 'DRX'
        else:
            self.mode = ''

        self.setMenuButtons(self.mode)

        # Set session parameters
        try:
            self.project.sessions[0].tbtSamples = self.project.sessions[0].observations[0].samples
        except:
            if self.mode == 'TBS':
                self.project.sessions[0].tbtSamples = 12000000

        self.project.sessions[0].drxGain = self.project.sessions[0].observations[0].gain

        # Setup columns and add observations
        self.addColumns()
        for i, obs in enumerate(self.project.sessions[0].observations):
            self.addObservation(obs, i + 1)

        self.statusbar.config(text=f'Loaded {filename} - {len(self.project.sessions[0].observations)} observations')

    def _saveFile(self, filename):
        """Save the current project to a file."""
        self.statusbar.config(text=f'Saving {filename}...')
        self.update()

        try:
            with open(filename, 'w') as fh:
                fh.write(self.project.render())
            self.statusbar.config(text=f'Saved {filename}')
        except IOError as err:
            messagebox.showerror('Save Error', f"Error saving to '{filename}':\n{str(err)}")

    def displayError(self, error, details=None, title=None):
        """Display an error dialog."""
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
    Dialog for entering observer and project information, including session type
    and data return method.
    """

    def __init__(self, parent):
        super().__init__(parent)

        self.title('Observer Information')
        self.sdf_parent = parent  # Use different name to avoid tkinter conflicts
        self.transient(parent)

        # Load preferences
        self.preferences = self._load_preferences()

        self.initUI()

        self.geometry('650x650')
        self.grab_set()

    def _load_preferences(self):
        """Load preferences from ~/.sessionGUI file."""
        preferences = {}
        try:
            prefs_file = os.path.join(os.path.expanduser('~'), '.sessionGUI')
            with open(prefs_file) as ph:
                for line in ph:
                    line = line.strip()
                    if len(line) < 3 or line[0] == '#':
                        continue
                    parts = line.split(None, 1)
                    if len(parts) == 2:
                        preferences[parts[0]] = parts[1]
        except:
            pass
        return preferences

    def initUI(self):
        """Build the dialog UI."""
        # Create scrollable frame
        canvas = tk.Canvas(self)
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas, padding="10")

        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )

        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        main_frame = scrollable_frame

        # Observer Information section
        obs_frame = ttk.LabelFrame(main_frame, text='Observer Information', padding="5")
        obs_frame.pack(fill=tk.X, pady=(0, 10))

        ttk.Label(obs_frame, text='ID Number:').grid(row=0, column=0, sticky=tk.E, padx=5, pady=2)
        self.obs_id = ttk.Entry(obs_frame, width=20)
        self.obs_id.grid(row=0, column=1, sticky=tk.W+tk.E, padx=5, pady=2)

        ttk.Label(obs_frame, text='First Name:').grid(row=1, column=0, sticky=tk.E, padx=5, pady=2)
        self.obs_first = ttk.Entry(obs_frame, width=30)
        self.obs_first.grid(row=1, column=1, sticky=tk.W+tk.E, padx=5, pady=2)

        ttk.Label(obs_frame, text='Last Name:').grid(row=2, column=0, sticky=tk.E, padx=5, pady=2)
        self.obs_last = ttk.Entry(obs_frame, width=30)
        self.obs_last.grid(row=2, column=1, sticky=tk.W+tk.E, padx=5, pady=2)

        obs_frame.columnconfigure(1, weight=1)

        # Project Information section
        proj_frame = ttk.LabelFrame(main_frame, text='Project Information', padding="5")
        proj_frame.pack(fill=tk.X, pady=(0, 10))

        ttk.Label(proj_frame, text='ID Code:').grid(row=0, column=0, sticky=tk.E, padx=5, pady=2)
        self.proj_id = ttk.Entry(proj_frame, width=20)
        self.proj_id.grid(row=0, column=1, sticky=tk.W+tk.E, padx=5, pady=2)

        ttk.Label(proj_frame, text='Title:').grid(row=1, column=0, sticky=tk.E, padx=5, pady=2)
        self.proj_title = ttk.Entry(proj_frame, width=50)
        self.proj_title.grid(row=1, column=1, sticky=tk.W+tk.E, padx=5, pady=2)

        ttk.Label(proj_frame, text='Comments:').grid(row=2, column=0, sticky=tk.NE, padx=5, pady=2)
        self.proj_comments = tk.Text(proj_frame, width=50, height=3)
        self.proj_comments.grid(row=2, column=1, sticky=tk.W+tk.E, padx=5, pady=2)

        proj_frame.columnconfigure(1, weight=1)

        # Session Information section
        sess_frame = ttk.LabelFrame(main_frame, text='Session Information', padding="5")
        sess_frame.pack(fill=tk.X, pady=(0, 10))

        ttk.Label(sess_frame, text='ID Number:').grid(row=0, column=0, sticky=tk.E, padx=5, pady=2)
        self.sess_id = ttk.Entry(sess_frame, width=20)
        self.sess_id.grid(row=0, column=1, sticky=tk.W+tk.E, padx=5, pady=2)

        ttk.Label(sess_frame, text='Title:').grid(row=1, column=0, sticky=tk.E, padx=5, pady=2)
        self.sess_name = ttk.Entry(sess_frame, width=50)
        self.sess_name.grid(row=1, column=1, sticky=tk.W+tk.E, padx=5, pady=2)

        ttk.Label(sess_frame, text='Comments:').grid(row=2, column=0, sticky=tk.NE, padx=5, pady=2)
        self.sess_comments = tk.Text(sess_frame, width=50, height=3)
        self.sess_comments.grid(row=2, column=1, sticky=tk.W+tk.E, padx=5, pady=2)

        # Session Type
        ttk.Label(sess_frame, text='Session Type:').grid(row=3, column=0, sticky=tk.NE, padx=5, pady=2)
        type_frame = ttk.Frame(sess_frame)
        type_frame.grid(row=3, column=1, sticky=tk.W, padx=5, pady=2)

        self.sess_type = tk.StringVar(value='DRX')
        self.tbt_rb = ttk.Radiobutton(type_frame, text='Transient Buffer-Triggered (TBT)',
                                       variable=self.sess_type, value='TBT', command=self._on_type_change)
        self.tbt_rb.pack(anchor=tk.W)
        self.tbs_rb = ttk.Radiobutton(type_frame, text='Transient Buffer-Streaming (TBS)',
                                       variable=self.sess_type, value='TBS', command=self._on_type_change)
        self.tbs_rb.pack(anchor=tk.W)
        self.drx_rb = ttk.Radiobutton(type_frame, text='Beam Forming (DRX)',
                                       variable=self.sess_type, value='DRX', command=self._on_type_change)
        self.drx_rb.pack(anchor=tk.W)

        # Data Return Method
        ttk.Label(sess_frame, text='Data Return Method:').grid(row=4, column=0, sticky=tk.NE, padx=5, pady=2)
        drm_frame = ttk.Frame(sess_frame)
        drm_frame.grid(row=4, column=1, sticky=tk.W, padx=5, pady=2)

        self.drm_var = tk.StringVar(value='USB')
        self.usb_rb = ttk.Radiobutton(drm_frame, text='Bare Drive(s)',
                                       variable=self.drm_var, value='USB', command=self._on_drm_change)
        self.usb_rb.grid(row=0, column=0, sticky=tk.W)
        self.ucf_rb = ttk.Radiobutton(drm_frame, text='Copy to UCF',
                                       variable=self.drm_var, value='UCF', command=self._on_drm_change)
        self.ucf_rb.grid(row=1, column=0, sticky=tk.W)

        ttk.Label(drm_frame, text='UCF Username:').grid(row=1, column=1, sticky=tk.E, padx=5)
        self.ucf_username = ttk.Entry(drm_frame, width=15)
        self.ucf_username.grid(row=1, column=2, sticky=tk.W, padx=5)
        self.ucf_username.config(state='disabled')

        # Beam Processing (DR Spectrometer)
        ttk.Label(sess_frame, text='Beam Processing:').grid(row=5, column=0, sticky=tk.NE, padx=5, pady=2)
        bp_frame = ttk.Frame(sess_frame)
        bp_frame.grid(row=5, column=1, sticky=tk.W, padx=5, pady=2)

        self.drs_var = tk.BooleanVar(value=False)
        self.drs_cb = ttk.Checkbutton(bp_frame, text='DR spectrometer',
                                       variable=self.drs_var, command=self._on_drs_change)
        self.drs_cb.grid(row=0, column=0, sticky=tk.W)

        ttk.Label(bp_frame, text='Channels:').grid(row=0, column=1, sticky=tk.E, padx=5)
        self.nchn_entry = ttk.Entry(bp_frame, width=8)
        self.nchn_entry.grid(row=0, column=2, sticky=tk.W, padx=2)
        self.nchn_entry.insert(0, '1024')
        self.nchn_entry.config(state='disabled')

        ttk.Label(bp_frame, text='FFTs/int.:').grid(row=0, column=3, sticky=tk.E, padx=5)
        self.nint_entry = ttk.Entry(bp_frame, width=8)
        self.nint_entry.grid(row=0, column=4, sticky=tk.W, padx=2)
        self.nint_entry.insert(0, '768')
        self.nint_entry.config(state='disabled')

        ttk.Label(bp_frame, text='Data Products:').grid(row=1, column=1, sticky=tk.E, padx=5)
        self.spc_type = tk.StringVar(value='Linear')
        self.linear_rb = ttk.Radiobutton(bp_frame, text='Linear',
                                          variable=self.spc_type, value='Linear')
        self.linear_rb.grid(row=1, column=2, sticky=tk.W, padx=2)
        self.stokes_rb = ttk.Radiobutton(bp_frame, text='Stokes',
                                          variable=self.spc_type, value='Stokes')
        self.stokes_rb.grid(row=1, column=3, sticky=tk.W, padx=2)
        self.linear_rb.config(state='disabled')
        self.stokes_rb.config(state='disabled')

        sess_frame.columnconfigure(1, weight=1)

        # Buttons
        btn_frame = ttk.Frame(main_frame)
        btn_frame.pack(fill=tk.X, pady=(10, 0))

        ttk.Button(btn_frame, text='Save Defaults', command=self.onSaveDefaults).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text='OK', command=self.onOK).pack(side=tk.RIGHT, padx=5)
        ttk.Button(btn_frame, text='Cancel', command=self.onCancel).pack(side=tk.RIGHT, padx=5)

        # Populate fields from parent project
        self._populate_fields()

    def _populate_fields(self):
        """Populate fields from parent's project data and preferences."""
        proj = self.sdf_parent.project

        # Observer info
        if proj.observer.id != 0:
            self.obs_id.insert(0, str(proj.observer.id))
        elif 'ObserverID' in self.preferences:
            self.obs_id.insert(0, self.preferences['ObserverID'])

        if proj.observer.first != '':
            self.obs_first.insert(0, proj.observer.first)
            self.obs_last.insert(0, proj.observer.last)
        else:
            if proj.observer.name != '':
                self.obs_first.insert(0, proj.observer.name)
            elif 'ObserverFirstName' in self.preferences:
                self.obs_first.insert(0, self.preferences.get('ObserverFirstName', ''))
                self.obs_last.insert(0, self.preferences.get('ObserverLastName', ''))

        # Project info
        if proj.id != '':
            self.proj_id.insert(0, proj.id)
        elif 'ProjectID' in self.preferences:
            self.proj_id.insert(0, self.preferences['ProjectID'])

        if proj.name != '':
            self.proj_title.insert(0, proj.name)
        elif 'ProjectName' in self.preferences:
            self.proj_title.insert(0, self.preferences['ProjectName'])

        if proj.comments and proj.comments != '':
            self.proj_comments.insert('1.0', proj.comments.replace(';;', '\n'))

        # Session info
        if proj.sessions[0].id != '':
            self.sess_id.insert(0, str(proj.sessions[0].id))
        if proj.sessions[0].name != '':
            self.sess_name.insert(0, proj.sessions[0].name)
        if proj.sessions[0].comments and proj.sessions[0].comments != '':
            comments = sdf.UCF_USERNAME_RE.sub('', proj.sessions[0].comments)
            self.sess_comments.insert('1.0', comments.replace(';;', '\n'))

        # Session type
        if self.sdf_parent.mode != '':
            self.sess_type.set(self.sdf_parent.mode)
            # Disable type selection if mode is already set
            self.tbt_rb.config(state='disabled')
            self.tbs_rb.config(state='disabled')
            self.drx_rb.config(state='disabled')

        # Data return method
        if proj.sessions[0].data_return_method == 'USB Harddrives':
            self.drm_var.set('USB')
        else:
            self.drm_var.set('UCF')
            self.ucf_username.config(state='normal')
            mtch = None
            if proj.sessions[0].comments is not None:
                mtch = sdf.UCF_USERNAME_RE.search(proj.sessions[0].comments)
            if mtch is not None:
                self.ucf_username.insert(0, mtch.group('username'))

        # DR Spectrometer
        if proj.sessions[0].spc_setup[0] != 0 and proj.sessions[0].spc_setup[1] != 0:
            self.drs_var.set(True)
            self.nchn_entry.config(state='normal')
            self.nchn_entry.delete(0, tk.END)
            self.nchn_entry.insert(0, str(proj.sessions[0].spc_setup[0]))
            self.nint_entry.config(state='normal')
            self.nint_entry.delete(0, tk.END)
            self.nint_entry.insert(0, str(proj.sessions[0].spc_setup[1]))
            self.linear_rb.config(state='normal')
            self.stokes_rb.config(state='normal')

            mt = proj.sessions[0].spc_metatag
            if mt is not None:
                junk, mt = mt.split('=', 1)
                mt = mt.replace('}', '')
                if mt in ('XXYY', 'CRCI', 'XXCRCIYY'):
                    self.spc_type.set('Linear')
                else:
                    self.spc_type.set('Stokes')

        # Disable DR spec for non-DRX modes
        if self.sdf_parent.mode in ('TBT', 'TBS'):
            self.drs_cb.config(state='disabled')

    def _on_type_change(self):
        """Handle session type change."""
        sess_type = self.sess_type.get()
        if sess_type == 'DRX':
            self.drs_cb.config(state='normal')
        else:
            self.drs_cb.config(state='disabled')
            self.drs_var.set(False)
            self._on_drs_change()

    def _on_drm_change(self):
        """Handle data return method change."""
        if self.drm_var.get() == 'UCF':
            self.ucf_username.config(state='normal')
        else:
            self.ucf_username.config(state='disabled')

    def _on_drs_change(self):
        """Handle DR spectrometer checkbox change."""
        if self.drs_var.get():
            self.nchn_entry.config(state='normal')
            self.nint_entry.config(state='normal')
            self.linear_rb.config(state='normal')
            self.stokes_rb.config(state='normal')
        else:
            self.nchn_entry.config(state='disabled')
            self.nint_entry.config(state='disabled')
            self.linear_rb.config(state='disabled')
            self.stokes_rb.config(state='disabled')

    def displayError(self, message, details=None, title='Error'):
        """Display an error dialog."""
        if details:
            messagebox.showerror(title, f"{message}\n\nDetails:\n{details}")
        else:
            messagebox.showerror(title, message)

    def onOK(self):
        """Save changes and close."""
        # Validate observer ID
        try:
            obs_id = int(self.obs_id.get() or 0)
            if obs_id < 1:
                self.displayError('Observer ID must be greater than zero', title='Observer ID Error')
                return
        except ValueError as err:
            self.displayError('Observer ID must be numeric', details=str(err), title='Observer ID Error')
            return

        # Validate session ID
        try:
            sess_id = int(self.sess_id.get() or 0)
            if sess_id < 1:
                self.displayError('Session ID must be greater than zero', title='Session ID Error')
                return
        except ValueError as err:
            self.displayError('Session ID must be numeric', details=str(err), title='Session ID Error')
            return

        proj = self.sdf_parent.project

        # Save observer info
        proj.observer.id = obs_id
        proj.observer.first = self.obs_first.get()
        proj.observer.last = self.obs_last.get()
        proj.observer.join_name()

        # Save project info
        proj.id = self.proj_id.get()
        proj.name = self.proj_title.get()
        proj.comments = self.proj_comments.get('1.0', 'end-1c').replace('\n', ';;')

        # Save session info
        proj.sessions[0].id = sess_id
        proj.sessions[0].name = self.sess_name.get()
        proj.sessions[0].comments = self.sess_comments.get('1.0', 'end-1c').replace('\n', ';;')

        # Data return method
        if self.drm_var.get() == 'USB':
            proj.sessions[0].data_return_method = 'USB Harddrives'
            proj.sessions[0].spc_setup = [0, 0]
            proj.sessions[0].spc_metatag = None
        else:
            proj.sessions[0].data_return_method = 'UCF'
            tempc = sdf.UCF_USERNAME_RE.sub('', proj.sessions[0].comments)
            proj.sessions[0].comments = tempc + ';;ucfuser:%s' % self.ucf_username.get()

            proj.sessions[0].spc_setup = [0, 0]
            proj.sessions[0].spc_metatag = None

            mtch = sdf.UCF_USERNAME_RE.search(proj.sessions[0].comments)
            if mtch is None:
                self.displayError('Cannot find UCF username needed for copying data to the UCF.',
                                 title='Missing UCF User Name')
                return

        # DR Spectrometer
        if self.drs_var.get():
            try:
                nchn = int(self.nchn_entry.get())
                nint = int(self.nint_entry.get())
            except ValueError:
                self.displayError('Channels and FFTs/int. must be numeric', title='DR Spectrometer Error')
                return
            proj.sessions[0].spc_setup = [nchn, nint]

            mt = proj.sessions[0].spc_metatag
            if mt is None:
                isLinear = True
                proj.sessions[0].spc_metatag = '{Stokes=XXYY}'
            else:
                junk, mt = mt.split('=', 1)
                mt = mt.replace('}', '')
                isLinear = mt in ('XXYY', 'CRCI', 'XXCRCIYY')

            if self.spc_type.get() == 'Linear' and not isLinear:
                proj.sessions[0].spc_metatag = '{Stokes=XXYY}'
            if self.spc_type.get() == 'Stokes' and isLinear:
                proj.sessions[0].spc_metatag = '{Stokes=IQUV}'

        # Session type
        sess_type = self.sess_type.get()
        if sess_type == 'TBT':
            self.sdf_parent.mode = 'TBT'
            proj.sessions[0].include_station_smib = True
        elif sess_type == 'TBS':
            self.sdf_parent.mode = 'TBS'
            proj.sessions[0].include_station_smib = True
        else:
            self.sdf_parent.mode = 'DRX'

        self.sdf_parent.setMenuButtons(self.sdf_parent.mode)
        if len(self.sdf_parent.listControl.get_children()) == 0:
            self.sdf_parent.addColumns()

        # Cleanup comments
        _cleanup0RE = re.compile(r';;;;+')
        _cleanup1RE = re.compile(r'(^;;)|(;;$)')
        if proj.comments:
            proj.comments = _cleanup0RE.sub(';;', proj.comments)
            proj.comments = _cleanup1RE.sub('', proj.comments)
        if proj.sessions[0].comments:
            proj.sessions[0].comments = _cleanup0RE.sub(';;', proj.sessions[0].comments)
            proj.sessions[0].comments = _cleanup1RE.sub('', proj.sessions[0].comments)

        self.sdf_parent.edited = True
        self.sdf_parent.setSaveButton()

        self.destroy()

    def onCancel(self):
        """Close without saving."""
        self.destroy()

    def onSaveDefaults(self):
        """Save current values as defaults."""
        preferences = self._load_preferences()

        try:
            preferences['ObserverID'] = int(self.obs_id.get())
        except (TypeError, ValueError):
            pass

        first = self.obs_first.get()
        if first:
            preferences['ObserverFirstName'] = first
        last = self.obs_last.get()
        if last:
            preferences['ObserverLastName'] = last

        pID = self.proj_id.get()
        if pID:
            preferences['ProjectID'] = pID
        pTitle = self.proj_title.get()
        if pTitle:
            preferences['ProjectName'] = pTitle

        # Write preferences file
        try:
            prefs_file = os.path.join(os.path.expanduser('~'), '.sessionGUI')
            with open(prefs_file, 'w') as ph:
                ph.write("# sessionGUI preferences file\n")
                for key, value in preferences.items():
                    ph.write(f"{key} {value}\n")
            messagebox.showinfo('Defaults Saved', 'Preferences have been saved.')
        except Exception as e:
            self.displayError(f'Could not save preferences: {e}')


class AdvancedInfo(tk.Toplevel):
    """
    Dialog for advanced settings including MCS, ASP, and mode-specific options.
    """

    def __init__(self, parent):
        super().__init__(parent)

        self.title('Advanced Settings')
        self.sdf_parent = parent  # Use different name to avoid tkinter conflicts
        self.transient(parent)

        self.initUI()
        
        self.geometry('625x625')
        self.grab_set()

    def _timeToCombo(self, time_val):
        """Convert time in minutes to combo box string."""
        if time_val == -1:
            return 'MCS Decides'
        elif time_val == 0:
            return 'Never'
        elif time_val == 1:
            return '1 minute'
        elif time_val == 5:
            return '5 minutes'
        elif time_val == 15:
            return '15 minutes'
        elif time_val == 30:
            return '30 minutes'
        elif time_val == 60:
            return '1 hour'
        else:
            return 'MCS Decides'

    def _parseTimeCombo(self, combo_val):
        """Convert combo box string to time in minutes."""
        if combo_val == 'MCS Decides':
            return -1
        elif combo_val == 'Never':
            return 0
        else:
            parts = combo_val.split(None, 1)
            t = int(parts[0])
            u = parts[1] if len(parts) > 1 else ''
            if 'minute' in u:
                return t
            else:
                return t * 60

    def _parseGainCombo(self, combo_val):
        """Convert combo box string to gain value."""
        if combo_val == 'MCS Decides':
            return -1
        return int(combo_val)

    def initUI(self):
        """Build the dialog UI."""
        # Create scrollable frame
        canvas = tk.Canvas(self)
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas, padding="10")

        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )

        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        main_frame = scrollable_frame

        # Define choices
        intervals = ['MCS Decides', 'Never', '1 minute', '5 minutes', '15 minutes', '30 minutes', '1 hour']
        aspFilters = ['MCS Decides', 'Split', 'Full', 'Reduced', 'Off', 'Split @ 3MHz', 'Full @ 3MHz']
        aspAttn = ['MCS Decides'] + [str(i) for i in range(16)]
        drxGain = ['MCS Decides'] + [str(i) for i in range(13)]
        drxBeam = ['MCS Decides'] + ['%i' % i for i in range(1, 5)]

        # MCS-Specific Information
        mcs_frame = ttk.LabelFrame(main_frame, text='MCS-Specific Information', padding="5")
        mcs_frame.pack(fill=tk.X, pady=(0, 10))

        # MIB Recording Period
        ttk.Label(mcs_frame, text='MIB Recording Period:').grid(row=0, column=0, columnspan=6, sticky=tk.W, pady=5)

        ttk.Label(mcs_frame, text='ASP:').grid(row=1, column=0, sticky=tk.E, padx=2)
        self.mrpASP = ttk.Combobox(mcs_frame, values=intervals, state='readonly', width=12)
        self.mrpASP.grid(row=1, column=1, padx=2)
        self.mrpASP.set(self._timeToCombo(self.sdf_parent.project.sessions[0].record_mib.get('ASP', -1)))

        ttk.Label(mcs_frame, text='NDP:').grid(row=1, column=2, sticky=tk.E, padx=2)
        self.mrpDP = ttk.Combobox(mcs_frame, values=intervals, state='readonly', width=12)
        self.mrpDP.grid(row=1, column=3, padx=2)
        self.mrpDP.set(self._timeToCombo(self.sdf_parent.project.sessions[0].record_mib.get('NDP', -1)))

        ttk.Label(mcs_frame, text='DR1-DR4:').grid(row=1, column=4, sticky=tk.E, padx=2)
        self.mrpDR = ttk.Combobox(mcs_frame, values=intervals, state='readonly', width=12)
        self.mrpDR.grid(row=1, column=5, padx=2)
        self.mrpDR.set(self._timeToCombo(self.sdf_parent.project.sessions[0].record_mib.get('DR1', -1)))

        ttk.Label(mcs_frame, text='SHL:').grid(row=2, column=0, sticky=tk.E, padx=2)
        self.mrpSHL = ttk.Combobox(mcs_frame, values=intervals, state='readonly', width=12)
        self.mrpSHL.grid(row=2, column=1, padx=2)
        self.mrpSHL.set(self._timeToCombo(self.sdf_parent.project.sessions[0].record_mib.get('SHL', -1)))

        ttk.Label(mcs_frame, text='MCS:').grid(row=2, column=2, sticky=tk.E, padx=2)
        self.mrpMCS = ttk.Combobox(mcs_frame, values=intervals, state='readonly', width=12)
        self.mrpMCS.grid(row=2, column=3, padx=2)
        self.mrpMCS.set(self._timeToCombo(self.sdf_parent.project.sessions[0].record_mib.get('MCS', -1)))

        # MIB Update Period
        ttk.Label(mcs_frame, text='MIB Update Period:').grid(row=3, column=0, columnspan=6, sticky=tk.W, pady=(10, 5))

        ttk.Label(mcs_frame, text='ASP:').grid(row=4, column=0, sticky=tk.E, padx=2)
        self.mupASP = ttk.Combobox(mcs_frame, values=intervals, state='readonly', width=12)
        self.mupASP.grid(row=4, column=1, padx=2)
        self.mupASP.set(self._timeToCombo(self.sdf_parent.project.sessions[0].update_mib.get('ASP', -1)))

        ttk.Label(mcs_frame, text='NDP:').grid(row=4, column=2, sticky=tk.E, padx=2)
        self.mupDP = ttk.Combobox(mcs_frame, values=intervals, state='readonly', width=12)
        self.mupDP.grid(row=4, column=3, padx=2)
        self.mupDP.set(self._timeToCombo(self.sdf_parent.project.sessions[0].update_mib.get('NDP', -1)))

        ttk.Label(mcs_frame, text='DR1-DR4:').grid(row=4, column=4, sticky=tk.E, padx=2)
        self.mupDR = ttk.Combobox(mcs_frame, values=intervals, state='readonly', width=12)
        self.mupDR.grid(row=4, column=5, padx=2)
        self.mupDR.set(self._timeToCombo(self.sdf_parent.project.sessions[0].update_mib.get('DR1', -1)))

        ttk.Label(mcs_frame, text='SHL:').grid(row=5, column=0, sticky=tk.E, padx=2)
        self.mupSHL = ttk.Combobox(mcs_frame, values=intervals, state='readonly', width=12)
        self.mupSHL.grid(row=5, column=1, padx=2)
        self.mupSHL.set(self._timeToCombo(self.sdf_parent.project.sessions[0].update_mib.get('SHL', -1)))

        ttk.Label(mcs_frame, text='MCS:').grid(row=5, column=2, sticky=tk.E, padx=2)
        self.mupMCS = ttk.Combobox(mcs_frame, values=intervals, state='readonly', width=12)
        self.mupMCS.grid(row=5, column=3, padx=2)
        self.mupMCS.set(self._timeToCombo(self.sdf_parent.project.sessions[0].update_mib.get('MCS', -1)))

        # Logging options
        self.schLog_var = tk.BooleanVar(value=self.sdf_parent.project.sessions[0].include_mcssch_log)
        self.schLog = ttk.Checkbutton(mcs_frame, text='Include relevant MCS/Scheduler Log', variable=self.schLog_var)
        self.schLog.grid(row=6, column=0, columnspan=3, sticky=tk.W, pady=5)

        self.exeLog_var = tk.BooleanVar(value=self.sdf_parent.project.sessions[0].include_mcsexe_log)
        self.exeLog = ttk.Checkbutton(mcs_frame, text='Include relevant MCS/Executive Log', variable=self.exeLog_var)
        self.exeLog.grid(row=6, column=3, columnspan=3, sticky=tk.W, pady=5)

        self.incSMIB_var = tk.BooleanVar(value=self.sdf_parent.project.sessions[0].include_station_smib)
        self.incSMIB = ttk.Checkbutton(mcs_frame, text='Include station static MIB', variable=self.incSMIB_var)
        self.incSMIB.grid(row=7, column=0, columnspan=3, sticky=tk.W, pady=2)

        self.incDESG_var = tk.BooleanVar(value=self.sdf_parent.project.sessions[0].include_station_design)
        self.incDESG = ttk.Checkbutton(mcs_frame, text='Include design and calibration information', variable=self.incDESG_var)
        self.incDESG.grid(row=7, column=3, columnspan=3, sticky=tk.W, pady=2)

        # ASP-Specific Information
        asp_frame = ttk.LabelFrame(main_frame, text='ASP-Specific Information', padding="5")
        asp_frame.pack(fill=tk.X, pady=(0, 10))

        # Get current ASP settings
        aspFlt_val = -1
        aspAT1_val = -1
        aspAT2_val = -1
        aspATS_val = -1
        try:
            if len(self.sdf_parent.project.sessions[0].observations) > 0:
                aspFlt_val = self.sdf_parent.project.sessions[0].observations[0].asp_filter[0]
                aspAT1_val = self.sdf_parent.project.sessions[0].observations[0].asp_atten_1[0]
                aspAT2_val = self.sdf_parent.project.sessions[0].observations[0].asp_atten_2[0]
                aspATS_val = self.sdf_parent.project.sessions[0].observations[0].asp_atten_split[0]
        except (IndexError, AttributeError):
            pass

        ttk.Label(asp_frame, text='Filter Mode Setting:').grid(row=0, column=0, sticky=tk.E, padx=5, pady=2)
        self.aspFlt = ttk.Combobox(asp_frame, values=aspFilters, state='readonly', width=15)
        self.aspFlt.grid(row=0, column=1, padx=5, pady=2)
        aspFltRev = {-1: 'MCS Decides', 0: 'Split', 1: 'Full', 2: 'Reduced', 3: 'Off', 4: 'Split @ 3MHz', 5: 'Full @ 3MHz'}
        self.aspFlt.set(aspFltRev.get(aspFlt_val, 'MCS Decides'))
        ttk.Label(asp_frame, text='for all inputs').grid(row=0, column=2, sticky=tk.W, padx=5)

        ttk.Label(asp_frame, text='First Attenuator Setting:').grid(row=1, column=0, sticky=tk.E, padx=5, pady=2)
        self.aspAT1 = ttk.Combobox(asp_frame, values=aspAttn, state='readonly', width=15)
        self.aspAT1.grid(row=1, column=1, padx=5, pady=2)
        self.aspAT1.set('MCS Decides' if aspAT1_val == -1 else str(aspAT1_val))
        ttk.Label(asp_frame, text='for all inputs').grid(row=1, column=2, sticky=tk.W, padx=5)

        ttk.Label(asp_frame, text='Second Attenuator Setting:').grid(row=2, column=0, sticky=tk.E, padx=5, pady=2)
        self.aspAT2 = ttk.Combobox(asp_frame, values=aspAttn, state='readonly', width=15)
        self.aspAT2.grid(row=2, column=1, padx=5, pady=2)
        self.aspAT2.set('MCS Decides' if aspAT2_val == -1 else str(aspAT2_val))
        ttk.Label(asp_frame, text='for all inputs').grid(row=2, column=2, sticky=tk.W, padx=5)

        ttk.Label(asp_frame, text='Split Attenuator Setting:').grid(row=3, column=0, sticky=tk.E, padx=5, pady=2)
        self.aspATS = ttk.Combobox(asp_frame, values=aspAttn, state='readonly', width=15)
        self.aspATS.grid(row=3, column=1, padx=5, pady=2)
        self.aspATS.set('MCS Decides' if aspATS_val == -1 else str(aspATS_val))
        ttk.Label(asp_frame, text='for all inputs').grid(row=3, column=2, sticky=tk.W, padx=5)

        # TBT-Specific Information (conditional)
        self.tbtSamp = None
        if self.sdf_parent.mode == 'TBT':
            tbt_frame = ttk.LabelFrame(main_frame, text='TBT-Specific Information', padding="5")
            tbt_frame.pack(fill=tk.X, pady=(0, 10))

            ttk.Label(tbt_frame, text='Samples:').grid(row=0, column=0, sticky=tk.E, padx=5, pady=2)
            self.tbtSamp = ttk.Entry(tbt_frame, width=15)
            self.tbtSamp.grid(row=0, column=1, padx=5, pady=2)
            try:
                self.tbtSamp.insert(0, str(self.sdf_parent.project.sessions[0].observations[0].samples))
            except (IndexError, AttributeError):
                self.tbtSamp.insert(0, '12000000')
            ttk.Label(tbt_frame, text='per capture').grid(row=0, column=2, sticky=tk.W, padx=5)

        # DRX-Specific Information (conditional)
        self.drxGain = None
        self.drxBeam = None
        if self.sdf_parent.mode == 'DRX':
            drx_frame = ttk.LabelFrame(main_frame, text='DRX-Specific Information', padding="5")
            drx_frame.pack(fill=tk.X, pady=(0, 10))

            ttk.Label(drx_frame, text='Gain:').grid(row=0, column=0, sticky=tk.E, padx=5, pady=2)
            self.drxGain = ttk.Combobox(drx_frame, values=drxGain, state='readonly', width=12)
            self.drxGain.grid(row=0, column=1, padx=5, pady=2)
            try:
                if len(self.sdf_parent.project.sessions[0].observations) == 0 or \
                   self.sdf_parent.project.sessions[0].observations[0].gain == -1:
                    self.drxGain.set('MCS Decides')
                else:
                    self.drxGain.set(str(self.sdf_parent.project.sessions[0].observations[0].gain))
            except (IndexError, AttributeError):
                self.drxGain.set('MCS Decides')

            ttk.Label(drx_frame, text='Beam:').grid(row=1, column=0, sticky=tk.E, padx=5, pady=2)
            self.drxBeam = ttk.Combobox(drx_frame, values=drxBeam, state='readonly', width=12)
            self.drxBeam.grid(row=1, column=1, padx=5, pady=2)
            if self.sdf_parent.project.sessions[0].drx_beam == -1:
                self.drxBeam.set('MCS Decides')
            else:
                self.drxBeam.set(str(self.sdf_parent.project.sessions[0].drx_beam))

        # Beam-Dipole Mode Information (for DRX only)
        self.bdmEnable_var = None
        self.bdmDipole = None
        self.bdmDGain = None
        self.bdmBGain = None
        self.bdmPol_var = None
        if self.sdf_parent.mode == 'DRX':
            bdm_frame = ttk.LabelFrame(main_frame, text='Beam-Dipole Mode Information', padding="5")
            bdm_frame.pack(fill=tk.X, pady=(0, 10))

            # Check if beam-dipole mode is currently enabled
            bdm_enabled = False
            bdm_stand = '256'
            bdm_dgain = '1.0000'
            bdm_bgain = '0.0041'
            bdm_pol = 'X'
            try:
                if len(self.sdf_parent.project.sessions[0].observations) > 0:
                    obs = self.sdf_parent.project.sessions[0].observations[0]
                    if getattr(obs, 'beamDipole', None) is not None:
                        bdm_enabled = True
                        dpStand = obs.beamDipole[0] * 2 - 2
                        realStand = self.sdf_parent.station.antennas[dpStand].stand.id
                        bdm_stand = str(realStand)
                        bdm_dgain = '%.4f' % obs.beamDipole[2]
                        bdm_bgain = '%.4f' % obs.beamDipole[1]
                        bdm_pol = obs.beamDipole[3]
            except (IndexError, AttributeError):
                pass

            self.bdmEnable_var = tk.BooleanVar(value=bdm_enabled)
            self.bdmEnable = ttk.Checkbutton(bdm_frame, text='Enabled for all observations',
                                              variable=self.bdmEnable_var, command=self._on_bdm_toggle)
            self.bdmEnable.grid(row=0, column=0, columnspan=2, sticky=tk.W, pady=2)

            self.bdmPol_var = tk.StringVar(value=bdm_pol)
            ttk.Label(bdm_frame, text='Pol.:').grid(row=0, column=2, sticky=tk.E, padx=5)
            self.bdmPolX = ttk.Radiobutton(bdm_frame, text='X', variable=self.bdmPol_var, value='X')
            self.bdmPolX.grid(row=0, column=3, padx=2)
            self.bdmPolY = ttk.Radiobutton(bdm_frame, text='Y', variable=self.bdmPol_var, value='Y')
            self.bdmPolY.grid(row=0, column=4, padx=2)

            ttk.Label(bdm_frame, text='Stand Number:').grid(row=1, column=0, sticky=tk.E, padx=5, pady=2)
            self.bdmDipole = ttk.Entry(bdm_frame, width=10)
            self.bdmDipole.grid(row=1, column=1, padx=5, pady=2)
            self.bdmDipole.insert(0, bdm_stand)

            ttk.Label(bdm_frame, text='Stand Gain:').grid(row=1, column=2, sticky=tk.E, padx=5, pady=2)
            self.bdmDGain = ttk.Entry(bdm_frame, width=10)
            self.bdmDGain.grid(row=1, column=3, padx=5, pady=2)
            self.bdmDGain.insert(0, bdm_dgain)

            ttk.Label(bdm_frame, text='Beam Gain:').grid(row=1, column=4, sticky=tk.E, padx=5, pady=2)
            self.bdmBGain = ttk.Entry(bdm_frame, width=10)
            self.bdmBGain.grid(row=1, column=5, padx=5, pady=2)
            self.bdmBGain.insert(0, bdm_bgain)

            # Set initial state
            if not bdm_enabled:
                self.bdmDipole.config(state='disabled')
                self.bdmDGain.config(state='disabled')
                self.bdmBGain.config(state='disabled')
                self.bdmPolX.config(state='disabled')
                self.bdmPolY.config(state='disabled')

        # DR Spectrometer Information (conditional)
        self.spc_opt = None
        if self.sdf_parent.project.sessions[0].data_return_method == 'DR Spectrometer' or \
           (self.sdf_parent.project.sessions[0].spc_setup[0] != 0 and self.sdf_parent.project.sessions[0].spc_setup[1] != 0):
            spc_frame = ttk.LabelFrame(main_frame, text='DR Spectrometer Information', padding="5")
            spc_frame.pack(fill=tk.X, pady=(0, 10))

            mt = self.sdf_parent.project.sessions[0].spc_metatag
            if mt is None:
                isLinear = True
            else:
                junk, mt = mt.split('=', 1)
                mt = mt.replace('}', '')
                isLinear = mt in ('XXYY', 'CRCI', 'XXCRCIYY')

            ttk.Label(spc_frame, text='Data Products:').grid(row=0, column=0, sticky=tk.E, padx=5, pady=2)

            self.spc_opt = tk.StringVar(value='opt1')
            if isLinear:
                opt1_text = 'XX and YY'
                opt2_text = 'Re(XY) and Im(XY)'
                opt3_text = 'XX, Re(XY), Im(XY), and YY'
            else:
                opt1_text = 'I'
                opt2_text = 'I and V'
                opt3_text = 'I, Q, U, and V'

            self.spc_opt1 = ttk.Radiobutton(spc_frame, text=opt1_text, variable=self.spc_opt, value='opt1')
            self.spc_opt1.grid(row=0, column=1, sticky=tk.W, padx=5)
            self.spc_opt2 = ttk.Radiobutton(spc_frame, text=opt2_text, variable=self.spc_opt, value='opt2')
            self.spc_opt2.grid(row=0, column=2, sticky=tk.W, padx=5)
            self.spc_opt3 = ttk.Radiobutton(spc_frame, text=opt3_text, variable=self.spc_opt, value='opt3')
            self.spc_opt3.grid(row=0, column=3, sticky=tk.W, padx=5)

            # Set current value
            if mt in ('XXYY', 'I'):
                self.spc_opt.set('opt1')
            elif mt in ('CRCI', 'IV'):
                self.spc_opt.set('opt2')
            elif mt in ('XXCRCIYY', 'IQUV'):
                self.spc_opt.set('opt3')

        # Buttons
        btn_frame = ttk.Frame(main_frame)
        btn_frame.pack(fill=tk.X, pady=(10, 0))

        ttk.Button(btn_frame, text='OK', command=self.onOK).pack(side=tk.RIGHT, padx=5)
        ttk.Button(btn_frame, text='Cancel', command=self.onCancel).pack(side=tk.RIGHT, padx=5)

        self.geometry('550x700')

    def _on_bdm_toggle(self):
        """Toggle beam-dipole mode controls."""
        if self.bdmEnable_var.get():
            self.bdmDipole.config(state='normal')
            self.bdmDGain.config(state='normal')
            self.bdmBGain.config(state='normal')
            self.bdmPolX.config(state='normal')
            self.bdmPolY.config(state='normal')
        else:
            self.bdmDipole.config(state='disabled')
            self.bdmDGain.config(state='disabled')
            self.bdmBGain.config(state='disabled')
            self.bdmPolX.config(state='disabled')
            self.bdmPolY.config(state='disabled')

    def displayError(self, message, details=None, title='Error'):
        """Display an error dialog."""
        if details:
            messagebox.showerror(title, f"{message}\n\nDetails:\n{details}")
        else:
            messagebox.showerror(title, message)

    def onOK(self):
        """Save everything into the correct places."""
        # Validate TBT settings
        if self.tbtSamp is not None:
            try:
                tbtSamp = int(self.tbtSamp.get())
                if tbtSamp < 0:
                    self.displayError('Number of TBT samples must be positive', title='TBT Sample Error')
                    return
                if tbtSamp > 196000000 * 3:
                    self.displayError('Number of TBT samples too large',
                                     details=f'{tbtSamp} > 3 sec', title='TBT Sample Error')
                    return
            except ValueError:
                self.displayError('TBT samples must be numeric', title='TBT Sample Error')
                return

        # Save MCS settings
        self.sdf_parent.project.sessions[0].record_mib['ASP'] = self._parseTimeCombo(self.mrpASP.get())
        self.sdf_parent.project.sessions[0].record_mib['NDP'] = self._parseTimeCombo(self.mrpDP.get())
        for i in range(1, 6):
            self.sdf_parent.project.sessions[0].record_mib['DR%i' % i] = self._parseTimeCombo(self.mrpDR.get())
        self.sdf_parent.project.sessions[0].record_mib['SHL'] = self._parseTimeCombo(self.mrpSHL.get())
        self.sdf_parent.project.sessions[0].record_mib['MCS'] = self._parseTimeCombo(self.mrpMCS.get())

        self.sdf_parent.project.sessions[0].update_mib['ASP'] = self._parseTimeCombo(self.mupASP.get())
        self.sdf_parent.project.sessions[0].update_mib['NDP'] = self._parseTimeCombo(self.mupDP.get())
        for i in range(1, 6):
            self.sdf_parent.project.sessions[0].update_mib['DR%i' % i] = self._parseTimeCombo(self.mupDR.get())
        self.sdf_parent.project.sessions[0].update_mib['SHL'] = self._parseTimeCombo(self.mupSHL.get())
        self.sdf_parent.project.sessions[0].update_mib['MCS'] = self._parseTimeCombo(self.mupMCS.get())

        self.sdf_parent.project.sessions[0].include_mcssch_log = self.schLog_var.get()
        self.sdf_parent.project.sessions[0].include_mcsexe_log = self.exeLog_var.get()
        self.sdf_parent.project.sessions[0].include_station_smib = self.incSMIB_var.get()
        self.sdf_parent.project.sessions[0].include_station_design = self.incDESG_var.get()

        # Save ASP settings
        aspFltDict = {'MCS Decides': -1, 'Split': 0, 'Full': 1, 'Reduced': 2, 'Off': 3,
                      'Split @ 3MHz': 4, 'Full @ 3MHz': 5}
        aspFlt = aspFltDict.get(self.aspFlt.get(), -1)
        aspAT1 = self._parseGainCombo(self.aspAT1.get())
        aspAT2 = self._parseGainCombo(self.aspAT2.get())
        aspATS = self._parseGainCombo(self.aspATS.get())

        for i in range(len(self.sdf_parent.project.sessions[0].observations)):
            obs = self.sdf_parent.project.sessions[0].observations[i]
            for j in range(len(obs.asp_filter)):
                obs.asp_filter[j] = aspFlt
                obs.asp_atten_1[j] = aspAT1
                obs.asp_atten_2[j] = aspAT2
                obs.asp_atten_split[j] = aspATS

        refresh_duration = False

        # Save TBT settings
        if self.tbtSamp is not None:
            tbtSamp = int(self.tbtSamp.get())
            self.sdf_parent.project.sessions[0].tbtSamples = tbtSamp
            for i in range(len(self.sdf_parent.project.sessions[0].observations)):
                self.sdf_parent.project.sessions[0].observations[i].samples = tbtSamp
                self.sdf_parent.project.sessions[0].observations[i].update()
                refresh_duration = True

        # Save DRX settings
        if self.drxGain is not None:
            gain = self._parseGainCombo(self.drxGain.get())
            self.sdf_parent.project.sessions[0].drxGain = gain
            for i in range(len(self.sdf_parent.project.sessions[0].observations)):
                self.sdf_parent.project.sessions[0].observations[i].gain = gain
        if self.drxBeam is not None:
            self.sdf_parent.project.sessions[0].drx_beam = self._parseGainCombo(self.drxBeam.get())

        # Save Beam-Dipole Mode settings
        if self.bdmEnable_var is not None and self.bdmEnable_var.get():
            try:
                realStand = int(self.bdmDipole.get())
                maxStand = max([ant.stand.id for ant in self.sdf_parent.station.antennas])
                if realStand < 0 or realStand > maxStand:
                    self.displayError(f'Invalid stand number: {realStand}',
                                     details=f'0 < stand <= {maxStand}',
                                     title='Beam-Dipole Setup Error')
                    return
            except ValueError:
                self.displayError(f'Invalid stand number: {self.bdmDipole.get()}',
                                 details='Not an integer',
                                 title='Beam-Dipole Setup Error')
                return

            try:
                dipoleGain = float(self.bdmDGain.get())
                if dipoleGain < 0.0 or dipoleGain > 1.0:
                    self.displayError(f'Invalid dipole gain value: {dipoleGain:.4f}',
                                     details='0 <= gain <= 1',
                                     title='Beam-Dipole Setup Error')
                    return
            except ValueError:
                self.displayError(f'Invalid dipole gain value: {self.bdmDGain.get()}',
                                 details='Not a float',
                                 title='Beam-Dipole Setup Error')
                return

            try:
                beamGain = float(self.bdmBGain.get())
                if beamGain < 0.0 or beamGain > 1.0:
                    self.displayError(f'Invalid beam gain value: {beamGain:.4f}',
                                     details='0 <= gain <= 1',
                                     title='Beam-Dipole Setup Error')
                    return
            except ValueError:
                self.displayError(f'Invalid beam gain value: {self.bdmBGain.get()}',
                                 details='Not a float',
                                 title='Beam-Dipole Setup Error')
                return

            outputPol = self.bdmPol_var.get()
            beamDipole = (realStand, beamGain, dipoleGain, outputPol)

            for i in range(len(self.sdf_parent.project.sessions[0].observations)):
                self.sdf_parent.project.sessions[0].observations[i].set_beamdipole_mode(*beamDipole)

        # Save DR Spectrometer settings
        if self.spc_opt is not None:
            mt = self.sdf_parent.project.sessions[0].spc_metatag
            if mt is None:
                isLinear = True
            else:
                junk, mt = mt.split('=', 1)
                mt = mt.replace('}', '')
                isLinear = mt in ('XXYY', 'CRCI', 'XXCRCIYY')

            if isLinear:
                if self.spc_opt.get() == 'opt1':
                    self.sdf_parent.project.sessions[0].spc_metatag = '{Stokes=XXYY}'
                elif self.spc_opt.get() == 'opt2':
                    self.sdf_parent.project.sessions[0].spc_metatag = '{Stokes=CRCI}'
                else:
                    self.sdf_parent.project.sessions[0].spc_metatag = '{Stokes=XXCRCIYY}'
            else:
                if self.spc_opt.get() == 'opt1':
                    self.sdf_parent.project.sessions[0].spc_metatag = '{Stokes=I}'
                elif self.spc_opt.get() == 'opt2':
                    self.sdf_parent.project.sessions[0].spc_metatag = '{Stokes=IV}'
                else:
                    self.sdf_parent.project.sessions[0].spc_metatag = '{Stokes=IQUV}'

        # Refresh duration column if needed
        if refresh_duration and hasattr(self.sdf_parent, 'columnMap') and 'duration' in self.sdf_parent.columnMap:
            col_idx = self.sdf_parent.columnMap.index('duration')
            for idx, child in enumerate(self.sdf_parent.listControl.get_children()):
                obs = self.sdf_parent.project.sessions[0].observations[idx]
                if obs.mode == 'TBT':
                    values = list(self.sdf_parent.listControl.item(child, 'values'))
                    values[col_idx] = obs.duration
                    self.sdf_parent.listControl.item(child, values=values)

        self.sdf_parent.edited = True
        self.sdf_parent.setSaveButton()

        self.destroy()

    def onCancel(self):
        """Close without saving."""
        self.destroy()


class SessionDisplay(tk.Toplevel):
    """
    Window for displaying the 'Session at a Glance' with observation timeline/altitude plots.
    """

    def __init__(self, parent):
        super().__init__(parent)

        self.title('Session at a Glance')
        self.sdf_parent = parent
        self.geometry('800x400')

        self.earliest = 0
        self.cidmotion = None

        self.initUI()

        if self.sdf_parent.mode == 'DRX':
            self.initPlotDRX()
        else:
            self.initPlot()

    def initUI(self):
        """Build the window UI with matplotlib canvas."""
        main_frame = ttk.Frame(self)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Create matplotlib figure and canvas
        self.figure = Figure(figsize=(8, 4), dpi=100)
        self.canvas = FigureCanvasTkAgg(self.figure, master=main_frame)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

        # Add navigation toolbar
        self.toolbar = NavigationToolbar2Tk(self.canvas, main_frame)
        self.toolbar.update()

        # Status bar
        self.statusbar = ttk.Label(main_frame, text='', relief=tk.SUNKEN, anchor=tk.W)
        self.statusbar.pack(fill=tk.X, side=tk.BOTTOM)

        # Bind resize event
        self.bind('<Configure>', self.resizePlots)

    def initPlot(self):
        """Plot observation timeline for non-DRX modes (TBT, TBS)."""
        self.obs = self.sdf_parent.project.sessions[0].observations

        if len(self.obs) == 0:
            return False

        colors = ['blue', 'green', 'red', 'cyan', 'magenta', 'yellow', 'orange', 'purple']

        # Find the earliest observation
        self.earliest = conflict.unravelObs(self.obs)[0][0]
        yls = [0] * len(self.obs)

        self.figure.clf()
        self.ax1 = self.figure.add_subplot(111)
        self.ax2 = self.ax1.twiny()

        # Plot the observations as horizontal bars
        i = 0
        for yl, o in zip(yls, self.obs):
            start = o.mjd + o.mpm / 1000.0 / (3600.0 * 24.0)
            dur = o.dur / 1000.0 / (3600.0 * 24.0)

            self.ax1.barh(yl, dur, height=1.0, left=start - self.earliest, alpha=0.6,
                         color=colors[i % len(colors)], label='Observation %i' % (i + 1))
            self.ax1.annotate('%i' % (i + 1), (start - self.earliest + dur / 2, yl + 0.5),
                            ha='center', va='center')
            i += 1

        # Configure axes
        self.ax1.xaxis.tick_bottom()
        self.ax2.xaxis.tick_top()
        self.ax2.set_xlim([self.ax1.get_xlim()[0] * 24.0, self.ax1.get_xlim()[1] * 24.0])

        # Labels
        self.ax1.set_xlabel('MJD-%i [days]' % self.earliest)
        self.ax1.set_ylabel('Observation')
        self.ax2.set_xlabel('Session Elapsed Time [hours]')
        self.ax2.xaxis.set_label_position('top')
        self.ax1.yaxis.set_major_formatter(NullFormatter())
        self.ax2.yaxis.set_major_formatter(NullFormatter())
        self.ax1.yaxis.set_major_locator(NullLocator())
        self.ax2.yaxis.set_major_locator(NullLocator())

        self.figure.tight_layout()
        self.canvas.draw()
        self.connect()

    def initPlotDRX(self):
        """Plot source altitude over time for DRX observations."""
        self.obs = self.sdf_parent.project.sessions[0].observations

        if len(self.obs) == 0:
            return False

        # Find the earliest observation
        self.earliest = conflict.unravelObs(self.obs)[0][0]

        self.figure.clf()
        self.ax1 = self.figure.add_subplot(111)
        self.ax2 = self.ax1.twiny()

        # Get the station observer
        observer = self.sdf_parent.station.get_observer()

        i = 0
        for o in self.obs:
            t = []
            alt = []

            if o.mode not in ('TBT', 'TBS', 'STEPPED'):
                # Get the source
                src = o.fixed_body

                dt = 0.0
                stepSize = o.dur / 1000.0 / 300
                if stepSize < 30.0:
                    stepSize = 30.0

                # Find altitude over the course of the observation
                while dt < o.dur / 1000.0:
                    observer.date = o.mjd + (o.mpm / 1000.0 + dt) / 3600 / 24.0 + MJD_OFFSET - DJD_OFFSET
                    src.compute(observer)

                    alt.append(float(src.alt) * 180.0 / math.pi)
                    t.append(o.mjd + (o.mpm / 1000.0 + dt) / (3600.0 * 24.0) - self.earliest)

                    dt += stepSize

                # Get the end of the observation
                dt = o.dur / 1000.0
                observer.date = o.mjd + (o.mpm / 1000.0 + dt) / 3600 / 24.0 + MJD_OFFSET - DJD_OFFSET
                src.compute(observer)

                alt.append(float(src.alt) * 180.0 / math.pi)
                t.append(o.mjd + (o.mpm / 1000.0 + dt) / (3600.0 * 24.0) - self.earliest)

                # Plot altitude over time
                self.ax1.plot(t, alt, label='%s' % o.target)

                # Draw observation limits
                self.ax1.vlines(o.mjd + o.mpm / 1000.0 / (3600.0 * 24.0) - self.earliest, 0, 90, linestyle=':')
                self.ax1.vlines(o.mjd + (o.mpm / 1000.0 + o.dur / 1000.0) / (3600.0 * 24.0) - self.earliest, 0, 90, linestyle=':')

                i += 1

            elif o.mode == 'STEPPED':
                t0 = o.mjd + (o.mpm / 1000.0) / (3600.0 * 24.0)
                t = []
                alt = []

                for s in o.steps:
                    src = s.fixed_body

                    if src is not None:
                        observer.date = t0 + MJD_OFFSET - DJD_OFFSET
                        src.compute(observer)
                        step_alt = float(src.alt) * 180.0 / math.pi
                    else:
                        step_alt = s.c2

                    alt.append(step_alt)
                    t.append(t0 - self.earliest)
                    t0 += (s.dur / 1000.0) / (3600.0 * 24.0)
                    alt.append(step_alt)
                    t.append(t0 - self.earliest)

                # Plot altitude over time
                self.ax1.plot(t, alt, label='%s' % o.target)

                # Draw observation limits
                self.ax1.vlines(o.mjd + o.mpm / 1000.0 / (3600.0 * 24.0) - self.earliest, 0, 90, linestyle=':')
                self.ax1.vlines(o.mjd + (o.mpm / 1000.0 + o.dur / 1000.0) / (3600.0 * 24.0) - self.earliest, 0, 90, linestyle=':')

                i += 1

        # Add legend
        if i > 0:
            handles, labels = self.ax1.get_legend_handles_labels()
            self.ax1.legend(handles[:i], labels[:i], loc=0, fontsize='small')

        # Configure axes
        self.ax1.xaxis.tick_bottom()
        self.ax1.set_ylim([0, 90])
        self.ax2.xaxis.tick_top()
        self.ax2.set_xlim([self.ax1.get_xlim()[0] * 24.0, self.ax1.get_xlim()[1] * 24.0])

        # Labels
        self.ax1.set_xlabel('MJD-%i [days]' % self.earliest)
        self.ax1.set_ylabel('Altitude [deg.]')
        self.ax2.set_xlabel('Session Elapsed Time [hours]')
        self.ax2.xaxis.set_label_position('top')

        self.figure.tight_layout()
        self.canvas.draw()
        self.connect()

    def connect(self):
        """Connect matplotlib events."""
        self.cidmotion = self.figure.canvas.mpl_connect('motion_notify_event', self.on_motion)

    def on_motion(self, event):
        """Handle mouse motion events to update status bar."""
        if event.inaxes:
            clickX = event.xdata

            # Events come from second axes (hours), convert to MJD/MPM
            t = clickX / 24.0 + self.earliest
            mjd = int(t)
            mpm = int((t - mjd) * 24.0 * 3600.0 * 1000.0)

            # Compute session elapsed time
            elapsed = clickX * 3600.0
            eHour = int(elapsed / 3600)
            eMinute = int((elapsed % 3600) / 60)
            eSecond = (elapsed % 3600) % 60

            elapsed_str = "%02i:%02i:%06.3f" % (eHour, eMinute, eSecond)

            self.statusbar.config(text="MJD: %i  MPM: %i;  Session Elapsed Time: %s" % (mjd, mpm, elapsed_str))
        else:
            self.statusbar.config(text="")

    def disconnect(self):
        """Disconnect matplotlib events."""
        if self.cidmotion:
            self.figure.canvas.mpl_disconnect(self.cidmotion)

    def resizePlots(self, event=None):
        """Handle window resize."""
        try:
            self.figure.tight_layout()
            self.canvas.draw()
        except:
            pass


class VolumeInfo(tk.Toplevel):
    """
    Dialog for displaying estimated data volume for the session.
    """

    def __init__(self, parent):
        super().__init__(parent)

        self.title('Estimated Data Volume')
        self.sdf_parent = parent
        self.transient(parent)

        self.initUI()
        self.grab_set()

    def initUI(self):
        """Build the dialog UI with volume calculations."""
        main_frame = ttk.Frame(self, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Header
        header = ttk.Label(main_frame, text='Estimated Data Volume:', font=('TkDefaultFont', 12, 'bold'))
        header.pack(pady=(0, 10))

        ttk.Separator(main_frame, orient='horizontal').pack(fill=tk.X, pady=5)

        # Create a frame for the observation list
        list_frame = ttk.Frame(main_frame)
        list_frame.pack(fill=tk.BOTH, expand=True)

        # Column headers
        ttk.Label(list_frame, text='Observation', width=15).grid(row=0, column=0, sticky=tk.W, padx=5)
        ttk.Label(list_frame, text='Mode', width=20).grid(row=0, column=1, sticky=tk.W, padx=5)
        ttk.Label(list_frame, text='Volume', width=15).grid(row=0, column=2, sticky=tk.E, padx=5)

        ttk.Separator(list_frame, orient='horizontal').grid(row=1, column=0, columnspan=3, sticky=tk.EW, pady=5)

        # Calculate and display volumes for each observation
        observationCount = 1
        totalData = 0
        row = 2

        for obs in self.sdf_parent.project.sessions[0].observations:
            if self.sdf_parent.project.sessions[0].spc_setup[0] != 0 and self.sdf_parent.project.sessions[0].spc_setup[1] != 0:
                # DR Spectrometer mode
                mt = self.sdf_parent.project.sessions[0].spc_metatag
                if mt is None:
                    mt = '{Stokes=XXYY}'
                junk, mt = mt.split('=', 1)
                mt = mt.replace('}', '')

                if mt in ('XXYY', 'CRCI', 'XXCRCIYY'):
                    products = len(mt) // 2
                else:
                    products = len(mt)

                mode = "%s+%s" % (obs.mode, mt)

                tunes = 2
                tlen, icount = self.sdf_parent.project.sessions[0].spc_setup
                sample_rate = obs.filter_codes[obs.filter]
                duration = obs.dur / 1000.0
                dataVolume = (76 + tlen * tunes * products * 4) / (1.0 * tlen * icount / sample_rate) * duration
            else:
                mode = obs.mode
                dataVolume = obs.dataVolume

            ttk.Label(list_frame, text='Observation #%i' % observationCount).grid(
                row=row, column=0, sticky=tk.W, padx=5, pady=2)
            ttk.Label(list_frame, text=mode).grid(
                row=row, column=1, sticky=tk.W, padx=5, pady=2)
            ttk.Label(list_frame, text='%.2f GB' % (dataVolume / 1024.0**3,)).grid(
                row=row, column=2, sticky=tk.E, padx=5, pady=2)

            observationCount += 1
            totalData += dataVolume
            row += 1

        # Separator before total
        ttk.Separator(list_frame, orient='horizontal').grid(row=row, column=0, columnspan=3, sticky=tk.EW, pady=5)
        row += 1

        # Total
        ttk.Label(list_frame, text='Total:', font=('TkDefaultFont', 10, 'bold')).grid(
            row=row, column=0, sticky=tk.W, padx=5, pady=5)
        ttk.Label(list_frame, text='%.2f GB' % (totalData / 1024.0**3,),
                  font=('TkDefaultFont', 10, 'bold')).grid(
            row=row, column=2, sticky=tk.E, padx=5, pady=5)

        # OK button
        btn_frame = ttk.Frame(main_frame)
        btn_frame.pack(fill=tk.X, pady=(10, 0))
        ttk.Button(btn_frame, text='OK', command=self.destroy).pack(side=tk.RIGHT, padx=5)


class ResolveTarget(tk.Toplevel):
    """
    Dialog for resolving target names to coordinates.
    """

    def __init__(self, parent):
        super().__init__(parent)

        self.title('Resolve Target')
        self.sdf_parent = parent
        self.transient(parent)

        # Store resolved values
        self.resolved_ra = None
        self.resolved_dec = None
        self.resolved_target = None

        # Get selected observation info
        self._get_selected_observation()

        self.initUI()
        self.grab_set()

    def _get_selected_observation(self):
        """Get the currently selected observation."""
        self.selected_idx = None
        selection = self.sdf_parent.listControl.selection()
        if selection:
            children = self.sdf_parent.listControl.get_children()
            for i, child in enumerate(children):
                if child in selection:
                    self.selected_idx = i
                    break

    def initUI(self):
        """Build the dialog UI."""
        main_frame = ttk.Frame(self, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Pre-fill with selected observation's target name if available
        initial_target = ''
        if self.selected_idx is not None:
            try:
                obs = self.sdf_parent.project.sessions[0].observations[self.selected_idx]
                initial_target = obs.target
            except (IndexError, AttributeError):
                pass

        ttk.Label(main_frame, text='Target Name:').grid(row=0, column=0, sticky=tk.E, padx=5, pady=5)
        self.target_entry = ttk.Entry(main_frame, width=30)
        self.target_entry.insert(0, initial_target)
        self.target_entry.grid(row=0, column=1, sticky=tk.W, padx=5, pady=5)
        self.target_entry.bind('<Return>', lambda e: self.onResolve())

        ttk.Button(main_frame, text='Resolve', command=self.onResolve).grid(row=0, column=2, padx=5, pady=5)

        ttk.Label(main_frame, text='RA (J2000):').grid(row=1, column=0, sticky=tk.E, padx=5, pady=5)
        self.ra_var = tk.StringVar()
        self.ra_label = ttk.Label(main_frame, textvariable=self.ra_var, width=20)
        self.ra_label.grid(row=1, column=1, sticky=tk.W, padx=5, pady=5)

        ttk.Label(main_frame, text='Dec (J2000):').grid(row=2, column=0, sticky=tk.E, padx=5, pady=5)
        self.dec_var = tk.StringVar()
        self.dec_label = ttk.Label(main_frame, textvariable=self.dec_var, width=20)
        self.dec_label.grid(row=2, column=1, sticky=tk.W, padx=5, pady=5)

        # Status label
        self.status_var = tk.StringVar()
        ttk.Label(main_frame, textvariable=self.status_var, foreground='gray').grid(
            row=3, column=0, columnspan=3, pady=5)

        btn_frame = ttk.Frame(main_frame)
        btn_frame.grid(row=4, column=0, columnspan=3, pady=(10, 0))

        self.apply_btn = ttk.Button(btn_frame, text='Apply to Selected', command=self.onApply, state='disabled')
        self.apply_btn.pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text='Close', command=self.destroy).pack(side=tk.LEFT, padx=5)

    def onResolve(self):
        """Resolve the target name."""
        target = self.target_entry.get().strip()
        if not target:
            self.status_var.set('Please enter a target name')
            return

        self.status_var.set('Resolving...')
        self.update()

        try:
            posn = astro.resolve_name(target)
            self.resolved_ra = posn.ra / 15.0  # Convert to hours
            self.resolved_dec = posn.dec
            self.resolved_target = target

            self.ra_var.set(str(astro.deg_to_hms(posn.ra)).replace(' ', ':'))
            self.dec_var.set(str(astro.deg_to_dms(posn.dec)).replace(' ', ':'))
            self.status_var.set(f'Resolved: {target}')
            self.apply_btn.config(state='normal')
        except RuntimeError as e:
            self.ra_var.set('')
            self.dec_var.set('')
            self.resolved_ra = None
            self.resolved_dec = None
            self.status_var.set(f'Error: {e}')
            self.apply_btn.config(state='disabled')

    def onApply(self):
        """Apply the resolved coordinates to the selected observation."""
        if self.resolved_ra is None or self.resolved_dec is None:
            messagebox.showwarning('No Coordinates', 'Please resolve a target first.')
            return

        if self.selected_idx is None:
            messagebox.showwarning('No Selection', 'No observation is selected.')
            return

        try:
            obs = self.sdf_parent.project.sessions[0].observations[self.selected_idx]

            # Check if this is a valid observation type for RA/Dec
            if obs.mode not in ('TRK_RADEC',):
                messagebox.showwarning('Invalid Mode',
                    f'Cannot apply coordinates to {obs.mode} observation.\nOnly TRK_RADEC observations can have RA/Dec set.')
                return

            # Update the observation
            obs.target = self.resolved_target
            obs.ra = self.resolved_ra
            obs.dec = self.resolved_dec
            obs.update()

            # Update the display
            self.sdf_parent.addObservation(obs, self.selected_idx + 1, update=True)

            self.sdf_parent.edited = True
            self.sdf_parent.setSaveButton()
            self.status_var.set(f'Applied to observation {self.selected_idx + 1}')
            self.sdf_parent.statusbar.config(text=f'Updated observation {self.selected_idx + 1} with resolved coordinates')

        except (IndexError, AttributeError) as e:
            messagebox.showerror('Error', f'Failed to apply coordinates: {e}')


class ScheduleWindow(tk.Toplevel):
    """
    Window for session scheduling options.
    """

    def __init__(self, parent):
        super().__init__(parent)

        self.title('Session Scheduling')
        self.sdf_parent = parent
        self.transient(parent)

        self.initUI()
        self.grab_set()

    def initUI(self):
        """Build the window UI."""
        main_frame = ttk.Frame(self, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Header
        header = ttk.Label(main_frame, text='Rescheduling Options:', font=('TkDefaultFont', 12, 'bold'))
        header.pack(pady=(0, 10))

        # Radio buttons for scheduling options
        self.schedule_var = tk.StringVar(value='sidereal')

        # Determine current setting from session comments
        comments = self.sdf_parent.project.sessions[0].comments or ''
        if 'ScheduleSolarMovable' in comments:
            self.schedule_var.set('solar')
        elif 'ScheduleFixed' in comments:
            self.schedule_var.set('fixed')
        else:
            self.schedule_var.set('sidereal')

        radio_frame = ttk.Frame(main_frame)
        radio_frame.pack(fill=tk.X, pady=5)

        ttk.Radiobutton(radio_frame, text='Sidereal time fixed, date changeable',
                       variable=self.schedule_var, value='sidereal').pack(anchor=tk.W, pady=2)
        ttk.Radiobutton(radio_frame, text='UTC time fixed, date changeable',
                       variable=self.schedule_var, value='solar').pack(anchor=tk.W, pady=2)
        ttk.Radiobutton(radio_frame, text='Use only specified date/time',
                       variable=self.schedule_var, value='fixed').pack(anchor=tk.W, pady=2)

        # Separator
        ttk.Separator(main_frame, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=10)

        # Buttons
        btn_frame = ttk.Frame(main_frame)
        btn_frame.pack(fill=tk.X, pady=(10, 0))

        ttk.Button(btn_frame, text='Cancel', command=self.destroy).pack(side=tk.RIGHT, padx=5)
        ttk.Button(btn_frame, text='Apply', command=self.onApply).pack(side=tk.RIGHT, padx=5)

    def onApply(self):
        """Apply scheduling settings to session comments."""
        # Get current comments and remove old scheduling markers
        comments = self.sdf_parent.project.sessions[0].comments or ''
        comments = comments.replace('ScheduleSiderealMovable', '')
        comments = comments.replace('ScheduleSolarMovable', '')
        comments = comments.replace('ScheduleFixed', '')

        # Add new scheduling marker based on selection
        selection = self.schedule_var.get()
        if selection == 'sidereal':
            comments += ';;ScheduleSiderealMovable'
        elif selection == 'solar':
            comments += ';;ScheduleSolarMovable'
        elif selection == 'fixed':
            comments += ';;ScheduleFixed'

        self.sdf_parent.project.sessions[0].comments = comments

        self.sdf_parent.edited = True
        self.sdf_parent.setSaveButton()

        self.destroy()


class HelpWindow(tk.Toplevel):
    """
    Help window with HTML content rendering.

    Provides basic HTML rendering using tk.Text with tags, supporting:
    - Headers (h4, h6)
    - Bold, italic, underline text
    - Unordered lists
    - Internal anchor links (scrolls to section)
    - External links (opens in browser)
    """

    def __init__(self, parent):
        super().__init__(parent)

        self.title('Session GUI Handbook')
        self.geometry('800x500')
        self.sdf_parent = parent

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
        help_path = os.path.join(self.sdf_parent.scriptPath, 'docs', 'help.html')
        try:
            with open(help_path, 'r') as f:
                content = f.read()
            self._render_html(content)
        except FileNotFoundError:
            self.text.insert('1.0', 'Help file not found.\n\nPlease refer to the LWA documentation at http://lwa.unm.edu')

    def _render_html(self, html_content):
        """Parse and render HTML content to the text widget."""
        import re
        import webbrowser

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


class SteppedWindow(tk.Toplevel):
    """
    Window for editing stepped observations.
    """

    def __init__(self, parent, observation_index):
        super().__init__(parent)

        self.sdf_parent = parent
        self.obsID = observation_index
        self.obs = self.sdf_parent.project.sessions[0].observations[self.obsID]
        self.RADec = self.obs.is_radec
        self.buffer = None

        title = '%s Stepped Observation #%i' % ("RA/Dec" if self.RADec else "Az/Alt", observation_index + 1)
        self.title(title)
        self.geometry('900x400')

        self.columnMap = []
        self.coerceMap = []

        self.initUI()
        self.loadSteps()

    def initUI(self):
        """Build the window UI."""
        # Menu bar
        menubar = tk.Menu(self)
        self.config(menu=menubar)

        # Edit menu
        editmenu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label='Edit', menu=editmenu)
        editmenu.add_command(label='Cut Selected', command=self.onCut, state='disabled')
        editmenu.add_command(label='Copy Selected', command=self.onCopy, state='disabled')
        editmenu.add_command(label='Paste Before Selected', command=self.onPasteBefore, state='disabled')
        editmenu.add_command(label='Paste After Selected', command=self.onPasteAfter, state='disabled')
        editmenu.add_command(label='Paste at End', command=self.onPasteEnd, state='disabled')
        self.editmenu = editmenu

        # Steps menu
        stepmenu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label='Steps', menu=stepmenu)
        stepmenu.add_command(label='Add Step', command=self.onAddStep)
        stepmenu.add_command(label='Remove Selected', command=self.onRemove)
        stepmenu.add_separator()
        stepmenu.add_command(label='Done', command=self.onQuit)

        main_frame = ttk.Frame(self, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Toolbar
        toolbar = ttk.Frame(main_frame)
        toolbar.pack(fill=tk.X, pady=(0, 5))

        ttk.Button(toolbar, text='Add Step', command=self.onAddStep).pack(side=tk.LEFT, padx=2)
        ttk.Button(toolbar, text='Remove', command=self.onRemove).pack(side=tk.LEFT, padx=2)
        ttk.Button(toolbar, text='Done', command=self.onQuit).pack(side=tk.LEFT, padx=2)

        # Stepped list
        list_frame = ttk.Frame(main_frame)
        list_frame.pack(fill=tk.BOTH, expand=True)

        # Create columns based on RA/Dec or Az/Alt mode
        if self.RADec:
            columns = ('id', 'duration', 'ra', 'dec', 'freq1', 'freq2', 'high_dr')
            headings = {
                'id': ('ID', 40),
                'duration': ('Duration', 120),
                'ra': ('RA (Hour J2000)', 140),
                'dec': ('Dec (Deg. J2000)', 140),
                'freq1': ('Tuning 1 (MHz)', 120),
                'freq2': ('Tuning 2 (MHz)', 120),
                'high_dr': ('Max S/N?', 80),
            }
        else:
            columns = ('id', 'duration', 'az', 'alt', 'freq1', 'freq2', 'high_dr')
            headings = {
                'id': ('ID', 40),
                'duration': ('Duration', 120),
                'az': ('Azimuth (Deg.)', 140),
                'alt': ('Altitude (Deg.)', 140),
                'freq1': ('Tuning 1 (MHz)', 120),
                'freq2': ('Tuning 2 (MHz)', 120),
                'high_dr': ('Max S/N?', 80),
            }

        self.listControl = ttk.Treeview(list_frame, columns=columns, show='headings', selectmode='extended')

        for col in columns:
            text, width = headings.get(col, (col, 80))
            self.listControl.heading(col, text=text)
            self.listControl.column(col, width=width, minwidth=40)

        self.listControl.pack(fill=tk.BOTH, expand=True, side=tk.LEFT)

        scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.listControl.yview)
        self.listControl.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # Enable inline editing
        self.listControl.bind('<Double-1>', self._on_double_click)
        self.listControl.bind('<<TreeviewSelect>>', self._on_selection_change)
        self._edit_entry = None
        self._edit_item = None

        # Status bar
        self.statusbar = ttk.Label(main_frame, text='', relief=tk.SUNKEN, anchor=tk.W)
        self.statusbar.pack(fill=tk.X, side=tk.BOTTOM)

    def _on_double_click(self, event):
        """Handle double-click for inline editing."""
        region = self.listControl.identify_region(event.x, event.y)
        if region != 'cell':
            return

        column = self.listControl.identify_column(event.x)
        item = self.listControl.identify_row(event.y)

        if not item or not column:
            return

        col_idx = int(column.replace('#', '')) - 1
        if col_idx == 0:  # ID column not editable
            return

        values = self.listControl.item(item, 'values')
        if not values:
            return

        bbox = self.listControl.bbox(item, column)
        if not bbox:
            return

        self._start_edit(item, column, col_idx, bbox, values[col_idx])

    def _start_edit(self, item, column, col_idx, bbox, current_value):
        """Start inline editing of a cell."""
        if self._edit_entry:
            self._edit_entry.destroy()

        # Use tk.Entry with flat relief to avoid double-border effect
        self._edit_entry = tk.Entry(self.listControl, relief='flat', borderwidth=0,
                                    highlightthickness=1, highlightcolor='#4a90d9',
                                    highlightbackground='#cccccc')
        self._edit_entry.insert(0, current_value)
        self._edit_entry.select_range(0, tk.END)

        self._edit_entry.place(x=bbox[0], y=bbox[1], width=bbox[2], height=bbox[3])
        self._edit_entry.focus_set()

        self._edit_item = item
        self._edit_col_idx = col_idx

        self._edit_entry.bind('<Return>', self._finish_edit)
        self._edit_entry.bind('<Escape>', self._cancel_edit)
        self._edit_entry.bind('<FocusOut>', self._finish_edit)

    def _finish_edit(self, event=None):
        """Finish editing and save the value."""
        if not self._edit_entry or not self._edit_item:
            return

        new_value = self._edit_entry.get()
        values = list(self.listControl.item(self._edit_item, 'values'))
        col_idx = self._edit_col_idx

        # Find the row index
        children = self.listControl.get_children()
        row_idx = None
        for i, child in enumerate(children):
            if child == self._edit_item:
                row_idx = i
                break

        self._edit_entry.destroy()
        self._edit_entry = None

        if row_idx is not None:
            self.onEdit(row_idx, col_idx, new_value)

        # Update display
        values[col_idx] = new_value
        self.listControl.item(self._edit_item, values=values)

    def _cancel_edit(self, event=None):
        """Cancel editing without saving."""
        if self._edit_entry:
            self._edit_entry.destroy()
            self._edit_entry = None

    def _on_selection_change(self, event):
        """Handle selection changes."""
        selection = self.listControl.selection()
        if len(selection) > 0:
            self.editmenu.entryconfig(0, state='normal')  # Cut
            self.editmenu.entryconfig(1, state='normal')  # Copy
        else:
            self.editmenu.entryconfig(0, state='disabled')
            self.editmenu.entryconfig(1, state='disabled')

    def _dec2sexstr(self, value, signed=True):
        """Convert decimal value to sexagesimal string."""
        sign = 1
        if value < 0:
            sign = -1
        value = abs(value)

        d = int(value)
        m = int(value * 60) % 60
        s = float(value * 3600) % 60

        if signed:
            return '%+03i:%02i:%04.1f' % (sign * d, m, s)
        else:
            return '%02i:%02i:%05.2f' % (d, m, s)

    def loadSteps(self):
        """Load existing steps from the observation."""
        # Clear existing items
        for item in self.listControl.get_children():
            self.listControl.delete(item)

        # Load steps
        for i, step in enumerate(self.obs.steps):
            self.addStep(step, i + 1)

    def addStep(self, step, step_id):
        """Add a step to the list display."""
        freq1 = "%.6f" % (step.freq1 * fS / 2**32 / 1e6)
        freq2 = "%.6f" % (step.freq2 * fS / 2**32 / 1e6)
        high_dr = "Yes" if step.high_dr else "No"

        c1_str = self._dec2sexstr(step.c1, signed=False)
        c2_str = self._dec2sexstr(step.c2, signed=True)

        values = (str(step_id), step.duration, c1_str, c2_str, freq1, freq2, high_dr)
        self.listControl.insert('', 'end', values=values)

    def onAddStep(self, event=None):
        """Add a new step with default values."""
        step_id = len(self.obs.steps) + 1

        # Create a new BeamStep with default values
        new_step = self.sdf_parent.sdf.BeamStep(0.0, 0.0, '00:00:00.000', 42e6, 74e6, is_radec=self.RADec)
        self.obs.steps.append(new_step)
        self.addStep(new_step, step_id)

        self.sdf_parent.edited = True
        self.sdf_parent.setSaveButton()

    def onEdit(self, row_idx, col_idx, new_value):
        """Handle cell edit and update the underlying step."""
        self.statusbar.config(text='')

        try:
            step = self.obs.steps[row_idx]

            if col_idx == 1:  # Duration
                step.duration = new_value
            elif col_idx == 2:  # C1 (RA or Az)
                step.c1 = self._parseCoord1(new_value)
            elif col_idx == 3:  # C2 (Dec or Alt)
                step.c2 = self._parseCoord2(new_value)
            elif col_idx == 4:  # Freq1
                value = float(new_value) * 1e6
                freq = int(round(value * 2**32 / fS))
                if freq < 219130984 or freq > 1928352663:
                    raise ValueError(f"Frequency {new_value} MHz is out of tuning range")
                step.freq1 = freq
            elif col_idx == 5:  # Freq2
                value = float(new_value) * 1e6
                if value == 0:
                    step.freq2 = 0
                else:
                    freq = int(round(value * 2**32 / fS))
                    if freq < 219130984 or freq > 1928352663:
                        raise ValueError(f"Frequency {new_value} MHz is out of tuning range")
                    step.freq2 = freq
            elif col_idx == 6:  # High DR
                step.high_dr = new_value.lower() in ('yes', 'true', '1')

            step.update()
            self.obs.update()

            # Update duration in parent window
            self._updateParentDuration()

            # Clear error state
            children = self.listControl.get_children()
            if row_idx < len(children):
                self.listControl.item(children[row_idx], tags=())

            self.sdf_parent.edited = True
            self.sdf_parent.setSaveButton()

        except (ValueError, AttributeError) as e:
            self.statusbar.config(text=f'Error: {str(e)}')
            # Mark the row as having an error
            children = self.listControl.get_children()
            if row_idx < len(children):
                self.listControl.item(children[row_idx], tags=('error',))
                self.listControl.tag_configure('error', foreground='red')

    def _parseCoord1(self, text):
        """Parse coordinate 1 (RA or Az)."""
        fields = text.replace('+', '').replace('-', '').split(':')
        fields = [float(f) for f in fields]
        value = 0
        for f, d in zip(fields, [1.0, 60.0, 3600.0]):
            value += (f / d)

        if self.RADec:
            if value < 0 or value >= 24:
                raise ValueError("RA must be 0 <= RA < 24")
        else:
            if value < 0 or value >= 360:
                raise ValueError("Azimuth must be 0 <= Az < 360")
        return value

    def _parseCoord2(self, text):
        """Parse coordinate 2 (Dec or Alt)."""
        sign = 1
        if text.startswith('-'):
            sign = -1
        text = text.replace('+', '').replace('-', '')
        fields = text.split(':')
        fields = [float(f) for f in fields]
        value = 0
        for f, d in zip(fields, [1.0, 60.0, 3600.0]):
            value += (f / d)
        value *= sign

        if self.RADec:
            if value < -90 or value > 90:
                raise ValueError("Dec must be -90 <= Dec <= 90")
        else:
            if value < 0 or value > 90:
                raise ValueError("Altitude must be 0 <= Alt <= 90")
        return value

    def _updateParentDuration(self):
        """Update the duration column in the parent window."""
        try:
            children = self.sdf_parent.listControl.get_children()
            if self.obsID < len(children):
                values = list(self.sdf_parent.listControl.item(children[self.obsID], 'values'))
                # Find duration column (usually column 5)
                if 'duration' in self.sdf_parent.columnMap:
                    dur_idx = self.sdf_parent.columnMap.index('duration')
                    values[dur_idx] = self.obs.duration
                    self.sdf_parent.listControl.item(children[self.obsID], values=values)
        except (AttributeError, IndexError):
            pass

    def onRemove(self, event=None):
        """Remove selected steps."""
        selection = self.listControl.selection()
        if not selection:
            return

        # Get indices to remove (in reverse order to avoid index shifting)
        indices = []
        children = self.listControl.get_children()
        for item in selection:
            for i, child in enumerate(children):
                if child == item:
                    indices.append(i)
                    break

        indices.sort(reverse=True)

        # Remove from observation and list
        for idx in indices:
            del self.obs.steps[idx]
            self.listControl.delete(children[idx])

        # Renumber remaining rows
        self._renumberRows()

        self.obs.update()
        self._updateParentDuration()

        self.sdf_parent.edited = True
        self.sdf_parent.setSaveButton()

    def _renumberRows(self):
        """Renumber all rows after deletion."""
        for i, child in enumerate(self.listControl.get_children()):
            values = list(self.listControl.item(child, 'values'))
            values[0] = str(i + 1)
            self.listControl.item(child, values=values)

    def onCopy(self, event=None):
        """Copy selected steps to buffer."""
        self.buffer = []
        children = self.listControl.get_children()
        for item in self.listControl.selection():
            for i, child in enumerate(children):
                if child == item:
                    self.buffer.append(copy.deepcopy(self.obs.steps[i]))
                    break

        if self.buffer:
            self.editmenu.entryconfig(2, state='normal')  # Paste Before
            self.editmenu.entryconfig(3, state='normal')  # Paste After
            self.editmenu.entryconfig(4, state='normal')  # Paste End

    def onCut(self, event=None):
        """Cut selected steps."""
        self.onCopy(event)
        self.onRemove(event)

    def onPasteBefore(self, event=None):
        """Paste before selected item."""
        if not self.buffer:
            return

        selection = self.listControl.selection()
        if not selection:
            return

        # Find first selected index
        children = self.listControl.get_children()
        first_idx = None
        for i, child in enumerate(children):
            if child == selection[0]:
                first_idx = i
                break

        if first_idx is not None:
            for stp in reversed(self.buffer):
                new_step = copy.deepcopy(stp)
                self.obs.steps.insert(first_idx, new_step)

            self.loadSteps()
            self.obs.update()
            self._updateParentDuration()
            self.sdf_parent.edited = True
            self.sdf_parent.setSaveButton()

    def onPasteAfter(self, event=None):
        """Paste after selected item."""
        if not self.buffer:
            return

        selection = self.listControl.selection()
        if not selection:
            return

        # Find last selected index
        children = self.listControl.get_children()
        last_idx = None
        for item in selection:
            for i, child in enumerate(children):
                if child == item:
                    last_idx = i
                    break

        if last_idx is not None:
            insert_idx = last_idx + 1
            for stp in reversed(self.buffer):
                new_step = copy.deepcopy(stp)
                self.obs.steps.insert(insert_idx, new_step)

            self.loadSteps()
            self.obs.update()
            self._updateParentDuration()
            self.sdf_parent.edited = True
            self.sdf_parent.setSaveButton()

    def onPasteEnd(self, event=None):
        """Paste at end of list."""
        if not self.buffer:
            return

        for stp in self.buffer:
            new_step = copy.deepcopy(stp)
            self.obs.steps.append(new_step)

        self.loadSteps()
        self.obs.update()
        self._updateParentDuration()
        self.sdf_parent.edited = True
        self.sdf_parent.setSaveButton()

    def onQuit(self, event=None):
        """Close the window."""
        self.obs.update()
        self._updateParentDuration()
        self.destroy()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description='GUI for creating session definition files for LWA observations',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument('filename', type=str, nargs='?',
                        help='SDF file to open')
    parser.add_argument('-d', '--drsu-size', type=float, default=10.0,
                        help='DRSU capacity in TB')
    args = parser.parse_args()

    app = SDFCreator(args)
    app.mainloop()
