"""Explicit, single-image CLI; not an MCP tool or an autonomous chat tool."""
from __future__ import annotations

import argparse
import base64
from decimal import Decimal, InvalidOperation
from datetime import date
import getpass
import hashlib
import http.client
import json
import os
from pathlib import Path
import stat
import struct
import sys
import zlib
from .chat import _hidden_input
from .api import APIBridgeError
from .policy import SECRET_PATTERN

ENDPOINT = 'https://api.openai.com/v1/images/generations'
MODEL = 'gpt-image-1-mini'
PREVIEW_MODELS = ('gpt-image-2.5-flare', 'gpt-image-2.5-sunburst')
MODELS = PREVIEW_MODELS + (MODEL,)
LATEST_BLOCK = 'GPT Image 2.5 live disabled: no verified local output-token estimate matrix for each size/quality yet.'
SIZES = ('1024x1024', '1024x1536', '1536x1024')
PRICES = {'low': ('0.005', '0.006', '0.006'),
          'medium': ('0.011', '0.015', '0.015'),
          'high': ('0.036', '0.052', '0.052')}
PRICE_REVIEW_DATE = date(2026, 10, 29)
PRICE_VALID_UNTIL = date(2026, 11, 28)
MINI_RETIREMENT_DATE = date(2026, 12, 1)
MAX_RESPONSE = 32 * 1024 * 1024
WARNING = ('Per-attempt local estimate only; NOT cumulative and NOT a provider hard spending cap. Actual cost may differ. '
           'Free API credits may run out and usage then consumes purchased credits. '
           'This command cannot verify your balance. No retries; uncertain attempts remain UNKNOWN. '
           'Duplicate protection is scoped only to the chosen output directory; different directories do not share a ledger.')
PNG_WARNING = ('PNG metadata and provenance are preserved byte-for-byte, not stripped or displayed. '
               'Do not assume metadata-free output; embedded metadata is not interpreted or verified. '
               'Pixel validation supports 8-bit RGB/RGBA without interlacing; unsupported or invalid output may be billed and remain UNKNOWN.')


class ImageError(Exception):
    pass


def prepare(prompt, model=MODEL, size='1024x1024', quality='low', budget='0.02'):
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt.encode('utf-8')) > 16000:
        raise ImageError('Prompt must contain 1–16000 UTF-8 bytes.')
    if SECRET_PATTERN.search(prompt):
        raise ImageError('Recognizable credential material blocked; nothing sent.')
    if model not in MODELS or size not in SIZES or quality not in ('low', 'medium', 'high', 'xhigh', 'max') or (model == MODEL and quality not in PRICES):
        raise ImageError('Model, size, or quality is not allowlisted.')
    try:
        cap = Decimal(budget)
    except (InvalidOperation, TypeError, ValueError):
        raise ImageError('Budget must be a positive finite USD amount, at most 10.') from None
    if type(budget) is bool or not cap.is_finite() or not 0 < cap <= 10:
        raise ImageError('Budget must be a positive finite USD amount, at most 10.')
    payload = dict(model=model, prompt=prompt, size=size, quality=quality, n=1, output_format='png')
    body = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(',', ':')).encode()
    # Deliberately rough text-token allowance: one token per UTF-8 byte.
    estimate = None if model in PREVIEW_MODELS else Decimal(PRICES[quality][SIZES.index(size)]) + Decimal(len(prompt.encode('utf-8'))) * Decimal('0.000002')
    if estimate is not None and estimate > cap:
        raise ImageError('Local estimate exceeds the supplied per-attempt budget; nothing sent.')
    return body, estimate, cap


