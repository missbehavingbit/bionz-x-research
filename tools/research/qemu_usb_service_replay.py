"""Run the QEMU-only USB service stimulus through the existing guarded mod loader."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time

from tools.research.qemu_boot_probe import Qmp
from tools.research.qemu_usb_host_probe import read_registers
from tools.research.qemu_ep0_host import Ep0Host

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'qemu-a6400/tools'))
import moddev


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('qemu-dir','inputs','loader','mod','report-dir'):
        p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--plugin',type=Path,help='optional passive original-code startup observer')
    p.add_argument('--uvc-import-preflight',action='store_true',
                   help='record guest exports for the hash-pinned E0476 module; does not load it')
    p.add_argument('--vbus-before-start',action='store_true',
                   help='explicit virtual-cable-present stimulus before configure/start; default preserves E0596')
    p.add_argument('--captured-start-gate',action='store_true',
                   help='opt in to the E0597 physical F0200024 connected-MSC snapshot')
    p.add_argument('--strict-ep0',action='store_true',
                   help='explicit host tokens and bounded standard enumeration; legacy probe unchanged')
    args=p.parse_args()
    out=args.report_dir.resolve()
    if not out.is_relative_to(ROOT/'artifacts'): raise ValueError('Output must stay under artifacts')
    out.mkdir(parents=True,exist_ok=False)
    report={'status':'started','physical_camera_access':False,'explicit_stimulus':'bus-power configure/start',
            'tool_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'inputs':{},'host_lines':[]}
    report['vbus_before_start']=args.vbus_before_start
    report['captured_start_gate']=args.captured_start_gate
    report['strict_ep0']=args.strict_ep0
    if args.uvc_import_preflight:
        import io
        from elftools.elf.elffile import ELFFile
        module=ROOT/'artifacts/native/uvc-transport-e0472-status-prearm/usbg_video_a6400_shared_candidate.ko'
        raw=module.read_bytes()
        digest=hashlib.sha256(raw).hexdigest()
        if digest!='dcc2f64f944790dd1b8519b29ee6f6ce709a03129fb92d363fdafe14f16915cd':
            raise ValueError('E0476 UVC module identity mismatch')
        symbols=ELFFile(io.BytesIO(raw)).get_section_by_name('.symtab')
        imports=sorted({s.name for s in symbols.iter_symbols()
                        if s.name and s['st_shndx']=='SHN_UNDEF'})
        report['uvc_import_preflight']={'module_sha256':digest,'imports':imports,
                                      'module_loaded':False}
    for name,path in [('qemu',args.qemu_dir/'build/qemu-system-arm'),('manifest',args.inputs/'boot-manifest.json'),
                      ('mod',args.mod),('core',args.loader/'core.bin'),('guard',args.loader/'mlv2_guard_arm'),
                      ('request',args.loader/'mlv2.req')]:
        report['inputs'][name]={'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
    with tempfile.TemporaryDirectory(prefix='qemu-usb-service-') as temp:
        scratch=Path(temp)
        plugin=None
        if args.plugin:
            kernel=args.inputs/'nflasha3-Image'
            if hashlib.sha256(kernel.read_bytes()).hexdigest()!='d7a9ae09d2195c1b5151e60f3ff1344715b619631cfad608d9a5fcf7a876c618':
                raise ValueError('Startup plugin kernel identity mismatch')
            plugin=scratch/'usb-startup.so';plugin.write_bytes(args.plugin.read_bytes())
            report['plugin_sha256']=hashlib.sha256(plugin.read_bytes()).hexdigest()
        for name in ('mlv2_guard_arm','mlv2.req'):shutil.copy(args.loader/name,scratch/name)
        monitor=scratch/'qmp';serial=scratch/'serial';hostpath=scratch/'host'
        env=dict(os.environ, A6400_QEMU_DIR=str(args.qemu_dir.resolve()),
                 A6400_QEMU_DATA_DIR=str(args.inputs.resolve()),RUN_DIR=str(scratch/'run'),TIMEOUT='0',
                 ULOGIO='',EVSEL_LATE_US='500',REAR_CONTROLS='1',REAR_LCD_DIAGNOSTIC='1',
                 DWC3_GADGET='1',USB_VBUS_TEST='1',USB_CID_HIGH='1',
                 SERIAL=f'unix:{serial},server=on,wait=off',
                 SETTING_FILES=moddev.setting_files(scratch/'mlv2_guard_arm',scratch/'mlv2.req'),
                 QEMU_EXTRA=f'-icount shift=0,sleep=on -net none -no-reboot -qmp unix:{monitor},server=on,wait=off '
                            f'-chardev socket,id=usbhost,path={hostpath},server=on,wait=off '
                            '-global cxd90045-dwc3.chardev=usbhost -global cxd90045-dwc3.captured-hwparams=on')
        if plugin:env['QEMU_EXTRA']+=f' -plugin {plugin}'
        if args.captured_start_gate:env['QEMU_EXTRA']+=' -global cxd90045-dwc3.captured-start-gate=on'
        if args.strict_ep0:env['QEMU_EXTRA']+=' -global cxd90045-dwc3.strict-ep0=on'
        qmp=None;console=None;started=time.monotonic()
        with (out/'console.log').open('wb') as log:
            proc=subprocess.Popen(['bash',str(ROOT/'qemu-a6400/run.sh')],env=env,stdout=log,stderr=log,
                                  start_new_session=True)
            try:
                for _ in range(300):
                    if monitor.exists():break
                    if proc.poll() is not None:raise RuntimeError('QEMU exited during startup')
                    time.sleep(.1)
                qmp=Qmp(monitor,time.monotonic()+300)
                console=moddev.Console(serial,out/'serial.log',proc)
                dev=moddev.ModDev(console,args.loader,[args.mod])
                dev.wait_menu(timeout=90)
                if args.uvc_import_preflight:
                    exports=console.run('cat /proc/kallsyms') or ''
                    (out/'guest-kallsyms.txt').write_text(exports)
                    names={line.split()[2] for line in exports.splitlines() if len(line.split())>=3}
                    check=report['uvc_import_preflight']
                    check['missing_exports']=[name for name in imports if '__ksymtab_'+name not in names]
                    check['observed_names']=[name for name in imports if name in names]
                    check['qualification']='export names only; relocation, ABI and module initialization untested'
                dev.install()
                report['loader']=dev.status
                report['before']=read_registers(qmp)
                if args.vbus_before_start:
                    qmp.request('qom-set',{'path':'/machine','property':'usb-vbus','value':True})
                    time.sleep(3)
                    report['before_start_with_vbus']=read_registers(qmp)
                report['library_files']=console.run('/tmp_bt/mlv2_guard fnv /usr/lib/libUsbControl.so; '
                    '/tmp_bt/mlv2_guard fnv /usr/lib/libMWF.so; '
                    '/tmp_bt/mlv2_guard fnv /lib/libc-2.11.2.so; '
                    'p=$(pidof im.elf); cat /proc/$p/maps')
                console.run('touch /tmp_bt/qemu-usb-service.allow')
                qmp.request('send-key',{'keys':[{'type':'qcode','data':'m'}],'hold-time':180})
                for _ in range(15):
                    time.sleep(1)
                    result=console.run('cat /tmp_bt/usb-service-probe.log 2>/dev/null') or ''
                    if 'initial_state' in result or 'identity_refused' in result:break
                report['stimulus_log']=result
                report['loader_log']=dev.loader_log()
                report['after_stimulus']=read_registers(qmp)
                with socket.socket(socket.AF_UNIX) as host:
                    host.connect(str(hostpath));host.settimeout(.2)
                    qmp.request('qom-set',{'path':'/machine','property':'usb-vbus','value':True})
                    time.sleep(3)
                    host.sendall(b'CONN\n');time.sleep(3)
                    if args.strict_ep0:
                        client=Ep0Host(host)
                        try:
                            report['enumeration']=client.enumerate_bus_power()
                            report['guest_before_disconnect']=console.run('cat /sys/class/udc/dwc3.0/state')
                        finally:
                            report['host_events']=client.events
                            report['host_lines']=[e['rx'] for e in client.events if 'rx' in e]
                    else:
                        host.sendall(b'SETUP 8006000100001200\n')
                        data=b'';end=time.monotonic()+5
                        while time.monotonic()<end:
                            try:data+=host.recv(65536)
                            except socket.timeout:pass
                        report['host_lines']=data.decode('ascii','replace').splitlines()
                    report['attached']=read_registers(qmp)
                    host.sendall(b'DISC\n')
                    qmp.request('qom-set',{'path':'/machine','property':'usb-vbus','value':False})
                report['guest']=console.run('cat /tmp_bt/usb-service-probe.log; '
                    'cat /sys/class/udc/dwc3.0/state; grep -i dwc /proc/interrupts; '
                    'dmesg | grep -i -E "gadget|otg|dwc|usbif"')
                report['configure_sent']='configure_result 00000000' in (report['guest'] or '')
                report['start_sent']='start_result 00000000' in (report['guest'] or '')
                report['controller_run']=int(report['attached']['DCTL'],16)&0x80000000 != 0
                report['status']='observed'
                qmp.request('quit');report['qemu_exit']=proc.wait(10)
            except Exception as error:
                report.update(status='stopped',stop_reason=f'{type(error).__name__}: {error}')
            finally:
                if qmp:qmp.close()
                if proc.poll() is None:
                    proc.terminate()
                    try:proc.wait(10)
                    except subprocess.TimeoutExpired:proc.kill();proc.wait(5)
                report['owned_exit']=proc.poll()
                report['elapsed_seconds']=round(time.monotonic()-started,1)
                (out/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({key:report.get(key) for key in
                     ('status','stop_reason','configure_sent','start_sent','controller_run',
                      'host_lines','elapsed_seconds')},indent=2))
    return 0 if report['status']=='observed' else 2

if __name__=='__main__':raise SystemExit(main())
