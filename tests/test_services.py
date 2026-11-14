from app.services import normalize

def test_normalize():
    assert normalize('  Һаумыһығыҙ,  ДУҪТАР! ') == normalize('һаумыһығыҙ дуҫтар')
