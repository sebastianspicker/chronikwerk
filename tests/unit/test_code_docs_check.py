"""Verify the maintained-code documentation policy and its intentional test exemptions."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType


def _load_checker(repo_root: Path) -> ModuleType:
    """Load the CI checker with an isolated repository root for fixture-based tests."""
    script = Path(__file__).resolve().parents[2] / "scripts" / "ci" / "check_code_docs.py"
    spec = importlib.util.spec_from_file_location("check_code_docs_fixture", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.__dict__["REPO_ROOT"] = repo_root
    return module


def test_python_policy_requires_module_and_public_docs(tmp_path: Path) -> None:
    checker = _load_checker(tmp_path)
    source = tmp_path / "src" / "chronikwerk" / "example.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "def public_api():\n    return 1\n\ndef _private_helper():\n    return 2\n",
        encoding="utf-8",
    )

    errors = checker._python_errors(source)

    assert any("module lacks" in error for error in errors)
    assert any("public public_api lacks" in error for error in errors)
    assert not any("_private_helper" in error for error in errors)

    source.write_text(
        '"""Explain this maintained module."""\n'
        "\n"
        "def public_api():\n"
        '    """Expose the supported behavior."""\n'
        "    return 1\n"
        "\n"
        "def _private_helper():\n"
        "    return 2\n",
        encoding="utf-8",
    )
    assert checker._python_errors(source) == []


def test_test_policy_documents_helpers_without_repeating_test_names(tmp_path: Path) -> None:
    checker = _load_checker(tmp_path)
    source = tmp_path / "tests" / "unit" / "test_example.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        '"""Verify the example contract."""\n'
        "\n"
        "def fixture_builder():\n"
        "    return object()\n"
        "\n"
        "def test_contract_is_visible_from_its_name():\n"
        "    assert True\n",
        encoding="utf-8",
    )

    errors = checker._python_errors(source)

    assert any("test helper fixture_builder lacks" in error for error in errors)
    assert not any("test_contract_is_visible_from_its_name" in error for error in errors)


def test_generated_placeholder_phrases_are_rejected(tmp_path: Path) -> None:
    checker = _load_checker(tmp_path)
    source = tmp_path / "tests" / "unit" / "test_example.py"
    source.parent.mkdir(parents=True)
    placeholders = (
        "Regression coverage for example contracts and failure boundaries.",
        "Register mock routes for ticket.",
        "Install mock behavior for ticket.",
        "Assert the expected  error note.",
    )

    for placeholder in placeholders:
        source.write_text(
            f'"""{placeholder}"""\n\ndef test_example():\n    assert True\n',
            encoding="utf-8",
        )
        errors = checker._python_errors(source)
        assert any("uses placeholder documentation" in error for error in errors)


def test_typescript_and_shell_files_need_purpose_headers(tmp_path: Path) -> None:
    checker = _load_checker(tmp_path)
    typescript = tmp_path / "frontend" / "admin.ts"
    shell = tmp_path / "scripts" / "check.sh"
    typescript.parent.mkdir(parents=True)
    shell.parent.mkdir(parents=True)
    typescript.write_text("export const ready = true;\n", encoding="utf-8")
    shell.write_text("#!/usr/bin/env bash\nset -euo pipefail\n", encoding="utf-8")

    assert checker._header_errors([typescript], ("//", "/*"))
    assert checker._header_errors([shell], ("#",))

    typescript.write_text("// Explain why this frontend entry point exists.\n", encoding="utf-8")
    shell.write_text(
        "#!/usr/bin/env bash\n# Explain why this release check exists.\nset -euo pipefail\n",
        encoding="utf-8",
    )
    assert checker._header_errors([typescript], ("//", "/*")) == []
    assert checker._header_errors([shell], ("#",)) == []
