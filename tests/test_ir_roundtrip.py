"""Resilience of the SceneGraph IR (de)serialization.

The IR is persisted as project_json into the history DB (see the P1 resume
work). That JSON can outlive the code that wrote it, so from_dict must tolerate
schema drift: unknown keys dropped, missing keys defaulted. A strict
``**kwargs`` splat would raise ``TypeError`` on any newer field -- exactly the
failure mode we are guarding against for cross-version resume.
"""

import json

from avb.ir import Project, Scene, Visual, Voiceover, CaptionStyle


def _sample() -> Project:
    return Project(
        name="茅台",
        script="line1\nline2",
        aspect="9:16",
        resolution="720p",
        scenes=[
            Scene(
                beat="hook",
                vo=Voiceover(text="hi", voice="aria"),
                visual=Visual(prompt="bottle", shot="close-up"),
                caption=CaptionStyle(mode="kinetic"),
            )
        ],
    )


def test_roundtrip_preserves_content():
    p = _sample()
    p2 = Project.from_json(p.to_json())
    assert p2.name == "茅台"
    assert p2.scenes[0].vo.text == "hi"
    assert p2.scenes[0].visual.shot == "close-up"
    assert p2.width == 720  # 1080 * 2/3


def test_from_dict_ignores_unknown_keys():
    d = json.loads(_sample().to_json())
    # A future build saved an extra field on a nested node -- older code must
    # still load it instead of crashing on an unexpected keyword argument.
    d["brand"]["accent_v2"] = "#123456"
    d["scenes"][0]["vo"]["neural_flag"] = True
    d["scenes"][0]["visual"]["lens"] = "anamorphic"
    p = Project.from_dict(d)
    assert p.brand.name == "BRAND"  # defaults intact
    assert p.scenes[0].vo.text == "hi"  # existing data preserved


def test_from_dict_defaults_missing_keys():
    d = json.loads(_sample().to_json())
    # Drop fields a newer schema might have added but this build lacks; the
    # load must fall back to dataclass defaults rather than KeyError.
    d["scenes"][0].pop("beat", None)
    d["scenes"][0]["visual"].pop("shot", None)
    d["scenes"][0]["vo"].pop("voice", None)
    p = Project.from_dict(d)
    assert p.scenes[0].beat == "problem"  # Scene.beat default
    assert p.scenes[0].visual.shot == ""  # Visual.shot default
    assert p.scenes[0].vo.voice == "adam"  # Voiceover.voice default


def test_from_dict_tolerates_empty_subobjects():
    d = json.loads(_sample().to_json())
    d["brand"] = {}
    d["music"] = None
    d["scenes"][0]["vo"] = None
    d["scenes"][0]["caption"] = {}
    p = Project.from_dict(d)
    assert p.brand.name == "BRAND"
    assert p.music.track == "pulse_drive"
    assert p.scenes[0].vo.text == ""
    assert p.scenes[0].caption.mode == "kinetic"
