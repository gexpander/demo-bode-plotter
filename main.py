#!/bin/env python3
import math

import numpy as np
from matplotlib import pyplot as plt

import gex
import time

# Two presets defined for demoing the plotter with a high-pass and low-pass RC filter
# made of 1 kOhm and 100 nF

#demo = 'HP'
demo = 'LP'
#demo = 'LP2'
#demo = 'LP3' # LT1112 sallenkey at 1 kHz

class ADG:
    def __init__(self, client:gex.Client):
        self.client = client
        self.spi = gex.SPI(client, 'spi')
        self.pb = gex.PayloadBuilder(endian='big')

    def write_word(self, u16):
        self.pb.reset()
        self.pb.u16(u16)
        self.spi.write(0, self.pb.close())

    def initialize(self):
        # enable 28-bit writes, reset registers, configured for SINE output
        self.write_word(0x2100)
        self.write_word(0xC000) # phase word
        self.set_frequency(0) # freq 0
        self.write_word(0x2000)

    def set_frequency(self, hz):
        word = round(hz * 10.73741824)
        self.write_word(0x4000 | (word & 0x0003FFF))
        self.write_word(0x4000 | (word & 0xFFFC000)>>14)

    def wfm_dc(self):
        self.write_word(0x2100)
        self.write_word(0x2000)
        self.set_frequency(0)

    def wfm_sine(self, freq=None):
        self.write_word(0x2000)
        if freq is not None:
            self.set_frequency(freq)

#with gex.Client(gex.TrxRawUSB()) as client:
with gex.Client(gex.TrxSerialThread('/dev/ttyACM0')) as client:
    # ===============================================
      
    # Delay between adjusting input and starting the measurement. 
    # Should be several multiples of the time constant
    settling_time_s = (4700*100e-9)*10    
    
    # max change in DB between samples to detect faulty measurements that need to be repeated
    max_allowed_shift_db = 5
    # db shift compensation (spread through the frequency sweep to adjust for different slopes)
    allowed_shift_compensation = -4.5
    
    # Frequency sweep parameters
    f_0 = 5
    f_1 = 6000
    
    f_step = 5
    f_step_begin = 5
    f_step_end = 200
    
    if demo == 'HP':
      # highpass filter example (corner 340 Hz)
      settling_time_s = (4700*100e-9)*10
      max_allowed_shift_db = 5.6
      allowed_shift_compensation = -5

    if demo == 'LP':
      # lowpass filter example (corner 340 Hz)
      settling_time_s = (4700*100e-9)*10
      max_allowed_shift_db = .5
      allowed_shift_compensation = 2
         
    if demo == 'LP2':
      # lowpass filter example (corner 340 Hz)
      settling_time_s = (4700*100e-9)*10
      max_allowed_shift_db = .5
      allowed_shift_compensation = 5
      f_1 = 4500
      f_step_end = 100
         
    if demo == 'LP3':
      # lowpass filter example (corner 340 Hz)
      settling_time_s = 0.05
      max_allowed_shift_db = .5
      allowed_shift_compensation = 5
      f_1 = 10000
      f_step_end = 250
    
    
    # Retry on failure
    retry_count = 5
    retry_delay_s = settling_time_s*2
    
    # Initial sample granularity
    samples_per_period = 60
    capture_periods = 10
    
    # Parameters for automatic params adjustment
    max_allowed_sample_rate = 65000
    max_allowed_nr_periods = 100
    min_samples_per_period = 16
    
    # ===============================================
    
    #allowed_shift_compensation /= (f_1 - f_0) / f_step
    
    adc = gex.ADC(client, 'adc')
    
    gen = ADG(client)
    gen.initialize()


    table = []
    
    last_db = None
    f = f_0
    first = True
    begin_allowedshift = max_allowed_shift_db
    while f <= f_1:
        if not first:
          f_step = round(f_step_begin + ((f - f_0) / (f_1 - f_0)) * (f_step_end - f_step_begin))
          f += f_step          
        first = False      
      
        #dac.set_frequency(1, f)
        gen.set_frequency(f)        
        
        max_allowed_shift_db = begin_allowedshift + allowed_shift_compensation * ((f - f_0) / (f_1 - f_0))

        # Adjust measurement parameters
        while True:
            desiredf = f*samples_per_period
            if desiredf > max_allowed_sample_rate:
                desiredf = max_allowed_sample_rate
                oldspp = samples_per_period
                samples_per_period = math.ceil(samples_per_period * 0.9)
                
                if samples_per_period == oldspp:
                  samples_per_period -= 1
                
                if samples_per_period <= min_samples_per_period:
                  samples_per_period = min_samples_per_period
                  break
                
                if capture_periods < max_allowed_nr_periods:
                  capture_periods = math.ceil(capture_periods * 1.1)
                
                continue
            break
        
        num_samples = samples_per_period * capture_periods
        print("\x1b[90mCap %d samples at %d Hz (samples per period %d, periods: %d, max db shift %f)\x1b[0m" % (
          num_samples, desiredf, samples_per_period, capture_periods, max_allowed_shift_db))

        adc.set_sample_rate(desiredf)
        
        last_db_in_fail = None
        suc = False
        for i in range(retry_count):
          time.sleep(settling_time_s if i == 0 else retry_delay_s)
          
          t = None
          
          samples = adc.capture(num_samples)
          ar = np.array(samples, dtype=float)
          
          try:
            t = np.reshape(ar, [num_samples, 2])
          except ValueError:
            print("\x1b[31mCorrupt capture, repeating - try %d\x1b[0m" % (i+1))
            continue

          y1 = np.max(t[:,0]) - np.min(t[:,0])
          y2 = np.max(t[:,1]) - np.min(t[:,1])
          gain_raw = y2/y1
          gain_db = 20*math.log10(gain_raw)
          
          # check feasibility
          if last_db is not None:            
            dbdelta = abs(last_db - gain_db)
            if dbdelta > max_allowed_shift_db:
              print("\x1b[31mGlitch detected (dB delta %f), repeating - try %d\x1b[0m" % (dbdelta, i+1))
              last_db_in_fail = gain_db
              continue
          
          last_db = gain_db

          avr1 = np.average(t[:,0])
          avr2 = np.average(t[:,0])

          aa = np.subtract(t[:,0], avr1)
          bb = np.subtract(t[:, 1], avr2)

          phaseoffset = (math.acos((np.dot(aa,bb))/(np.linalg.norm(aa) * np.linalg.norm(bb))) / math.pi) * -180

          table.append(f)
          table.append(gain_db)
          table.append(phaseoffset)
          print("f %f Hz ... Gain %f dB ... Phase %f °" % (f, gain_db, phaseoffset))
          
          suc = True
          break
        if not suc:
          last_db = last_db_in_fail

    gen.wfm_dc()
    
    t = np.reshape(np.array(table), [int(len(table) / 3), 3])

    freqs = t[:, 0]
    gains = t[:, 1]
    phases = t[:, 2]

    fig = plt.figure()
    ax1 = fig.add_subplot(211)
    ax1.set_ylabel('Gain (dB)')
    ax1.semilogx(freqs, gains)  # Bode magnitude plot
    ax1.grid()

    ax2 = fig.add_subplot(212)
    ax2.set_ylabel('Phase (deg)')
    ax2.set_xlabel('Frequency (Hz)')
    ax2.semilogx(freqs, phases)  # Bode phase plot
    ax2.grid()
    
    plt.show()


