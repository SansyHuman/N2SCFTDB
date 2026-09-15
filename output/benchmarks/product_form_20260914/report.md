# Product-gauge FORM timeout investigation

Measured 14 September 2026, using FORM 5.0.0 and the current Sage implementation.

The primary bottleneck is the repeated expansion of the **joint formal-character
plethystic exponential**, before singlet projection. Two avoidable costs are
especially important: expanding disconnected gauge sectors together, and
multiplying by exponent terms that cannot fit within the remaining t cutoff.
Large surviving formal polynomials then add substantial parsing and memory costs.

## Evidence from the actual failures

The run in `logs/log_index_20260914_174903_908185.log` selected 3,164 theories,
used 32 worker processes, full order 18 and Coulomb cutoff 90. It logged 39
`superconformal_index: FORM calculation timed out` errors, about 600.3–600.6
seconds after their calculations started. The run was eventually stopped.
The later retries of theory 9780 took about 60.5 seconds, including a run with
only one selected theory. The current saved `tools/timeout` value is **60**;
the application default remains **600**. Settings were inspected, not changed.

A read-only query retrieved those 39 theories from the configured database.
Their exact inputs are retained in [timeout_cases.json](timeout_cases.json).
Structural analysis is in [case_structure.json](case_structure.json):

| Structure | Timed-out theories | Formal characters | Matter character monomials |
|---|---:|---:|---:|
| Two disconnected gauge sectors | 29 | 10 | 8 |
| Coupled product groups | 8 | 8 | 8 |
| Coupled product groups | 2 | 10 | 10 |

The number and support of the matter character monomials matter more to this
FORM stage than the ranks alone. For example, theories **9780** (SU(3) × SU(3)),
**9816**, **9844**, **9998** (SU(3) × SU(11)) and **12561** generate exactly the
same order-18 FORM program. Group-specific representation decomposition occurs
later, in the LiE/singlet stage.

There are 31 distinct FORM programs among the 39 failures. Cache lookup precedes
calculation and insertion follows it, with no claim for an in-progress program.
Workers can therefore duplicate the same expensive cache miss. A timeout never
writes the incomplete expansion, so a retry starts that FORM expansion again.

## Controlled measurements

Each FORM timing bypassed the FORM cache and executed the generated program
directly in a private temporary directory, with statistics enabled. There was
no MySQL query or write inside these timings. Projection used a private copy of
the existing character cache; these are **not cold-LiE timings**. The initial
baseline sweep was sequential. Later diagnostic runs could overlap on this
32-logical-CPU machine; values below are single-run measurements, not statistical
speed guarantees. CPU and wall times were nearly equal for the long FORM runs.

| Example, through t^18 | FORM wall time | Formal expansion terms | Parse time | Projection time |
|---|---:|---:|---:|---:|
| SU(2) × SU(2), two full bifundamentals | 2.40 s | 28,154 | 0.35 s | 0.08 s |
| SU(2) × G2, repository example | 17.92 s | 146,305 | 1.80 s | 0.37 s |
| One SU(3) sector: one fundamental + one symmetric full hyper | 11.37 s | 113,831 | 1.39 s | 0.93 s |
| Theory 9780: two such SU(3) sectors, printing disabled | **516.16 s** | **2,649,141** | not run in this control | not run in this control |
| Theory 12526: coupled SU(11) × SU(11) | >60 s, stopped by profiling cap | unfinished | — | not reached |

The 60-second entry is a profiling cap, not a measurement claiming that the
program takes exactly 60 seconds. Historical 600-second failures occurred in
the 32-worker run described above. An extended 600-second-budget control for
theory 9780 completed in **516.16 s wall / 516.05 s CPU**, even with `Print result`
removed. Only about 16% additional wall time under load would make this case
exceed 600 seconds. The trace and resources are in
[no_print_600/result.json](9780_t18_no_print_600/result.json).

For theory 9780, its Coulomb index through dimension 90 took **0.228 s** and
its complete spectrum **0.00079 s**. The spectrum is `(2, 2, 3, 3)`.
The error message is raised by the timeout around the FORM subprocess itself;
Python parsing, singlet projection and database writes occur after that call.

## Where FORM spends the time

`index/n2_theory_index.py:160` builds a single exponent `itotal` containing
all gauge factors and matter species. With N = 18 it has the form

\[
 S=\sum_{j=1}^{9}\frac{f(t^j,y^j,u^j;\chi^{(j)})}{j},\qquad
 I=\left[\exp S\right]_{t^{\le18}}.
\]

The loop at lines 185–187 repeatedly substitutes
`z = 1 + z*itotal/i` and sorts. For theory 9780, `itotal` contains 616 terms.
In the first baseline trace, the growing `I` expression reaches:

| Completed Taylor step | Cumulative FORM CPU time | Generated terms in that module | Terms retained in I |
|---|---:|---:|---:|
| 2 | 0.16 s | 64,668 | 16,950 |
| 3 | 3.53 s | 699,916 | 129,970 |
| 4 | 24.80 s | 2,623,042 | 481,798 |

The first 60-second cap interrupts step 5. The extended original-program run
finishes with the same 2,649,141-term count as the diagnostic expansion. It takes
about 212 s to finish step 6, 354 s for step 7 and 461 s for step 8; polynomial
printing is disabled throughout that control. The completed diagnostic expansion has
**2,649,141** formal terms and **859,673** character structures, but only
**168** monomials after projection and collection. The connected theory 12526
has **2,548,814** formal terms and only **174** final monomials.

