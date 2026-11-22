"""Workspace-only launchers for isolated, pinned host analysis MCP servers."""
import argparse
import http.client
import importlib.metadata
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import time

from tools.research.toolchain import rea_profile, toolchain_config


REVERIFY_TOOLS = {
    're_backends', 're_parse', 're_auto_triage', 're_pattern_scan',
    're_verify_claim', 're_ledger',
}
REVERIFY_CLAIMS = {
    'bytes_at', 'u16_at', 'u32_at', 'u64_at', 'pattern_present',
    'string_present', 'section_present', 'import_present', 'export_present',
}


def analysis_environment(root, inherited):
    env = {key: value for key, value in inherited.items()
           if not key.startswith(('GHIDRA_', 'MCP_', 'JAVA_', '_JAVA_'))}
    home = root / 'home'
    env.update({
        'HOME': str(home),
        'XDG_CONFIG_HOME': str(home / 'config'),
        'XDG_CACHE_HOME': str(home / 'cache'),
        'JAVA_TOOL_OPTIONS': f'-Duser.home="{home}"',
        'ANONYMIZED_TELEMETRY': 'False',
        'OTEL_SDK_DISABLED': 'true',
        'HF_HUB_OFFLINE': '1',
        'TRANSFORMERS_OFFLINE': '1',
        'DO_NOT_TRACK': '1',
        'GHIDRA_MCP_ALLOW_SCRIPTS': '0',
        'GHIDRA_DEBUGGER_TOOLS': '0',
        'GHIDRA_MCP_REQUIRE_PROGRAM_SELECTORS': '1',
        'GHIDRA_MCP_FILE_ROOT': str(root / 'workspace'),
        'REVERIFY_LEDGER_DIR': str(root / 'workspace' / 'reverify-ledger'),
    })
    return env


def validate_reverify_call(name, arguments, input_root):
    if name not in REVERIFY_TOOLS:
        raise ValueError(f'Tool is not enabled for workspace verification: {name}')
    if name == 're_backends':
        return
    target = Path(arguments['file_path']).resolve(strict=True)
    if not target.is_file() or not target.is_relative_to(input_root.resolve()):
        raise ValueError('Reverify inputs must be staged inside the workspace MCP inputs directory')
    if name == 're_ledger' and arguments.get('action', 'show') not in ('show', 'index'):
        raise ValueError('Deleting evidence through re_ledger is disabled')
    if name == 're_verify_claim':
        claims = arguments.get('claims')
        if not isinstance(claims, list) or not claims or len(claims) > 64:
            raise ValueError('Supply between 1 and 64 byte or structural claims')
        for claim in claims:
            if not isinstance(claim, dict) or claim.get('kind') not in REVERIFY_CLAIMS:
                raise ValueError('Instruction, emulation, execution and equivalence claims are disabled')


def reverify_request(original, request, input_root):
    if request.get('method') == 'tools/call':
        params = request.get('params', {})
        try:
            validate_reverify_call(params.get('name'), params.get('arguments', {}), input_root)
        except (KeyError, OSError, TypeError, ValueError) as error:
            return {'jsonrpc': '2.0', 'id': request.get('id'), 'result': {
                'isError': True, 'content': [{'type': 'text', 'text': str(error)}]}}
    response = original(request)
    if response and request.get('method') == 'tools/call':
        for content in response.get('result', {}).get('content', []):
            result = json.loads(content['text'])
            if isinstance(result, dict) and result.get('error') is not None:
                response['result']['isError'] = True
    return response


def run_reverify(input_root):
    from reverify import mcp_server

    original = mcp_server.handle_request
    mcp_server.TOOLS_MANIFEST = [
        tool for tool in mcp_server.TOOLS_MANIFEST if tool['name'] in REVERIFY_TOOLS]
    mcp_server.handle_request = lambda request: reverify_request(original, request, input_root)
    mcp_server.run_mcp_server()


def wait_for_backend(backend, socket_path, token, log_path):
    from bridge_mcp_ghidra.transport import UnixHTTPConnection

    deadline = time.monotonic() + 60
    headers = {'Authorization': 'Bearer ' + token}
    while time.monotonic() < deadline:
        if backend.poll() is not None:
            raise RuntimeError(f'Ghidra MCP exited during startup; inspect {log_path}')
        if socket_path.exists():
            connection = UnixHTTPConnection(str(socket_path), timeout=5)
            try:
                connection.request('GET', '/mcp/instance_info', headers=headers)
                response = connection.getresponse()
                text = response.read().decode('utf-8')
                if response.status != 200:
                    raise RuntimeError(f'Ghidra MCP instance discovery failed: HTTP {response.status}')
                info = json.loads(text)
                info = info.get('data', info)
            finally:
                connection.close()
            port = info.get('tcp_port')
            if isinstance(port, int) and 0 < port <= 65535:
                connection = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
                try:
                    connection.request('GET', '/mcp/schema', headers=headers)
                    response = connection.getresponse()
                    schema = json.loads(response.read())
                    schema = schema.get('data', schema)
                    if response.status != 200 or not any(
                            tool.get('path') == '/get_metadata' for tool in schema.get('tools', [])):
                        raise RuntimeError('Ghidra MCP is missing its ready analysis schema')
                finally:
                    connection.close()
                return f'http://127.0.0.1:{port}'
        time.sleep(0.1)
    raise RuntimeError(f'Ghidra MCP did not become ready; inspect {log_path}')


