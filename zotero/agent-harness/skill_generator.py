"""
SKILL.md Generator for CLI-Anything harnesses.
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


def _format_display_name(name: str) -> str:
    return name.replace("_", " ").replace("-", " ").title()


@dataclass
class CommandInfo:
    name: str
    description: str


@dataclass
class CommandGroup:
    name: str
    description: str
    commands: list[CommandInfo] = field(default_factory=list)


@dataclass
class Example:
    title: str
    description: str
    code: str


@dataclass
class SkillMetadata:
    skill_name: str
    skill_description: str
    software_name: str
    skill_intro: str
    version: str
    command_groups: list[CommandGroup] = field(default_factory=list)
    examples: list[Example] = field(default_factory=list)


def extract_intro_from_readme(content: str) -> str:
    lines = content.splitlines()
    intro: list[str] = []
    seen_title = False
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if seen_title and intro:
                break
            continue
        if stripped.startswith("# "):
            seen_title = True
            continue
        if stripped.startswith("##"):
            break
        if seen_title:
            intro.append(stripped)
    return " ".join(intro) or "Agent-native CLI interface."


def extract_version_from_setup(setup_path: Path) -> str:
    content = setup_path.read_text(encoding="utf-8")
    match = re.search(r'PACKAGE_VERSION\s*=\s*["\']([^"\']+)["\']', content)
    if match:
        return match.group(1)
    match = re.search(r'version\s*=\s*["\']([^"\']+)["\']', content)
    return match.group(1) if match else "1.0.0"


def extract_commands_from_cli(cli_path: Path) -> list[CommandGroup]:
    content = cli_path.read_text(encoding="utf-8")
    groups: list[CommandGroup] = []
    group_name_by_function: dict[str, str] = {}

    group_pattern = (
        r'@cli\.group(?:\(([^)]*)\))?'
        r'(?:\s*@[\w.]+(?:\([^)]*\))?)*'
        r'\s*def\s+(\w+)\([^)]*\)'
        r'(?:\s*->\s*[^:]+)?'
        r':\s*'
        r'(?:"""([\s\S]*?)"""|\'\'\'([\s\S]*?)\'\'\')?'
    )
    for match in re.finditer(group_pattern, content):
        decorator_args = match.group(1) or ""
        func_name = match.group(2)
        doc = (match.group(3) or match.group(4) or "").strip()
        explicit_name = re.search(r'["\']([^"\']+)["\']', decorator_args)
        name = explicit_name.group(1) if explicit_name else func_name.replace("_", " ")
        display_name = name.replace("-", " ").title()
        group_name_by_function[func_name] = display_name
        groups.append(CommandGroup(name=display_name, description=doc or f"Commands for {name}."))

    command_pattern = (
        r'@(\w+)\.command(?:\(([^)]*)\))?'
        r'(?:\s*@[\w.]+(?:\([^)]*\))?)*'
        r'\s*def\s+(\w+)\([^)]*\)'
        r'(?:\s*->\s*[^:]+)?'
        r':\s*'
        r'(?:"""([\s\S]*?)"""|\'\'\'([\s\S]*?)\'\'\')?'
    )
    for match in re.finditer(command_pattern, content):
        group_func = match.group(1)
        decorator_args = match.group(2) or ""
        func_name = match.group(3)
        doc = (match.group(4) or match.group(5) or "").strip()
        explicit_name = re.search(r'["\']([^"\']+)["\']', decorator_args)
        cmd_name = explicit_name.group(1) if explicit_name else func_name.replace("_", "-")
        title = group_name_by_function.get(group_func, group_func.replace("_", " ").replace("-", " ").title())
        for group in groups:
            if group.name == title:
                group.commands.append(CommandInfo(cmd_name, doc or f"Execute `{cmd_name}`."))
                break
    return groups


def generate_examples(software_name: str) -> list[Example]:
    return [
        Example("Runtime Status", "Inspect Zotero paths and backend availability.", f"cli-anything-{software_name} app status --json"),
        Example("Read Selected Collection", "Persist the collection selected in the Zotero GUI.", f"cli-anything-{software_name} collection use-selected --json"),
        Example("Render Citation", "Render a citation using Zotero's Local API.", f"cli-anything-{software_name} item citation <item-key> --style apa --locale en-US --json"),
        Example("Add Child Note", "Create a child note under an existing Zotero item.", f"cli-anything-{software_name} note add <item-key> --text \"Key takeaway\" --json"),
        Example("Build LLM Context", "Assemble structured context for downstream model analysis.", f"cli-anything-{software_name} item context <item-key> --include-notes --include-links --json"),
    ]


def extract_cli_metadata(harness_path: str) -> SkillMetadata:
    harness_root = Path(harness_path)
    cli_root = harness_root / "cli_anything"
    software_dir = next(path for path in cli_root.iterdir() if path.is_dir() and (path / "__init__.py").exists())
    software_name = software_dir.name
    intro = extract_intro_from_readme((software_dir / "README.md").read_text(encoding="utf-8"))
    version = extract_version_from_setup(harness_root / "setup.py")
    groups = extract_commands_from_cli(software_dir / f"{software_name}_cli.py")
    return SkillMetadata(
        skill_name=f"cli-anything-{software_name}",
        skill_description=f"CLI harness for {_format_display_name(software_name)}.",
        software_name=software_name,
        skill_intro=intro,
        version=version,
        command_groups=groups,
        examples=generate_examples(software_name),
    )


def generate_skill_md_simple(metadata: SkillMetadata) -> str:
    lines = [
        "---",
        "name: >-",
        f"  {metadata.skill_name}",
        "description: >-",
        f"  {metadata.skill_description}",
        "---",
        "",
        f"# {metadata.skill_name}",
        "",
        metadata.skill_intro,
        "",
        "## Installation",
        "",
        "```bash",
        "pip install -e .",
        "```",
        "",
        "## Entry Points",
        "",
        "```bash",
        f"cli-anything-{metadata.software_name}",
        f"python -m cli_anything.{metadata.software_name}",
        "```",
        "",
        "## Command Groups",
        "",
    ]
    for group in metadata.command_groups:
        lines.extend([f"### {group.name}", "", group.description, "", "| Command | Description |", "|---------|-------------|"])
        for cmd in group.commands:
            lines.append(f"| `{cmd.name}` | {cmd.description} |")
        lines.append("")
    lines.extend(["## Examples", ""])
    for example in metadata.examples:
        lines.extend([f"### {example.title}", "", example.description, "", "```bash", example.code, "```", ""])
    lines.extend(["## Version", "", metadata.version, ""])
    return "\n".join(lines)


def generate_skill_md(metadata: SkillMetadata, template_path: Optional[str] = None) -> str:
    try:
        from jinja2 import Environment, FileSystemLoader
    except ImportError:
        return generate_skill_md_simple(metadata)

    template = Path(template_path) if template_path else Path(__file__).parent / "templates" / "SKILL.md.template"
    if not template.exists():
        return generate_skill_md_simple(metadata)
    env = Environment(loader=FileSystemLoader(template.parent))
    tpl = env.get_template(template.name)
    return tpl.render(
        skill_name=metadata.skill_name,
        skill_description=metadata.skill_description,
        software_name=metadata.software_name,
        skill_intro=metadata.skill_intro,
        version=metadata.version,
        command_groups=[
            {"name": group.name, "description": group.description, "commands": [{"name": c.name, "description": c.description} for c in group.commands]}
            for group in metadata.command_groups
        ],
        examples=[{"title": ex.title, "description": ex.description, "code": ex.code} for ex in metadata.examples],
    )


def generate_skill_file(harness_path: str, output_path: Optional[str] = None, template_path: Optional[str] = None) -> str:
    metadata = extract_cli_metadata(harness_path)
    content = generate_skill_md(metadata, template_path=template_path)
    output = Path(output_path) if output_path else Path(harness_path) / "cli_anything" / metadata.software_name / "skills" / "SKILL.md"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(content, encoding="utf-8")
    return str(output)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Generate SKILL.md for a CLI-Anything harness")
    parser.add_argument("harness_path")
    parser.add_argument("-o", "--output", default=None)
    parser.add_argument("-t", "--template", default=None)
    args = parser.parse_args(argv)
    print(generate_skill_file(args.harness_path, output_path=args.output, template_path=args.template))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
