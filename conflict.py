"""
Assign beams for plotting based on start and stop times of a set of observations.  

This module helps Session GUI make intelegent plotting decissions for DRX 
observations when generating the "Session at a Glance" plots.  The primary function
in this modules, assignBeams, takes a list of sdf.Observation objects and returns
a list of beam ID (zero indexed) and deals with conflicts by mapping all conflicts
to beam 0.
"""

# Python2 compatibility
from __future__ import print_function, division
 
from functools import cmp_to_key

__version__ = '0.1'
__all__ = ['lowestIdleBeam', 'unravelObs', 'assignBeams']


def lowestIdleBeam(beams):
    """
    Given a list of active beams, return the lowest index with
    a value of -1.  If all beams are active, return -1.
    """
    
    for i in range(len(beams)):
        if beams[i] == -1:
            return i
    return -1


def unravelObs(obs):
    """
    Given a list of sdf.Observation instances, unravel them into a
    time ordered list of tuples storing time, observation, and whether
    the event is a start (1) or stop (0).
    """
    
    # Loop over the observations.  We will worry about the time order 
    # later
    rObs = []
    for i in range(len(obs)):
        iStart =  obs[i].mjd + obs[i].mpm/1000.0/3600.0/24.0
        iStop = iStart + obs[i].dur/1000.0/3600.0/24.0
        
        rObs.append( (iStart, i, 1) )
        rObs.append( (iStop,  i, 0) )
    
    def cmpObs(x, y):
        """
        Comparison function for the list of three-element tuples 
        generated in the parent function.  This sorts the list by time 
        then observation ID number.
        """
        
        f1X = x[0]
        f2X = x[1]
        f1Y = y[0]
        f2Y = y[1]
        
        if f1X < f1Y:
            return -1
        elif f1X > f1Y:
            return 1
        else:
            if f2X < f2Y:
                return -1
            elif f2X > f2Y:
                return 1
            else:
                return 0
    
    # Sort and return
    return sorted(rObs, key=cmp_to_key(cmpObs))


def assignBeams(obs, nBeams=4):
    """
    Given a list of sdf.Observation instances, return a list of beam IDs 
    that have been assigned to each observation.  If conflicts exist, i.e., 
    there are more simultaneous observations than beams, the conflicting 
    beams are mapped to the first beam.
    """
    
    beams = [-1]*len(obs)
    activeBeams = [-1]*nBeams
    
    # Unravel the observations
    sObs = unravelObs(obs)
    
    # Loop over the unraveled observations
    for t,o,m in sObs:
        if m:
            # Observation starts
            toUse = lowestIdleBeam(activeBeams)
            
            # If there are no free beams (a conflict) we put the conflicting
            # observation into beam 0.
            if toUse == -1:
                toUse = 0
            
            beams[o] = toUse
            activeBeams[toUse] = o
        else:
            # Observation ends
            try:
                # Why is there a try...except block here?  Because of the
                # conflict resolution that the other part of this if...else
                # block uses.  If we can't find the observation that probably
                # means its part of zero.
                toStop = activeBeams.index(o)
                activeBeams[toStop] = -1
            except ValueError:
                pass
    
    return beams
