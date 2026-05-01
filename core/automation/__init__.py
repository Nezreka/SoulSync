"""Automation API + progress tracking helpers package.

Lifted from web_server.py /api/automations/* routes and progress
emitters. The action handler registration (`_register_automation_handlers`)
stays in web_server.py because each handler closure is tightly coupled
to other application features.
"""
