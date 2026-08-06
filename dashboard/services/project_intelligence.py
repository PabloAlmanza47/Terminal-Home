"""Read-only project intelligence and conservative setup planning.

The inspector deliberately has a small, fixed indicator set.  It never walks a
repository recursively (the bounded .NET lookup is the sole exception), never
follows indicator symlinks, and never evaluates project-provided commands.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

try:  # Python 3.10 compatibility
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore[no-redef]

from dashboard.services.project_commands import detect_project_commands
from dashboard.services.project_files import (
    is_directory,
    is_regular_file,
    load_json_object,
    read_bounded,
)

MAX_DOTNET_RESULTS = 32
MAX_DOTNET_DEPTH = 2


class Ecosystem(str, Enum):
    NODE = "Node.js"
    PYTHON = "Python"
    DOTNET = ".NET"


class FindingLevel(str, Enum):
    PASS = "PASS"
    WARN = "WARN"
    INFO = "INFO"
    FAIL = "FAIL"


class ActionKind(str, Enum):
    COMMAND = "command"
    COPY_FILE = "copy_file"


class ActionRisk(str, Enum):
    LOW = "low"
    MODERATE = "moderate"
    MANUAL = "manual"


@dataclass(frozen=True, slots=True)
class Evidence:
    source: str
    detail: str = ""

    def __str__(self) -> str:
        return f"{self.source}{(' ' + self.detail) if self.detail else ''}"


@dataclass(frozen=True, slots=True)
class InferredCommand:
    label: str
    argv: tuple[str, ...]
    evidence: tuple[Evidence, ...]


@dataclass(frozen=True, slots=True)
class ReadinessFinding:
    level: FindingLevel
    message: str
    evidence: tuple[Evidence, ...] = ()


@dataclass(frozen=True, slots=True)
class SetupAction:
    id: str
    kind: ActionKind
    description: str
    reason: str
    evidence: tuple[Evidence, ...]
    cwd: Path
    argv: tuple[str, ...] = ()
    source: Path | None = None
    destination: Path | None = None
    risk: ActionRisk = ActionRisk.MODERATE
    dependencies: tuple[str, ...] = ()
    executable: bool = True
    manual_only: bool = False


@dataclass(frozen=True, slots=True)
class ProjectIntelligence:
    name: str
    path: Path
    ecosystems: tuple[Ecosystem, ...]
    frameworks: tuple[str, ...]
    package_manager: str | None
    requested_runtimes: tuple[str, ...]
    commands: tuple[InferredCommand, ...]
    evidence: tuple[Evidence, ...]
    findings: tuple[ReadinessFinding, ...]
    setup_actions: tuple[SetupAction, ...]
    malformed_critical: bool = False


def _file(path: Path) -> bool:
    return is_regular_file(path)


def _directory(path: Path) -> bool:
    return is_directory(path)


def _read(path: Path) -> tuple[str | None, str | None]:
    return read_bounded(path)


def _json(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    return load_json_object(path)


def _toml(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    text, error = _read(path)
    if error or text is None:
        return None, error
    try:
        value = tomllib.loads(text)
    except (tomllib.TOMLDecodeError, ValueError) as exc:
        return None, f"{path.name} is malformed TOML: {type(exc).__name__}"
    return value, None


def _version_command(executable: str) -> str | None:
    try:
        result = subprocess.run(
            [executable, "--version"], capture_output=True, text=True, timeout=3, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    output = (result.stdout or result.stderr).strip().splitlines()
    return output[0][:120] if result.returncode == 0 and output else None


def _env_mapping(root: Path) -> tuple[tuple[Path, Path, Evidence], ...]:
    mappings = ((".env.example", ".env"), (".env.local.example", ".env.local"))
    result = []
    for source_name, target_name in mappings:
        source, target = root / source_name, root / target_name
        if _file(source) and not os.path.lexists(target):
            result.append((source, target, Evidence(source_name)))
    return tuple(result)


def _dotnet_files(root: Path) -> tuple[Path, ...]:
    found: list[Path] = []
    try:
        for current, dirs, files in os.walk(root, followlinks=False):
            current_path = Path(current)
            try:
                depth = len(current_path.relative_to(root).parts)
            except ValueError:
                continue
            dirs[:] = sorted(d for d in dirs if not (current_path / d).is_symlink())
            if depth > MAX_DOTNET_DEPTH:
                dirs[:] = []
                continue
            for filename in sorted(files):
                if filename.endswith((".csproj", ".fsproj", ".vbproj")):
                    candidate = current_path / filename
                    if _file(candidate):
                        found.append(candidate)
                        if len(found) >= MAX_DOTNET_RESULTS:
                            return tuple(found)
    except OSError:
        return tuple(found)
    return tuple(found)


def _target_frameworks(path: Path) -> tuple[str, ...]:
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError, UnicodeError):
        return ()
    values: list[str] = []
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] == "TargetFramework":
            if element.text and element.text.strip():
                values.extend(v.strip() for v in element.text.split(";"))
    return tuple(sorted(set(values)))


def _node(
    root: Path, evidence: list[Evidence], findings: list[ReadinessFinding]
) -> tuple[str | None, tuple[str, ...], tuple[str, ...], tuple[str, ...], bool]:
    package_path = root / "package.json"
    package, error = _json(package_path)
    if not _file(package_path):
        return None, (), (), (), False
    evidence.append(Evidence("package.json"))
    if error:
        findings.append(
            ReadinessFinding(
                FindingLevel.WARN,
                f"Cannot analyze package.json safely: {error}",
                (Evidence("package.json"),),
            )
        )
        return None, (), (), (), True
    assert package is not None
    lockfiles = (
        ("package-lock.json", "npm"),
        ("npm-shrinkwrap.json", "npm"),
        ("pnpm-lock.yaml", "pnpm"),
        ("yarn.lock", "yarn"),
        ("bun.lock", "bun"),
        ("bun.lockb", "bun"),
    )
    present = [(name, manager) for name, manager in lockfiles if _file(root / name)]
    for name, _ in present:
        evidence.append(Evidence(name))
    explicit = package.get("packageManager")
    explicit_manager = explicit.split("@", 1)[0] if isinstance(explicit, str) else None
    managers = {manager for _, manager in present}
    if explicit_manager in {"npm", "pnpm", "yarn", "bun"}:
        manager = explicit_manager
        if managers and managers != {manager}:
            findings.append(
                ReadinessFinding(
                    FindingLevel.WARN,
                    "package.json packageManager conflicts with lockfiles; "
                    "install choice requires manual review",
                    (Evidence("package.json packageManager"),),
                )
            )
    elif len(managers) == 1:
        manager = next(iter(managers))
    elif len(managers) > 1:
        manager = None
        findings.append(
            ReadinessFinding(
                FindingLevel.WARN,
                "Multiple package manager lockfiles found; resolve the conflict manually",
                tuple(Evidence(n) for n, _ in present),
            )
        )
    else:
        manager = "npm"
    raw_scripts = package.get("scripts")
    scripts: dict[str, Any] = raw_scripts if isinstance(raw_scripts, dict) else {}
    commands: list[str] = []
    for key in ("dev", "start", "test", "build"):
        if (
            isinstance(scripts.get(key), str)
            and scripts[key].strip()
            and "no test specified" not in scripts[key].lower()
        ):
            commands.append(key)
            evidence.append(Evidence(f"package.json scripts.{key}"))
    frameworks: list[str] = []
    for section in ("dependencies", "devDependencies"):
        section_deps = package.get(section)
        if not isinstance(section_deps, dict):
            continue
        for dep, label in (
            ("next", "Next.js"),
            ("react", "React"),
            ("prisma", "Prisma"),
            ("@prisma/client", "Prisma"),
        ):
            if dep in section_deps:
                frameworks.append(label)
                evidence.append(Evidence(f"package.json {section}.{dep}"))
    if _file(root / "prisma/schema.prisma"):
        frameworks.append("Prisma")
        evidence.append(Evidence("prisma/schema.prisma"))
    runtimes: list[str] = []
    for name in (".nvmrc", ".node-version"):
        text, _ = _read(root / name)
        if text and text.strip():
            runtimes.append(f"Node {text.strip()[:80]}")
            evidence.append(Evidence(name))
    engines = package.get("engines")
    if isinstance(engines, dict) and isinstance(engines.get("node"), str):
        runtimes.append(f"Node {engines['node'][:80]}")
        evidence.append(Evidence("package.json engines.node"))
    if isinstance(package.get("volta"), dict) and isinstance(package["volta"].get("node"), str):
        runtimes.append(f"Node {package['volta']['node'][:80]}")
        evidence.append(Evidence("package.json volta.node"))
    return (
        manager,
        tuple(sorted(set(frameworks))),
        tuple(commands),
        tuple(sorted(set(runtimes))),
        False,
    )


def inspect_project(project_path: Path) -> ProjectIntelligence:
    """Inspect one local directory without mutating it or running project code."""
    root = project_path.expanduser().resolve()
    evidence: list[Evidence] = []
    findings: list[ReadinessFinding] = []
    ecosystems: list[Ecosystem] = []
    frameworks: list[str] = []
    runtimes: list[str] = []
    commands: list[InferredCommand] = []
    malformed = False

    manager, node_frameworks, node_script_names, node_runtimes, malformed_node = _node(
        root, evidence, findings
    )
    runtimes.extend(node_runtimes)
    if _file(root / "package.json"):
        ecosystems.append(Ecosystem.NODE)
        frameworks.extend(node_frameworks)
        node_available = shutil.which("node")
        if node_available:
            version = _version_command(node_available)
            findings.append(
                ReadinessFinding(
                    FindingLevel.PASS,
                    f"Node.js is available ({version or node_available})",
                    (Evidence("PATH node"),),
                )
            )
            requested = next(
                (
                    match.group(1)
                    for runtime in runtimes
                    if (match := re.search(r"(?:Node\s+)?(?:\^|~|>=|>|=)?(\d+)", runtime))
                ),
                None,
            )
            installed = re.search(r"(?:v)?(\d+)", version or "")
            if requested and installed and requested != installed.group(1):
                findings.append(
                    ReadinessFinding(
                        FindingLevel.WARN,
                        f"Installed Node.js major {installed.group(1)} does not match "
                        f"requested {requested}",
                        (Evidence("Node runtime declaration"), Evidence("PATH node")),
                    )
                )
        else:
            findings.append(
                ReadinessFinding(
                    FindingLevel.FAIL,
                    "Node.js is not available on PATH",
                    (Evidence("package.json"),),
                )
            )
        if manager:
            pm_path = shutil.which(manager)
            findings.append(
                ReadinessFinding(
                    FindingLevel.PASS if pm_path else FindingLevel.FAIL,
                    f"Package manager {manager} is {'available' if pm_path else 'not available'}",
                    (Evidence("package manager selection"),),
                )
            )
        if _directory(root / "node_modules"):
            findings.append(
                ReadinessFinding(
                    FindingLevel.PASS, "node_modules is present", (Evidence("node_modules"),)
                )
            )
        else:
            findings.append(
                ReadinessFinding(
                    FindingLevel.WARN, "node_modules is missing", (Evidence("node_modules"),)
                )
            )
        for name in node_script_names:
            cmd: tuple[str, ...] = (manager or "npm", "run", name)
            if manager == "yarn":
                cmd = (manager, name)
            if manager == "bun":
                cmd = (manager, "run", name)
            if name == "test" and manager in {"npm", "pnpm"}:
                cmd = (manager, "test")
            if name == "start" and manager in {"npm", "pnpm", "yarn"}:
                cmd = (manager, "start")
            commands.append(
                InferredCommand(
                    {
                        "dev": "Development",
                        "start": "Development",
                        "test": "Test",
                        "build": "Build",
                    }[name],
                    cmd,
                    tuple(e for e in evidence if f"scripts.{name}" in e.source),
                )
            )
        malformed = malformed or malformed_node

    pyproject, py_error = _toml(root / "pyproject.toml")
    python_indicators = _file(root / "pyproject.toml") or any(
        _file(root / n)
        for n in (
            "requirements.txt",
            "requirements-dev.txt",
            "setup.cfg",
            "setup.py",
            "manage.py",
            "pytest.ini",
            "tox.ini",
            ".python-version",
        )
    )
    if python_indicators:
        ecosystems.append(Ecosystem.PYTHON)
        if _file(root / "pyproject.toml"):
            evidence.append(Evidence("pyproject.toml"))
            if py_error:
                findings.append(
                    ReadinessFinding(
                        FindingLevel.WARN,
                        f"Cannot analyze pyproject.toml safely: {py_error}",
                        (Evidence("pyproject.toml"),),
                    )
                )
        for name in (
            "requirements.txt",
            "requirements-dev.txt",
            "setup.cfg",
            "setup.py",
            "manage.py",
            "pytest.ini",
            "tox.ini",
        ):
            if _file(root / name):
                evidence.append(Evidence(name))
        project_section = pyproject.get("project", {}) if isinstance(pyproject, dict) else {}
        if isinstance(project_section, dict) and isinstance(
            project_section.get("requires-python"), str
        ):
            runtimes.append(f"Python {project_section['requires-python'][:80]}")
            evidence.append(Evidence("pyproject.toml project.requires-python"))
        version_file, _ = _read(root / ".python-version")
        if version_file and version_file.strip():
            runtimes.append(f"Python {version_file.strip()[:80]}")
        python = shutil.which("python3") or sys.executable
        version = _version_command(python)
        findings.append(
            ReadinessFinding(
                FindingLevel.PASS,
                f"Python is available ({version or python})",
                (Evidence("PATH python3"),),
            )
        )
        venv = root / ".venv"
        findings.append(
            ReadinessFinding(
                FindingLevel.PASS if _directory(venv) else FindingLevel.WARN,
                "Local .venv is present" if _directory(venv) else "Local .venv is missing",
                (Evidence(".venv"),),
            )
        )
        if _file(root / "manage.py"):
            frameworks.append("Django")
            evidence.append(Evidence("manage.py"))
        text, _ = _read(root / "pyproject.toml")
        dep_text = text or ""
        if "flask" in dep_text.lower():
            frameworks.append("Flask")
        if "fastapi" in dep_text.lower():
            frameworks.append("FastAPI")
        existing = detect_project_commands(root)
        if existing.development and not any(c.label == "Development" for c in commands):
            commands.append(
                InferredCommand(
                    "Development",
                    tuple(existing.development.command.split()),
                    (Evidence(existing.development.source.value),),
                )
            )
        if existing.test and not any(c.label == "Test" for c in commands):
            commands.append(
                InferredCommand(
                    "Test",
                    tuple(existing.test.command.split()),
                    (Evidence(existing.test.source.value),),
                )
            )

    solution_files = tuple(
        sorted(p for suffix in ("*.sln", "*.slnx") for p in root.glob(suffix) if _file(p))
    )
    project_files = tuple(
        sorted(
            p
            for suffix in ("*.csproj", "*.fsproj", "*.vbproj")
            for p in root.glob(suffix)
            if _file(p)
        )
    )
    if solution_files and not project_files:
        project_files = _dotnet_files(root)
    if solution_files or project_files or _file(root / "global.json"):
        ecosystems.append(Ecosystem.DOTNET)
        for path in (
            *solution_files,
            *project_files,
            (root / "global.json",) if _file(root / "global.json") else (),
        ):
            if isinstance(path, tuple):
                continue
            evidence.append(Evidence(str(path.relative_to(root))))
        frameworks.extend(
            sorted({framework for p in project_files for framework in _target_frameworks(p)})
        )
        for project_file in project_files:
            xml_text, xml_error = _read(project_file)
            if xml_error:
                findings.append(
                    ReadinessFinding(
                        FindingLevel.WARN,
                        f"Cannot read {project_file.name} safely: {xml_error}",
                        (Evidence(str(project_file.relative_to(root))),),
                    )
                )
            elif xml_text is not None:
                try:
                    ET.fromstring(xml_text)
                except ET.ParseError:
                    findings.append(
                        ReadinessFinding(
                            FindingLevel.WARN,
                            f"{project_file.name} is malformed XML",
                            (Evidence(str(project_file.relative_to(root))),),
                        )
                    )
        global_json, error = _json(root / "global.json")
        if error:
            findings.append(
                ReadinessFinding(
                    FindingLevel.WARN,
                    f"Cannot analyze global.json safely: {error}",
                    (Evidence("global.json"),),
                )
            )
        if (
            isinstance(global_json, dict)
            and isinstance(global_json.get("sdk"), dict)
            and isinstance(global_json["sdk"].get("version"), str)
        ):
            runtimes.append(f".NET SDK {global_json['sdk']['version'][:80]}")
        dotnet = shutil.which("dotnet")
        if dotnet:
            findings.append(
                ReadinessFinding(
                    FindingLevel.PASS,
                    f"dotnet is available ({_version_command(dotnet) or dotnet})",
                    (Evidence("PATH dotnet"),),
                )
            )
        else:
            findings.append(
                ReadinessFinding(
                    FindingLevel.FAIL,
                    "dotnet is not available on PATH",
                    (Evidence(".NET project file"),),
                )
            )
        restore_target: Path | None = (
            solution_files[0]
            if len(solution_files) == 1
            else (project_files[0] if len(project_files) == 1 else None)
        )
        if len(project_files) > 1 and not solution_files:
            findings.append(
                ReadinessFinding(
                    FindingLevel.WARN,
                    "Multiple .NET projects found; choose a runnable project manually",
                    tuple(Evidence(str(p.relative_to(root))) for p in project_files),
                )
            )
        if restore_target:
            commands.extend(
                (
                    InferredCommand(
                        "Build",
                        ("dotnet", "build", str(restore_target.relative_to(root))),
                        (Evidence(str(restore_target.relative_to(root))),),
                    ),
                    InferredCommand(
                        "Test",
                        ("dotnet", "test", str(restore_target.relative_to(root))),
                        (Evidence(str(restore_target.relative_to(root))),),
                    ),
                )
            )
            if not _file(restore_target.parent / "obj/project.assets.json"):
                findings.append(
                    ReadinessFinding(
                        FindingLevel.WARN,
                        ".NET restore evidence is missing",
                        (Evidence("obj/project.assets.json"),),
                    )
                )

    for source, target, ev in _env_mapping(root):
        findings.append(
            ReadinessFinding(
                FindingLevel.WARN, f"{target.name} is missing; {source.name} exists", (ev,)
            )
        )
    if not ecosystems:
        findings.append(
            ReadinessFinding(
                FindingLevel.INFO,
                "No supported ecosystem was confidently detected",
                tuple(
                    Evidence(n)
                    for n in (
                        "package.json",
                        "pyproject.toml",
                        "requirements.txt",
                        "*.sln",
                        "*.csproj",
                    )
                ),
            )
        )
    if not evidence:
        findings.append(
            ReadinessFinding(
                FindingLevel.INFO, "Supported root-level indicators checked; none were found"
            )
        )

    actions = build_setup_plan(
        ProjectIntelligence(
            root.name,
            root,
            tuple(sorted(set(ecosystems), key=lambda e: e.value)),
            tuple(sorted(set(frameworks))),
            manager,
            tuple(sorted(set(runtimes))),
            tuple(commands),
            tuple(sorted(set(evidence), key=str)),
            tuple(findings),
            (),
            malformed,
        )
    )
    return ProjectIntelligence(
        root.name,
        root,
        tuple(sorted(set(ecosystems), key=lambda e: e.value)),
        tuple(sorted(set(frameworks))),
        manager,
        tuple(sorted(set(runtimes))),
        tuple(commands),
        tuple(sorted(set(evidence), key=str)),
        tuple(findings),
        actions,
        malformed,
    )


def build_setup_plan(info: ProjectIntelligence) -> tuple[SetupAction, ...]:
    """Build only evidence-backed local actions; no action runs here."""
    actions: list[SetupAction] = []
    root = info.path
    node_missing = any(f.message.startswith("node_modules is missing") for f in info.findings)
    manager_available = info.package_manager and shutil.which(info.package_manager)
    conflict = any(
        "Multiple package manager" in f.message or "conflicts" in f.message for f in info.findings
    )
    if (
        Ecosystem.NODE in info.ecosystems
        and node_missing
        and manager_available
        and not conflict
        and info.package_manager
    ):
        lock_name = "package-lock.json" if info.package_manager == "npm" else "pnpm-lock.yaml"
        valid_npm_lock = info.package_manager != "npm" or _json(root / lock_name)[0] is not None
        argv = (
            (info.package_manager, "ci")
            if info.package_manager in {"npm", "pnpm"}
            and _file(root / lock_name)
            and valid_npm_lock
            else ((info.package_manager, "install"))
        )
        actions.append(
            SetupAction(
                "node-dependencies",
                ActionKind.COMMAND,
                "Install Node.js dependencies",
                "node_modules is missing; package lifecycle hooks may run",
                tuple(e for e in info.evidence if "lock" in e.source or e.source == "package.json"),
                root,
                argv,
                risk=ActionRisk.MODERATE,
            )
        )
    for source, target, evidence in _env_mapping(root):
        actions.append(
            SetupAction(
                f"copy-{target.name}",
                ActionKind.COPY_FILE,
                f"Create {target.name} from {source.name}",
                "recognized environment example mapping; target does not exist",
                (evidence,),
                root,
                source=source,
                destination=target,
                risk=ActionRisk.LOW,
            )
        )
    prisma_declared = any(
        evidence.source.startswith(
            (
                "package.json dependencies.prisma",
                "package.json dependencies.@prisma/client",
                "package.json devDependencies.prisma",
                "package.json devDependencies.@prisma/client",
            )
        )
        for evidence in info.evidence
    )
    prisma_binary = root / "node_modules/.bin/prisma"
    node_install_planned = any(action.id == "node-dependencies" for action in actions)
    if (
        prisma_declared
        and _file(root / "prisma/schema.prisma")
        and (_file(prisma_binary) or node_install_planned)
    ):
        actions.append(
            SetupAction(
                "prisma-generate",
                ActionKind.COMMAND,
                "Generate Prisma client",
                "Prisma schema and local Prisma executable detected",
                (Evidence("prisma/schema.prisma"), Evidence("node_modules/.bin/prisma")),
                root,
                (str(prisma_binary), "generate"),
                risk=ActionRisk.MODERATE,
                dependencies=("node-dependencies",) if node_missing else (),
            )
        )
    venv_path = root / ".venv"
    venv_absent = not os.path.lexists(venv_path)
    if Ecosystem.PYTHON in info.ecosystems and venv_absent and shutil.which("python3"):
        actions.append(
            SetupAction(
                "python-venv",
                ActionKind.COMMAND,
                "Create local Python virtual environment",
                "no local .venv was detected",
                (Evidence(".venv"),),
                root,
                (shutil.which("python3") or sys.executable, "-m", "venv", ".venv"),
                risk=ActionRisk.MODERATE,
            )
        )
    if (
        Ecosystem.PYTHON in info.ecosystems
        and _file(root / "requirements.txt")
        and (_directory(venv_path) or venv_absent)
        and shutil.which("python3")
    ):
        actions.append(
            SetupAction(
                "python-requirements",
                ActionKind.COMMAND,
                "Install Python requirements into .venv",
                "requirements.txt is present and a project-local environment is selected",
                (Evidence("requirements.txt"), Evidence(".venv")),
                root,
                (str(root / ".venv/bin/python"), "-m", "pip", "install", "-r", "requirements.txt"),
                risk=ActionRisk.MODERATE,
                dependencies=("python-venv",) if venv_absent else (),
            )
        )
    if (
        Ecosystem.DOTNET in info.ecosystems
        and shutil.which("dotnet")
        and any("restore evidence is missing" in f.message for f in info.findings)
    ):
        targets = tuple(
            p
            for p in root.iterdir()
            if p.suffix in {".sln", ".slnx", ".csproj", ".fsproj", ".vbproj"} and _file(p)
        )
        solution_targets = tuple(p for p in targets if p.suffix in {".sln", ".slnx"})
        restore_target: Path | None = (
            solution_targets[0]
            if len(solution_targets) == 1
            else (targets[0] if len(targets) == 1 else None)
        )
        if restore_target:
            actions.append(
                SetupAction(
                    "dotnet-restore",
                    ActionKind.COMMAND,
                    "Restore .NET dependencies",
                    "restore evidence is missing",
                    (Evidence(restore_target.name),),
                    root,
                    ("dotnet", "restore", restore_target.name),
                    risk=ActionRisk.MODERATE,
                )
            )
    return tuple(actions)


def execute_setup_action(action: SetupAction) -> tuple[bool, str]:
    """Execute one already-approved action using argv, never a shell string."""
    if action.kind is ActionKind.COPY_FILE:
        assert action.source is not None and action.destination is not None
        root = action.cwd.resolve()
        source = action.source
        destination = action.destination
        try:
            source.resolve().relative_to(root)
            destination.parent.resolve().relative_to(root)
        except (OSError, ValueError):
            return False, "copy paths must remain inside the project root"
        if not _file(source):
            return False, "source is missing or is a symlink"
        if os.path.lexists(destination):
            return False, "destination already exists"
        destination.write_bytes(source.read_bytes())
        return True, f"copied {source.name} to {destination.name}"
    result = subprocess.run(list(action.argv), cwd=action.cwd, check=False)
    return result.returncode == 0, f"exit code {result.returncode}"


def shell_display(argv: tuple[str, ...]) -> str:
    """Quote argv for display only; callers must execute ``argv`` itself."""
    import shlex

    return shlex.join(argv)


def format_project_report(info: ProjectIntelligence) -> str:
    lines = [
        "Terminal Home project doctor",
        "",
        f"Project: {info.name}",
        f"Path: {info.path}",
        "",
        "Detected",
    ]
    lines.append(f"  Ecosystem: {', '.join(e.value for e in info.ecosystems) or 'Unknown'}")
    if info.frameworks:
        lines.append(f"  Frameworks: {', '.join(info.frameworks)}")
    if info.package_manager:
        lines.append(f"  Package manager: {info.package_manager}")
    if info.requested_runtimes:
        lines.append(f"  Requested runtime: {', '.join(info.requested_runtimes)}")
    lines.append("\nCommands")
    for command in info.commands:
        lines.append(f"  {command.label}: {shell_display(command.argv)}")
        lines.append(
            f"    Evidence: {', '.join(map(str, command.evidence)) or 'fixed project indicator'}"
        )
    lines.append("\nReadiness")
    for finding in info.findings:
        detail = f"; evidence: {', '.join(map(str, finding.evidence))}" if finding.evidence else ""
        lines.append(f"{finding.level.value:<4}  {finding.message}{detail}")
    lines.append("\nSuggested setup")
    if info.setup_actions:
        for index, action in enumerate(info.setup_actions, 1):
            lines.append(f"  {index}. {action.description}")
            lines.append(f"     Why: {action.reason} ({action.risk.value} effect)")
            lines.append(f"     Evidence: {', '.join(map(str, action.evidence))}")
            if action.kind is ActionKind.COMMAND:
                lines.append(f"     {shell_display(action.argv)}")
            elif action.source and action.destination:
                lines.append(f"     Copy {action.source.name} -> {action.destination.name}")
    else:
        lines.append("  No automated actions proposed.")
    return "\n".join(lines)


def project_exit_code(info: ProjectIntelligence) -> int:
    return (
        1
        if info.malformed_critical or any(f.level is FindingLevel.FAIL for f in info.findings)
        else 0
    )
