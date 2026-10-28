"""将已验证的Agent放入现有财务项目。目标必须是新目录，不改业务库及版本指针。"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

SOURCE=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser()
parser.add_argument('--target',required=True)
args=parser.parse_args()
target=Path(args.target).resolve()
allowed=Path('E:/codex/job_payment').resolve()
if target.parent!=allowed or target.name!='财务问数Agent':
    raise SystemExit('安装目标必须为E:/codex/job_payment/财务问数Agent')
if target.exists():raise SystemExit('目标已存在，为保护已有配置，不覆盖；请检查目标目录。')
target.mkdir()
ignored={'.venv','runtime','__pycache__','.pytest_cache','.git'}
copied={}
for path in SOURCE.rglob('*'):
    rel=path.relative_to(SOURCE)
    if any(part in ignored for part in rel.parts) or not path.is_file():continue
    if path.name=='config.local.json' or 'secrets' in rel.parts:continue
    out=target/rel;out.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,out)
    copied[str(rel)]=hashlib.sha256(out.read_bytes()).hexdigest()
shutil.copy2(target/'config.example.json',target/'config.local.json')
(target/'runtime').mkdir()
for name in ['r33_readonly_verification.json','delivery_verification.json']:
    report=SOURCE/'runtime'/name
    if report.exists():shutil.copy2(report,target/'runtime'/name)
# Windows PowerShell 5.1使用BOM识别中文脚本。
launcher=target/'启动.ps1'
launcher.write_text(launcher.read_text(encoding='utf-8-sig'),encoding='utf-8-sig')
copied['启动.ps1']=hashlib.sha256(launcher.read_bytes()).hexdigest()
(target/'runtime/installation.json').write_text(json.dumps({'source':str(SOURCE),'target':str(target),'files':copied,'business_data_modified':False},ensure_ascii=False,indent=2),encoding='utf8')
subprocess.run([sys.executable,'-m','venv',str(target/'.venv')],check=True)
subprocess.run([str(target/'.venv/Scripts/python.exe'),'-m','pip','install','--disable-pip-version-check','-r',str(target/'requirements.lock.txt')],check=True)
print(json.dumps({'installed':str(target),'files':len(copied),'config':str(target/'config.local.json')},ensure_ascii=False))
