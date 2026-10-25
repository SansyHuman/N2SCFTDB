# Independent LiE environment benchmark

Independent native caches per persistent thread. Same fixed number of uncached tensor calls at every worker count. Prepared signed Adams operands and reference calculation outside timing. Thread/library initialization reported separately. Timed calls include Python binding conversions and returned exact maps; exact comparisons occur after timing. Default 600-second per-call timeout. Each row uses a fresh process. No SQLite or index expansion work.

CPU: AMD Ryzen 9 7950X 16-Core Processor. 256 calls/row; medians of 3 runs.

| Workload | Workers | Median | Speedup vs 1 | Setup | Peak RSS |
|---|---:|---:|---:|---:|---:|
| su9 | 1 | 2.039905 s | 1.00x | 0.000564 s | 32.2 MiB |
| su9 | 2 | 1.930534 s | 1.06x | 0.000750 s | 31.7 MiB |
| su9 | 4 | 0.962222 s | 2.12x | 0.001136 s | 33.2 MiB |
| su9 | 8 | 0.490896 s | 4.16x | 0.001968 s | 35.2 MiB |
| su9 | 16 | 0.288792 s | 7.06x | 0.003357 s | 39.2 MiB |
| su9 | 32 | 0.186297 s | 10.95x | 0.006083 s | 47.7 MiB |
| e8 | 1 | 5.928321 s | 1.00x | 0.000632 s | 28.0 MiB |
| e8 | 2 | 5.407275 s | 1.10x | 0.000768 s | 28.8 MiB |
| e8 | 4 | 2.718686 s | 2.18x | 0.001253 s | 28.2 MiB |
| e8 | 8 | 1.385425 s | 4.28x | 0.001905 s | 29.5 MiB |
| e8 | 16 | 0.733059 s | 8.09x | 0.003505 s | 32.2 MiB |
| e8 | 32 | 0.494943 s | 11.98x | 0.006307 s | 41.9 MiB |

All exact results matched. These are independent LiE calls, not end-to-end index timings.
The existing `project_index` scheduler remains serial.

Reproduce:

```sh
/home/subo-lee/miniconda3/envs/sage/bin/python -B test/benchmark_lie_environments.py --workers 1 2 4 8 16 32 --calls 256 --repetitions 3
```
