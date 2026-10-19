"""Deterministic checks with known ground truth, not a real-audio quality claim."""
import unittest
import numpy as np
from scipy import signal
from rescue import align, reference_on_timeline, separate, build_parser

class RecoveryTests(unittest.TestCase):
    def test_successful_preset_is_default(self):
        args = build_parser().parse_args(["video.mp4", "song.mp3"])
        self.assertEqual(args.mode, "cancel")
        self.assertEqual(args.strength, 1.15)
        self.assertEqual(args.window, .15)

    def setUp(self):
        self.sr=48000
        rng=np.random.default_rng(42)
        # Nonrepeating wideband reference and independent, sparse effect bursts.
        n=self.sr*24
        a=signal.lfilter([1], [1,-.7],rng.normal(size=(n,2)),axis=0)*.06
        self.r=a.astype(np.float32)
        self.fx=np.zeros((n+24000,2),np.float32)
        for t in [3,7,12,18,22]:
            k=int(t*self.sr);m=4800
            self.fx[k:k+m]=rng.normal(size=(m,2))*.04*np.exp(-np.arange(m)[:,None]/1200)

    def test_alignment_and_drift(self):
        true_offset=.23743;rate=1.00018
        ref=reference_on_timeline(self.r,len(self.fx),self.sr,true_offset,rate)
        gain=(.25+.15*np.sin(np.arange(len(ref))/self.sr*2))[:,None]
        y=ref*gain+self.fx
        off,estimated,_=align(y,self.r,self.sr)
        self.assertLess(abs(off-true_offset),.001)
        self.assertLess(abs(estimated-rate),30e-6)

    def test_recovery_and_passthrough(self):
        ref=reference_on_timeline(self.r,len(self.fx),self.sr,.2,1)
        y=.4*ref+self.fx
        fx=separate(y,ref,self.sr,mode='cancel')
        before=np.mean((y-self.fx)**2);after=np.mean((fx-self.fx)**2)
        improvement=10*np.log10(before/after)
        print('Known-mixture cancellation error improvement:',improvement,'dB')
        self.assertGreater(improvement,10)
        passthrough=separate(y,ref,self.sr,strength=0)
        self.assertLess(np.max(abs(passthrough-y)),1e-6)
        silent=separate(y,np.zeros_like(ref),self.sr)
        self.assertLess(np.max(abs(silent-y)),1e-6)
        self.assertTrue(np.isfinite(fx).all())

if __name__=='__main__':unittest.main()
