#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Wrapper around the validator.py module to make it useable from the
command line.

$Rev$
$LastChangedBy$
$LastChangedDate$
""" 

import sys
import validator

validator.main(sys.argv[1:])

