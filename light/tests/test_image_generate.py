import base64
import hashlib
import io
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch, Mock
import zlib

from mcp_agent_bus_light import image_generate as img


def chunk(kind, value):
    return struct.pack('>I', len(value)) + kind + value + struct.pack('>I', zlib.crc32(kind + value))


def fixture_png(width=1024, height=1024):
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress((b'\x00' + b'\xff\x00\x00' * width) * height)) + chunk(b'IEND', b''))


def response(png=None):
    return json.dumps({'data': [{'b64_json': base64.b64encode(png or fixture_png()).decode()}]}).encode()


class ImageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        # Pin only this synthetic fixture clock; runtime expiry remains real.
        self.clock = patch.object(img, "date")
        self.clock.start().today.return_value = img.PRICE_REVIEW_DATE
        self.addCleanup(self.clock.stop)
        self.body, self.estimate, self.cap = img.prepare('synthetic red square')
        self.network = patch('socket.create_connection', side_effect=AssertionError('NO NETWORK IN TESTS'))
        self.network.start()

    def tearDown(self):
        self.network.stop()
        self.temp.cleanup()

    def execute(self, transport):
        return img.execute(self.body, self.estimate, self.cap, 'fake-secret-only', self.root, transport)

    def test_credentials_in_prompt_blocked(self):
        with self.assertRaises(img.ImageError):
            img.prepare('oops sk-abcdefghijklmnopqrstuv123456')
        body, estimate, cap = img.prepare('my fake-secret-only value')
        sender = Mock()
        with self.assertRaises(img.ImageError):
            img.execute(body, estimate, cap, 'fake-secret-only', self.root, sender)
        sender.assert_not_called()

    def test_stale_pricing_no_dispatch_or_record(self):
        with patch.object(img, 'date') as clock:
            clock.today.return_value = img.PRICE_VALID_UNTIL.replace(year=2027)
            sender = Mock()
            with self.assertRaises(img.ImageError):
                self.execute(sender)
        sender.assert_not_called()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_non_posix_live_path_fails_closed(self):
        with patch.object(img.os, 'name', 'nt'):
            with self.assertRaisesRegex(img.ImageError, 'POSIX'):
                img.private_directory('unused')

    def test_png_unknown_critical_and_invalid_chunk_types_rejected(self):
        blob = fixture_png()
        for kind in (b'ABCD', b'1234', b'IDaT', b'!DAT', b'ID\x80T'):
            for offset in (8, 33, len(blob) - 12):
                with self.subTest(kind=kind, offset=offset), self.assertRaises(img.ImageError):
                    img.png_bytes(response(blob[:offset] + chunk(kind, b'private metadata') + blob[offset:]), '1024x1024')

    def test_png_ancillary_metadata_and_provenance_preserved_exactly(self):
        blob = fixture_png()
        ancillary = (chunk(b'tEXt', b'Description\0private metadata') +
                     chunk(b'eXIf', b'II*\0opaque-exif') +
                     chunk(b'caBX', b'opaque-c2pa-provenance') +
                     chunk(b'sRGB', b'\0') + chunk(b'gAMA', struct.pack('>I', 45455)) +
                     chunk(b'vpAg', b'unknown-private-ancillary'))
        candidate = blob[:33] + ancillary + blob[33:-12] + chunk(b'tEXt', b'Comment\0kept after IDAT') + blob[-12:]
        self.assertEqual(img.png_bytes(response(candidate), '1024x1024'), candidate)

    def test_png_ancillary_order_and_singletons(self):
        blob = fixture_png()
        head, data, end = blob[:33], blob[33:-12], blob[-12:]
        palette = chunk(b'PLTE', b'\xff\0\0')
        cases = [head + data + chunk(b'gAMA', b'\0' * 4) + end,
                 head + palette + chunk(b'sRGB', b'\0') + data + end,
                 head + chunk(b'tRNS', b'\0' * 6) + palette + data + end,
                 head + palette + palette + data + end,
                 head + chunk(b'hIST', b'\0\1') + data + end,
                 head + chunk(b'pHYs', b'\0' * 9) * 2 + data + end,
                 head + data + chunk(b'eXIf', b'opaque') + end,
                 head + chunk(b'fdAT', b'opaque') + data + end,
                 head + data + chunk(b'tEXt', b'k\0v') + chunk(b'IDAT', b'') + end]
        for candidate in cases:
            with self.subTest(length=len(candidate)), self.assertRaises(img.ImageError):
                img.png_bytes(response(candidate), '1024x1024')
        candidate = head + palette + data + end
        self.assertEqual(img.png_bytes(response(candidate), '1024x1024'), candidate)

    def test_png_strict_chunk_order(self):
        blob = fixture_png()
        signature, header, data, end = blob[:8], blob[8:33], blob[33:-12], blob[-12:]
        invalid = [signature + data + header + end,
                   signature + header + header + data + end,
                   signature + header + end,
                   signature + header + end + data,
                   blob + end,
                   signature + header + data + chunk(b'IEND', b'x'),
                   signature + header + data + b'junk',
                   signature + header + data + struct.pack('>I', 0x80000000) + b'IDAT' + b'\0' * 4]
        for candidate in invalid:
            with self.subTest(length=len(candidate)), self.assertRaises(img.ImageError):
                img.png_bytes(response(candidate), '1024x1024')

    def test_png_consecutive_split_idat_accepted_unchanged(self):
        blob = fixture_png()
        compressed = blob[41:-16]
        split = len(compressed) // 2
        candidate = blob[:33] + chunk(b'IDAT', compressed[:split]) + chunk(b'IDAT', compressed[split:]) + blob[-12:]
        self.assertEqual(img.png_bytes(response(candidate), '1024x1024'), candidate)

    def test_mini_retirement_independent_of_price_refresh(self):
        sender = Mock()
        for day in (img.MINI_RETIREMENT_DATE, img.MINI_RETIREMENT_DATE.replace(year=2027)):
            with self.subTest(day=day), patch.object(img, 'date') as clock, patch.object(img, 'PRICE_VALID_UNTIL', img.MINI_RETIREMENT_DATE.replace(year=2028)):
                clock.today.return_value = day
                with self.assertRaisesRegex(img.ImageError, 'retired'):
                    self.execute(sender)
        sender.assert_not_called()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_cli_retirement_blocks_before_confirmation_or_key(self):
        with patch.object(img, 'date') as clock, patch.object(img, 'PRICE_VALID_UNTIL', img.MINI_RETIREMENT_DATE.replace(year=2028)), patch('sys.stdin.isatty', return_value=True), patch('builtins.input', return_value='test'), patch.object(img, '_hidden_input') as hidden, patch.object(img, 'execute') as execute, patch('sys.stdout', new_callable=io.StringIO), patch('sys.stderr', new_callable=io.StringIO) as err:
            clock.today.return_value = img.MINI_RETIREMENT_DATE
            with self.assertRaises(SystemExit):
                img.main(['--budget-usd','0.02','--live','--model',img.MODEL])
        self.assertIn('retired', err.getvalue())
        hidden.assert_not_called()
        execute.assert_not_called()

    @unittest.skipUnless(os.name == 'posix', 'Private files require POSIX')
    def test_budget_is_per_attempt_not_cumulative(self):
        sender = Mock(return_value=response())
        total = 0
        for prompt in ('first', 'second'):
            body, estimate, cap = img.prepare(prompt, budget='0.006')
            total += estimate
            img.execute(body, estimate, cap, 'fake-key', self.root, sender)
        self.assertGreater(total, cap)
        self.assertEqual(sender.call_count, 2)

    @unittest.skipUnless(os.name == 'posix', 'Private files require POSIX')
    def test_duplicate_ledger_is_per_directory_not_global(self):
        sender = Mock(return_value=response())
        for name in ('one', 'two'):
            img.execute(self.body, self.estimate, self.cap, 'fake-key', self.root / name, sender)
        self.assertEqual(sender.call_count, 2)

    def test_payload_is_fixed(self):
        payload = json.loads(self.body)
        self.assertEqual(payload['n'], 1)
        self.assertEqual(payload['output_format'], 'png')
        self.assertEqual(set(payload), {'model', 'prompt', 'n', 'size', 'quality', 'output_format'})

    def test_reject_unknown_inputs(self):
        for kwargs in ({'model':'gpt-image-1'}, {'size':'auto'}, {'quality':'auto'}, {'quality':'max'}, {'budget':'0.0001'}, {'budget':'NaN'}, {'budget':'Infinity'}, {'budget':'0'}, {'budget':'-1'}):
            with self.subTest(kwargs=kwargs), self.assertRaises(img.ImageError):
                img.prepare('test', **kwargs)
        for prompt in ('', ' ', 'a' * 16001):
            with self.assertRaises(img.ImageError):
                img.prepare(prompt)

    def test_estimates_include_utf8_prompt(self):
        _, amount, _ = img.prepare('鸟', size='1024x1536', quality='medium')
        self.assertEqual(str(amount), '0.015006')

    def test_latest_preview_no_guess_and_no_live(self):
        for model in img.PREVIEW_MODELS:
            body, estimate, cap = img.prepare('test', model=model, quality='max')
            self.assertIsNone(estimate)
            sender = Mock()
            with self.assertRaisesRegex(img.ImageError, 'live disabled'):
                img.execute(body, estimate, cap, 'fake', self.root, sender)
            sender.assert_not_called()
        self.assertEqual(list(self.root.iterdir()), [])

    @unittest.skipUnless(os.name == 'posix', 'Live private file saving requires POSIX; Windows is preview-only')
    def test_fake_success_private_and_no_secret_or_prompt_in_metadata(self):
        sender = Mock(return_value=response())
        target = self.execute(sender)
        self.assertEqual(target.read_bytes(), fixture_png())
        sender.assert_called_once_with(self.body, 'fake-secret-only')
        for p in self.root.iterdir():
            self.assertEqual(p.stat().st_mode & 0o777, 0o600)
            if p.suffix == '.json':
                self.assertNotIn(b'fake-secret', p.read_bytes())
                self.assertNotIn(b'synthetic red square', p.read_bytes())
        self.assertTrue(list(self.root.glob('*.success.json')))

    @unittest.skipUnless(os.name == 'posix', 'Live private file saving requires POSIX; Windows is preview-only')
    def test_duplicate_success_never_resubmits(self):
        sender = Mock(return_value=response())
        self.execute(sender)
        with self.assertRaisesRegex(img.ImageError, 'duplicate'):
            self.execute(sender)
        self.assertEqual(sender.call_count, 1)

    @unittest.skipUnless(os.name == 'posix', 'Live private file saving requires POSIX; Windows is preview-only')
    def test_timeout_unknown_retained_no_retry_or_leak(self):
        sender = Mock(side_effect=TimeoutError('fake-secret-only raw provider body'))
        with self.assertRaises(img.ImageError) as caught:
            self.execute(sender)
        self.assertNotIn('fake-secret', str(caught.exception))
        record = json.loads(next(self.root.glob('*.attempt.json')).read_text())
        self.assertEqual(record['state'], 'UNKNOWN')
        with self.assertRaises(img.ImageError):
            self.execute(sender)
        self.assertEqual(sender.call_count, 1)

    @unittest.skipUnless(os.name == 'posix', 'Live private file saving requires POSIX; Windows is preview-only')
    def test_keyboard_interrupt_retains_unknown(self):
        with self.assertRaises(img.ImageError):
            self.execute(Mock(side_effect=KeyboardInterrupt()))
        self.assertEqual(len(list(self.root.glob('*.attempt.json'))), 1)

    def test_bad_response_never_saves_image(self):
        bad = [b'{}', b'not json', b'{"data":[{"b64_json":"@@@"}]}', response(b'not png'), response(fixture_png(1, 1)), response(fixture_png()[:-2]), b'{"data":[{"url":"https://evil.invalid/file"}]}', b'{"data":[]}']
        for raw in bad:
            with self.subTest(raw=raw[:40]), self.assertRaises(img.ImageError):
                img.png_bytes(raw, '1024x1024')

    def test_crc_valid_but_bad_deflate_rejected(self):
        blob = fixture_png()
        offset = 33
        length = struct.unpack('>I', blob[offset:offset+4])[0]
        value = b'x' * length
        broken = blob[:offset+8] + value + struct.pack('>I', zlib.crc32(b'IDAT'+value)) + blob[offset+12+length:]
        with self.assertRaises(img.ImageError):
            img.png_bytes(response(broken), '1024x1024')

    @unittest.skipUnless(os.name == 'posix', 'Live private file saving requires POSIX; Windows is preview-only')
    def test_existing_image_never_overwritten(self):
        target = self.root / (hashlib.sha256(self.body).hexdigest() + '.png')
        target.write_bytes(b'precious')
        sender = Mock(return_value=response())
        with self.assertRaises(img.ImageError):
            self.execute(sender)
        sender.assert_not_called()
        self.assertEqual(target.read_bytes(), b'precious')

    @unittest.skipUnless(os.name == 'posix', 'Live private file saving requires POSIX; Windows is preview-only')
    def test_unsafe_directory_and_symlink_rejected_before_dispatch(self):
        sender = Mock()
        self.root.chmod(0o755)
        with self.assertRaises(img.ImageError):
            self.execute(sender)
        self.root.chmod(0o700)
        link = self.root / 'link'
        link.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(img.ImageError):
            img.execute(self.body, self.estimate, self.cap, 'fake', link, sender)
        sender.assert_not_called()

    def test_mutated_payload_rejected(self):
        payload = json.loads(self.body)
        payload['n'] = 2
        with self.assertRaises(img.ImageError):
            img.execute(json.dumps(payload).encode(), self.estimate, self.cap, 'fake', self.root, Mock())

    def test_transport_fixed_no_redirect_and_single_request(self):
        conn = Mock()
        conn.getresponse.return_value.status = 302
        conn.getresponse.return_value.read.return_value = b'secret response'
        with patch.object(img.http.client, 'HTTPSConnection', return_value=conn) as factory:
            with self.assertRaises(img.ImageError):
                img.send(self.body, 'fake-key')
        factory.assert_called_once_with('api.openai.com', timeout=180)
        self.assertEqual(conn.request.call_count, 1)
        self.assertEqual(conn.request.call_args.args[:2], ('POST', '/v1/images/generations'))
        conn.close.assert_called_once()

    def test_preview_no_credentials_network_or_files(self):
        with patch('builtins.input', return_value='test'), patch.object(img, '_hidden_input') as hidden, patch.object(img, 'execute') as execute, patch('sys.stdout', new_callable=io.StringIO) as out:
            img.main(['--budget-usd', '0.02', '--output-dir', str(self.root / 'absent')])
        hidden.assert_not_called()
        execute.assert_not_called()
        self.assertIn('PREVIEW ONLY', out.getvalue())
        self.assertIn('gpt-image-2.5-flare', out.getvalue())
        self.assertEqual(list(self.root.iterdir()), [])

    def test_live_noninteractive_refused_before_input(self):
        with patch('sys.stdin.isatty', return_value=False), patch('builtins.input') as prompt, patch('sys.stderr', new_callable=io.StringIO):
            with self.assertRaises(SystemExit):
                img.main(['--budget-usd','0.02','--live'])
        prompt.assert_not_called()

    @unittest.skipUnless(os.name == 'posix', 'Live private file saving requires POSIX; Windows is preview-only')
    def test_exact_confirmation_then_key_and_fake_dispatch(self):
        body, estimate, cap = img.prepare('test')
        phrase = 'GENERATE ' + hashlib.sha256(body).hexdigest() + ' USD ' + str(cap)
        with patch('sys.stdin.isatty', return_value=True), patch('builtins.input', return_value='test'), patch.object(img, '_hidden_input', side_effect=[phrase, 'fake-only-key']) as hidden, patch.object(img, 'execute', return_value=self.root / 'fake.png') as execute, patch('sys.stdout', new_callable=io.StringIO) as out:
            img.main(['--budget-usd','0.02','--live','--model',img.MODEL,'--output-dir',str(self.root)])
        self.assertEqual(hidden.call_count, 2)
        execute.assert_called_once_with(body, estimate, cap, 'fake-only-key', str(self.root))
        self.assertNotIn('fake-only-key', out.getvalue())

    def test_hidden_input_failure_no_dispatch(self):
        with patch('sys.stdin.isatty', return_value=True), patch('builtins.input', return_value='test'), patch.object(img, '_hidden_input', side_effect=img.APIBridgeError('Hidden input unavailable; refusing echo fallback.')), patch.object(img, 'execute') as execute, patch('sys.stdout', new_callable=io.StringIO), patch('sys.stderr', new_callable=io.StringIO):
            with self.assertRaises(SystemExit):
                img.main(['--budget-usd','0.02','--live','--model',img.MODEL])
        execute.assert_not_called()

    @unittest.skipUnless(os.name == 'posix', 'Live private file saving requires POSIX; Windows is preview-only')
    def test_reservation_fsync_failure_never_dispatches(self):
        sender = Mock()
        with patch.object(img.os, 'fsync', side_effect=OSError('private detail')):
            with self.assertRaisesRegex(img.ImageError, 'nothing sent'):
                self.execute(sender)
        sender.assert_not_called()

    def test_canceled_hidden_confirmation_never_collects_key(self):
        with patch('sys.stdin.isatty', return_value=True), patch('builtins.input', return_value='test'), patch.object(img, '_hidden_input', return_value='fake-mispasted-key') as hidden, patch.object(img, 'execute') as execute, patch('sys.stdout', new_callable=io.StringIO), patch('sys.stderr', new_callable=io.StringIO) as err:
            with self.assertRaises(SystemExit):
                img.main(['--budget-usd','0.02','--live','--model',img.MODEL])
        self.assertEqual(hidden.call_count, 1)
        self.assertNotIn('fake-mispasted-key', err.getvalue())
        execute.assert_not_called()


if __name__ == '__main__':
    unittest.main()
