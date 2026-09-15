"""Verify cross-theory FORM-cache hits using the retained benchmark inputs."""

from contextlib import closing
from datetime import datetime, timezone
import itertools
import json
from pathlib import Path
import runpy
import sqlite3
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from common.form_utils import run_form
from index import n2_theory_index as idx
from index.form_expansion_cache import FormExpansionCache


def program_for(data, order):
    factors, hypers = idx._parse_input(data)
    specs, vectors, matter = idx._character_basis(factors, hypers)
    return idx._build_form_program(order, len(specs), vectors, matter)


def stored_rows(path):
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
        return connection.execute("SELECT program, expansion_json FROM form_expansions").fetchall()


def main():
    destination = Path(__file__).parent
    prior = json.loads((destination / "results.json").read_text())
    warm_only = runpy.run_path(str(destination / "benchmark.py"), run_name="benchmark_helpers")["WarmOnlyCharacterCache"]
    groups = {}
    for case in prior["cases"]:
        program = program_for(case["input"], case["order"])
        assert program == case["program"]
        groups.setdefault(program, []).append(case)
    groups = {program: cases for program, cases in groups.items() if len(cases) > 1}
    records = []
    with tempfile.TemporaryDirectory(prefix="sci-cross-theory-form-") as temporary:
        temporary = Path(temporary)
        character_path = temporary / "character.db"
        with closing(sqlite3.connect(Path(prior["character_database"]).as_uri() + "?mode=ro", uri=True)) as source:
            with closing(sqlite3.connect(character_path)) as target:
                source.backup(target)
        options = dict(char_cache_database_path=character_path, processes=1,
                       lie_executable="LiE-must-not-run", timeout=600)
        with patch.object(idx, "CharacterDecompositionCache", warm_only):
            for program, cases in groups.items():
                for first, second in itertools.permutations(cases, 2):
                    sequence = len(records)
                    shared_path = temporary / f"shared-{sequence}.db"
                    independent_path = temporary / f"independent-{sequence}.db"
                    # A genuinely empty database is populated only by the first theory.
                    with patch("index.form_expansion_cache.run_form", wraps=run_form) as execute:
                        first_index = idx.calculate_index(first["input"], first["order"],
                            form_cache_database_path=shared_path, **options)
                        execute.assert_called_once()
                        assert execute.call_args.args[0] == program
                    rows_before = stored_rows(shared_path)
                    assert len(rows_before) == 1 and rows_before[0][0] == program

                    # The second theory creates a new cache client. Neither execution
                    # nor parsing of FORM is permitted; decoding must happen once.
                    with patch("index.form_expansion_cache.run_form", side_effect=AssertionError("unexpected FORM execution")) as execute, \
                         patch.object(FormExpansionCache, "parse_form_output", side_effect=AssertionError("unexpected FORM parsing")) as parse, \
                         patch.object(FormExpansionCache, "_decode_expansion", wraps=FormExpansionCache._decode_expansion) as decode:
                        second_index = idx.calculate_index(second["input"], second["order"],
                            form_cache_database_path=shared_path,
                            form_executable="FORM-must-not-run", **options)
                        execute.assert_not_called()
                        parse.assert_not_called()
                        decode.assert_called_once()
                    assert stored_rows(shared_path) == rows_before

                    # Compute the second theory independently with a fresh FORM cache.
                    with patch("index.form_expansion_cache.run_form", wraps=run_form) as execute:
                        reference = idx.calculate_index(second["input"], second["order"],
                            form_cache_database_path=independent_path, **options)
                        execute.assert_called_once()
                    assert second_index == reference
                    assert stored_rows(independent_path) == rows_before
                    assert first_index != second_index
                    a, b = first_index.dict(), second_index.dict()
                    different_power = next(power for power in sorted(set(a) | set(b)) if a.get(power, 0) != b.get(power, 0))
                    # A different raw program must attempt FORM rather than reuse a row.
                    other = next(case for case in prior["cases"]
                                 if case["order"] == first["order"] and case["program"] != program)
                    with FormExpansionCache(database_path=shared_path) as cache:
                        with patch("index.form_expansion_cache.run_form", side_effect=RuntimeError("expected cache miss")) as execute:
                            try:
                                cache.get_expansion(other["program"])
                            except RuntimeError as error:
                                assert str(error) == "expected cache miss"
                            else:
                                raise AssertionError("different program incorrectly reused an expansion")
                            execute.assert_called_once()
                    assert stored_rows(shared_path) == rows_before
                    record = dict(
                        source=first["label"], target=second["label"], order=first["order"],
                        source_input=first["input"], target_input=second["input"], program=program,
                        source_form_calls=1, target_form_calls=0, target_parse_calls=0,
                        target_decode_calls=1, rows_after_source=1, rows_after_target=1,
                        independent_target_form_calls=1, target_matches_independent=True,
                        final_indices_differ=True, different_program_missed=True,
                        example_difference=dict(powers=list(map(int, different_power)),
                                                source=str(a.get(different_power, 0)),
                                                target=str(b.get(different_power, 0))),
                    )
                    records.append(record)
                    print(json.dumps({key: record[key] for key in
                                      ("source", "target", "order", "target_form_calls", "rows_after_target", "example_difference")}), flush=True)

    report = dict(completed_utc=datetime.now(timezone.utc).isoformat(),
                  successful_cross_theory_checks=len(records), checks=records)
    (destination / "cross_theory_reuse.json").write_text(json.dumps(report, indent=2) + "\n")
    lines = ["# Cross-theory FORM cache reuse", "",
             "Verified using current generated programs, fresh temporary FORM databases, and a prefilled character-cache copy with writes and LiE disabled.", "",
             "| Theory populating cache | Different theory reusing it | Order | FORM calls on reuse | Rows after reuse | Independent index matches |",
             "|---|---|---:|---:|---:|---|"]
    for record in records:
        lines.append(f"| {record['source']} | {record['target']} | {record['order']} | 0 | 1 | Yes |")
    lines += ["", "For each row, the first theory executed FORM exactly once. The second theory used a new cache client, executed and parsed FORM zero times, and decoded the persisted expansion once. The single stored row was unchanged. An independent FORM calculation of the second theory produced exactly the same index and serialized expansion. The two theories' final indices differed, confirming that their gauge projections remain distinct. A different raw-program control correctly missed the cache.", "",
              "The same complete program text shares one cache entry regardless of gauge group or representation metadata. Reuse requires a common FORM database. Whitespace, numerical matter multiplicities, truncation order, and character naming/order are part of the exact key; no symbolic equivalence check is performed.", "",
              "Run with Sage's Python:", "", "```bash",
              "DOT_SAGE=/tmp/codex-sage-cache PYTHONDONTWRITEBYTECODE=1 /home/subo-lee/miniconda3/envs/sage/bin/sage -python -B output/benchmarks/form_expansion_theories/check_cross_theory_reuse.py",
              "```", "", "Exact inputs, shared programs, and differing coefficient examples are retained in [cross_theory_reuse.json](cross_theory_reuse.json).", ""]
    (destination / "cross_theory_reuse.md").write_text("\n".join(lines))


if __name__ == "__main__":
    main()
