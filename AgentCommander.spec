# -*- mode: python ; coding: utf-8 -*-
mcp = Analysis(['agentcommander_mcp.py'], pathex=['.'], hiddenimports=['commander.server', 'commander.pi_worker', 'commander.doctor'], datas=[('prompts', 'prompts')])
gui = Analysis(['agentcommander_gui.py'], pathex=['.'], hiddenimports=['commander.gui'], datas=[('prompts', 'prompts')])
mcp_pyz = PYZ(mcp.pure)
gui_pyz = PYZ(gui.pure)
mcp_exe = EXE(mcp_pyz, mcp.scripts, [], exclude_binaries=True, name='AgentCommanderMCP', console=True)
gui_exe = EXE(gui_pyz, gui.scripts, [], exclude_binaries=True, name='AgentCommander', console=False)
collect = COLLECT(mcp_exe, gui_exe, mcp.binaries, mcp.datas, gui.binaries, gui.datas, name='AgentCommander')
