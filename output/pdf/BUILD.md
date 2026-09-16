# PDF sources and rebuilding

## Current revision: 16 September 2026

The canonical outputs are the **54-page implementation reference** and the
**26-page index mathematical background**. This revision documents the complete
session implementation: index-tab layout/search/selection and retained jobs,
the two tab controllers and shared GUI/file logging, parallel index workers and
their shutdown/transaction behavior, the test relocation and test-account
permission repair, disconnected-sector calculation and stored-index reuse,
character-cache file arguments, degree-bounded FORM multiplication, and
password-confirmed Settings deletion with its separate editable `.ui` file.
The SQLite size and WAL discussion is included as operational guidance;
flavor-refined factorization is explicitly mathematical, not an implemented API.

The index guide remains scoped to index algorithms. Its sections 5.4-5.6
(PDF pages 5-7) explain the FORM program line by line, including bracket
coefficients, module-local `Skip`, the `z`/`w` markers, factorial induction,
both degree bounds, low-order cases and a worked expansion. Sections 7.4-7.6
(pages 11-13) cover disconnected components, singlets, flavor characters,
database reuse and the measured bottlenecks. The reference's GUI additions are
sections 5.6-5.9 (PDF pages 47-50). Earlier experimental timings and test counts
retain their dates and cache/printing conditions. Application tests and
benchmarks were not rerun for this documentation-only refresh.

Additional retained source dependencies:

- `form_degree_bound.tex`: current FORM derivation and generated-code excerpt,
  included by both documents through their `sessionheading` macro.
- `disconnected_index_sectors.tex`: graph partition, factorization proof,
  borrowed database connection, exact truncation and profiling evidence.
- `gui_index_settings_workflow.tex`: controllers, index workflow, logging,
  lock/retry boundaries, test organization and deletion; reference only.
- `session_references.tex`: official FORM, NetworkX and SQLite references;
  included in both bibliographies.

Both documents were built with `pdflatex` until cross-references stabilized.
All 80 final pages were rendered; contact sheets and full-size checks of new
material found no clipping or overlap. There are no unresolved references,
citations or overfull boxes. The reference retains its prior mild underfull
diagnostic in the Coulomb API table. All pre-existing numbered equation
environments are unchanged, and preserved guide pages 2-4 have identical
extracted text. Their original FORM recurrence is explicitly identified as
the baseline; the added sections show the current implementation. The order-8
FORM listing was checked against `_build_form_program` itself.

Build logs and render scratch files are in
`/tmp/n2-session-pdfs-20260916/`; none is required for rebuilding. The commands
below remain valid with the additional retained inputs listed above.

## Earlier revisions and retained build instructions

The 15 September 2026 cache-path update changes index examples to
`char_cache_database_path=` / `--char-cache-database`, including the database
index worker. The standalone character-cache builder retains its own path
options. Both PDFs are rebuilt from the updated sources.

The 14 September 2026 revision adds the anomaly GUI, persistent index cutoffs
and CPU limits, portable Sage launch, editable/file-loaded gauge lists,
parallel SCFT checking and MySQL imports, optional character-cache preparation,
Stop behavior, timestamped GUI/file logs, test-harness repairs and the
missing-properties-row deadlock fix to the implementation reference. The index
guide is restricted to index-package algorithms and validation: its former
chapters 12 (property/database workflow) and 13 (anomaly GUI) have been removed.
Their content is retained in the reference without repeating its existing
explanations. Interpretation and limitations is now chapter 12 of the guide.

The final implementation run immediately before this documentation refresh
passed 268 backend tests in 33.518 seconds with no skips, against an isolated,
password-protected MySQL 8.0.46 server using actual Sage/FORM/LiE. During the
refresh, 32 offscreen GUI tests passed in 0.869 seconds. The reference's GUI chapter
records the controlled 256-candidate/eight-worker comparison: 93 failed inserts
before the fix, all 256 inserted afterward, and zero added deadlocks in the
fixed pair/stress runs. It distinguishes these observations from guarantees.

The 12 September property/index split, schema 7, deferred worker and independent
precision rules remain intact. Existing equations and dated cache benchmarks
are retained; earlier 238- and 209-test results remain historical evidence.

Both final PDFs have retained LaTeX sources in this directory:

- `n2_implementation_reference_summary.tex` builds the package reference here.
- `n2_theory_index_Mathematical_Background.tex` builds the index algorithm guide;
  its canonical PDF is `../../index/n2_theory_index_Mathematical_Background.pdf`.

The index guide includes `source_assets/n2_background_preserved_pages_2_to_4.pdf`
to retain unrevised content from the original document exactly. Original page 2
has one revised sentence describing SQLite storage; pages 3-4 are unchanged.
Keep this asset with the TeX source. The remaining pages are authored in LaTeX.
Both documents include `cache_session_algorithms.tex` and
`cache_session_validation.tex`. Only the implementation reference includes
`property_database_workflow.tex` and `gui_anomaly_workflow.tex`; retain those
files for its build. They contain the property/database contracts and GUI
workflow removed from the index guide. The chapter 12 introductory material
already appears in the reference's property API and Coulomb-precision sections;
the direct-index/SCFT-wrapper boundary is now explicit in its property section.

Run from `output/pdf` with a TeX Live installation that supplies the packages
listed in the preambles. Choose an existing temporary build directory. Two
passes resolve local equation, page and bibliography references; no BibTeX run
is needed because references are in `thebibliography`.

