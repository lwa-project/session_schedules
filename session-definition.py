#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import math
from jinja2 import Environment, FileSystemLoader

from mod_python import Session as PySession
from sdf import *


def index(req):
	sess = PySession.Session(req)
	pageMode = req.form.getfirst('mode', None)

	path = os.path.join(os.path.dirname(__file__), 'templates')
	env = Environment(loader=FileSystemLoader(path))

	if pageMode == 'observations' and not sess.is_new():
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
		return template.render(projectInfo=projectInfo, sessionInfo=sessionInfo, observations=observations)
	
	elif pageMode == 'definitions' and not sess.is_new():
		# Stage 3:  Session Definition File Creation
		projectInfo = sess['projectInfo']
		sessionInfo = sess['sessionInfo']
		
		observer = Observer(projectInfo['lastName']+', '+projectInfo['firstName'], projectInfo['observerID'])
		project = Project(observer, projectInfo['projectName'], projectInfo['projectID'], comments=projectInfo['projectComments'])
		session = Session(sessionInfo['sessionName'], sessionInfo['sessionID'], dataReturnMethod=sessionInfo['dataReturnMethod'], comments=sessionInfo['sessionComments'])
		
		numObs = 1
		observations = []
		observationsSimple = []
		while req.form.getfirst('obsName%i' % numObs, None) is not None:
			obsName = req.form.getfirst('obsName%i' % numObs, None)
			obsTarget = req.form.getfirst('obsTarget%i' % numObs, None)
			obsComments = req.form.getfirst('obsComments%i' % numObs, None)
			obsStart = req.form.getfirst('obsStart%i' % numObs, None)
			if projectInfo['sessionMode'] == 'TBW':
				obsBits = int(req.form.getfirst('bits', 12))
				obsSamples = int(req.form.getfirst('samples', 12000000))
				observations.append( TBW(obsName, obsTarget, obsStart, obsSamples, bits=obsBits, comments=obsComments) )
				observationsSimple.append( {'id': numObs, 'name': obsName, 'target': obsTarget, 'start': obsStart, 'comments': obsComments} )
				
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
					
				if obsMode == 'TRK_SOL':
					observations.append( Solar(obsName, obsTarget, obsStart, obsDur, obsFreq1, obsFreq2, obsFilter, MaxSNR=MaxSNR, comments=obsComments) )
					
				if obsMode == 'TRK_JOV':
					observations.append( Jovian(obsName, obsTarget, obsStart, obsDur, obsFreq1, obsFreq2, obsFilter, MaxSNR=MaxSNR, comments=obsComments) )
					
				if obsMode == 'STEPPED':
					pass
					
			numObs = numObs + 1
					
		session.observations = observations
		project.sessions = [session,]
		
		sess['observations'] = observationsSimple
		sess.save()
		
		# Set the output content type and create file
		req.headers_out["Content-type"] = 'text/plain'
		return project.render()
		
	else:
		# Stage 1:  Observer and Proposal Information; DP Output Mode
		sess.set_timeout(300)
		template = env.get_template('session.html')
		
		try:
			projectInfo = sess['projectInfo']
		except:
			projectInfo = None
		return template.render(projectInfo=projectInfo)
		