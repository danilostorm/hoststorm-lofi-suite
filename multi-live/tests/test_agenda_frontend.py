from pathlib import Path


APP_JS = Path(__file__).resolve().parents[1] / 'static' / 'app.js'


def test_agenda_progress_is_updated_in_place():
    source = APP_JS.read_text(encoding='utf-8')
    assert "$$('.hs-schedule-progress',board).forEach(x=>x.remove());" not in source
    assert "if(!sid||!runs.has(sid))el.remove();" in source
    assert "if(div.innerHTML!==html)div.innerHTML=html;" in source


def test_agenda_mutation_observer_ignores_progress_only_changes():
    source = APP_JS.read_text(encoding='utf-8')
    assert "const relevant=mutations.some" in source
    assert "n.matches?.('.agenda-card,.agenda-pill,.agenda-group')" in source
    assert "if(!relevant||queued)return;" in source
    assert "requestAnimationFrame(()=>" in source
