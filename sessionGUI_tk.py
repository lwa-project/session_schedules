#!/usr/bin/env python3

import os
import re
import sys
import copy
import math
import ephem
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
from lsl.common import stations
from lsl.astro import deg_to_dms, deg_to_hms, MJD_OFFSET, DJD_OFFSET
from lsl.reader.tbn import FILTER_CODES as TBNFilters
from lsl.reader.drx import FILTER_CODES as DRXFilters
from lsl.common import sdf, sdfADP, sdfNDP
from lsl.misc import parser as aph

import matplotlib
matplotlib.use('TkAgg')
matplotlib.interactive(True)

from matplotlib.backends.backend_tkagg import NavigationToolbar2Tk, FigureCanvasTkAgg
from matplotlib.figure import Figure
from matplotlib.ticker import NullFormatter, NullLocator

# Try to import tkhtmlview for HTML help, fall back to basic text
try:
    from tkhtmlview import HTMLLabel, HTMLScrolledText
    HAS_HTML = True
except ImportError:
    HAS_HTML = False

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


class ObservationTreeview(CheckableTreeview, EditableCell):
    """Combined Treeview with checkboxes and editable cells for observations."""

    def __init__(self, master=None, mode='TBW', **kwargs):
        CheckableTreeview.__init__(self, master, **kwargs)
        EditableCell.__init__(self)
        self.mode = mode
        self.editable_columns = []
        self.column_options = {}


class SteppedTreeview(CheckableTreeview, EditableCell):
    """Treeview for stepped observations with editable cells."""

    def __init__(self, master=None, **kwargs):
        CheckableTreeview.__init__(self, master, **kwargs)
        EditableCell.__init__(self)
        self.editable_columns = []
        self.column_options = {
            7: ['Yes', 'No'],  # is_radec column
        }


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


