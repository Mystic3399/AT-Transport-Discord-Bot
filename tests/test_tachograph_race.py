from decimal import Decimal
from io import BytesIO

import pytest
from PIL import Image
import tachograph as t


def test_real_and_race_totals_select_independent_ranks():
    real = t.progression(56)
    race = t.progression('6654.25')
    assert (real['name'], real['target']) == ('New Driver', 5000)
    assert (race['name'], race['target'], race['remaining']) == (
        'Road Runner', 10000, Decimal('3345.75'))
    assert t.progression(None) is None
    assert t.progression(0)['current'] == 0
    assert t.progression(1000000)['target'] is None
    for value in ('NaN', 'Infinity', '-1'):
        with pytest.raises(ValueError):
            t.progression(value)


def test_race_art_preserves_shared_shields_and_labels_every_rank():
    for miles in t.MILES:
        before = t.badge(miles, False).tobytes()
        assert t.race_badge(miles).size == (180, 200)
        assert t.race_badge(miles, True).size == (180, 200)
        assert before == t.badge(miles, False).tobytes()


@pytest.mark.parametrize('race', [None, '0', '4999.99', '5000', '6654.25', '1000000', '2000000'])
def test_both_faces_support_missing_fractional_and_maximum_race_totals(race):
    for side in ('front', 'back'):
        image = t.render('Long driver name ' * 12,
                         dict(real_miles=56, trucky_race_miles=race),
                         dict(truck_key=t.truck_keys()[0], country_code=None), side)
        assert Image.open(image).size == (1117, 700)
