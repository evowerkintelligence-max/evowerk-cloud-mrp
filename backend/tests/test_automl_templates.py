from __future__ import annotations

import pytest

from app.services import automl_templates as templates


def test_register_and_get_template():
    t = templates.AutoMLTemplate(
        id="_test_template",
        label="Test Template",
        roles=[templates.ColumnRole("x_col", "X column")],
        preprocess=lambda df, config: templates.PreprocessResult(),
        postprocess=lambda engine_result, config, context: {},
    )
    templates.register_template(t)
    try:
        fetched = templates.get_template("_test_template")
        assert fetched is t
        assert any(x.id == "_test_template" for x in templates.list_templates())
    finally:
        del templates._REGISTRY["_test_template"]


def test_get_unknown_template_raises():
    with pytest.raises(KeyError, match="Unknown AutoML template"):
        templates.get_template("_does_not_exist")
