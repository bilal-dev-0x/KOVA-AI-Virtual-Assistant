# KOVA performance measurements

Measured 9 Nov 2026 on the Windows 10 development laptop, three runs per state. Values are medians; each run sampled for 120 seconds after the dashboard window appeared.

## Results

| Measurement | Reaper off: runs 1 / 2 / 3 | Median | Reaper on: runs 1 / 2 / 3 | Median |
|---|---:|---:|---:|---:|
| Start to dashboard visible | 1.35 / 1.33 / 1.38 s | 1.35 s | 1.35 / 1.45 / 1.40 s | 1.40 s |
| Local handler latency (`what time is it`, outside UI) | 9.1 / 0.69 / 0.67 ms | 0.70 ms | 6.9 / 0.70 / 0.71 ms | 0.70 ms |
| Dashboard idle CPU, asleep voice loop | 4.45 / 2.50 / 3.40% | 3.40% | 2.75 / 2.70 / 2.60% | 2.70% |
| Process-tree private memory (USS) | 258.0 / 198.5 / 212.3 MB | 212.3 MB | 239.6 / 241.7 / 240.9 MB | 240.9 MB |

The Reaper-on measurement used a temporary in-memory `PET_ON=True` override in the benchmark child; it did not modify `config.json`. The median memory increase was about 28.6 MB, and CPU differences are within the run-to-run variation (the one 4.45% off sample still sat below 5%). An earlier version of this table carried figures from before a verified harness run; every number above comes from the 9 Nov rerun of `scripts/measure_performance.py`. "Asleep voice loop" means the dashboard's normal background wake-word listener, not an actively spoken command. CPU during active speech capture was not separately measured. The local-handler latency is a direct `Agent.handle` measurement and is not an end-to-end dashboard response time.

## Method and limits

Run `python scripts/measure_performance.py --runs 3 --idle-seconds 120 --pet-mode off` and repeat with `--pet-mode on`. The harness starts the normal dashboard, waits until its KOVA window is visible, and samples the Python process and descendants every five seconds. CPU is the process-tree total; memory sums `memory_full_info().uss` (private memory), not working sets. It stops only the process tree it created. A standalone local `Agent` handles `what time is it` to measure local routing without network or desktop interaction. Dashboard startup and response timing are measured separately.

The machine's state, installed apps, screen scale, and WebView2 version affect these figures. The tests did not run a spoken command, play audio, or change the user's config. Active microphone capture latency/CPU and a new screenshot from the live window remain unverified.
