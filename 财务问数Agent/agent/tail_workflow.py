"""Pinned local statement workflow bridge. No arbitrary commands, model calls or DB writes."""
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading
import time
import uuid

from .settings import ROOT


def statement_request(question):
    """Only affirmative output requests authorize generation; mentions/questions don't."""
    if re.search(r'不(?:要|用|必|需要)?(?:再)?(?:生成|制作|输出|导出|出账|整理)|别(?:生成|制作|出账)|不(?:要|用|需要).{0,4}(?:尾期|退出结算|对账单)|取消|如何|怎么|流程|能做什么', question):
        return False
    return bool(re.search(r'尾期|退出结算', question) and
                re.search(r'生成|制作|出一[份张]|做一[份张]|导出|输出|出具|做[个份张]|出[个份张]|需要一[份张]|要一[份张]|给我一[份张]|整理|出账', question) and
                re.search(r'对账单|结算单|账单|excel|表格', question, re.I))


class TailWorkflow:
    slots = threading.BoundedSemaphore(2)

    def __init__(self, root=ROOT):
        self.root = Path(root)
        self.config = json.loads((self.root/'knowledge/tail_workflow.json').read_text(encoding='utf-8'))
        self.outputs = self.root/'runtime/statements'

    def verify(self):
        bundle = (self.root/self.config['bundle']).resolve()
        if not bundle.is_relative_to(self.root.resolve()):
            raise ValueError('工作流路径校验未通过。')
        for name, expected in self.config['files_sha256'].items():
            file = (bundle/name).resolve()
            if not file.is_relative_to(bundle) or hashlib.sha256(file.read_bytes()).hexdigest() != expected:
                raise ValueError('最终工作流代码指纹已变化，请先核对版本。')
        source = Path(self.config['source_root'])
        if not source.is_dir():
            raise ValueError('原尾期工作流资料目录不可用，不能改用其他数据猜测生成。')
        return bundle, source

    def generate(self, dimension, on_event=None, cancelled=None):
        bid = str(dimension.get('building_id') or '')
        if not re.fullmatch(r'[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}', bid):
            raise ValueError('楼栋没有可确认的真实ID，不能调用尾期出账。')
        if cancelled and cancelled.is_set():
            raise ValueError('已取消，未启动尾期工作流。')
        bundle, source = self.verify()
        if not self.slots.acquire(blocking=False):
            raise ValueError('已有两份对账单正在生成，请稍后重试。')
        job = uuid.uuid4().hex
        directory = self.outputs/job
        process = None
        started = time.monotonic()
        try:
            directory.mkdir(parents=True)
            request = dict(job_id=job, building_id=bid, project=dimension['project'], building=dimension['building'],
                           source_root=str(source), bundle=str(bundle), workflow_id=self.config['id'])
            (directory/'request.json').write_text(json.dumps(request, ensure_ascii=False), encoding='utf-8')
            # Keep only OS/runtime variables. Keys and model configuration never enter the worker.
            allowed = {'SYSTEMROOT','WINDIR','PATH','TEMP','TMP','USERPROFILE','LOCALAPPDATA','APPDATA','COMSPEC'}
            environment = {k:v for k,v in os.environ.items() if k.upper() in allowed}
            environment['PYTHONIOENCODING'] = 'utf-8'
            command = [sys.executable, '-X', 'utf8', str(self.root/'scripts/tail_statement_worker.py'), str(directory/'request.json')]
            kwargs = {'creationflags':subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
            process = subprocess.Popen(command, cwd=self.root, env=environment, stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace', **kwargs)
            events = queue.Queue()
            def read():
                for line in process.stdout:
                    events.put(line)
                events.put(None)
            threading.Thread(target=read, daemon=True).start()
            finished = False
            while not finished:
                if cancelled and cancelled.is_set():
                    raise ValueError('已取消，尾期工作流已停止，不会自动重试。')
                if time.monotonic()-started > self.config['timeout_seconds']:
                    raise ValueError('尾期工作流超时，已停止；没有自动重试或生成成功结论。')
                try:
                    line = events.get(timeout=.1)
                except queue.Empty:
                    continue
                if line is None:
                    finished = True
                else:
                    try:
                        event = json.loads(line)
                    except (ValueError, TypeError):
                        continue
                    if event.get('event') == 'progress' and on_event:
                        on_event('progress', {'stage':'statement','label':event['label'],'state':'started'})
            process.wait(timeout=3)
            receipt_file = directory/'receipt.json'
            if not receipt_file.is_file():
                raise ValueError('工作流异常结束，未取得完整出账凭据；未生成可下载账单。')
            receipt = json.loads(receipt_file.read_text(encoding='utf-8'))
            receipt['elapsed_seconds'] = round(time.monotonic()-started, 3)
            if process.returncode or receipt.get('status') != 'completed':
                return dict(status='workflow_blocked', answer='尾期工作流暂未完成：'+receipt.get('error','资料不足或处理异常')+
                            '\n未生成可下载对账单；需要按诊断核对资料，不会自动补金额。', workflow=receipt)
            file = directory/'statement.xlsx'
            if not file.is_file() or hashlib.sha256(file.read_bytes()).hexdigest() != receipt['output_sha256']:
                raise ValueError('生成文件与出账凭据不一致，停止提供下载。')
            ocr_warning = '\n凭证OCR未完成，仍需人工核验。' if any('OCR未完成' in n or '已跳过OCR' in n for n in receipt.get('result',{}).get('notes',[])) else ''
            return dict(status='statement_ready', answer='尾期对账单已生成，可下载Excel。\n来源：原最终工作流Excel资料；当前数据库用于楼栋识别，未用数据库重算工作流金额。\n这是机处理待复核稿，不表示审批、付款或核销完成。'+ocr_warning,
                        artifact={'id':job,'name':dimension['project']+'_'+dimension['building']+'_尾期对账单.xlsx',
                                  'url':'/api/statements/'+job,'sha256':receipt['output_sha256']}, workflow=receipt)
        finally:
            if process is not None:
                if process.poll() is None:
                    process.terminate()
                    try: process.wait(timeout=3)
                    except subprocess.TimeoutExpired: process.kill(); process.wait()
                process.stdout.close()
            self.slots.release()

    def download(self, token):
        if not re.fullmatch('[a-f0-9]{32}', token):
            raise ValueError('无效文件编号。')
        folder = (self.outputs/token).resolve()
        if not folder.is_relative_to(self.outputs.resolve()):
            raise ValueError('文件路径无效。')
        record = json.loads((folder/'receipt.json').read_text(encoding='utf-8'))
        file = (folder/'statement.xlsx').resolve()
        if not file.is_relative_to(folder) or record.get('status') != 'completed' or hashlib.sha256(file.read_bytes()).hexdigest() != record.get('output_sha256'):
            raise ValueError('文件未完成或校验未通过。')
        return file
