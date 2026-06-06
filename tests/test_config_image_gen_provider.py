from __future__ import annotations

from backlink_publisher.config import load_config


def test_image_gen_provider_image2_loaded_from_toml(tmp_path):
    config_toml = tmp_path / "config.toml"
    config_toml.write_text(
        "[image_gen]\n"
        'provider = "image2"\n'
        'base_url = "https://api.openai.com/v1"\n'
        'model = "gpt-image-1.5"\n'
        'banner_size = "1536x1024"\n',
        encoding="utf-8",
    )

    cfg = load_config(config_toml)

    assert cfg.image_gen is not None
    assert cfg.image_gen.provider == "image2"
    assert cfg.image_gen.model == "gpt-image-1.5"
