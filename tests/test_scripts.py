# -*- coding: utf-8 -*-

"""
Unit tests for the various session_schedules scripts.
"""

# Python3 compatibility
from __future__ import print_function, division, absolute_import
import sys
if sys.version_info > (3,):
    xrange = range
    
import unittest
import glob
import sys
import imp
import re
import os

currentDir = os.path.abspath(os.getcwd())
if os.path.exists(os.path.join(currentDir, 'test_scripts.py')):
    MODULE_BUILD = currentDir
else:
    MODULE_BUILD = None
    
run_scripts_tests = False
try:
    from pylint import epylint as lint
    if MODULE_BUILD is not None:
        run_scripts_tests = True
except ImportError:
    pass


__version__  = "0.1"
__author__   = "Jayce Dowell"


_LINT_RE = re.compile('(?P<module>.*?)\:(?P<line>\d+)\: (error )?[\[\(](?P<type>.*?)[\]\)] (?P<info>.*)')


@unittest.skipUnless(run_scripts_tests, "requires the 'pylint' module")
class scripts_tests(unittest.TestCase):
    """A unittest.TestCase collection of unit tests for the session_schedules scripts."""
    
    pass     


def _test_generator(script):
    """
    Function to build a test method for each script that is provided.  
    Returns a function that is suitable as a method inside a unittest.TestCase
    class
    """
    
    def test(self):
        out, err = lint.py_run("%s -E" % script, return_std=True)
        out_lines = out.read().split('\n')
        err_lines = err.read().split('\n')
        out.close()
        err.close()
        
        for line in out_lines:
            if line.find("Module 'numpy") != -1:
                continue
            if line.find("Module 'ephem") != -1:
                continue
                
            mtch = _LINT_RE.match(line)
            if mtch is not None:
                line_no, type, info = mtch.group('line'), mtch.group('type'), mtch.group('info')
                self.assertEqual(type, None, "%s:%s - %s" % (os.path.basename(script), line_no, info))
    return test


if run_scripts_tests:
    _SCRIPTS = glob.glob(os.path.join(MODULE_BUILD, '..', '*.py'))
    for depth in range(1, 3):
        path = [MODULE_BUILD, '..']
        path.extend(['*',]*depth)
        path.append('*.py')
        _SCRIPTS.extend(glob.glob(os.path.join(*path)))
    _SCRIPTS = list(filter(lambda x: x.find('test_scripts.py') == -1, _SCRIPTS))
    _SCRIPTS.sort()
    for script in _SCRIPTS:
        test = _test_generator(script)
        name = 'test_%s' % os.path.splitext(os.path.basename(script))[0]
        doc = """Static analysis of the '%s' script.""" % os.path.basename(script)
        setattr(test, '__doc__', doc)
        setattr(scripts_tests, name, test)


class scripts_test_suite(unittest.TestSuite):
    """A unittest.TestSuite class which contains all of the session_schedules script
    tests."""
    
    def __init__(self):
        unittest.TestSuite.__init__(self)
        
        loader = unittest.TestLoader()
        self.addTests(loader.loadTestsFromTestCase(scripts_tests))


if __name__ == '__main__':
    unittest.main()
    
