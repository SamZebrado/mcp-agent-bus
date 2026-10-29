import asyncio
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from agent_bus_acp.config import BridgeError, Profile
from agent_bus_acp.runner import Runner

ROOT=Path(__file__).resolve().parents[1]
FIXTURE=ROOT/'examples'/'fake_acp_agent.py'


class RunnerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)

    def runner(self,scenario='happy',**changes):
        data={'name':'fixture','argv':[sys.executable,str(FIXTURE),'--scenario',scenario], 'cwd':str(self.root),'max_calls':20,'timeout_seconds':2}
        data.update(changes)
        return Runner(Profile.from_dict(data),self.root/'state',True)

    def args(self,r):
        return {'profile':'fixture','text':'Synthetic ACP test only.', 'user_approved_agent_run':True,
                'approved_request_sha256':r.preview('fixture','Synthetic ACP test only.')['request_sha256']}

    async def test_no_approval_no_sdk_no_process_no_state(self):
        r=self.runner();args=self.args(r);args['user_approved_agent_run']=False
        with patch('agent_bus_acp.runner.load_sdk',side_effect=AssertionError('SDK import')):
            with self.assertRaisesRegex(BridgeError,'APPROVAL'):await r.run(**args)
        self.assertFalse((self.root/'state').exists())

    async def test_wrong_hash_and_disabled_no_process(self):
        r=self.runner();args=self.args(r)
        for key in ('0'*64,'中'*64):
            with self.assertRaisesRegex(BridgeError,'FINGERPRINT'):await r.run(**{**args,'approved_request_sha256':key})
        r.enabled=False
        with self.assertRaisesRegex(BridgeError,'DISABLED'):await r.run(**args)
        self.assertFalse((self.root/'state').exists())

    async def test_real_sdk_fake_process_text(self):
        r=self.runner();counts=[]
        async def progress(count):counts.append(count)
        value=await r.run(**self.args(r),progress=progress)
        self.assertEqual(value['text'],'fixture answer');self.assertEqual(value['status'],'completed')
        self.assertEqual(counts,sorted(counts));self.assertTrue(counts)
        self.assertTrue(r.status()['local_child_reaped'])

    async def test_callbacks_denied_even_without_reject_option(self):
        for scenario in ('callbacks','allow-only'):
            r=self.runner(scenario);value=await r.run(**self.args(r))
            self.assertEqual(value['text'],'FIXTURE_CALLBACKS_DENIED')

    async def test_cancel_waits_for_original_result(self):
        r=self.runner('cancel');task=asyncio.create_task(r.run(**self.args(r)))
        for _ in range(100):
            if r.active and r.active['state']=='prompting':break
            await asyncio.sleep(.01)
        requested=await r.cancel(r.status()['run_id']);self.assertFalse(requested['cancel_confirmed'])
        value=await task;self.assertTrue(value['cancel_confirmed']);self.assertEqual(value['status'],'cancelled')
        self.assertIn('late-before-cancel-completion',value['text'])

    async def test_unknown_blocked_after_restart(self):
        r=self.runner('disconnect')
        with self.assertRaises(BridgeError):await r.run(**self.args(r))
        again=self.runner('disconnect',timeout_seconds=3,max_calls=30)
        with self.assertRaisesRegex(BridgeError,'UNKNOWN'):await again.run(**self.args(again))
        self.assertEqual(r.status()['state'],'unknown');self.assertTrue(r.status()['local_child_reaped'])

    async def test_protocol_and_output_errors_unknown(self):
        for scenario in ('wrong-version','wrong-session','malformed-json','long-line'):
            r=self.runner(scenario)
            with self.subTest(scenario=scenario),self.assertRaises(BridgeError):await r.run(**self.args(r))
            self.assertEqual(r.status()['state'],'unknown');self.assertTrue(r.status()['local_child_reaped'])
        r=self.runner(max_output_bytes=1)
        with self.assertRaises(BridgeError):await r.run(**self.args(r))
        self.assertEqual(r.status()['state'],'unknown')

    async def test_timeout_no_cancel_confirmation_remains_unknown(self):
        r=self.runner('hang',timeout_seconds=1)
        with self.assertRaisesRegex(BridgeError,'UNKNOWN|unknown'):await r.run(**self.args(r))
        self.assertEqual(r.status()['state'],'unknown')

    async def test_post_completion_text_not_returned(self):
        r=self.runner('post-completion');value=await r.run(**self.args(r))
        self.assertEqual(value['text'],'fixture answer')
