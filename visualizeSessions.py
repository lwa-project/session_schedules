#!/usr/bin/env python3

import os
import sys
import math
import pytz
import ephem
import numpy
import argparse
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from datetime import datetime, timedelta

from lsl.common import sdf, metabundle, sdfADP, metabundleADP, sdfNDP, metabundleNDP
from lsl.common import stations
from lsl.astro import utcjd_to_unix, MJD_OFFSET

import matplotlib
matplotlib.use('TkAgg')
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.figure import Figure

import matplotlib.dates
from matplotlib import pyplot as plt
from matplotlib.collections import LineCollection

__version__ = "0.4"
__author__ = "Jayce Dowell"

# Date/time manipulation
_UTC = pytz.utc
_MST = pytz.timezone('US/Mountain')
formatString = '%Y/%m/%d %H:%M:%S.%f %Z'

# MCS session padding extent
sessionLag = timedelta(seconds=5)


def round15Minutes(tNow):
    """
    Round a datetime instance to the nearst 15 minutes
    """

    reducedTime = tNow.minute*60 + tNow.second + tNow.microsecond/1000000.0
    nearest15min = round(reducedTime / 900)*900
    diff = nearest15min - reducedTime

    sign = 1
    if diff < 0:
        sign = -1
        diff = abs(diff)

    diffSeconds = int(diff)
    diffMicroseconds = int(round((diff - diffSeconds)*1e6))

    return tNow + sign*timedelta(seconds=diffSeconds, microseconds=diffMicroseconds)


def getObsStartStop(obs):
    """
    Given an observation, get the start and stop times (returned as a two-
    element tuple).
    """

    # UNIX timestamp for the start
    tStart = utcjd_to_unix(obs.mjd + MJD_OFFSET)
    tStart += obs.mpm / 1000.0

    # UNIX timestamp for the stop
    tStop = tStart +  obs.dur / 1000.0

    # Conversion to a timezone-aware datetime instance
    tStart = _UTC.localize( datetime.utcfromtimestamp(tStart) )
    tStop  = _UTC.localize( datetime.utcfromtimestamp(tStop ) )

    # Return
    return tStart, tStop


