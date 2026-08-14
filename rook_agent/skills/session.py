"""Session event helpers for skill audit records."""

from __future__ import annotations

from rook_agent.context.events import SessionEvent
from rook_agent.context.identity import new_event_id
from rook_agent.context.store import JsonlSessionStore
from rook_agent.context.writer import SessionEventWriter
from rook_agent.skills.loader import SkillLoadError, SkillLoader
from rook_agent.skills.models import LoadedSkill, LoadedSkillRequiredFile, SkillCatalog, SkillRoutingDecision


_MAX_ACTIVE_SKILLS = 4


def append_skill_selected(writer: SessionEventWriter, decision: SkillRoutingDecision) -> None:
    if decision.selected is None:
        return
    skill = decision.selected
    writer.store.append_event(
        SessionEvent(
            id=new_event_id(),
            session_id=writer.session_id,
            type="skill_selected",
            payload={
                "skill_name": skill.name,
                "skill_scope": skill.scope,
                "skill_source": skill.source.value,
                "skill_root": skill.root,
                "skill_path": skill.path,
                "reason": decision.reason,
                "confidence": decision.confidence,
                "turn_id": writer.current_turn,
            },
        )
    )


def append_skill_loaded(writer: SessionEventWriter, loaded: LoadedSkill) -> None:
    skill = loaded.skill
    writer.store.append_event(
        SessionEvent(
            id=new_event_id(),
            session_id=writer.session_id,
            type="skill_loaded",
            payload={
                "skill_name": skill.name,
                "skill_scope": skill.scope,
                "skill_source": skill.source.value,
                "skill_root": skill.root,
                "skill_path": skill.path,
                "content_hash": loaded.content_hash,
                "bytes": loaded.bytes,
                "required_files": list(loaded.required_files),
                "turn_id": writer.current_turn,
            },
        )
    )


def append_skill_required_file_loaded(writer: SessionEventWriter, required: LoadedSkillRequiredFile) -> None:
    skill = required.skill
    writer.store.append_event(
        SessionEvent(
            id=new_event_id(),
            session_id=writer.session_id,
            type="skill_required_file_loaded",
            payload={
                "skill_name": skill.name,
                "skill_scope": skill.scope,
                "skill_source": skill.source.value,
                "skill_root": skill.root,
                "skill_path": skill.path,
                "file_path": required.file_path,
                "content_hash": required.content_hash,
                "bytes": required.bytes,
                "turn_id": writer.current_turn,
            },
        )
    )


def append_skills_cleared(writer: SessionEventWriter, *, reason: str) -> None:
    writer.store.append_event(
        SessionEvent(
            id=new_event_id(),
            session_id=writer.session_id,
            type="skills_cleared",
            payload={
                "reason": reason,
                "turn_id": writer.current_turn,
            },
        )
    )


def merge_active_skill(
    active: list[LoadedSkill],
    loaded: LoadedSkill,
) -> list[LoadedSkill]:
    identity = (loaded.skill.root, loaded.skill.path)
    merged = [
        item
        for item in active
        if (item.skill.root, item.skill.path) != identity
    ]
    merged.append(loaded)
    return merged[-_MAX_ACTIVE_SKILLS:]


def replay_loaded_skills(store: JsonlSessionStore, session_id: str, catalog: SkillCatalog) -> list[LoadedSkill]:
    loaded: list[LoadedSkill] = []
    for event in store.list_events(session_id):
        if event.type == "skills_cleared":
            loaded.clear()
            continue
        if event.type != "skill_loaded":
            continue
        skill_path = str(event.payload.get("skill_path") or "")
        skill_root = str(event.payload.get("skill_root") or "")
        skill = next(
            (
                candidate
                for candidate in catalog.skills
                if candidate.path == skill_path and candidate.root == skill_root
            ),
            None,
        )
        if skill is None:
            continue
        try:
            loader = SkillLoader()
            loaded_skill = loader.load(skill)
            required_files = []
            for file_path in loaded_skill.required_files:
                try:
                    required_files.append(loader.load_required_file(loaded_skill, file_path))
                except SkillLoadError:
                    continue
            if required_files:
                loaded_skill = LoadedSkill(
                    skill=loaded_skill.skill,
                    content=loaded_skill.content,
                    required_files=loaded_skill.required_files,
                    required_file_contents=required_files,
                )
            loaded = merge_active_skill(loaded, loaded_skill)
        except SkillLoadError:
            continue
    return loaded
