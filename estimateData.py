#!/usr/bin/env python

"""
Simple script for estimating the data volume for a TBN or DRX observation.

Usage:
estimateData.py <mode> <filter code>  HH:MM:SS.SSS

.. note::
    For spectrometer data, the mode is given by SPC,<tlen>,<icount> where
    'tlen' is the transform length (channel count) and 'icount' is the 
    number of transforms per integration.
"""

import os
import sys
import argparse

from lsl.reader.tbn import FRAME_SIZE as tbnFRAME_SIZE
from lsl.reader.tbn import FILTER_CODES as tbn_filters
from lsl.reader.drx import FRAME_SIZE as drxFRAME_SIZE
from lsl.reader.drx import FILTER_CODES as drx_filters
from lsl.misc import parser as aph


def main(args):
    # Parse the command line
    mode = args.mode
    filterCode = args.filter_code
    duration = args.duration

    # Convert the HH:MM:SS.SSS string to a duration in seconds
    try:
        hour, minute, second = duration.split(':', 2)
    except ValueError:
        try:
            hour = '0'
            minute, second = duration.split(':', 1)
        except ValueError:
            hour = '0'
            minute = '0'
            second = duration
    hour = int(hour)
    minute = int(minute)
    second = float(second)
    duration = hour*3600 + minute*60 + second

    # Figure out the data rate
    if mode == 'TBN':
        antpols = 520
        sample_rate = tbn_filters[filterCode]
        dataRate = 1.0*sample_rate/512*tbnFRAME_SIZE*antpols
    elif mode == 'DRX':
        tunepols = 4
        sample_rate = drx_filters[filterCode]
        dataRate = 1.0*sample_rate/4096*drxFRAME_SIZE*tunepols
    elif mode[0:3] == 'SPC':
        try:
            junk, tlen, icount = mode.split(',', 2)
        except ValueError:
            print "Spectrometer settings transform length and integration count not specified"
            sys.exit(1)
        tlen = int(tlen)
        icount = int(icount)
        tunes = 2
        products = 4
        sample_rate = drx_filters[filterCode]
        
        # Calculate the DR spectrometer frame size
        headerSize = 76
        dataSize = tlen*tunes*products*4
        dataRate = (headerSize+dataSize)/(1.0*tlen*icount/sample_rate)
    else:
        print "Unsupported mode: %s" % mode
        sys.exit(1)
        
    # Display the final answer
    print "%s: filter code %i (%i samples/s)" % (mode, filterCode, sample_rate)
    if mode[0:3] == 'SPC':
        print "  Channel Count: %i" % tlen
        print "  Resolution Bandwidth: %.3f Hz" % (1.0*sample_rate/tlen,)
        print "  Integration Count: %i" % icount
        print "  Integration Time: %.3f s" % (1.0*tlen*icount/sample_rate,)
        print "  Polarization Products: %i" % products
    print "  Data rate: %.2f MB/s" % (dataRate/1024**2,)
    print "  Data volume for %02i:%02i:%06.3f is %.2f GB" % (hour, minute, second, dataRate*duration/1024**3)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description='estimate the data volume for a TBN or DRX observation', 
        epilog="NOTE:  For spectrometer data, the mode is given by SPC,<tlen>,<icount> where 'tlen' is the transform length (channel count) and 'icount' is the number of transforms per integration.", 
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
        )
    parser.add_argument('mode', type=str, 
                        help='observing mode')
    parser.add_argument('filter_code', type=aph.positive_int, 
                        help='observing filter code')
    parser.add_argument('duration', type=str, 
                        help='observing duration; HH:MM:SS.SS format')
    args = parser.parse_args()
    main(args)
    
