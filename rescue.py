#!/usr/bin/env python3
"""Offline reference-assisted recovery of game effects. No cloud/GPU required."""
import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
from scipy.io import wavfile
from scipy import signal, ndimage


def decode(path, sr):
    p = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(path), '-map', '0:a:0',
                        '-vn', '-ac', '2', '-ar', str(sr), '-f', 'f32le', '-'],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if p.returncode:
        raise ValueError(p.stderr.decode(errors='replace'))
    x = np.frombuffer(p.stdout, np.float32).reshape(-1, 2).copy()
    if len(x) < sr / 2:
        raise ValueError(f'Audio too short: {path}')
    if not np.isfinite(x).all():
        raise ValueError('Non-finite input samples')
    return x


def peak_lag(y, r, valid=False):
    """Normalized cross correlation. Positive lag means reference starts later in y."""
    mode = 'valid' if valid else 'full'
    c = signal.correlate(y, r, mode=mode, method='fft')
    ey = signal.correlate(y*y, np.ones(len(r)), mode=mode, method='fft')
    er = (np.sum(r*r) if valid else
          signal.correlate(np.ones(len(y)), r*r, mode='full', method='fft'))
    overlap = (len(r) if valid else signal.correlate(
        np.ones(len(y)), np.ones(len(r)), mode='full', method='fft'))
    score = np.abs(c) / np.sqrt(np.maximum(ey * er, 1e-20))
    if not valid:
        score[overlap < min(len(y), len(r)) * .35] = 0
    k = int(np.argmax(score)); delta = 0.
    if 0 < k < len(score)-1:
        den = score[k-1] - 2*score[k] + score[k+1]
        if abs(den) > 1e-12:
            delta = float(np.clip(.5*(score[k-1]-score[k+1])/den, -.5, .5))
    lag = k + delta if valid else k + delta - len(r) + 1
    return float(lag), float(score[k])


def align(y, r, sr, offset=None, drift_ppm=None):
    # Work at 12 kHz, then 3 kHz for the full-recording search.
    ym = signal.resample_poly(y.mean(axis=1), 12000, sr)
    rm = signal.resample_poly(r.mean(axis=1), 12000, sr)
    if np.max(np.abs(ym)) < 1e-8 or np.max(np.abs(rm)) < 1e-8:
        raise ValueError('Recording or reference is silent')
    if offset is None:
        lag, confidence = peak_lag(signal.resample_poly(ym, 1, 4),
                                   signal.resample_poly(rm, 1, 4))
        offset = lag / 3000
        if confidence < .06:
            raise ValueError('Alignment confidence too low. Supply --offset SECONDS '
                             '(video time corresponding to reference time zero).')
    else:
        confidence = None
    anchors = []
    # Refine several distributed anchors and estimate linear clock drift.
    initial_rate = 1 + (drift_ppm or 0) * 1e-6
    for rt in np.linspace(0, max(0, len(rm)/12000-5), 15):
        start = int(rt*12000); q = rm[start:start+60000]
        predicted = offset + rt / initial_rate
        radius = .25 if drift_ppm is not None else .6
        lo = int((predicted-radius)*12000); hi = int((predicted+5+radius)*12000)
        if lo < 0 or hi > len(ym) or len(q) < 60000:
            continue
        lag, score = peak_lag(ym[lo:hi], q, valid=True)
        vt = (lo+lag)/12000
        if score > .12 and abs(vt-predicted) < radius*.95:
            anchors.append((float(rt), float(vt), score))
    rate = initial_rate
    if anchors:
        aa = np.array(anchors); residuals = aa[:,1] - aa[:,0]/initial_rate
        keep = abs(residuals - np.median(residuals)) < .08
        aa = aa[keep]
        if drift_ppm is None and len(aa) >= 4 and np.ptp(aa[:,0]) > 15:
            slope, intercept = np.polyfit(aa[:,0], aa[:,1], 1, w=aa[:,2]**2)
            if abs(1/slope-1) < .002:
                rate = 1/slope; offset = float(intercept)
            else:
                print('Warning: implausible drift; using constant offset.', file=sys.stderr)
                offset = float(np.median(aa[:,1]-aa[:,0]))
        elif len(aa):
            offset = float(np.median(aa[:,1]-aa[:,0]/rate))
    print(f'Alignment: reference 0 at video {offset:.6f}s; drift {(rate-1)*1e6:.2f} ppm')
    return offset, rate, {'coarse_confidence': confidence, 'anchors': anchors}


def reference_on_timeline(r, n, sr, offset, rate):
    pos = (np.arange(n, dtype=np.float64)/sr-offset)*rate*sr
    out = np.empty((n,2), np.float32)
    for c in range(2):
        out[:,c] = np.interp(pos, np.arange(len(r)), r[:,c], left=0, right=0)
    return out


DEFAULT_STRENGTH = 1.15
DEFAULT_WINDOW = 0.15
DEFAULT_MODE = 'cancel'


