#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import math
import time
from jinja2 import Environment, FileSystemLoader

from mod_python import Session as PySession
from sdf import *


def index(req):
	sess = PySession.Session(req)
	pageMode = req.form.getfirst('mode', None)
	#reset = req.form.getfirst('Reset', None)
	#if reset is not None:
		#pass
		#pageMode = None
		#sess.invalidate()
		#sess = PySession.Session(req)

	path = os.path.join(os.path.dirname(__file__), 'templates')
	env = Environment(loader=FileSystemLoader(path))

	if pageMode == 'observations':
		# Stage 2:  Observation Definitions
		sessionMode = req.form.getfirst('sessionMode', 'DRX')
		projectInfo = {}
		for keyword in ['firstName', 'lastName', 'observerID', 'projectName', 'projectID', 'projectComments']:
			projectInfo[keyword] = req.form.getfirst(keyword, None)
		projectInfo['sessionMode'] = sessionMode
		
		sess['projectInfo'] = projectInfo
		sess.save()
		
		if sessionMode == 'TBW':
			template = env.get_template('tbw.html')
		elif sessionMode == 'TBN':
			template = env.get_template('tbn.html')
		else:
			template = env.get_template('drx.html')
			
		try:
			sessionInfo = sess['sessionInfo']
		except:
			sessionInfo = None
		try:
			observations = sess['observations']
		except:
			observations = []
		return template.render(projectInfo=projectInfo, sessionInfo=sessionInfo, observations=observations, to=(time.time() - sess.last_accessed()))
	
	elif pageMode == 'definitions':
		# Stage 3:  Session Definition File Creation
		projectInfo = sess['projectInfo']
		sessionInfo = {}
		for keyword in ['sessionName', 'sessionID', 'sessionComments', 'dataReturnMethod']:
			sessionInfo[keyword] = req.form.getfirst(keyword, None)
		
		po = ProjectOffice()
		observer = Observer(projectInfo['lastName']+', '+projectInfo['firstName'], projectInfo['observerID'])
		project = Project(observer, projectInfo['projectName'], projectInfo['projectID'], comments=projectInfo['projectComments'], projectOffice=po)
		session = Session(sessionInfo['sessionName'], sessionInfo['sessionID'], dataReturnMethod=sessionInfo['dataReturnMethod'], comments=sessionInfo['sessionComments'])
		
		numObs = 1
		observations = []
		observationsSimple = []
		project.projectOffice.observations.append( [] )
		while req.form.getfirst('obsName%i' % numObs, None) is not None:
			obsName = req.form.getfirst('obsName%i' % numObs, None)
			obsTarget = req.form.getfirst('obsTarget%i' % numObs, None)
			obsComments = req.form.getfirst('obsComments%i' % numObs, None)
			obsStart = req.form.getfirst('obsStart%i' % numObs, None)
			if projectInfo['sessionMode'] == 'TBW':
				obsBits = int(req.form.getfirst('bits', 12))
				obsSamples = int(req.form.getfirst('samples', 12000000))
				observations.append( TBW(obsName, obsTarget, obsStart, obsSamples, bits=obsBits, comments=obsComments) )
				observationsSimple.append( {'id': numObs, 'name': obsName, 'target': obsTarget, 'start': obsStart, 'comments': obsComments, 
										'bits': obsBits, 'samples': obsSamples} )
				
			if projectInfo['sessionMode'] == 'TBN':
				obsDur = req.form.getfirst('obsDuration%i' % numObs, '00:00:00.000')
				obsFreq = float(req.form.getfirst('obsFrequency%i' % numObs, 38.0))*1e6
				obsFilter = int(req.form.getfirst('obsFilter%i' % numObs, 7))
				observations.append( TBN(obsName, obsTarget, obsStart, obsDur, obsFreq, obsFilter, comments=obsComments) )
				observationsSimple.append( {'id': numObs, 'name': obsName, 'target': obsTarget, 'start': obsStart, 
										'duration': obsDur, 'frequency': obsFreq, 'filter': obsFilter, 'comments': obsComments} )
				
			if projectInfo['sessionMode'] == 'DRX':
				obsMode = req.form.getfirst('obsMode%i' % numObs, 'TRK_RADEC')
				obsDur = req.form.getfirst('obsDuration%i' % numObs, '00:00:00.000')
				obsFreq1 = float(req.form.getfirst('obsFrequency%i-1' % numObs, 38.0))*1e6
				obsFreq2 = float(req.form.getfirst('obsFrequency%i-2' % numObs, 38.0))*1e6
				obsFilter = int(req.form.getfirst('obsFilter%i' % numObs, 7))
				obsBeam = req.form.getfirst('obsBeam%i' % numObs, 'SIMPLE')
				if obsBeam == 'SIMPLE':
					MaxSNR = False
				else:
					MaxSNR = True
					
				if obsMode == 'TRK_RADEC':
					obsRA = float(req.form.getfirst('obsRA%i' % numObs, 0.000000))
					obsDec = float(req.form.getfirst('obsDec%i' % numObs, 0.000000))
					observations.append( DRX(obsName, obsTarget, obsStart, obsDur, obsRA, obsDec, obsFreq1, obsFreq2, obsFilter, MaxSNR=MaxSNR, comments=obsComments) )
					observationsSimple.append( {'id': numObs, 'name': obsName, 'target': obsTarget, 'start': obsStart, 
										'duration': obsDur, 'frequency1': obsFreq1, 'frequency2': obsFreq2, 'filter': obsFilter, 
										'ra': obsRA, 'dec': obsDec, 'MaxSNR': MaxSNR, 'comments': obsComments, 'mode': obsMode} )
					
				if obsMode == 'TRK_SOL':
					observations.append( Solar(obsName, obsTarget, obsStart, obsDur, obsFreq1, obsFreq2, obsFilter, MaxSNR=MaxSNR, comments=obsComments) )
					observationsSimple.append( {'id': numObs, 'name': obsName, 'target': obsTarget, 'start': obsStart, 
										'duration': obsDur, 'frequency1': obsFreq1, 'frequency2': obsFreq2, 'filter': obsFilter, 
										'MaxSNR': MaxSNR, 'comments': obsComments, 'mode': obsMode} )
					
				if obsMode == 'TRK_JOV':
					observations.append( Jovian(obsName, obsTarget, obsStart, obsDur, obsFreq1, obsFreq2, obsFilter, MaxSNR=MaxSNR, comments=obsComments) )
					observationsSimple.append( {'id': numObs, 'name': obsName, 'target': obsTarget, 'start': obsStart, 
										'duration': obsDur, 'frequency1': obsFreq1, 'frequency2': obsFreq2, 'filter': obsFilter, 
										'MaxSNR': MaxSNR, 'comments': obsComments, 'mode': obsMode} )
					
				if obsMode == 'STEPPED':
					obsRADec = req.form.getfirst('obsCoords%i' % numObs, 'RADec')
					if obsRADec == 'RADec':
						obsRADec = True
					else:
						obsRADec = False
					
					steps = []
					numStp = 1
					stepsSimple = []
					while req.form.getfirst('obs%istpDuration%i' % (numObs, numStp), None) is not None:
						stpDur = req.form.getfirst('obs%istpDuration%i' % (numObs, numStp), None)
						stpFreq1 = float(req.form.getfirst('obs%istpFrequency%i-1' % (numObs, numStp), 38.0))*1e6
						stpFreq2 = float(req.form.getfirst('obs%istpFrequency%i-2' % (numObs, numStp), 38.0))*1e6
						stpC1 = float(req.form.getfirst('obs%istpC%i-1' % (numObs, numStp), 0.0))
						stpC2 = float(req.form.getfirst('obs%istpC%i-2' % (numObs, numStp), 0.0))
						stpBeam = req.form.getfirst('obs%istpBeam%i' % (numObs, numStp), 'SIMPLE')
						if stpBeam == 'SIMPLE':
							stpMaxSNR = False
						else:
							stpMaxSNR = True
							
						fields = stpDur.split(':')
						if len(fields) == 3:
							out = int(fields[0])*3600.0
							out += int(fields[1])*60.0
							out += float(fields[2])
						elif len(fields) == 2:
							out = int(fields[0])*60.0
							out += float(fields[1])
						else:
							out = float(fields[0])
						stpDurn = int(round(out*1000.0))
							
						steps.append( BeamStep(stpC1, stpC2, stpDurn, stpFreq1, stpFreq2, RADec=obsRADec, MaxSNR=stpMaxSNR) )
						stepsSimple.append( {'id': numStp, 'c1': stpC1, 'c2': stpC2, 'duration': stpDur, 
											'frequency1': stpFreq1, 'frequency2': stpFreq2, 'RADec': obsRADec, 'MaxSNR': stpMaxSNR} )
						
						numStp = numStp + 1
						
					observations.append( Stepped(obsName, obsTarget, obsStart, obsFilter, steps=steps, comments=obsComments) )
					observationsSimple.append( {'id': numObs, 'name': obsName, 'target': obsTarget, 'start': obsStart, 
										'filter': obsFilter, 'steps': stepsSimple, 'comments': obsComments, 'mode': obsMode} )
										
			numObs = numObs + 1
			project.projectOffice.observations[0].append( 'None' )
					
		session.observations = observations
		project.sessions = [session,]
		project.projectOffice.sessions = []
		project.projectOffice.observations.append([])
		
		sess['sessionInfo'] = sessionInfo
		sess['observations'] = observationsSimple
		sess.save()
		
		# Set the output content type and create file
		req.headers_out["Content-type"] = 'text/plain'
		return project.render()
		
	if pageMode == 'prevfile':
		# Stage 0.5:  Initialize everything from an old SD file that has been uploaded
		import tempfile
		
		# Get the SD file's contents and save it to a temporary file in a temporary 
		# directory.  This is all done in binary mode so the file will have to be
		# re-opened later to work on it.
		sdfData = req.form['file']
		tmpDir = tempfile.mkdtemp(prefix='session-definition-')
		tmpFile = os.path.join(tmpDir, 'uploaded-sdf.txt')
		fh = open(tmpFile, 'wb')
		fh.write(sdfData.file.read())
		fh.close()
		
		# Run the file through the parser
		project = parseSDF(tmpFile)
		
		# Cleanup the temporary file and directory
		os.unlink(tmpFile)
		os.rmdir(tmpDir)
		
		projectInfo = {}
		projectInfo['firstName'] = project.observer.name.split(',')[1]
		projectInfo['lastName'] = project.observer.name.split(',')[0]
		projectInfo['observerID'] = project.observer.id
		projectInfo['projectID'] = project.id
		projectInfo['projectName'] = project.name
		projectInfo['projectComments'] = project.comments
		if project.sessions[0].observations[0].mode in ['TBW', 'TBN']:
			projectInfo['sessionMode'] = project.sessions[0].observations[0].mode
		else:
			projectInfo['sessionMode'] = 'DRX'
		
		sess['projectInfo'] = projectInfo
		
		
		sessionInfo = {}
		sessionInfo['sessionName'] = project.sessions[0].name
		sessionInfo['sessionID'] = project.sessions[0].id
		sessionInfo['sessionComments'] = project.sessions[0].comments
		sess['sessionInfo'] = sessionInfo
		
		numObs = 1
		observationsSimple = []
		for obs in project.sessions[0].observations:
			if obs.mode == 'TBW':
				observationsSimple.append( {'id': numObs, 'name': obs.name, 'target': obs.target, 'start': obs.start, 'comments': obs.comments, 
										'bits': obs.bits, 'samples': obs.samples} )
			elif obs.mode == 'TBN':
				observationsSimple.append( {'id': numObs, 'name': obs.name, 'target': obs.target, 'start': obs.start, 
										'duration': obs.duration, 'frequency': obs.frequency1, 'filter': obs.filter, 'comments': obs.comments} )	
			elif obs.mode == 'TRK_RADEC':
				observationsSimple.append( {'id': numObs, 'name': obs.name, 'target': obs.target, 'start': obs.start, 
									'duration': obs.duration, 'frequency1': obs.frequency1, 'frequency2': obs.frequency2, 'filter': obs.filter, 
									'ra': obs.ra, 'dec': obs.dec, 'MaxSNR': obs.MaxSNR, 'comments': obs.comments, 'mode': obs.mode} )	
			elif obs.mode == 'TRK_SOL':
				observationsSimple.append( {'id': numObs, 'name': obs.name, 'target': obs.target, 'start': obs.start, 
									'duration': obs.duration, 'frequency1': obs.frequency1, 'frequency2': obs.frequency2, 'filter': obs.filter, 
									'MaxSNR': obs.MaxSNR, 'comments': obs.comments, 'mode': obs.mode} )	
			elif obs.mode == 'TRK_JOV':
				observationsSimple.append( {'id': numObs, 'name': obs.name, 'target': obs.target, 'start': obs.start, 
									'duration': obs.duration, 'frequency1': obs.frequency1, 'frequency2': obs.frequency2, 'filter': obs.filter, 
									'MaxSNR': obs.MaxSNR, 'comments': obs.comments, 'mode': obs.mode} )
			elif obs.mode == 'STEPPED':
				steps = []
				numStp = 1
				stepsSimple = []
				for step in obs.steps:
					stepsSimple.append( {'id': numStp, 'c1': step.c1, 'c2': step.c2, 'duration': step.duration, 
										'frequency1': step.frequency1, 'frequency2': step.frequency2, 'RADec': step.RADec, 'MaxSNR': step.MaxSNR} )
					numStp = numStp + 1
					
				observationsSimple.append( {'id': numObs, 'name': obs.name, 'target': obs.target, 'start': obs.start, 
										'filter': obs.filter, 'steps': stepsSimple, 'comments': obs.comments, 'mode': obs.mode} )
			else:
				raise RuntimeError("Unknown observation mode '%s' for observation %i" % (obs.mode, numObs))
										
			numObs = numObs + 1
		sess['observations'] = observationsSimple
		sess.save()
		
		template = env.get_template('session.html')
		return template.render(projectInfo=projectInfo, uploaded=True, numObs=len(observationsSimple), to=(time.time() - sess.last_accessed()))
		
	else:
		# Stage 1:  Observer and Proposal Information; DP Output Mode
		sess.set_timeout(3600)
		template = env.get_template('session.html')
		
		try:
			projectInfo = sess['projectInfo']
		except:
			projectInfo = None
		return template.render(projectInfo=projectInfo, uploaded=False, numObs=0, to=(time.time() - sess.last_accessed()))
		