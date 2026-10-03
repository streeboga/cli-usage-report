import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / 'collect-cli-usage.py'

class UsageTests(unittest.TestCase):
    def run_report(self, root, start='2026-09-30', end='2026-09-30'):
        return subprocess.run([sys.executable, str(SCRIPT), '--from', start, '--to', end,
            '--timezone', 'Europe/Moscow', '--claude-dir', str(root/'claude'),
            '--codex-dir', str(root/'codex'), '--output', str(root/'report.json')], capture_output=True, text=True)

    def write_events(self, path, events):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('bad json\n'+'\n'.join(json.dumps(event) for event in events))

    def test_complete_models_baskets_timezone_and_deduplication(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def claude(identifier, stamp, output):
                return {'timestamp':stamp,'message':{'id':identifier,'model':'claude-test',
                    'content':'synthetic content must never appear in output',
                    'usage':{'input_tokens':3,'output_tokens':output,'cache_read_input_tokens':30,
                        'cache_creation_input_tokens':12,'cache_creation':{'ephemeral_5m_input_tokens':5,'ephemeral_1h_input_tokens':7}}}}
            final = claude('message-a','2026-09-29T21:00:00Z',10)
            self.write_events(root/'claude/old-session.jsonl',[
                claude('before','2026-09-29T20:59:59Z',99),
                claude('message-a','2026-09-29T21:00:00Z',1), final,
                claude('last-second','2026-09-30T20:59:59Z',20),
                claude('after','2026-09-30T21:00:00Z',99)])
            self.write_events(root/'claude/subagents/fork.jsonl',[final])
            def count(stamp, total, inputs, outputs, cached):
                return {'timestamp':stamp,'type':'event_msg','payload':{'type':'token_count','info':{
                    'total_token_usage':{'total_tokens':total},'last_token_usage':{'input_tokens':inputs,
                    'output_tokens':outputs,'cached_input_tokens':cached,'reasoning_output_tokens':outputs-1}}}}
            one = count('2026-09-30T00:00:00Z',1000,600,20,500)
            two = count('2026-09-30T01:00:00Z',1100,50,30,10)
            self.write_events(root/'codex/sessions/2020-01-01.jsonl',[
                {'type':'turn_context','payload':{'model':'gpt-first'}},one,one,
                {'type':'turn_context','payload':{'model':'gpt-second'}},two,
                count('2026-09-30T21:00:00Z',1200,999,999,0)])
            self.write_events(root/'codex/archived_sessions/fork.jsonl',[
                {'type':'turn_context','payload':{'model':'gpt-first'}},one])
            self.write_events(root/'codex/sessions/unattributed.jsonl',[count('2026-09-30T02:00:00Z',50,15,5,3)])
            result = self.run_report(root)
            self.assertEqual(result.returncode,0,result.stderr)
            report = json.loads((root/'report.json').read_text())
            rows = {(row['source'],row['model']):row for row in report['models']}
            self.assertEqual(len(rows),4)
            a = rows['Claude','claude-test']
            self.assertEqual([a[key] for key in ['input','output','cache_read','cache_write_5m','cache_write_1h','responses']],[6,30,60,10,14,2])
            first = rows['Codex','gpt-first']
            self.assertEqual([first[key] for key in ['input','output','cache_read','responses']],[100,20,500,1])
            self.assertEqual(rows['Codex','gpt-second']['input'],40)
            self.assertEqual(rows['Codex','unattributed']['input'],12)
            self.assertEqual(report['period']['days'],1)
            self.assertEqual(report['source_files'],{'claude':2,'codex':3})
            self.assertNotIn('synthetic content',result.stdout)
            self.assertEqual((root/'report.json').stat().st_mode & 0o777,0o600)

    def test_inverted_period_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            result=self.run_report(root,'2026-10-02','2026-09-30')
            self.assertNotEqual(result.returncode,0)
            self.assertFalse((root/'report.json').exists())

if __name__ == '__main__':
    unittest.main()
