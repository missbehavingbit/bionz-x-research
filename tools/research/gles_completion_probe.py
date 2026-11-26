"""Verify original TGID completion instructions; never supply a GPU or scheduler reply."""
import argparse
import hashlib
import io
import json
import sys

import unicorn
from unicorn import arm_const as arm
from elftools.elf.elffile import ELFFile

from project_paths import PROJECT_ROOT
from tools.research.mount_evidence import checked_input
from tools.research.startup_trace import read_va
from tools.research.wbi_gles_execute import execute, load_context, new_owner_options


LIBOBJ = ('firmware/extracted_a6400/firmware.tar_unpacked/0700_part_image/dev/'
          'nflasha15_unpacked/lib/libObj.so')
LIBOBJ_SHA256 = '60ffd2b0f31f4bc139a7c13a4f62c25cdeb6a531ad5ef35df48471e6e36e88b1'


def original_result_branch(raw, status):
    """Execute the original caller's class/result check, stopping before either successor."""
    elf = ELFFile(io.BytesIO(raw))
    start = 0x72b88c
    code = read_va(elf, raw, start, 16)
    if code is None or len(code) != 16:
        raise ValueError('Original result branch is not file-backed')
    mu = unicorn.Uc(unicorn.UC_ARCH_ARM, unicorn.UC_MODE_ARM)
    mu.ctl_set_cpu_model(arm.UC_CPU_ARM_CORTEX_A9)
    mu.mem_map(0x72b000, 4096)
    mu.mem_map(0x72d000, 4096)
    mu.mem_write(start, code)
    stops = {0x72b7c6: 'resubmit', 0x72b89c: 'normal_completion', 0x72d6e0: 'recovery'}
    result = {'status': 'running', 'input_status': status, 'trace': [],
              'code_sha256': hashlib.sha256(code).hexdigest()}

    def observe(uc, address, size, _):
        if address in stops:
            result.update(status=stops[address], stop_pc=address)
            uc.emu_stop()
        elif not start <= address < address + size <= start + len(code):
            result.update(status='unsupported_control_flow', stop_pc=address)
            uc.emu_stop()
        else:
            result['trace'].append({'pc': address, 'bytes': bytes(uc.mem_read(address, size)).hex()})

    mu.hook_add(unicorn.UC_HOOK_CODE, observe)
    mu.reg_write(arm.UC_ARM_REG_CPSR, 0x30)
    mu.reg_write(arm.UC_ARM_REG_R3, status)
    try:
        mu.emu_start(start | 1, 0, count=16, timeout=1000000)
    except unicorn.UcError as error:
        result.update(status='execution_fault', error=str(error))
    if result['status'] == 'running':
        result['status'] = 'timeout' if mu.query(unicorn.UC_QUERY_TIMEOUT) else 'instruction_limit'
    return result


def run():
    context = load_context()
    options = new_owner_options(0x60a1c000)
    options['instruction_budget'] = 30000
    result = execute(context, **options, owner_lifecycle=True, completion_lifecycle=True)
    libobj = checked_input(PROJECT_ROOT, LIBOBJ, LIBOBJ_SHA256)
    branches = [original_result_branch(libobj, status)
                for status in (0, 0x04000000, 0x40000000, 0x80000000, 0x45000000)]
    calls = result['calls']
    states = [call['completion_state'] for call in calls[1:]]
    checks = {
        'ordered_original_calls': [call['operation'] for call in calls] == [
            'open', 'wait_reset', 'signal_zero', 'signal_tgid', 'wait_fast', 'wait_again'],
        'original_returns': [call['returned'] for call in calls] == [0, None, 0, 0, 0, None],
        'tgid_not_user_pointer': {state['tgid'] for state in states} == {97},
        'completion_mutations': [(state['fast'], state['done']) for state in states] == [
            (0, 0), (0, 0x7fffffff), (1, 0x7fffffff), (0, 0x7fffffff), (0, 0)],
        'only_original_output_words': [call['output_word'] for call in calls[1:]] == [
            0xa5a5a5a5, 0, 97, 1, 0xa5a5a5a5],
        'explicit_scheduler_boundaries': len(calls) == 6 and all(
            call.get('boundary', {}).get('symbol') == 'wait_for_completion_interruptible_timeout'
            and call.get('timeout_jiffies') == 10000000
            and call.get('completion_argument') == call['completion_state']['owner'] + 0x14
            for call in (calls[1], calls[-1])),
        'no_substitute_returns': result['service_returns_supplied'] == result['hardware_responses_supplied'] == 0,
        'class_bits_complete_without_bit24': [branch['status'] for branch in branches] == [
            'resubmit', 'normal_completion', 'normal_completion', 'normal_completion', 'recovery'],
    }
    return {
        'status': 'contract_windows_verified' if all(checks.values()) else 'contract_verification_failed',
        'checks': checks,
        'inputs': {**context['inputs'], LIBOBJ: LIBOBJ_SHA256},
        'python': sys.version,
        'unicorn': unicorn.__version__,
        'original_completion_sequence': result,
        'original_user_result_branches': branches,
        'display_output_verified': False,
        'limitations': [
            'Isolated captured-memory execution, not a loaded camera application.',
            'Original open creates the new owner; original kernel helpers own completion mutations.',
            'The first wait is paused before the kernel wait import, not registered with a scheduler.',
            'The probe then invokes independent ioctls; it does not fabricate a return from that wait.',
            'No IRQ, GPU submission, scheduler wakeup, rendering or display scanout is supplied.',
        ],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report-dir', required=True)
    args = parser.parse_args(argv)
    output = (PROJECT_ROOT / args.report_dir).resolve()
    if not output.is_relative_to(PROJECT_ROOT / 'artifacts'):
        parser.error('Reports must remain under artifacts')
    output.mkdir(parents=True, exist_ok=False)
    report = run()
    report['tool_sha256'] = {
        path: hashlib.sha256((PROJECT_ROOT / path).read_bytes()).hexdigest()
        for path in ('tools/research/gles_completion_probe.py', 'tools/research/wbi_gles_execute.py',
                     'src/a6400/execution/guest_gles.py', 'src/a6400/execution/guest_threads.py',
                     'src/a6400/execution/interpreter_probe.py')}
    report['command'] = [sys.executable, '-m', 'tools.research.gles_completion_probe',
                         *(sys.argv[1:] if argv is None else argv)]
    (output / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    sequence = report['original_completion_sequence']
    print(report['status'], report['checks'])
    print(sequence['status'], [(call['operation'], call.get('completion_state'))
                               for call in sequence['calls']])
    return 0 if all(report['checks'].values()) else 2


if __name__ == '__main__':
    raise SystemExit(main())
