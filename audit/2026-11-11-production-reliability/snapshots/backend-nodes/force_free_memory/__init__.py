import gc
import logging

class AnyType(str):
    def __ne__(self, other):
        return False

ANY = AnyType("*")

class ForceFreeMemory:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "anything": (ANY, {"forceInput": True}),
                "unload_models": ("BOOLEAN", {"default": True}),
                "free_memory": ("BOOLEAN", {"default": True}),
            }
        }

    RETURN_TYPES = ()
    FUNCTION = "run"
    OUTPUT_NODE = True
    CATEGORY = "utils/memory"

    def run(self, anything=None, unload_models=True, free_memory=True):
        try:
            import comfy.model_management as mm

            if unload_models:
                if hasattr(mm, "unload_all_models"):
                    mm.unload_all_models()
                elif hasattr(mm, "unload_model_clones"):
                    mm.unload_model_clones()

            if free_memory:
                if hasattr(mm, "soft_empty_cache"):
                    mm.soft_empty_cache()

        except Exception as e:
            logging.warning(f"[ForceFreeMemory] Comfy cleanup failed: {e}")

        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
                torch.cuda.ipc_collect()
        except Exception as e:
            logging.warning(f"[ForceFreeMemory] Torch CUDA cleanup failed: {e}")

        gc.collect()
        logging.info("[ForceFreeMemory] requested model unload and CUDA cache cleanup")
        return ()

NODE_CLASS_MAPPINGS = {
    "ForceFreeMemory": ForceFreeMemory,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "ForceFreeMemory": "Force Free Memory"
}