def run_ghidra(root, profile, env):
    ghidra = Path(profile['ghidra'])
    jar = root / 'sources' / 'ghidra-mcp' / 'build' / 'libs' / 'GhidraMCP-7.0.0.jar'
    if not jar.is_file():
        raise FileNotFoundError(f'Build the pinned Ghidra MCP extension first: {jar}')
    jars = [jar]
    for section in ('Framework', 'Features', 'Processors', 'Debug'):
        jars.extend(sorted((ghidra / 'Ghidra' / section).glob('*/lib/*.jar')))
    with tempfile.TemporaryDirectory(prefix='ghidra-', dir=root / 'runtime') as runtime:
        env = dict(env, XDG_RUNTIME_DIR=runtime,
                   GHIDRA_MCP_AUTH_TOKEN=secrets.token_urlsafe(32),
                   GHIDRA_MCP_PIN_URL='1')
        log_path = root / 'receipts' / f'ghidra-runtime-{Path(runtime).name}.log'
        command = [
            str(Path(profile['java']) / 'bin' / 'java'), '-Xmx2g',
            f'-Dghidra.home={ghidra}', '-Dapplication.name=GhidraMCP',
            '-cp', os.pathsep.join(map(str, jars)),
            'com.xebyte.headless.GhidraMCPHeadlessServer',
            '--port', '0', '--bind', '127.0.0.1',
        ]
        with log_path.open('x') as log:
            backend = subprocess.Popen(command, env=env, stdin=subprocess.DEVNULL,
                                       stdout=log, stderr=subprocess.STDOUT)
            try:
                socket_path = Path(runtime) / 'ghidra-mcp' / f'ghidra-{backend.pid}.sock'
                env['GHIDRA_MCP_URL'] = wait_for_backend(
                    backend, socket_path, env['GHIDRA_MCP_AUTH_TOKEN'], log_path)
                result = subprocess.run(
                    [sys.executable, '-m', 'bridge_mcp_ghidra', '--transport', 'stdio', '--no-lazy'],
                    env=env)
                return result.returncode
            finally:
                if backend.poll() is None:
                    backend.terminate()
                    try:
                        backend.wait(timeout=20)
                    except subprocess.TimeoutExpired:
                        backend.kill()
                        backend.wait(timeout=10)
                        raise RuntimeError(f'Ghidra MCP required forced shutdown; inspect {log_path}')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('server', choices=('pyghidra', 'ghidra', 'reverify'))
    parser.add_argument('--install-root', help='Standalone MCP installation with its own Ghidra and JDK')
    args = parser.parse_args(argv)
    if sys.platform != 'linux':
        parser.error('Launch through the workspace WSL MCP configuration')
    config = toolchain_config()['workspace_mcp']
    install_root = args.install_root
    if install_root is None and args.server in ('ghidra', 'pyghidra'):
        install_root = config['ghidra_install_root']
    root = Path(install_root if install_root is not None else config['root']).expanduser()
    if not root.is_absolute():
        parser.error('The MCP installation root must be an absolute Linux path')
    root = root.resolve()
    environment = root / 'envs' / args.server
    python = environment / 'bin' / 'python'
    if not python.is_file():
        parser.error(f'Missing isolated MCP environment: {environment}')
    os.umask(0o077)
    for directory in ('home', 'runtime', 'receipts', 'workspace/inputs', 'workspace/pyghidra'):
        (root / directory).mkdir(parents=True, exist_ok=True)
    env = analysis_environment(root, os.environ)
    profile = rea_profile('current', tools_root=root if install_root is not None else None)
    env.update(JAVA_HOME=profile['java'], GHIDRA_INSTALL_DIR=profile['ghidra'])
    if Path(sys.prefix) != environment:
        command = [str(python), '-u', '-m', 'tools.research.mcp_workspace', args.server]
        if install_root is not None:
            command.extend(('--install-root', str(root)))
        os.execve(python, command, env)
    os.environ.clear()
    os.environ.update(env)
    distribution = {'pyghidra': 'pyghidra-mcp', 'ghidra': 'ghidra-mcp-bridge',
                    'reverify': 'reverify'}[args.server]
    if importlib.metadata.version(distribution) != config['servers'][args.server]['version']:
        raise RuntimeError(f'Installed {distribution} differs from the pinned workspace version')
    if args.server == 'reverify':
        run_reverify(root / 'workspace' / 'inputs')
        return 0
    if args.server == 'ghidra':
        return run_ghidra(root, profile, env)
    os.execve(python, [
        str(python), '-u', '-m', 'pyghidra_mcp', '--transport', 'stdio',
        '--project-path', str(root / 'workspace' / 'pyghidra'),
        '--project-name', 'sony-research', '--max-workers', '2',
    ], env)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f'Workspace MCP startup failed: {error}', file=sys.stderr)
        raise SystemExit(1)
