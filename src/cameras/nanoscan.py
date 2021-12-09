#!/usr/bin/env python3

# Made 2021, Sun Yudong
# yudong.sun [at] mpq.mpg.de / yudong [at] outlook.de

import os,sys
from typing import Tuple, List

base_dir = os.path.dirname(os.path.realpath(__file__))
root_dir = os.path.abspath(os.path.join(base_dir, ".."))
sys.path.insert(0, root_dir)

import cameras.camera as cam

import logging
import time

import numpy as np

from msl.loadlib import Client64
from cameras.nanoscan_constants import SelectParameters as NsSP
from cameras.nanoscan_constants import NsAxes

class NanoScan(cam.Camera):
	"""Provides interface to the NanoScan 2s Pyro/9/5. Naive implementation
	   following the example codes. 
	"""
	
	AXES = NsAxes

	def __init__(self, devMode: bool = False, *args, **kwargs):
		cam.Camera.__init__(self, *args, **kwargs)

		self.devMode = devMode
		self.NS = NanoScanDLL() # Init and Shutdown is done by the 32-bit server

		assert self.NS.GetNumDevices() > 0, "No devices connected"

		self.daqState = False
		self.roiIndex = 0

	def getAxis_avg_D4Sigma(self, axis: NsAxes, numsamples: int = 20) -> Tuple[float, float]:
		"""Get the d4sigma in one `axis` and averages it over `numsamples` using the Sync1Rev implementation.

		Using NsAxes somewhat changes the signature of this function in a strict sense, but at this point I think would make easier for me to check.

		Parameters
		----------
		axis : NsAxes
			Either `NsAxes.X` or `NsAxes.Y`, or `NsAxes.BOTH`.
			
			Arguably using `NsAxes.BOTH` is more efficient but leads to 
			spaghetti code in that the return type is no longer consistent.

			This is a compromise I am willing to take. 
		numsamples : int, optional
			Number of samples to average over, by default 20

		Returns
		-------
		ret : (float, float) or array_like of form [[float, float], [float, float]]
			Returns the d4sigma of the given axis in micrometer in the form of (average, stddev) or (x, y) where each axis is given in the form of (average, stddev)
			If the given `axis` is not `NsAxes.X` or `NsAxes.Y` or `NsAxes.XY`, then (`None`, `None`)
		"""
		ret = (None, None)

		if not isinstance(axis, NsAxes):
			self.log(f"Invalid axis {axis} selected, expected axis of type {NsAxes}.")
			return ret

		self.waitStable()

		self.NS.AutoFind()

		originalParams = self.NS.GetSelectedParameters()
		self.NS.SelectParameters(originalParams | NsSP.BEAM_WIDTH_D4SIGMA)

		# A stack of x, y values
		out = np.array([[self.oneRev()] for _ in range(numsamples)])

		average = np.average(out, axis = 0)
		stddev  = np.std(out, axis = 0)

		self.log(f"average = {average}, stddev = {stddev}", loglevel = logging.DEBUG)

		if axis == NsAxes.BOTH:
			ret = np.vstack((average, stddev)).T
		else:
			ret = (average.flatten()[axis], stddev.flatten()[axis])

		self.NS.SelectParameters(originalParams)

		return ret
		
	def oneRev(self) -> Tuple[float, float]:
		self.NS.AcquireSync1Rev()
		self.NS.RunComputation()
		x = self.NS.GetBeamWidth4Sigma(NsAxes.X, self.roiIndex)
		y = self.NS.GetBeamWidth4Sigma(NsAxes.Y, self.roiIndex)

		self.log(f"Got 1 Reading of (x, y): {(x, y)}", logging.DEBUG)

		return (x, y)

	def waitStable(self):
		self.SetDAQ(True)
		self.waitForData()
		self.SetDAQ(False)
	
	def SetDAQ(self, state: bool) -> None:
		"""Sets the DAQ state. Use this instead of directly using `self.NS.SetDataAcquisition`. This helps to keep track of the DAQ State.

		Do not use in conjunction with Sync1Rev, it will be useless.

		Parameters
		----------
		state : bool
			Sets the Data Acquisition to `state`

		"""

		self.NS.SetDataAcquisition(state)
		self.daqState = state
	
	def waitForData(self) -> bool:
		"""A valid method of determining whether data has been processed yet is
		to evaluate whether any Results (Parameters per NS1) have yet been computed.
		In this example the Centroid position result is used due to its benign
		nature, i.e. usually enabled and not affected by other settings or results.

		Reference: Program.cs from Automation examples folder from NanoScan

		Returns
		-------
		success : bool
			Returns true when data is available

		"""

		if not self.daqState:
			self.log("Start DAQ before waiting for data. Ignoring function call", logging.WARN)
			return False

		originalParams = self.NS.GetSelectedParameters()

		self.NS.SelectParameters(
			originalParams | NsSP.BEAM_CENTROID_POS
		)

		daqState = False
		centroidValue_X = 0
		centroidValue_Y = 0
		cnt = 0

		while not daqState:
			time.sleep(50e-3)
			centroidValue_X = self.NS.GetCentroidPosition(NsAxes.X, self.roiIndex)
			centroidValue_Y = self.NS.GetCentroidPosition(NsAxes.Y, self.roiIndex)

			self.log(f"{cnt}: waitStable: ({centroidValue_X}, {centroidValue_Y})", logging.DEBUG, end = "\r")

			cnt += 1

			if (centroidValue_X > 0) and (centroidValue_Y > 0):
				daqState = True

		self.NS.SelectParameters(originalParams)
		
		return True

		# def freeRunning(self, axis: NsAxes = NsAxes.X) -> float:
		# 	# self.NS.SetShowWindow(True)
		# 	self.SetDAQ(True)
		# 	self.waitForData()
		# 	self.NS.SelectParameters(NsSP.BEAM_WIDTH_D4SIGMA)
		# 	x = self.NS.GetBeamWidth4Sigma(axis, self.roiIndex)
		# 	self.SetDAQ(False)
		# 	return x

	def __exit__(self, e_type, e_val, traceback):
		self.NS.__exit__(e_type, e_val, traceback)
		return super(NanoScan, self).__exit__(e_type, e_val, traceback)

class NanoScanDLL(Client64):
	"""Provides interface to the 32-bit NanoScan C# DLL using msl-loadlib."""

	def __init__(self, *args, **kwargs):
		Client64.__init__(self, module32='nanoscan_server.py')

	def __getattr__(self, name):
		def send(*args, **kwargs):
			return self.request32(name, *args, **kwargs)
		return send

	def __enter__(self):
		return self

	def __exit__(self, e_type, e_val, traceback):
		return self.ShutdownNS()
		# return super().__exit__(e_type, e_val, traceback)

if __name__ == '__main__':
    with NanoScan() as n:
        print("with Nanoscan as n")
        import code; code.interact(local=locals())