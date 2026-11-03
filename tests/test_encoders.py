import pytest

from lostfound import encoders, store
from lostfound.settings import settings


def test_active_encoders_match_the_configured_names():
    active = encoders.active_encoders()
    assert active["text"]["name"] == settings.text_embedder
    assert active["image"]["name"] == settings.image_embedder
    assert active["text"]["dim"] > 0
    assert active["image"]["dim"] > 0
    assert active["text"]["preprocess_hash"] == active["text"]["preprocess_hash"]


def test_registry_is_versioned_and_covers_both_kinds():
    entries = encoders.registry()
    assert {info.kind for info in entries} == {"text", "image"}
    for info in entries:
        assert info.revision
        assert len(info.preprocess_hash) == 12


def test_unknown_encoder_is_a_loud_error():
    with pytest.raises(KeyError):
        encoders.encoder_info("image", "stablediffusion-xl")


def test_preprocess_hash_changes_with_the_preprocessing_contract():
    info = encoders.encoder_info("image", "hist-grid-v1")
    same = encoders.EncoderInfo(
        kind=info.kind,
        name=info.name,
        dim=info.dim,
        revision=info.revision,
        preprocess=info.preprocess,
    )
    other = encoders.EncoderInfo(
        kind=info.kind,
        name=info.name,
        dim=info.dim,
        revision=info.revision,
        preprocess=info.preprocess + ";resize128",
    )
    assert info.preprocess_hash == same.preprocess_hash
    assert info.preprocess_hash != other.preprocess_hash


def test_no_stored_vectors_on_a_fresh_database(tmp_path):
    con = store.connect(tmp_path / "encoders.db")
    try:
        assert encoders.stale_vector_models(con) == {}
    finally:
        con.close()
