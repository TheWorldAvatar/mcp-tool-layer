import ast
import tempfile
from pathlib import Path

from src.agents.scripts_and_prompts_generation.direct_script_generation import (
    _validate_relationships_script_output,
    extract_functions_from_underlying,
    _build_main_py_deterministic,
)


def test_relationship_interface_annotation_ok_and_bad():
    tmp = tempfile.TemporaryDirectory()
    outdir = tmp.name
    # Minimal base module (not imported; only used by validator when scanning imports)
    Path(outdir, 'Test_creation_base.py').write_text(
        'NAMESPACE = None\n'  # placeholder; validator only inspects names when imported-from
        'def _format_error(x, **k):\n    return x\n'
        'def _format_success_json(a,b, created=False, **k):\n    return b\n',
        encoding='utf-8',
    )

    # Accept: object_iri is Annotated[str, Field(description=...)] and mentions create_Target
    code_ok = (
        'from typing import Annotated\n'
        'from pydantic import Field\n'
        'def add_relatesTo(subject_iri: str, object_iri: Annotated[str, Field(description="absolute IRI returned by create_Target; never a label or plain text")]) -> str:\n'
        '    """Link Source to Target; object_iri must be an absolute IRI returned by create_Target (not a label)."""\n'
        '    return ""\n'
    )
    ok, err = _validate_relationships_script_output(
        code=code_ok,
        ontology_name='Test',
        output_dir=outdir,
        concise_content="",
        expected_relationship_props=['relatesTo'],
        expected_relationship_contracts={'relatesTo': {'internal_targets': ['Target'], 'external_targets': []}},
    )
    assert ok, err

    # Reject: plain str without Annotated/Field description
    code_bad = (
        'def add_relatesTo(subject_iri: str, object_iri: str) -> str:\n'
        '    """Link."""\n'
        '    return ""\n'
    )
    ok2, err2 = _validate_relationships_script_output(
        code=code_bad,
        ontology_name='Test',
        output_dir=outdir,
        concise_content="",
        expected_relationship_props=['relatesTo'],
        expected_relationship_contracts={'relatesTo': {'internal_targets': ['Target'], 'external_targets': []}},
    )
    assert not ok2 and 'object_iri' in err2

    tmp.cleanup()


def test_extract_functions_retains_docstring():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td, 'Test_creation_relationships.py')
        p.write_text(
            'def add_relatesTo(a: str, b: str) -> str:\n'
            '    """First line.\n\nSecond line."""\n'
            '    return ""\n',
            encoding='utf-8',
        )
        fns = extract_functions_from_underlying(str(p))
        item = next((x for x in fns if x['name'] == 'add_relatesTo'), None)
        assert item is not None
        assert 'First line.' in item.get('docstring', '')
        assert 'Second line.' in item.get('docstring', '')


def test_deterministic_main_preserves_annotations_and_docstrings():
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        # Minimal underlying modules
        Path(td_path, 'Test_creation_base.py').write_text(
            'def init_memory_wrapper(a=None,b=None):\n    return ""\n'
            'def export_memory_wrapper():\n    return ""\n',
            encoding='utf-8',
        )
        Path(td_path, 'Test_creation_checks.py').write_text('', encoding='utf-8')
        rel_src = (
            'from typing import Annotated\n'
            'from pydantic import Field\n'
            'def add_relatesTo(subject_iri: str, object_iri: Annotated[str, Field(description="absolute IRI returned by create_Target; never a label or plain text")]) -> str:\n'
            '    """Relates; object_iri must be absolute IRI from create_Target."""\n'
            '    return ""\n'
        )
        Path(td_path, 'Test_creation_relationships.py').write_text(rel_src, encoding='utf-8')

        main_path = _build_main_py_deterministic(
            ontology_name='Test',
            checks_script_path=str(Path(td_path, 'Test_creation_checks.py')),
            relationships_script_path=str(Path(td_path, 'Test_creation_relationships.py')),
            base_script_path=str(Path(td_path, 'Test_creation_base.py')),
            entity_script_paths=[],
            output_dir=str(td_path),
        )
        main_code = Path(main_path).read_text(encoding='utf-8')
        # Annotation imports present
        assert 'from typing import Optional, Annotated' in main_code
        assert 'from pydantic import Field' in main_code
        # Wrapper preserves signature and docstring
        tree = ast.parse(main_code)
        wrapper = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == 'add_relatesTo'
        )
        object_arg = next(arg for arg in wrapper.args.args if arg.arg == 'object_iri')
        annotation = ast.unparse(object_arg.annotation)
        assert 'Annotated[str, Field(' in annotation
        assert 'absolute IRI returned by create_Target' in annotation
        assert 'never a label or plain text' in annotation
        assert ast.get_docstring(wrapper) == (
            'Relates; object_iri must be absolute IRI from create_Target.'
        )
        # No varargs fallback wrappers
        assert '*args' not in main_code and '**kwargs' not in main_code
