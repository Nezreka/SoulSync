"""every file-naming template the path builder reads has a field in settings.

#1385 (cremonies): compilations were filed with `compilation_path`, a template
settings never showed. setting the album template to
`$albumartist/$album/$artist - $title` did nothing for a soundtrack, which
landed in `Compilations/<album>/10 - <artist> - <title>` from the hidden
default. a template the builder reads but settings can't show or save is a
setting the user can't change.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _template_keys_the_builder_reads() -> set:
    src = (ROOT / "core" / "imports" / "paths.py").read_text(encoding="utf-8")
    return set(re.findall(r'["\']((?:album|single|compilation|playlist)_path)["\']', src))


def test_the_builder_reads_the_compilation_template():
    # if this fails the regex above went stale, not the fix
    assert "compilation_path" in _template_keys_the_builder_reads()


def test_every_music_template_has_a_settings_field_that_loads_and_saves():
    # Library > Naming (settings-bic phase 4): each template has a field (the
    # page's template() helper, given its key and id), shows the saved value
    # (or its default) and saves its key
    tabs = ROOT / "webui" / "src" / "features" / "settings" / "tabs"
    card = (tabs / "library-organization.tsx").read_text(encoding="utf-8")
    rules = (tabs / "organization.ts").read_text(encoding="utf-8")
    defaults = rules.split("export const TEMPLATE_DEFAULTS", 1)[1].split("} as const", 1)[0]
    for key in sorted(_template_keys_the_builder_reads()):
        field = "template-" + key.replace("_", "-")
        flat = " ".join(card.split())
        assert f"template( '{key}', '{field}'," in flat, f"no settings field for {key}"
        assert f"useTemplateField(store, '{key}'" in card, f"the {key} field is not wired"
        assert f"  {key}: '" in defaults, f"settings never loads {key}"
    assert "store.set('file_organization', `templates.${key}`, next);" in card, "templates never save"
