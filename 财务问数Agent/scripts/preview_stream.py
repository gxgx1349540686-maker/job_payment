"""仅用于无Key浏览器验收；必须显式传--mock，与正式网页端口隔离。"""
import argparse
from dataclasses import replace
from pathlib import Path
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import uvicorn
import app as service
from agent.settings import Settings
from agent.engine import Agent

parser=argparse.ArgumentParser()
parser.add_argument('--mock',required=True,action='store_true')
parser.add_argument('--port',type=int,default=8766)
options=parser.parse_args()


def forbid_key(self):raise AssertionError('模拟预览禁止读取真实Key')
Settings.key=forbid_key


class MockModel:
    enabled=False
    def parse(self,question,prompt):
        time.sleep(.7)
        return {'scope_type':'project','project':'石厦公社'}


settings=replace(Settings.load(),model='模拟验证 · 无真实API')
service._agent=Agent(settings,model=MockModel())
print('模拟预览：不读取Key，不发起外部API请求。',flush=True)
uvicorn.run(service.app,host='127.0.0.1',port=options.port)
