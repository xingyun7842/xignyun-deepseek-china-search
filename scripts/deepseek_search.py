"""DeepSeek Search/Fetch client; standard library only, no key in arguments."""
import argparse
import base64
import ctypes
import getpass
import json
import os
from pathlib import Path
import sys
import time
from datetime import datetime, timezone
from urllib import request, error, parse

KEY_FILE = Path.home() / '.codex' / 'secrets' / 'deepseek-api-key.dpapi'
BASE = 'https://api.deepseek.com/v1/tools/'


def dpapi(data, decrypt=False):
    if os.name != 'nt':
        raise ValueError('DPAPI requires Windows; use DEEPSEEK_API_KEY on other systems.')
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]

    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    target = Blob()
    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    function = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    function.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise ValueError('Windows DPAPI operation failed for the current user.')
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        kernel.LocalFree(target.data)


def valid_key(value):
    value = value.strip()
    if not value or any(c.isspace() for c in value) or not value.isascii():
        raise ValueError('API key must be non-empty ASCII without whitespace.')
    return value


def windows_user_key():
    """Read UI-managed user environment directly, even if this process is older."""
    if os.name != 'nt':
        return ''
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, 'Environment') as handle:
            value, kind = winreg.QueryValueEx(handle, 'DEEPSEEK_API_KEY')
    except FileNotFoundError:
        return ''
    if kind not in (winreg.REG_SZ, winreg.REG_EXPAND_SZ) or not isinstance(value, str):
        raise ValueError('DEEPSEEK_API_KEY user environment value must be text.')
    return value.strip()


def key_source():
    if os.environ.get('DEEPSEEK_API_KEY', '').strip():
        return 'process environment'
    if windows_user_key():
        return 'Windows user environment'
    return 'DPAPI file' if KEY_FILE.is_file() else 'missing'


def load_key():
    if os.environ.get('DEEPSEEK_API_KEY', '').strip():
        return valid_key(os.environ['DEEPSEEK_API_KEY'])
    user_key = windows_user_key()
    if user_key:
        return valid_key(user_key)
    if KEY_FILE.is_file():
        return valid_key(dpapi(base64.b64decode(KEY_FILE.read_bytes(), validate=True), True).decode('utf-8'))
    raise ValueError('No API key configured. Set Windows user environment variable DEEPSEEK_API_KEY.')


def configure(replace=False):
    if os.name != 'nt':
        raise ValueError('Use DEEPSEEK_API_KEY on non-Windows systems.')
    if not sys.stdin.isatty():
        raise ValueError('configure requires an interactive terminal with hidden input.')
    if KEY_FILE.exists() and not replace:
        raise ValueError('Credential already exists; use --replace to change it.')
    key = valid_key(getpass.getpass('DeepSeek API key (hidden): '))
    encrypted = base64.b64encode(dpapi(key.encode('utf-8')))
    if dpapi(base64.b64decode(encrypted), True).decode('utf-8') != key:
        raise ValueError('Credential encryption verification failed.')
    KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive create prevents accidental overwrite; replacement is atomic.
    if replace:
        import tempfile
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=KEY_FILE.parent, delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(encrypted)
            os.replace(temporary, KEY_FILE)
        finally:
            if temporary and temporary.exists():
                temporary.unlink()
    else:
        with KEY_FILE.open('xb') as handle:
            handle.write(encrypted)
    return {'configured': True, 'storage': 'Windows current-user DPAPI', 'path': str(KEY_FILE),
            'environment_override': bool(os.environ.get('DEEPSEEK_API_KEY', '').strip())}


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def call_api(operation, payload, key, timeout=45, retries=2):
    if operation not in ('search', 'fetch'):
        raise ValueError('Unsupported operation.')
    opener = request.build_opener(NoRedirect)
    req = request.Request(BASE + operation, data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
                          headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'}, method='POST')
    for attempt in range(retries + 1):
        try:
            with opener.open(req, timeout=timeout) as response:
                result = json.load(response)
            if not isinstance(result, dict):
                raise ValueError('API response is not a JSON object.')
            if operation == 'search' and not isinstance(result.get('results'), list):
                raise ValueError('API response lacks results array.')
            if operation == 'fetch' and not isinstance(result.get('content'), str):
                raise ValueError('API response lacks content string.')
            return result
        except error.HTTPError as exc:
            status = exc.code
            exc.close()
            retryable = status in (408, 429) or 500 <= status <= 599
            if not retryable or attempt == retries:
                # Do not print server error bodies that might echo request data.
                raise ValueError(f'DeepSeek HTTP {status}; attempts={attempt + 1}. Check API error guidance.') from None
        except (error.URLError, TimeoutError, OSError):
            if attempt == retries:
                raise ValueError(f'Network request failed; attempts={attempt + 1}.') from None
        time.sleep(2 ** attempt)


def bounded_text(value):
    if not 1 <= len(value) <= 128 or not value.strip():
        raise argparse.ArgumentTypeError('Expected 1–128 Unicode characters, not all whitespace.')
    return value


def url_value(value):
    try:
        parsed = parse.urlsplit(value)
        if len(value) <= 8192 and parsed.scheme in ('http', 'https') and parsed.hostname and parsed.username is None and parsed.password is None and not any(c.isspace() for c in value):
            return value
    except ValueError:
        pass
    raise argparse.ArgumentTypeError('Expected HTTP(S) URL with host, no credentials, at most 8192 characters.')


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    config = commands.add_parser('configure')
    config.add_argument('--replace', action='store_true')
    commands.add_parser('status')
    search = commands.add_parser('search')
    search.add_argument('--query', required=True, type=bounded_text)
    search.add_argument('--count', type=int, choices=range(1, 51), default=10, metavar='1..50')
    search.add_argument('--content', action='store_true')
    search.add_argument('--ori-question', type=bounded_text)
    search.add_argument('--localization')
    fetch = commands.add_parser('fetch')
    fetch.add_argument('--url', type=url_value, required=True)
    for command in (search, fetch):
        command.add_argument('--output', type=Path)
    args = parser.parse_args()
    try:
        if args.command == 'fetch' and parse.urlsplit(args.url).hostname == 'mp.weixin.qq.com':
            raise ValueError('Paid Fetch is disabled for WeChat article links. Use wechat-article-reader or a browser directly; no API request was made.')
        if args.command == 'configure':
            result = configure(args.replace)
        elif args.command == 'status':
            source = key_source()
            if source != 'missing':
                load_key()  # Verify readability without exposing the value or making a request.
            result = {'configured': source != 'missing', 'source': source, 'network_verified': False}
        else:
            if args.output and args.output.exists():
                raise ValueError('Output file already exists; choose a new path.')
            if args.command == 'search':
                payload = {'query': args.query, 'count': args.count, 'need_content': args.content}
                if args.ori_question is not None:
                    payload['ori_question'] = args.ori_question
                if args.localization is not None:
                    payload['localization'] = args.localization
            else:
                payload = {'url': args.url}
            result = {'provider': 'DeepSeek', 'endpoint': BASE + args.command, 'request': payload,
                      'retrieved_at': datetime.now(timezone.utc).isoformat(),
                      'response': call_api(args.command, payload, load_key())}
        serialized = json.dumps(result, ensure_ascii=False, indent=2)
        if getattr(args, 'output', None):
            with args.output.open('x', encoding='utf-8') as handle:
                handle.write(serialized + '\n')
        else:
            print(serialized)
        return 0
    except (ValueError, OSError) as exc:
        print(json.dumps({'error': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
