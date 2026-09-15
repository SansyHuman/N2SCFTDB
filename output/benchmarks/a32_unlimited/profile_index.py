from pathlib import Path
import json
import sys
import time
from functools import wraps
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from index import n2_theory_index as idx
from index.char_decomposition_cache import CharacterDecompositionCache
OUT = Path('/tmp/sci-a32-unlimited')
log = (OUT / 'events.jsonl').open('w', buffering=1)
start = time.perf_counter()
phase = 'cold'
sequence = 0

def emit(event, **data):
    log.write(json.dumps(dict(event=event, phase=phase, elapsed=time.perf_counter()-start, **data))+'\n')

def measured(name, fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        global sequence
        sequence += 1
        ident = sequence
        metadata = {}
        if name == 'lie':
            metadata['expressions'] = args[1]
        elif name == 'decomposition':
            metadata = dict(algebra=args[1], labels=args[2], powers=args[3])
        elif name == 'partial_tensor':
            metadata = dict(algebra=args[1], left_terms=len(args[2]), right_terms=len(args[3]))
        emit('start', id=ident, name=name, **metadata)
        began = time.perf_counter()
        try:
            value = fn(*args, **kwargs)
        except BaseException as exc:
            emit('error', id=ident, name=name, seconds=time.perf_counter()-began, error=repr(exc))
            raise
        emit('end', id=ident, name=name, seconds=time.perf_counter()-began,
             result_terms=len(value) if isinstance(value, (dict,list,tuple)) else None)
        return value
    return wrapper

class ProfileCache(CharacterDecompositionCache):
    _execute_lie = measured('lie', CharacterDecompositionCache._execute_lie)
    _calculate_decomposition = measured('decomposition', CharacterDecompositionCache._calculate_decomposition)
    _tensor_decompositions = measured('partial_tensor', CharacterDecompositionCache._tensor_decompositions)
    _character_singlets = measured('singlets', CharacterDecompositionCache._character_singlets)
    get_decompositions = measured('decomposition_batch', CharacterDecompositionCache.get_decompositions)
idx.CharacterDecompositionCache = ProfileCache
for name in ['check_input_data', '_parse_input', '_character_basis', '_build_form_program',
             'run_form', '_parse_form_output', '_project_terms', '_to_sage_polynomial']:
    if hasattr(idx,name):
        setattr(idx, name, measured(name, getattr(idx,name)))
data = {'algebra':'A32', 'hypermultiplets':[
    {'representation':'fundamental','number':66,'kind':'full'}]}
results = []
reference = None
for phase in ['cold','warm']:
    emit('calculation_start', order=18, workers=1, timeout=None, input=data)
    began = time.perf_counter()
    result = idx.calculate_index(data, 18, char_cache_database_path=OUT/'cache.db', processes=1, timeout=None)
    seconds = time.perf_counter()-began
    coefficients = sorted((tuple(int(x) for x in powers), str(value)) for powers,value in result.dict().items())
    if reference is not None:
        assert coefficients == reference
    reference = coefficients
    (OUT / f'index_{phase}.txt').write_text(str(result)+'\n')
    results.append(dict(phase=phase, seconds=seconds, terms=len(coefficients), coefficients=coefficients))
    (OUT/'results.json').write_text(json.dumps(results,indent=2)+'\n')
    emit('calculation_end', seconds=seconds, terms=len(coefficients))
    print(json.dumps(dict(phase=phase,seconds=seconds,terms=len(coefficients))),flush=True)