class Visualization_GUI(object):
    """
    Class to handle the parsing and plotting of the SDF files selected in the GUI.  The
    loadFiles() function relies on the 'filenames' attribute of the parent to be a list
    of valid SDF filenames.
    """

    def __init__(self, frame, station='lwa1'):
        self.frame = frame
        self.showDayNight = True
        self.showJupiter = False

        if station == 'lwa1':
            self.observer = stations.lwa1
            self.sdf = sdf
            self.adp = False
            self.ndp = False
        elif station == 'lwasv':
            self.observer = stations.lwasv
            self.sdf = sdfADP
            self.adp = True
            self.ndp = False
        elif station == 'lwana':
            self.observer = stations.lwana
            self.sdf = sdfNDP
            self.adp = False
            self.ndp = True
        else:
            raise ValueError(f"Unkown station: {station}")

        self.colors = ['Blue','Green','Cyan','Magenta','Yellow',
                    'Peru', 'Moccasin', 'Orange', 'DarkOrchid']

        self.sessionSDFs = []
        self.sessionNames = []
        self.sessionBeams = []
        self.sessionStarts = []
        self.sessionDurations = []
        self.unqiueProjects = []

    def loadFiles(self):
        """
        Load in the SDF files listed in self.parent.filenames and compute the free time.
        """

        sessionSDFs = []
        sessionNames = []
        sessionBeams = []
        sessionStarts = []
        sessionStops = []
        sessionDurations = []
        sessionDataFiles = []

        # Loop over filenames
        for filename in self.frame.filenames:
            try:
                project = self.sdf.parse_sdf(filename)
                dataFile = None
            except Exception as e:
                try:
                    project = metabundle.get_sdf(filename)
                    dataFile = metabundle.get_session_metadata(filename)
                except Exception as e:
                    print(f"Warning: Cannot parse '{os.path.basename(filename)}': {str(e)}")
                    continue

            pID = project.id
            sID = project.sessions[0].id

            if project.sessions[0].observations[0].mode in ('TBW', 'TBN'):
                if self.ndp:
                    raise RuntimeError("No TBW or TBN for NDP")
                elif self.adp:
                    beam = 3
                else:
                    beam = 5
            else:
                beam = project.sessions[0].drx_beam
            sessionStart = getObsStartStop(project.sessions[0].observations[ 0])[0] - sessionLag
            sessionStop  = getObsStartStop(project.sessions[0].observations[-1])[1] + sessionLag
            duration = sessionStop-sessionStart

            sessionSDFs.append(project)
            sessionNames.append(f"{pID}_{sID:04d}")
            sessionBeams.append(beam)
            sessionStarts.append(sessionStart)
            sessionStops.append(sessionStop)
            sessionDurations.append(duration)
            sessionDataFiles.append(dataFile)

        # Find unique project identifiers
        uniqueProjects = []
        for name in sessionNames:
            pID, sID = name.split('_', 1)
            if pID not in uniqueProjects:
                uniqueProjects.append(pID)

        # Save the data
        self.sessionSDFs = sessionSDFs
        self.sessionNames = sessionNames
        self.sessionBeams = sessionBeams
        self.sessionStarts = sessionStarts
        self.sessionStops = sessionStops
        self.sessionDurations = sessionDurations
        self.sessionDataFiles = sessionDataFiles
        self.uniqueProjects = uniqueProjects

        # Compute the free time
        self.getFreeTime()

        try:
            self.disconnect()
        except:
            pass
        self.connect()

    def getFreeTime(self, step=timedelta(seconds=900)):
        """
        Using the list of SDFs read in by loadFiles(), find times when nothing is
        scheduled and save these 'free' times to self.freePeriods.  This attribute
        contains a list of two-element tuples (free time start, stop).
        """

        startMin = min(self.sessionStarts)
        startMax = max(self.sessionStarts)

        # Find free time in 15 minutes chunks
        frees = []
        tNow = round15Minutes(startMin)
        while tNow <= startMax:
            free = True
            # Loop over SDFs
            for start,duration in zip(self.sessionStarts, self.sessionDurations):
                if tNow >= start and tNow <= start+duration:
                    free = False
                if tNow+step >= start and tNow+step <= start+duration:
                    free = False
            if free:
                frees.append(tNow)
            tNow += step

        # Use the free times to come up with free periods
        if len(frees) > 0:
            freePeriods = [[frees[0], frees[0]],]
            for free in frees:
                if free-freePeriods[-1][1] <= step:
                    freePeriods[-1][1] = free
                else:
                    freePeriods.append([free, free])
        else:
            freePeriods = []

        # Save
        self.freePeriods = freePeriods

    def getSolarAltitude(self, step=timedelta(seconds=900)):
        """
        Using the time range of all loaded SDFs, return a two-element tuple of the
        time and altitude for the Sun in 15 minute steps.
        """

        # Find out how long we need to compute the position of the Sun
        start = min(self.sessionStarts)
        stop  = max(self.sessionStops)

        # Define the Sun
        Sun = ephem.Sun()

        # Go!
        tNow = start
        points = []
        alts = []
        while tNow <= stop:
            self.observer.date = tNow.strftime('%Y/%m/%d %H:%M:%S')
            Sun.compute(self.observer)
            alt = float(Sun.alt)*180/math.pi

            s = tNow - start
            s = s.days*24*3600 + s.seconds + s.microseconds/1e6
            s /= 3600.0

            points.append([matplotlib.dates.date2num(tNow),6])
            alts.append(alt)

            tNow += step

        return numpy.array(points), numpy.array(alts)

    def getJupiterAltitude(self, step=timedelta(seconds=900)):
        """
        Using the time range of all loaded SDFs, return a two-element tuple of the
        time and altitude for Jupiter in 15 minute steps.
        """

        # Find out how long we need to compute the position of the Sun
        start = min(self.sessionStarts)
        stop  = max(self.sessionStops)

        # Define Jupiter
        Jupiter = ephem.Jupiter()

        # Go!
        tNow = start
        points = []
        alts = []
        while tNow <= stop:
            self.observer.date = tNow.strftime('%Y/%m/%d %H:%M:%S')
            Jupiter.compute(self.observer)
            alt = float(Jupiter.alt)*180/math.pi

            s = tNow - start
            s = s.days*24*3600 + s.seconds + s.microseconds/1e6
            s /= 3600.0

            points.append([matplotlib.dates.date2num(tNow),5])
            alts.append(alt)

            tNow += step

        return numpy.array(points), numpy.array(alts)

    def connect(self):
        """
        Connect to all the events we need.
        """

        self.cidmotion = self.frame.canvas.mpl_connect('motion_notify_event', self.on_motion)

    def disconnect(self):
        """
        Disconnect all the stored connection ids.
        """

        self.frame.canvas.mpl_disconnect(self.cidmotion)

    def on_motion(self, event):
        """
        Deal with motion events in the image window.  This involves updating the
        current MCS session information at the bottom of the plot window.
        """

        if event.inaxes:
            clickX = matplotlib.dates.num2date(event.xdata)
            clickX = clickX.replace(tzinfo=_UTC)

            self.displayText(clickX, y=event.ydata)
        else:
            self.displayText(None)

    def displayText(self, t, y=None):
        """
        Given a time value, determine which SDF(s) overlap and return the SDF name, beam,
        observation mode, and target to the window.
        """

        info = ''
        if t is not None:
            try:
                ## Times
                if y is not None:
                    info += f"Time: {t.strftime('%Y/%m/%d %H:%M:%S %Z')} (beam {int(round(y))})\n\n"
                else:
                    info += f"Time: {t.strftime('%Y/%m/%d %H:%M:%S %Z')}\n\n"

                ## Free time?
                isFree = True
                for start,stop in zip(self.sessionStarts, self.sessionStops):
                    if t >= start and t <= stop:
                        isFree = False
                if isFree:
                    info += "FREE TIME\n\n"

                ## Session info
                for i,(name,beam,start,stop,project,data_file) in enumerate(zip(self.sessionNames, self.sessionBeams,
                                                                                  self.sessionStarts, self.sessionStops,
                                                                                  self.sessionSDFs, self.sessionDataFiles)):
                    if t >= start and t <= stop:
                        info += "Session:\n"
                        info += "  Project: %s\n" % name.split('_', 1)[0]
                        info += "  Session: %s\n" % name.split('_', 1)[1]
                        if data_file is not None:
                            info += "  Data dir: %s\n" % data_file['tag']
                            info += "  Comments: %s\n" % data_file['comments']
                        info += f"  Beam: {beam}\n"
                        info += f"  Start: {start.strftime('%Y/%m/%d %H:%M:%S %Z')}\n"
                        info += f"  Stop:  {stop.strftime('%Y/%m/%d %H:%M:%S %Z')}\n"
                        obs = project.sessions[0].observations

                        ## Observation info
                        j = 0
                        for obs in project.sessions[0].observations:
                            oStart, oStop = getObsStartStop(obs)
                            if t >= oStart and t <= oStop:
                                j += 1
                                info += "\n"
                                info += "Observation #%i:\n" % (j,)
                                info += "  Start: %s\n" % oStart.strftime('%Y/%m/%d %H:%M:%S %Z')
                                info += "  Stop:  %s\n" % oStop.strftime('%Y/%m/%d %H:%M:%S %Z')
                                info += "  Mode: %s\n" % obs.mode
                                info += "  Target: %s\n" % obs.target
                                try:
                                    info += "  RA: %s\n" % obs.ra
                                    info += "  Dec: %s\n" % obs.dec
                                except AttributeError:
                                    pass
                                try:
                                    info += "  Alt: %.1f\n" % obs.alt
                                    info += "  Az: %.1f\n" % obs.az
                                except AttributeError:
                                    pass
                                try:
                                    info += "  Filter code: %i\n" % obs.filter
                                except AttributeError:
                                    pass
                        info += "\n"
            except ValueError:
                pass

        # Set
        self.frame.info.delete('1.0', tk.END)
        self.frame.info.insert('1.0', info)

    def draw(self):
        """
        Make a plot of the session in a "at a glance" manner.
        """

        self.frame.figure.clf()
        ax = self.frame.figure.gca()

        # Get the project ID for each session (easier than doing it inside a list comprehension)
        sessionPIDs = []
        for name in self.sessionNames:
            pID, sID = name.split('_', 1)
            sessionPIDs.append(pID)

        # Build up a collection of rectangles to display
        ## Loop over unique project IDs
        segments = []
        colors = []
        for i,pID in enumerate(self.uniqueProjects):
            ## Get the color for the project
            color = self.colors[i % len(self.colors)]

            ## Loop over sessions
            for sID,sBeam,sStart,sDuration in zip(self.sessionNames, self.sessionBeams, self.sessionStarts, self.sessionDurations):
                ### Check project ID
                if sID.find(pID+'_') == -1:
                    continue

                ### Get times in days for the plot
                t0 = sStart
                t1 = sStart + sDuration

                ### Create the rectangle
                segments.append( [(matplotlib.dates.date2num(t0), sBeam),
                                  (matplotlib.dates.date2num(t1), sBeam)] )
                colors.append(color)

        lc = LineCollection(segments, colors=colors, linewidths=10)
        ax.add_collection(lc)

        # Mark the day/night periods
        if self.showDayNight:
            points, alts = self.getSolarAltitude()

            for i in range(len(alts)):
                ## Night -> day and day -> night transitions
                if i == 0 or i == (len(alts)-1):
                    if alts[i] < 0:
                        ax.axvspan(points[i,0], points[i,0], color='blue', alpha=0.25)
                elif alts[i-1] < 0 and alts[i] >= 0:
                    ax.axvspan(points[0,0], points[i,0], color='blue', alpha=0.25)
                elif alts[i-1] >= 0 and alts[i] < 0:
                    ax.axvspan(points[i,0], points[-1,0], color='blue', alpha=0.25)

        # Mark times when Jupiter is visible
        if self.showJupiter:
            points, alts = self.getJupiterAltitude()

            for i in range(len(alts)):
                ## Rise and set
                if i == 0 or i == (len(alts)-1):
                    if alts[i] >= 10:
                        ax.axvspan(points[i,0], points[i,0], color='green', alpha=0.25)
                elif alts[i-1] < 10 and alts[i] >= 10:
                    ax.axvspan(points[0,0], points[i,0], color='green', alpha=0.25)
                elif alts[i-1] >= 10 and alts[i] < 10:
                    ax.axvspan(points[i,0], points[-1,0], color='green', alpha=0.25)

        # Set the limits to just zoom in on what is there
        pad = (max(self.sessionStarts)-min(self.sessionStarts))
        pad = pad.days + pad.seconds/86400.0
        pad *= 0.05

        ## Datetime instances to plot
        ax.set_xlim([matplotlib.dates.date2num(min(self.sessionStarts))-pad,
                     matplotlib.dates.date2num(max(self.sessionStops))+pad])
        ax.set_ylim([0.5, 6.5])

        ## Proper axes labels
        ax.xaxis_date()
        locator = matplotlib.dates.AutoDateLocator()
        locator.intervald[3] = [1]  # Only show 1 hour intervals for hours
        ax.xaxis.set_major_locator(locator)
        ax.xaxis.set_major_formatter(matplotlib.dates.AutoDateFormatter(locator))
        ax.set_xlabel('Date [UTC]')

        ## Beam labels
        beamValues = []
        beamLabels = []
        for b in range(1, 7):
            beamValues.append(b)
            if self.adp or self.ndp:
                if b == 3:
                    beamLabels.append('Tied')
                elif b == 4:
                    beamLabels.append('Spec.')
                else:
                    beamLabels.append('Beam %i' % b)
            else:
                if b == 5:
                    beamLabels.append('Tied')
                elif b == 6:
                    beamLabels.append('Spec.')
                else:
                    beamLabels.append('Beam %i' % b)
        ax.set_yticks(beamValues)
        ax.set_yticklabels(beamLabels)
        ax.set_ylabel('Beam')

        # Legend
        rectangles = []
        for i,pID in enumerate(self.uniqueProjects):
            color = self.colors[i % len(self.colors)]
            rectangles.append( plt.Rectangle((0,0), 1, 1, fc=color) )
        lgd = ax.legend(rectangles, self.uniqueProjects, loc=0, ncol=2)

        ## Try to get the legend to not cover things
        points = lgd.get_window_extent()
        if points.x1 > self.frame.figure.get_figwidth()*self.frame.figure.get_dpi():
            lgd = ax.legend(rectangles, self.uniqueProjects, loc='upper left', bbox_to_anchor=(1.02, 1), ncol=1)

        self.frame.canvas.draw()


