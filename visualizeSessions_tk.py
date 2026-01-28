#!/usr/bin/env python3

import os
import sys
import math
import pytz
import ephem
import numpy
import argparse
from datetime import datetime, timedelta

from lsl.common import sdf, metabundle, sdfADP, metabundleADP, sdfNDP, metabundleNDP
from lsl.common import stations
from lsl.astro import utcjd_to_unix, MJD_OFFSET

import tkinter as tk
from tkinter import ttk, messagebox, filedialog

import matplotlib
matplotlib.use('TkAgg')
matplotlib.interactive(True)

from matplotlib.backends.backend_tkagg import NavigationToolbar2Tk, FigureCanvasTkAgg
from matplotlib.figure import Figure

import matplotlib.dates
from matplotlib import pyplot as plt
from matplotlib.collections import LineCollection


class NavigationToolbar(NavigationToolbar2Tk):
    """
    Custom navigation toolbar that suppresses the coordinate display.
    Coordinates are shown in a separate status bar instead.
    """

    def set_message(self, s):
        """Suppress coordinate display - handled by our status bar."""
        pass


__version__ = "0.3"
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


class FilesListCtrl(ttk.Treeview):
    """
    Treeview that displays files with checkboxes for selection.
    """

    def __init__(self, parent, **kwargs):
        self.columns = ('filename',)
        super().__init__(parent, columns=self.columns, show='tree headings',
                         selectmode='extended', **kwargs)

        self.parent = parent
        self._check_states = {}  # Track checkbox states by item id

        # Setup columns
        self.heading('filename', text='Filename')
        self.column('filename', width=500, minwidth=200)
        self.column('#0', width=30, minwidth=30, stretch=False)

        # Bind click events for checkbox toggling
        self.bind('<Button-1>', self._on_click)

    def _on_click(self, event):
        """Handle clicks to toggle checkbox state."""
        region = self.identify_region(event.x, event.y)
        if region == 'tree':
            item = self.identify_row(event.y)
            if item:
                self.CheckItem(item, not self.IsChecked(item))

    def InsertItem(self, index, filename):
        """Insert an item with a checkbox."""
        item = self.insert('', index, values=(filename,), text='[ ]')
        self._check_states[item] = False
        return item

    def CheckItem(self, item, check=True):
        """Set the check state of an item."""
        self._check_states[item] = check
        self.item(item, text='[X]' if check else '[ ]')

    def IsChecked(self, item):
        """Get the check state of an item."""
        return self._check_states.get(item, False)

    def GetItemCount(self):
        """Return the number of items."""
        return len(self.get_children())

    def GetItemAtIndex(self, index):
        """Get the item at a specific index."""
        children = self.get_children()
        if 0 <= index < len(children):
            return children[index]
        return None


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
        self.ax1 = None
        self.ax2 = None
        self.solar_times = None
        self.solar_alts = None
        self.jupiter_times = None
        self.jupiter_alts = None

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


    def getJovianAltitude(self, step=timedelta(seconds=900)):
        """
        Using the time range of all loaded SDFs, return a two-element tuple of the
        time and altitude for Jupiter in 15 minute steps.
        """

        # Find out how long we need to compute the position of the Sun
        start = min(self.sessionStarts)
        stop  = max(self.sessionStops)

        # Setup Jupiter
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

            points.append([matplotlib.dates.date2num(tNow),6])
            alts.append(alt)

            tNow += step

        return numpy.array(points), numpy.array(alts)

    def draw(self, selected=None):
        """
        Shows the sessions.
        """

        self.frame.figure.clf()
        self.ax1 = self.frame.figure.gca()

        # Plot the sessions
        startMin = min(self.sessionStarts)
        startMax = max(self.sessionStarts)
        for s,(name,beam,start,duration) in enumerate(zip(self.sessionNames, self.sessionBeams, self.sessionStarts, self.sessionDurations)):
            d = duration.days*24*3600 + duration.seconds + duration.microseconds/1e6
            d /= 3600.0

            i = self.uniqueProjects.index(name.split('_')[0])
            if s == selected:
                alpha = 0.5
            else:
                alpha = 0.2
            self.ax1.barh(beam-0.5, d/24, left=start, height=1.0, alpha=alpha, color=self.colors[i % len(self.colors)])

            self.ax1.text(start+duration/2, beam, name, size=10, horizontalalignment='center', verticalalignment='center', rotation='vertical')

        # Plot the free time more than 30 minutes
        for s,(free1,free2) in enumerate(self.freePeriods):
            duration = free2 - free1
            d = duration.days*24*3600 + duration.seconds + duration.microseconds/1e6
            d /= 3600.0
            if d < 0.5:
                continue

            if -(s+1) == selected:
                alpha = 0.5
            else:
                alpha = 0.2
            self.ax1.barh(-0.5, d/24, left=free1, alpha=alpha, height=1.0, color='r', hatch='/')
            self.ax1.text(free1+duration/2, 0, '%i:%02i' % (int(d), int((d-int(d))*60)), size=10, horizontalalignment='center', verticalalignment='center', rotation='vertical')

        # Plot Sun altitude in a way that indicates day and night (if needed)
        if self.showDayNight:
            points, alts = self.getSolarAltitude()
            self.solar_times = points[:, 0].copy()  # Store for mouseover lookup
            self.solar_alts = alts.copy()
            points = points.reshape((-1, 1, 2))
            if self.ndp:
                points[:,:,1] = 4.75
            elif self.adp:
                points[:,:,1] = 3.75
            else:
                points[:,:,1] = 5.75
            segments = numpy.concatenate([points[:-1], points[1:]], axis=1)
            lc = LineCollection(segments, cmap=plt.get_cmap('Blues_r'), norm=plt.Normalize(-18, 0.25))
            lc.set_array(alts)
            lc.set_linewidth(5)
            self.ax1.add_collection(lc)
        else:
            self.solar_times = None
            self.solar_alts = None

        # Plot Jupiter's altitude (if needed)
        if self.showJupiter:
            points, alts = self.getJovianAltitude()
            self.jupiter_times = points[:, 0].copy()  # Store for mouseover lookup
            self.jupiter_alts = alts.copy()
            points = points.reshape((-1, 1, 2))
            points[:,:,1] = -1.75
            segments = numpy.concatenate([points[:-1], points[1:]], axis=1)
            lc = LineCollection(segments, cmap=plt.get_cmap('RdYlGn'), norm=plt.Normalize(0, 90))
            lc.set_array(alts)
            lc.set_linewidth(5)
            self.ax1.add_collection(lc)
        else:
            self.jupiter_times = None
            self.jupiter_alts = None

        # Fix the x axis labels so that we have both MT and UT
        self.ax2 = self.ax1.twiny()
        self.ax2.set_xticks(self.ax1.get_xticks())
        self.ax2.set_xlim(self.ax1.get_xlim())

        self.ax1.xaxis.set_major_formatter( matplotlib.dates.DateFormatter("%Y-%m-%d\n%H:%M:%S", tz=_UTC))
        self.ax1.set_xlabel('Time [UTC]')
        self.ax2.xaxis.set_major_formatter(matplotlib.dates.DateFormatter("%Y-%m-%d\n%H:%M:%S", tz=_MST))
        self.ax2.set_xlabel('Time [Mountain]')
        self.frame.figure.autofmt_xdate()

        # Fix the y axis labels to use beams, free time, etc.
        if self.showDayNight:
            if self.ndp:
                lower = 5
            elif self.adp:
                lower = 4
            else:
                lower = 6
        else:
            if self.ndp:
                lower = 4.5
            elif self.adp:
                lower = 3.5
            else:
                lower = 5.5
        if self.showJupiter:
            upper = -2
        else:
            upper = -1.5
        self.ax1.set_ylim((lower, upper))
        if self.ndp:
            self.ax1.set_yticks([5, 4.75, 4, 3, 2, 1, 0, -1, -1.75, -2])
            self.ax1.set_yticklabels(['', 'Day/Night', 'Beam 4', 'Beam 3', 'Beam 2', 'Beam 1', 'Unassigned', 'MCS Decides', 'Jupiter', ''])
        elif self.adp:
            self.ax1.set_yticks([4, 3.75, 3, 2, 1, 0, -1, -1.75, -2])
            self.ax1.set_yticklabels(['', 'Day/Night', 'TBN', 'Beam 2', 'Beam 1', 'Unassigned', 'MCS Decides', 'Jupiter', ''])
        else:
            self.ax1.set_yticks([6, 5.75, 5, 4, 3, 2, 1, 0, -1, -1.75, -2])
            self.ax1.set_yticklabels(['', 'Day/Night', 'TBN/TBW', 'Beam 4', 'Beam 3', 'Beam 2', 'Beam 1', 'Unassigned', 'MCS Decides', 'Jupiter', ''])

        try:
            self.frame.figure.tight_layout()
        except ValueError:
            # tight_layout can fail with certain axis configurations
            pass

        self.frame.canvas.draw()

    def describeSDF(self, sdfIndex):
        """
        Given an self.sessionSDFs index, display the SDF file in a descriptive manner.
        This function returns a string (like a __str__ call).
        """

        # Get the SDF/data file collection in question
        project = self.sessionSDFs[sdfIndex]
        dataFile = self.sessionDataFiles[sdfIndex]

        nObs = len(project.sessions[0].observations)
        tStart = [None,]*nObs
        for i in range(nObs):
            tStart[i]  = utcjd_to_unix(project.sessions[0].observations[i].mjd + MJD_OFFSET)
            tStart[i] += project.sessions[0].observations[i].mpm / 1000.0
            tStart[i]  = datetime.utcfromtimestamp(tStart[i])
            tStart[i]  = _UTC.localize(tStart[i])

        # Get the LST at the start
        self.observer.date = (min(tStart)).strftime('%Y/%m/%d %H:%M:%S')
        lst = self.observer.sidereal_time()

        out = ""
        out += " Project ID: %s\n" % project.id
        out += " Session ID: %i\n" % project.sessions[0].id
        out += " Observations appear to start at %s\n" % (min(tStart)).strftime(formatString)
        out += " -> LST at %s for this date/time is %s\n" % (self.observer.name, lst)

        lastDur = project.sessions[0].observations[nObs-1].dur
        lastDur = timedelta(seconds=int(lastDur/1000), microseconds=(lastDur*1000) % 1000000)
        sessionDur = max(tStart) - min(tStart) + lastDur

        out += "\n"
        out += " Total Session Duration: %s\n" % sessionDur
        out += " -> First observation starts at %s\n" % min(tStart).strftime(formatString)
        out += " -> Last observation ends at %s\n" % (max(tStart) + lastDur).strftime(formatString)
        if project.sessions[0].observations[0].mode not in ('TBW', 'TBN'):
            drspec = 'No'
            if project.sessions[0].spcSetup[0] != 0 and project.sessions[0].spcSetup[1] != 0:
                drspec = 'Yes'
            drxBeam = project.sessions[0].drx_beam
            if drxBeam < 1:
                drxBeam = "MCS decides"
            else:
                drxBeam = "%i" % drxBeam
            out += " DRX Beam: %s\n" % drxBeam
            out += " DR Spectrometer used? %s\n" % drspec
            if drspec == 'Yes':
                out += " -> %i channels, %i windows/integration\n" % tuple(project.sessions[0].spcSetup)
        else:
            out += " Transient Buffer: %s\n" % ('Wide band' if project.sessions[0].observations[0].mode == 'TBW' else 'Narrow band',)

        out += "\n"
        out += " Number of observations: %i\n" % nObs
        out += " Observation Detail:\n"
        for i in range(nObs):
            currDur = project.sessions[0].observations[i].dur
            currDur = timedelta(seconds=int(currDur/1000), microseconds=(currDur*1000) % 1000000)

            out += "  Observation #%i\n" % (i+1,)

            ## Basic setup
            out += "   Target: %s\n" % project.sessions[0].observations[i].target
            out += "   Mode: %s\n" % project.sessions[0].observations[i].mode
            out += "   Start:\n"
            out += "    MJD: %i\n" % project.sessions[0].observations[i].mjd
            out += "    MPM: %i\n" % project.sessions[0].observations[i].mpm
            out += "    -> %s\n" % getObsStartStop(project.sessions[0].observations[i])[0].strftime(formatString)
            out += "   Duration: %s\n" % currDur

            ## DP setup
            if project.sessions[0].observations[i].mode not in ('TBW',):
                out += "   Tuning 1: %.3f MHz\n" % (project.sessions[0].observations[i].frequency1/1e6,)
            if project.sessions[0].observations[i].mode not in ('TBW', 'TBN'):
                out += "   Tuning 2: %.3f MHz\n" % (project.sessions[0].observations[i].frequency2/1e6,)
            if project.sessions[0].observations[i].mode not in ('TBW',):
                out += "   Filter code: %i\n" % project.sessions[0].observations[i].filter

            ## Comments/notes
            out += "   Observer Comments: %s\n" % project.sessions[0].observations[i].comments

            ## Data file (optional)
            if dataFile is not None:
                try:
                    dataFilename = dataFile[i+1]
                    out += "   Data File Tag: %s\n" % dataFilename['tag']
                except KeyError:
                    pass

        return out

    def describeFree(self, freeIndex):
        """
        Given a self.freePeriods index, describe a block of free time.  This function
        returns a string (like a __str__ call).
        """

        # UT and MT
        fUT = self.freePeriods[freeIndex]
        fMT = (fUT[0].astimezone(_MST), fUT[1].astimezone(_MST))
        d = fUT[1] - fUT[0]

        out  = ""
        out += "Free time between %s and %s\n" % (fUT[0].strftime(formatString), fUT[1].strftime(formatString))
        out += "               -> %s and %s\n" % (fMT[0].strftime(formatString), fMT[1].strftime(formatString))
        out += "               -> %i:%02i in length\n" % (d.days*24+d.seconds/3600, d.seconds/60 % 60)

        return out

    def connect(self):
        """
        Connect to all the events we need
        """

        self.cidpress = self.frame.figure.canvas.mpl_connect('button_press_event', self.on_press)
        self.cidmotion = self.frame.figure.canvas.mpl_connect('motion_notify_event', self.on_motion)

    def on_press(self, event):
        """
        On button press we will see if the mouse is over us and display some data
        """

        if event.inaxes:
            clickBeam = round(event.ydata)
            clickTime = matplotlib.dates.num2date(event.xdata)

            if clickBeam == 0:
                for i in range(len(self.freePeriods)):
                    if clickTime >= self.freePeriods[i][0] and clickTime <= self.freePeriods[i][1]:
                        self.frame.setInfoText(self.describeFree(i))
                        self.draw(selected=-(i+1))
            else:
                project = None
                for i in range(len(self.sessionSDFs)):
                    if clickTime >= self.sessionStarts[i] and clickTime <= self.sessionStarts[i] + self.sessionDurations[i] and clickBeam == self.sessionBeams[i]:
                        self.frame.setInfoText(self.describeSDF(i))
                        self.draw(selected=i)

    def get_row_label(self, ydata):
        """
        Convert a y-axis value to a descriptive row label.
        Handles beams, Day/Night, Jupiter, etc. based on station type.
        """

        if ydata is None:
            return ""

        # Round to nearest 0.25 to catch fractional rows (Day/Night, Jupiter)
        y_rounded = round(ydata * 4) / 4

        # Jupiter row (all station types)
        if -2.0 < y_rounded <= -1.5:
            return "Jupiter"

        # MCS Decides row
        if -1.5 < y_rounded <= -0.5:
            return "MCS Decides"

        # Free time / Unassigned row
        if -0.5 < y_rounded <= 0.5:
            return "Free Time"

        # Beam rows (station-dependent)
        if self.ndp:
            # NDP: Beams 1-4, Day/Night at 4.75
            if 4.5 < y_rounded <= 5.0:
                return "Day/Night"
            elif 3.5 < y_rounded <= 4.5:
                return "Beam 4"
            elif 2.5 < y_rounded <= 3.5:
                return "Beam 3"
            elif 1.5 < y_rounded <= 2.5:
                return "Beam 2"
            elif 0.5 < y_rounded <= 1.5:
                return "Beam 1"
        elif self.adp:
            # ADP: Beams 1-2, TBN at 3, Day/Night at 3.75
            if 3.5 < y_rounded <= 4.0:
                return "Day/Night"
            elif 2.5 < y_rounded <= 3.5:
                return "TBN"
            elif 1.5 < y_rounded <= 2.5:
                return "Beam 2"
            elif 0.5 < y_rounded <= 1.5:
                return "Beam 1"
        else:
            # LWA1: Beams 1-4, TBN/TBW at 5, Day/Night at 5.75
            if 5.5 < y_rounded <= 6.0:
                return "Day/Night"
            elif 4.5 < y_rounded <= 5.5:
                return "TBN/TBW"
            elif 3.5 < y_rounded <= 4.5:
                return "Beam 4"
            elif 2.5 < y_rounded <= 3.5:
                return "Beam 3"
            elif 1.5 < y_rounded <= 2.5:
                return "Beam 2"
            elif 0.5 < y_rounded <= 1.5:
                return "Beam 1"

        return ""

    def on_motion(self, event):
        """
        Handle mouse motion to update the status bar with coordinates.
        """

        # Guard against ax1 not existing yet
        if self.ax1 is None:
            return

        # Check both ax1 and ax2 since twin axis may be "on top" for mouse events
        if event.inaxes in (self.ax1, self.ax2):
            # Format time from x-axis
            try:
                t = matplotlib.dates.num2date(event.xdata)
                time_str = t.strftime("%Y-%m-%d %H:%M:%S UTC")
            except (ValueError, OverflowError):
                time_str = ""

            # Get row label from y-axis
            row_label = self.get_row_label(event.ydata)

            # Enhance Day/Night label with sun altitude
            if row_label == "Day/Night" and self.solar_times is not None:
                try:
                    alt = numpy.interp(event.xdata, self.solar_times, self.solar_alts)
                    if alt > 0:
                        row_label = f"Day - Sun @ {alt:.0f}°"
                    elif alt > -6:
                        row_label = f"Civil Twilight - Sun @ {alt:.0f}°"
                    elif alt > -12:
                        row_label = f"Nautical Twilight - Sun @ {alt:.0f}°"
                    elif alt > -18:
                        row_label = f"Astronomical Twilight - Sun @ {alt:.0f}°"
                    else:
                        row_label = f"Night - Sun @ {alt:.0f}°"
                except (ValueError, IndexError):
                    pass

            # Enhance Jupiter label with altitude
            if row_label == "Jupiter" and self.jupiter_times is not None:
                try:
                    alt = numpy.interp(event.xdata, self.jupiter_times, self.jupiter_alts)
                    if alt > 0:
                        row_label = f"Jupiter @ {alt:.0f}° alt"
                    else:
                        row_label = f"Jupiter below horizon ({alt:.0f}°)"
                except (ValueError, IndexError):
                    pass

            if time_str and row_label:
                self.frame.status_var.set(f"{time_str}  |  {row_label}")
            elif time_str:
                self.frame.status_var.set(time_str)
            else:
                self.frame.status_var.set("")
        else:
            self.frame.status_var.set("")

    def disconnect(self):
        """
        Disconnect all the stored connection IDs.
        """

        self.frame.figure.canvas.mpl_disconnect(self.cidpress)
        self.frame.figure.canvas.mpl_disconnect(self.cidmotion)


