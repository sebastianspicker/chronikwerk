"""Pin rejection of trigger tags that collide with the reserved workflow state tags."""

from __future__ import annotations

from pathlib import Path

import pytest

from chronikwerk.archiving.tags import DONE_TAG, ERROR_TAG, PROCESSING_TAG
from chronikwerk.configuration.load import load_settings
from chronikwerk.configuration.validation import RESERVED_STATE_TAGS, ConfigValidationError
from tests.support.settings_factory import write_test_config


def _config(base: Path, trigger_tag: str) -> Path:
    """Write a valid YAML file whose workflow trigger tag is the given value."""
    storage = base / "archive"
    storage.mkdir()
    config = base / "config.yaml"
    write_test_config(config, storage, state_dir=base / "state")
    config.write_text(
        config.read_text(encoding="utf-8") + f'workflow:\n  trigger_tag: "{trigger_tag}"\n'
    )
    return config


def test_reserved_set_matches_the_archiving_state_tags() -> None:
    """The configuration-local reserved set cannot drift from the archiving tag constants."""
    assert RESERVED_STATE_TAGS == {PROCESSING_TAG, DONE_TAG, ERROR_TAG}


@pytest.mark.parametrize("tag", sorted(RESERVED_STATE_TAGS))
def test_reserved_state_tag_is_rejected_as_trigger(isolated_config_env: Path, tag: str) -> None:
    """A trigger tag equal to a state tag fails validation with a path-scoped issue."""
    config = _config(isolated_config_env, tag)

    with pytest.raises(ConfigValidationError) as excinfo:
        load_settings(config_path=config)

    assert any(issue.path == "workflow.trigger_tag" for issue in excinfo.value.issues)


def test_custom_trigger_tag_is_accepted(isolated_config_env: Path) -> None:
    """A non-reserved trigger tag loads normally."""
    config = _config(isolated_config_env, "archive:now")

    assert load_settings(config_path=config).workflow.trigger_tag == "archive:now"