class ObserverInfoTk(tk.Toplevel):
    """Dialog for collecting observer, project, and session information."""

    def __init__(self, parent, project):
        tk.Toplevel.__init__(self, parent)
        self.title("Observer/Project/Session Information")
        self.parent = parent
        self.project = project
        self.result = None

        # Make it modal
        self.transient(parent)
        self.grab_set()

        self.create_widgets()
        self.load_defaults()

        # Center on parent
        self.geometry("+%d+%d" % (parent.winfo_rootx() + 50, parent.winfo_rooty() + 50))

    def create_widgets(self):
        """Create the dialog widgets."""
        main_frame = ttk.Frame(self, padding="10")
        main_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))

        # Observer Information
        observer_frame = ttk.LabelFrame(main_frame, text="Observer Information", padding="5")
        observer_frame.grid(row=0, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=5)

        ttk.Label(observer_frame, text="Observer ID:").grid(row=0, column=0, sticky=tk.W, padx=5, pady=2)
        self.observer_id = ttk.Entry(observer_frame, width=30)
        self.observer_id.grid(row=0, column=1, padx=5, pady=2)

        ttk.Label(observer_frame, text="First Name:").grid(row=1, column=0, sticky=tk.W, padx=5, pady=2)
        self.observer_first = ttk.Entry(observer_frame, width=30)
        self.observer_first.grid(row=1, column=1, padx=5, pady=2)

        ttk.Label(observer_frame, text="Last Name:").grid(row=2, column=0, sticky=tk.W, padx=5, pady=2)
        self.observer_last = ttk.Entry(observer_frame, width=30)
        self.observer_last.grid(row=2, column=1, padx=5, pady=2)

        # Project Information
        project_frame = ttk.LabelFrame(main_frame, text="Project Information", padding="5")
        project_frame.grid(row=1, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=5)

        ttk.Label(project_frame, text="Project ID:").grid(row=0, column=0, sticky=tk.W, padx=5, pady=2)
        self.project_id = ttk.Entry(project_frame, width=30)
        self.project_id.grid(row=0, column=1, padx=5, pady=2)

        ttk.Label(project_frame, text="Project Title:").grid(row=1, column=0, sticky=tk.W, padx=5, pady=2)
        self.project_title = ttk.Entry(project_frame, width=50)
        self.project_title.grid(row=1, column=1, columnspan=2, padx=5, pady=2)

        ttk.Label(project_frame, text="Project Comments:").grid(row=2, column=0, sticky=tk.NW, padx=5, pady=2)
        self.project_comments = tk.Text(project_frame, width=50, height=3)
        self.project_comments.grid(row=2, column=1, columnspan=2, padx=5, pady=2)

        # Session Information
        session_frame = ttk.LabelFrame(main_frame, text="Session Information", padding="5")
        session_frame.grid(row=2, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=5)

        ttk.Label(session_frame, text="Session ID:").grid(row=0, column=0, sticky=tk.W, padx=5, pady=2)
        self.session_id = ttk.Entry(session_frame, width=30)
        self.session_id.grid(row=0, column=1, padx=5, pady=2)

        ttk.Label(session_frame, text="Session Title:").grid(row=1, column=0, sticky=tk.W, padx=5, pady=2)
        self.session_title = ttk.Entry(session_frame, width=50)
        self.session_title.grid(row=1, column=1, columnspan=2, padx=5, pady=2)

        ttk.Label(session_frame, text="Session Comments:").grid(row=2, column=0, sticky=tk.NW, padx=5, pady=2)
        self.session_comments = tk.Text(session_frame, width=50, height=3)
        self.session_comments.grid(row=2, column=1, columnspan=2, padx=5, pady=2)

        # Session Type
        type_frame = ttk.LabelFrame(session_frame, text="Session Type", padding="5")
        type_frame.grid(row=3, column=0, columnspan=3, sticky=(tk.W, tk.E), pady=5)

        self.session_type = tk.StringVar(value="TRK_RADEC")
        ttk.Radiobutton(type_frame, text="TRK_RADEC (DRX RA/Dec)", variable=self.session_type,
                       value="TRK_RADEC", command=self.on_radio_change).grid(row=0, column=0, sticky=tk.W, padx=5)
        ttk.Radiobutton(type_frame, text="TRK_SOL (Solar)", variable=self.session_type,
                       value="TRK_SOL", command=self.on_radio_change).grid(row=1, column=0, sticky=tk.W, padx=5)
        ttk.Radiobutton(type_frame, text="TRK_JOV (Jovian)", variable=self.session_type,
                       value="TRK_JOV", command=self.on_radio_change).grid(row=2, column=0, sticky=tk.W, padx=5)
        ttk.Radiobutton(type_frame, text="TRK_LUN (Lunar)", variable=self.session_type,
                       value="TRK_LUN", command=self.on_radio_change).grid(row=3, column=0, sticky=tk.W, padx=5)
        ttk.Radiobutton(type_frame, text="STEPPED", variable=self.session_type,
                       value="STEPPED", command=self.on_radio_change).grid(row=4, column=0, sticky=tk.W, padx=5)

        # Data Return Method
        return_frame = ttk.LabelFrame(main_frame, text="Data Return Method", padding="5")
        return_frame.grid(row=3, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=5)

        self.data_return = tk.StringVar(value="USB Hard Drive")
        ttk.Radiobutton(return_frame, text="USB Hard Drive", variable=self.data_return,
                       value="USB Hard Drive").grid(row=0, column=0, sticky=tk.W, padx=5)
        ttk.Radiobutton(return_frame, text="UCF", variable=self.data_return,
                       value="UCF").grid(row=0, column=1, sticky=tk.W, padx=5)

        # DR Spectrometer
        drspec_frame = ttk.LabelFrame(main_frame, text="DR Spectrometer", padding="5")
        drspec_frame.grid(row=4, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=5)

        self.drspec_enabled = tk.BooleanVar(value=False)
        ttk.Checkbutton(drspec_frame, text="Enable DR Spectrometer", variable=self.drspec_enabled,
                       command=self.on_drspec_change).grid(row=0, column=0, columnspan=2, sticky=tk.W, padx=5)

        ttk.Label(drspec_frame, text="Channels:").grid(row=1, column=0, sticky=tk.W, padx=5, pady=2)
        self.drspec_channels = ttk.Combobox(drspec_frame, values=['1024', '2048', '4096'], width=10, state='disabled')
        self.drspec_channels.set('1024')
        self.drspec_channels.grid(row=1, column=1, sticky=tk.W, padx=5, pady=2)

        ttk.Label(drspec_frame, text="FFTs:").grid(row=2, column=0, sticky=tk.W, padx=5, pady=2)
        self.drspec_ffts = ttk.Combobox(drspec_frame, values=['1024', '2048', '4096'], width=10, state='disabled')
        self.drspec_ffts.set('1024')
        self.drspec_ffts.grid(row=2, column=1, sticky=tk.W, padx=5, pady=2)

        self.drspec_linear = tk.BooleanVar(value=True)
        ttk.Radiobutton(drspec_frame, text="Linear Polarizations", variable=self.drspec_linear,
                       value=True, state='disabled').grid(row=3, column=0, sticky=tk.W, padx=5)
        ttk.Radiobutton(drspec_frame, text="Stokes Parameters", variable=self.drspec_linear,
                       value=False, state='disabled').grid(row=3, column=1, sticky=tk.W, padx=5)

        # Buttons
        button_frame = ttk.Frame(main_frame)
        button_frame.grid(row=5, column=0, columnspan=2, pady=10)

        ttk.Button(button_frame, text="OK", command=self.on_ok).grid(row=0, column=0, padx=5)
        ttk.Button(button_frame, text="Cancel", command=self.on_cancel).grid(row=0, column=1, padx=5)
        ttk.Button(button_frame, text="Save as Defaults", command=self.on_save_defaults).grid(row=0, column=2, padx=5)

    def on_radio_change(self):
        """Handle session type radio button changes."""
        pass  # Can add logic to enable/disable DRX-specific options

    def on_drspec_change(self):
        """Handle DR spectrometer checkbox changes."""
        state = 'normal' if self.drspec_enabled.get() else 'disabled'
        self.drspec_channels.config(state=state)
        self.drspec_ffts.config(state=state)

    def load_defaults(self):
        """Load default values from ~/.sessionGUI if it exists."""
        config_file = os.path.expanduser("~/.sessionGUI")
        if os.path.exists(config_file):
            with open(config_file, 'r') as fh:
                for line in fh:
                    line = line.strip()
                    if line.startswith('ObserverID'):
                        self.observer_id.insert(0, line.split(None, 1)[1])
                    elif line.startswith('ObserverFirstName'):
                        self.observer_first.insert(0, line.split(None, 1)[1])
                    elif line.startswith('ObserverLastName'):
                        self.observer_last.insert(0, line.split(None, 1)[1])
                    elif line.startswith('ProjectID'):
                        self.project_id.insert(0, line.split(None, 1)[1])
                    elif line.startswith('ProjectName'):
                        self.project_title.insert(0, line.split(None, 1)[1])

        # Load current project values if they exist
        if self.project.observer.id:
            self.observer_id.delete(0, tk.END)
            self.observer_id.insert(0, str(self.project.observer.id))
        if self.project.observer.first:
            self.observer_first.delete(0, tk.END)
            self.observer_first.insert(0, self.project.observer.first)
        if self.project.observer.last:
            self.observer_last.delete(0, tk.END)
            self.observer_last.insert(0, self.project.observer.last)
        if self.project.id:
            self.project_id.delete(0, tk.END)
            self.project_id.insert(0, self.project.id)
        if self.project.name:
            self.project_title.delete(0, tk.END)
            self.project_title.insert(0, self.project.name)
        if self.project.comments:
            self.project_comments.insert('1.0', self.project.comments)

        if len(self.project.sessions) > 0:
            session = self.project.sessions[0]
            if session.id:
                self.session_id.delete(0, tk.END)
                self.session_id.insert(0, str(session.id))
            if session.name:
                self.session_title.delete(0, tk.END)
                self.session_title.insert(0, session.name)
            if session.comments:
                self.session_comments.insert('1.0', session.comments)
            if hasattr(session, 'data_return_method'):
                self.data_return.set(session.data_return_method)

    def on_ok(self):
        """Validate and save the observer/project/session information."""
        # Validate required fields
        if not self.observer_id.get():
            messagebox.showerror("Error", "Observer ID is required")
            return
        if not self.observer_first.get():
            messagebox.showerror("Error", "Observer First Name is required")
            return
        if not self.observer_last.get():
            messagebox.showerror("Error", "Observer Last Name is required")
            return
        if not self.project_id.get():
            messagebox.showerror("Error", "Project ID is required")
            return
        if not self.project_title.get():
            messagebox.showerror("Error", "Project Title is required")
            return
        if not self.session_id.get():
            messagebox.showerror("Error", "Session ID is required")
            return

        # Update project
        self.project.observer.id = int(self.observer_id.get())
        self.project.observer.first = self.observer_first.get()
        self.project.observer.last = self.observer_last.get()
        self.project.observer.name = f"{self.observer_first.get()} {self.observer_last.get()}"

        self.project.id = self.project_id.get()
        self.project.name = self.project_title.get()
        self.project.comments = self.project_comments.get('1.0', tk.END).strip()

        session = self.project.sessions[0]
        session.id = int(self.session_id.get())
        session.name = self.session_title.get()
        session.comments = self.session_comments.get('1.0', tk.END).strip()
        session.data_return_method = self.data_return.get()

        # DR Spectrometer settings
        if self.drspec_enabled.get():
            session.spcSetup = [int(self.drspec_channels.get()), int(self.drspec_ffts.get())]
            session.spcMetatag = 'LINEAR' if self.drspec_linear.get() else 'STOKES'
        else:
            session.spcSetup = [0, 0]
            session.spcMetatag = None

        self.result = 'ok'
        self.destroy()

    def on_cancel(self):
        """Cancel the dialog."""
        self.result = 'cancel'
        self.destroy()

    def on_save_defaults(self):
        """Save current values as defaults to ~/.sessionGUI."""
        config_file = os.path.expanduser("~/.sessionGUI")
        with open(config_file, 'w') as fh:
            fh.write(f"ObserverID {self.observer_id.get()}\n")
            fh.write(f"ObserverFirstName {self.observer_first.get()}\n")
            fh.write(f"ObserverLastName {self.observer_last.get()}\n")
            fh.write(f"ProjectID {self.project_id.get()}\n")
            fh.write(f"ProjectName {self.project_title.get()}\n")
        messagebox.showinfo("Saved", "Defaults saved to ~/.sessionGUI")


# TODO: Continue with more dialog classes and main window
# This is a starting framework - more classes need to be added

