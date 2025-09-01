#!/usr/bin/env python3
"""Check test suite compliance for the ClearFlow project.

This script enforces test-specific best practices to ensure proper test isolation
and prevent resource leaks that can cause test failures.

Requirements enforced:
- TEST001: Tests SHALL NOT use asyncio.run() (use @pytest.mark.asyncio)
- TEST002: Tests SHALL NOT create event loops without proper cleanup
- TEST003: Tests SHALL NOT use asyncio.get_event_loop() (let pytest-asyncio manage)
- TEST005: Tests SHALL properly close all resources (files, sockets, connections)

Why these matter:
- asyncio.run() creates new event loops that may leave resources unclosed
- Unclosed resources cause ResourceWarnings that fail subsequent tests
- pytest-asyncio properly manages event loop lifecycle between tests
- Test isolation is critical for reliable test suites
"""

import ast
import sys
from pathlib import Path
from typing import NamedTuple


class Violation(NamedTuple):
    """Test suite compliance violation details."""

    file: Path
    line: int
    column: int
    code: str
    message: str
    suggestion: str


def is_test_file(file_path: Path) -> bool:
    """Check if a file is a test file.

    Returns:
        True if the file is in tests/ directory or matches test patterns.

    """
    # Check if in tests directory
    if "tests" in file_path.parts:
        return True

    # Check filename patterns
    name = file_path.name
    return name.startswith("test_") or name.endswith("_test.py")


def check_asyncio_run(file_path: Path, content: str) -> tuple[Violation, ...]:
    """Check for asyncio.run() usage in tests.

    Returns:
        List of violations for asyncio.run() usage.

    """
    violations = []

    if not is_test_file(file_path):
        return tuple(violations)

    try:
        tree = ast.parse(content, filename=str(file_path))
    except SyntaxError:
        return tuple(violations)

    for node in ast.walk(tree):
        # Check for asyncio.run() calls
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute):
                if (
                    isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "asyncio"
                    and node.func.attr == "run"
                ):
                    violations.append(
                        Violation(
                            file=file_path,
                            line=node.lineno,
                            column=node.col_offset,
                            code="TEST001",
                            message="Using asyncio.run() in tests causes resource leaks",
                            suggestion="Use @pytest.mark.asyncio and await instead",
                        )
                    )

    return tuple(violations)


def check_event_loop_creation(file_path: Path, content: str) -> tuple[Violation, ...]:
    """Check for manual event loop creation without cleanup.

    Returns:
        List of violations for event loop creation.

    """
    violations = []

    if not is_test_file(file_path):
        return tuple(violations)

    try:
        tree = ast.parse(content, filename=str(file_path))
    except SyntaxError:
        return tuple(violations)

    # Track variables that are event loops
    loop_vars = set()

    for node in ast.walk(tree):
        # Check for new_event_loop() calls
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute):
                if (
                    isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "asyncio"
                    and node.func.attr == "new_event_loop"
                ):
                    # Check if it's assigned to a variable
                    parent = getattr(node, "_parent", None)
                    if isinstance(parent, ast.Assign):
                        for target in parent.targets:
                            if isinstance(target, ast.Name):
                                loop_vars.add(target.id)

                    # Check if there's proper cleanup (look for try/finally)
                    # This is a simplified check - could be more sophisticated
                    has_cleanup = False
                    for other_node in ast.walk(tree):
                        if isinstance(other_node, ast.Try):
                            # Check if the loop creation is in the try block
                            for stmt in ast.walk(other_node):
                                if stmt == node:
                                    # Check if finally block has close()
                                    for final_stmt in other_node.finalbody:
                                        if isinstance(final_stmt, ast.Expr):
                                            if isinstance(final_stmt.value, ast.Call):
                                                if isinstance(
                                                    final_stmt.value.func, ast.Attribute
                                                ):
                                                    if (
                                                        final_stmt.value.func.attr
                                                        == "close"
                                                    ):
                                                        has_cleanup = True

                    if not has_cleanup:
                        violations.append(
                            Violation(
                                file=file_path,
                                line=node.lineno,
                                column=node.col_offset,
                                code="TEST002",
                                message="Creating event loop without proper cleanup",
                                suggestion="Use @pytest.mark.asyncio or ensure try/finally with loop.close()",
                            )
                        )

        # Check for get_event_loop() calls
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute):
                if (
                    isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "asyncio"
                    and node.func.attr == "get_event_loop"
                ):
                    violations.append(
                        Violation(
                            file=file_path,
                            line=node.lineno,
                            column=node.col_offset,
                            code="TEST003",
                            message="Using asyncio.get_event_loop() in tests",
                            suggestion="Let pytest-asyncio manage the event loop",
                        )
                    )

    return tuple(violations)


# TEST004 check removed - we use asyncio_mode="auto" in pyproject.toml
# which automatically detects async tests without needing explicit decorators


