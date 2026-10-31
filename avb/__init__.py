"""dtc-ads-video-ai-generator: script -> SceneGraph IR -> finished video ad.

Layer map (see docs/ARCHITECTURE.md):

    config.avb.toml   what to use (models, providers, keys-by-env-name)
    llm.py            OpenAI-compatible chat client (DeepSeek by default)
    parse.py          script -> IR          (heuristic or LLM)
    edit.py           NL instruction -> IR patch   (rule-based)
    llm_edit.py       NL instruction -> IR patch   (LLM, falls back to rules)
    pipeline.py       IR -> mp4 with content-addressed per-scene caching
    qc.py             automated quality control
    providers live in assets/*  (voice / video / image / music)

Public surface:
    parse_script / parse_script_llm   script -> IR
    Pipeline                          IR -> mp4 (with per-scene caching)
    plan_edit / plan_edit_llm / apply_plan / invalidate   conversational editing
    load_config / get_client          configuration + LLM
    run_qc                            automated quality control
"""

__version__ = "0.2.0"
