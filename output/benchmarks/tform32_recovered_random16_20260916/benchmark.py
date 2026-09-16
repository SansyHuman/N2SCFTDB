import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time

ROOT = Path('/home/subo-lee/PycharmProjects/N2SCFTDB')
sys.path.insert(0, str(ROOT))
from index import n2_theory_index as idx

OUT = ROOT / 'output/benchmarks/tform32_recovered_random16_20260916'
LIMIT = 600
rows = json.loads((OUT / 'inputs.json').read_text())
metadata = {
    'order': 18, 'tform_workers': 32, 'timeout_seconds': LIMIT,
    'tform_version': subprocess.check_output(['/usr/bin/tform', '-v'], text=True).strip(),
    'generator_sha256': hashlib.sha256((ROOT / 'index/n2_theory_index.py').read_bytes()).hexdigest(),
    'source_logs': sorted(p.name for p in (OUT / 'logs').glob('*.log')),
    'selection': json.loads((OUT / 'selection.json').read_text()),
    'hardware': {'logical_cpus': os.cpu_count(), 'cpu_model': next(line.split(':', 1)[1].strip() for line in Path('/proc/cpuinfo').read_text().splitlines() if line.startswith('model name')), 'mem_total_kib': int(next(line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemTotal:')))},
    'timing_scope': 'TFORM startup, expansion and full textual output to local file; excludes Sage preparation, parsing, projection and cache operations',
    'repetitions': 1, 'concurrent_theories': 1,
}
(OUT / 'metadata.json').write_text(json.dumps(metadata, indent=2) + '\n')
results = []
for number, row in enumerate(rows, 1):
    target = OUT / str(row['theory_id'])
    target.mkdir(exist_ok=True)
    factors, hypers = idx._parse_input(row['input'])
    sectors = idx._split_disconnected_sectors(factors, hypers)
    assert len(sectors) == 1, 'Unexpected disconnected case'
    specs, vectors, matter = idx._character_basis(factors, hypers)
    program = idx._build_form_program(18, len(specs), vectors, matter)
    source = target / 'program.frm'
    source.write_text(program)
    stdout_path, stderr_path = target / 'tform.out', target / 'stderr.txt'
    metrics_path = target / 'time.txt'
    record = {'theory_id': row['theory_id'], 'gauge_group': row['gauge_group'],
              'characters': len(specs), 'program_sha256': hashlib.sha256(program.encode()).hexdigest()}
    command = ['/usr/bin/time', '-f', '%e %U %S %M', '-o', str(metrics_path),
               '/usr/bin/tform', '-w32', '-q', str(source)]
    record['command'] = command
    print(json.dumps({'progress': f'{number}/{len(rows)}', 'theory_id': row['theory_id'], 'stage': 'starting'}), flush=True)
    environment = os.environ.copy()
    for key in list(environment):
        if key.startswith('FORM_'):
            environment.pop(key)
    max_threads = max_sample_rss_kib = 0
    timed_out = False
    with tempfile.TemporaryDirectory(prefix='n2-tform32-') as scratch:
        with stdout_path.open('wb') as stdout, stderr_path.open('wb') as stderr:
            started = time.perf_counter()
            proc = subprocess.Popen(command, cwd=scratch, env=environment, stdout=stdout,
                                    stderr=stderr, start_new_session=True)
            while proc.poll() is None:
                if time.perf_counter() - started > LIMIT:
                    timed_out = True
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait()
                    break
                try:
                    children = Path(f'/proc/{proc.pid}/task/{proc.pid}/children').read_text().split()
                    for pid in children:
                        status = Path(f'/proc/{pid}/status').read_text()
                        threads = re.search(r'Threads:\s+(\d+)', status)
                        rss = re.search(r'VmRSS:\s+(\d+)', status)
                        if threads: max_threads = max(max_threads, int(threads[1]))
                        if rss: max_sample_rss_kib = max(max_sample_rss_kib, int(rss[1]))
                except (FileNotFoundError, ProcessLookupError):
                    pass
                time.sleep(0.1)
            elapsed = time.perf_counter() - started
    record.update(returncode=proc.returncode, timed_out=timed_out,
                  monitored_wall_seconds=elapsed, observed_max_threads=max_threads,
                  sampled_peak_rss_kib=max_sample_rss_kib, stdout_bytes=stdout_path.stat().st_size)
    if metrics_path.exists():
        for line in metrics_path.read_text().splitlines():
            values = line.split()
            if len(values) == 4:
                try:
                    wall, user, system, peak = map(float, values)
                except ValueError:
                    continue
                record.update(wall_seconds=wall, user_seconds=user, system_seconds=system,
                              peak_rss_kib=int(peak), peak_rss_mib=peak/1024)
    record['stderr'] = stderr_path.read_text()[:4000]
    has_result = False
    last_nonspace = b''
    digest = hashlib.sha256()
    with stdout_path.open('rb') as raw, gzip.open(target/'tform.out.gz', 'wb', compresslevel=1) as zipped:
        while chunk := raw.read(1024*1024):
            digest.update(chunk)
            zipped.write(chunk)
            if b'result =' in chunk: has_result = True
            if chunk.rstrip(): last_nonspace = chunk.rstrip()[-1:]
    record['stdout_sha256'] = digest.hexdigest()
    record['output_complete'] = has_result and last_nonspace == b';'
    record['success'] = proc.returncode == 0 and not timed_out and not record['stderr'].strip() and record['output_complete']
    stdout_path.unlink()  # Only the raw output created above; compressed copy retained.
    (target/'result.json').write_text(json.dumps(record, indent=2)+'\n')
    results.append(record)
    (OUT/'results.json').write_text(json.dumps(results,indent=2)+'\n')
    print(json.dumps({k: record.get(k) for k in ('theory_id','gauge_group','success','wall_seconds','peak_rss_mib','observed_max_threads','stdout_bytes','timed_out')}), flush=True)
print('COMPLETE', flush=True)
