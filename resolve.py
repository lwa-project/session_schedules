#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Apache mod_python module for resolving a catalog name to a RA/dec. pair using
the name resolver service at:
  http://www1.cadc-ccda.hia-iha.nrc-cnrc.gc.ca/NameResolver/find
  
Returns an XML file with the coordinates of the target or an error.
"""

import os
import urllib
from jinja2 import Environment, FileSystemLoader


__version__ = "0.1"
__revision__ = "$Rev$"
__author__ = "Jayce Dowell"


def index(req):
	try:
		result = urllib.urlopen('http://www1.cadc-ccda.hia-iha.nrc-cnrc.gc.ca/NameResolver/find?target=%s' % urllib.quote_plus(req.form.getfirst('name', None)))
		
		line = result.readlines()
		target = (line[0].replace('\n', '').split('='))[1]
		service = (line[1].replace('\n', '').split('='))[1]
		coordsys = (line[2].replace('\n', '').split('='))[1]
		ra = (line[3].replace('\n', '').split('='))[1]
		dec = (line[4].replace('\n', '').split('='))[1]

	except IOError:
		target = self.request.get('name')
		service = 'Download Error'
		coordsys = 'NA'
		ra = '-9999.0'
		dec = '-9999.0'
		
	path = os.path.join(os.path.dirname(__file__), 'templates')
	env = Environment(loader=FileSystemLoader(path))
	
	req.headers_out["Content-type"] = 'text/xml'
	template = env.get_template('resolve.xml')
	return template.render(target=target, ra=ra, dec=dec, coordsys=coordsys, service=service, raUnits="degrees", decUnits="degrees")
	