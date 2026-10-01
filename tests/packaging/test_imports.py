import subprocess
import sys

BLOCK_OPTIONAL_IMPORTS = """
import importlib.abc
import sys
class RejectOptional(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'torch', 'transformers', 'tokenizers', 'numpy',
                                      'huggingface_hub', 'typesafe', 'typesafe_sdk', 'httpx2'}:
            raise AssertionError('Optional dependency imported: ' + fullname)
sys.meta_path.insert(0, RejectOptional())
"""


def test_core_and_composition_import_without_optional_sdk():
    script = (
        BLOCK_OPTIONAL_IMPORTS
        + """
import contracts.models, contracts.pipeline, contracts.interfaces, contracts.errors
import resolution.selection, resolution.recovery, resolution.annotation, resolution.entities
import runtime.resolver, runtime.pipeline, runtime.config
import bootstrap, transport.cli
import adapters.otter.provisioning
import threading
assert threading.active_count() == 1
from transport.cli import main
assert main(['identities']) == 0
"""
    )
    completed = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr


def test_model_pins_are_immutable():
    script = """
from model_specs import MODELS, MODEL_THRESHOLDS, RUNTIME_VERSIONS, BM_TOKENIZER_FILES
for mapping in (MODELS, MODEL_THRESHOLDS, RUNTIME_VERSIONS, BM_TOKENIZER_FILES):
    try:
        mapping['invalid'] = 'change'
    except TypeError:
        pass
    else:
        raise AssertionError('Mutable production model specs')
"""
    completed = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
