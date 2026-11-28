import ast
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo
import pytest
import tachograph as t

@pytest.mark.parametrize('original,expected', [
    ('[001] CEO | Mystic',('001','Mystic')),
    ('[020] littleplanet909',('020','littleplanet909')),
    ('[002] Co-Owner/Driver | Bungie',('002','Bungie')),
    ('[12345678] Driver',('12345678','Driver')),
    ('[001] Ghost | Wolf',('001','Ghost | Wolf')),
    ('Mystic',(None,'Mystic')),
    ('[A01] Mystic',(None,'[A01] Mystic')),
    ('[001]',(None,'[001]')),
])
def test_driver_identity(original,expected):
    assert t.driver_identity(original)==expected

@pytest.mark.asyncio
async def test_career_details():
    tree=ast.parse((Path(__file__).resolve().parents[1]/'bot.py').read_text())
    node=next(n for n in tree.body if getattr(n,'name',None)=='fetch_card_career_details')
    start=datetime(2026,11,1,tzinfo=ZoneInfo('Europe/London'))
    end=datetime(2026,12,1,tzinfo=ZoneInfo('Europe/London'))
    monthly=AsyncMock(return_value=[dict(real_miles=3750)])
    ns=dict(leaderboard_period_bounds=lambda _: (start,end),fetch_period_rankings=monthly,
            datetime=datetime,UK_TIMEZONE=ZoneInfo('Europe/London'),
            PROGRESSION_ROLES=[(150000,None,'Viking Pathfinder')],
            ACHIEVEMENT_ROLES=[(100000,None,'Centurion Club')])
    exec(compile(ast.Module(body=[node],type_ignores=[]),'bot.py','exec'),ns)
    conn=SimpleNamespace(fetch=AsyncMock(side_effect=[
        [dict(miles=150000,kind='rank',earned_at=start,track='real')],
        [dict(miles=100000,kind='achievement',earned_at=end,track='race')]]),
        fetchval=AsyncMock(return_value=True))
    member=SimpleNamespace(id=42,guild=object(),joined_at=start)
    result=await ns['fetch_card_career_details'](conn,member)
    monthly.assert_awaited_once_with(conn,member.guild,start,end,driver_id=42)
    assert result['monthly_target']==10000
    assert result['monthly_real_miles']==3750
    assert result['race_permanent_achievements']==(100000,)
    assert result['latest_award']=='Centurion Club · RACE MILEAGE'
    assert result['member_since']=='01 NOV 2026'
    conn.fetch.reset_mock(side_effect=True)
    conn.fetch.return_value=[]
    conn.fetchval.return_value=False
    monthly.return_value=[]
    member.joined_at=None
    result=await ns['fetch_card_career_details'](conn,member)
    assert result['monthly_real_miles']==0
    assert result['member_since'] is None
    assert result['race_permanent_achievements']==()
    assert 'latest_award' not in result
