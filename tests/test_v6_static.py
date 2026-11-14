from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def test_dictionary_route_and_provenance():
    code=(ROOT/'app/main.py').read_text()
    assert "@app.get('/v1/dictionary')" in code
    assert 'reference.is_reference.is_(True)' in code
    assert "checked.status=='confirmed'" in code
    assert 'u.lang_pair' in code

def test_dictionary_ui_and_mascot():
    ui=(ROOT/'static/index.html').read_text()
    assert 'data-tab="dictionary"' in ui
    assert 'id="dict-query"' in ui
    assert 'function loadDictionary(' in ui
    assert 'baly-bee.webp' in ui
    assert (ROOT/'static/baly-bee.webp').is_file()
