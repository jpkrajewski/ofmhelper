"""
Gender/pronoun resolution for the character being built -- shapes.py's
voice/camera templates are written with `{...}` placeholders (label,
subject/object/possessive pronoun, noun, speaker TAG) instead of hardcoded
"female"/"she"/"her", so the same shape renders correctly regardless of
which gender the user picks on the /replicate form.

This is purely about pronouns/voice register, never physical appearance --
the RULE block (prompt_builder.py) already says identity comes from the
reference images only, and that constraint doesn't change here.
"""

from dataclasses import dataclass

DEFAULT_GENDER = "female"


@dataclass(frozen=True)
class Gender:
    label: str  # "female" / "male" / "androgynous"
    noun: str  # "woman" / "man" / "person"
    subject: str  # "she" / "he" / "they"
    object: str  # "her" / "him" / "them"
    possessive: str  # "her" / "his" / "their"
    tag: str  # per-second timeline speaker tag: "SHE" / "HE" / "THEY"

    @property
    def noun_cap(self) -> str:
        return self.noun.capitalize()


GENDERS: dict[str, Gender] = {
    "female": Gender(
        label="female",
        noun="woman",
        subject="she",
        object="her",
        possessive="her",
        tag="SHE",
    ),
    "male": Gender(
        label="male",
        noun="man",
        subject="he",
        object="him",
        possessive="his",
        tag="HE",
    ),
    "nonbinary": Gender(
        label="androgynous",
        noun="person",
        subject="they",
        object="them",
        possessive="their",
        tag="THEY",
    ),
}


def get_gender(name: str | None) -> Gender:
    return GENDERS.get(name or DEFAULT_GENDER, GENDERS[DEFAULT_GENDER])


def render_text(template: str, gender: Gender) -> str:
    """Fills a shapes.py template string's gender placeholders. Plain
    str.format so a template with no placeholders at all (e.g.
    action_no_dialogue's voiceless shape) just passes through unchanged."""
    return template.format(
        label=gender.label,
        noun=gender.noun,
        noun_cap=gender.noun_cap,
        subject=gender.subject,
        object=gender.object,
        possessive=gender.possessive,
        tag=gender.tag,
    )
