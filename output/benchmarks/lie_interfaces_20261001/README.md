# Consecutive LiE interface benchmark

CPU: AMD Ryzen 9 7950X 16-Core Processor. Affinity: [15]. 5 repetitions; medians shown.

Sequential calls, exact owning maps returned, no database access. Operands are prepared before timing. Timing includes binding conversions, native allocation/recovery, or subprocess startup/IPC/formatting/parsing. Batch mode also includes command construction and marker parsing. Verification is outside timing; mode order rotates between repetitions. The executable is the installed LiE; its compiler flags may differ from the local shared library. Batch mode is a startup-amortization control, not the currently implemented subprocess calculation API.

| Workload | Calls/round | Shared library | Executable/call | Executable/batch | Speedup vs per-call |
|---|---:|---:|---:|---:|---:|
| SU2 small | 1200 | 0.004891 s | 2.738801 s | 0.135851 s | 559.97x |
| SU3/Sp4 adjoint | 600 | 0.015418 s | 1.395301 s | 0.077685 s | 90.50x |
| SU9 adjoint tensors | 60 | 1.112598 s | 0.893869 s | 0.763540 s | 0.80x |
| E8 adjoint tensors | 24 | 0.447833 s | 0.316467 s | 0.264203 s | 0.71x |

All returned decompositions matched exactly, including signed coefficients. No production database was accessed. No application code was changed.

Reproduce with the Python used to build the extension:

```sh
/home/subo-lee/miniconda3/envs/sage/bin/python -B test/benchmark_lie_interfaces.py --repetitions 5 --cpu 15
```

## Control using the identical shared-library core

The control executable is a CLI entry point linked to the very same local `liblie.so`. It retains the interpreter and conventional runtime allocation. Native unlimited mode changes only the timeout option to `-1`, suppressing the clock/deadline checks; native object limits and allocation tracking remain enabled. These are diagnostic settings; application defaults were not modified. Each result still matched exactly.

| Workload | Native/default deadline | Native/unlimited | Same-core CLI/call | Same-core CLI/batch |
|---|---:|---:|---:|---:|
| SU9 adjoint tensors | 1.111399 s | 0.824582 s | 0.978188 s | 0.844220 s |
| E8 adjoint tensors | 0.447190 s | 0.284770 s | 0.339025 s | 0.288680 s |

Suppressing native deadline checks reduced the SU9 run by 25.8% and the E8 run by 36.3%. Against the identical-core executable started for each call, native unlimited mode was approximately 1.19x faster in both cases. The remaining difference versus a batched identical-core interpreter was small (approximately 2.4% and 1.4%). These controls indicate that frequent native deadline checks account for the main reversal on expensive calculations; the executable startup savings dominate small calculations.

The installed executable and the local library have different build histories. Comparing only their wall times cannot assign all differences to embedding. The identical-core control removes that ambiguity for the timeout diagnosis.

Reproduce the control:

```sh
/home/subo-lee/miniconda3/envs/sage/bin/python -B test/benchmark_lie_embedding_control.py
```

Raw timings, result fingerprints and environment details are in `results.json` and `same_library_control.json`. The control executable and databases are temporary; no database was created by either benchmark.
