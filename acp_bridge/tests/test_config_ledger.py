import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from agent_bus_acp.config import BridgeError, Profile, canonical, prepare
from agent_bus_acp.ledger import Ledger


class ConfigLedgerTests(unittest.TestCase):
    def data(self, root):
        return {'name':'fixture','argv':[sys.executable,'fixed.py'],'cwd':str(root)}

    def test_static_profile_rejects_dynamic_and_shell_fields(self):
        with tempfile.TemporaryDirectory() as root:
            for extra in ({'url':'https://example.invalid'}, {'env':{'KEY':'fake'}}, {'argv':['python']}, {'argv':['/test/a.cmd']}, {'pass_env':['HOME']}, {'pass_env':['PYTHONPATH']}, {'max_calls':True}, {'timeout_seconds':0}):
                with self.subTest(extra=extra), self.assertRaises(BridgeError):
                    Profile.from_dict({**self.data(root), **extra})

    def test_approval_binding_and_transport_identity(self):
        with tempfile.TemporaryDirectory() as root:
            a=Profile.from_dict(self.data(root));b=Profile.from_dict({**self.data(root),'timeout_seconds':60,'name':'renamed','max_calls':20})
            pa,ia=prepare(a,'synthetic');pb,ib=prepare(b,'synthetic')
            self.assertNotEqual(pa['request_sha256'],pb['request_sha256']);self.assertEqual(ia,ib)
            self.assertNotEqual(pa['request_sha256'],prepare(a,'different')[0]['request_sha256'])
            self.assertFalse(pa['provider_budget_enforced'])

    def test_preview_limits_credentials_and_invalid_unicode(self):
        with tempfile.TemporaryDirectory() as root:
            p=Profile.from_dict(self.data(root))
            for text in ('', ' ', '\ud800', 'sk-'+'a'*30, 'x'*24001):
                with self.subTest(text=text[:10]), self.assertRaises(BridgeError): prepare(p,text)

    def test_child_environment_is_allowlisted_and_home_replaced(self):
        with tempfile.TemporaryDirectory() as root:
            p=Profile.from_dict({**self.data(root),'pass_env':['TEST_API_KEY']})
            with patch.dict(os.environ,{'HOME':'original-home','AWS_SECRET_ACCESS_KEY':'not-forwarded','TEST_API_KEY':'fixed-fake','OPENAI_API_KEY':'not-forwarded','PYTHONPATH':'not-forwarded'},clear=True):
                env=p.child_env('new-isolated-home')
            self.assertEqual(env['HOME'],'new-isolated-home');self.assertEqual(env['TEST_API_KEY'],'fixed-fake')
            self.assertNotIn('OPENAI_API_KEY',env);self.assertNotIn('AWS_SECRET_ACCESS_KEY',env);self.assertNotIn('PYTHONPATH',env)

    def test_unknown_persists_across_instances_and_cap_change(self):
        with tempfile.TemporaryDirectory() as root:
            state=Path(root)/'state';p=Profile.from_dict(self.data(root));_,identity=prepare(p,'private synthetic text')
            scope=canonical(p.execution_identity());l=Ledger(state);fp=l.reserve(identity,scope,20);l.resolve(fp,False)
            with self.assertRaisesRegex(BridgeError,'UNKNOWN'): Ledger(state).reserve(identity,scope,30)
            raw=(state/'quarantine.sqlite').read_bytes()
            self.assertNotIn(b'private synthetic text',raw);self.assertNotIn(sys.executable.encode(),raw)

    def test_pending_crash_persists_without_resolve(self):
        with tempfile.TemporaryDirectory() as root:
            state=Path(root)/'state';Ledger(state).reserve(b'body',b'scope',20)
            with self.assertRaisesRegex(BridgeError,'UNKNOWN'):Ledger(state).reserve(b'body',b'scope',20)

    def test_known_completion_keeps_attempt_count(self):
        with tempfile.TemporaryDirectory() as root:
            l=Ledger(Path(root)/'state');fp=l.reserve(b'body',b'scope',1);l.resolve(fp,True)
            with self.assertRaisesRegex(BridgeError,'LIMIT'):l.reserve(b'body',b'scope',1)
            l.reserve(b'body',b'scope',2)

    def test_unreaped_child_blocks_all_new_work_across_restart(self):
        with tempfile.TemporaryDirectory() as root:
            state=Path(root)/'state';Ledger(state).block_cleanup()
            with self.assertRaisesRegex(BridgeError,'CLEANUP'):
                Ledger(state).reserve(b'different body',b'different scope',20)

    @unittest.skipIf(os.name=='nt','Windows ACL must be managed by operator')
    def test_private_directory_permissions(self):
        with tempfile.TemporaryDirectory() as root:
            d=Path(root)/'public';d.mkdir(mode=0o755)
            with self.assertRaisesRegex(BridgeError,'PRIVATE'):Ledger(d).reserve(b'body',b'scope',1)
