from pathlib import Path


def test_ui_contains_review_and_accessibility_controls():
    page = (Path(__file__).parent.parent / 'static' / 'index.html').read_text()
    for expected in ('id="practice"', 'id="history"', 'id="level-track"',
                     'role="progressbar"', 'overflow-wrap:anywhere',
                     '/v1/practice/review', '/v1/me/history', 'localStorage.setItem'):
        assert expected in page


def test_backend_exposes_review_without_rewarding_points():
    backend = (Path(__file__).parent.parent / 'app' / 'main.py').read_text()
    assert "@app.get('/v1/practice/review')" in backend
    block = backend.split("@app.get('/v1/practice/review')")[1].split("@app.get('/v1/fragments')")[0]
    assert 'submit(' not in block
    assert 'db.add(' not in block
