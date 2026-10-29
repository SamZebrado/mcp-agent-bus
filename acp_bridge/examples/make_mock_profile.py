"""Print a static synthetic profile; creates no files or processes."""
import argparse
import json
from pathlib import Path
import sys

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--cwd', type=Path, required=True)
p.add_argument('--scenario', choices=('happy','callbacks','cancel','disconnect'), default='happy')
a=p.parse_args()
if not a.cwd.is_absolute():p.error('Use an absolute empty test workspace')
print(json.dumps({'name':'offline-fixture','argv':[sys.executable,str(Path(__file__).resolve().with_name('fake_acp_agent.py')),'--scenario',a.scenario],
                  'cwd':str(a.cwd),'pass_env':[],'timeout_seconds':10,'max_output_bytes':65536,'max_calls':5},indent=2))