def check_resource_management(file_path: Path, content: str) -> tuple[Violation, ...]:
    """Check for potential resource leaks in tests.

    Returns:
        List of violations for resource management issues.

    """
    violations = []

    if not is_test_file(file_path):
        return tuple(violations)

    try:
        tree = ast.parse(content, filename=str(file_path))
    except SyntaxError:
        return tuple(violations)

    # Track open() calls without context managers
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            # Check for open() calls
            if isinstance(node.func, ast.Name) and node.func.id == "open":
                # Check if it's in a with statement
                in_with = False
                for other_node in ast.walk(tree):
                    if isinstance(other_node, ast.With):
                        for item in other_node.items:
                            if item.context_expr == node:
                                in_with = True
                                break

                if not in_with:
                    violations.append(
                        Violation(
                            file=file_path,
                            line=node.lineno,
                            column=node.col_offset,
                            code="TEST005",
                            message="File opened without context manager may leak resources",
                            suggestion="Use 'with open(...) as f:' pattern",
                        )
                    )

    return tuple(violations)


def check_file(file_path: Path) -> tuple[Violation, ...]:
    """Check a single file for test suite compliance violations.

    Returns:
        List of all violations found in the file.

    """
    violations = []

    # Only check Python test files
    if file_path.suffix != ".py":
        return tuple(violations)

    if not is_test_file(file_path):
        return tuple(violations)

    try:
        content = file_path.read_text(encoding="utf-8")
    except Exception as e:
        print(f"Warning: Could not read {file_path}: {e}", file=sys.stderr)
        return tuple(violations)

    # Skip __pycache__ files
    if "__pycache__" in str(file_path):
        return tuple(violations)

    # Run all checks
    violations.extend(check_asyncio_run(file_path, content))
    violations.extend(check_event_loop_creation(file_path, content))
    # TEST004 removed - asyncio_mode="auto" handles this
    violations.extend(check_resource_management(file_path, content))

    return tuple(violations)


def scan_directory(directory: Path) -> tuple[Violation, ...]:
    """Scan directory recursively for test files.

    Returns:
        List of all violations found in test files within the directory.

    """
    violations = []

    if not directory.exists():
        return tuple(violations)

    for file_path in directory.rglob("*.py"):
        if is_test_file(file_path):
            violations.extend(check_file(file_path))

    return tuple(violations)


def print_report(violations: tuple[Violation, ...]) -> None:
    """Print detailed violation report."""
    if not violations:
        print("✅ No test suite compliance violations found!")
        return

    print(f"\n🚨 TEST SUITE COMPLIANCE VIOLATIONS: {len(violations)}")
    print("=" * 70)

    # Group by code
    by_code = {}
    for v in violations:
        if v.code not in by_code:
            by_code[v.code] = []
        by_code[v.code].append(v)

    code_descriptions = {
        "TEST001": "asyncio.run() in tests (causes resource leaks)",
        "TEST002": "Manual event loop creation without cleanup",
        "TEST003": "Using asyncio.get_event_loop() in tests",
        "TEST005": "Potential resource leak (unclosed file/socket)",
    }

    for code, code_violations in sorted(by_code.items()):
        description = code_descriptions.get(code, "Unknown violation")
        print(f"\n{code}: {description} ({len(code_violations)} violations)")
        print("-" * 70)

        for v in sorted(code_violations, key=lambda x: (x.file, x.line)):
            file_path = str(v.file).replace(str(Path.cwd()) + "/", "")
            print(f"  {file_path}:{v.line}:{v.column}")
            print(f"    Problem: {v.message}")
            print(f"    Fix: {v.suggestion}")

    print("\n" + "=" * 70)
    print("WHY THESE MATTER:")
    print("  • asyncio.run() creates event loops that may leave resources unclosed")
    print("  • Unclosed resources cause ResourceWarnings that fail other tests")
    print("  • Test isolation is CRITICAL for reliable test suites")
    print("  • pytest-asyncio properly manages event loop lifecycle")

    print("\nBEST PRACTICES:")
    print("  ✅ Use @pytest.mark.asyncio for async tests")
    print("  ✅ Let pytest-asyncio manage event loops")
    print("  ✅ Use context managers for resources (with open() as f:)")
    print("  ✅ Never use asyncio.run() in test suites")

    print("\n" + "🚨" * 35)
    print("TEST ISOLATION VIOLATIONS DETECTED!")
    print("These violations can cause:")
    print("  • Tests that pass alone but fail when run together")
    print("  • ResourceWarnings and 'unclosed event loop' errors")
    print("  • Flaky, unreliable test suites")
    print("✅ FIX IMMEDIATELY: Follow the suggestions above")
    print("🚨" * 35)


def main() -> None:
    """Execute the test suite compliance check."""
    # Get items from command line arguments, or use default
    items = sys.argv[1:] if len(sys.argv) > 1 else ["tests"]

    all_violations = []
    for item in items:
        path = Path(item)
        if path.is_file() and path.suffix == ".py":
            # Single Python file
            violations = check_file(path)
            all_violations.extend(violations)
        elif path.is_dir():
            # Directory - scan recursively
            violations = scan_directory(path)
            all_violations.extend(violations)
        else:
            print(f"Warning: {item} is not a Python file or directory, skipping")
            continue

    # Print report
    print_report(all_violations)

    # Exit with appropriate code
    if all_violations:
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == "__main__":
    main()
