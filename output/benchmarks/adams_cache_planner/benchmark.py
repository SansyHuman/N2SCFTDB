"""Temporary old/new comparison; all cache writes are confined to scratch files."""
from contextlib import closing
from collections import Counter
from pathlib import Path
from statistics import median
from unittest.mock import patch
import argparse
import itertools
import json
import shutil
import sqlite3
import sys
import tempfile
import time
ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
import baseline_cache as old
import candidate_cache as new
from index import n2_theory_index as idx
from index.form_expansion_cache import FormExpansionCache
from test.test_character_decomposition_cache import su2_fundamental_adams_product
OUT=ROOT/'output/benchmarks/adams_cache_planner'


def copy_db(source,target):
    with closing(sqlite3.connect(source)) as src, closing(sqlite3.connect(target)) as dst:
        src.backup(dst)


def run_timed(module,path,fn,workers=1):
    calls=Counter()
    original=module.CharacterDecompositionCache._run_lie
    def execute(self,expressions):
        for expr in expressions:
            calls[expr.split('(',1)[0]]+=1
        return original(self,expressions)
    with patch.object(module.CharacterDecompositionCache,'_run_lie',execute):
        start=time.perf_counter()
        with module.CharacterDecompositionCache(database_path=path,max_workers=workers) as cache:
            value=fn(cache)
        seconds=time.perf_counter()-start
    return value,dict(seconds=seconds,calls=dict(calls))


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--suite',choices=['decomposition','index'],default='decomposition')
    parser.add_argument('--api',choices=['single','batch'],default='single')
    args=parser.parse_args()
    records=[]
    with tempfile.TemporaryDirectory(prefix='sci-cache-planner-') as tmp:
        tmp=Path(tmp)
        if args.suite=='decomposition':
            scenarios=[
                ('A1-fund','A1',(1,),(2,2),[(2,),(0,2)],False),
                ('A2-adjoint','A2',(1,1),(2,2),[(2,),(0,2)],False),
                ('C2-fund','C2',(1,0),(2,2),[(2,),(0,2)],False),
                ('G2-fund','G2',(1,0),(2,2),[(2,),(0,2)],False),
                ('A4-adjoint','A4',(1,0,0,1),(2,2),[(2,),(0,2)],False),
                ('A1-only-composite-rows','A1',(1,),(0,2,2),[(0,2),(0,0,2)],True),
            ]
            for name,algebra,labels,target,seeds,prune in scenarios:
                seed=tmp/(name+'-seed.db')
                with old.CharacterDecompositionCache(database_path=seed,max_workers=1) as cache:
                    for powers in seeds:
                        cache.get_decomposition(algebra,labels,powers)
                if prune:
                    keep=[old._json_key(old._canonical_adams_powers(p)) for p in seeds]
                    with closing(sqlite3.connect(seed)) as c:
                        with c:
                            c.execute('DELETE FROM character_decompositions WHERE adams_powers NOT IN (?,?)',keep)
                expected=None
                measurements={'old':[],'new':[]}
                for trial in range(5):
                    order=[('old',old),('new',new)]
                    if trial%2: order.reverse()
                    for version,module in order:
                        path=tmp/f'{name}-{version}-{trial}.db'
                        copy_db(seed,path)
                        value,timing=run_timed(module,path,lambda cache: (cache.get_decomposition(algebra,labels,target) if args.api=='single' else cache.get_decompositions([(algebra,labels,target)])[0]))
                        if expected is None: expected=value
                        assert value==expected,(name,version)
                        if algebra=='A1': assert value==su2_fundamental_adams_product(target)
                        measurements[version].append(timing)
                entry=dict(case=name,api=args.api,target=target,measurements=measurements,
                           median={k:median(v['seconds'] for v in vs) for k,vs in measurements.items()},equal=True)
                entry['speedup']=entry['median']['old']/entry['median']['new']
                records.append(entry)
                print(json.dumps(entry),flush=True)
            # Broad exact comparison, plus an independent SU(2) weight oracle.
            requests=[]
            for algebra,labels in [('A1',(1,)),('A2',(1,0)),('A2',(1,1)),('C2',(1,0)),('G2',(1,0))]:
                requests += [(algebra,labels,p) for p in itertools.product(range(7),range(4),range(3),range(2))
                             if 0<sum((j+1)*n for j,n in enumerate(p))<=6]
            a,ta=run_timed(old,tmp/'broad-old.db',lambda c:c.get_decompositions(requests))
            b,tb=run_timed(new,tmp/'broad-new.db',lambda c:c.get_decompositions(requests))
            assert a==b
            for req,value in zip(requests,b):
                if req[0]=='A1': assert value==su2_fundamental_adams_product(req[2])
            # Same requests via the existing multi-process dependency scheduler.
            c,tc=run_timed(new,tmp/'broad-parallel.db',lambda c:c.get_decompositions(requests),workers=3)
            assert c==a
            records.append(dict(case='broad-exact',requests=len(requests),old=ta,new=tb,new_parallel=tc,equal=True))
        else:
            prior=json.loads((ROOT/'output/benchmarks/form_expansion_theories/results.json').read_text())
            selected=['su2_4fund','su3_6fund','su5_10fund','sp2_6fund','g2_4fund','su5_sym_asym']
            form_db=tmp/'form.db'
            copy_db(Path(prior['hit_database']),form_db)
            for case in prior['cases']:
                if case['order']!=18 or case['case'] not in selected: continue
                name=case['case']; data=case['input']
                for state in ['cold','from_t12']:
                    seed=tmp/f'{name}-{state}-seed.db'
                    with old.CharacterDecompositionCache(database_path=seed) as cache: cache._connection()
                    if state=='from_t12':
                        with patch.object(idx,'CharacterDecompositionCache',old.CharacterDecompositionCache):
                            idx.calculate_index(data,12,char_cache_database_path=seed,form_cache_database_path=form_db,
                                                processes=1,form_executable='FORM-must-not-run')
                    expected=None; measurements={'old':[],'new':[]}
                    for trial in range(3):
                        order=[('old',old),('new',new)]
                        if trial%2: order.reverse()
                        for version,module in order:
                            path=tmp/f'{name}-{state}-{version}-{trial}.db'; copy_db(seed,path)
                            calls=Counter(); original=module.CharacterDecompositionCache._run_lie
                            def execute(self,expressions):
                                for expression in expressions: calls[expression.split('(',1)[0]]+=1
                                return original(self,expressions)
                            with patch.object(idx,'CharacterDecompositionCache',module.CharacterDecompositionCache),patch.object(module.CharacterDecompositionCache,'_run_lie',execute):
                                start=time.perf_counter()
                                value=idx.calculate_index(data,18,char_cache_database_path=path,form_cache_database_path=form_db,
                                                          processes=1,form_executable='FORM-must-not-run')
                                seconds=time.perf_counter()-start
                            if expected is None: expected=value
                            assert value==expected,(name,state,version)
                            # Cross-check the retained previous, independently computed benchmark.
                            coefficients=[[list(map(int,p)),str(v)] for p,v in sorted(value.dict().items())]
                            assert coefficients==case['coefficients']
                            measurements[version].append(dict(seconds=seconds,calls=dict(calls)))
                    entry=dict(case=name,state=state,measurements=measurements,
                               median={k:median(v['seconds'] for v in vs) for k,vs in measurements.items()},equal=True)
                    entry['speedup']=entry['median']['old']/entry['median']['new']
                    records.append(entry)
                    print(json.dumps(entry),flush=True)
                    (OUT/'index-temporary.json').write_text(json.dumps(records,indent=2)+'\n')
        (OUT/f'{args.suite}-{args.api}-temporary.json').write_text(json.dumps(records,indent=2)+'\n')

if __name__=='__main__': main()