class MainWindow(tk.Tk):
    """
    Main Tkinter window for displaying the sessions and adding/removing files.
    """

    def __init__(self, args):
        super().__init__()

        self.dirname = ''
        self.scriptPath = os.path.abspath(__file__)
        self.scriptPath = os.path.split(self.scriptPath)[0]

        self.data = None
        self.filenames = []

        self.title("Visualize Sessions")
        self.geometry("600x800")

        self.initUI()
        self.initEvents()

        # Process command line arguments
        if args.filename is not None:
            self.filenames = args.filename

            if args.lwasv:
                station = 'lwasv'
            elif args.lwana:
                station = 'lwana'
            else:
                station = 'lwa1'

            self.data = Visualization_GUI(self, station=station)
            self.data.loadFiles()
            self.data.draw()

    def initUI(self):
        """
        Start the user interface.
        """

        # Create menu bar
        menubar = tk.Menu(self)

        # File menu
        fileMenu = tk.Menu(menubar, tearoff=0)
        fileMenu.add_command(label='Add File(s)', command=self.onAddFiles, accelerator='Ctrl+O')
        fileMenu.add_command(label='Remove File(s)', command=self.onRemoveFiles)
        fileMenu.add_separator()
        fileMenu.add_command(label='Quit', command=self.onQuit, accelerator='Ctrl+Q')
        menubar.add_cascade(label='File', menu=fileMenu)

        # Display menu
        dispMenu = tk.Menu(menubar, tearoff=0)
        self.showDayNightVar = tk.BooleanVar(value=True)
        self.showJupiterVar = tk.BooleanVar(value=False)
        dispMenu.add_checkbutton(label='Show Day/Night', variable=self.showDayNightVar,
                                 command=self.onDayNight)
        dispMenu.add_checkbutton(label='Show Jupiter Visibility', variable=self.showJupiterVar,
                                 command=self.onJupiter)
        menubar.add_cascade(label='Display', menu=dispMenu)

        # Help menu
        helpMenu = tk.Menu(menubar, tearoff=0)
        helpMenu.add_command(label='About', command=self.onAbout)
        menubar.add_cascade(label='Help', menu=helpMenu)

        self.config(menu=menubar)

        # Main container using PanedWindow for resizable split
        paned = ttk.PanedWindow(self, orient=tk.VERTICAL)
        paned.pack(fill=tk.BOTH, expand=True)

        # Top frame for plot
        plot_frame = ttk.Frame(paned)
        paned.add(plot_frame, weight=1)

        # Create a container for toolbar and status bar at bottom of plot_frame
        # Pack this FIRST with side=BOTTOM so canvas can fill remaining space
        bottom_bar = ttk.Frame(plot_frame)
        bottom_bar.pack(side=tk.BOTTOM, fill=tk.X)

        # Navigation toolbar (inside bottom_bar, will pack itself)
        # We create a sub-frame so the toolbar's auto-pack stays contained
        toolbar_frame = ttk.Frame(bottom_bar)
        toolbar_frame.pack(side=tk.TOP, fill=tk.X)

        # Status bar for coordinate display (fixed width, won't cause layout shifts)
        self.status_var = tk.StringVar(value="Ready")
        self.status_bar = ttk.Label(bottom_bar, textvariable=self.status_var,
                                    anchor='w', width=80, relief='sunken', padding=(5, 2))
        self.status_bar.pack(side=tk.TOP, fill=tk.X, padx=2, pady=(0, 4))

        # Create matplotlib figure and canvas
        self.figure = Figure(figsize=(6, 4), dpi=100)
        self.canvas = FigureCanvasTkAgg(self.figure, master=plot_frame)
        self.canvas.get_tk_widget().pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        # Create toolbar after canvas (it needs the canvas reference)
        self.toolbar = NavigationToolbar(self.canvas, toolbar_frame)
        self.toolbar.update()

        # Bottom frame for info panel
        info_frame = ttk.Frame(paned)
        paned.add(info_frame, weight=1)

        # Info text widget with scrollbar
        info_scroll = ttk.Scrollbar(info_frame)
        info_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self.info = tk.Text(info_frame, wrap=tk.WORD, height=15,
                           yscrollcommand=info_scroll.set, state='disabled')
        self.info.pack(fill=tk.BOTH, expand=True)
        info_scroll.config(command=self.info.yview)

    def initEvents(self):
        """
        Set all of the various events in the main window.
        """

        # Keyboard shortcuts
        self.bind('<Control-o>', lambda e: self.onAddFiles())
        self.bind('<Control-q>', lambda e: self.onQuit())

        # Window close
        self.protocol("WM_DELETE_WINDOW", self.onQuit)

    def setInfoText(self, text):
        """Set the info panel text."""
        self.info.config(state='normal')
        self.info.delete('1.0', tk.END)
        self.info.insert('1.0', text)
        self.info.config(state='disabled')

    def onAddFiles(self):
        """
        Open a file or files.
        """

        filenames = filedialog.askopenfilenames(
            initialdir=self.dirname,
            title="Choose file(s)",
            filetypes=[('All Files', '*.*')]
        )

        if filenames:
            # Update dirname from first file
            if len(filenames) > 0:
                self.dirname = os.path.dirname(filenames[0])

            for filename in filenames:
                if filename not in self.filenames:
                    self.filenames.append(filename)

            if self.data is None:
                self.data = Visualization_GUI(self)

            self.data.loadFiles()
            self.data.draw()

    def onRemoveFiles(self):
        """
        Remove a file or files.
        """

        if self.filenames:
            RemoveFilesDialog(self)

    def onDayNight(self):
        """
        Toggle whether or not the day/night indicator is shown.
        """

        if self.data is not None:
            self.data.showDayNight = self.showDayNightVar.get()
            self.data.draw()

    def onJupiter(self):
        """
        Toggle whether or not the Jupiter visibility indicator is shown.
        """

        if self.data is not None:
            self.data.showJupiter = self.showJupiterVar.get()
            self.data.draw()

    def onAbout(self):
        """
        Display a very very very brief 'about' window.
        """

        about_text = f"""Visualize Sessions
Version {__version__}

GUI for displaying the current LWA1 schedule via its SDFs.

Developer: {__author__}
Website: http://lwa.unm.edu"""

        messagebox.showinfo('About Visualize Sessions', about_text)

    def onQuit(self):
        """
        Quit the main window.
        """

        self.destroy()


