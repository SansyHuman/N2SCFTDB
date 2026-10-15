# Consecutive LiE interface benchmark

CPU: AMD Ryzen 9 7950X 16-Core Processor. Affinity: [15]. 5 repetitions; medians shown.

Sequential calls, exact owning maps returned, no database access. Operands are prepared before timing. Timing includes binding conversions, native allocation/recovery, or subprocess startup/IPC/formatting/parsing. Batch mode also includes command construction and marker parsing. Verification is outside timing; mode order rotates between repetitions. The executable is the installed LiE; its compiler flags may differ from the local shared library. Batch mode is a startup-amortization control, not the currently implemented subprocess calculation API.

| Workload | Calls/round | Shared library | Executable/call | Executable/batch | Speedup vs per-call |
|---|---:|---:|---:|---:|---:|
| SU2 small | 1200 | 0.003475 s | 2.718946 s | 0.135793 s | 782.33x |
| SU3/Sp4 adjoint | 600 | 0.009657 s | 1.386635 s | 0.077927 s | 143.59x |
| SU9 adjoint tensors | 60 | 0.802336 s | 0.893245 s | 0.764069 s | 1.11x |
| E8 adjoint tensors | 24 | 0.276886 s | 0.317202 s | 0.264001 s | 1.15x |

All returned decompositions matched exactly, including signed coefficients. No production database was accessed. The benchmark itself wrote reports only; the application changes are described below.

Reproduce with the Python used to build the extension:

```sh
/home/subo-lee/miniconda3/envs/sage/bin/python -B test/benchmark_lie_interfaces.py --repetitions 5 --cpu 15 --output output/benchmarks/lie_timeout_sampling_20261001
```

## Adaptive native timeout checks

Native timeout polling now uses an inlined event counter, sampling the monotonic clock adaptively toward a 1 ms interval. Batches begin at 64 hooks, grow by at most 4x, remain between 1 and 4096 hooks, and shrink near the deadline. Entry, input preparation, calculation completion and result-export completion retain mandatory deadline checks. Zero still expires immediately; negative timeout still means unlimited. Sampling state is reset after success or failure.

| Workload | Native before | Native after | Elapsed reduction |
|---|---:|---:|---:|
| SU2 small | 0.004891 s | 0.003475 s | 28.9% |
| SU3/Sp4 adjoint | 0.015418 s | 0.009657 s | 37.4% |
| SU9 adjoint tensors | 1.112598 s | 0.802336 s | 27.9% |
| E8 adjoint tensors | 0.447833 s | 0.276886 s | 38.2% |

## Timeout overhead with the identical LiE core

The executable control links the exact same local shared library, retaining its interpreter and conventional allocation. Unlimited mode changes only the timeout option; native allocation tracking and object limits remain enabled.

| Workload | Deadline enabled | Unlimited | Overhead | Same-core CLI/call |
|---|---:|---:|---:|---:|
| SU9 adjoint tensors | 0.811639 s | 0.800020 s | 1.45% | 0.939454 s |
| E8 adjoint tensors | 0.276406 s | 0.278566 s | -0.78% | 0.334895 s |

The small negative E8 overhead is measurement variation, not evidence of acceleration caused by enabling a deadline. Compared with unlimited mode, the remaining measured deadline overhead is approximately 0 to 1.5%.

## Timeout behavior

All 119 relevant cache/wrapper/index integration tests passed. The standalone C++ regression driver also passed address and undefined-behavior sanitizers with leak detection. Exact comparison against the executable passed throughout both benchmarks. Failures leave no false cache entries and subsequent calls recover correctly.

Cooperative deadlines do not promise a hard 1 ms detection bound. Dense E8 initialization/math steps can delay the next polling hook, and cleanup adds to the elapsed return time. The short-deadline diagnostic measured:

| Requested timeout | Median elapsed return time |
|---:|---:|
| 0.0 ms | 0.007 ms |
| 2.0 ms | 35.136 ms |
| 10.0 ms | 34.604 ms |
| 50.0 ms | 50.199 ms |

Raw data is retained in `results.json`, `same_library_control.json`, `before_after.json`, and `timeout_latency.json`. The previous baseline remains in `../lie_interfaces_20261001/`. To repeat the identical-core control:

```sh
/home/subo-lee/miniconda3/envs/sage/bin/python -B test/benchmark_lie_embedding_control.py --cpu 15 --repetitions 5 --output output/benchmarks/lie_timeout_sampling_20261001
```
