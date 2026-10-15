# C++ index expansion timeout overhead

Native function time includes validation, worker management, exact expansion, output construction and sorting; it excludes process startup, text input/output and hashing. One warmup per mode, followed by rotated mode order. Exact sorted output fingerprints match every run. Default timeout is 600 seconds; -1 is unlimited.

7 measured repetitions per mode; medians shown. Negative overhead reflects measurement variation.

| Input | Cutoff | Workers | Terms | Before | Unlimited | Default timeout | Timeout overhead |
|---|---:|---:|---:|---:|---:|---:|---:|
| simple | 18 | 1 | 6771 | 0.007893 s | 0.007990 s | 0.007895 s | -1.19% |
| simple | 18 | 4 | 6771 | 0.007772 s | 0.008073 s | 0.008606 s | 6.60% |
| trifundamental | 18 | 1 | 81816 | 0.197116 s | 0.191141 s | 0.195301 s | 2.18% |
| trifundamental | 18 | 4 | 81816 | 0.158018 s | 0.160498 s | 0.157049 s | -2.15% |
| trifundamental | 20 | 1 | 191148 | 0.538758 s | 0.549538 s | 0.537951 s | -2.11% |
| trifundamental | 20 | 4 | 191148 | 0.460222 s | 0.453578 s | 0.460153 s | 1.45% |
| large_output | 2 | 1 | 4202 | 0.003341 s | 0.003229 s | 0.003286 s | 1.77% |
| large_output | 2 | 4 | 4202 | 0.003400 s | 0.003300 s | 0.003318 s | 0.55% |

No persistent data was accessed. The internal unordered helper preserves canonical character tuples while skipping only the final outer-vector sort. The public functions retain the previous exact sorted output.

See `results.json` for raw samples, fingerprints, compiler command and source hashes.

The short four-worker simple case was repeated for 31 measured runs per mode,
with rotated mode order and CPU affinity restricted to four physical cores
(CPUs 8, 9, 10 and 11). Its median was 0.007565 s with the default deadline,
0.007543 s unlimited, and 0.007666 s before the change: 0.29% timeout overhead.
Exact sorted outputs matched on every run. The original 6.60% sample above was
not reproduced; see `simple_parallel_confirmation.json` for all repeat samples.
The larger cases measured approximately 0–2% overhead within timing variation.

Validation passed: 13 expansion tests, including 316 exact comparisons against
FORM; three focused AddressSanitizer/UndefinedBehaviorSanitizer tests; and the
ThreadSanitizer timeout/cancellation self-test. Tests cover zero deadlines,
serial and parallel expiration, recovery, independent concurrent callers,
finite/unlimited equality, and equality of unordered and sorted term maps.
Timeouts are cooperative, so an individual GMP operation, allocation or cleanup
can delay the final exception beyond the deadline.