class RemoveFilesDialog(tk.Toplevel):
    """
    Dialog to display a checkable list of filenames so that selected files
    can be removed from the plot window.
    """

    def __init__(self, parent):
        super().__init__(parent)

        self.parent = parent

        self.title('Select Files to Remove')
        self.geometry('600x300')
        self.transient(parent)

        self.initUI()
        self.loadFiles()

        self.grab_set()  # Make modal

    def initUI(self):
        """
        Start the user interface.
        """

        # Main frame
        main_frame = ttk.Frame(self)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # File list with scrollbar
        list_frame = ttk.Frame(main_frame)
        list_frame.pack(fill=tk.BOTH, expand=True)

        scrollbar = ttk.Scrollbar(list_frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.listControl = FilesListCtrl(list_frame, yscrollcommand=scrollbar.set)
        self.listControl.pack(fill=tk.BOTH, expand=True)
        scrollbar.config(command=self.listControl.yview)

        # Buttons
        button_frame = ttk.Frame(main_frame)
        button_frame.pack(fill=tk.X, pady=(10, 0))

        ok_btn = ttk.Button(button_frame, text='OK', command=self.onOK, width=10)
        ok_btn.pack(side=tk.LEFT, padx=5)

        cancel_btn = ttk.Button(button_frame, text='Cancel', command=self.onCancel, width=10)
        cancel_btn.pack(side=tk.LEFT, padx=5)

    def loadFiles(self):
        """
        Setup the checkable list and populate it with what is currently loaded.
        """

        for i, filename in enumerate(self.parent.filenames):
            self.listControl.InsertItem(i, filename)

    def onOK(self):
        """
        Process the checklist and remove as necessary.
        """

        # Build a list of filenames to remove
        toRemove = []
        children = self.listControl.get_children()
        for i, item in enumerate(children):
            if self.listControl.IsChecked(item):
                toRemove.append(self.parent.filenames[i])

        # Remove them
        for filename in toRemove:
            self.parent.filenames.remove(filename)

        # Reload the SDFs and update the plot if needed
        if len(toRemove) > 0:
            self.parent.data.loadFiles()
            self.parent.data.draw()

        self.destroy()

    def onCancel(self):
        """
        Quit without deleting any files.
        """

        self.destroy()


def main(args):
    app = MainWindow(args)
    app.mainloop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description='GUI for looking at the schedule on a station',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        )
    parser.add_argument('filename', type=str, nargs='*', default=None,
                        help='SDF file to examine')
    sgroup = parser.add_mutually_exclusive_group(required=False)
    sgroup.add_argument('-s', '--lwasv', action='store_true',
                        help='files are for LWA-SV instead of LWA1')
    sgroup.add_argument('-n', '--lwana', action='store_true',
                        help='files are for LWA-NA instead of LWA1')
    args = parser.parse_args()

    # Handle empty filename list
    if args.filename is not None and len(args.filename) == 0:
        args.filename = None

    main(args)
