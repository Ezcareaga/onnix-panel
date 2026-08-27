"""Tests for crm_followup.html template — Alpine x-data attribute quoting.

Bug: tojson produces double-quoted JSON strings. When used inside
double-quoted HTML attributes (x-data="...", @click="..."), the first
'"' in the JSON value terminates the HTML attribute early. Alpine.js
cannot initialize the component, making edit mode permanently unreachable.

Fix: outer HTML attribute quotes must be single-quoted so that the
double-quoted JSON string from tojson is safely embedded.
"""
import sys
from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader

# Ensure panel/ is on sys.path
_panel_dir = str(Path(__file__).resolve().parent.parent)
if _panel_dir not in sys.path:
    sys.path.insert(0, _panel_dir)

from app.tz import to_pyt


# ---------------------------------------------------------------------------
# Minimal mock objects — avoid touching the DB
# ---------------------------------------------------------------------------


class MockNote:
    """Minimal note object for template rendering tests."""

    id: int = 1
    content: str = 'Test "quoted" note with double quotes'
    created_at = None  # Avoids needing a real datetime for the pyt filter


class MockContact:
    """Minimal contact object for template rendering tests."""

    id: int = 42


# ---------------------------------------------------------------------------
# Jinja2 environment fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def jinja_env() -> Environment:
    """Create a Jinja2 environment pointing at app/templates with pyt filter."""
    templates_dir = Path(_panel_dir) / "app" / "templates"
    env = Environment(
        loader=FileSystemLoader(str(templates_dir)),
        autoescape=False,  # crm_followup.html uses tojson explicitly, not autoescape
    )
    env.filters["pyt"] = to_pyt
    return env


# ---------------------------------------------------------------------------
# RED test — must FAIL before the template fix
# ---------------------------------------------------------------------------


def test_xdata_attribute_not_broken_by_double_quoted_content(jinja_env: Environment) -> None:
    """x-data attribute must NOT be terminated early by double quotes in note content.

    Before fix: tojson inside a double-quoted x-data="..." attribute produces
        x-data="{ editing: false, content: "Test ..."
    which breaks Alpine.js initialization.

    After fix: the outer attribute uses single quotes, so
        x-data='{ "editing": false, "content": "Test ..." }'
    is valid HTML and Alpine parses the full expression correctly.
    """
    template = jinja_env.get_template("partials/crm_followup.html")
    note = MockNote()

    rendered = template.render(
        followup=[{"kind": "nota", "obj": note}],
        contact_id=MockContact.id,
        overdue_ids=set(),
    )

    # This broken pattern appears when x-data="..." (double-quoted) is used
    # and tojson produces a double-quoted string: the attribute is truncated at
    # the first '"' of the JSON value.
    broken_pattern = 'x-data="{ editing: false, content: "'
    assert broken_pattern not in rendered, (
        "x-data attribute is NOT broken with double-quoted content: "
        "the outer HTML attribute must use single quotes so that tojson's "
        "double-quoted JSON string does not terminate it early."
    )


def test_click_cancel_attribute_not_broken_by_double_quoted_content(jinja_env: Environment) -> None:
    """@click Cancelar attribute must NOT be terminated early by double quotes in note content.

    Before fix: tojson inside a double-quoted @click="..." attribute produces
        @click="editing = false; content = "Test ..."
    which breaks Alpine.js click handler.

    After fix: the outer attribute uses single quotes.
    """
    template = jinja_env.get_template("partials/crm_followup.html")
    note = MockNote()

    rendered = template.render(
        followup=[{"kind": "nota", "obj": note}],
        contact_id=MockContact.id,
        overdue_ids=set(),
    )

    # Broken pattern for the @click Cancelar button
    broken_pattern = '@click="editing = false; content = "'
    assert broken_pattern not in rendered, (
        "@click Cancelar attribute is NOT broken with double-quoted content: "
        "the outer HTML attribute must use single quotes."
    )