def send(body, key):
    """One stdlib HTTPS request, fixed host/path, no env proxy, redirect or retry."""
    conn = http.client.HTTPSConnection('api.openai.com', timeout=180)
    try:
        conn.request('POST', '/v1/images/generations', body=body,
                     headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'})
        response = conn.getresponse()
        data = response.read(MAX_RESPONSE + 1)
        if response.status != 200 or len(data) > MAX_RESPONSE:
            raise ImageError('Provider result unavailable; UNKNOWN. Do not retry automatically.')
        return data
    finally:
        conn.close()


def png_bytes(raw, size):
    """Strict base64, bounded PNG chunk integrity/dimensions (not visual QA)."""
    try:
        result = json.loads(raw)
        if len(result['data']) != 1:
            raise ValueError()
        blob = base64.b64decode(result['data'][0]['b64_json'], validate=True)
        if not blob.startswith(b'\x89PNG\r\n\x1a\n') or len(blob) > 24 * 1024 * 1024:
            raise ValueError()
        offset, first, ended, has_data = 8, True, False, False
        compressed = bytearray()
        expected_raw = stride = 0
        seen = set()
        data_ended = False
        frame_controls_before_data = 0
        before_palette = {b'cHRM', b'cICP', b'gAMA', b'iCCP', b'mDCV', b'cLLI', b'sBIT', b'sRGB'}
        before_data = before_palette | {b'bKGD', b'hIST', b'tRNS', b'eXIf', b'pHYs', b'sPLT', b'acTL'}
        singleton = before_data - {b'sPLT'} | {b'PLTE', b'tIME'}
        while offset < len(blob):
            if len(blob) - offset < 12:
                raise ValueError()
            length = struct.unpack('>I', blob[offset:offset+4])[0]
            kind = blob[offset+4:offset+8]
            # ASCII letters only; the reserved third type bit must be uppercase.
            if length > 0x7fffffff or not all(65 <= c <= 90 or 97 <= c <= 122 for c in kind) or not 65 <= kind[2] <= 90:
                raise ValueError()
            if kind[0] < 97 and kind not in (b'IHDR', b'PLTE', b'IDAT', b'IEND'):
                raise ValueError()  # Unknown critical chunks cannot be decoded.
            # Ancillary bytes (including provenance) are opaque, retained, never executed.
            if kind in singleton and kind in seen:
                raise ValueError()
            if kind in before_data and has_data:
                raise ValueError()
            if kind in before_palette and b'PLTE' in seen:
                raise ValueError()
            if kind == b'PLTE' and (has_data or seen & {b'bKGD', b'hIST', b'tRNS'}):
                raise ValueError()
            if kind == b'hIST' and b'PLTE' not in seen:
                raise ValueError()
            if kind == b'fdAT' and not has_data:
                raise ValueError()
            if kind == b'fcTL' and not has_data:
                frame_controls_before_data += 1
                if frame_controls_before_data > 1:
                    raise ValueError()
            if kind == b'IDAT' and data_ended:
                raise ValueError()
            if has_data and kind != b'IDAT':
                data_ended = True
            chunk = blob[offset+8:offset+8+length]
            end = offset + length + 12
            if end > len(blob) or zlib.crc32(kind + chunk) != struct.unpack('>I', blob[end-4:end])[0]:
                raise ValueError()
            if first:
                if kind != b'IHDR' or length != 13 or struct.unpack('>II', chunk[:8]) != tuple(map(int, size.split('x'))):
                    raise ValueError()
                width, height, depth, color, compression, filtering, interlace = struct.unpack('>IIBBBBB', chunk)
                if depth != 8 or color not in (2, 6) or compression or filtering or interlace:
                    raise ValueError()
                stride = 1 + width * (3 if color == 2 else 4)
                expected_raw = stride * height
                first = False
            elif kind == b'IHDR':
                raise ValueError()
            if kind == b'PLTE' and (not 0 < length <= 768 or length % 3):
                raise ValueError()
            seen.add(kind)
            has_data |= kind == b'IDAT'
            if kind == b'IDAT':
                compressed.extend(chunk)
            if kind == b'IEND':
                if not has_data or length != 0 or end != len(blob):
                    raise ValueError()
                ended = True
            offset = end
        if not ended or not has_data:
            raise ValueError()
        decoder = zlib.decompressobj()
        pixels = decoder.decompress(compressed, expected_raw + 1)
        if len(pixels) != expected_raw or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
            raise ValueError()
        if any(pixels[i] > 4 for i in range(0, len(pixels), stride)):
            raise ValueError()
        return blob
    except Exception:
        raise ImageError('Invalid image response; UNKNOWN. No image saved; do not auto-retry.') from None


def private_directory(root):
    if os.name != 'posix':
        raise ImageError('Live image saving currently requires POSIX private file permissions.')
    root = Path(root).expanduser().absolute()
    # Reject symlinks in every existing ancestor, including the destination.
    for part in (root, *root.parents):
        if part.is_symlink():
            raise ImageError('Output path must not contain symlinks.')
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = root.stat()
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
        raise ImageError('Output directory must be owned by you and mode 0700.')
    return root


def exclusive(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    directory = os.open(Path(path).parent, os.O_RDONLY | os.O_DIRECTORY | getattr(os, 'O_NOFOLLOW', 0))
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def execute(body, estimate, cap, key, root, transport=send):
    """Caller must obtain exact confirmation first. Write reservation before dispatch.

    File/containing-directory fsync supports ordinary restart protection, not a
    power-loss guarantee for the entire newly-created ancestor hierarchy."""
    if json.loads(body).get('model') == MODEL and date.today() >= MINI_RETIREMENT_DATE:
        raise ImageError('Deprecated Mini model is retired; live disabled independently of pricing review.')
    if not PRICE_REVIEW_DATE <= date.today() <= PRICE_VALID_UNTIL:
        raise ImageError('Image pricing review expired; live disabled until reviewed.')
    if not key or key != key.strip() or any(ord(c) < 33 or ord(c) > 126 for c in key):
        raise ImageError('Invalid key; nothing sent.')
    payload = json.loads(body)
    if key in payload['prompt']:
        raise ImageError('Active credential detected in prompt; nothing sent.')
    canonical, expected_estimate, expected_cap = prepare(payload['prompt'], payload['model'], payload['size'], payload['quality'], str(cap))
    if payload['model'] in PREVIEW_MODELS:
        raise ImageError(LATEST_BLOCK)
    if body != canonical or estimate != expected_estimate or cap != expected_cap:
        raise ImageError('Request changed; nothing sent.')
    root = private_directory(root)
    fingerprint = hashlib.sha256(body).hexdigest()
    pending = root / (fingerprint + '.attempt.json')
    target = root / (fingerprint + '.png')
    if target.exists() or target.is_symlink():
        raise ImageError('Output already exists; refusing duplicate dispatch and overwrite.')
    record = dict(state='UNKNOWN', fingerprint=fingerprint, estimate_usd=str(estimate),
                  budget_usd=str(cap), endpoint=ENDPOINT, warning='May have incurred cost; no automatic retry.')
    try:
        exclusive(pending, json.dumps(record).encode())
    except FileExistsError:
        raise ImageError('An identical request was already attempted; refusing duplicate submission.') from None
    except OSError:
        raise ImageError('Could not persist local attempt reservation; nothing sent.') from None
    try:
        raw = transport(body, key)
        blob = png_bytes(raw, json.loads(body)['size'])
        exclusive(target, blob)
        exclusive(root / (fingerprint + '.success.json'), b'{"state":"SAVED"}')
        return target
    except BaseException:
        # Never expose provider bodies, headers, keys, prompt or exception text.
        raise ImageError('Attempt UNKNOWN; reservation retained. Check billing/output before any manual new attempt.') from None


def main(argv=None):
    parser = argparse.ArgumentParser(description='Independent text-to-image entry. Default: offline preview only.')
    parser.add_argument('--model', choices=MODELS, default=PREVIEW_MODELS[0])
    parser.add_argument('--size', choices=SIZES, default=SIZES[0])
    parser.add_argument('--quality', choices=('low', 'medium', 'high', 'xhigh', 'max'), default='low')
    parser.add_argument('--budget-usd', required=True, help='Per-attempt LOCAL estimate threshold, NOT a provider cap')
    parser.add_argument('--live', action='store_true', help='Allow one request after exact interactive confirmation')
    parser.add_argument('--output-dir', default=str(Path.home() / '.local/state/mcp-agent-bus-light/images'))
    args = parser.parse_args(argv)
    try:
        if args.live and not sys.stdin.isatty():
            raise ImageError('Live mode requires an interactive terminal.')
        prompt = input('Image prompt (one line; terminal may retain it): ')
        body, estimate, cap = prepare(prompt, args.model, args.size, args.quality, args.budget_usd)
        print(body.decode())
        print('Destination:', ENDPOINT)
        print('Save directory:', json.dumps(str(Path(args.output_dir).expanduser().absolute())))
        print('Estimate USD:', 'UNAVAILABLE; live disabled' if estimate is None else estimate, '| your local threshold USD:', cap)
        print(WARNING)
        print(PNG_WARNING)
        if args.model == MODEL:
            print('Deprecated limited legacy model; retirement hard stop:', MINI_RETIREMENT_DATE)
        print('Price review:', PRICE_REVIEW_DATE, '| live valid through:', PRICE_VALID_UNTIL)
        if not args.live:
            print('PREVIEW ONLY: no key read, no request, no application files written.')
            return
        if estimate is None:
            raise ImageError(LATEST_BLOCK)
        if args.model == MODEL and date.today() >= MINI_RETIREMENT_DATE:
            raise ImageError('Deprecated Mini model is retired; live disabled independently of pricing review.')
        if not PRICE_REVIEW_DATE <= date.today() <= PRICE_VALID_UNTIL:
            raise ImageError('Image pricing review expired; live disabled until reviewed.')
        fingerprint = hashlib.sha256(body).hexdigest()
        phrase = 'GENERATE ' + fingerprint + ' USD ' + str(cap)
        if _hidden_input('Confirm exactly (hidden; NOT your key): ' + phrase + '\n> ') != phrase:
            raise ImageError('Canceled; nothing sent.')
        # Validate destination before collecting credentials. Existing permissions never changed.
        private_directory(args.output_dir)
        key = _hidden_input('Official OpenAI API key (hidden, process only): ')
        if not key or key != key.strip() or any(ord(c) < 33 or ord(c) > 126 for c in key):
            raise ImageError('Invalid key; nothing sent.')
        target = execute(body, estimate, cap, key, args.output_dir)
        print('Saved:', json.dumps(str(target)))
    except getpass.GetPassWarning:
        parser.exit(2, 'Hidden input unavailable; refusing echo fallback. Nothing sent.\n')
    except (EOFError, KeyboardInterrupt):
        parser.exit(2, 'Canceled. If dispatch began, retain UNKNOWN and inspect before retrying.\n')
    except (ImageError, APIBridgeError) as exc:
        parser.exit(2, str(exc) + '\n')
    except Exception:
        parser.exit(2, 'Local operation failed; no raw error details logged. Inspect attempt records before retrying.\n')


if __name__ == '__main__':
    main()
