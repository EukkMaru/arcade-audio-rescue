# Arcade Audio Rescue

Recover surviving game sound effects from a damaged arcade recording using the
original song as a reference, then mix those effects over the clean song.

Built for a maimai stream whose music was intermittently suppressed while guide
and EXtap sounds mostly survived. Reference cancellation followed by expansion
of the recovered effects and a final mix limiter produced a result the recording's
owner reported as flawless. Results on other recordings will vary.

Runs offline on CPU. Requires Python 3.10+ and FFmpeg. Audio and video files are
not included in this repository.

## Quick start

Install Python and FFmpeg, making both available on PATH. On Windows, enable
Python's PATH option during installation. FFmpeg is a separate executable, not
an audio package installed with pip.

Extract or clone this repository, open a terminal in its directory, and run:

```powershell
py -m pip install -r requirements.txt
py rescue.py "C:\Videos\gameplay.mp4" "C:\Music\song.mp3" --out result --rebuild
```

On Linux/macOS, use `python3` instead of `py`. Quote paths containing spaces.
The tool reads the first audio stream of the video; export a different audio
stream first if necessary. Each run requires a new output directory and leaves
input files untouched.

## Defaults: the successful cancellation preset

| Setting | Default |
| --- | --- |
| Method | `cancel` |
| Subtraction strength | `1.15` |
| Local fitting window | `0.15` seconds |
| Sample rate | `48000` Hz, stereo |
| Rebuilt mix song gain | `-6` dB |
| Rebuilt mix effects gain | `0` dB |

The command above uses these settings automatically. The software exports the
recovered audio; expansion and dynamic limiting are applied separately in your
editor. It does not silently bake in unknown Premiere settings.

## Outputs

| File | Purpose |
| --- | --- |
| `effects.wav` | Estimated surviving game effects; import at recording time zero. |
| `removed.wav` | Removed audio; listen for wanted effects accidentally removed. |
| `aligned_song.wav` | Reference song placed on the recording's full timeline. |
| `rebuilt.wav` | With `--rebuild`: reference song plus recovered effects. |
| `report.json` | Settings, alignment matches, estimated drift, and output levels. |

WAV files use 32-bit float. The effects and aligned reference may exceed full
scale; lower their gain before a fixed-point export. The rebuilt mix receives
only uniform attenuation if needed to keep sample peaks below -1 dBFS. This
preserves dynamics and does not implement a real-time limiter or true-peak ceiling.

## Premiere finishing chain

1. Mute the damaged recording's audio.
2. Place `effects.wav` and `aligned_song.wav` at the same start as the recording.
3. Apply **Dynamics → Expander** to the effects track only. A starting point is
   threshold **-30 dB**, ratio **4:1**, with the other Dynamics modules disabled.
   Raise the threshold gradually if music residue remains; lower it if wanted
   guide/EXtap sounds weaken. These are starting values, not measured final
   settings from the original edit.
4. Balance the clean song against the effects. The tool's optional rebuilt mix
   starts the song at -6 dB; you can use that as an initial balance in Premiere.
5. Add **Hard Limiter** to the combined Mix/Master channel. Start at maximum
   amplitude **-1 dB**, input boost **0 dB**, with default timing, then listen.

Use the separate effects and song WAVs for this chain. `rebuilt.wav` is a quick
mix for auditioning; its effects have not received Premiere's expansion.

## Adjusting the recovery

For less subtraction:

```powershell
py rescue.py "gameplay.mp4" "song.mp3" --out gentle --strength 1.0 --window 0.25 --rebuild
```

Try the alternative spectral energy-removal method:

```powershell
py rescue.py "gameplay.mp4" "song.mp3" --out spectral --mode spectral --strength 1.0 --window 0.25 --rebuild
```

Spectral mode alone uses `--floor-db` (default -30 dB); -15 retains more residual
signal and -40 permits stronger attenuation. Smaller fitting windows follow
changing suppression faster but may mistake more effects for song. Greater
subtraction strength can leave inverted music residue rather than improve it.
Always compare `effects.wav` with `removed.wav`.

Use `--song-db` and `--fx-db` to set the optional rebuilt mix balance:

```powershell
py rescue.py "gameplay.mp4" "song.mp3" --out balanced --song-db -6 --fx-db 3 --rebuild
```

## Alignment and algorithm

The reference must match the game's song edit. The program uses normalized
cross-correlation to estimate timing, refines distributed local matches, and
estimates a linear clock drift when enough reliable matches exist. It then
fits changing complex reference-to-recording transfers in short overlapping
time/frequency neighborhoods, separately for each channel.

Cancellation mode subtracts the estimated song component. Spectral mode estimates
song power, floors residual power at a nonnegative attenuation floor, and retains
mixture phase. Neither method clips negative waveform samples.

If matching fails, supply the approximate video time corresponding to reference
time zero (negative if the recording starts partway through the reference):

```powershell
py rescue.py "gameplay.mp4" "song.mp3" --out manual --offset 1.247 --drift-ppm 0 --rebuild
```

A supplied offset can still be locally refined. `--drift-ppm 0` disables drift
estimation. Low automatic match confidence causes an error. High confidence
validates alignment, not effects isolation.

## Limits

Already-erased sounds cannot be recovered. Song-correlated effects can be
subtracted, distorted music can remain, and wrong edits or nonlinear timing
changes can prevent matching. This version handles a global offset and linear
drift, not arbitrary time warping. It loads decoded audio and spectral arrays
into memory; allow roughly 1–2 GB for a two-minute stereo recording. For longer
videos, extract a song-sized segment and place results at that segment's start.

## Tests

```powershell
py -m unittest discover -s . -v
```

Deterministic synthetic tests check the default preset, delay/drift estimation,
cancellation against known effects, zero-strength passthrough, and missing-
reference passthrough. Synthetic accuracy is not a quality claim about real
arcade recordings.
