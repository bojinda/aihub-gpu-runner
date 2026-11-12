"""HA authentication and obsolete-action refusal, without any production request."""
import asyncio,importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

path=Path(__file__).resolve().parents[1]/'home_assistant/custom_components/aihub_recovery/bridge.py'
spec=importlib.util.spec_from_file_location('one_click_bridge',path);bridge=importlib.util.module_from_spec(spec);spec.loader.exec_module(bridge)

class AdminOnly(unittest.IsolatedAsyncioTestCase):
    async def test_active_human_admin_is_the_only_required_confirmation(self):
        calls=[]
        async def executor(fn,*args):calls.append(args);return fn(*args)
        request=Mock(return_value=dict(timestamp=1,outcome='success',released_leases=1,error=None))
        hass=SimpleNamespace(data={'aihub_recovery':SimpleNamespace(request=request)},async_add_executor_job=executor)
        for flags in ({'is_admin':False},{'is_active':False},{'system_generated':True}):
            values=dict(is_admin=True,is_active=True,system_generated=False,id='operator');values.update(flags)
            c=SimpleNamespace(user=SimpleNamespace(**values),send_error=Mock(),send_result=Mock())
            await bridge.dispatch(hass,c,dict(id=1,type='aihub_recovery/control',action='estop'))
            c.send_error.assert_called_once();request.assert_not_called()
        c=SimpleNamespace(user=SimpleNamespace(is_admin=True,is_active=True,system_generated=False,id='operator'),send_error=Mock(),send_result=Mock())
        await bridge.dispatch(hass,c,dict(id=2,type='aihub_recovery/control',action='estop'))
        request.assert_called_once_with('estop','operator',{});c.send_result.assert_called_once()
        for action in ('estop-check','estop-verify'):
            c.send_error.reset_mock();await bridge.dispatch(hass,c,dict(id=3,type='aihub_recovery/control',action=action));c.send_error.assert_called_once()
        self.assertEqual(request.call_count,1)
