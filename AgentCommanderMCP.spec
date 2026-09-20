# -*- mode: python ; coding: utf-8 -*-
a = Analysis(['agentcommander_mcp.py'], pathex=['.'], hiddenimports=['commander.server', 'commander.pi_worker', 'commander.doctor'], datas=[('prompts', 'prompts')])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name='AgentCommanderMCP', console=True)
