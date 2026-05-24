"""CLI commands for managing custom SRE skills."""

from __future__ import annotations

import json
import click
from app.constants import OPENSRE_HOME_DIR

SKILLS_STORE_PATH = OPENSRE_HOME_DIR / "skills.json"


def load_skills() -> dict:
    """Load all registered skills from skills.json."""
    if not SKILLS_STORE_PATH.exists():
        return {}
    try:
        with open(SKILLS_STORE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_skills(skills: dict) -> None:
    """Save all registered skills to skills.json."""
    OPENSRE_HOME_DIR.mkdir(parents=True, exist_ok=True)
    try:
        with open(SKILLS_STORE_PATH, "w", encoding="utf-8") as f:
            json.dump(skills, f, indent=2, ensure_ascii=False)
    except Exception as e:
        click.echo(f"Error saving skills: {e}", err=True)


@click.group(name="skill")
def skill_group() -> None:
    """Manage custom SRE skills (prompts/configurations)."""
    pass


@skill_group.command(name="add")
@click.argument("name")
@click.argument("content")
def add_skill(name: str, content: str) -> None:
    """Add or update a skill by name. CONTENT can be a prompt string or a JSON configuration."""
    skills = load_skills()

    # Try parsing content as JSON
    try:
        parsed_content = json.loads(content)
        if not isinstance(parsed_content, dict):
            parsed_content = {"prompt": str(parsed_content)}
    except json.JSONDecodeError:
        # Not a JSON, treat as a raw prompt string
        parsed_content = {"prompt": content}

    skills[name] = parsed_content
    save_skills(skills)
    click.echo(f"Skill '{name}' added/updated successfully.")


@skill_group.command(name="remove")
@click.argument("name")
def remove_skill(name: str) -> None:
    """Remove a skill by name."""
    skills = load_skills()
    if name in skills:
        del skills[name]
        save_skills(skills)
        click.echo(f"Skill '{name}' removed successfully.")
    else:
        click.echo(f"Skill '{name}' not found.", err=True)


@skill_group.command(name="list")
def list_skills() -> None:
    """List all registered skills."""
    skills = load_skills()
    if not skills:
        click.echo("No skills registered yet.")
        return
    for name, data in skills.items():
        prompt = data.get("prompt") if isinstance(data, dict) else data
        click.echo(f"- {name}: {prompt}")
