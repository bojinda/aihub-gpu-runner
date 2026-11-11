import os
import sys
from pathlib import Path

# The existing Wallpaper user bind mount carries the enable file. Other Comfy
# services sharing custom_nodes remain untouched. No image/env/port change.
settings=Path('/app/user/.aihub-recovery/enabled')
if os.environ.get('AIHUB_WALLPAPER_BARRIER') or settings.is_file():
    if not os.environ.get('AIHUB_WALLPAPER_BARRIER'):
        os.environ['AIHUB_WALLPAPER_BARRIER']=str(settings.parent)
    wheel=Path(__file__).parent/'runner.whl'
    if wheel.is_file():
        sys.path.insert(0,str(wheel))
        from _aihub_recovery_comfy.comfy_plugin import install
    else:
        from aihub_gpu_runner.comfy_plugin import install
    BARRIER=install()
NODE_CLASS_MAPPINGS = {}
NODE_DISPLAY_NAME_MAPPINGS = {}