def separate(y, r, sr, strength=DEFAULT_STRENGTH, floor_db=-30.,
             window=DEFAULT_WINDOW, mode=DEFAULT_MODE):
    """Estimate a locally coherent reference component; remove conservatively.

    Spectral mode floors residual power, retaining mixture phase. Cancel mode
    subtracts a complex estimate (signed waveform residual after inverse STFT).
    """
    out = np.empty_like(y)
    fft = 2048; hop = 512
    size = (3, max(3, int(window*sr/hop) | 1))
    def smooth(a):
        if np.iscomplexobj(a):
            return ndimage.uniform_filter(a.real, size=size, mode='nearest') + 1j*ndimage.uniform_filter(a.imag, size=size, mode='nearest')
        return ndimage.uniform_filter(a, size=size, mode='nearest')
    for c in range(2):
        print(f'Processing channel {c+1}/2...', flush=True)
        _, _, Y = signal.stft(y[:,c], sr, nperseg=fft, noverlap=fft-hop)
        _, _, R = signal.stft(r[:,c], sr, nperseg=fft, noverlap=fft-hop)
        rr = smooth(abs(R)**2)
        yr = smooth(Y * R.conj())
        eps = max(float(np.max(rr))*1e-7, 1e-15)
        H = yr/(rr+eps)
        predicted = H*R
        if mode == 'cancel':
            Z = Y - strength*predicted
        else:
            # Per-bin nonnegative residual power, no rectification of signed samples.
            fraction = abs(predicted)**2/(abs(Y)**2+1e-16)
            mask = np.sqrt(np.maximum(1-strength*fraction, 10**(floor_db/10)))
            Z = Y*np.minimum(mask, 1.)
        _, z = signal.istft(Z, sr, nperseg=fft, noverlap=fft-hop)
        out[:,c] = z[:len(y)]
    return out


def build_parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('video', help='Video or damaged audio file; first audio stream is used')
    p.add_argument('song', help='Matching reference song (same game edit)')
    p.add_argument('--out', default='rescued_audio', help='New output directory')
    p.add_argument('--offset', type=float, help='Video seconds at reference time zero; may be negative')
    p.add_argument('--drift-ppm', type=float, help='Reference clock rate minus video clock rate, ppm; 0 disables auto drift')
    p.add_argument('--strength', type=float, default=DEFAULT_STRENGTH, help='Subtraction strength (default: 1.15); 0=unchanged')
    p.add_argument('--floor-db', type=float, default=-30., help='Spectral attenuation floor; try -15 to preserve more effects')
    p.add_argument('--window', type=float, default=DEFAULT_WINDOW, help='Local fit duration in seconds (default: 0.15)')
    p.add_argument('--mode', choices=['spectral','cancel'], default=DEFAULT_MODE)
    p.add_argument('--rate', type=int, choices=[44100,48000], default=48000)
    p.add_argument('--fx-db', type=float, default=0., help='Effects gain in optional rebuilt mix')
    p.add_argument('--song-db', type=float, default=-6., help='Song gain in optional rebuilt mix')
    p.add_argument('--rebuild', action='store_true', help='Also export original song plus estimated effects')
    return p


def main():
    p = build_parser()
    args = p.parse_args()
    if not shutil.which('ffmpeg'):
        p.error('FFmpeg must be installed and available on PATH')
    if not (0 <= args.strength <= 3 and .03 <= args.window <= 3 and -100 <= args.floor_db <= 0):
        p.error('Require strength 0..3, window .03..3, floor-db -100..0')
    if not all(np.isfinite(v) for v in [args.strength,args.window,args.floor_db,args.fx_db,args.song_db] +
               [v for v in [args.offset,args.drift_ppm] if v is not None]):
        p.error('Numeric options must be finite')
    dest = Path(args.out)
    if dest.exists():
        p.error('Output directory already exists; choose a new --out to protect previous results')
    print('Decoding recording and reference...', flush=True)
    y=decode(args.video,args.rate); r=decode(args.song,args.rate)
    offset,rate,diagnostics=align(y,r,args.rate,args.offset,args.drift_ppm)
    r=reference_on_timeline(r,len(y),args.rate,offset,rate)
    if np.max(abs(r)) < 1e-8:
        raise ValueError('Reference does not overlap the recording at this offset')
    fx=separate(y,r,args.rate,args.strength,args.floor_db,args.window,args.mode)
    dest.mkdir(parents=True)
    # Float WAV preserves levels/overs without clipping; no hidden normalization.
    wavfile.write(dest/'effects.wav',args.rate,fx.astype(np.float32))
    wavfile.write(dest/'removed.wav',args.rate,(y-fx).astype(np.float32))
    wavfile.write(dest/'aligned_song.wav',args.rate,r.astype(np.float32))
    report={'settings':vars(args),'offset_seconds':offset,'rate':rate,**diagnostics,
            'effects_peak':float(np.max(abs(fx)))}
    if args.rebuild:
        rebuilt=fx*10**(args.fx_db/20)+r*10**(args.song_db/20)
        gain=min(1.,.89125094/max(float(np.max(abs(rebuilt))),1e-12))
        wavfile.write(dest/'rebuilt.wav',args.rate,(rebuilt*gain).astype(np.float32))
        report['rebuilt_global_attenuation_db']=float(20*np.log10(gain))
    (dest/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(f'Done: {dest.resolve()}\nListen to effects.wav AND removed.wav to check lost effects.')


if __name__=='__main__':
    try:
        main()
    except (ValueError,OSError,MemoryError) as e:
        print(f'Error: {e}',file=sys.stderr);sys.exit(1)