The declaration `t(:18)` does truncate automatically, but FORM applies that
restriction during term normalization. It does not arrange the multiplication
to avoid every excessive-degree candidate in the first place. Thus a term
already near the cutoff still encounters the whole 616-term exponent in the
original loop. The FORM manual documents this normalization behavior in its
[symbol declarations](https://form-dev.github.io/form-docs/stable/manual/#symbols).

No FORM scratch-file spill was observed in these runs. FORM CPU time tracks
wall time, so the isolated delay is active symbolic computation, rather than a
database deadlock or a wait for the character cache. The heavy completed FORM
processes peaked around **1.03–1.08 GiB RSS** each. Thirty-two concurrent heavy
jobs can also put pressure on this machine's approximately 30 GiB RAM, before
accounting for Python and its parsed term objects.

## Exact factorization opportunity

Theory 9780 has full hypers
`(3,1) + (6,1) + (1,3) + (1,6)`, with the conjugates supplied by the full-hyper
convention. No hypermultiplet is charged under both gauge factors. Its index
therefore factorizes:

\[
 \int_{G_1\times G_2}\!\mathrm{PE}[f_1+f_2]
 =\left(\int_{G_1}\!\mathrm{PE}[f_1]\right)
  \left(\int_{G_2}\!\mathrm{PE}[f_2]\right).
\]

Both sectors are identical, so one sector calculation can be squared and
truncated through t^18. The measured sector stages total **13.684 s**; multiplying
its 145-term projected polynomial and truncating took **0.00282 s**, producing
168 final terms. At t^12 this agrees exactly with the existing combined pipeline;
at t^18 it agrees exactly with the degree-bounded combined diagnostic below.
The exact coefficients/checks are in [factorization_check.json](factorization_check.json).
This identity applies to the 29 disconnected failures, but does not split
theories with charged bifundamental matter such as 12526.

## Diagnostic changes tested without changing application code

The profiling script contains two experimental variants. `prune_z` finalizes
marked terms whose degree is already greater than N−2; it helps at order 12,
but the heavy order-18 examples still exceeded its 120-second profiling cap.

`degree_bounded` brackets `itotal` by t degree and only multiplies a degree-d
term by coefficients of degrees at most N−d. It preserves the same finite
exponential. The timing and exact sorted-formal-polynomial comparisons are in
[validation.json](validation.json):

| Case | Order | Original FORM | Degree-bounded FORM | Exact formal polynomial checked |
|---|---:|---:|---:|---|
| Theory 9780 | 12 | 4.55 s | 0.77 s | yes |
| Theory 12526 | 12 | 4.88 s | 0.83 s | yes |
| SU(2) × SU(2) bifundamentals | 18 | 2.40 s | 0.65 s | yes |
| SU(2) × G2 | 18 | 17.92 s | 3.29 s | yes |

For the two large order-18 cases, the degree-bounded variant completed FORM in
**59.07 s** (9780) and **60.59 s** (12526).
The 9780 result is about **8.7 times faster** than the 516.16-second original
control, despite printing the polynomial that the original control omitted.
This ratio is diagnostic rather than a matched-output-mode benchmark.
The variant still materializes the entire
joint formal polynomial: parsing took **41.36/40.23 s**, and projection
**10.87/9.42 s**, respectively. Their FORM stdout sizes were **137.1/143.4 MiB**.
Thus improving multiplication alone exposes a substantial next cost in parsing
and retaining these pre-projection expansions. Disconnected-sector factorization
avoids that large intermediate representation altogether.

## Recommended implementation order

1. Detect connected components of the gauge/matter incidence graph. Calculate
   independent components separately and multiply their already-projected indices,
   with exact truncation. Handle gauge-singlet hypers as a separate common factor.
2. Make the FORM exponential multiplication aware of the remaining degree bound,
   including for connected product groups. Validate general inputs and cutoffs
   before promoting the diagnostic variant into the runtime builder.
3. Coordinate identical in-progress FORM programs so workers share expensive
   expansions; use a memory-conscious worker limit for large intermediate results.

Increasing the timeout can allow an expensive calculation to finish, but does
not remove any of these costs. The saved 60-second setting also explains why
the later small retries fail much sooner than the original 600-second run.

## Reproduction and files

All new diagnostic code is in `test/benchmark_product_form.py`. Application
source, GUI settings, source cache databases and stored theory properties were
not modified by this investigation. Private projection caches and all outputs
are under this report directory.

```bash
PYTHONDONTWRITEBYTECODE=1 DOT_SAGE=/tmp/codex-sage-cache \
  sage -python -B test/benchmark_product_form.py \
  --cases bifundamental a1_g2 su3_sym_fund 9780 12526 \
  --orders 12 18 --timeout 60 --project

PYTHONDONTWRITEBYTECODE=1 DOT_SAGE=/tmp/codex-sage-cache \
  sage -python -B test/benchmark_product_form.py \
  --cases 9780 12526 --orders 18 --timeout 180 \
  --variant degree_bounded --project

PYTHONDONTWRITEBYTECODE=1 DOT_SAGE=/tmp/codex-sage-cache \
  sage -python -B test/benchmark_product_form.py \
  --cases 9780 --orders 18 --timeout 600 --no-print --variant no_print_600

PYTHONDONTWRITEBYTECODE=1 DOT_SAGE=/tmp/codex-sage-cache \
  sage -python -B test/benchmark_product_form.py --verify
```

The last command writes [verification.json](verification.json), checking six
complete formal-polynomial comparisons and the exact sector-product coefficients
through orders 12 and 18. These are targeted diagnostic checks; this investigation
did not rerun the application regression suite or promote the prototype.

Each case directory contains `program.frm`, the FORM statistics/output in
`form.out`, and a `result.json` with timings, program hash, term counts and
final coefficients when projection ran. Use `--input-cases` to select another
exported JSON array and `--output` to choose a separate output directory.