class MainWindow(tk.Tk):
    """
    Main Tkinter window for displaying the sessions and adding/removing files.
    """

    def __init__(self):
        super().__init__()

        self.title("Visualize Sessions")
        self.geometry("800x900")

        self.dirname = ''
        self.scriptPath = os.path.abspath(__file__)
        self.scriptPath = os.path.split(self.scriptPath)[0]

        self.data = None
        self.filenames = []

        self.create_menu()
        self.create_widgets()

    def create_menu(self):
        menubar = tk.Menu(self)

        # File menu
        filemenu = tk.Menu(menubar, tearoff=0)
        filemenu.add_command(label="Add File(s)", command=self.on_add_files)
        filemenu.add_command(label="Remove File(s)", command=self.on_remove_files)
        filemenu.add_separator()
        filemenu.add_command(label="Quit", command=self.on_quit)
        menubar.add_cascade(label="File", menu=filemenu)

        # Display menu
        dispmenu = tk.Menu(menubar, tearoff=0)
        self.show_daynight_var = tk.BooleanVar(value=True)
        self.show_jupiter_var = tk.BooleanVar(value=False)
        dispmenu.add_checkbutton(label="Show Day/Night", variable=self.show_daynight_var,
                                 command=self.on_daynight)
        dispmenu.add_checkbutton(label="Show Jupiter Visibility", variable=self.show_jupiter_var,
                                 command=self.on_jupiter)
        menubar.add_cascade(label="Display", menu=dispmenu)

        # Help menu
        helpmenu = tk.Menu(menubar, tearoff=0)
        helpmenu.add_command(label="About", command=self.on_about)
        menubar.add_cascade(label="Help", menu=helpmenu)

        self.config(menu=menubar)

    def create_widgets(self):
        # Main container
        main_frame = tk.Frame(self)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Create matplotlib figure and canvas for plots
        plot_frame = tk.Frame(main_frame)
        plot_frame.pack(fill=tk.BOTH, expand=True)

        self.figure = Figure(figsize=(8, 4))
        self.canvas = FigureCanvasTkAgg(self.figure, plot_frame)
        self.canvas.get_tk_widget().pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        # Add navigation toolbar
        self.toolbar = NavigationToolbar2Tk(self.canvas, plot_frame)
        self.toolbar.update()

        # Info text area
        info_frame = tk.Frame(main_frame)
        info_frame.pack(fill=tk.BOTH, expand=True)

        scrollbar = tk.Scrollbar(info_frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.info = tk.Text(info_frame, wrap=tk.WORD, yscrollcommand=scrollbar.set,
                           height=20, state=tk.NORMAL)
        self.info.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.config(command=self.info.yview)

    def on_add_files(self):
        """
        Open a file or files.
        """

        filenames = filedialog.askopenfilenames(
            title="Choose file(s)",
            initialdir=self.dirname,
            filetypes=[("All files", "*.*")]
        )

        if filenames:
            self.dirname = os.path.dirname(filenames[0])
            for filename in filenames:
                if filename not in self.filenames:
                    self.filenames.append(filename)

            if self.data is None:
                self.data = Visualization_GUI(self)

            self.data.loadFiles()
            self.data.draw()

    def on_remove_files(self):
        """
        Remove a file or files.
        """

        RemoveFilesDialog(self)

    def on_daynight(self):
        """
        Toggle whether or not the day/night indicator is shown.
        """

        if self.data is not None:
            self.data.showDayNight = self.show_daynight_var.get()
            self.data.draw()

    def on_jupiter(self):
        """
        Toggle whether or not the Jupiter visibility indicator is shown.
        """

        if self.data is not None:
            self.data.showJupiter = self.show_jupiter_var.get()
            self.data.draw()

    def on_about(self):
        """
        Display a very very very brief 'about' window.
        """

        about_window = tk.Toplevel(self)
        about_window.title("About Visualize Sessions")
        about_window.geometry("400x300")
        about_window.resizable(False, False)

        # Add name and version
        tk.Label(about_window, text="Visualize Sessions", font=("Arial", 14, "bold")).pack(pady=10)
        tk.Label(about_window, text=f"Version: {__version__}").pack()

        # Description
        description = """GUI for displaying the current
LWA1 schedule via its SDFs."""

        desc_label = tk.Label(about_window, text=description, justify=tk.CENTER)
        desc_label.pack(pady=10)

        # Website
        website_frame = tk.Frame(about_window)
        website_frame.pack(pady=5)
        tk.Label(website_frame, text="Website: ").pack(side=tk.LEFT)
        website_link = tk.Label(website_frame, text="http://lwa.unm.edu", fg="blue", cursor="hand2")
        website_link.pack(side=tk.LEFT)
        website_link.bind("<Button-1>", lambda e: self.open_website("http://lwa.unm.edu"))

        # Developer
        tk.Label(about_window, text=f"Developer: {__author__}").pack(pady=2)
        tk.Label(about_window, text=f"Documentation: {__author__}").pack(pady=2)

        # Close button
        tk.Button(about_window, text="Close", command=about_window.destroy).pack(pady=10)

    def open_website(self, url):
        """
        Open a website URL in the default browser
        """
        import webbrowser
        webbrowser.open(url)

    def on_quit(self):
        """
        Quit the main window.
        """

        self.destroy()


class RemoveFilesDialog(tk.Toplevel):
    """
    Class to implement a window that displays a checkable list of filenames so
    that the selected files can be removed from the plot window.
    """

    def __init__(self, parent):
        super().__init__(parent)

        self.title('Select Files to Remove')
        self.geometry('600x300')

        self.parent = parent

        self.create_widgets()
        self.load_files()

    def create_widgets(self):
        """
        Start the user interface.
        """

        # Main frame
        main_frame = tk.Frame(self)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # Create Treeview with checkboxes
        tree_frame = tk.Frame(main_frame)
        tree_frame.pack(fill=tk.BOTH, expand=True)

        # Scrollbar
        scrollbar = tk.Scrollbar(tree_frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # Treeview
        self.tree = ttk.Treeview(tree_frame, columns=('filename',), show='tree headings',
                                yscrollcommand=scrollbar.set)
        self.tree.heading('#0', text='')
        self.tree.heading('filename', text='Filename')
        self.tree.column('#0', width=30)
        self.tree.column('filename', width=550)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.config(command=self.tree.yview)

        # Track checked items
        self.checked_items = set()

        # Bind click event for checkboxes
        self.tree.bind('<Button-1>', self.on_click)

        # Buttons
        button_frame = tk.Frame(main_frame)
        button_frame.pack(fill=tk.X, pady=5)

        ok_button = tk.Button(button_frame, text="Ok", width=10, command=self.on_ok)
        ok_button.pack(side=tk.LEFT, padx=5)

        cancel_button = tk.Button(button_frame, text="Cancel", width=10, command=self.on_cancel)
        cancel_button.pack(side=tk.LEFT, padx=5)

    def on_click(self, event):
        """Handle checkbox clicks"""
        region = self.tree.identify("region", event.x, event.y)
        if region == "tree":
            item = self.tree.identify_row(event.y)
            if item:
                if item in self.checked_items:
                    self.checked_items.remove(item)
                    self.tree.item(item, text='☐')
                else:
                    self.checked_items.add(item)
                    self.tree.item(item, text='☑')

    def load_files(self):
        """
        Setup the checkable list and populate it with what is currently loaded.
        """

        # Fill with filenames
        for filename in self.parent.filenames:
            self.tree.insert('', 'end', text='☐', values=(filename,))

    def on_ok(self):
        """
        Process the checklist and remove as necessary.
        """

        # Build a list of filenames to remove
        to_remove = []
        for item in self.checked_items:
            values = self.tree.item(item, 'values')
            if values:
                to_remove.append(values[0])

        # Remove them
        for filename in to_remove:
            if filename in self.parent.filenames:
                self.parent.filenames.remove(filename)

        # Reload the SDFs and update the plot if needed
        if len(to_remove) > 0 and self.parent.data is not None:
            self.parent.data.loadFiles()
            self.parent.data.draw()

        self.destroy()

    def on_cancel(self):
        """
        Quit without deleting any files.
        """

        self.destroy()


def main(args):
    frame = MainWindow()
    if args.filename is not None:
        frame.filenames = args.filename

        if args.lwasv:
            station = 'lwasv'
        elif args.lwana:
            station = 'lwana'
        else:
            station = 'lwa1'

        frame.data = Visualization_GUI(frame, station=station)
        frame.data.loadFiles()
        frame.data.draw()

    frame.mainloop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description='GUI for looking at the schedule on a station',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        )
    parser.add_argument('filename', type=str, nargs='+',
                        help='SDF file to examine')
    sgroup = parser.add_mutually_exclusive_group(required=False)
    sgroup.add_argument('-s', '--lwasv', action='store_true',
                        help='files are for LWA-SV instead of LWA1')
    sgroup.add_argument('-n', '--lwana', action='store_true',
                        help='files are for LWA-NA instead of LWA1')
    args = parser.parse_args()
    main(args)