```bash
mkdir -p /tmp/sci-reference-build
pdflatex -interaction=nonstopmode -halt-on-error -output-directory /tmp/sci-reference-build n2_implementation_reference_summary.tex
pdflatex -interaction=nonstopmode -halt-on-error -output-directory /tmp/sci-reference-build n2_implementation_reference_summary.tex
pdflatex -interaction=nonstopmode -halt-on-error -output-directory /tmp/sci-reference-build n2_theory_index_Mathematical_Background.tex
pdflatex -interaction=nonstopmode -halt-on-error -output-directory /tmp/sci-reference-build n2_theory_index_Mathematical_Background.tex
```

Inspect logs for unresolved references and overfull boxes. Render the PDFs with
`pdftoppm -png` and review the pages before replacing the canonical outputs:

```bash
cp /tmp/sci-reference-build/n2_implementation_reference_summary.pdf ./n2_implementation_reference_summary.pdf
cp /tmp/sci-reference-build/n2_theory_index_Mathematical_Background.pdf ../../index/n2_theory_index_Mathematical_Background.pdf
```

The retained 10 September 2026 index/cache revision describes source commit `04bbe21`:

- Exact FORM-term serialization and SQLite lookup keyed by the full raw program.
- Per-thread/process connections, concurrent misses and cross-theory expansion reuse.
- Reuse of cached lower-order character factors and its measured limitations.
- `build_decomposition_cache`, Frobenius enumeration, process batches, order
  barriers, resume behavior, CLI examples and the `floor(N/2)` index cutoff.
- FORM cache I/O and nine-theory timings with character/singlet caches prefilled,
  planner comparisons and the 209-test validation snapshot (207 pass, 2 skips).

New displays in the shared inputs are unnumbered. Existing equation numbers
remain stable; the guide's projection equations retain labels (28a)-(28d).
The embedded source asset is unchanged by this revision. Older projection and
A32 profiles are explicitly dated as preceding FORM expansion caching. The
reference's property/database chapters retain the 12 September workflow and
its GUI chapter adds the 14 September implementation. The index guide contains
neither chapter. Other mathematical chapters retain their earlier snapshots.

The earlier database implementation was validated separately with 238 passing tests in
21.920 seconds, including actual Sage/FORM/LiE and a disposable MySQL 8.0.46
server. The later 268-test run and fresh GUI rerun are recorded above. This
documentation update verifies source contracts, two-pass builds, PDF text and
visual layout. No application source or production database is changed during
the documentation refresh. Benchmark numbers remain dated measurements.

For a reproducible layout review, render both staged outputs before copying:

```bash
mkdir -p /tmp/sci-reference-build/render
pdftoppm -r 110 -png /tmp/sci-reference-build/n2_implementation_reference_summary.pdf /tmp/sci-reference-build/render/reference
pdftoppm -r 110 -png /tmp/sci-reference-build/n2_theory_index_Mathematical_Background.pdf /tmp/sci-reference-build/render/background
```

Check every page, especially shared cache sections, tables and function maps.
The final LaTeX logs must have no unresolved citations/references or overfull
boxes. Build products and review PNGs belong in the temporary build directory,
not beside the tracked final PDFs.

Historical verified outputs for the 12 September revision: **39-page implementation
reference and 24-page index guide**. All pages were rendered and visually
reviewed, with the changed sections additionally checked at reading size.
There are no unresolved citations/references or overfull boxes. The reference
retains one pre-existing mild underfull-box diagnostic in the Coulomb API table;
it has no visible clipping or overlap. All 48 reference and 15 guide authored
equation/align environments remain verbatim, and embedded guide pages 2-4
retain their previous text. New cutoff-policy displays are unnumbered.

The prior 10 September validation was 209 tests in 19.658 seconds: 207 passed,
2 MySQL skips. It remains labeled as historical evidence in the shared cache
chapter rather than being presented as the current test result.

Historical outputs for the **initial 14 September revision: 44-page implementation
reference and 29-page index guide**. Both PDFs were built with `pdflatex` and
resolved cross-references. All 73 pages were rendered and visually reviewed;
the new chapter pages were also inspected at reading size. No unresolved
citations/references or overfull boxes remain. The reference retains the
pre-existing mild underfull-box diagnostic in the Coulomb API table, with no
visible clipping or overlap. All 48 reference and 15 guide equation/align
blocks match the previous sources verbatim. Embedded guide pages 2-4 retain
identical extracted text, and the preserved source asset is unchanged.

Initial GUI chapter location: reference section 5 (PDF pages 36-40, printed pages
35-39), guide section 13 (pages 23-27). Contents and cross-references point to
the updated sections. The new cutoff display is unnumbered. Temporary build
logs and review PNGs for this refresh are under `/tmp/n2-session-docs-20260914/`;
they are not required to rebuild the retained sources.

Verified outputs after the **14 September package-scope correction: 44-page
implementation reference and 20-page index guide**. The former guide chapters
12-13 occupied nine pages. Their property/database and GUI explanations remain
in the reference's common and GUI chapters; no duplicate chapter was added.
The reference's separate index-property section now explicitly names the direct
index API and its different SCFT-checking contract. Its Coulomb-precision
section already explains why order 18 supports dimension nine and why the
separate calculation can reach dimension 90.

Both outputs were rebuilt with resolved references. Guide pages 2-18 retain
their content (apart from extraction whitespace), all 48/15 authored
equation/align blocks remain verbatim, and the embedded guide pages 2-4 retain
identical text. The reference's changed page is PDF page 28; its GUI chapter
remains section 5 on PDF pages 36-40. The guide now ends with chapter 12,
Interpretation and limitations, on page 19, followed by References on page 20.
No undefined citations/references or overfull boxes remain; the reference's
previous mild underfull diagnostic is unchanged. This edit reran document
build/layout checks, not application tests. Build and rendering scratch files
are under `/tmp/n2-index-scope-20260914/`.
