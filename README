Session Schedules
=================

Overview and Requirements
-------------------------
Session Schedules is a collection of python modules and scripts for 
building MCS0030 compliant session definition files (SDFs).  Session 
Schedules builds off of the LSL 0.6.x framework for working with LWA
data and also depends on the following python modules:
  * pytz
  * wxPython

Note:  LSL version 0.6.2 or later is required to use the latest version 
       of sessionGUI.py.

No installation (e.g., python setup.py install) is required to use the
software.  Simply run the scripts in the SessionSchedules directory.

Python Contents
---------------
estimateData.py
  Simple command-line utility for estimating the data volume for a TBN or 
  DRX observation.

sessionGUI.py
  wxPython GUI for creating SDF from scratch or modifying existing SDF for
  another purpose.  sessionGUI contains a variety of features for working 
  with SDFs including a name resolver, a graphical observation layout, and 
  a data volume estimator.

shiftSDF.py
  Multi-purpose tool for shifting SDF files.  This utility allows the operator
  to:
    * Move a SDF file to a new start date/time
    * Move a SDF file to a new UTC date but the same LST
    * Apply a pointing correction (currently ~430 seconds in RA) to
      the observations
    * Switch the session ID to a new value
    * Convert TRK_SOL and TRK_JOV observations to TRK_RADEC
    * Only update one of the above and leave the time alone
    * Print out the contents of the SDF file in an easy-to-digest manner

visualizeSessions.py
  Tool for operators to examine multiple SDFs at the same time.

efficianado.py
  Script to pack a collection of SDFs into the smallest possible time period.

resolveCLI.py
  Script to provide a name resolver that can be run from the command line.

validatorCLI.py
  Script to validate SDFs via the validation interfaces available 
  in sdf.py and with the tpss executable.  This validator runs tpss up to level 2.

Other Contents
--------------
docs
  Directory containing on-line documentation for sessionGUI.py

examples
  Directory containing example SDFs from MCS.

icons
  Directory containing icons used by sessionGUI.py

