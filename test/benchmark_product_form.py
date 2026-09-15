"""Profile product-group FORM programs without changing runtime code or source caches.

Run with Sage. Inputs are explicit JSON files or the exported timeout_cases.json.
Each run retains the generated program, FORM statistics, resource use and timings
under its output directory. Optional projection uses private cache copies.
"""

import argparse
from contextlib import closing
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import re
import resource
import sqlite3
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from index import n2_theory_index as idx
from index.form_expansion_cache import FormExpansionCache
from index.char_decomposition_cache import CharacterDecompositionCache


def describe(data):
    factors, hypers = idx._parse_input(data)
    specs, vectors, matter = idx._character_basis(factors, hypers)
    return factors, hypers, specs, vectors, matter


def form_statistics(text):
    pattern = (r"Time =\s+([\d.]+) sec\s+Generated terms =\s+(\d+)\s*\n"
               r"\s*(\w+)\s+Terms in output =\s+(\d+)\s*\n"
               r"\s*(?:step (\d+)\s+)?Bytes used\s+=\s+(\d+)")
    return [{"cpu_seconds": float(t), "generated_terms": int(g), "expression": name,
             "output_terms": int(n), "step": int(step) if step else None,
             "bytes": int(size)} for t, g, name, n, step, size in re.findall(pattern, text)]


def profile(case, data, order, output, timeout, *, print_result=True, project=False,
            variant="baseline"):
    factors, hypers, specs, vectors, matter = describe(data)
    program = idx._build_form_program(order, len(specs), vectors, matter)
    digest = hashlib.sha256(program.encode()).hexdigest()
    if variant == "prune_z":
        # Diagnostic-only algebraic equivalent: a marked term of degree >N-2
        # cannot contribute another power of the exponent (minimum degree 2).
        # Keep its existing contribution but stop expanding its z branch.
        program = program.replace("  id z=1+z*itotal/`i';", f"""  if (count(t,1) > {order - 2});
    id z=1;
  endif;
  id z=1+z*itotal/`i';""")
    elif variant == "degree_bounded":
        # Diagnostic prototype: preserve t-brackets on the exponent and select
        # only coefficients that can contribute below the remaining cutoff.
        program = program.split("L I=z;", 1)[0].replace("j,z,u,t", "j,z,w,u,t") + f"""
Bracket t;
.sort
Skip J,itotal;
L I=z*itotal;
.sort
#do i=2,{order // 2}
  Skip J,itotal;
  #do k=2,{order - 2}
    if (count(t,1) == `k');
      id z=1+w*sum_(idx1,2,{order}-`k',itotal[t^idx1]*t^idx1)/`i';
    endif;
  #enddo
  id z=1;
  id w=z;
  .sort:step `i';
#enddo
L result=1+I;
id z=1;
.sort
Print result;
.end
"""
    program = program.replace("Off statistics;", "On statistics;")
    if not print_result:
        program = program.replace("Print result;", "")
    folder = output / f"{case}_t{order}_{variant}"
    folder.mkdir(parents=True, exist_ok=True)
    source, stdout = folder / "program.frm", folder / "form.out"
    source.write_text(program)
    print(json.dumps({"case": case, "order": order, "stage": "FORM", "characters": len(specs)}), flush=True)
    usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    start = time.perf_counter()
    peak_rss = peak_scratch = 0
    timed_out = False
    with tempfile.TemporaryDirectory(prefix="product-form-") as scratch, stdout.open("w") as log:
        process = subprocess.Popen(["form", "-q", str(source.resolve())], cwd=scratch,
                                   stdout=log, stderr=subprocess.STDOUT)
        while process.poll() is None:
            if time.perf_counter() - start >= timeout:
                timed_out = True
                process.kill()
                process.wait()
                break
            try:
                status = Path(f"/proc/{process.pid}/status").read_text()
                found = re.search(r"VmRSS:\s+(\d+)", status)
                if found:
                    peak_rss = max(peak_rss, int(found[1]))
                peak_scratch = max(peak_scratch, sum(p.stat().st_size for p in Path(scratch).iterdir() if p.is_file()))
            except (FileNotFoundError, ProcessLookupError):
                pass
            time.sleep(0.02)
    elapsed = time.perf_counter() - start
    final_usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    text = stdout.read_text()
    modules = form_statistics(text)
    record = {
        "case": case, "order": order, "variant": variant, "input": data,
        "formal_characters": len(specs), "vector_characters": len(vectors),
        "matter_monomials": len(matter), "max_matter_support": max(map(len, matter), default=0),
        "raw_program_sha256": digest,
        "form_seconds": elapsed, "form_cpu_seconds": final_usage.ru_utime + final_usage.ru_stime - usage.ru_utime - usage.ru_stime,
        "peak_rss_kib": peak_rss, "peak_scratch_bytes": peak_scratch,
        "stdout_bytes": stdout.stat().st_size, "timed_out": timed_out,
        "returncode": process.returncode, "last_output_terms": modules[-1]["output_terms"] if modules else None,
        "modules": modules,
    }
    if not timed_out and process.returncode == 0 and print_result:
        expression = text.split("result =", 1)[1].split(";", 1)[0]
        record["formal_result_sha256"] = hashlib.sha256("".join(expression.split()).encode()).hexdigest()
        start = time.perf_counter()
        terms = FormExpansionCache.parse_form_output("result =" + expression + ";")
        record["parse_seconds"] = time.perf_counter() - start
        record["expansion_terms"] = len(terms)
        record["structures"] = len({term.characters for term in terms})
        if project:
            cache_path = output / "private_characters.sqlite"
            if not cache_path.exists():
                with closing(sqlite3.connect((ROOT / "char_decomposition_cache.db").as_uri() + "?mode=ro", uri=True)) as src, closing(sqlite3.connect(cache_path)) as dst:
                    src.backup(dst)
            print(json.dumps({"case": case, "order": order, "stage": "projection", "terms": len(terms)}), flush=True)
            start = time.perf_counter()
            with CharacterDecompositionCache(database_path=cache_path, max_workers=1, timeout=60) as cache:
                projected = idx._project_terms(terms, factors, specs, cache)
            record["projection_seconds"] = time.perf_counter() - start
            record["index_terms"] = len(projected)
            record["coefficients"] = [[list(powers), str(value)] for powers, value in sorted(projected.items())]
    (folder / "result.json").write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps({k: v for k, v in record.items() if k not in ("input", "coefficients", "modules")}), flush=True)
    return record


