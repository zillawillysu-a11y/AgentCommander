# -*- mode: python ; coding: utf-8 -*-
a = Analysis(['agentcommander_gui.py'], pathex=['.'], hiddenimports=['commander.gui'], datas=[('prompts', 'prompts')])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name='AgentCommander', console=False)
