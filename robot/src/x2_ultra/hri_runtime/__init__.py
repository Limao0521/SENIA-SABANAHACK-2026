"""HRI runtime del reto 2 — Club N (docs/reto-2/HRI_RUNTIME_SPEC.md).

T1 posee: fsm.py, access.py, club_kb.json, personas.py, contract.py,
llm_client.py, intents.py. Otros agentes escriben asr/, confirm/, backends/,
motion_online/ en paralelo; este __init__ NO los importa eagerly.
"""
__version__ = "0.1.0"

__all__ = ["access", "contract", "fsm", "intents", "llm_client", "personas"]


def __getattr__(name):  # lazy submodules
    import importlib
    if name in __all__:
        return importlib.import_module(f".{name}", __name__)
    raise AttributeError(name)