def verify_results(output):
    """Check complete formal outputs and exact disconnected-sector factorization."""
    profiles = {}
    for path in output.glob("*/result.json"):
        record = json.loads(path.read_text())
        if not record.get("formal_result_sha256") and record.get("expansion_terms"):
            raw = (path.parent / "form.out").read_text()
            expression = raw.split("result =", 1)[1].split(";", 1)[0]
            record["formal_result_sha256"] = hashlib.sha256("".join(expression.split()).encode()).hexdigest()
        profiles[record["case"], record["order"], record["variant"]] = record
    comparisons = []
    for (case, order, variant), record in profiles.items():
        baseline = profiles.get((case, order, "baseline"), {})
        if variant != "baseline" and baseline.get("formal_result_sha256") and record.get("formal_result_sha256"):
            assert baseline["formal_result_sha256"] == record["formal_result_sha256"], (case, order, variant)
            comparisons.append({"case": case, "order": order, "variant": variant,
                                "exact_formal_polynomial_equality": True,
                                "baseline_seconds": baseline["form_seconds"],
                                "variant_seconds": record["form_seconds"]})
    products = []
    for order in (12, 18):
        sector = profiles.get(("su3_sym_fund", order, "baseline"), {})
        if "coefficients" not in sector:
            continue
        terms = {tuple(k): Fraction(v) for k, v in sector["coefficients"]}
        product = {}
        start = time.perf_counter()
        for a, c in terms.items():
            for b, d in terms.items():
                if a[0] + b[0] <= order:
                    key = tuple(x + y for x, y in zip(a, b))
                    product[key] = product.get(key, Fraction()) + c * d
        seconds = time.perf_counter() - start
        coefficients = [[list(k), str(v)] for k, v in sorted(product.items()) if v]
        checked = []
        for variant in ("baseline", "degree_bounded"):
            combined = profiles.get(("9780", order, variant), {})
            if "coefficients" in combined:
                assert coefficients == combined["coefficients"], (order, variant)
                checked.append(variant)
        products.append({"order": order, "multiplication_seconds": seconds,
                         "final_terms": len(coefficients), "matched_variants": checked})
    result = {"formal_polynomial_comparisons": comparisons, "factorization_comparisons": products}
    (output / "verification.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "output/benchmarks/product_form_20260914")
    parser.add_argument("--cases", nargs="+", default=["bifundamental", "a1_g2", "9780", "12526"])
    parser.add_argument("--orders", nargs="+", type=int, default=[12, 16, 18])
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--no-print", action="store_true")
    parser.add_argument("--project", action="store_true")
    parser.add_argument("--variant", default="baseline", help="output label, used to retain repeat runs")
    parser.add_argument("--input-cases", type=Path, help="JSON array of {theory_id, input} records")
    parser.add_argument("--verify", action="store_true", help="check existing profile results and exit")
    args = parser.parse_args()
    if args.verify:
        verify_results(args.output)
        return
    if args.timeout <= 0 or any(order < 4 for order in args.orders):
        parser.error("timeout must be positive and profiling orders must be at least four")
    inputs = args.input_cases or args.output / "timeout_cases.json"
    cases = ({str(row["theory_id"]): row["input"] for row in json.loads(inputs.read_text())}
             if inputs.exists() else {})
    cases["bifundamental"] = json.loads((ROOT / "anomalies/example_product_bifundamental.json").read_text())
    cases["a1_g2"] = json.loads((ROOT / "anomalies/example_a1_g2_product.json").read_text())
    cases["su3_sym_fund"] = {"algebra": "A2", "hypermultiplets": [
        {"representation": "fundamental", "kind": "full", "number": 1},
        {"representation": "symmetric", "kind": "full", "number": 1},
    ]}
    if any(case not in cases for case in args.cases):
        parser.error("unknown case; supply --input-cases or use the built-in bifundamental, a1_g2 or su3_sym_fund")
    for case in args.cases:
        for order in args.orders:
            profile(case, cases[case], order, args.output, args.timeout,
                    print_result=not args.no_print, project=args.project, variant=args.variant)


if __name__ == "__main__":
    main()
